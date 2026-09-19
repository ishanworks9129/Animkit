"""The horizontal bar that docks against the time slider.

Same operations as the panel, laid out along one line instead of down a
column, because that is where an animator's hands already are: the time slider
is the thing they are looking at while they work, and a tool docked beside it
costs no trip across the screen.

WHY A workspaceControl AND NOT A WIDGET PUSHED INTO MAYA'S OWN LAYOUT
--------------------------------------------------------------------
The tempting approach is to find Maya's time slider with `MQtUtil.findControl`
and insert a widget into its parent layout. It works, and it breaks: the layout
around the time slider is Maya's, it is not API, and it has been rearranged
between versions. A workspaceControl docked to `TimeSlider` asks Maya to place
it instead of reaching in, so Maya remembers it in the workspace and restores
it on the next launch without any help.

That restore is also the trap. Maya rebuilds a docked control by executing this
module's `BUILD_CODE` as Python before anything else has run in that session,
so `build()` must work from a COLD INTERPRETER and assume nothing about
module-level state. See animkit.ui.mayawin, and house rule 11.

WHAT GOES ON IT
---------------
Everything the panel shows, in registry order, one labelled section per group
and a divider between them. The arrangement is computed by `ui.catalogue`,
which has no Qt in it and is therefore testable; this module only turns that
arrangement into widgets. Trim REGISTRIES to shorten the strip -- it is the one
place the contents are decided.
"""

import logging

from animkit.ui import catalogue, mayawin, style
from animkit.vendor.qt import QtCore, QtWidgets

log = logging.getLogger(__name__)

CONTROL_NAME = "animkitStrip"
BUILD_CODE = "import animkit.ui.strip as m; m.build()"

#: Where the strip tries to dock, in order. THE FIRST ONE THAT EXISTS WINS.
#:
#: Maya's main pane, Graph Editor and time slider are all workspaceControls,
#: which is what lets a tool sit against them without reaching into Maya's own
#: layout. `MainPane` is first because docking under it puts the strip directly
#: beneath the viewport and ABOVE whatever else is stacked at the bottom --
#: the Graph Editor included, which is the arrangement this was asked for.
#:
#: The order after that is a fallback chain, not decoration.
#: `graphEditor1Window` DOES NOT EXIST until the Graph Editor has been opened
#: at least once, so a list naming only it would float on a fresh Maya and dock
#: on a used one: one install behaving two ways for a reason invisible from the
#: outside. `TimeSlider` is always present, so this list always resolves.
#:
#: None of this overrides the animator. Maya remembers where a control was left
#: and `show()` never re-docks an existing one, so dragging it somewhere else
#: wins permanently -- this list only decides the FIRST launch.
DOCK_TARGETS = (
    ("MainPane", "bottom"),
    ("graphEditor1Window", "top"),
    ("TimeSlider", "top"),
)

#: Which registries appear, in order. THIS IS THE ONE PLACE the strip's
#: contents are decided -- remove a name and that whole section goes.
REGISTRIES = ("keys", "pose", "sets", "reference")


def _registries():
    """The registries named in REGISTRIES, loaded lazily.

    One registry failing to import must not empty the whole strip -- this runs
    from a uiScript at Maya startup, where a traceback is easy to miss and a
    silently empty bar is easier to miss still.
    """
    import importlib

    found = []
    for name in REGISTRIES:
        try:
            module = importlib.import_module("animkit.tools." + name)
        except Exception:
            log.exception("animkit: could not load %s for the strip", name)
            continue
        operations = getattr(module, "OPERATIONS", ())
        if operations:
            found.append(operations)
    return tuple(found)


def _divider():
    line = QtWidgets.QFrame()
    line.setFrameShape(QtWidgets.QFrame.VLine)
    line.setFixedWidth(1)
    line.setStyleSheet("color: %s; background: %s;"
                       % (style.SEPARATOR, style.SEPARATOR))
    return line


