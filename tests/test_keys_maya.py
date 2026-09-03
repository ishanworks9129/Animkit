"""Keyframe operations. Needs mayapy.

The parametrised classes here are the point of the file. They run over
`keys.OPERATIONS`, so every operation added to that registry is covered by all
of them the moment it exists, without anyone remembering to write a test.

They deliberately target the class of bug that actually bites in a tool like
this, which is NOT "the maths is wrong":

  * an operation that produces the right result in three undo entries
  * an operation that quietly writes to the base layer while a layer is active
  * an operation that writes to a locked layer
  * an operation that changes key indices underneath itself and edits the
    wrong key from the second one onward

ASSERT ON CURVE STATE, NEVER ON getAttr
---------------------------------------
`cmds.keyframe(q=True, valueChange=True)` reads the curve. `cmds.getAttr`
reads the result of DG evaluation, which is stale after any topology change
until the plug is dirtied -- so a getAttr assertion here would be testing
Maya's evaluation timing rather than whether the operation was correct. That
confusion has already cost this codebase one debugging session; see the README
section on stale DG reads.
"""

import pytest

from conftest import requires_maya  # noqa: F401

pytestmark = requires_maya


def _operations():
    from animkit.tools import keys

    return keys.OPERATIONS


def _op_ids():
    return [op.name for op in _operations()]


# Collected at import so parametrize can see them by name in the report.
try:
    from animkit.tools import keys as _keys

    ALL_OPERATIONS = list(_keys.OPERATIONS)
    ALL_IDS = [op.name for op in ALL_OPERATIONS]
except Exception:  # no Maya -- the whole module is skipped anyway
    ALL_OPERATIONS = []
    ALL_IDS = []


# --- fixtures ---------------------------------------------------------------


@pytest.fixture
def scene(clean_scene):
    """One control with keys at 1, 6 and 11 on tx, selected, cursor on 6."""
    cmds = clean_scene
    node = cmds.createNode("transform", name="ctrl")
    cmds.setKeyframe(node + ".tx", time=1, value=0)
    cmds.setKeyframe(node + ".tx", time=6, value=50)
    cmds.setKeyframe(node + ".tx", time=11, value=100)
    cmds.currentTime(6)
    cmds.select(node)
    cmds.selectKey(clear=True)
    return cmds, node


def key_times(cmds, node, attr="tx"):
    """Key times straight off the curve. No DG evaluation involved."""
    return cmds.keyframe(node + "." + attr, q=True, timeChange=True) or []


def key_values(cmds, node, attr="tx"):
    """Key values straight off the curve. See the module docstring."""
    return cmds.keyframe(node + "." + attr, q=True, valueChange=True) or []


def key_count(cmds, node, attr="tx"):
    return cmds.keyframe(node + "." + attr, q=True, keyframeCount=True)


def curve_state(cmds, node, attr="tx"):
    """Everything about the curve that an operation could change."""
    plug = node + "." + attr
    return {
        "times": tuple(cmds.keyframe(plug, q=True, timeChange=True) or []),
        "values": tuple(cmds.keyframe(plug, q=True, valueChange=True) or []),
        "in": tuple(cmds.keyTangent(plug, q=True, inTangentType=True) or []),
        "out": tuple(cmds.keyTangent(plug, q=True, outTangentType=True) or []),
        "pre": cmds.setInfinity(plug, q=True, preInfinite=True),
        "post": cmds.setInfinity(plug, q=True, postInfinite=True),
    }


# --- the harness ------------------------------------------------------------


