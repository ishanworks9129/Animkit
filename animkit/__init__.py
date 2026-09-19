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


def install_viewport_drop():
    """Arm the viewport for dropped media. Never raises. Returns the panels.

    WHY THIS IS NOT IN startup(), AND WHY IT IS NOT ONLY IN THE REF TAB.
    It installs a Qt event filter on Maya's model panels, so it cannot go in
    `startup()` -- that runs from userSetup.py before Maya has a UI, and there
    are no model panels to hook yet.

    For a long time its only caller was the Ref tab building, which is
    correct and insufficient: "drag a video onto the viewport" is a headline
    feature, and until somebody opened that one tab the drop fell through to
    Maya's own handler, which tried to open the .mp4 as a scene file and said
    "No translator found." A tester reads that as the feature being broken,
    and they are not wrong.

    So it runs here too -- deferred, after Maya's UI exists, from
    startup/userSetup.py and from the installer. `install_if_wanted` honours
    the `reference.viewport_drop` setting, so turning it off still turns it
    off, and `install()` is idempotent, so arming it twice costs nothing.
    """
    import logging

    log = logging.getLogger(__name__)

    try:
        from animkit.ui import viewport_drop

        return viewport_drop.install_if_wanted()
    except Exception:
        # A Maya with no model panels, a Qt that will not take the filter.
        # The Ref tab's own drop zone still works and so does everything
        # else; this is a degraded feature, not a broken launch.
        import traceback

        traceback.print_exc()
        log.warning("animkit: could not arm the viewport for dropped media")
        return []


#: What `ui.open_at_startup` may say, and what each one opens.
_STARTUP_UI = {
    "strip": ("animkit.ui.strip",),
    "panel": ("animkit.ui.panel",),
    "both": ("animkit.ui.strip", "animkit.ui.panel"),
}


def open_startup_ui():
    """Open whatever `ui.open_at_startup` names. Never raises.

    DELIBERATELY NOT CALLED FROM startup(). That function runs from
    userSetup.py, where Maya's UI does not reliably exist yet, and the rule
    that it touches no Qt is the reason animkit cannot break a Maya launch.
    This is the other half: called separately, later, once Maya is idle --
    see startup/userSetup.py.

    Opening nothing is the default and the common case, so the import of any
    UI module happens only after the setting has been read and found to ask
    for one. A tool that drags Qt into every Maya launch to then open nothing
    has made every animator's startup slower for no one's benefit.
    """
    import logging

    log = logging.getLogger(__name__)

    try:
        from animkit.core import settings

        wanted = (settings.get("ui.open_at_startup") or "").strip().lower()
        if not wanted:
            return ()

        modules = _STARTUP_UI.get(wanted)
        if modules is None:
            log.warning(
                "animkit: ui.open_at_startup is %r, which is not one of %s -- "
                "opening nothing", wanted, ", ".join(sorted(_STARTUP_UI)))
            return ()

        import importlib

        opened = []
        for name in modules:
            try:
                importlib.import_module(name).show()
                opened.append(name)
            except Exception:
                # One panel failing must not cost the other, and neither may
                # cost the animator their Maya.
                import traceback

                traceback.print_exc()
                log.warning("animkit: could not open %s at startup", name)
        return tuple(opened)
    except Exception:
        import traceback

        traceback.print_exc()
        print("animkit: open_startup_ui failed; Maya is unaffected")
        return ()
