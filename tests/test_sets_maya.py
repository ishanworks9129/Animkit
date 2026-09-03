"""Named selection sets.

The interesting cases are all the ones a preferences file would get wrong:
renames, namespaces, two copies of the same rig, and a control that has been
deleted. Those are what decide whether this can be stored as node names, and
the answer is no -- so most of what is asserted here is that names are never
what resolves a set.
"""

import pytest

from conftest import requires_maya  # noqa: F401

pytestmark = requires_maya


def _character(cmds, prefix="", namespace=""):
    """A root with two shaped controls under it. Returns (root, [controls])."""
    if namespace:
        if not cmds.namespace(exists=namespace):
            cmds.namespace(add=namespace)
        cmds.namespace(set=namespace)

    root = cmds.createNode("transform", name=prefix + "rig_root")
    controls = []
    for side, sign in (("L", 1.0), ("R", -1.0)):
        ctrl = cmds.createNode(
            "transform", name="%s%s_hand_ctrl" % (prefix, side), parent=root
        )
        cmds.createNode("nurbsCurve", parent=ctrl)
        cmds.setAttr(ctrl + ".translateX", sign * 5)
        controls.append(ctrl)

    if namespace:
        cmds.namespace(set=":")
    return root, controls


class TestStoreAndRecall:
    def test_stores_the_selection_under_a_name(self, clean_scene):
        from animkit.tools import sets

        cmds = clean_scene
        _root, controls = _character(cmds)
        cmds.select(controls)

        entry = sets.store("hands")
        assert entry is not None
        assert entry.name == "hands"
        assert len(entry.members) == 2

    def test_recall_selects_the_members(self, clean_scene):
        from animkit.tools import sets

        cmds = clean_scene
        _root, controls = _character(cmds)
        cmds.select(controls)
        sets.store("hands")
        cmds.select(clear=True)

        assert sets.recall("hands") == 2
        assert set(cmds.ls(selection=True)) == set(controls)

    def test_recall_add_extends_the_selection(self, clean_scene):
        from animkit.tools import sets

        cmds = clean_scene
        _root, controls = _character(cmds)
        other = cmds.createNode("transform", name="elsewhere")

        cmds.select(controls)
        sets.store("hands")
        cmds.select(other)

        sets.recall("hands", add=True)
        assert set(cmds.ls(selection=True)) == set(controls + [other])

    def test_storing_the_same_name_twice_replaces_and_keeps_the_slot(
        self, clean_scene
    ):
        """Save-as twice means the second one.

        A second set of the same name would leave the hotkey pointing at the
        first -- the one the animator just decided was wrong.
        """
        from animkit.tools import sets

        cmds = clean_scene
        _root, controls = _character(cmds)

        cmds.select(controls)
        first = sets.store("hands")
        slot = first.slot

        cmds.select(controls[0])
        second = sets.store("hands")

        assert len(sets.all_sets()) == 1
        assert second.slot == slot
        assert len(second.members) == 1

    def test_stores_nothing_with_an_empty_selection(self, clean_scene):
        from animkit.tools import sets

        cmds = clean_scene
        _character(cmds)
        cmds.select(clear=True)

        assert sets.store("hands") is None
        assert sets.all_sets() == []

    def test_an_unnamed_set_is_refused(self, clean_scene):
        from animkit.tools import sets

        cmds = clean_scene
        _root, controls = _character(cmds)
        cmds.select(controls)

        assert sets.store("   ") is None
        assert sets.all_sets() == []


class TestNamesAreNeverWhatResolvesASet:
    """Every one of these is a case a JSON list of node names gets wrong."""

    def test_renaming_a_control_does_not_break_the_set(self, clean_scene):
        from animkit.tools import sets

        cmds = clean_scene
        _root, controls = _character(cmds)
        cmds.select(controls)
        sets.store("hands")

        renamed = cmds.rename(controls[0], "L_paw_ctrl")
        cmds.select(clear=True)

        assert sets.recall("hands") == 2
        assert renamed in cmds.ls(selection=True)

    def test_renaming_the_set_node_does_not_break_it(self, clean_scene):
        """Recognition is by tag. The node name is a readability hint."""
        from animkit.tools import sets

        cmds = clean_scene
        _root, controls = _character(cmds)
        cmds.select(controls)
        entry = sets.store("hands")

        cmds.rename(entry.node, "someone_renamed_this")
        assert len(sets.all_sets()) == 1
        assert sets.all_sets()[0].name == "hands"

    def test_a_deleted_control_leaves_the_set_quietly(self, clean_scene):
        from animkit.tools import sets

        cmds = clean_scene
        _root, controls = _character(cmds)
        cmds.select(controls)
        sets.store("hands")

        cmds.delete(controls[0])
        cmds.select(clear=True)

        assert sets.recall("hands") == 1
        assert cmds.ls(selection=True) == [controls[1]]

    def test_two_characters_keep_separate_sets(self, clean_scene):
        """The case that makes storing names actively dangerous.

        Both characters have an `L_hand_ctrl`. A recall must select the one
        belonging to the character the animator is working on.
        """
        from animkit.tools import sets

        cmds = clean_scene
        root_a, controls_a = _character(cmds, namespace="charA")
        root_b, controls_b = _character(cmds, namespace="charB")

        cmds.select(controls_a)
        sets.store("hands")
        cmds.select(controls_b)
        sets.store("hands")

        assert len(sets.all_sets()) == 2
        assert len(sets.sets_for(root_a)) == 1
        assert len(sets.sets_for(root_b)) == 1

        cmds.select(controls_b[0])
        sets.recall("hands")
        assert set(cmds.ls(selection=True)) == set(controls_b)

        cmds.select(controls_a[0])
        sets.recall("hands")
        assert set(cmds.ls(selection=True)) == set(controls_a)