@pytest.mark.parametrize("operation", ALL_OPERATIONS, ids=ALL_IDS)
class TestEveryOperation:
    """Properties that must hold for every operation in the registry."""

    def test_is_one_undo_step(self, operation, scene):
        """The bug this catches: a correct result in three undo entries.

        Asserted by comparing FULL curve state either side of a single undo,
        not by counting anything. If the operation opened more than one chunk,
        one Ctrl+Z leaves the curve partway back and this fails.

        Deliberately does NOT require the operation to change anything. On any
        one fixture some operations are legitimately identity -- setting auto
        tangents on keys that are already auto, or clearing a cycle on a curve
        that was not cycling -- and an assertion that they must change
        something would only force the fixture to lie. Whether each operation
        actually does its job is asserted per operation further down, where the
        fixture can be built for it.
        """
        cmds, node = scene
        before = curve_state(cmds, node)

        assert operation.invoke() >= 0
        cmds.undo()
        assert curve_state(cmds, node) == before

    def test_is_one_undo_step_in_selected_key_mode(self, operation, scene):
        """Same invariant with a Graph Editor selection, away from the cursor.

        This is the mode that skips layer resolution entirely and the one where
        an operation is most likely to be acting on several keys at once, so
        the index-invalidation bugs show up here first.
        """
        from animkit.core import layers

        cmds, node = scene
        curve_name = layers.resolve_curve(node + ".tx")
        cmds.currentTime(1)
        cmds.selectKey(clear=True)
        cmds.selectKey(curve_name)  # every key on the curve

        before = curve_state(cmds, node)
        operation.invoke()
        cmds.undo()
        cmds.selectKey(clear=True)

        assert curve_state(cmds, node) == before

    def test_redo_reapplies(self, operation, scene):
        cmds, node = scene
        operation.invoke()
        applied = curve_state(cmds, node)
        cmds.undo()
        cmds.redo()
        assert curve_state(cmds, node) == applied

    def test_returns_a_count(self, operation, scene):
        """Every operation reports how much it did, so a caller can tell
        "nothing was selected" from "it worked"."""
        _cmds, _node = scene
        result = operation.invoke()
        assert isinstance(result, int) and result >= 0

    def test_does_nothing_with_nothing_selected(self, operation, scene):
        """And leaves no undo entry behind while doing it."""
        cmds, node = scene
        before = curve_state(cmds, node)
        cmds.select(clear=True)

        assert operation.invoke() == 0
        assert curve_state(cmds, node) == before

    def test_respects_active_layer(self, operation, scene):
        """The base layer must not move while a layer is active.

        Read through the layer-resolved CURVE rather than through the
        attribute: getAttr returns the flattened stack, which would hide a
        base-layer write behind the layer's own contribution.
        """
        from animkit.core import layers

        cmds, node = scene
        cmds.select(node)
        layer = cmds.animLayer("L1", addSelectedObjects=True)
        cmds.animLayer(layer, edit=True, selected=True)
        cmds.setKeyframe(node + ".tx", time=1, value=0, animLayer=layer)
        cmds.setKeyframe(node + ".tx", time=6, value=5, animLayer=layer)
        cmds.setKeyframe(node + ".tx", time=11, value=10, animLayer=layer)
        cmds.select(node)
        cmds.currentTime(6)

        base = layers.resolve_curve(node + ".tx", layers.root_layer())
        assert base, "fixture failed: no base curve"

        def base_state():
            return (
                tuple(cmds.keyframe(base, q=True, timeChange=True) or []),
                tuple(cmds.keyframe(base, q=True, valueChange=True) or []),
                cmds.setInfinity(base, q=True, preInfinite=True),
            )

        before = base_state()
        operation.invoke()
        assert base_state() == before, "operation modified the base layer"

    def test_never_touches_locked_layer(self, operation, scene):
        """A locked layer is not merely skipped -- it must be unchanged."""
        cmds, node = scene
        cmds.select(node)
        layer = cmds.animLayer("Locked1", addSelectedObjects=True)
        cmds.animLayer(layer, edit=True, selected=True)
        cmds.setKeyframe(node + ".tx", time=1, value=0, animLayer=layer)
        cmds.setKeyframe(node + ".tx", time=6, value=5, animLayer=layer)
        cmds.setKeyframe(node + ".tx", time=11, value=10, animLayer=layer)

        from animkit.core import layers

        locked_curve = layers.resolve_curve(node + ".tx", layer)
        assert locked_curve, "fixture failed: no curve on the layer"

        def locked_state():
            return (
                tuple(cmds.keyframe(locked_curve, q=True, timeChange=True) or []),
                tuple(cmds.keyframe(locked_curve, q=True, valueChange=True) or []),
                tuple(cmds.keyTangent(locked_curve, q=True, outTangentType=True) or []),
                cmds.setInfinity(locked_curve, q=True, postInfinite=True),
            )

        cmds.animLayer(layer, edit=True, lock=True)
        cmds.select(node)
        cmds.currentTime(6)

        before = locked_state()
        operation.invoke()
        assert locked_state() == before, "operation wrote to a locked layer"

    def test_survives_a_single_key_curve(self, operation, clean_scene):
        """A lone key has no neighbours. Nothing may raise on it."""
        cmds = clean_scene
        node = cmds.createNode("transform", name="lonely")
        cmds.setKeyframe(node + ".tx", time=6, value=5)
        cmds.currentTime(6)
        cmds.select(node)
        cmds.selectKey(clear=True)

        operation.invoke()  # must not raise

    def test_survives_a_static_channel(self, operation, clean_scene):
        """Nothing animated at all: no keys created, no exception."""
        cmds = clean_scene
        node = cmds.createNode("transform", name="static")
        cmds.currentTime(6)
        cmds.select(node)
        cmds.selectKey(clear=True)

        assert operation.invoke() == 0
        assert key_count(cmds, node) == 0


