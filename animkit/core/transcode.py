"""Video to image sequence, so a dropped .mp4 is reference and not a blank plane.

    from animkit.core import transcode
    result = transcode.convert("C:/ref/take01.mp4", fps=24)
    result.first_frame      # hand this to an image plane

Imports **no Maya and no Qt**. It runs a subprocess and does path arithmetic,
which is why the whole of it -- lookup order, cache keys, argument building,
output parsing, and a real end-to-end conversion -- runs in the fast test tier.

WHY THIS EXISTS AT ALL
----------------------
Maya cannot decode `.mp4` on an image plane. Not "badly", not "depending on
codecs" -- measured on 2024/Windows, `coverage` stays -1 in both Movie and
Image File mode, so the plane loads and draws nothing. A MJPEG `.avi` reads
fine, which is the trap: the format nobody has works, and the format everybody
has does not.

An image sequence is also simply the better reference. Maya scrubs a sequence
frame by frame; a movie image plane *seeks*, and seeking backwards through a
long-GOP H.264 file is why scrubbing video reference feels like treacle.

FRAME RATE IS THE WHOLE POINT, AND IT IS NOT THE SOURCE'S
----------------------------------------------------------
The conversion resamples to the SCENE's frame rate, not the video's. That is
deliberate and it is the difference between reference you can animate to and
reference you cannot: at the scene rate, frame N of the sequence is frame N of
the timeline, so an action that happens one second in happens at frame 24 in a
24fps scene. Keeping the source's 30fps instead would leave every timing in
the reference 25% out, consistently, in a way that looks fine until you try to
match a contact.

CACHED BY CONTENT, NOT BY NAME
------------------------------
Converting a two-minute reference takes real seconds, and an animator drops the
same file repeatedly -- reopening a shot, undoing, trying it on another camera.
The cache key covers the path, the file's size and mtime, and every setting
that changes the pixels, so re-dropping the same video is instant and editing
the video re-converts. A key on the path alone would serve yesterday's render
forever.

NOTHING HERE RAISES ON THE READ PATH
------------------------------------
`executable()` and `is_available()` answer "can we do this at all" and are
called from a drop handler that has nowhere to report an exception. A missing
binary, an unreadable cache directory and a broken PATH all end the same way:
False, one line in the log, and the caller falls back to handing the movie to
Maya as it always did.
"""

import hashlib
import logging
import os
import re
import subprocess
import sys

log = logging.getLogger(__name__)

#: Where a bundled binary lives, per platform. A folder per platform rather
#: than one file, so a macOS build can be dropped in beside the Windows one
#: without touching this module.
_PLATFORM_DIRS = {
    "win32": "win64",
    "darwin": "macos",
    "linux": "linux",
}

FFMPEG_NAME = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"

#: Formats worth writing. jpg by default: a 300-frame 1080p reference is ~45MB
#: as jpg and ~300MB as png, and nobody is grading a reference board. png is
#: there for the case where an animator is matching subtle colour.
FORMATS = ("jpg", "png")

#: Reference does not need to be full resolution -- it needs to be legible
#: behind a rig at 30% opacity. Capping the height keeps the conversion fast
#: and the cache small. Sources shorter than this are never upscaled.
#:
#: It is the wrong cap for one real case, which is why NATIVE exists: a screen
#: recording, a phone shot held in portrait, anything whose subject is TEXT or
#: fine detail rather than a performance. 1920x1080 of small type resampled to
#: 1280x720 and then JPEG'd at 4:2:0 is not soft, it is unreadable, and no
#: amount of scaling the plane up in the viewport brings it back -- the pixels
#: were thrown away at conversion time.
DEFAULT_HEIGHT = 720

#: `height=NATIVE` means do not scale at all. The frames come out at the
#: source's own resolution and the cache grows accordingly, which is the trade
#: an animator matching a screen recording actually wants to make.
NATIVE = 0
DEFAULT_FPS = 24.0
DEFAULT_FORMAT = "jpg"

#: Chroma sampling for the jpg path. 4:2:0 -- the default everywhere -- stores
#: colour at half resolution in both directions, which is invisible on a
#: photographed performance and destroys exactly the thing a screen recording
#: is made of: coloured text on a flat background comes back with fringed,
#: smeared edges no amount of resolution fixes. Measured cost of 4:4:4 on a
#: 1080p60 clip: 231MB against 211MB per 30 seconds, and 4.2s against 3.6s.
#: Worth it for a tool whose job is legibility.
JPEG_PIXEL_FORMAT = "yuvj444p"

