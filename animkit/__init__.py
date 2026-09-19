"""animkit - animation tooling for Autodesk Maya.

Package name is deliberately NOT 'animbot' so this can be installed alongside
a real Animbot licence without a sys.modules collision.

Supported: Maya 2022-2026 (Python 3.7-3.11, PySide2 and PySide6).
"""

__version__ = "0.1.0"

_SUBMODULES = (
    "animkit.vendor.qt",
    "animkit.core.undo",
    "animkit.core.scene",
    "animkit.core.cache",
    "animkit.core.settings",
    "animkit.core.usage",
    "animkit.core.media",
    "animkit.core.transcode",
    "animkit.core.layers",
    "animkit.core.curves",
    "animkit.core.selection",
    "animkit.core.targets",
    "animkit.core.xform",
    "animkit.core.pairing",
    "animkit.core.rest_store",
    "animkit.tools.registry",
    "animkit.tools.blend",
    "animkit.tools.tween",
    "animkit.tools.keys",
    "animkit.tools.pose",
    "animkit.tools.sets",
    "animkit.tools.reference",
    "animkit.commands",
    "animkit.ui.mayawin",
    "animkit.ui.style",
    "animkit.ui.icons",
    "animkit.ui.radial_geom",
    "animkit.ui.radial",
    "animkit.ui.tween_ui",
    "animkit.ui.keys_ui",
    "animkit.ui.sets_ui",
    "animkit.ui.viewport_drop",
    "animkit.ui.reference_ui",
    "animkit.ui.panel",
)


def reload_all():
    """Drop every animkit module from sys.modules.

    Maya holds imported modules for the life of the session, so this is the
    dev-iteration escape hatch: call it, then re-import. Close any open UI
    first -- live QWidgets keep references to the old classes.
    """
    import sys

    for name in list(sys.modules):
        if name == "animkit" or name.startswith("animkit."):
            del sys.modules[name]


def startup(verbose=False):
    """Idempotent session setup. Safe to call from userSetup.py.

    Deliberately does no UI work -- userSetup.py runs before Maya's UI exists,
    so anything that touches Qt here will fail on some machines and not
    others, which is the worst possible failure mode to debug.
    """
    from animkit.core import rest_store, scene, settings, usage
    from animkit import commands

    # Deliberately first, and deliberately not wrapped in a try/except here:
    # settings.load() is contractually incapable of raising, and catching
    # around it would only hide a regression in that guarantee. See
    # animkit.core.settings for why that guarantee matters at userSetup time.
    settings.load()

    # Registered BEFORE the callbacks are installed, so a scene opened during
    # this same startup cannot slip between the two.
    rest_store.install()
    scene.install_callbacks()
    commands.register(verbose=verbose)

    # After settings.load(), because it asks settings whether it is even on,
    # and it is a no-op if it is not. Writes one line per Maya session.
    usage.session_start()

    # And load whatever the already-open scene carries. install() only wires
    # the callback, which fires on the NEXT open -- startup() itself usually
    # runs with a scene already loaded.
    rest_store.load()
