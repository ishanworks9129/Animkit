"""Persisting a captured rest pose in the scene.

    Set Rest  ->  xform.capture_rest()  ->  rest_store.save()
    file open ->  rest_store.load()     ->  xform._rest_overrides

WHY THIS HAD TO EXIST
---------------------
`xform._rest_overrides` is a module-level dict, so a captured rest pose lived
exactly as long as the Python session. Close Maya, reopen the shot, and it was
gone -- which made the advice printed by every ambiguous Reset ("put the rig on
its neutral pose and press Set Rest") hollow: the animator would do it, it would
work, and it would be gone tomorrow.

It also made the dict OUTLIVE ITS SCENE, which is worse than losing it. Nothing
cleared it on file-open, so a rest captured for `ctrl` in one shot was still
sitting there keyed on that name when a different shot with a different `ctrl`
was opened, and the mirror silently used it. Scene callbacks clear it now -- see
install().

HOW IT IS STORED, AND WHY NOT ON THE CONTROLS
---------------------------------------------
One local `network` node per rig, holding parallel multi-attributes: a message
connection to each control, and that control's rest world matrix at the same
index.

The obvious alternative -- an `animkitRestMatrix` attribute added to each
control -- is wrong for the case that matters. Rigs are referenced, and adding
an attribute to a referenced node is a reference edit: it makes the shot file
carry per-control edits against the rig, which break when the rig is updated and
which a pipeline may strip or refuse outright. A local network node connected to
referenced controls writes nothing into the reference, because every plug being
written is on the local node.

Controls are identified by MESSAGE CONNECTION, never by name, for the same
reason selection sets are: a name-keyed store rots on rename, on namespace, and
on a second copy of the same rig in one shot.
"""

import logging

import maya.api.OpenMaya as om2
from maya import cmds

from animkit.core import scene, xform

log = logging.getLogger(__name__)

#: The tag that makes a network node ours. Recognition is by attribute, never by
#: node name -- an animator may rename it in the Outliner.
TAG_ATTR = "animkitRestPose"
CONTROL_ATTR = "restControl"
MATRIX_ATTR = "restMatrix"

NODE_NAME = "animkitRestPose"


def _store_nodes():
    """Every rest-pose store in the scene."""
    found = []
    try:
        candidates = cmds.ls(type="network") or []
    except Exception:
        return found
    for node in candidates:
        try:
            if cmds.attributeQuery(TAG_ATTR, node=node, exists=True):
                found.append(node)
        except Exception:
            continue
    return found


def _make_store():
    node = cmds.createNode("network", name=NODE_NAME)
    cmds.addAttr(node, longName=TAG_ATTR, attributeType="message")
    cmds.addAttr(node, longName=CONTROL_ATTR, attributeType="message",
                 multi=True, indexMatters=True)
    cmds.addAttr(node, longName=MATRIX_ATTR, dataType="matrix",
                 multi=True, indexMatters=True)
    return node


def save(nodes=None):
    """Write the current rest overrides into the scene. Returns the count.

    Replaces whatever was stored: a captured rest is a snapshot, and merging two
    snapshots taken at different moments would produce a rest pose that never
    existed.
    """
    overrides = xform._rest_overrides
    if nodes is not None:
        overrides = dict(
            (n, m) for n, m in overrides.items() if n in set(nodes)
        )

    for existing in _store_nodes():
        try:
            cmds.delete(existing)
        except Exception:
            log.debug("animkit: could not clear rest store %s", existing,
                      exc_info=True)

    if not overrides:
        return 0

    node = _make_store()
    written = 0
    for index, (control, matrix) in enumerate(sorted(overrides.items())):
        if not cmds.objExists(control):
            continue
        try:
            cmds.connectAttr(
                control + ".message",
                "{0}.{1}[{2}]".format(node, CONTROL_ATTR, index),
            )
            cmds.setAttr(
                "{0}.{1}[{2}]".format(node, MATRIX_ATTR, index),
                list(matrix),
                type="matrix",
            )
            written += 1
        except Exception:
            log.debug("animkit: could not store rest for %s", control,
                      exc_info=True)
    return written


def load():
    """Read any stored rest pose back into xform. Returns the count.

    Replaces the in-memory overrides rather than adding to them, so opening a
    scene without a stored rest correctly leaves none -- which is the bug that
    let one shot's rest pose apply to the next shot's controls.
    """
    xform._rest_overrides.clear()

    restored = 0
    for node in _store_nodes():
        try:
            indices = cmds.getAttr(
                "{0}.{1}".format(node, CONTROL_ATTR), multiIndices=True
            ) or []
        except Exception:
            continue

        for index in indices:
            try:
                control = cmds.listConnections(
                    "{0}.{1}[{2}]".format(node, CONTROL_ATTR, index),
                    source=True, destination=False,
                ) or []
                if not control:
                    # The control was deleted. Its rest goes with it rather
                    # than lingering under a name something else may reuse.
                    continue
                values = cmds.getAttr(
                    "{0}.{1}[{2}]".format(node, MATRIX_ATTR, index)
                )
                if not values:
                    continue
                short = control[0].split("|")[-1]
                xform._rest_overrides[short] = om2.MMatrix(values)
                restored += 1
            except Exception:
                log.debug("animkit: could not restore a rest entry from %s",
                          node, exc_info=True)

    if restored:
        log.debug("animkit: restored a captured rest pose for %d node(s)",
                  restored)
    return restored


def clear(write=True):
    """Forget the captured rest, in memory and in the scene."""
    xform.clear_rest_overrides()
    if not write:
        return 0
    removed = 0
    for node in _store_nodes():
        try:
            cmds.delete(node)
            removed += 1
        except Exception:
            log.debug("animkit: could not delete rest store %s", node,
                      exc_info=True)
    return removed


def install():
    """Wire the scene callbacks. Idempotent; called from animkit.startup().

    ONE HANDLER, ON THE AFTER EVENTS ONLY, and that is a deliberate correction.

    The obvious wiring is two: an invalidator that clears the overrides on any
    scene change, and an after-open handler that loads them back. Both fire on
    kAfterOpen, and Maya runs them in registration order -- so the whole thing
    hinges on an ordering that nothing declares and nothing enforces. Observed
    on Maya 2024: it loaded correctly in one scene and came back empty in
    another, from the same code, because the clear landed after the load.

    `load()` clears before it reads, so it is the only handler needed and the
    ordering question disappears. A scene with no stored rest correctly ends up
    with none, because the clear still happens -- it just happens inside the
    thing that repopulates.

    Nothing clears on kBeforeOpen any more, which is fine: the overrides are
    replaced at kAfterOpen and nothing reads a rest pose in between.
    """
    scene.register_after_open(load)
