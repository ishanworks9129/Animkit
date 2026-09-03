"""What a dropped file actually is: an image, a movie, or one frame of a sequence.

    from animkit.core import media
    items = media.resolve(["C:/ref/walk.0001.png"])   # -> one Item, 240 frames

Path arithmetic and one directory listing. Imports **no Maya and no Qt**, which
is why it runs in the fast test tier alongside `tools.blend` -- and it needs to
be tested hard, because every question here has an answer that looks obvious and
is wrong on a real drop.

WHY A DROP IS NOT A LIST OF FILES
---------------------------------
An animator with a 240-frame video reference has 240 PNGs in a folder. They
select all of them in Explorer and drag them in, because that is what selecting
a sequence looks like. Treated as a list of files that is **240 image planes**,
one per frame, all stacked on the camera -- a scene that has to be undone before
anything else can happen.

So the drop is collapsed: every path that is a frame of the same sequence
becomes ONE item. Dropping one frame of that sequence produces the identical
item, because the sequence is discovered from the *directory*, not from how many
files the animator happened to select. Both gestures mean the same thing and
this module makes them mean the same thing.

Dropping a *folder* means the same thing again -- one level is scanned and
whatever media is inside comes back collapsed the same way. An animator who has
just exported a sequence drags the folder, not the frames.

FINDING THE FRAME NUMBER: THE LAST DIGIT RUN, NOT THE FIRST
-----------------------------------------------------------
`shot_010.0001.exr` has two digit runs and only the second one is the frame.
A greedy or a lazy `.*` in front of `(\\d+)` both get this wrong -- lazy takes
`010`, greedy backtracks to the single digit `1` and reports a padding of 1. The
pattern here is anchored to the extension instead, so `\\d+` can only match a run
that is followed immediately by the extension. `010` cannot be it, because
`.0001.exr` is not an extension.

That single mistake is the difference between a reference that plays and one
that shows frame 1 for the whole shot, and it is invisible until you scrub.

PADDING IS WHAT MAYA WILL ACTUALLY LOOK FOR
-------------------------------------------
Maya substitutes the frame number using the padding of the name it was given,
so `ref.0001.png` makes it look for `ref.0086.png` and never for `ref.86.png`.
A directory holding both widths -- which happens when a sequence crosses 999
with three-digit padding, or when two renders were merged -- is therefore not
one sequence to Maya even though it looks like one to a person.

`sequence_of` reports the frames Maya will actually find (the ones matching the
dropped file's width) and sets `mixed_padding` so the caller can say why the
count is lower than the folder looks. Reporting the on-disk range instead would
be a number that disagrees with what the animator sees when they scrub.

GAPS ARE REPORTED, NOT REPAIRED
-------------------------------
A half-rendered sequence has holes. Maya holds the last good frame across them
without complaint, which reads as a reference that hitches rather than as
missing files. `Sequence.missing` is what lets the panel say so.
"""

import logging
import os
import re

log = logging.getLogger(__name__)

#: Stills Maya's image reader handles. Deliberately generous -- an extension
#: that is on this list and unreadable is caught by the loader, which asks Maya
#: whether the pixels arrived rather than trusting this set. An extension that
#: is MISSING from it is refused before Maya ever sees it, and that is the
#: failure worth avoiding, so err towards including.
IMAGE_EXTENSIONS = frozenset([
    "als", "bmp", "cin", "dds", "dpx", "eps", "exr", "gif", "hdr", "iff",
    "jpe", "jpeg", "jpg", "map", "pic", "pict", "png", "pnm", "ppm", "psd",
    "rgb", "rgba", "sgi", "tga", "tif", "tiff", "webp", "xpm", "yuv",
])

#: Movie containers. Whether Maya can decode one is NOT knowable from the
#: extension -- see tools.reference.load, which asks the image plane whether it
#: got any pixels and says so plainly when it did not.
MOVIE_EXTENSIONS = frozenset([
    "avi", "m4v", "mkv", "mov", "mp4", "mpeg", "mpg", "mxf", "webm", "wmv",
])

#: Audio containers. Same rule as movies: the extension says what an animator
#: CALLS it, never whether Maya can decode it. Measured on 2024/Windows, Maya's
#: audio node reads wav and aiff and refuses mp3, m4a, ogg and flac -- and it
#: refuses them with "cannot find file", pointing at a file that is plainly
#: there. So a dropped mp3 is converted before Maya ever sees it, exactly as a
#: dropped mp4 is.
AUDIO_EXTENSIONS = frozenset([
    "aif", "aifc", "aiff", "flac", "m4a", "mp3", "oga", "ogg", "opus", "wav",
    "wma",
])

