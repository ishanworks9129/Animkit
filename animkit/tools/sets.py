"""Named selection sets, stored on the rig.

    store("left hand")          # save the current selection under a name
    recall("left hand")         # select it again
    recall_slot(1)              # what a hotkey binds to

WHY AN objectSet AND NOT A JSON FILE
------------------------------------
Because a selection set is a list of NODES, and only Maya can keep a list of
nodes correct. Written to a preferences file it becomes a list of node NAMES,
and every one of these then silently rots:

    the animator renames a control          -> the name no longer resolves
    the rig is referenced under a namespace -> every name is wrong by a prefix
    the same rig is loaded twice            -> one name matches two nodes
    a control is deleted                    -> a stale name nobody notices

An objectSet holds real connections to the nodes. Renames follow, namespaces
are irrelevant because nothing is matched by name, two copies of a rig have two
sets, and a deleted control leaves the set quietly. It is also saved in the
scene file, so the sets are there tomorrow without a preferences file to lose,
and they travel to whoever opens the shot.

WHAT "ON THE RIG" MEANS, AND WHERE THE NODE ACTUALLY LIVES
----------------------------------------------------------
Each set carries a message connection to its rig root, so a shot with three
characters keeps three sets of sets and a recall picks the right one. The set
NODE is created in whatever file is currently open, which is the only thing
Maya allows and also the right behaviour:

    made by an animator in a shot   -> lives in the shot, travels with the shot
    made by a rigger in the rig     -> lives in the rig, arrives with every
                                       reference of it, for free

No special case distinguishes the two. Referenced rigs work because the
connection's destination plug (the set's own `animkitRig`) is local, so nothing
is written into the referenced file and no reference edit is created.

RECOGNITION IS BY TAG, NEVER BY NAME
------------------------------------
A set is animkit's if it carries the `animkitSelectionSet` attribute. The node
name is a readability hint and nothing reads it. This is the same rule the
rest of the codebase follows -- ask the scene what something is rather than
matching a naming convention -- and it means an animator can rename the node in
the Outliner without breaking anything.

SLOTS, AND WHY THEY EXIST
-------------------------
Hotkeys are registered at startup, before any scene is open, so a hotkey cannot
name a set that does not exist yet. It names a SLOT -- a small integer stored
on the set -- and the slot resolves against whichever rig the animator is
working on at the time. That is what makes one keypress mean "this character's
left hand" for every character in the shot.

A NOTE ON tools.pose.rig_controls
---------------------------------
It recognises a control set by its contents, so a selection set of controls
will be read as one. That is deliberate: a set an animator made of things they
select and pose is good evidence about what a control is, and its only effect
is to contribute members that have no shape of their own.
"""

import logging
import re

from maya import cmds

from animkit.core import pairing, selection, undo
from animkit.tools import registry

log = logging.getLogger(__name__)

#: The tag that makes an objectSet one of ours. Holds the display name.
TAG_ATTR = "animkitSelectionSet"
#: Hotkey slot, 1..MAX_SLOTS. 0 means "no slot" -- panel only.
SLOT_ATTR = "animkitSlot"
#: message plug, connected from the rig root's .message.
RIG_ATTR = "animkitRig"

#: How many slots get a runTimeCommand. Six because that is roughly how many
#: distinct selections an animator holds in muscle memory, and every one costs
#: a hotkey the animator has to find room for.
MAX_SLOTS = 6

_NAME_SAFE = re.compile(r"[^A-Za-z0-9_]+")


class SelectionSet(object):
    """One named set. A thin reader over the objectSet node; holds no state.

    Deliberately not cached. Sets are created, renamed and deleted by the
    animator while the panel is open, and a stale wrapper showing the previous
    membership is worse than reading four attributes again.
    """

    def __init__(self, node):
        self.node = node

    @property
    def name(self):
        try:
            return cmds.getAttr(self.node + "." + TAG_ATTR) or self.node
        except Exception:
            return self.node

    @property
    def slot(self):
        try:
            return int(cmds.getAttr(self.node + "." + SLOT_ATTR) or 0)
        except Exception:
            return 0

    @property
    def root(self):
        """The rig root this set belongs to, or None if it was orphaned."""
        try:
            found = cmds.listConnections(
                self.node + "." + RIG_ATTR, source=True, destination=False
            ) or []
        except Exception:
            return None
        return found[0] if found else None

    @property
    def members(self):
        """Members that still exist, flattened through any nested set.

        Maya drops a deleted node from its sets on its own, so this needs no
        pruning -- but a set built by hand in the Outliner can nest, and a
        recall that selected a set node instead of its contents would look
        like the tool had lost the selection.

        Names come back EXACTLY as Maya gives them, paths and namespaces
        included. Shortening them here is how a two-character shot ends up
        selecting the wrong arm: two rigs without namespaces have controls
        with the same short name, and Maya resolves the ambiguity by picking
        one.
        """
        return _flatten(self.node)

    def __len__(self):
        return len(self.members)

    def __repr__(self):
        return "<SelectionSet {0!r} slot={1} on {2}>".format(
            self.name, self.slot, self.root
        )


