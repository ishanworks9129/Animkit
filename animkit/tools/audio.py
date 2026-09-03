"""Sound on the timeline: drop an mp3, scrub to it, animate to it.

    from animkit.tools import audio
    audio.drop(["C:/ref/dialogue.mp3"])   # what drag-and-drop calls
    audio.set_start(101)                  # move it in time
    audio.toggle_mute()

The same gesture as video reference and the same shape of problem underneath,
which is why it lives beside it and shares its cache.

MAYA READS TWO AUDIO FORMATS AND NOBODY HAS EITHER
---------------------------------------------------
Measured on 2024/Windows with a three-second tone written in each format: `wav`
and `aiff` load and report 72 frames at 24fps. `mp3`, `m4a`, `ogg` and `flac`
all fail.

They fail in the worst possible way. `cmds.sound` raises **"cannot find file"**
against a path that is plainly on disk, so an animator who drops the mp3 their
director emailed them is sent looking for a missing file that is not missing.
That error is why this module converts rather than reporting: a dropped sound
goes through `core.transcode` and becomes a cached wav before Maya ever sees
it, and the animator sees a sound on their timeline instead of a lie about
their filesystem.

A file Maya can already read is used where it lies. There is nothing to gain by
rewriting a wav, and a cache full of copies of files that were fine is disk
spent on nothing.

ONE SOUND IS ON THE TIME SLIDER, THE REST ARE IN THE SCENE
-----------------------------------------------------------
Maya's time slider displays a single `audio` node -- that is a Maya limit, not
a choice made here. So a scene can hold several sounds and exactly one of them
is the one you hear, and `activate()` is what swaps them. The panel shows which
is live rather than leaving the animator to wonder why the second file they
dropped is silent.

RECOGNITION IS BY TAG, NEVER BY NAME
------------------------------------
Same rule as the rest of the codebase. A shot may already have an audio node
the layout department set up, and a tool that decided ownership by node type
would happily delete it on Remove.

TIME IS ONE ATTRIBUTE AND IT IS IN FRAMES
------------------------------------------
`audio.offset` is the timeline frame the sound starts on -- directly, with no
arithmetic, unlike the image plane's `frameOffset`. So "starts at" here is the
attribute itself, and `sync_to_current` is a `setAttr`.
"""

import logging
import os

from maya import cmds

from animkit.core import media, settings, transcode, undo
from animkit.tools import registry

log = logging.getLogger(__name__)

#: The tag that makes an audio node one of ours. Holds the display label.
TAG_ATTR = "animkitAudio"
#: The path exactly as dropped, before any conversion. Kept so the panel can
#: say where a sound came from when `filename` points into the cache.
SOURCE_ATTR = "animkitAudioSource"


class Clip(object):
    """One sound in the scene. Reads the node every time; caches nothing."""

    def __init__(self, node):
        self.node = node

    @property
    def label(self):
        try:
            return cmds.getAttr(self.node + "." + TAG_ATTR) or self.node
        except Exception:
            return self.node

    @property
    def source(self):
        try:
            return cmds.getAttr(self.node + "." + SOURCE_ATTR) or self.path
        except Exception:
            return self.path

    @property
    def path(self):
        try:
            return cmds.getAttr(self.node + ".filename") or ""
        except Exception:
            return ""

    @property
    def starts_at(self):
        """The timeline frame the sound starts on. Maya's own `offset`."""
        try:
            return int(round(float(cmds.getAttr(self.node + ".offset"))))
        except Exception:
            return 0

    @property
    def length(self):
        """How many frames long it is at the CURRENT scene rate, or 0.

        Asked of Maya rather than computed from the sample count, because the
        answer depends on the scene's frame rate and Maya is the thing that
        knows how it is placing the samples.
        """
        try:
            return int(round(float(cmds.sound(self.node, q=True,
                                              length=True) or 0)))
        except Exception:
            return 0

    @property
    def muted(self):
        try:
            return bool(cmds.getAttr(self.node + ".mute"))
        except Exception:
            return False

    @property
    def active(self):
        """True when this is the one the time slider is playing."""
        return active() == self.node

    def __repr__(self):
        return "<Clip %s @%d>" % (self.label, self.starts_at)