#: Bumped when a change here alters the PIXELS a given set of settings
#: produces. It is part of the cache key, so bumping it re-converts rather than
#: serving frames made by the old encoder settings forever -- which is what
#: would otherwise happen, silently, to everyone who already has a cache.
ENCODER_VERSION = 2

#: Padding on the written frames. Four digits covers 9999 frames, which is
#: nearly seven minutes at 24fps -- longer than any reference clip, and if it
#: is exceeded ffmpeg simply writes wider numbers and `core.media` reads the
#: sequence as the narrower run. Documented rather than guarded because the
#: failure is "your six-minute reference stops at frame 9999", not corruption.
PADDING = 4

#: `frame=  123 fps=...` on ffmpeg's stderr. The only progress it offers.
_PROGRESS = re.compile(r"frame=\s*(\d+)")
#: `Duration: 00:01:23.45,`
_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
#: `, 1920x1080` and `, 29.97 fps`
_SIZE = re.compile(r",\s*(\d{2,5})x(\d{2,5})")
_FPS = re.compile(r",\s*(\d+(?:\.\d+)?)\s*fps")


# --- finding the binary -----------------------------------------------------


def bundled_path():
    """The binary shipped inside the package, or None if this platform has none.

    animkit ships one for Windows. The macOS and Linux folders are slots: the
    lookup finds a binary dropped into them, and finds nothing when they are
    empty, which is exactly the behaviour wanted either way.
    """
    folder = _PLATFORM_DIRS.get(sys.platform)
    if not folder:
        return None
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidate = os.path.join(here, "vendor", "ffmpeg", folder, FFMPEG_NAME)
    return candidate if os.path.isfile(candidate) else None


def _on_path():
    """An ffmpeg on PATH, or None. Never raises."""
    try:
        from shutil import which

        return which("ffmpeg")
    except Exception:
        log.debug("animkit: could not search PATH for ffmpeg", exc_info=True)
        return None


def executable(override=None):
    """The ffmpeg to use, or None if there is not one.

    Order, and the reason for it: an explicit setting wins because a studio
    that has standardised on a build wants that build; then the bundled one,
    so the tool works out of the box on a machine with nothing installed; then
    PATH, which is the developer's copy and the macOS/Linux answer until those
    slots are filled.
    """
    if override:
        candidate = os.path.expanduser(str(override))
        if os.path.isfile(candidate):
            return candidate
        log.warning("animkit: ffmpeg not found at %r, falling back", override)

    found = bundled_path()
    if found:
        return found
    return _on_path()


def is_available(override=None):
    return executable(override) is not None


def version(override=None):
    """The first line of `ffmpeg -version`, for the self-test. None if absent."""
    binary = executable(override)
    if binary is None:
        return None
    try:
        out = _run([binary, "-version"], capture=True)
    except Exception:
        log.debug("animkit: could not run ffmpeg -version", exc_info=True)
        return None
    return (out or "").splitlines()[0].strip() if out else None


# --- running it -------------------------------------------------------------


def _no_window():
    """Keep a console from flashing up on Windows on every conversion."""
    if sys.platform != "win32":
        return {}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {"startupinfo": startup,
            "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def _run(args, capture=False):
    """Run to completion and return the combined output. For short commands."""
    process = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        universal_newlines=True, **_no_window()
    )
    out, _ = process.communicate()
    return out if capture else None


def probe(path, override=None):
    """What a video is: {duration, fps, width, height}. None if unreadable.

    Parsed from `ffmpeg -i`'s own stderr rather than by shipping ffprobe as
    well -- a second 200MB binary to read four numbers is not a trade worth
    making. ffmpeg exits non-zero when given no output file, which is expected
    and not an error here.
    """
    binary = executable(override)
    if binary is None or not os.path.isfile(path):
        return None
    try:
        text = _run([binary, "-hide_banner", "-i", path], capture=True) or ""
    except Exception:
        log.debug("animkit: could not probe %r", path, exc_info=True)
        return None

    found = {}
    match = _DURATION.search(text)
    if match:
        hours, minutes, seconds = match.groups()
        found["duration"] = (int(hours) * 3600 + int(minutes) * 60
                             + float(seconds))
    match = _SIZE.search(text)
    if match:
        found["width"] = int(match.group(1))
        found["height"] = int(match.group(2))
    match = _FPS.search(text)
    if match:
        found["fps"] = float(match.group(1))

    # No stream dimensions means ffmpeg did not recognise a video track --
    # a stray .mp4 that is really something else, or an audio-only file.
    return found if "width" in found else None


# --- the cache --------------------------------------------------------------