def _flatten(node, _depth=0):
    if _depth > 8:
        return []
    try:
        members = cmds.sets(node, q=True) or []
    except Exception:
        return []
    out = []
    for member in members:
        try:
            if cmds.objectType(member, isAType="objectSet"):
                out.extend(_flatten(member, _depth + 1))
                continue
        except Exception:
            pass
        out.append(member)
    return out


# --- finding sets -----------------------------------------------------------


def all_sets():
    """Every animkit selection set in the scene, in slot then name order."""
    found = []
    try:
        candidates = cmds.ls(type="objectSet") or []
    except Exception:
        return found

    for node in candidates:
        try:
            if not cmds.attributeQuery(TAG_ATTR, node=node, exists=True):
                continue
        except Exception:
            continue
        found.append(SelectionSet(node))

    # Slot order first, because that is the order the hotkeys are in and the
    # order the panel lists them. Unslotted sets (slot 0) sort to the end
    # rather than the front, which is where "no hotkey" belongs.
    return sorted(found, key=lambda s: (s.slot or MAX_SLOTS + 1, s.name))


def sets_for(root):
    """The sets belonging to one rig root. [] for None."""
    if not root:
        return []
    return [s for s in all_sets() if s.root == root]


def current_root(nodes=None):
    """Which rig a recall should apply to.

    The selection decides, because that is what the animator is looking at and
    it is also self-correcting -- one recall selects controls, so every recall
    after it resolves without ambiguity.

    With nothing selected, a scene holding exactly one rig with sets is not
    ambiguous either, and refusing there would make the first press of a hotkey
    fail for the overwhelmingly common one-character shot.
    """
    nodes = nodes if nodes is not None else selection.selected_nodes()
    if nodes:
        return pairing.root_of(nodes[0])

    roots = []
    for candidate in all_sets():
        root = candidate.root
        if root and root not in roots:
            roots.append(root)
    return roots[0] if len(roots) == 1 else None


def find(name, root=None):
    """One set by display name, on `root` (default: the current rig)."""
    root = root if root is not None else current_root()
    for candidate in sets_for(root):
        if candidate.name == name:
            return candidate
    # Fall back to a scene-wide search, but only when it is unambiguous. A rig
    # whose root cannot be resolved must not silently recall another
    # character's set of the same name.
    matches = [s for s in all_sets() if s.name == name]
    return matches[0] if len(matches) == 1 else None


# --- writing ----------------------------------------------------------------


def _node_name(name):
    safe = _NAME_SAFE.sub("_", name).strip("_") or "set"
    return "animkitSet_" + safe


def _next_slot(root):
    """The lowest free hotkey slot on this rig, or 0 when they are all taken."""
    used = {s.slot for s in sets_for(root)}
    for slot in range(1, MAX_SLOTS + 1):
        if slot not in used:
            return slot
    return 0


def _tag(node, name, slot, root):
    if not cmds.attributeQuery(TAG_ATTR, node=node, exists=True):
        cmds.addAttr(node, longName=TAG_ATTR, dataType="string")
    cmds.setAttr(node + "." + TAG_ATTR, name, type="string")

    if not cmds.attributeQuery(SLOT_ATTR, node=node, exists=True):
        cmds.addAttr(node, longName=SLOT_ATTR, attributeType="long")
    cmds.setAttr(node + "." + SLOT_ATTR, slot)

    if not cmds.attributeQuery(RIG_ATTR, node=node, exists=True):
        cmds.addAttr(node, longName=RIG_ATTR, attributeType="message")
    if root and cmds.objExists(root):
        plug = node + "." + RIG_ATTR
        try:
            if not cmds.listConnections(plug, source=True, destination=False):
                # Destination is the LOCAL set's plug, so a referenced rig root
                # as the source writes nothing into the referenced file and
                # creates no reference edit.
                cmds.connectAttr(root + ".message", plug)
        except Exception:
            log.debug("animkit: could not link %s to %s", node, root,
                      exc_info=True)


