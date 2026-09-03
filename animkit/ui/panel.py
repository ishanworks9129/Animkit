"""One dockable panel for everything. The tool's actual front door.

    import animkit.ui.panel as p; p.show()

WHY THIS REPLACED THREE FLOATING WINDOWS
----------------------------------------
Because three windows is three title bars, three things to position, three
things to lose behind the viewport, and three things to close one at a time --
for a tool that is one tool. An animation panel lives docked in a strip beside
the viewport and competes for width with the thing the animator is looking at,
so the number of pixels it spends on chrome is the number it does not spend on
buttons.

The old panels are still here and still work. `tween_ui.show()` and friends open
them exactly as before, because somebody may have one docked into a saved
workspace and having it vanish on upgrade is a worse first impression than
anything this panel could fix. This is the front door, not a demolition.

ICON FIRST, LABEL IN THE TOOLTIP
--------------------------------
A text button has to be wide enough for its longest label, so a row of them is
as wide as "Paste Opp." times four whether or not that is useful. An icon button
is square, which means eight per row instead of four, which is the difference
between the pose group being one band and being three. The tooltip carries the
name AND the runTimeCommand, so nothing is lost -- the label just stops being
what sets the layout.

Groups that read better as words keep their words. `-1` and `+1` are already
shorter than any icon could be.
"""

import logging

from maya import cmds

from animkit.tools import keys, pose
from animkit.ui import icons, mayawin, style
from animkit.vendor import qt

QtCore = qt.QtCore
QtGui = qt.QtGui
QtWidgets = qt.QtWidgets

log = logging.getLogger(__name__)

CONTROL_NAME = "animkitPanel"
BUILD_CODE = "import animkit.ui.panel as m; m.build()"

#: Icon buttons per row. Eight fits a docked panel at its default width, which
#: is what makes a group of eight one band rather than two.
COLUMNS = 8


# --- the button -------------------------------------------------------------


class OperationButton(QtWidgets.QPushButton):
    """One operation. Icon if it has one, label if it does not.

    Says something when it did nothing. Silence after a button press reads as a
    broken tool, and the two reasons nothing happens -- no selection, or no keys
    where the operation was pointed -- are both worth saying out loud.
    """

    def __init__(self, operation, group="", parent=None):
        super(OperationButton, self).__init__(parent)
        self.operation = operation
        self._group = group

        accent = style.DANGER if operation.destructive else style.accent_for(group)

        if icons.has_icon(operation.name):
            size = style.px(style.ICON_BUTTON)
            self.setIcon(icons.icon(operation.name, colour=accent))
            self.setIconSize(QtCore.QSize(
                style.px(style.ICON_SIZE), style.px(style.ICON_SIZE)
            ))
            self.setFixedSize(size, size)
        else:
            self.setText(operation.label)
            self.setFixedHeight(style.px(style.ROW_HEIGHT))

        if operation.destructive:
            self.setProperty("animkitDanger", True)

        self.setToolTip(self._tooltip())
        self.clicked.connect(self._invoke)

    def _tooltip(self):
        return "<b>{0}</b><br>{1}<br><br><i>Hotkey Editor: {2}</i>".format(
            self.operation.label, self.operation.tooltip, self.operation.name
        )

    def _invoke(self):
        try:
            count = self.operation.invoke()
        except Exception:
            log.exception("animkit: %s failed", self.operation.name)
            cmds.warning(
                "animkit: %s failed -- see the Script Editor"
                % self.operation.label
            )
            return

        if not count:
            try:
                cmds.inViewMessage(
                    assistMessage="animkit: nothing to %s"
                                  % self.operation.label,
                    position="midCenter",
                    fade=True,
                )
            except Exception:
                pass


def _grid_of(operations, group, columns=COLUMNS):
    """A titled band of buttons for one registry group."""
    box = QtWidgets.QWidget()
    column = QtWidgets.QVBoxLayout(box)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(style.px(3))
    column.addWidget(style.group_title(group, group))

    grid = QtWidgets.QGridLayout()
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setSpacing(style.px(style.GUTTER))
    # Stretch every column in EVERY group, whether or not the group fills them.
    # Without this each group sizes its own columns to its own button count, so
    # a four-button group comes out with quarter-width buttons and a one-button
    # group spans the panel -- and the one-button group is Delete.
    for index in range(columns):
        grid.setColumnStretch(index, 0)
    grid.setColumnStretch(columns, 1)

    for index, operation in enumerate(operations):
        grid.addWidget(
            OperationButton(operation, group),
            index // columns,
            index % columns,
        )

    column.addLayout(grid)
    return box


def _page(bands):
    """A scrollable tab page from a list of widgets."""
    inner = QtWidgets.QWidget()
    column = QtWidgets.QVBoxLayout(inner)
    column.setContentsMargins(
        style.px(style.MARGIN), style.px(style.MARGIN),
        style.px(style.MARGIN), style.px(style.MARGIN),
    )
    column.setSpacing(style.px(style.GROUP_GAP))
    for band in bands:
        column.addWidget(band)
    column.addStretch(1)

    area = QtWidgets.QScrollArea()
    area.setWidget(inner)
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
    return area


