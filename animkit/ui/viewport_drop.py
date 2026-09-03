"""Drag a file from Explorer onto Maya and get reference or sound from it.

    from animkit.ui import viewport_drop
    viewport_drop.install()      # idempotent
    viewport_drop.uninstall()

An event filter on each `modelPanel`'s widget, and on the TIME SLIDER. Qt
delivers drag events to the innermost widget under the cursor that has
`acceptDrops`, and neither Maya's 3D view nor its time slider does, so
switching it on for them is what makes a drop land there at all.

TWO TARGETS, BECAUSE THEY MEAN DIFFERENT THINGS
------------------------------------------------
A drop on a VIEWPORT means "put this in front of that camera", so it carries
the camera. A drop on the TIME SLIDER means "start this here", so it carries a
frame -- read off the cursor's position across the visible playback range, the
same way an animator reads the slider itself. Sound is what makes the second
one worth having: lining a dialogue track up with a shot is a frame decision
and nothing else, and dropping it where it belongs beats dropping it anywhere
and typing the number afterwards.

Everything else about the two is identical, which is why they share a filter
rather than having one each.

WHY A FILTER PER PANEL AND NOT ONE ON THE MAIN WINDOW
-----------------------------------------------------
`installEventFilter` only sees events delivered to the object it was installed
on -- never to that object's children. Catching a drop over a viewport from the
main window therefore needs a filter on `QApplication`, which then sees every
mouse move Maya makes for the rest of the session. That is a real cost paid all
day for an event that happens twice.

One filter per panel costs nothing, and it carries the panel's name -- which is
how a drop knows which camera it landed on without any hit-testing. What it
does not survive is a modelPanel created after `install()` ran. Maya makes its
four at startup and reuses them for every layout, so in practice that is a
panel the animator explicitly created; `install()` is idempotent and re-scans,
so re-running it is the fix, and both the checkbox in the Ref tab and
`animkitRefDropInstall` do exactly that.

THE TRAP: THE FILTER MUST BE KEPT ALIVE
---------------------------------------
`widget.installEventFilter(SomeFilter())` binds nothing on the Python side.
The QObject is garbage collected on the next sweep, the C++ side is left with a
dangling filter that Qt quietly drops, and drops stop working minutes after they
started -- with no error anywhere. `_INSTALLED` exists to hold the references.

THE OTHER TRAP: MIME DATA DIES WITH THE EVENT
---------------------------------------------
The paths are read out of the event into plain strings *before* the scene work
is deferred. `QMimeData` belongs to the drag, which the OS tears down as soon
as the handler returns, so a deferred callback holding the event reads freed
memory -- which is a crash, not an exception.

The scene work is deferred because a drop handler runs inside the platform's
drag loop. Building image planes there blocks the OS drag machinery for as long
as it takes to read the first frame off a network share, and Explorer's drag
cursor stays stuck to the pointer while it does.
"""

import logging
import os

from maya import cmds

from animkit.core import media, settings
from animkit.ui import mayawin
from animkit.vendor import qt

QtCore = qt.QtCore

log = logging.getLogger(__name__)

#: panel name -> [(filter, widget, previous acceptDrops), ...]. Module level
#: because it is what keeps the filter objects alive -- see the docstring.
#: A LIST per panel, not one entry: see `_drop_targets`.
_INSTALLED = {}


def _local_paths(event):
    """The dropped files, as plain strings. [] for a drag of anything else.

    Text dragged out of a browser, a node dragged out of the Outliner and a
    file dragged from Explorer all arrive through the same handler; only the
    last has local URLs.
    """
    try:
        mime = event.mimeData()
        if mime is None or not mime.hasUrls():
            return []
        out = []
        for url in mime.urls():
            try:
                if not url.isLocalFile():
                    continue
                path = url.toLocalFile()
            except Exception:
                continue
            if path:
                out.append(path)
        return out
    except Exception:
        log.debug("animkit: could not read dropped mime data", exc_info=True)
        return []