# --- per-operation behaviour ------------------------------------------------


class TestOffset:
    def test_moves_keys_later(self, scene):
        from animkit.tools import keys

        cmds, node = scene
        cmds.selectKey(clear=True)
        cmds.select(node)
        keys.offset(frames=2)
        assert 8.0 in key_times(cmds, node)

    def test_moves_only_the_target_key(self, scene):
        from animkit.tools import keys

        cmds, node = scene
        keys.offset(frames=2)
        assert sorted(key_times(cmds, node)) == [1.0, 8.0, 11.0]

    def test_zero_offset_is_a_no_op(self, scene):
        from animkit.tools import keys

        cmds, node = scene
        before = curve_state(cmds, node)
        assert keys.offset(frames=0) == 0
        assert curve_state(cmds, node) == before

    def test_multiple_selected_keys_do_not_collide(self, scene):
        """Moving a set of keys must not merge two of them en route.

        Applied in the wrong order, the key at 6 lands on 11 before 11 has
        moved, Maya merges them, and the result is one key short. The count is
        the assertion that catches it.
        """
        from animkit.core import layers
        from animkit.tools import keys

        cmds, node = scene
        curve_name = layers.resolve_curve(node + ".tx")
        cmds.selectKey(clear=True)
        cmds.selectKey(curve_name, time=(6, 6))
        cmds.selectKey(curve_name, time=(11, 11), add=True)

        keys.offset(frames=5)
        cmds.selectKey(clear=True)

        assert key_count(cmds, node) == 3
        assert sorted(key_times(cmds, node)) == [1.0, 11.0, 16.0]

    def test_backward_offset_merges_onto_an_occupied_frame(self, scene):
        """Keys at 1/6/11; move 6 and 11 back by 5. The key landing on the
        existing key at frame 1 merges with it -- Maya's own behaviour for a
        key dropped on an occupied frame, and the documented contract of
        offset(). What must NOT happen is the two MOVING keys colliding with
        each other, which would lose frame 6 as well."""
        from animkit.core import layers
        from animkit.tools import keys

        cmds, node = scene
        curve_name = layers.resolve_curve(node + ".tx")
        cmds.selectKey(clear=True)
        cmds.selectKey(curve_name, time=(6, 6))
        cmds.selectKey(curve_name, time=(11, 11), add=True)

        keys.offset(frames=-5)
        cmds.selectKey(clear=True)

        assert sorted(key_times(cmds, node)) == [1.0, 6.0]