# --- the panel --------------------------------------------------------------


class AnimkitPanel(QtWidgets.QWidget):
    """Tween, Keys, Pose and Sets in one dockable control."""

    def __init__(self, parent=None):
        super(AnimkitPanel, self).__init__(parent)
        style.apply_to(self)
        self.setMinimumWidth(style.px(270))
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(
            style.px(4), style.px(4), style.px(4), style.px(4)
        )
        layout.setSpacing(style.px(4))

        self._tabs = QtWidgets.QTabWidget()
        self._tabs.setDocumentMode(True)
        layout.addWidget(self._tabs, 1)

        self._tabs.addTab(self._tween_page(), "Tween")
        self._tabs.addTab(self._keys_page(), "Keys")
        self._tabs.addTab(self._pose_page(), "Pose")
        self._tabs.addTab(self._sets_page(), "Sets")
        self._tabs.addTab(self._reference_page(), "Ref")

    # --- pages --------------------------------------------------------------

    def _tween_page(self):
        """The existing slider, embedded rather than reimplemented.

        It carries the drag session, the overshoot clamp and the numeric entry,
        none of which has anything to do with how this panel is laid out.
        """
        try:
            from animkit.ui import tween_ui

            return _page([tween_ui.TweenWidget()])
        except Exception:
            log.exception("animkit: could not build the tween page")
            return _page([style.hint(
                "The tween slider failed to load -- see the Script Editor."
            )])

    def _keys_page(self):
        bands = []
        for group in keys.GROUPS:
            members = [op for op in keys.OPERATIONS if op.group == group]
            if members:
                bands.append(_grid_of(members, group))
        bands.append(style.hint(
            "Acts on the keys selected in the Graph Editor, or on the current "
            "frame if none are."
        ))
        return _page(bands)

    def _pose_page(self):
        bands = [_grid_of(list(pose.OPERATIONS), "Pose")]
        bands.append(style.hint(
            "Mirror and Flip find each control's counterpart from the rig "
            "itself -- no per-rig setup. Set Rest is only needed for a rig "
            "whose controls do not zero to their defaults."
        ))
        return _page(bands)

    def _sets_page(self):
        """The existing sets widget, which already reads the scene on refresh."""
        try:
            from animkit.ui import sets_ui

            return _page([sets_ui.SetsWidget()])
        except Exception:
            log.exception("animkit: could not build the sets page")
            return _page([style.hint(
                "The sets panel failed to load -- see the Script Editor."
            )])

    def _reference_page(self):
        """Drop zone and reference rows, plus the operation buttons.

        The buttons come from the registry like every other group, so the
        eight reference operations arrive here, in the Hotkey Editor and in
        the tests from one place. The widget above them is the part a registry
        cannot express: a drop target, and live state per reference.
        """
        try:
            from animkit.tools import reference
            from animkit.ui import reference_ui

            return _page([
                reference_ui.ReferenceWidget(),
                _grid_of(list(reference.OPERATIONS), "Ref"),
            ])
        except Exception:
            log.exception("animkit: could not build the reference page")
            return _page([style.hint(
                "The reference panel failed to load -- see the Script Editor."
            )])

    # --- api ----------------------------------------------------------------

    def show_tab(self, name):
        for index in range(self._tabs.count()):
            if self._tabs.tabText(index).lower() == name.lower():
                self._tabs.setCurrentIndex(index)
                return True
        return False


# --- entry points -----------------------------------------------------------


def build():
    """Fill the current workspaceControl. Named by BUILD_CODE / uiScript.

    Must work from a cold interpreter -- Maya restores docked panels on startup
    by executing this string before anything else has run. See animkit.ui.mayawin.
    """
    try:
        import animkit

        animkit.startup()

        parent = mayawin.current_parent()
        if parent is None:
            log.error("animkit: panel.build() called with no current parent")
            return None

        if parent.layout() is None:
            QtWidgets.QVBoxLayout(parent)
        mayawin.clear_children(parent)

        widget = AnimkitPanel(parent)
        parent.layout().setContentsMargins(0, 0, 0, 0)
        parent.layout().addWidget(widget)

        parent.setProperty(mayawin.BUILT_PROPERTY, True)
        return widget
    except Exception:
        import traceback

        traceback.print_exc()
        print("animkit: panel.build() failed -- panel will be empty")
        return None


def show(tab=None):
    """Open the dockable panel, optionally on a named tab."""
    mayawin.show_workspace_control(
        CONTROL_NAME, "animkit", BUILD_CODE, width=style.px(300)
    )
    if tab:
        widget = mayawin.find_control(CONTROL_NAME)
        for child in (widget.findChildren(AnimkitPanel) if widget else []):
            child.show_tab(tab)
    return CONTROL_NAME


def reset():
    """Delete the panel and its saved state so show() rebuilds from scratch."""
    return mayawin.delete_workspace_control(CONTROL_NAME)