KIND_IMAGE = "image"
KIND_MOVIE = "movie"
KIND_AUDIO = "audio"

#: The frame number is the digit run that the extension follows IMMEDIATELY.
#: See the module docstring -- this is the one piece of parsing here that has a
#: plausible wrong answer.
_FRAME = re.compile(r"(\d+)(\.[^.]+)$")

#: How many names of a folder scan are worth reading before it is fair to
#: assume the animator dropped a render output directory rather than a
#: reference. Purely a guard against a listdir of something enormous.
MAX_SCAN = 20000


# --- classification ---------------------------------------------------------


def extension(path):
    """The lowercase extension without its dot. '' when there is none."""
    return os.path.splitext(path or "")[1].lstrip(".").lower()


def kind(path):
    """KIND_IMAGE, KIND_MOVIE, KIND_AUDIO, or None for anything animkit will
    not load.

    Answers from the extension alone and touches no disk. Whether the file is
    readable is a different question with a different answer -- ask Maya.

    `m4a` is audio and `m4v` is video, which is the one pair here that a
    careless membership test gets wrong: they differ by a letter and land in
    completely different halves of the tool.
    """
    ext = extension(path)
    if ext in IMAGE_EXTENSIONS:
        return KIND_IMAGE
    if ext in MOVIE_EXTENSIONS:
        return KIND_MOVIE
    if ext in AUDIO_EXTENSIONS:
        return KIND_AUDIO
    return None


def is_media(path):
    return kind(path) is not None


def split_frame(path):
    """(head, digits, tail) for a numbered file, or None.

    The digit run is found in the basename only -- a directory called `2024`
    is not a frame number -- but `head` comes back with the directory still
    attached, so head + digits + tail rebuilds the path that went in.
    """
    folder, name = os.path.split(path or "")
    match = _FRAME.search(name)
    if match is None:
        return None
    head = name[:match.start(1)]
    return (os.path.join(folder, head), match.group(1), match.group(2))


# --- sequences --------------------------------------------------------------


class Sequence(object):
    """The frames of one image sequence that Maya will actually find.

    Holds no file handles and re-reads nothing; build a new one if the folder
    changes underneath. `example` is the path to hand an image plane -- Maya
    wants one real frame and derives the rest from its padding.
    """

    def __init__(self, head, padding, tail, frames, mixed_padding=False):
        self.head = head
        self.padding = padding
        self.tail = tail
        #: Sorted, de-duplicated, and only the frames Maya's padding will reach.
        self.frames = tuple(sorted(set(frames)))
        self.mixed_padding = mixed_padding

    @property
    def first(self):
        return self.frames[0] if self.frames else 0

    @property
    def last(self):
        return self.frames[-1] if self.frames else 0

    @property
    def count(self):
        return len(self.frames)

    @property
    def span(self):
        """How many frames the range covers, holes included."""
        return (self.last - self.first + 1) if self.frames else 0

    @property
    def missing(self):
        """Frames inside the range that are not on disk.

        Maya holds the previous frame across a hole rather than reporting one,
        so nothing else in the pipeline will mention these.
        """
        if not self.frames:
            return ()
        present = set(self.frames)
        return tuple(
            frame for frame in range(self.first, self.last + 1)
            if frame not in present
        )

    def path_for(self, frame):
        return "%s%0*d%s" % (self.head, self.padding, int(frame), self.tail)

    @property
    def example(self):
        """A frame that exists. What gets handed to `imagePlane -fileName`."""
        return self.path_for(self.first)

    @property
    def label(self):
        """`walk.####.png` -- the name a person uses for the whole sequence."""
        return "%s%s%s" % (
            os.path.basename(self.head), "#" * self.padding, self.tail
        )

    def __repr__(self):
        return "<Sequence %s %d-%d>" % (self.label, self.first, self.last)


def _listdir(folder):
    """Names in a folder, or [] for anything that cannot be listed.

    Never raises. A dropped path can name a share that is down, a drive that
    was unplugged, or a folder this user cannot read, and none of those is a
    reason for a drag-and-drop handler to throw.
    """
    try:
        names = os.listdir(folder or ".")
    except Exception:
        log.debug("animkit: could not list %r", folder, exc_info=True)
        return []
    if len(names) > MAX_SCAN:
        log.warning(
            "animkit: %r holds %d files -- reading the first %d looking for a "
            "sequence", folder, len(names), MAX_SCAN,
        )
        names = names[:MAX_SCAN]
    return names


