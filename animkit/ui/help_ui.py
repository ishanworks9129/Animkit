"""What can animkit do -- generated from the registries, never written by hand.

Every line on this page comes from an `Operation`: its label, the one-sentence
tooltip that already had to explain it, and the `runTimeCommand` name an
animator types into the Hotkey Editor to bind it. So the page cannot go stale.
A hand-written shortcut list is out of date the first time somebody adds an
operation and forgets the documentation, which is the same drift house rule 5
exists to stop -- and a help page that lies is worse than no help page, because
an animator trusts it once.

IT SHOWS *YOUR* BINDING, NOT A SUGGESTED ONE.
animkit binds no hotkeys at all (house rule 6), so on a fresh install every
line reads "unbound" and that is correct rather than broken. What the page is
for is the other direction: once you have bound things, it tells you what you
bound them to, and it is the only place that knows. A picture of somebody
else's shortcuts cannot do that.

The arrangement comes from `ui.catalogue`, which has no Qt in it and is
therefore covered by the fast tier. This module is only the widgets.
"""

import logging

from animkit.core import settings, usage
from animkit.ui import catalogue, mayawin, style
from animkit.vendor.qt import QtCore, QtWidgets

log = logging.getLogger(__name__)

CONTROL_NAME = "animkitHelp"
BUILD_CODE = "import animkit.ui.help_ui as m; m.build()"

#: Same registries the strip shows, for the same reason: a help page that
#: listed different operations from the toolbar would send somebody looking for
#: a button that is not there.
REGISTRIES = ("keys", "pose", "sets", "reference")


def _registries():
    import importlib

    found = []
    for name in REGISTRIES:
        try:
            module = importlib.import_module("animkit.tools." + name)
        except Exception:
            log.exception("animkit: could not load %s for the help page", name)
            continue
        operations = getattr(module, "OPERATIONS", ())
        if operations:
            found.append(operations)
    return tuple(found)


def _bindings():
    """Best-effort. An unavailable hotkey system reads as "everything unbound"."""
    try:
        from animkit import commands

        return commands.current_bindings()
    except Exception:
        log.debug("animkit: could not read hotkey bindings", exc_info=True)
        return {}


def _row(entry):
    """One operation: what it is called, what it does, and where it is bound."""
    box = QtWidgets.QWidget()
    line = QtWidgets.QHBoxLayout(box)
    line.setContentsMargins(0, style.px(1), 0, style.px(1))
    line.setSpacing(style.px(style.MARGIN))

    name = QtWidgets.QLabel(entry.label)
    name.setMinimumWidth(style.px(70))
    name.setStyleSheet(
        "color: %s; font-weight: 600;"
        % (style.DANGER if entry.destructive else style.TEXT)
    )
    line.addWidget(name)

    what = QtWidgets.QLabel(entry.tooltip)
    what.setWordWrap(True)
    what.setStyleSheet("color: %s;" % style.TEXT_DIM)
    line.addWidget(what, 1)

    # The runTimeCommand name is the actionable half of this page: it is what
    # you paste into the Hotkey Editor's search box. Selectable for that reason.
    command = QtWidgets.QLabel(entry.command)
    command.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
    command.setMinimumWidth(style.px(150))
    command.setStyleSheet("color: %s; font-family: monospace;"
                          % style.TEXT_DIM)
    line.addWidget(command)

    bound = QtWidgets.QLabel(entry.binding)
    bound.setMinimumWidth(style.px(70))
    unbound = entry.binding == catalogue.UNBOUND
    bound.setStyleSheet(
        "color: %s;" % (style.TEXT_DIM if unbound else style.ACCENT_DEFAULT)
    )
    line.addWidget(bound)

    return box


class HelpPage(QtWidgets.QWidget):
    """Every operation, grouped the way the strip and the panel group them."""

    def __init__(self, parent=None):
        super(HelpPage, self).__init__(parent)
        style.apply_to(self)

        arranged = catalogue.help_entries(_registries(), _bindings())
        groups, operations = catalogue.counts(_registries())
        bound = catalogue.bound_count(arranged)

        column = QtWidgets.QVBoxLayout(self)
        column.setContentsMargins(
            style.px(style.MARGIN), style.px(style.MARGIN),
            style.px(style.MARGIN), style.px(style.MARGIN),
        )
        column.setSpacing(style.px(style.GROUP_GAP))

        column.addWidget(self._summary(groups, operations, bound))

        for group, entries in arranged:
            column.addWidget(style.group_title(group, group))
            for entry in entries:
                column.addWidget(_row(entry))

        column.addWidget(style.group_title("Feedback", "Feedback"))
        column.addWidget(FeedbackBox(self))

        column.addStretch(1)

    @staticmethod
    def _summary(groups, operations, bound):
        text = (
            "%d operations in %d groups. %s"
            % (
                operations,
                groups,
                "None are bound to a key yet -- animkit deliberately binds "
                "none, so this is a fresh install rather than a fault. Bind "
                "them in the Hotkey Editor by the name in the third column."
                if not bound else
                "%d of them are bound to a key." % bound,
            )
        )
        label = QtWidgets.QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet("color: %s;" % style.TEXT_DIM)
        return label


