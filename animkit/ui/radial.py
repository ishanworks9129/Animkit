"""Held-hotkey radial menu. Press a key, flick, release.

    import animkit.ui.radial as r
    r.install_hotkey("c", "pose", alt=True)     # opt in, once
    # hold Alt+C, flick toward a wedge, let go

WHY MAYA'S OWN PRESS/RELEASE AND NOT AN EVENT FILTER
----------------------------------------------------
`cmds.hotkey` takes a `releaseName` alongside `name`, so Maya will run one
command on key-down and another on key-up. That is the entire mechanism. The
obvious alternative -- a QObject event filter on the main window watching for
KeyPress/KeyRelease -- loses to Maya on both of the things that matter:

    FOCUS. A filter only sees keys the focused widget did not consume, so the
    menu works over the viewport and stops working the moment the animator
    clicks the Graph Editor, the Channel Box or a text field. Maya's hotkey
    system is above all of that.

    VIEWPORT RECREATION. Maya destroys and rebuilds viewport widgets on layout
    changes, going in and out of full screen, and on some GPU driver events. A
    filter installed on a widget that no longer exists is a filter that has
    silently stopped working, and the symptom is "it worked this morning".

There is nothing to reinstall and nothing to keep alive here, because the
binding lives in Maya's hotkey set rather than in this process.

THE OVERLAY NEVER TAKES FOCUS
-----------------------------
It is frameless, translucent, always on top, and carries WA_ShowWithoutActivating
so the viewport keeps keyboard focus while it is up. That matters more than it
sounds: if the overlay took focus, the KEY RELEASE would be delivered to the
overlay instead of to Maya, the release command would never run, and the menu
would stay on screen forever.

Because it never takes focus it also never receives mouse events reliably, so
the highlight is driven by POLLING the cursor position on a timer instead. That
is not a workaround for a bug; it is the price of not fighting for focus, and
it is 30ms of one comparison.

AND IT ALWAYS GOES AWAY
-----------------------
A hotkey release can be lost -- alt-tab away mid-flick, a modal dialog opening
underneath, a keyboard-layout switch. The overlay is on top of the viewport, so
"stuck visible" is not a cosmetic bug, it is a Maya the animator has to
restart. Hence the timeout: if no release arrives within TIMEOUT_MS the menu
cancels itself. Cancels, not fires -- a menu that fires a command nobody asked
for because a key event went missing would be much worse.
"""

import logging

from maya import cmds

from animkit.ui import mayawin, radial_geom as geom
from animkit.vendor import qt

QtCore = qt.QtCore
QtGui = qt.QtGui
QtWidgets = qt.QtWidgets

log = logging.getLogger(__name__)

#: How long a radial may stay up with no release before it cancels itself.
#: Long enough to think, short enough that a lost release is an annoyance
#: rather than a restart.
TIMEOUT_MS = 6000

#: Cursor poll interval. Fast enough to feel attached to the mouse, slow
#: enough that it does not show up in a profile.
POLL_MS = 30

#: Past eight wedges a radial is the wrong control -- the angles get too fine
#: to hit without looking, which defeats the point of not looking.
MAX_WEDGES = 8


# --- what goes in a radial --------------------------------------------------

#: The curated menus, by runTimeCommand name. Explicit rather than "the first
#: eight of the registry": keys.OPERATIONS has sixteen entries and a radial of
#: sixteen is unusable, so somebody has to choose, and it should be visible
#: here rather than implied by registry order. test_radial_maya.py asserts
#: every name below still resolves, so this cannot rot silently.
MENUS = {
    "pose": (
        "animkitPoseCopy",
        "animkitPosePaste",
        "animkitPosePasteMirrored",
        "animkitPoseMirror",
        "animkitPoseFlip",
        "animkitPoseReset",
        "animkitPoseCaptureRest",
        "animkitPoseClearRest",
    ),
    "keys": (
        "animkitKeysOffsetBack",
        "animkitKeysTangentAuto",
        "animkitKeysOffsetForward",
        "animkitKeysTangentFlat",
        "animkitKeysDelete",
        "animkitKeysTangentStepped",
        "animkitKeysHold",
        "animkitKeysTangentSpline",
    ),
}


def invoke_item(items, index):
    """Run the wedge at `index`, or nothing. Returns the label, or None.

    Split out of the widget so the decision it encodes is testable without a
    QApplication: None means the animator released in the dead zone and NOTHING
    may happen, and an out-of-range index -- which a resized menu or a race
    with the poll timer can produce -- means the same rather than an IndexError
    in a hotkey handler.

    A failing item is reported and swallowed. This is a hotkey path, and a
    traceback escaping it kills the release handler, which leaves the overlay
    on screen over the viewport.
    """
    if index is None or not (0 <= index < len(items)):
        return None

    item = items[index]
    try:
        item.invoke()
    except Exception:
        log.exception("animkit: radial item %r failed", item.label)
        cmds.warning("animkit: %s failed -- see the Script Editor" % item.label)
    return item.label