def sequence_of(path):
    """The sequence `path` is one frame of, or None if it stands alone.

    None is returned for a numbered file with no numbered siblings, which is
    the common `poster_01.jpg` case -- a lone still that happens to have a
    digit in its name is a still, not a one-frame sequence.
    """
    parts = split_frame(path)
    if parts is None:
        return None
    head, digits, tail = parts
    padding = len(digits)
    folder, head_name = os.path.split(head)

    # Escaped, and anchored at both ends. A head of `shot[a]_` is a real
    # filename and also a perfectly good character class if it reaches a
    # pattern unescaped -- at which point the sequence silently has no frames.
    # Case-insensitive on Windows because the filesystem is: `Walk.0001.PNG`
    # and `walk.0002.png` are one sequence there and two anywhere else.
    pattern = re.compile(
        r"^%s(\d+)%s$" % (re.escape(head_name), re.escape(tail)),
        re.IGNORECASE if os.name == "nt" else 0,
    )

    widths = {}
    for name in _listdir(folder):
        match = pattern.match(name)
        if match is None:
            continue
        found = match.group(1)
        widths.setdefault(len(found), []).append(int(found))

    frames = widths.get(padding, [])
    if len(frames) < 2:
        return None
    return Sequence(head, padding, tail, frames,
                    mixed_padding=len(widths) > 1)


# --- what a drop resolves to ------------------------------------------------


class Item(object):
    """One thing to load: a still, a movie, or a whole sequence.

    `path` is always a real file on disk, because that is what Maya is given.
    For a sequence it is the first frame and `sequence` carries the range.
    """

    def __init__(self, path, media_kind, sequence=None):
        self.path = path
        self.kind = media_kind
        self.sequence = sequence

    @property
    def is_sequence(self):
        return self.sequence is not None

    @property
    def label(self):
        if self.sequence is not None:
            return self.sequence.label
        return os.path.basename(self.path)

    @property
    def first(self):
        return self.sequence.first if self.sequence else 1

    @property
    def last(self):
        return self.sequence.last if self.sequence else 1

    @property
    def frame_count(self):
        return self.sequence.count if self.sequence else 1

    @property
    def key(self):
        """What makes two dropped paths the same item.

        A sequence is identified by head+tail so that frame 1 and frame 200 of
        it collapse into one; anything else is identified by its own path,
        normalised, so `C:/ref/a.mov` and `C:\\ref\\A.MOV` are not two drops.
        """
        if self.sequence is not None:
            return (os.path.normcase(os.path.abspath(self.sequence.head)),
                    self.sequence.tail.lower())
        return (os.path.normcase(os.path.abspath(self.path)), "")

    def __repr__(self):
        return "<Item %s %s>" % (self.kind, self.label)


def item_for(path):
    """One Item for one path, or None if animkit will not load it.

    Only images are looked up as sequences. A folder of `take_01.mov` ..
    `take_09.mov` is nine takes, not nine frames of one, and collapsing them
    would silently discard eight of the animator's files.
    """
    media_kind = kind(path)
    if media_kind is None:
        return None
    if media_kind in (KIND_MOVIE, KIND_AUDIO):
        # Neither is ever a numbered sequence. `dialogue.001.wav` beside
        # `dialogue.002.wav` is two takes, not two frames of one.
        return Item(path, media_kind)

    found = sequence_of(path)
    if found is None:
        return Item(path, media_kind)
    return Item(found.example, media_kind, sequence=found)


def _expand(paths):
    """Directories become the media files directly inside them, one level.

    Not recursive on purpose. A `Documents` folder dropped by accident should
    produce nothing, not a walk of every image on the machine.
    """
    out = []
    for path in paths:
        if not path:
            continue
        try:
            is_folder = os.path.isdir(path)
        except Exception:
            is_folder = False
        if is_folder:
            for name in sorted(_listdir(path)):
                child = os.path.join(path, name)
                if is_media(child):
                    out.append(child)
        else:
            out.append(path)
    return out


def resolve(paths):
    """Turn a drop into the list of things to load, in the order dropped.

    Collapses a whole sequence to one item, drops files animkit cannot load,
    and never returns the same item twice. Returns [] rather than raising for a
    drop of nothing usable -- the caller is what says so out loud; this only
    decides what is there.
    """
    items = []
    seen = set()
    for path in _expand(list(paths or [])):
        item = item_for(path)
        if item is None:
            continue
        if item.key in seen:
            continue
        seen.add(item.key)
        items.append(item)
    return items


def rejected(paths):
    """The dropped paths animkit will not load. For saying what was ignored.

    A drop of a folder holding a movie and a text file must not report the
    text file -- the animator did not drop it. Only paths that were dropped
    directly can be rejected out loud.
    """
    return [path for path in (paths or [])
            if path and not is_media(path) and not os.path.isdir(path)]