def cache_root():
    """Where converted sequences live: the animkit prefs folder, `refcache`.

    Beside the animator's video would put hundreds of files into a reference
    folder they did not ask to have written to, and often onto a shared drive.
    Under the prefs it is per-user, out of the way, and disposable.
    """
    from animkit.core import settings

    return os.path.join(settings.directory(), "refcache")


def cache_key(path, fps, height, image_format):
    """Identity of a conversion: the source's content and every setting.

    Size and mtime rather than a hash of the file, because hashing a 2GB video
    to decide whether to spend 20 seconds converting it is the wrong trade. A
    re-render that keeps the byte count and the timestamp is the case this
    misses, and it does not happen by accident.
    """
    try:
        stat = os.stat(path)
        stamp = "%d-%d" % (stat.st_size, int(stat.st_mtime))
    except Exception:
        stamp = "nostat"
    seed = "|".join([
        os.path.normcase(os.path.abspath(path)), stamp,
        "%.4f" % float(fps), str(int(height)), str(image_format),
        "v%d" % ENCODER_VERSION,
    ])
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", os.path.basename(path))[:40]
    return "%s_%s" % (stem, digest)


def cached_sequence(folder, image_format=DEFAULT_FORMAT):
    """The first frame in a finished conversion, or None.

    A conversion killed halfway leaves frames but no marker, so the marker is
    what makes a cache hit trustworthy -- without it a cancelled convert would
    be served forever as a complete one.
    """
    marker = os.path.join(folder, "animkit.done")
    if not os.path.isfile(marker):
        return None
    first = os.path.join(folder, "frame.%s.%s" % ("1".zfill(PADDING),
                                                  image_format))
    return first if os.path.isfile(first) else None


class Result(object):
    """Where a conversion ended up and what it cost."""

    def __init__(self, first_frame, folder, frames, cached=False,
                 seconds=0.0):
        self.first_frame = first_frame
        self.folder = folder
        self.frames = frames
        self.cached = cached
        self.seconds = seconds

    def __repr__(self):
        return "<Result %d frames%s>" % (self.frames,
                                         " (cached)" if self.cached else "")


# --- converting -------------------------------------------------------------


def normalise_height(height):
    """A height cap as an int, with anything unusable meaning NATIVE.

    A setting an animator can type into is a setting that can hold "1080p",
    None, or -1 by the time it reaches here. None of those is worth failing a
    conversion over, and every one of them means the same sensible thing:
    do not scale.
    """
    try:
        value = int(height)
    except (TypeError, ValueError):
        return NATIVE
    return value if value > 0 else NATIVE


def build_args(binary, source, folder, fps, height, image_format):
    """The ffmpeg command line. Separate so it can be asserted on.

    `-vsync cfr` with `-r` is what makes the output a constant-rate sequence
    that lines up with the timeline; without it a variable-rate phone video
    produces frames that drift against the frame numbers.

    The scale filter only ever shrinks (`min(iw,-1)` style via the `'if'`
    guard), because upscaling a 480p reference to 720 wastes disk to add
    nothing. At NATIVE there is no `-vf` at all rather than a filter that
    happens to be identity -- a scale filter always costs a full resample, and
    a resample to the size you already are is the one that buys nothing.

    Nothing here disables ffmpeg's autorotation. A phone clip carries its
    orientation in a display matrix rather than in the pixels, and ffmpeg
    applies it before this filter chain, so a portrait video converts to
    portrait frames. `-noautorotate` would produce a sideways reference, which
    is why it is named here and not used.
    """
    pattern = os.path.join(folder, "frame.%%0%dd.%s" % (PADDING, image_format))
    height = normalise_height(height)
    args = [
        binary, "-hide_banner", "-loglevel", "error", "-stats",
        "-y", "-i", source,
        "-r", "%.4f" % float(fps),
        "-vsync", "cfr",
    ]
    if height > 0:
        args += ["-vf", "scale=-2:'min(%d,ih)'" % height]
    args += ["-an", "-sn"]
    if image_format == "jpg":
        # 2 is the best the mjpeg encoder actually gives -- `-q:v 1` produces a
        # byte-identical file, measured, so there is nothing above this short
        # of png. Full chroma is where the remaining quality is; see
        # JPEG_PIXEL_FORMAT.
        args += ["-q:v", "2", "-pix_fmt", JPEG_PIXEL_FORMAT]
    args.append(pattern)
    return args


