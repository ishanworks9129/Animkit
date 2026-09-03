"""Selection sets panel.

One row per set on the rig the animator is working on: its hotkey slot, its
name, how many controls are in it, and the buttons to recall, replace and
delete it.

The panel READS THE SCENE every time it refreshes and holds no list of its own.
Sets are created and deleted by hotkey, by another panel, by undo, and by
opening a different shot -- so any cached copy is a stale copy waiting to be
shown. Refreshing is a handful of attribute reads on a handful of nodes.

Same workspaceControl discipline as the other panels: build() must work from a
cold interpreter, because Maya restores docked panels on startup by executing a
stored uiScript before anything else has run. See animkit.ui.mayawin.
"""

import logging

from maya import cmds

from animkit.tools import sets
from animkit.ui import mayawin
from animkit.vendor import qt

QtCore = qt.QtCore
QtGui = qt.QtGui
QtWidgets = qt.QtWidgets

log = logging.getLogger(__name__)

CONTROL_NAME = "animkitSetsPanel"
BUILD_CODE = "import animkit.ui.sets_ui as m; m.build()"


class SetsWidget(QtWidgets.QWidget):
    """The set list, plus a name field and Store."""

    def __init__(self, parent=None):
        super(SetsWidget, self).__init__(parent)
        self.setMinimumWidth(300)
        self._rows = QtWidgets.QVBoxLayout()
        self._build_ui()
        self.refresh()

    # --- construction -------------------------------------------------------

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self._rig_label = QtWidgets.QLabel()
        self._rig_label.setEnabled(False)
        layout.addWidget(self._rig_label)

        store_row = QtWidgets.QHBoxLayout()
        self._name = QtWidgets.QLineEdit()
        self._name.setPlaceholderText("name this selection...")
        # Enter stores, because that is what the hands expect after typing a
        # name, and reaching for the mouse to press a button next to the field
        # you are already in is the kind of friction that gets a panel closed.
        self._name.returnPressed.connect(self._store)
        store_row.addWidget(self._name, 1)

        store = QtWidgets.QPushButton("Store")
        store.setFixedHeight(22)
        store.setToolTip(
            "Save the selected controls under this name, on this rig.\n\n"
            "Hotkey Editor: animkitSetStore"
        )
        store.clicked.connect(self._store)
        store_row.addWidget(store)
        layout.addLayout(store_row)

        self._rows.setSpacing(2)
        layout.addLayout(self._rows)

        refresh = QtWidgets.QPushButton("Refresh")
        refresh.setFixedHeight(20)
        refresh.setToolTip(
            "Re-read the scene. Needed after opening a file or undoing, "
            "because this panel keeps no list of its own."
        )
        refresh.clicked.connect(self.refresh)
        layout.addWidget(refresh)

        self._hint = QtWidgets.QLabel()
        self._hint.setWordWrap(True)
        self._hint.setEnabled(False)
        layout.addWidget(self._hint)

        layout.addStretch(1)

    # --- state --------------------------------------------------------------

    def refresh(self):
        """Rebuild the rows from the scene."""
        while self._rows.count():
            item = self._rows.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
            elif item.layout() is not None:
                _clear_layout(item.layout())

        try:
            root = sets.current_root()
            found = sets.sets_for(root) if root else sets.all_sets()
        except Exception:
            log.exception("animkit: could not read selection sets")
            self._rig_label.setText("could not read the scene")
            return

        if root:
            self._rig_label.setText("rig: %s" % root)
        elif found:
            self._rig_label.setText(
                "no rig selected -- showing every set in the scene"
            )
        else:
            self._rig_label.setText("no rig selected")

        for entry in found:
            self._rows.addWidget(self._build_row(entry))

        if not found:
            self._hint.setText(
                "No selection sets yet. Select some controls, type a name and "
                "press Store. Slots 1-%d are bindable in the Hotkey Editor as "
                "animkitSetRecall1..%d and follow whichever character you have "
                "selected." % (sets.MAX_SLOTS, sets.MAX_SLOTS)
            )
        else:
            self._hint.setText(
                "Hotkey Editor: animkitSetRecall1..%d recall slots 1-%d on "
                "whichever character is selected." % (sets.MAX_SLOTS,
                                                      sets.MAX_SLOTS)
            )

    def _build_row(self, entry):
        box = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(3)

        slot = QtWidgets.QLabel(str(entry.slot) if entry.slot else "-")
        slot.setFixedWidth(14)
        slot.setAlignment(QtCore.Qt.AlignCenter)
        if entry.slot:
            slot.setToolTip(
                "Hotkey slot. Bind animkitSetRecall%d to reach this."
                % entry.slot
            )
        else:
            slot.setToolTip(
                "No hotkey slot -- every slot on this rig is taken."
            )
        row.addWidget(slot)

        recall = QtWidgets.QPushButton(
            "%s  (%d)" % (entry.name, len(entry.members))
        )
        recall.setFixedHeight(22)
        recall.setToolTip("Select the %d control(s) in %r"
                          % (len(entry.members), entry.name))
        recall.clicked.connect(
            lambda _=False, name=entry.name: self._recall(name)
        )
        row.addWidget(recall, 1)

        replace = QtWidgets.QPushButton("Set")
        replace.setFixedHeight(22)
        replace.setFixedWidth(34)
        replace.setToolTip(
            "Replace the contents of %r with the current selection" % entry.name
        )
        replace.clicked.connect(
            lambda _=False, name=entry.name: self._replace(name)
        )
        row.addWidget(replace)

        delete = QtWidgets.QPushButton("X")
        delete.setFixedHeight(22)
        delete.setFixedWidth(22)
        # Coloured, not confirmed. A dialog on a button pressed all day gets
        # clicked through blind, and deleting a set is one undo step that
        # touches no control.
        delete.setStyleSheet("QPushButton { color: rgb(210,140,140); }")
        delete.setToolTip(
            "Delete the set %r. The controls in it are NOT touched." % entry.name
        )
        delete.clicked.connect(
            lambda _=False, name=entry.name: self._delete(name)
        )
        row.addWidget(delete)

        return box

    # --- actions ------------------------------------------------------------

    def _store(self):
        name = self._name.text().strip()
        if not name:
            cmds.warning("animkit: type a name for the selection set first")
            return
        if sets.store(name) is not None:
            self._name.clear()
        self.refresh()

    def _replace(self, name):
        sets.store(name)
        self.refresh()

    def _delete(self, name):
        sets.remove(name)
        self.refresh()

    def _recall(self, name):
        count = sets.recall(name)
        # The panel is rebuilt because recalling changes the selection, which
        # changes which rig is current, which changes which sets are listed.
        self.refresh()
        if not count:
            try:
                cmds.inViewMessage(
                    assistMessage="animkit: %r selected nothing" % name,
                    position="midCenter",
                    fade=True,
                )
            except Exception:
                pass


def _clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout() is not None:
            _clear_layout(item.layout())


# --- entry points -----------------------------------------------------------


def build():
    """Fill the current workspaceControl. Named by BUILD_CODE / uiScript."""
    try:
        import animkit

        animkit.startup()

        parent = mayawin.current_parent()
        if parent is None:
            log.error("animkit: sets_ui.build() called with no current parent")
            return None

        if parent.layout() is None:
            QtWidgets.QVBoxLayout(parent)
        mayawin.clear_children(parent)

        widget = SetsWidget(parent)
        parent.layout().setContentsMargins(0, 0, 0, 0)
        parent.layout().addWidget(widget)

        parent.setProperty(mayawin.BUILT_PROPERTY, True)
        return widget
    except Exception:
        import traceback

        traceback.print_exc()
        print("animkit: sets_ui.build() failed -- panel will be empty")
        return None


def show():
    """Open the dockable sets panel."""
    return mayawin.show_workspace_control(
        CONTROL_NAME, "Sets", BUILD_CODE, width=320
    )


def reset():
    """Delete the panel and its saved state so show() rebuilds from scratch."""
    return mayawin.delete_workspace_control(CONTROL_NAME)
