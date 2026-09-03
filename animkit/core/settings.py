"""User settings, persisted as JSON in the Maya prefs directory.

    from animkit.core import settings
    settings.get("tween.mode")
    settings.set("tween.mode", "linear")     # writes through to disk

THE DESIGN REQUIREMENT, WHICH IS NOT OPTIONAL
---------------------------------------------
`animkit.startup()` runs from userSetup.py. An exception there does not just
break animkit -- it degrades Maya launch for every animator with the module
installed, and it does so before there is any UI to report it in. A settings
file that is missing, empty, truncated by a crash, hand-edited into invalid
JSON, owned by another user, or sitting on a network share that is down must
therefore all end the same way: shipped defaults, one line in the log, startup
continues.

So nothing in this module raises on the read path. `load()` catches everything
and falls back.

That is also why values are TYPE-COERCED and not merely parsed. "It did not
raise while loading" is worth very little if it then hands the UI the string
"banana" where a float belongs and the widget raises three seconds later, at
which point the traceback points at the UI and not at the corrupt file that
caused it. A value that cannot be coerced to the shape of its default is
discarded and the default used.

Writes are atomic -- temp file, then os.replace -- because the failure this
module exists to survive is most easily created by crashing halfway through
writing the file it reads.
"""

import json
import logging
import os

log = logging.getLogger(__name__)

FILENAME = "settings.json"
DIRNAME = "animkit"

#: Every setting animkit knows about, with the value used when the file is
#: absent, unreadable, or holds nonsense. The TYPES here are load-bearing:
#: they are what an incoming value is coerced to. A key absent from this dict
#: is dropped on load rather than carried forward -- that keeps a typo in a
#: hand-edited file from silently shadowing a real setting.
DEFAULTS = {
    "tween.mode": "between",
    "tween.quick_buttons": [-1.0, -0.6, -0.3, 0.3, 0.6, 1.0],
    "tween.overshoot": 2.0,
    "tween.numeric_limit": 1.2,
    # Reference media. Opacity starts below 1 because the first thing an
    # animator does with a fresh reference plane is fade it enough to see the
    # rig through it, and depth starts near the far end of a default camera's
    # frustum so the plane is BEHIND the character rather than in front of it.
    "reference.opacity": 0.75,
    "reference.depth": 500.0,
    #: "free" or "camera". Free makes a top-level plane with working move,
    #: rotate and scale handles; camera pins it to the frame and it cannot be
    #: grabbed in the viewport at all. Free is the default because a reference
    #: an animator cannot move reads as a broken object rather than as a
    #: deliberate choice.
    "reference.attach": "free",
    #: Where a newly dropped reference STARTS on the timeline. One of:
    #:
    #:   "range"    the first frame of the playback range. The default.
    #:   "current"  the frame the animator is sitting on.
    #:   "native"   no offset at all -- the reference's own frame numbers land
    #:              on the timeline frames of the same number, so a render
    #:              numbered 101-200 sits at 101-200.
    #:
    #: "current" was the original behaviour and the default, on the reasoning
    #: that an animator sitting on a frame is sitting on the frame they want
    #: the reference to start at. In practice it reads as the tool moving the
    #: reference for reasons of its own: drop a video while parked on frame 59
    #: and it silently starts at 59, and the only visible trace is an offset of
    #: -58. Lining up with the start of the shot is the answer that needs no
    #: explaining, and Sync is one click away for the other case.
    "reference.start_at": "range",
    #: How a new FREE reference is oriented. One of:
    #:
    #:   "front"    rotate (0,0,0). Axis-aligned, facing world +Z, exactly what
    #:              Maya's own Create > Free Image Plane produces.
    #:   "upright"  vertical, yawed to face the camera: rotate (0, yaw, 0).
    #:   "view"     the camera's frame exactly, pitch and roll included.
    #:
    #: "front" is the default because it is the only one of the three that is
    #: PREDICTABLE. The other two depend on where the viewport happened to be
    #: pointing when the file was loaded, so the same drop gives a different
    #: transform every time and the board is foreshortened in the orthographic
    #: views -- through the default persp, "upright" yields rotate (0, 45, 0),
    #: which squashes the picture to 71% of its width in `front` and reads as
    #: the reference having been stretched.
    #:
    #: Snapping the yaw to the nearest axis instead was considered and refused:
    #: the default persp sits at exactly 45 degrees, so the snap would land on
    #: a knife edge and a one-degree orbit would flip the board 90 degrees.
    "reference.orient": "front",
    #: How far in front of the camera a new free plane is stood up, in scene
    #: units. 0 means "where the camera is looking" -- its centre of interest,
    #: which puts the reference on the origin for the front/side/top views and
    #: next to the character for a framed persp. A fixed distance is wrong
    #: exactly where it matters: the orthographic cameras sit 1000 units out,
    #: so 40 units in front of `front` is 960 units from the character.
    "reference.distance": 0.0,
    #: Whether dropping onto the Maya viewport itself is wired up. Off means
    #: the Ref tab's drop zone still works -- this only governs the event
    #: filter on the model panels, which is the part that touches Maya's own
    #: widgets and is therefore the part worth being able to switch off.
    "reference.viewport_drop": True,
    #: Convert a dropped video to an image sequence instead of handing it to
    #: Maya as a movie. On, because Maya cannot decode .mp4 on an image plane
    #: at all -- so off means the format everybody actually has draws nothing.
    "reference.convert_movies": True,
    #: Height cap for converted frames. Reference is looked at behind a rig at
    #: 30% opacity, not graded, so 720 is generous. Sources shorter than this
    #: are never upscaled.
    #:
    #: 0 means NATIVE -- no scaling at all. That is the right answer for the
    #: case 720 gets wrong: a screen recording or anything whose subject is
    #: text, where the downscale plus 4:2:0 JPEG is the difference between
    #: readable and not. It costs cache; the Ref tab says how much.
    "reference.convert_height": 720,
    #: "jpg" or "png". A 300-frame 1080p reference is ~45MB as jpg and ~300MB
    #: as png; png is for matching subtle colour, which reference rarely is.
    "reference.convert_format": "jpg",
    #: Convert a dropped sound to wav instead of handing it to Maya as it is.
    #: On, because Maya's audio node reads wav and aiff and nothing else --
    #: measured -- and it refuses an mp3 with "cannot find file", which sends
    #: an animator looking for a file that is right where they dropped it from.
    "audio.convert": True,
    #: An ffmpeg to use instead of the bundled one. Empty means "the one that
    #: ships with animkit, else whatever is on PATH". For a studio that has
    #: standardised on its own build.
    "reference.ffmpeg": "",
}