def convert(source, fps=DEFAULT_FPS, height=DEFAULT_HEIGHT,
            image_format=DEFAULT_FORMAT, override=None, progress=None,
            root=None):
    """Turn a video into an image sequence. Returns a Result, or None.

    `progress` is called as `progress(done, total)` with frame counts, and may
    return False to cancel -- which deletes the half-written folder rather than
    leaving frames that look like a short reference.

    Never raises. A drop handler has nowhere to report to, so every failure is
    a warning in the log and a None here.
    """
    import time

    binary = executable(override)
    if binary is None:
        log.warning("animkit: no ffmpeg, cannot convert %s", source)
        return None
    if not os.path.isfile(source):
        log.warning("animkit: %s is not on disk", source)
        return None

    if image_format not in FORMATS:
        image_format = DEFAULT_FORMAT
    fps = float(fps) if fps and float(fps) > 0 else DEFAULT_FPS
    # Normalised HERE and not only in build_args, because it is half the cache
    # key. `720` and `"720"` building the same command line but different keys
    # would convert the same video twice and serve neither from cache.
    height = normalise_height(height)

    base = root or cache_root()
    folder = os.path.join(base, cache_key(source, fps, height, image_format))

    hit = cached_sequence(folder, image_format)
    if hit:
        return Result(hit, folder, _count(folder, image_format), cached=True)

    details = probe(source, override=override) or {}
    total = 0
    if details.get("duration"):
        total = int(details["duration"] * fps) or 0

    try:
        if not os.path.isdir(folder):
            os.makedirs(folder)
    except Exception:
        log.warning("animkit: could not make the cache folder %r", folder,
                    exc_info=True)
        return None

    args = build_args(binary, source, folder, fps, height, image_format)
    started = time.time()
    cancelled = False
    try:
        process = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, bufsize=1, **_no_window()
        )
    except Exception:
        log.warning("animkit: could not start ffmpeg", exc_info=True)
        _discard(folder)
        return None

    tail = []
    try:
        for line in iter(process.stdout.readline, ""):
            tail.append(line.rstrip())
            del tail[:-12]
            match = _PROGRESS.search(line)
            if match and progress is not None:
                keep = progress(int(match.group(1)), total)
                if keep is False:
                    cancelled = True
                    process.terminate()
                    break
        process.wait()
    except Exception:
        log.warning("animkit: ffmpeg failed on %s", source, exc_info=True)
        try:
            process.kill()
        except Exception:
            pass
        _discard(folder)
        return None
    finally:
        try:
            process.stdout.close()
        except Exception:
            pass

    if cancelled:
        _discard(folder)
        return None

    written = _count(folder, image_format)
    if process.returncode != 0 or not written:
        log.warning("animkit: ffmpeg could not convert %s (exit %s): %s",
                    source, process.returncode, " / ".join(tail[-4:]))
        _discard(folder)
        return None

    try:
        with open(os.path.join(folder, "animkit.done"), "w") as handle:
            handle.write("%d\n" % written)
    except Exception:
        log.debug("animkit: could not write the cache marker", exc_info=True)

    first = os.path.join(folder, "frame.%s.%s"
                         % ("1".zfill(PADDING), image_format))
    if not os.path.isfile(first):
        log.warning("animkit: conversion of %s produced no frame 1", source)
        _discard(folder)
        return None
    return Result(first, folder, written, seconds=time.time() - started)


# --- audio -------------------------------------------------------------------


#: What Maya's audio node can actually read. Measured on 2024/Windows with a
#: three-second tone in each format: wav and aiff load and report 72 frames at
#: 24fps; mp3, m4a, ogg and flac all fail. And they fail with "cannot find
#: file" against a path that is plainly there, which is the worst possible
#: error for the case -- it sends an animator looking for a missing file.
AUDIO_READY = ("wav", "aiff", "aif", "aifc")

#: 16-bit PCM at the source's own rate. Uncompressed is the whole point; Maya
#: reads it, it scrubs without decoding, and a dialogue track is minutes long
#: rather than hours so the size does not matter.
AUDIO_CODEC = "pcm_s16le"


def audio_ready(path):
    """True when Maya can load this file as-is, so no conversion is needed."""
    return os.path.splitext(path or "")[1].lstrip(".").lower() in AUDIO_READY


def audio_cache_key(path):
    """Identity of a converted sound. Same content-plus-settings rule as video.

    No fps in the key, unlike the image sequence: a wav is a wav whatever the
    scene rate is, and Maya places it in frames from its own sample rate. So
    changing the scene rate does not re-convert audio, and should not.
    """
    try:
        stat = os.stat(path)
        stamp = "%d-%d" % (stat.st_size, int(stat.st_mtime))
    except Exception:
        stamp = "nostat"
    seed = "|".join([os.path.normcase(os.path.abspath(path)), stamp,
                     AUDIO_CODEC, "v%d" % ENCODER_VERSION])
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", os.path.basename(path))[:40]
    return "%s_%s.wav" % (stem, digest)