# --- finding them -----------------------------------------------------------


def _is_ours(node):
    try:
        return cmds.attributeQuery(TAG_ATTR, node=node, exists=True)
    except Exception:
        return False


def clips():
    """Every sound animkit put in this scene, in creation order."""
    found = []
    for node in cmds.ls(type="audio") or []:
        if _is_ours(node):
            found.append(Clip(node))
    return found


def _selected_clips():
    picked = []
    for node in cmds.ls(selection=True, long=True) or []:
        try:
            if cmds.nodeType(node) == "audio" and _is_ours(node):
                picked.append(Clip(node))
        except Exception:
            continue
    return picked


def targets():
    """What an operation acts on: the selection if it holds sounds, else all.

    Same rule as the reference tools, for the same reason -- an animator with
    nothing selected means "the sound", and there is usually only one.
    """
    picked = _selected_clips()
    return picked if picked else clips()


# --- the time slider --------------------------------------------------------


def _slider():
    """Maya's playback slider control, or None under mayapy.

    The name lives in a global MEL variable and there is no `cmds` route to it,
    which is why this is the one place here that touches MEL.
    """
    try:
        from maya import mel

        name = mel.eval("$animkitTmp = $gPlayBackSlider")
        return name or None
    except Exception:
        log.debug("animkit: no playback slider available", exc_info=True)
        return None


def active():
    """The node currently on the time slider, or None. Never raises."""
    slider = _slider()
    if slider is None:
        return None
    try:
        found = cmds.timeControl(slider, q=True, sound=True)
    except Exception:
        log.debug("animkit: could not read the time slider sound",
                  exc_info=True)
        return None
    return found or None


def activate(clip=None):
    """Put one sound on the time slider. That is what makes it audible.

    Maya shows one `audio` node at a time, so this is a swap and not an add.
    Passing None clears it, which is how the slider stops referring to a sound
    that has just been deleted.

    Returns True when the slider took it. False under mayapy, where there is no
    slider -- and that is not a failure worth a warning, because the node is in
    the scene either way and a batch session has nothing to play it on.
    """
    slider = _slider()
    if slider is None:
        return False
    node = clip.node if isinstance(clip, Clip) else clip
    try:
        cmds.timeControl(slider, edit=True, sound=node or "",
                         displaySound=bool(node))
    except Exception:
        log.debug("animkit: could not put %r on the time slider", node,
                  exc_info=True)
        return False
    return True


# --- loading ----------------------------------------------------------------


def _tag(node, label, source):
    if not cmds.attributeQuery(TAG_ATTR, node=node, exists=True):
        cmds.addAttr(node, longName=TAG_ATTR, dataType="string")
    cmds.setAttr(node + "." + TAG_ATTR, label, type="string")
    if not cmds.attributeQuery(SOURCE_ATTR, node=node, exists=True):
        cmds.addAttr(node, longName=SOURCE_ATTR, dataType="string")
    cmds.setAttr(node + "." + SOURCE_ATTR, source, type="string")


def _convert(path, label):
    """Turn a sound into a wav Maya can read. Returns a path, or None.

    No progress window, unlike the video path: converting a three-minute
    dialogue track to wav is well under a second, and a progress bar that
    flashes is worse than none.
    """
    if transcode.audio_ready(path):
        return path
    if not settings.get("audio.convert"):
        cmds.warning(
            "animkit: %s is not a format Maya reads, and conversion is off. "
            "Convert it to wav, or switch conversion back on." % label
        )
        return None

    override = settings.get("reference.ffmpeg") or None
    if not transcode.is_available(override):
        cmds.warning(
            "animkit: no ffmpeg, so %s cannot be converted. Maya reads wav "
            "and aiff only." % label
        )
        return None

    made = transcode.convert_audio(path, override=override)
    if made is None:
        cmds.warning(
            "animkit: could not convert %s -- see the Script Editor." % label
        )
    return made