class FeedbackBox(QtWidgets.QWidget):
    """The usage log, explained in full, next to the switch that turns it off.

    This is on the Help page and not buried in a preferences dialog on
    purpose. A log an animator has to discover the existence of is not one
    they agreed to, however local it is and however little it contains -- and
    "we told them, it was in the settings" is not the position to be in when
    the person you handed a build to runs a studio.

    So the page states what is recorded, states what is not, shows the running
    count, and puts Export and Stop logging side by side.
    """

    #: Rewritten by _refresh(), which is also what the buttons call.
    COUNTED = "%d operations recorded in %d session(s) on this machine."

    def __init__(self, parent=None):
        super(FeedbackBox, self).__init__(parent)

        column = QtWidgets.QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(style.px(6))

        column.addWidget(style.hint(
            "animkit keeps a local log of which operations you use, so the "
            "build you are testing can be improved against what actually gets "
            "reached for. It records operation names, how many things each "
            "one touched, and your Maya version.\n\n"
            "It records no node names, no attribute names, no file paths and "
            "no scene names -- nothing about the rig or the shot. It never "
            "leaves this machine: animkit has no network code of any kind. "
            "Sending it back is Export, and then you deciding to."
        ))

        self._count = QtWidgets.QLabel("")
        self._count.setWordWrap(True)
        self._count.setStyleSheet("color: %s;" % style.TEXT_DIM)
        column.addWidget(self._count)

        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(style.px(6))

        self._export = QtWidgets.QPushButton("Export feedback bundle")
        self._export.setToolTip(
            "Write a zip -- the log, a readable report and your settings -- "
            "and show it in the file browser. Look inside before you send it.")
        self._export.clicked.connect(self._on_export)
        row.addWidget(self._export)

        self._toggle = QtWidgets.QPushButton("")
        self._toggle.clicked.connect(self._on_toggle)
        row.addWidget(self._toggle)

        self._clear = QtWidgets.QPushButton("Delete the log")
        self._clear.setToolTip("Remove it from this machine entirely.")
        self._clear.clicked.connect(self._on_clear)
        row.addWidget(self._clear)

        row.addStretch(1)
        column.addLayout(row)

        self._refresh()

    # -- state ---------------------------------------------------------------

    def _refresh(self):
        """Re-read the log and relabel everything. Never raises: this is a
        help page, and a help page that throws is worse than a stale count."""
        try:
            records = usage.entries()
            self._count.setText(self.COUNTED % (
                len([e for e in records if e.get("op")]),
                len(usage.sessions(records)),
            ))
            on = usage.enabled()
            self._toggle.setText("Stop logging" if on else "Start logging")
            self._toggle.setToolTip(
                "Turn the log off. Nothing already recorded is deleted."
                if on else "Turn the log back on.")
            self._export.setEnabled(bool(records))
            self._clear.setEnabled(bool(records))
        except Exception:
            log.debug("animkit: could not refresh the feedback box",
                      exc_info=True)

    # -- buttons -------------------------------------------------------------

    def _on_export(self):
        target = usage.export_and_reveal()
        self._count.setText(
            "Wrote %s" % target if target else
            "Could not write the bundle -- see the Script Editor.")

    def _on_toggle(self):
        settings.set("usage.log", not usage.enabled())
        self._refresh()

    def _on_clear(self):
        usage.clear()
        self._refresh()


def build():
    """Fill the current workspaceControl. Named by BUILD_CODE / uiScript.

    Cold-interpreter safe, like every other build function here -- Maya will
    execute BUILD_CODE at startup if this page is left docked. House rule 11.
    """
    try:
        import animkit

        animkit.startup()

        parent = mayawin.current_parent()
        if parent is None:
            log.error("animkit: help_ui.build() called with no current parent")
            return None

        if parent.layout() is None:
            QtWidgets.QVBoxLayout(parent)
        mayawin.clear_children(parent)

        area = QtWidgets.QScrollArea()
        area.setWidget(HelpPage(area))
        area.setWidgetResizable(True)
        area.setFrameShape(QtWidgets.QFrame.NoFrame)

        parent.layout().setContentsMargins(0, 0, 0, 0)
        parent.layout().addWidget(area)

        parent.setProperty(mayawin.BUILT_PROPERTY, True)
        return area
    except Exception:
        import traceback

        traceback.print_exc()
        print("animkit: help_ui.build() failed -- the page will be empty")
        return None


def show():
    """Open the help page. Floating: it is read once, not worked in."""
    return mayawin.show_workspace_control(
        CONTROL_NAME, "animkit -- what it does", BUILD_CODE,
        width=style.px(620),
    )


def reset():
    return mayawin.delete_workspace_control(CONTROL_NAME)