def store(name, nodes=None, root=None):
    """Save `nodes` (default: the selection) under `name`. Returns a SelectionSet.

    Storing over an existing name REPLACES its members and keeps its slot,
    because "save selection as Left Hand" pressed twice means the second one --
    and silently making a second set with the same name would leave the hotkey
    pointing at the first, which is the one the animator just decided was
    wrong.
    """
    nodes = nodes if nodes is not None else selection.selected_nodes()
    name = (name or "").strip()

    if not name:
        cmds.warning("animkit: a selection set needs a name")
        return None
    if not nodes:
        cmds.warning(
            "animkit: nothing selected -- select the controls you want in "
            "%r first" % name
        )
        return None

    root = root if root is not None else pairing.root_of(nodes[0])

    with undo.undo_chunk("animkit: store selection set"):
        existing = None
        for candidate in sets_for(root):
            if candidate.name == name:
                existing = candidate
                break

        if existing is not None:
            slot = existing.slot
            try:
                cmds.sets(clear=existing.node)
                cmds.sets(nodes, addElement=existing.node)
            except Exception:
                log.exception("animkit: could not update selection set %r", name)
                return None
            _tag(existing.node, name, slot, root)
            print("animkit: updated selection set %r (%d node(s))"
                  % (name, len(nodes)))
            return existing

        slot = _next_slot(root)
        try:
            node = cmds.sets(nodes, name=_node_name(name))
        except Exception:
            log.exception("animkit: could not create selection set %r", name)
            cmds.warning(
                "animkit: could not store %r -- see the Script Editor" % name
            )
            return None
        _tag(node, name, slot, root)

    if slot:
        print("animkit: stored selection set %r (%d node(s)) on slot %d"
              % (name, len(nodes), slot))
    else:
        print(
            "animkit: stored selection set %r (%d node(s)). All %d hotkey "
            "slots on this rig are taken, so it has no hotkey -- recall it "
            "from the Sets panel, or free a slot."
            % (name, len(nodes), MAX_SLOTS)
        )
    return SelectionSet(node)


def remove(name, root=None):
    """Delete a set. The MEMBERS are untouched -- deleting an objectSet in Maya
    removes the set and nothing else."""
    found = find(name, root=root)
    if found is None:
        cmds.warning("animkit: no selection set called %r" % name)
        return False
    with undo.undo_chunk("animkit: delete selection set"):
        cmds.delete(found.node)
    return True


def rename(old, new, root=None):
    found = find(old, root=root)
    new = (new or "").strip()
    if found is None:
        cmds.warning("animkit: no selection set called %r" % old)
        return False
    if not new:
        cmds.warning("animkit: a selection set needs a name")
        return False
    with undo.undo_chunk("animkit: rename selection set"):
        cmds.setAttr(found.node + "." + TAG_ATTR, new, type="string")
    return True


def set_slot(name, slot, root=None):
    """Move a set to a hotkey slot, swapping with whatever was there.

    Swapping rather than refusing or duplicating: two sets on one slot makes a
    hotkey ambiguous, and refusing would mean the animator has to clear the old
    one first to do the obvious thing.
    """
    found = find(name, root=root)
    if found is None:
        cmds.warning("animkit: no selection set called %r" % name)
        return False
    slot = int(slot)
    if not 0 <= slot <= MAX_SLOTS:
        cmds.warning("animkit: slot must be 0 (none) to %d" % MAX_SLOTS)
        return False

    rig = found.root
    with undo.undo_chunk("animkit: move selection set slot"):
        if slot:
            for other in sets_for(rig):
                if other.node != found.node and other.slot == slot:
                    cmds.setAttr(other.node + "." + SLOT_ATTR, found.slot)
        cmds.setAttr(found.node + "." + SLOT_ATTR, slot)
    return True


def add_members(name, nodes=None, root=None):
    nodes = nodes if nodes is not None else selection.selected_nodes()
    found = find(name, root=root)
    if found is None or not nodes:
        return 0
    with undo.undo_chunk("animkit: add to selection set"):
        cmds.sets(nodes, addElement=found.node)
    return len(nodes)


