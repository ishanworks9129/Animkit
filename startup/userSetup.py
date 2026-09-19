r"""animkit startup, carried by the module rather than by the animator.

WHY THIS FOLDER EXISTS AND WHY IT IS NOT `scripts/`
    Maya executes every `userSetup.py` it finds on MAYA_SCRIPT_PATH at launch,
    and a module's `scripts/` folder is added to that path automatically. So
    the usual advice is to put this file in `scripts/`.

    That would also put the development scripts -- bench.py, rig_probe.py --
    on every animator's import path, where `import bench` is a name collision
    waiting to happen in somebody else's pipeline. This folder holds one file
    and is added explicitly by [modules/animkit.mod](../modules/animkit.mod):

        MAYA_SCRIPT_PATH +:= startup

WHAT IT REPLACES
    [userSetup_example.py](../userSetup_example.py), which asks the animator
    to paste a fragment into their own userSetup.py. That still works and is
    still the right answer for a studio with a managed userSetup. This file is
    for everyone else: install the module, get the commands, edit nothing.

THE RULES HERE ARE NOT STYLE PREFERENCES
    1. No UI. userSetup.py runs before Maya's UI exists on some startup paths,
       and a Qt call at this point fails on some machines and not others --
       the worst kind of bug to chase.
    2. executeDeferred, so scene work happens once Maya is actually up.
    3. Catch everything. A tool that breaks Maya's launch is uninstalled by
       lunchtime, and rightly so.
"""

import maya.utils


def _animkit_startup():
    try:
        import animkit

        animkit.startup()

        # The two Qt-shaped steps, and only ever after startup(). It
        # registers commands and callbacks and touches no Qt; these need
        # Maya's UI to exist, which by the time this deferred call runs it
        # does.
        #
        # All three live inside one deferred call rather than three, so the
        # order is not at the mercy of Maya's idle queue: a panel cannot be
        # built before the commands its buttons carry are registered.
        animkit.open_startup_ui()

        # Arms the viewport for dropped video and images. Without it the drop
        # falls through to Maya, which tries to open the .mp4 as a scene and
        # reports "No translator found" -- and the animator concludes the
        # feature is broken.
        animkit.install_viewport_drop()
    except Exception:
        import traceback

        traceback.print_exc()
        print("animkit: startup failed; Maya will continue without it")


maya.utils.executeDeferred(_animkit_startup)
