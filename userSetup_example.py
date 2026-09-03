r"""Example userSetup.py fragment.

Copy into (or append to) your Maya userSetup.py:
    Windows: %USERPROFILE%\Documents\maya\<version>\scripts\userSetup.py

Do NOT open any UI from here. userSetup.py runs before Maya's UI exists on
some startup paths, and Qt calls at this point fail on some machines and not
others -- the worst kind of bug to chase. Register commands and callbacks
only; let the animator open the panel.

executeDeferred is used so a failure here cannot stop Maya from starting.
"""

import maya.utils


def _animkit_startup():
    try:
        import animkit

        animkit.startup()
    except Exception:
        import traceback

        traceback.print_exc()
        print("animkit: startup failed; Maya will continue without it")


maya.utils.executeDeferred(_animkit_startup)
