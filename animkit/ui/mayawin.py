"""Maya main window access and dockable workspaceControl plumbing.

The workspaceControl trap, restated because it is the one that costs a day:
Maya persists dockable panels across sessions and rebuilds them on startup by
executing a stored `uiScript` string as PYTHON. That script runs before anything
your tool has done in that session. So the build function it names must be
importable from a cold interpreter and must assume NOTHING about module-level
state, open sessions, or previously created widgets.

If your panel only works when the user opens it by hand, you will find out on
the first Maya restart after someone docks it.
"""

import logging

from maya import cmds
from maya import OpenMayaUI as omui

from animkit.vendor import qt

log = logging.getLogger(__name__)


def maya_main_window():
    return qt.as_qwidget(omui.MQtUtil.mainWindow())


def current_parent():
    """The Qt widget Maya is currently parenting into.

    Only meaningful while a uiScript is executing -- that is how a build
    function finds the workspaceControl it is supposed to fill.
    """
    return qt.as_qwidget(omui.MQtUtil.getCurrentParent())


def find_control(name):
    return qt.as_qwidget(omui.MQtUtil.findControl(name))


# Marker property set by a build function once it has successfully populated a
# control. See show_workspace_control for why an unmarked control is rebuilt.
BUILT_PROPERTY = "animkitBuilt"


def purge_workspace_control_state(control_name):
    """Remove Maya's SAVED state for a workspaceControl.

    Deleting a workspaceControl is not enough. Maya persists the control's
    definition -- including its uiScript -- in the current workspace, and reuses
    that saved definition when a control of the same name is created again. So a
    control created with a broken uiScript will come back with the same broken
    uiScript after deleteUI, no matter what you pass to the new call.

    Symptom: a fix that provably changed the uiScript string still produces the
    original error, and deleting the panel does not help.
    """
    for kwargs in ({"query": True, "exists": True}, None):
        try:
            if kwargs is not None:
                if not cmds.workspaceControlState(control_name, **kwargs):
                    return False
            cmds.workspaceControlState(control_name, remove=True)
            return True
        except Exception:
            # -exists is not queryable on every version; fall through to a
            # bare remove before giving up.
            continue
    log.debug("animkit: could not purge saved state for %s", control_name)
    return False


def delete_workspace_control(control_name):
    """Delete a workspaceControl and its saved state. Returns True if it existed.

    State is purged AFTER deleteUI, because deleting the control writes its
    state back out.
    """
    existed = bool(cmds.workspaceControl(control_name, q=True, exists=True))
    if existed:
        try:
            cmds.deleteUI(control_name)
        except Exception:
            log.exception("animkit: could not delete %s", control_name)
    purge_workspace_control_state(control_name)
    return existed


def is_built(control_name):
    """True if a build function has populated this control successfully.

    Reads a Qt property rather than counting child widgets. A workspaceControl
    has internal children of its own, so "does it have children" cannot tell a
    populated panel from an empty one.
    """
    widget = find_control(control_name)
    if widget is None:
        return False
    return bool(widget.property(BUILT_PROPERTY))


def show_workspace_control(control_name, label, build_code, width=320):
    """Create, restore or raise a dockable panel.

    build_code is a Python one-liner that fills the current parent, e.g.
    "import animkit.ui.tween_ui as m; m.build()".

    It is passed to -uiScript AS PYTHON, not wrapped in a MEL python() call.
    A workspaceControl created through maya.cmds executes its uiScript as
    Python; wrapping it in python("...") raises
    NameError: name 'python' is not defined, the uiScript dies, and you get a
    panel with a title bar and nothing inside it.

    An existing control that was never successfully built is deleted and
    recreated rather than restored. Otherwise a single failed uiScript leaves a
    permanently empty panel that every later show() politely restores, with no
    way out but knowing to delete it by hand.
    """
    if cmds.workspaceControl(control_name, q=True, exists=True):
        if is_built(control_name):
            if cmds.workspaceControl(control_name, q=True, visible=True):
                cmds.workspaceControl(control_name, e=True, restore=True)
            else:
                cmds.workspaceControl(
                    control_name, e=True, visible=True, restore=True
                )
            return control_name
        log.warning(
            "animkit: %s exists but was never populated -- rebuilding it",
            control_name,
        )
        delete_workspace_control(control_name)

    cmds.workspaceControl(
        control_name,
        label=label,
        retain=False,
        floating=True,
        initialWidth=width,
        uiScript=build_code,
    )

    # The uiScript runs synchronously during creation, so a populated panel is
    # verifiable right now. Say so plainly instead of handing back an empty
    # window and a return value that looks like success.
    if not is_built(control_name):
        log.error(
            "animkit: %s was created but its uiScript did not populate it. "
            "Check the Script Editor for a traceback, then run "
            "animkit.ui.tween_ui.reset() before trying again.",
            control_name,
        )
    return control_name


def clear_children(widget):
    """Empty a workspaceControl before refilling it.

    Maya sometimes re-runs a uiScript against a parent that already has
    children (notably on workspace switches). Without this you get the panel
    stacked on top of itself.
    """
    if widget is None:
        return
    layout = widget.layout()
    if layout is None:
        return
    while layout.count():
        item = layout.takeAt(0)
        child = item.widget()
        if child is not None:
            child.setParent(None)
            child.deleteLater()