def _section_label(group):
    """The group name, quiet enough that the icons stay the loud thing."""
    label = QtWidgets.QLabel(group)
    label.setStyleSheet(
        "color: %s; font-size: %dpx; padding: 0 %dpx;"
        % (style.TEXT_DIM, style.px(9), style.px(2))
    )
    return label


class Strip(QtWidgets.QWidget):
    """One horizontal row: every group, in registry order."""

    def __init__(self, parent=None):
        super(Strip, self).__init__(parent)
        style.apply_to(self)

        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(
            style.px(style.MARGIN), style.px(2), style.px(style.MARGIN),
            style.px(2),
        )
        row.setSpacing(style.px(style.GUTTER))

        # Imported here rather than at module scope: panel imports nothing from
        # strip, and keeping it that way means a cold-interpreter uiScript for
        # either one cannot drag the other in behind it.
        from animkit.ui import panel

        arranged = catalogue.rows(_registries())
        for index, (group, operations) in enumerate(arranged):
            if index:
                row.addWidget(_divider())
            row.addWidget(_section_label(group))
            for operation in operations:
                row.addWidget(panel.OperationButton(operation, group))

        row.addStretch(1)
        row.addWidget(_divider())
        row.addWidget(self._help_button())

    def _help_button(self):
        button = QtWidgets.QPushButton("?")
        size = style.px(style.ICON_BUTTON)
        button.setFixedSize(size, size)
        button.setToolTip(
            "<b>What can animkit do?</b><br>Every operation, what it is for, "
            "and the key it is on right now.<br><br>"
            "<i>Hotkey Editor: animkitHelpShow</i>"
        )
        button.clicked.connect(self._open_help)
        return button

    @staticmethod
    def _open_help():
        try:
            from animkit.ui import help_ui

            help_ui.show()
        except Exception:
            log.exception("animkit: could not open the help panel")


def build():
    """Fill the current workspaceControl. Named by BUILD_CODE / uiScript.

    Must work from a cold interpreter -- Maya restores docked controls on
    startup by executing that string before anything this tool has done. See
    animkit.ui.mayawin.
    """
    try:
        import animkit

        animkit.startup()

        parent = mayawin.current_parent()
        if parent is None:
            log.error("animkit: strip.build() called with no current parent")
            return None

        if parent.layout() is None:
            QtWidgets.QVBoxLayout(parent)
        mayawin.clear_children(parent)

        area = QtWidgets.QScrollArea()
        area.setWidget(Strip(area))
        area.setWidgetResizable(True)
        area.setFrameShape(QtWidgets.QFrame.NoFrame)
        # A strip is one row. Vertical scrolling would mean the row does not
        # fit its own control, which is a layout bug rather than something to
        # let the animator scroll around.
        area.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)

        parent.layout().setContentsMargins(0, 0, 0, 0)
        parent.layout().addWidget(area)

        parent.setProperty(mayawin.BUILT_PROPERTY, True)
        return area
    except Exception:
        import traceback

        traceback.print_exc()
        print("animkit: strip.build() failed -- the strip will be empty")
        return None


def show():
    """Open the strip, docked above the time slider the first time."""
    return mayawin.show_workspace_control(
        CONTROL_NAME, "animkit", BUILD_CODE,
        width=style.px(900), dock_to=DOCK_TARGETS,
    )


def dock():
    """Put the strip back above the time slider, wherever it ended up.

    For the case where it opened floating -- a Maya whose time slider control
    is named something else, or a saved workspace that remembers it torn off.
    Deletes and recreates, because a floating workspaceControl cannot be docked
    by editing it; that is the whole reason show() docks at creation.
    """
    if not mayawin.redock(CONTROL_NAME, DOCK_TARGETS):
        return None
    return show()


def reset():
    """Delete the strip and its saved state so show() rebuilds from scratch."""
    return mayawin.delete_workspace_control(CONTROL_NAME)