def _worth_taking(paths):
    """Whether this drag is animkit's, decided as cheaply as possible.

    Runs on DragEnter, so it must not touch the contents of a folder -- that
    is a directory scan while the animator is still moving the mouse. The
    extension check and one `isdir` are enough to decide; what is actually in
    a dropped folder is `core.media`'s question at drop time.

    Anything else is passed straight through, so dropping a .ma on the
    viewport still means whatever Maya means by it.
    """
    for path in paths:
        if media.is_media(path):
            return True
        try:
            if os.path.isdir(path):
                return True
        except Exception:
            continue
    return False


#: The pseudo-panel name the time slider is filed under in `_INSTALLED`.
#: Not a modelPanel, so it cannot collide with one.
TIMELINE = "<timeline>"


def time_slider():
    """Maya's playback slider control name, or None under mayapy.

    The name lives in a global MEL variable and there is no `cmds` route to it,
    which is why this is the one place in this module that touches MEL.
    """
    try:
        from maya import mel

        name = mel.eval("$animkitTmp = $gPlayBackSlider")
        return name or None
    except Exception:
        log.debug("animkit: no playback slider available", exc_info=True)
        return None


def frame_at(fraction, start, end):
    """The timeline frame `fraction` of the way across the time slider.

    Pure arithmetic and no widget, so the mapping is testable directly -- which
    matters because an off-by-one here lands a dialogue track a frame out and
    an animator would blame the sound, not the drop.

    Clamped rather than extrapolated: a drop registered a pixel outside the
    control belongs at the end of the range, not past it.
    """
    fraction = min(1.0, max(0.0, float(fraction)))
    start, end = float(start), float(end)
    return int(round(start + fraction * (end - start)))


def _drop_frame(widget, event):
    """The frame under the cursor for a drop on the time slider, or None.

    None whenever the answer cannot be trusted -- a dead widget, no playback
    range, a Qt version whose event carries the position under a different
    name. The caller then falls back to the ordinary "where do drops start"
    setting, which is a worse answer but never a wrong-looking one.
    """
    try:
        width = float(widget.width())
        if width < 1.0:
            return None
        # Qt6 renamed pos() to position(); both exist on Qt5's event, but only
        # position() on Qt6, so it is tried first.
        try:
            x = float(event.position().x())
        except Exception:
            x = float(event.pos().x())
        start = cmds.playbackOptions(q=True, minTime=True)
        end = cmds.playbackOptions(q=True, maxTime=True)
    except Exception:
        log.debug("animkit: could not read the drop position", exc_info=True)
        return None
    if end <= start:
        return None
    return frame_at(x / width, start, end)


class _DropFilter(QtCore.QObject):
    """Accepts a media drag over one viewport and loads it on drop."""

    def __init__(self, panel, parent=None):
        super(_DropFilter, self).__init__(parent)
        self.panel = panel
        #: Decided once per drag on DragEnter. DragMove fires on every mouse
        #: move and re-deciding there would stat the dropped paths hundreds of
        #: times for an answer that cannot have changed.
        self._taking = False

    def eventFilter(self, obj, event):
        try:
            kind = event.type()
        except Exception:
            return False

        if kind == QtCore.QEvent.DragEnter:
            self._taking = _worth_taking(_local_paths(event))
            if self._taking:
                event.acceptProposedAction()
                return True
            return False

        if kind == QtCore.QEvent.DragMove:
            if self._taking:
                event.acceptProposedAction()
                return True
            return False

        if kind == QtCore.QEvent.DragLeave:
            self._taking = False
            return False

        if kind == QtCore.QEvent.Drop:
            paths = _local_paths(event)
            self._taking = False
            if not _worth_taking(paths):
                return False
            event.acceptProposedAction()
            # Read the position HERE, not in the deferred call. The event is
            # torn down the moment this handler returns -- the same reason the
            # paths are read out as plain strings; see the module docstring.
            frame = None
            if self.panel == TIMELINE:
                frame = _drop_frame(obj, event)
            self._defer(paths, frame)
            return True

        return False

    def _defer(self, paths, frame=None):
        """Hand the paths to the tools layer once the drag loop has let go.

        `paths` is already a list of plain strings by the time it gets here --
        see the module docstring on why holding the event would not be.
        """
        panel = self.panel

        def _run():
            try:
                camera = None
                if panel != TIMELINE and cmds.modelPanel(panel, exists=True):
                    camera = cmds.modelPanel(panel, q=True, camera=True)
                from animkit.tools import reference

                reference.drop(paths, camera=camera, frame=frame)
            except Exception:
                log.exception("animkit: drop failed")
                try:
                    cmds.warning(
                        "animkit: could not load what was dropped -- see the "
                        "Script Editor"
                    )
                except Exception:
                    pass

        try:
            cmds.evalDeferred(_run, lowestPriority=True)
        except Exception:
            # No idle queue to defer onto (batch, or a Maya that refuses a
            # callable). Running inline is worse than deferring and better
            # than dropping the animator's file on the floor.
            log.debug("animkit: evalDeferred unavailable, loading inline",
                      exc_info=True)
            _run()


