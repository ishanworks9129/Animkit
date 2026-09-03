"""Keyframe operations panel.

A grid of buttons, one per entry in `animkit.tools.keys.OPERATIONS`, grouped by
that registry's own groups. Nothing here knows what any operation does -- add
one to the registry and it appears here, in the Hotkey Editor, and in the test
harness, without this file changing.

Same workspaceControl discipline as the tween panel, for the same reasons:
build() must work from a cold interpreter because Maya restores docked panels
on startup by executing a stored uiScript before anything else has run. See
animkit.ui.mayawin for the traps that cost a day each.
"""

import logging

from maya import cmds

from animkit.tools import keys, pose
from animkit.ui import mayawin
from animkit.vendor import qt

QtCore = qt.QtCore
QtGui = qt.QtGui
QtWidgets = qt.QtWidgets

log = logging.getLogger(__name__)

CONTROL_NAME = "animkitKeysPanel"
BUILD_CODE = "import animkit.ui.keys_ui as m; m.build()"

#: Buttons per row within a group. Four fits a docked panel at its default
#: width without the labels truncating.
COLUMNS = 4


class KeysWidget(QtWidgets.QWidget):
    """One button per registered keyframe operation, grouped."""

    def __init__(self, parent=None):
        super(KeysWidget, self).__init__(parent)
        self._buttons = {}
        # Enough width that the longest label ("Hold Ends") does not elide at
        # one COLUMNS-th of the panel.
        self.setMinimumWidth(300)
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        for group in keys.GROUPS:
            layout.addWidget(self._build_group(group, keys.OPERATIONS))
        layout.addWidget(self._build_group("Pose", pose.OPERATIONS))

        hint = QtWidgets.QLabel(
            "Acts on selected keys in the Graph Editor, or the current frame."
        )
        hint.setWordWrap(True)
        hint.setEnabled(False)  # greys it out; it is a hint, not a control
        layout.addWidget(hint)

        layout.addStretch(1)

    def _build_group(self, group, operations):
        box = QtWidgets.QGroupBox(group)
        grid = QtWidgets.QGridLayout(box)
        grid.setContentsMargins(6, 4, 6, 6)
        grid.setSpacing(3)

        # Stretch all COLUMNS columns in EVERY group, whether or not the group
        # fills them. Without this each group box sizes its own columns to its
        # own button count, so Timing buttons come out a quarter wide, Cycle
        # buttons a third, and the lone Delete button spans the whole panel --
        # which is the one button that should not be the easiest to hit by
        # accident.
        for column in range(COLUMNS):
            grid.setColumnStretch(column, 1)

        operations = [op for op in operations if op.group == group]
        for i, operation in enumerate(operations):
            button = QtWidgets.QPushButton(operation.label)
            button.setFixedHeight(22)
            button.setToolTip(self._tooltip(operation))
            if operation.destructive:
                # Not a confirmation dialog. A dialog on a button an animator
                # presses fifty times an hour gets clicked through blind, and
                # the operation is one undo step anyway.
                button.setStyleSheet("QPushButton { color: rgb(210,140,140); }")
            button.clicked.connect(
                lambda _=False, op=operation: self._invoke(op)
            )
            grid.addWidget(button, i // COLUMNS, i % COLUMNS)
            self._buttons[operation.name] = button

        return box

    @staticmethod
    def _tooltip(operation):
        """Tooltip carries the runTimeCommand name, so an animator who wants
        this on a hotkey can find it in the Hotkey Editor without guessing."""
        return "{0}\n\nHotkey Editor: {1}".format(
            operation.tooltip, operation.name
        )

    def _invoke(self, operation):
        """Run an operation and say something when it did nothing.

        Silence after a button press reads as a broken tool. The two reasons
        nothing happens -- no selection, or no keys where the operation was
        pointed -- are both worth saying out loud.
        """
        try:
            count = operation.invoke()
        except Exception:
            log.exception("animkit: %s failed", operation.name)
            cmds.warning(
                "animkit: %s failed -- see the Script Editor" % operation.label
            )
            return

        if not count:
            try:
                cmds.inViewMessage(
                    assistMessage=(
                        "animkit: nothing to %s (no keys selected, and no "
                        "animated channels at this frame)" % operation.label
                    ),
                    position="midCenter",
                    fade=True,
                )
            except Exception:
                pass


# --- entry points -----------------------------------------------------------


def build():
    """Fill the current workspaceControl. Named by BUILD_CODE / uiScript.

    Must work from a cold interpreter. Exceptions are caught and printed
    rather than propagated: a uiScript that raises leaves an empty panel and
    one red line easily lost in the Script Editor.
    """
    try:
        import animkit

        animkit.startup()

        parent = mayawin.current_parent()
        if parent is None:
            log.error("animkit: keys_ui.build() called with no current parent")
            return None

        if parent.layout() is None:
            QtWidgets.QVBoxLayout(parent)
        mayawin.clear_children(parent)

        widget = KeysWidget(parent)
        parent.layout().setContentsMargins(0, 0, 0, 0)
        parent.layout().addWidget(widget)

        parent.setProperty(mayawin.BUILT_PROPERTY, True)
        return widget
    except Exception:
        import traceback

        traceback.print_exc()
        print("animkit: keys_ui.build() failed -- panel will be empty")
        return None


def show():
    """Open the dockable keys panel."""
    return mayawin.show_workspace_control(
        CONTROL_NAME, "Keys", BUILD_CODE, width=320
    )


def reset(*extra_names):
    """Delete the panel and its saved state so show() rebuilds from scratch."""
    results = {CONTROL_NAME: mayawin.delete_workspace_control(CONTROL_NAME)}
    for name in extra_names:
        results[name] = mayawin.delete_workspace_control(name)
    return results