class TestRetime:
    def test_spreads_keys_apart(self, scene):
        from animkit.core import layers
        from animkit.tools import keys

        cmds, node = scene
        curve_name = layers.resolve_curve(node + ".tx")
        cmds.selectKey(clear=True)
        cmds.selectKey(curve_name)  # every key

        cmds.currentTime(6)
        keys.retime(factor=2.0)
        cmds.selectKey(clear=True)

        assert sorted(key_times(cmds, node)) == [-4.0, 6.0, 16.0]

    def test_pulls_keys_together(self, scene):
        from animkit.core import layers
        from animkit.tools import keys

        cmds, node = scene
        curve_name = layers.resolve_curve(node + ".tx")
        cmds.selectKey(clear=True)
        cmds.selectKey(curve_name)

        cmds.currentTime(6)
        keys.retime(factor=0.5)
        cmds.selectKey(clear=True)

        assert sorted(key_times(cmds, node)) == [3.5, 6.0, 8.5]

    def test_keeps_the_key_count(self, scene):
        from animkit.core import layers
        from animkit.tools import keys

        cmds, node = scene
        curve_name = layers.resolve_curve(node + ".tx")
        cmds.selectKey(clear=True)
        cmds.selectKey(curve_name)
        keys.retime(factor=0.5)
        cmds.selectKey(clear=True)
        assert key_count(cmds, node) == 3

    def test_factor_one_is_a_no_op(self, scene):
        from animkit.tools import keys

        cmds, node = scene
        before = curve_state(cmds, node)
        assert keys.retime(factor=1.0) == 0
        assert curve_state(cmds, node) == before


class TestSnapToFrame:
    def test_rounds_onto_whole_frames(self, scene):
        from animkit.core import layers
        from animkit.tools import keys

        cmds, node = scene
        curve_name = layers.resolve_curve(node + ".tx")
        cmds.keyframe(curve_name, edit=True, index=(1, 1),
                      timeChange=6.4, absolute=True)
        assert 6.4 in key_times(cmds, node)

        cmds.currentTime(6.4)
        keys.snap_to_frame()
        assert sorted(key_times(cmds, node)) == [1.0, 6.0, 11.0]


class TestTangents:
    def test_flat_sets_both_sides(self, scene):
        from animkit.tools import keys

        cmds, node = scene
        keys.set_tangents(preset="flat")
        assert cmds.keyTangent(node + ".tx", q=True, time=(6, 6),
                               inTangentType=True) == ["flat"]
        assert cmds.keyTangent(node + ".tx", q=True, time=(6, 6),
                               outTangentType=True) == ["flat"]

    def test_stepped_holds_the_pose_forward(self, scene):
        """Stepped is asymmetric on purpose: out steps, in is left alone."""
        from animkit.tools import keys

        cmds, node = scene
        keys.set_tangents(preset="stepped")
        assert cmds.keyTangent(node + ".tx", q=True, time=(6, 6),
                               outTangentType=True) == ["step"]

    def test_only_the_target_key_changes(self, scene):
        from animkit.tools import keys

        cmds, node = scene
        keys.set_tangents(preset="linear")
        assert cmds.keyTangent(node + ".tx", q=True, time=(1, 1),
                               outTangentType=True) != ["linear"]

    def test_unknown_preset_is_refused(self, scene):
        from animkit.tools import keys

        _cmds, _node = scene
        assert keys.set_tangents(preset="nonsense") == 0


class TestHold:
    def test_takes_the_previous_keys_value(self, scene):
        from animkit.tools import keys

        cmds, node = scene
        cmds.currentTime(6)
        keys.hold()
        values = key_values(cmds, node)
        assert values[1] == pytest.approx(values[0])

    def test_inserts_a_key_where_there_is_none(self, scene):
        from animkit.tools import keys

        cmds, node = scene
        cmds.currentTime(8)
        assert keys.hold() > 0
        assert key_count(cmds, node) == 4
        times = sorted(key_times(cmds, node))
        assert 8.0 in times

    def test_first_key_is_left_alone(self, scene):
        from animkit.core import layers
        from animkit.tools import keys

        cmds, node = scene
        curve_name = layers.resolve_curve(node + ".tx")
        cmds.selectKey(clear=True)
        cmds.selectKey(curve_name, time=(1, 1))
        before = key_values(cmds, node)
        keys.hold()
        cmds.selectKey(clear=True)
        assert key_values(cmds, node) == pytest.approx(before)