def remove_members(name, nodes=None, root=None):
    nodes = nodes if nodes is not None else selection.selected_nodes()
    found = find(name, root=root)
    if found is None or not nodes:
        return 0
    # ASK MAYA whether each node is a member rather than matching names. The
    # set stores full paths and the selection reports short ones, so a string
    # comparison quietly removes nothing at all.
    present = []
    for node in nodes:
        try:
            if cmds.sets(node, isMember=found.node):
                present.append(node)
        except Exception:
            continue
    if not present:
        return 0
    with undo.undo_chunk("animkit: remove from selection set"):
        cmds.sets(present, remove=found.node)
    return len(present)


# --- recall -----------------------------------------------------------------


def _select(found, add=False):
    members = found.members
    if not members:
        cmds.warning(
            "animkit: selection set %r is empty -- every control that was in "
            "it has been deleted" % found.name
        )
        return 0
    try:
        if add:
            cmds.select(members, add=True)
        else:
            cmds.select(members)
    except Exception:
        log.exception("animkit: could not select %r", found.name)
        return 0
    return len(members)


def recall(name, root=None, add=False):
    """Select a set by name. Returns how many nodes were selected."""
    found = find(name, root=root)
    if found is None:
        cmds.warning("animkit: no selection set called %r" % name)
        return 0
    return _select(found, add=add)


def recall_slot(slot, add=False):
    """Select the set on `slot`, for the rig the animator is working on.

    This is what a hotkey calls, so it has to behave when pressed at a moment
    the animator did not think about: no selection, no sets, the wrong
    character, an empty scene. Every one of those says what happened rather
    than failing silently, because a hotkey that does nothing and says nothing
    reads as a broken tool.
    """
    slot = int(slot)
    root = current_root()

    matches = [s for s in sets_for(root) if s.slot == slot]
    if not matches:
        # No resolvable rig, or that rig has nothing on this slot. A single
        # scene-wide match is still unambiguous and is what the animator meant.
        scene_wide = [s for s in all_sets() if s.slot == slot]
        if len(scene_wide) == 1:
            matches = scene_wide
        elif len(scene_wide) > 1:
            cmds.warning(
                "animkit: %d characters have a set on slot %d and nothing is "
                "selected, so there is no way to tell which you meant. Select "
                "any control on the character first."
                % (len(scene_wide), slot)
            )
            return 0

    if not matches:
        if all_sets():
            cmds.warning(
                "animkit: no selection set on slot %d for this rig. Open the "
                "Sets panel to see what is on which slot." % slot
            )
        else:
            cmds.warning(
                "animkit: no selection sets in this scene yet. Select some "
                "controls and press Store in the Sets panel."
            )
        return 0

    return _select(matches[0], add=add)


def store_prompt():
    """Ask for a name, then store the selection. The hotkey-friendly entry point.

    The prompt is here rather than in the UI module so that a hotkey works with
    no panel open. It is the one place in the tools layer that opens a dialog,
    and it degrades to a warning when there is no UI at all -- mayapy, batch --
    rather than raising.
    """
    nodes = selection.selected_nodes()
    if not nodes:
        cmds.warning(
            "animkit: nothing selected -- select the controls to store first"
        )
        return None

    try:
        result = cmds.promptDialog(
            title="animkit",
            message="Name this selection set (%d node(s)):" % len(nodes),
            button=("Store", "Cancel"),
            defaultButton="Store",
            cancelButton="Cancel",
            dismissString="Cancel",
        )
        if result != "Store":
            return None
        name = cmds.promptDialog(q=True, text=True)
    except Exception:
        cmds.warning(
            "animkit: no UI available to ask for a name -- call "
            "animkit.tools.sets.store('a name') instead"
        )
        return None

    return store(name, nodes=nodes)


def _recall_slot_operations():
    """One Operation per hotkey slot.

    Generated rather than listed, so MAX_SLOTS is the only place the number
    lives and a slot cannot exist without a runTimeCommand to reach it.
    """
    made = []
    for slot in range(1, MAX_SLOTS + 1):
        made.append(registry.Operation(
            "animkitSetRecall%d" % slot,
            "%d" % slot,
            "Select selection set %d on the current rig" % slot,
            recall_slot,
            {"slot": slot},
            group="Sets",
        ))
    return tuple(made)


OPERATIONS = (
    registry.Operation(
        "animkitSetStore", "Store",
        "Save the selected controls as a named selection set on this rig",
        store_prompt, group="Sets",
    ),
) + _recall_slot_operations()

BY_NAME = dict((op.name, op) for op in OPERATIONS)