def default_start():
    """The frame a newly dropped sound starts on.

    Shares `reference.start_at` rather than adding a second setting: an
    animator who wants reference to line up with the start of the shot wants
    the dialogue to line up with it too, and two settings that are always set
    the same way are one setting and a way to get them out of step.
    """
    mode = settings.get("reference.start_at")
    if mode == "current":
        try:
            return cmds.currentTime(q=True)
        except Exception:
            return 0
    try:
        return float(cmds.playbackOptions(q=True, minTime=True))
    except Exception:
        log.debug("animkit: no playback range to start from", exc_info=True)
        return 0


def load(path, frame=None, activate_it=True):
    """Put one sound on the timeline. Returns a Clip, or None.

    One undo step, and a lazy one -- a hotkey pressed on a file Maya cannot
    read must not leave an entry in the undo queue.
    """
    label = os.path.basename(path or "that")
    if not path or not os.path.isfile(path):
        cmds.warning("animkit: %s is not on disk" % label)
        return None
    if media.kind(path) != media.KIND_AUDIO:
        cmds.warning("animkit: %s is not a sound animkit can load" % label)
        return None

    playable = _convert(path, label)
    if playable is None:
        return None

    if frame is None:
        frame = default_start()

    with undo.LazyChunk("animkit: load audio") as chunk:
        chunk.open()
        try:
            node = cmds.sound(file=playable.replace("\\", "/"),
                              offset=float(frame))
        except Exception:
            # Maya says "cannot find file" for a file it simply cannot decode,
            # so the message is rewritten rather than passed on -- the original
            # sends an animator looking for a file that is right there.
            log.exception("animkit: Maya refused %s", playable)
            cmds.warning(
                "animkit: Maya could not read %s. It reads wav and aiff; if "
                "this was converted, the conversion produced something it "
                "still will not take -- see the Script Editor." % label
            )
            return None

        _tag(node, label, path)
        found = Clip(node)

    if activate_it:
        activate(found)
    _describe(found)
    return found


def drop(paths, frame=None):
    """Load every sound in a drop. Returns the Clip list.

    Nothing raises out of here: the caller is a drag-and-drop handler with
    nowhere to report to.
    """
    made = []
    sounds = [path for path in (paths or [])
              if media.kind(path) == media.KIND_AUDIO]
    if not sounds:
        return []

    with undo.undo_chunk("animkit: drop audio"):
        for index, path in enumerate(sounds):
            try:
                # Only the FIRST one is put on the slider. Activating each in
                # turn would leave the last file dropped playing, which is not
                # what "I dropped these in this order" means.
                found = load(path, frame=frame, activate_it=(index == 0))
            except Exception:
                log.exception("animkit: could not load %s", path)
                cmds.warning("animkit: could not load %s -- see the Script "
                             "Editor" % os.path.basename(path))
                continue
            if found is not None:
                made.append(found)

    if len(made) > 1:
        cmds.warning(
            "animkit: %d sounds loaded, and Maya plays one at a time. %s is "
            "the one on the timeline -- press On beside another to swap."
            % (len(made), made[0].label)
        )
    return made


def _describe(found):
    """Say what arrived. Silence after a drop reads as a drop that missed."""
    length = found.length
    parts = [found.label]
    if length:
        parts.append("frames %d-%d" % (found.starts_at,
                                       found.starts_at + length))
    if not found.active:
        parts.append("NOT on the timeline -- press On to hear it")
    message = "animkit: " + " -- ".join(parts)
    log.info(message)
    try:
        cmds.inViewMessage(assistMessage=message, position="midCenterBot",
                           fade=True)
    except Exception:
        pass


