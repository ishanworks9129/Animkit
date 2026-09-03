"""runTimeCommand registration.

Register every user-facing entry point as a runTimeCommand rather than binding
hotkeys directly. Two reasons, both learned the hard way:

  1. Animators rebind everything. A runTimeCommand shows up in the Hotkey
     Editor under its own category, so they can rebind it without editing your
     code -- and without you having to guess which keys their studio already
     uses.
  2. Binding a hotkey directly stomps whatever was on that key. Do that to a
     lead's muscle memory once and your tool is uninstalled by lunchtime.

This module ships NO default hotkeys. Suggested bindings live in the README
for the animators to apply, or for a studio-wide hotkey set to carry.
"""

import logging

from maya import cmds

log = logging.getLogger(__name__)

CATEGORY = "Custom Scripts.animkit"

COMMANDS = (
    (
        "animkitShow",
        "Open the animkit panel -- Tween, Keys, Pose and Sets in one dock",
        "import animkit.ui.panel as m; m.show()",
    ),
    (
        "animkitTweenShow",
        "Open the animkit Tween panel",
        "import animkit.ui.tween_ui as m; m.show()",
    ),
    (
        "animkitTweenPrev30",
        "Tween 30% toward the previous key",
        "import animkit.tools.tween as t; t.tween_once(-0.3)",
    ),
    (
        "animkitTweenNext30",
        "Tween 30% toward the next key",
        "import animkit.tools.tween as t; t.tween_once(0.3)",
    ),
    (
        "animkitTweenPrev60",
        "Tween 60% toward the previous key",
        "import animkit.tools.tween as t; t.tween_once(-0.6)",
    ),
    (
        "animkitTweenNext60",
        "Tween 60% toward the next key",
        "import animkit.tools.tween as t; t.tween_once(0.6)",
    ),
    (
        "animkitTweenAverage",
        "Flatten the pose toward the average of its neighbours",
        "import animkit.tools.tween as t, animkit.tools.blend as b; "
        "t.tween_once(1.0, mode=b.MODE_AVERAGE)",
    ),
    (
        "animkitKeysShow",
        "Open the animkit Keys panel",
        "import animkit.ui.keys_ui as m; m.show()",
    ),
    (
        "animkitSetsShow",
        "Open the animkit selection Sets panel",
        "import animkit.ui.sets_ui as m; m.show()",
    ),
    (
        "animkitRefShow",
        "Open the animkit Reference panel -- drop a video or image sequence "
        "onto a viewport",
        "import animkit.ui.reference_ui as m; m.show()",
    ),
    (
        "animkitRefDropInstall",
        "Let files be dropped straight onto a 3D viewport. Idempotent -- run "
        "it again after creating a new model panel",
        "import animkit.ui.viewport_drop as m; m.set_enabled(True)",
    ),
    (
        "animkitRefDropUninstall",
        "Stop animkit taking drops on the 3D viewports. The Ref tab's drop "
        "zone keeps working",
        "import animkit.ui.viewport_drop as m; m.set_enabled(False)",
    ),
    (
        "animkitRefClearCache",
        "Delete every video animkit has converted to an image sequence. A "
        "reference still up will draw nothing until the video is dropped again",
        "import animkit.core.transcode as m; "
        "print('animkit: cleared %d clip(s)' % m.clear_cache())",
    ),
    (
        "animkitRadialPose",
        "Show the pose radial while this key is held (bind with a releaseName "
        "of animkitRadialRelease)",
        "import animkit.ui.radial as r; r.show('pose')",
    ),
    (
        "animkitRadialKeys",
        "Show the keyframe radial while this key is held (bind with a "
        "releaseName of animkitRadialRelease)",
        "import animkit.ui.radial as r; r.show('keys')",
    ),
    (
        "animkitRadialSets",
        "Show this rig's selection sets as a radial while this key is held "
        "(bind with a releaseName of animkitRadialRelease)",
        "import animkit.ui.radial as r; r.show('sets')",
    ),
    (
        "animkitRadialRelease",
        "Fire the highlighted radial wedge and hide it. Bind this as the "
        "RELEASE command of a radial hotkey, never on its own.",
        "import animkit.ui.radial as r; r.release()",
    ),
)


def _set_commands():
    """Selection-set commands, from the registry in tools.sets.

    The recall slots are generated from sets.MAX_SLOTS, so a slot cannot exist
    without a runTimeCommand to reach it -- which is the whole reason recall is
    addressed by slot rather than by name. A hotkey is bound at startup, before
    any scene is open, so it cannot name a set that does not exist yet.
    """
    try:
        from animkit.tools import sets
    except Exception:
        log.exception("animkit: could not load selection-set operations")
        return ()
    return tuple((op.name, op.tooltip, op.command) for op in sets.OPERATIONS)


def _reference_commands():
    """Every reference operation, from the registry in tools.reference.

    Slip back and slip forward are the two worth a hotkey: lining a video
    reference up with the animation is a nudge repeated until it matches, and
    reaching for a button between each one is what makes people stop doing it.
    """
    try:
        from animkit.tools import reference
    except Exception:
        log.exception("animkit: could not load reference operations")
        return ()
    return tuple((op.name, op.tooltip, op.command) for op in reference.OPERATIONS)


def _audio_commands():
    """Every audio operation, from the registry in tools.audio.

    Separate from the reference registry because they act on different nodes
    and one can be present without the other -- a shot with dialogue and no
    video reference is ordinary.
    """
    try:
        from animkit.tools import audio
    except Exception:
        log.exception("animkit: could not load audio operations")
        return ()
    return tuple((op.name, op.tooltip, op.command) for op in audio.OPERATIONS)


def _pose_commands():
    """Every pose operation, from the registry in tools.pose. See below."""
    try:
        from animkit.tools import pose
    except Exception:
        log.exception("animkit: could not load pose operations")
        return ()
    return tuple((op.name, op.tooltip, op.command) for op in pose.OPERATIONS)


def _keyframe_commands():
    """Every keyframe operation, straight from the registry in tools.keys.

    Generated rather than listed so a new operation cannot be added without
    also being bindable -- the registry is the single source of truth for the
    panel, the hotkeys and the tests alike.

    Imported lazily inside the function: this module is imported by startup(),
    which runs from userSetup.py, and a failure to import the tools layer
    there must not take Maya's launch with it.
    """
    try:
        from animkit.tools import keys
    except Exception:
        log.exception("animkit: could not load keyframe operations")
        return ()
    return tuple(
        (op.name, op.tooltip, op.command) for op in keys.OPERATIONS
    )


COMMANDS = (
    COMMANDS + _keyframe_commands() + _pose_commands() + _set_commands()
    + _reference_commands() + _audio_commands()
)


def register(verbose=False):
    """Idempotent. Safe to call from userSetup.py and again after a reload."""
    made = []
    for name, annotation, command in COMMANDS:
        try:
            if cmds.runTimeCommand(name, q=True, exists=True):
                cmds.runTimeCommand(name, e=True, delete=True)
            cmds.runTimeCommand(
                name,
                annotation=annotation,
                category=CATEGORY,
                command=command,
                commandLanguage="python",
                default=False,
            )
            made.append(name)
        except Exception:
            log.exception("animkit: could not register runTimeCommand %s", name)

    if verbose:
        print("animkit: registered %d commands under '%s'" % (len(made), CATEGORY))
    return made


def unregister():
    for name, _annotation, _command in COMMANDS:
        try:
            if cmds.runTimeCommand(name, q=True, exists=True):
                cmds.runTimeCommand(name, e=True, delete=True)
        except Exception:
            pass