# --- install / uninstall ----------------------------------------------------


def _panel_widget(panel):
    """The Qt widget `MQtUtil` resolves for a modelPanel name, or None."""
    try:
        return mayawin.find_control(panel)
    except Exception:
        log.debug("animkit: no control for panel %r", panel, exc_info=True)
        return None


def _view_widget(panel):
    """The widget Maya actually DRAWS the 3D view into, or None.

    Not the same object as the panel's control, and the difference is the whole
    reason drops onto the viewport did nothing.
    """
    try:
        import maya.api.OpenMayaUI as omui2

        view = omui2.M3dView.getM3dViewFromModelPanel(panel)
        return qt.as_qwidget(view.widget())
    except Exception:
        log.debug("animkit: no M3dView widget for panel %r", panel,
                  exc_info=True)
        return None


def _drop_targets(panel):
    """Every widget of this panel that a drag could be delivered to.

    THE BUG THIS FIXES
    ------------------
    Hooking only the panel's control looked right and did nothing. Qt delivers
    a drag to the innermost widget under the cursor and walks UP the parent
    chain for one that accepts drops -- but that walk stops at a native window,
    and Maya's 3D view is one: it is a separate QWindow with its own OLE drop
    target on Windows. So the drag over a viewport was delivered to the view
    widget, found no `acceptDrops` there, and was never offered to the panel
    control the filter was sitting on. Dropping on the Ref tab's own zone kept
    working, which is exactly why this looked like "viewport drop is broken"
    rather than "the filter is on the wrong widget".

    So both are hooked, plus any native descendant between them. Duplicates are
    dropped by identity, and one filter per widget is cheap -- these are three
    or four objects per panel that see only drag events.
    """
    found = []
    seen = set()
    for widget in (_view_widget(panel), _panel_widget(panel)):
        if widget is None or not _alive(widget):
            continue
        try:
            key = id(widget)
        except Exception:
            continue
        if key not in seen:
            seen.add(key)
            found.append(widget)

    # Native children of the panel control. `windowHandle()` is non-None
    # exactly for the widgets that own a native window, which are the ones a
    # drag can be delivered to without the parent ever hearing about it.
    parent = _panel_widget(panel)
    if parent is not None and _alive(parent):
        try:
            children = parent.findChildren(qt.QtWidgets.QWidget)
        except Exception:
            children = []
        for child in children:
            try:
                if child.windowHandle() is None:
                    continue
                key = id(child)
            except Exception:
                continue
            if key not in seen:
                seen.add(key)
                found.append(child)
    return found


def _alive(widget):
    """False once Maya has deleted the C++ side out from under the wrapper.

    Touching a dead wrapper raises RuntimeError rather than returning
    anything, so this is a call and a catch, not a truth test.
    """
    if widget is None:
        return False
    try:
        widget.objectName()
        return True
    except Exception:
        return False