class TestCycle:
    def test_sets_both_infinities(self, scene):
        from animkit.tools import keys

        cmds, node = scene
        keys.set_cycle(mode="cycle")
        # setInfinity's query returns a LIST, not a string.
        assert cmds.setInfinity(node + ".tx", q=True, preInfinite=True) == ["cycle"]
        assert cmds.setInfinity(node + ".tx", q=True, postInfinite=True) == ["cycle"]

    def test_set_once_per_curve_not_once_per_key(self, scene):
        from animkit.core import layers
        from animkit.tools import keys

        cmds, node = scene
        curve_name = layers.resolve_curve(node + ".tx")
        cmds.selectKey(clear=True)
        cmds.selectKey(curve_name)
        assert keys.set_cycle(mode="cycle") == 1
        cmds.selectKey(clear=True)

    def test_unknown_mode_is_refused(self, scene):
        from animkit.tools import keys

        _cmds, _node = scene
        assert keys.set_cycle(mode="nonsense") == 0


class TestVisibilityIsReachable:
    """Pressing S keys visibility. Every keys operation has to reach it.

    Skipping it was a TWEEN policy -- you cannot interpolate visibility to 0.37
    -- applied by accident to every operation, because they all resolve targets
    through the same survey. The result was a control the animator had cleared
    that still showed as keyed, and an offset that moved the rotate keys a frame
    and left the visibility key behind. See selection.UNTWEENABLE.
    """

    @pytest.fixture
    def keyed_visibility(self, scene):
        cmds, node = scene
        for time in (1, 6, 11):
            cmds.setKeyframe(node + ".visibility", time=time, value=1)
        cmds.currentTime(6)
        cmds.select(node)
        cmds.selectKey(clear=True)
        return cmds, node

    def test_delete_clears_the_visibility_key(self, keyed_visibility):
        """The reported bug: transforms cleared, visibility left keyed."""
        from animkit.tools import keys

        cmds, node = keyed_visibility
        keys.delete()
        assert sorted(key_times(cmds, node, "visibility")) == [1.0, 11.0]

    def test_delete_can_clear_the_control_completely(self, keyed_visibility):
        """Clearing every key must leave NO curve anywhere on the control.

        This is the state the Channel Box actually reports. A control with one
        surviving visibility curve reads as animated, which is what "delete did
        not work" looked like.
        """
        from animkit.tools import keys

        cmds, node = keyed_visibility
        for time in (1, 6, 11):
            cmds.currentTime(time)
            keys.delete()

        assert not cmds.keyframe(node, q=True, keyframeCount=True)

    def test_offset_moves_visibility_with_everything_else(self, keyed_visibility):
        """The quieter half of the bug: channels drifting out of sync.

        Worse than delete leaving a key behind, because nothing looks wrong
        until the visibility switch fires a frame off the pose it belongs to.
        """
        from animkit.tools import keys

        cmds, node = keyed_visibility
        keys.offset(frames=1)
        assert 7.0 in key_times(cmds, node, "tx")
        assert 7.0 in key_times(cmds, node, "visibility")

    def test_tween_still_never_touches_visibility(self, keyed_visibility):
        """The regression guard. Tweening is what UNTWEENABLE exists for.

        If this ever fails, the two filters have been collapsed back into one
        and an animator's visibility switch is being interpolated.
        """
        from animkit.tools import tween

        cmds, node = keyed_visibility
        before = curve_state(cmds, node, "visibility")
        tween.tween_once(0.5)
        assert curve_state(cmds, node, "visibility") == before

    def test_survey_default_still_excludes_visibility(self, keyed_visibility):
        """The default is unchanged; only callers that opt in see it."""
        from animkit.core import selection

        _cmds, node = keyed_visibility
        plain = selection.plugs_from_selection([node])
        assert node + ".visibility" not in plain

        opted_in = selection.plugs_from_selection(
            [node], include_untweenable=True
        )
        assert node + ".visibility" in opted_in