def build_audio_args(binary, source, target):
    """The ffmpeg command line for a sound. Separate so it can be asserted on.

    `-vn` because an mp3 with cover art is a video stream as far as ffmpeg is
    concerned, and without it the conversion tries to write one into a wav and
    fails on a file the animator considers pure audio.
    """
    return [binary, "-hide_banner", "-loglevel", "error", "-y",
            "-i", source, "-vn", "-acodec", AUDIO_CODEC, target]


def convert_audio(source, override=None, root=None):
    """Turn any sound into a wav Maya can read. Returns a path, or None.

    A file Maya already reads is returned untouched rather than copied -- there
    is nothing to gain by rewriting a wav, and a cache full of duplicates of
    files that were fine is a cache that costs disk for nothing.

    Never raises, for the same reason `convert` does not: the caller is a drop
    handler with nowhere to report to.
    """
    if not os.path.isfile(source):
        log.warning("animkit: %s is not on disk", source)
        return None
    if audio_ready(source):
        return source

    binary = executable(override)
    if binary is None:
        log.warning("animkit: no ffmpeg, cannot convert %s", source)
        return None

    base = root or cache_root()
    target = os.path.join(base, audio_cache_key(source))
    if os.path.isfile(target) and os.path.getsize(target) > 0:
        return target

    try:
        if not os.path.isdir(base):
            os.makedirs(base)
    except Exception:
        log.warning("animkit: could not make the cache folder %r", base,
                    exc_info=True)
        return None

    try:
        process = subprocess.Popen(
            build_audio_args(binary, source, target),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, **_no_window()
        )
        out, _ = process.communicate()
    except Exception:
        log.warning("animkit: could not run ffmpeg on %s", source,
                    exc_info=True)
        _discard_file(target)
        return None

    if process.returncode != 0 or not os.path.isfile(target):
        log.warning("animkit: ffmpeg could not convert %s (exit %s): %s",
                    source, process.returncode, (out or "").strip()[-300:])
        _discard_file(target)
        return None
    return target


def _discard_file(path):
    """Remove a half-written conversion. A partial wav is worse than none."""
    try:
        if os.path.isfile(path):
            os.remove(path)
    except Exception:
        log.debug("animkit: could not clear %r", path, exc_info=True)


# --- housekeeping ------------------------------------------------------------


def _count(folder, image_format):
    try:
        return len([name for name in os.listdir(folder)
                    if name.startswith("frame.")
                    and name.endswith("." + image_format)])
    except Exception:
        return 0


def _discard(folder):
    """Remove a half-written conversion. A partial cache is worse than none."""
    try:
        for name in os.listdir(folder):
            try:
                os.remove(os.path.join(folder, name))
            except Exception:
                pass
        os.rmdir(folder)
    except Exception:
        log.debug("animkit: could not clear %r", folder, exc_info=True)


# --- housekeeping -----------------------------------------------------------


def cache_size(root=None):
    """(folders, bytes) currently cached. What a Clear button reports."""
    base = root or cache_root()
    folders = 0
    total = 0
    try:
        for name in os.listdir(base):
            folder = os.path.join(base, name)
            if not os.path.isdir(folder):
                # A converted sound is one loose wav rather than a folder of
                # frames. Counted here so the panel's cache line reports the
                # whole cost and not just the video half of it.
                try:
                    total += os.path.getsize(folder)
                    folders += 1
                except Exception:
                    pass
                continue
            folders += 1
            for entry in os.listdir(folder):
                try:
                    total += os.path.getsize(os.path.join(folder, entry))
                except Exception:
                    continue
    except Exception:
        log.debug("animkit: could not measure the cache", exc_info=True)
    return (folders, total)


def clear_cache(root=None):
    """Delete every converted sequence. Returns how many folders went.

    Safe to call while a reference is up -- Maya will lose the images and draw
    the plane empty, which is recoverable by dropping the video again. Deleting
    the scene's references first is the caller's business, not this module's.
    """
    base = root or cache_root()
    gone = 0
    try:
        names = os.listdir(base)
    except Exception:
        return 0
    for name in names:
        folder = os.path.join(base, name)
        if os.path.isdir(folder):
            _discard(folder)
            if not os.path.isdir(folder):
                gone += 1
        else:
            _discard_file(folder)
            if not os.path.isfile(folder):
                gone += 1
    return gone