class TestSlots:
    """What a hotkey binds to, and why it is a number and not a name."""

    def test_slots_are_handed_out_from_one(self, clean_scene):
        from animkit.tools import sets

        cmds = clean_scene
        _root, controls = _character(cmds)

        cmds.select(controls[0])
        assert sets.store("left").slot == 1
        cmds.select(controls[1])
        assert sets.store("right").slot == 2

    def test_recall_slot_selects_that_set(self, clean_scene):
        from animkit.tools import sets

        cmds = clean_scene
        _root, controls = _character(cmds)
        cmds.select(controls[0])
        sets.store("left")
        cmds.select(controls[1])
        sets.store("right")

        cmds.select(controls[0])
        assert sets.recall_slot(2) == 1
        assert cmds.ls(selection=True) == [controls[1]]

    def test_one_slot_follows_whichever_character_is_selected(self, clean_scene):
        """The whole point of a slot. One keypress, three characters."""
        from animkit.tools import sets

        cmds = clean_scene
        _root_a, controls_a = _character(cmds, namespace="charA")
        _root_b, controls_b = _character(cmds, namespace="charB")

        cmds.select(controls_a)
        assert sets.store("hands").slot == 1
        cmds.select(controls_b)
        assert sets.store("hands").slot == 1

        cmds.select(controls_a[0])
        sets.recall_slot(1)
        assert set(cmds.ls(selection=True)) == set(controls_a)

        cmds.select(controls_b[0])
        sets.recall_slot(1)
        assert set(cmds.ls(selection=True)) == set(controls_b)

    def test_a_lone_character_resolves_with_nothing_selected(self, clean_scene):
        """The first press of a hotkey, in the shot everybody actually has."""
        from animkit.tools import sets

        cmds = clean_scene
        _root, controls = _character(cmds)
        cmds.select(controls)
        sets.store("hands")

        cmds.select(clear=True)
        assert sets.recall_slot(1) == 2

    def test_ambiguous_slot_with_nothing_selected_does_nothing(
        self, clean_scene
    ):
        """It must not guess. Two characters, no selection, no way to tell."""
        from animkit.tools import sets

        cmds = clean_scene
        _root_a, controls_a = _character(cmds, namespace="charA")
        _root_b, controls_b = _character(cmds, namespace="charB")
        cmds.select(controls_a)
        sets.store("hands")
        cmds.select(controls_b)
        sets.store("hands")

        cmds.select(clear=True)
        assert sets.recall_slot(1) == 0
        assert cmds.ls(selection=True) == []

    def test_slots_can_be_swapped(self, clean_scene):
        from animkit.tools import sets

        cmds = clean_scene
        _root, controls = _character(cmds)
        cmds.select(controls[0])
        sets.store("left")
        cmds.select(controls[1])
        sets.store("right")

        cmds.select(controls[0])
        assert sets.set_slot("right", 1)
        assert sets.find("right").slot == 1
        assert sets.find("left").slot == 2

    def test_a_slot_beyond_the_last_is_refused(self, clean_scene):
        from animkit.tools import sets

        cmds = clean_scene
        _root, controls = _character(cmds)
        cmds.select(controls)
        sets.store("hands")

        assert not sets.set_slot("hands", sets.MAX_SLOTS + 1)
        assert sets.find("hands").slot == 1