class TestDelete:
    def test_removes_the_target_key(self, scene):
        from animkit.tools import keys

        cmds, node = scene
        keys.delete()
        assert sorted(key_times(cmds, node)) == [1.0, 11.0]

    def test_removes_every_selected_key(self, scene):
        """Descending order matters. Ascending deletes the wrong keys.

        Delete index 1 first and every later key renumbers, so the second
        delete removes a key the animator never selected -- and it looks like
        it worked, because a key did disappear.
        """
        from animkit.core import layers
        from animkit.tools import keys

        cmds, node = scene
        curve_name = layers.resolve_curve(node + ".tx")
        cmds.selectKey(clear=True)
        cmds.selectKey(curve_name, time=(1, 1))
        cmds.selectKey(curve_name, time=(6, 6), add=True)

        keys.delete()
        cmds.selectKey(clear=True)

        assert sorted(key_times(cmds, node)) == [11.0]

    def test_clearing_every_key_removes_the_curve_cleanly(self, scene):
        """Deleting the last key deletes the animCurve node with it.

        Anything that then tries to dgdirty by curve name is holding a dead
        node. It must not raise, and it must not print a traceback for an
        operation that worked.
        """
        from animkit.core import layers
        from animkit.tools import keys

        cmds, node = scene
        curve_name = layers.resolve_curve(node + ".tx")
        cmds.selectKey(clear=True)
        cmds.selectKey(curve_name)  # all three keys

        assert keys.delete() == 3
        cmds.selectKey(clear=True)

        assert key_count(cmds, node) == 0
        assert not cmds.objExists(curve_name)

    def test_undo_after_clearing_every_key_restores_the_curve(self, scene):
        from animkit.core import layers
        from animkit.tools import keys

        cmds, node = scene
        curve_name = layers.resolve_curve(node + ".tx")
        cmds.selectKey(clear=True)
        cmds.selectKey(curve_name)
        before = curve_state(cmds, node)

        keys.delete()
        cmds.undo()
        cmds.selectKey(clear=True)

        assert curve_state(cmds, node) == before


class TestRegistry:
    def test_every_operation_has_a_unique_name(self):
        from animkit.tools import keys

        names = [op.name for op in keys.OPERATIONS]
        assert len(names) == len(set(names))

    def test_every_operation_generates_an_importable_command(self):
        from animkit.tools import keys

        for op in keys.OPERATIONS:
            assert op.command.startswith("import animkit.tools.keys as k;")
            compile(op.command, "<op>", "exec")

    def test_commands_registry_covers_every_operation(self):
        from animkit import commands
        from animkit.tools import keys

        registered = {name for name, _a, _c in commands.COMMANDS}
        for op in keys.OPERATIONS:
            assert op.name in registered, "%s is not registered" % op.name

    def test_groups_cover_every_operation(self):
        """The panel lays out by group, so an operation in no group is an
        operation with no button."""
        from animkit.tools import keys

        for op in keys.OPERATIONS:
            assert op.group in keys.GROUPS, "%s has no group" % op.name

    def test_group_order_follows_the_registry(self):
        """Not alphabetical. Registry order is the intended panel order."""
        from animkit.tools import keys

        seen = []
        for op in keys.OPERATIONS:
            if op.group not in seen:
                seen.append(op.group)
        assert list(keys.GROUPS) == seen

    def test_every_cycle_mode_maps_to_a_real_enum_value(self):
        """The animCurve infinity enum skips 2: Constant=0, Linear=1, Cycle=3,
        Cycle with offset=4, Oscillate=5. A mapping that drifts back onto 2 is
        silently ignored by setAttr, and the operation reports success while
        changing nothing."""
        from animkit.tools import keys

        assert 2 not in keys.CYCLE_MODES.values()
        assert keys.CYCLE_MODES["cycle"] == 3
        assert set(keys.CYCLE_MODES.values()) == {0, 1, 3, 4, 5}

    def test_panels_import_from_a_cold_interpreter(self):
        """Maya restores docked panels by running a stored uiScript before
        anything else in the session. Both panel modules must therefore import
        with no Maya UI and no prior animkit state.

        Import only -- constructing a QWidget is impossible in headless mayapy
        (it terminates the interpreter), so panel LAYOUT has to be verified by
        opening them in Maya. This at least pins that the uiScript will not die
        on the import.
        """
        import importlib

        for name in ("animkit.ui.tween_ui", "animkit.ui.keys_ui"):
            module = importlib.import_module(name)
            assert callable(module.build)
            assert callable(module.show)
            assert module.BUILD_CODE
            compile(module.BUILD_CODE, "<uiScript>", "exec")