class Item(object):
    """One wedge: a label, a tooltip and something to call."""

    def __init__(self, label, tooltip, invoke, destructive=False):
        self.label = label
        self.tooltip = tooltip
        self.invoke = invoke
        self.destructive = destructive


def _operations_by_name():
    from animkit.tools import keys, pose

    found = {}
    for registry in (keys.OPERATIONS, pose.OPERATIONS):
        for op in registry:
            found[op.name] = op
    return found


def items_for(menu):
    """The wedges for a named menu, or [] if there is nothing to show.

    "sets" is built from the SCENE rather than from a registry, because that is
    the only menu whose contents the animator authored. It is also the reason
    the radial was worth building: a selection set is exactly the kind of thing
    that should cost a flick rather than a trip to a panel.
    """
    if menu == "sets":
        return _set_items()

    names = MENUS.get(menu)
    if not names:
        cmds.warning(
            "animkit: no radial menu called %r -- try one of: %s"
            % (menu, ", ".join(sorted(list(MENUS) + ["sets"])))
        )
        return []

    known = _operations_by_name()
    items = []
    for name in names[:MAX_WEDGES]:
        op = known.get(name)
        if op is None:
            # Registry drift. Drop the wedge rather than the menu: seven
            # working commands beat a radial that refuses to open.
            log.warning("animkit: radial menu %r names unknown %r", menu, name)
            continue
        items.append(Item(op.label, op.tooltip, op.invoke, op.destructive))
    return items


def _set_items():
    from animkit.tools import sets

    try:
        root = sets.current_root()
        found = sets.sets_for(root) if root else sets.all_sets()
    except Exception:
        log.exception("animkit: could not read selection sets for the radial")
        return []

    if not found:
        cmds.warning(
            "animkit: no selection sets on this rig yet -- store some from "
            "the Sets panel (animkitSetsShow)"
        )
        return []

    items = []
    for entry in found[:MAX_WEDGES]:
        name = entry.name
        items.append(Item(
            name,
            "Select the %d control(s) in %r" % (len(entry.members), name),
            lambda n=name: sets.recall(n),
        ))
    return items


# --- the overlay ------------------------------------------------------------