# --- operating on them ------------------------------------------------------


def _acted(found, verb):
    if found:
        return len(found)
    if clips():
        cmds.warning(
            "animkit: nothing to %s -- select the sound you mean, or deselect "
            "everything to act on all of them." % verb
        )
    else:
        cmds.warning(
            "animkit: no sound in this scene to %s. Drag an mp3 or a wav onto "
            "the viewport, or press Load in the Ref tab." % verb
        )
    return 0


def set_start(frame, nodes=None):
    """Put the sound's first sample on timeline frame `frame`."""
    found = nodes if nodes is not None else targets()
    if not found:
        return _acted(found, "move")
    with undo.undo_chunk("animkit: audio start frame"):
        for clip in found:
            cmds.setAttr(clip.node + ".offset", float(frame))
    return len(found)


def slip(frames=1, nodes=None):
    """Shift the sound in time. Positive moves it LATER."""
    found = nodes if nodes is not None else targets()
    if not found:
        return _acted(found, "slip")
    with undo.undo_chunk("animkit: slip audio"):
        for clip in found:
            cmds.setAttr(clip.node + ".offset",
                         float(clip.starts_at + int(frames)))
    return len(found)


def sync_to_current(nodes=None):
    """Start the sound on the frame the animator is sitting on."""
    found = nodes if nodes is not None else targets()
    if not found:
        return _acted(found, "sync")
    return set_start(cmds.currentTime(q=True), nodes=found)


def set_muted(muted, nodes=None):
    found = nodes if nodes is not None else targets()
    if not found:
        return _acted(found, "mute")
    with undo.undo_chunk("animkit: mute audio"):
        for clip in found:
            cmds.setAttr(clip.node + ".mute", bool(muted))
    return len(found)


def toggle_mute(nodes=None):
    """Flip ALL the targets to the same state rather than inverting each.

    Two sounds half-muted is a state nobody asked for. If any is audible, the
    gesture means mute.
    """
    found = nodes if nodes is not None else targets()
    if not found:
        return _acted(found, "mute")
    audible = any(not clip.muted for clip in found)
    return set_muted(audible, nodes=found)


def remove(nodes=None):
    """Delete the sounds animkit made. Touches no other audio node."""
    found = nodes if nodes is not None else targets()
    if not found:
        return _acted(found, "remove")

    doomed = [clip.node for clip in found if cmds.objExists(clip.node)]
    if not doomed:
        return 0
    # Take it off the slider FIRST. A slider left pointing at a deleted node
    # keeps its waveform drawn and plays nothing.
    if active() in doomed:
        activate(None)
    with undo.undo_chunk("animkit: remove audio"):
        cmds.delete(doomed)

    remaining = clips()
    if remaining and active() is None:
        activate(remaining[0])
    return len(doomed)


# --- the registry -----------------------------------------------------------

OPERATIONS = (
    registry.Operation(
        "animkitAudioSync", "Sound Sync",
        "Start the sound on the current frame",
        sync_to_current, group="Ref",
    ),
    registry.Operation(
        "animkitAudioMute", "Mute",
        "Mute or unmute the sound without removing it",
        toggle_mute, group="Ref",
    ),
    registry.Operation(
        "animkitAudioSlipBack", "Sound -1",
        "Move the sound one frame EARLIER",
        slip, {"frames": -1}, group="Ref",
    ),
    registry.Operation(
        "animkitAudioSlipForward", "Sound +1",
        "Move the sound one frame LATER",
        slip, {"frames": 1}, group="Ref",
    ),
    registry.Operation(
        "animkitAudioRemove", "Sound Remove",
        "Delete the sounds animkit made. Touches no other audio node",
        remove, group="Ref", destructive=True,
    ),
)

BY_NAME = dict((op.name, op) for op in OPERATIONS)