_cache = None


# --- location ---------------------------------------------------------------


def directory():
    """The animkit folder inside Maya's user prefs, or a home-dir fallback.

    The fallback matters for mayapy and batch jobs, where internalVar can
    return something unusable. Never raises.
    """
    base = None
    try:
        from maya import cmds

        base = cmds.internalVar(userPrefDir=True)
    except Exception:
        log.debug("animkit: internalVar unavailable, using home dir", exc_info=True)

    if not base:
        base = os.path.join(os.path.expanduser("~"), ".animkit")
        return base
    return os.path.join(base, DIRNAME)


def path():
    return os.path.join(directory(), FILENAME)


# --- coercion ---------------------------------------------------------------


def _coerce(value, default):
    """Force `value` into the shape of `default`, or raise ValueError.

    Callers treat a raise as "use the default". Bool is checked before int
    because bool IS an int in Python, and silently turning True into 1.0 in a
    float setting is the kind of thing that works until it does not.
    """
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        raise ValueError("expected bool")

    if isinstance(default, float):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("expected number")
        out = float(value)
        # NaN and infinity survive json.loads happily and then poison every
        # comparison downstream -- a clamp against NaN silently passes.
        if out != out or out in (float("inf"), float("-inf")):
            raise ValueError("not finite")
        return out

    if isinstance(default, int):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("expected int")
        return int(value)

    if isinstance(default, str):
        if not isinstance(value, str):
            raise ValueError("expected string")
        return value

    if isinstance(default, list):
        if not isinstance(value, list):
            raise ValueError("expected list")
        if not default:
            return list(value)
        element = default[0]
        return [_coerce(item, element) for item in value]

    if isinstance(default, dict):
        if not isinstance(value, dict):
            raise ValueError("expected object")
        return dict(value)

    raise ValueError("unsupported default type")