class Radial(QtWidgets.QWidget):
    """A frameless wheel of wedges, centred on the cursor."""

    def __init__(self, items, parent=None):
        super(Radial, self).__init__(parent)
        self._items = items
        self._radius = geom.outer_radius(len(items))
        self._highlight = None
        # Set properly in popup(); initialised here so a widget that is
        # dismissed before it is ever shown cannot raise on the way out.
        self._centre = QtGui.QCursor.pos()

        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint
            | QtCore.Qt.Tool
            | QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        # Without this the overlay activates on show, steals keyboard focus,
        # and Maya never sees the key RELEASE -- so the menu never closes.
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating)

        size = int(self._radius * 2 + 40)
        self.resize(size, size)

        self._poll = QtCore.QTimer(self)
        self._poll.setInterval(POLL_MS)
        self._poll.timeout.connect(self._track)

        self._timeout = QtCore.QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.setInterval(TIMEOUT_MS)
        self._timeout.timeout.connect(self._expire)

    # --- lifecycle ----------------------------------------------------------

    def popup(self):
        """Show centred on the cursor and start tracking."""
        centre = QtGui.QCursor.pos()
        self._centre = centre
        self.move(centre.x() - self.width() // 2,
                  centre.y() - self.height() // 2)
        self.show()
        self.raise_()
        self._poll.start()
        self._timeout.start()

    def _track(self):
        cursor = QtGui.QCursor.pos()
        index = geom.wedge_at(
            cursor.x() - self._centre.x(),
            cursor.y() - self._centre.y(),
            len(self._items),
        )
        if index != self._highlight:
            self._highlight = index
            self.update()

    def _expire(self):
        """No release arrived. Cancel -- never fire.

        A missing key event must not become a command the animator did not
        ask for, and on this menu that command could be Reset or Delete.
        """
        log.debug("animkit: radial timed out with no key release")
        self.dismiss()

    def dismiss(self):
        self._poll.stop()
        self._timeout.stop()
        self.hide()
        self.deleteLater()

    def fire(self):
        """Invoke the highlighted wedge, if any, and go away. Returns its label."""
        self._track()
        index = self._highlight
        self.dismiss()
        return invoke_item(self._items, index)

    # --- input --------------------------------------------------------------

    def mousePressEvent(self, event):
        """Clicking a wedge works too.

        Not a fallback for a broken release -- a real second way in. An
        animator who has not bound a hotkey can still call show() from a shelf
        button and click, and that must not be a dead end.
        """
        self.fire()

    # --- painting -----------------------------------------------------------

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

        centre = QtCore.QPointF(self.width() / 2.0, self.height() / 2.0)
        radius = self._radius
        box = QtCore.QRectF(
            centre.x() - radius, centre.y() - radius, radius * 2, radius * 2
        )

        painter.setPen(QtCore.Qt.NoPen)
        for index, item in enumerate(self._items):
            active = index == self._highlight
            if active and item.destructive:
                colour = QtGui.QColor(150, 70, 70, 235)
            elif active:
                colour = QtGui.QColor(90, 130, 175, 235)
            else:
                colour = QtGui.QColor(48, 48, 48, 195)
            painter.setBrush(colour)

            start, span = geom.wedge_span(index, len(self._items))
            # Qt takes sixteenths of a degree. A wedge drawn one degree short
            # of its neighbour leaves a hairline the highlight flickers across.
            painter.drawPie(box, int(round(start * 16)), int(round(span * 16)))

        # The dead zone, drawn so that "release here to cancel" is visible
        # rather than something the animator has to be told.
        painter.setBrush(QtGui.QColor(28, 28, 28, 210))
        painter.drawEllipse(centre, geom.DEAD_ZONE, geom.DEAD_ZONE)

        self._paint_labels(painter, centre)

    def _paint_labels(self, painter, centre):
        font = painter.font()
        font.setPointSize(9)
        painter.setFont(font)
        metrics = QtGui.QFontMetrics(font)

        label_radius = self._radius * geom.LABEL_RADIUS
        for index, item in enumerate(self._items):
            dx, dy = geom.wedge_centre(index, len(self._items), label_radius)
            width = metrics.horizontalAdvance(item.label) if hasattr(
                metrics, "horizontalAdvance"
            ) else metrics.width(item.label)
            height = metrics.height()
            rect = QtCore.QRectF(
                centre.x() + dx - width / 2.0 - 4,
                centre.y() + dy - height / 2.0,
                width + 8,
                height,
            )
            painter.setPen(
                QtGui.QColor(255, 255, 255)
                if index == self._highlight
                else QtGui.QColor(205, 205, 205)
            )
            painter.drawText(rect, QtCore.Qt.AlignCenter, item.label)


# --- press / release --------------------------------------------------------

#: The live menu. Module level because the press and the release arrive as two
#: separate runTimeCommand invocations with nothing to carry state between
#: them -- that is the shape Maya's hotkey system imposes.
_active = None


def _can_build_widgets():
    """True if this interpreter can construct a QWidget at all.

    NOT `QApplication.instance() is not None`, which is the obvious version and
    is wrong. mayapy running maya.standalone has a **QGuiApplication** -- so
    `QApplication.instance()` returns something truthy, the obvious check
    passes, and the QWidget constructor then fails anyway. Measured under Maya
    2024 / PySide2; the instance comes back as
    `PySide2.QtGui.QGuiApplication`, and `isinstance(it, QApplication)` is
    False.

    Only a real QApplication can host widgets, so ask that.
    """
    return isinstance(
        QtWidgets.QApplication.instance(), QtWidgets.QApplication
    )


def show(menu="pose"):
    """Show a radial. Bound as the PRESS command of a held hotkey."""
    global _active

    if _active is not None:
        # Auto-repeat. Windows fires the press command over and over while a
        # key is held, and building a second overlay per repeat would leave a
        # stack of them behind when only the last one gets a release.
        return _active

    items = items_for(menu)
    if not items:
        return None

    if not _can_build_widgets():
        # mayapy, a batch render, a farm job. Constructing a QWidget without a
        # real QApplication does not raise cleanly on every binding -- it can
        # take the interpreter with it -- so this is checked rather than
        # caught. Nobody could have pressed a hotkey in that session anyway.
        log.debug("animkit: no widget-capable QApplication, so no radial")
        return None

    try:
        widget = Radial(items, parent=mayawin.maya_main_window())
        widget.popup()
    except Exception:
        log.exception("animkit: could not open the %r radial", menu)
        return None

    _active = widget
    return widget


def release():
    """Fire the highlighted wedge and hide. Bound as the RELEASE command.

    Safe to call with nothing up, because it will be: an animator who binds
    this on its own, or presses the key while a dialog has focus, gets here
    with no menu, and a traceback in the Script Editor would be the only
    symptom.
    """
    global _active

    widget = _active
    _active = None
    if widget is None:
        return None
    try:
        return widget.fire()
    except Exception:
        log.exception("animkit: radial release failed")
        try:
            widget.dismiss()
        except Exception:
            pass
        return None


def is_open():
    return _active is not None


def cancel():
    """Close any open radial without firing. The panic button."""
    global _active
    widget = _active
    _active = None
    if widget is not None:
        try:
            widget.dismiss()
        except Exception:
            pass
    return widget is not None


# --- binding ----------------------------------------------------------------

#: Maya's shipped hotkey set is read-only, and its name is the only way to
#: recognise it -- there is no "is this set locked" query.
DEFAULT_HOTKEY_SET = "Maya_Default"
ANIMKIT_HOTKEY_SET = "animkit"

_MENU_COMMANDS = {
    "pose": "animkitRadialPose",
    "keys": "animkitRadialKeys",
    "sets": "animkitRadialSets",
}
RELEASE_COMMAND = "animkitRadialRelease"


def _editable_hotkey_set():
    """The current hotkey set, or a copy of it that can actually be edited.

    Maya refuses edits to Maya_Default, so binding anything requires a set of
    one's own. Copying the current one rather than starting empty means the
    animator keeps every key they already had.
    """
    current = cmds.hotkeySet(q=True, current=True)
    if current != DEFAULT_HOTKEY_SET:
        return current

    if not cmds.hotkeySet(ANIMKIT_HOTKEY_SET, q=True, exists=True):
        cmds.hotkeySet(ANIMKIT_HOTKEY_SET, source=current, current=True)
        print(
            "animkit: Maya's own hotkey set cannot be edited, so animkit "
            "copied it to a set called %r and switched to it. Everything you "
            "had is still bound; switch back any time in the Hotkey Editor."
            % ANIMKIT_HOTKEY_SET
        )
    else:
        cmds.hotkeySet(ANIMKIT_HOTKEY_SET, e=True, current=True)
    return ANIMKIT_HOTKEY_SET


def install_hotkey(key, menu="pose", alt=False, ctrl=False, shift=False,
                   force=False):
    """Bind a held key to a radial. Opt-in, and it refuses to stomp.

    animkit binds no hotkeys on its own -- see animkit.commands for why -- so
    this is something the animator or a studio hotkey set runs deliberately.

    It will not overwrite a key that already carries something else unless
    force=True. Silently taking a key an animator has had bound for ten years
    is exactly how a tool gets uninstalled, and it is worth the one extra
    argument to make that a decision rather than a side effect.
    """
    press = _MENU_COMMANDS.get(menu)
    if press is None:
        cmds.warning(
            "animkit: no radial menu called %r -- try one of: %s"
            % (menu, ", ".join(sorted(_MENU_COMMANDS)))
        )
        return False

    modifiers = {"altModifier": alt, "ctrlModifier": ctrl,
                 "shiftModifier": shift}

    try:
        hotkey_set = _editable_hotkey_set()

        existing = cmds.hotkey(keyShortcut=key, query=True, name=True,
                               **modifiers)
        if existing and existing != press and not force:
            cmds.warning(
                "animkit: %s is already bound to %r. Pass force=True to take "
                "it, or pick another key."
                % (_describe(key, alt, ctrl, shift), existing)
            )
            return False

        cmds.hotkey(keyShortcut=key, name=press, releaseName=RELEASE_COMMAND,
                    **modifiers)
    except Exception:
        log.exception("animkit: could not bind the %r radial", menu)
        cmds.warning(
            "animkit: could not bind %s -- see the Script Editor"
            % _describe(key, alt, ctrl, shift)
        )
        return False

    print(
        "animkit: hold %s for the %s radial (hotkey set %r)"
        % (_describe(key, alt, ctrl, shift), menu, hotkey_set)
    )
    return True


def uninstall_hotkey(key, alt=False, ctrl=False, shift=False):
    """Clear a radial binding. Leaves anything that is not ours alone."""
    modifiers = {"altModifier": alt, "ctrlModifier": ctrl,
                 "shiftModifier": shift}
    try:
        existing = cmds.hotkey(keyShortcut=key, query=True, name=True,
                               **modifiers)
        if existing not in _MENU_COMMANDS.values():
            cmds.warning(
                "animkit: %s is bound to %r, which is not an animkit radial "
                "-- leaving it alone"
                % (_describe(key, alt, ctrl, shift), existing or "nothing")
            )
            return False
        cmds.hotkey(keyShortcut=key, name="", releaseName="", **modifiers)
    except Exception:
        log.exception("animkit: could not clear a radial binding")
        return False
    return True


def _describe(key, alt, ctrl, shift):
    parts = []
    if ctrl:
        parts.append("Ctrl")
    if alt:
        parts.append("Alt")
    if shift:
        parts.append("Shift")
    parts.append(key.upper())
    return "+".join(parts)