def _timeline_targets():
    """The widgets a drag over the TIME SLIDER can be delivered to.

    Same native-window problem as the viewport, and the same answer: hook the
    control and any native descendant, because a drag over a native child is
    never offered to its Qt parent.
    """
    name = time_slider()
    if not name:
        return []
    found = []
    seen = set()
    widget = _panel_widget(name)
    if widget is not None and _alive(widget):
        found.append(widget)
        seen.add(id(widget))
        try:
            children = widget.findChildren(qt.QtWidgets.QWidget)
        except Exception:
            children = []
        for child in children:
            try:
                if child.windowHandle() is None or id(child) in seen:
                    continue
            except Exception:
                continue
            seen.add(id(child))
            found.append(child)
    return found


def model_panels():
    try:
        return list(cmds.getPanel(type="modelPanel") or [])
    except Exception:
        log.debug("animkit: could not list model panels", exc_info=True)
        return []


def install(verbose=False):
    """Wire up every model panel that is not wired up yet. Idempotent.

    Returns the panel names now accepting drops -- all of them, not just the
    ones this call added, because "which viewports can I drop on" is the
    question a caller actually has.

    `verbose` reports the WIDGETS hooked, not just the panels. When a drop onto
    a viewport does nothing, the useful question is which widget the filter
    landed on, and a count of panels cannot answer it.
    """
    for panel in model_panels() + [TIMELINE]:
        live = [entry for entry in _INSTALLED.get(panel, [])
                if _alive(entry[1])]
        if live:
            _INSTALLED[panel] = live
            continue

        hooked = []
        targets = (_timeline_targets() if panel == TIMELINE
                   else _drop_targets(panel))
        for widget in targets:
            try:
                previous = bool(widget.acceptDrops())
                handler = _DropFilter(panel)
                widget.setAcceptDrops(True)
                widget.installEventFilter(handler)
            except Exception:
                log.exception("animkit: could not hook drops on %s", panel)
                continue
            hooked.append((handler, widget, previous))
        if hooked:
            _INSTALLED[panel] = hooked

    if verbose:
        print("animkit: viewport drop active on %d panel(s)" % len(_INSTALLED))
        for panel in sorted(_INSTALLED):
            for _handler, widget, _previous in _INSTALLED[panel]:
                try:
                    name = "%s (%s)" % (widget.objectName() or "-",
                                        type(widget).__name__)
                except Exception:
                    name = "<dead>"
                print("    %-16s %s" % (panel, name))
    return sorted(_INSTALLED)


def uninstall():
    """Unhook every panel and put `acceptDrops` back the way it was.

    Restoring the previous value rather than clearing it: Maya may want drops
    on a panel for its own reasons, and a tool that switches that off on its
    way out breaks something it never owned.
    """
    for panel, entries in list(_INSTALLED.items()):
        for handler, widget, previous in entries:
            if not _alive(widget):
                continue
            try:
                widget.removeEventFilter(handler)
                widget.setAcceptDrops(previous)
            except Exception:
                log.debug("animkit: could not unhook %s", panel, exc_info=True)
        del _INSTALLED[panel]
    return True


def is_installed():
    """True when at least one live panel is still hooked.

    Checks the widgets rather than the dict: Maya deleting a panel leaves the
    entry behind, and reporting "on" for a set of dead wrappers would make the
    Ref tab's checkbox disagree with what dropping actually does.
    """
    return any(_alive(widget)
               for entries in _INSTALLED.values()
               for _handler, widget, _prev in entries)


def installed_panels():
    return sorted(
        panel for panel, entries in _INSTALLED.items()
        if any(_alive(widget) for _handler, widget, _prev in entries)
    )


def set_enabled(enabled, remember=True):
    """Turn viewport drop on or off, and remember the choice.

    The setting is read by the Ref tab when it builds, so this is what makes
    the answer survive a Maya restart.
    """
    enabled = bool(enabled)
    if remember:
        settings.set("reference.viewport_drop", enabled)
    if enabled:
        install()
    else:
        uninstall()
    return enabled


def install_if_wanted():
    """Honour the saved setting. Called when the Ref tab builds.

    Not called from `animkit.startup()` on purpose -- startup runs from
    userSetup.py, before Maya has a UI, and there are no model panels to hook
    yet. Anything Qt-shaped there fails on some machines and not others.
    """
    if settings.get("reference.viewport_drop"):
        return install()
    return []