def _clean(raw):
    """Merge a parsed file over the defaults, discarding anything unusable."""
    out = dict(DEFAULTS)
    if not isinstance(raw, dict):
        log.warning("animkit: settings file is not a JSON object, using defaults")
        return out

    for key, default in DEFAULTS.items():
        if key not in raw:
            continue
        try:
            out[key] = _coerce(raw[key], default)
        except Exception as exc:
            log.warning(
                "animkit: setting %r is unusable (%s), using default %r",
                key, exc, default,
            )
    return out


# --- read / write -----------------------------------------------------------


def load(force=False):
    """Return the settings dict. Never raises, never returns None.

    Cached after the first call; pass force=True to re-read from disk.
    """
    global _cache
    if _cache is not None and not force:
        return _cache

    target = path()
    raw = None
    try:
        with open(target, "r") as handle:
            raw = json.load(handle)
    except IOError:
        log.debug("animkit: no settings file at %s, using defaults", target)
    except ValueError:
        log.warning(
            "animkit: settings file at %s is not valid JSON -- using defaults. "
            "Delete it, or run animkit.core.settings.reset(), to stop this "
            "warning.", target,
        )
    except Exception:
        # Permissions, a dead network share, a directory where a file should
        # be. All of it degrades to defaults rather than breaking startup.
        log.warning(
            "animkit: could not read settings at %s -- using defaults",
            target, exc_info=True,
        )

    _cache = _clean(raw) if raw is not None else dict(DEFAULTS)
    return _cache


def save():
    """Write the current settings. Returns True on success, never raises.

    Atomic: a crash partway through leaves the previous file intact rather
    than a truncated one that the next launch has to recover from.
    """
    data = load()
    target = path()
    temp = target + ".tmp"
    try:
        folder = os.path.dirname(target)
        if folder and not os.path.isdir(folder):
            os.makedirs(folder)
        with open(temp, "w") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
        os.replace(temp, target)
        return True
    except Exception:
        log.warning("animkit: could not save settings to %s", target, exc_info=True)
        try:
            if os.path.exists(temp):
                os.remove(temp)
        except Exception:
            pass
        return False


def get(key, default=None):
    """Read one setting. Falls back to DEFAULTS, then to `default`."""
    data = load()
    if key in data:
        return data[key]
    if key in DEFAULTS:
        return DEFAULTS[key]
    return default


def set(key, value, write=True):  # noqa: A001 - deliberate, reads as settings.set
    """Write one setting through to disk. Returns the value actually stored.

    An unknown key, or one whose value will not coerce, is refused and logged
    rather than stored -- otherwise the next load() would silently drop it and
    the caller would never find out why its setting keeps reverting.
    """
    if key not in DEFAULTS:
        log.warning("animkit: refusing to store unknown setting %r", key)
        return None

    try:
        coerced = _coerce(value, DEFAULTS[key])
    except Exception as exc:
        log.warning("animkit: refusing setting %r = %r (%s)", key, value, exc)
        return None

    data = load()
    if data.get(key) == coerced and not write:
        return coerced
    data[key] = coerced
    if write:
        save()
    return coerced


def update(values, write=True):
    """set() several at once with a single disk write."""
    stored = {}
    for key, value in values.items():
        result = set(key, value, write=False)
        if result is not None:
            stored[key] = result
    if write and stored:
        save()
    return stored


def reset(write=True):
    """Restore shipped defaults. The fix to hand someone with a broken file."""
    global _cache
    _cache = dict(DEFAULTS)
    if write:
        save()
    return _cache


def forget():
    """Drop the in-memory cache without touching the file. For tests."""
    global _cache
    _cache = None