class TestEditing:
    def test_rename(self, clean_scene):
        from animkit.tools import sets

        cmds = clean_scene
        _root, controls = _character(cmds)
        cmds.select(controls)
        sets.store("hands")

        assert sets.rename("hands", "paws")
        assert sets.find("hands") is None
        assert sets.find("paws") is not None

    def test_remove_deletes_the_set_and_not_the_controls(self, clean_scene):
        from animkit.tools import sets

        cmds = clean_scene
        _root, controls = _character(cmds)
        cmds.select(controls)
        sets.store("hands")

        assert sets.remove("hands")
        assert sets.all_sets() == []
        for ctrl in controls:
            assert cmds.objExists(ctrl)

    def test_add_and_remove_members(self, clean_scene):
        from animkit.tools import sets

        cmds = clean_scene
        _root, controls = _character(cmds)
        cmds.select(controls[0])
        sets.store("hands")

        cmds.select(controls[1])
        assert sets.add_members("hands") == 1
        assert len(sets.find("hands").members) == 2

        assert sets.remove_members("hands") == 1
        assert len(sets.find("hands").members) == 1

    def test_remove_members_ignores_nodes_that_are_not_in_the_set(
        self, clean_scene
    ):
        from animkit.tools import sets

        cmds = clean_scene
        _root, controls = _character(cmds)
        stranger = cmds.createNode("transform", name="stranger")
        cmds.select(controls)
        sets.store("hands")

        cmds.select(stranger)
        assert sets.remove_members("hands") == 0
        assert len(sets.find("hands").members) == 2


class TestPressedAtTheWrongMoment:
    """Every one of these is a hotkey press the animator did not plan.

    None may raise, and each has to say what happened -- a hotkey that does
    nothing and says nothing reads as a broken tool.
    """

    def test_recall_slot_in_an_empty_scene(self, clean_scene):
        from animkit.tools import sets

        clean_scene.select(clear=True)
        assert sets.recall_slot(1) == 0

    def test_recall_slot_that_holds_nothing(self, clean_scene):
        from animkit.tools import sets

        cmds = clean_scene
        _root, controls = _character(cmds)
        cmds.select(controls)
        sets.store("hands")

        assert sets.recall_slot(sets.MAX_SLOTS) == 0

    def test_recall_an_unknown_name(self, clean_scene):
        from animkit.tools import sets

        _character(clean_scene)
        assert sets.recall("nothing called this") == 0

    def test_recall_a_set_whose_controls_are_all_gone(self, clean_scene):
        from animkit.tools import sets

        cmds = clean_scene
        _root, controls = _character(cmds)
        cmds.select(controls)
        sets.store("hands")

        cmds.delete(controls)
        cmds.select(clear=True)
        assert sets.recall("hands") == 0

    def test_every_slot_operation_runs_on_an_empty_scene(self, clean_scene):
        """Bound to hotkeys, so all of them get pressed on an empty scene."""
        from animkit.tools import sets

        clean_scene.select(clear=True)
        for op in sets.OPERATIONS:
            if op.name == "animkitSetStore":
                continue  # opens a dialog; covered by store() directly
            assert op.invoke() == 0


class TestUndo:
    def test_store_is_one_undo_step(self, clean_scene):
        from animkit.tools import sets

        cmds = clean_scene
        _root, controls = _character(cmds)
        cmds.select(controls)
        sets.store("hands")
        assert len(sets.all_sets()) == 1

        cmds.undo()
        assert sets.all_sets() == []

    def test_remove_is_one_undo_step(self, clean_scene):
        from animkit.tools import sets

        cmds = clean_scene
        _root, controls = _character(cmds)
        cmds.select(controls)
        sets.store("hands")
        sets.remove("hands")
        assert sets.all_sets() == []

        cmds.undo()
        assert len(sets.all_sets()) == 1


class TestRegistration:
    def test_every_set_operation_is_registered(self):
        from animkit import commands
        from animkit.tools import sets

        registered = {name for name, _a, _c in commands.COMMANDS}
        for op in sets.OPERATIONS:
            assert op.name in registered, "%s is not registered" % op.name

    def test_there_is_a_recall_command_per_slot(self):
        from animkit.tools import sets

        names = {op.name for op in sets.OPERATIONS}
        for slot in range(1, sets.MAX_SLOTS + 1):
            assert "animkitSetRecall%d" % slot in names

    def test_commands_compile_and_name_the_right_module(self):
        from animkit.tools import sets

        for op in sets.OPERATIONS:
            assert op.command.startswith("import animkit.tools.sets as s;")
            compile(op.command, "<op>", "exec")

    def test_names_are_unique_across_every_registry(self):
        """The panel, the radial and the Hotkey Editor share one namespace."""
        from animkit import commands

        names = [name for name, _a, _c in commands.COMMANDS]
        assert len(names) == len(set(names))
