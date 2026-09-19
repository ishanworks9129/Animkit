"""Bake-to-Ns. Needs mayapy.

The shared harness in test_keys_maya.py already covers every bake operation for
one-undo-step, layer policy, locked layers and the empty cases, because they are
in `keys.OPERATIONS` like everything else. What is here is the behaviour that is
specific to resampling and that no generic harness could know to check.

The invariant that matters is NOT "the curve is unchanged" -- a resample changes
the curve, that is its whole job. It is:

    at every frame the bake KEEPS, the animation is worth what it was worth
    before the bake.

Everything else about a bake is a consequence of that plus the frame set.

ASSERT ON CURVE STATE, NEVER ON getAttr -- house rule 2, and doubly so here,
because a bake is a topology change and getAttr goes stale across one.
"""

import pytest

from conftest import requires_maya  # noqa: F401

pytestmark = requires_maya


# --- fixtures ---------------------------------------------------------------


@pytest.fixture
def dense(clean_scene):
    """One control keyed on EVERY frame from 1 to 11, selected, cursor on 6.

    Keyed on every frame on purpose: it is the only fixture where "bake on twos
    halves the key count" is a statement about the operation rather than about
    where the fixture happened to put its keys.
    """
    cmds = clean_scene
    node = cmds.createNode("transform", name="ctrl")
    for frame in range(1, 12):
        cmds.setKeyframe(node + ".tx", time=frame, value=float(frame) ** 2)
    cmds.currentTime(6)
    cmds.select(node)
    cmds.selectKey(clear=True)
    return cmds, node


def times(cmds, node, attr="tx"):
    return cmds.keyframe(node + "." + attr, q=True, timeChange=True) or []


def values(cmds, node, attr="tx"):
    return cmds.keyframe(node + "." + attr, q=True, valueChange=True) or []


def value_at(cmds, node, frame, attr="tx"):
    """What the CURVE is worth at a frame. Not getAttr -- see the docstring."""
    from animkit.core import layers

    curve = layers.resolve_curve(node + "." + attr)
    found = cmds.keyframe(curve, q=True, eval=True, time=(frame, frame))
    return found[0] if found else None


# --- the invariant ----------------------------------------------------------


class TestPreservesTheFramesItKeeps:
    def test_values_at_sampled_frames_are_unchanged(self, dense):
        """The one assertion the whole feature exists to satisfy."""
        from animkit.tools import keys

        cmds, node = dense
        before = dict((f, value_at(cmds, node, f)) for f in range(1, 12))

        assert keys.bake_every(step=2) > 0

        for frame in (1, 3, 5, 7, 9, 11):
            assert value_at(cmds, node, frame) == pytest.approx(
                before[frame], abs=1e-9
            ), "frame %d moved" % frame

    def test_keeps_the_first_and_last_frame(self, dense):
        """Dropping the end key would shorten the shot, silently."""
        from animkit.tools import keys

        cmds, node = dense
        keys.bake_every(step=4)
        found = times(cmds, node)
        assert min(found) == 1.0
        assert max(found) == 11.0

    def test_end_frame_survives_an_uneven_step(self, dense):
        """1..11 on fours lands on 1, 5, 9 -- and then 11, off the grid.

        The last interval is shorter than the step. That is deliberate and it
        is what keeps the end pose where the animator put it.
        """
        from animkit.tools import keys

        cmds, node = dense
        keys.bake_every(step=4)
        assert times(cmds, node) == [1.0, 5.0, 9.0, 11.0]


class TestKeyCount:
    def test_bake_on_twos_halves_a_dense_curve(self, dense):
        from animkit.tools import keys

        cmds, node = dense
        assert len(times(cmds, node)) == 11

        keys.bake_every(step=2)
        assert times(cmds, node) == [1.0, 3.0, 5.0, 7.0, 9.0, 11.0]

    def test_bake_on_ones_keeps_every_frame(self, dense):
        from animkit.tools import keys

        cmds, node = dense
        keys.bake_every(step=1)
        assert times(cmds, node) == [float(f) for f in range(1, 12)]

    def test_a_sparse_curve_gains_keys_on_ones(self, clean_scene):
        """Baking is a resample, not a reduction: on ones it densifies."""
        from animkit.tools import keys

        cmds = clean_scene
        node = cmds.createNode("transform", name="sparse")
        cmds.setKeyframe(node + ".tx", time=1, value=0)
        cmds.setKeyframe(node + ".tx", time=11, value=100)
        cmds.currentTime(1)
        cmds.select(node)
        cmds.selectKey(clear=True)

        keys.bake_every(step=1)
        assert times(cmds, node) == [float(f) for f in range(1, 12)]


class TestIdempotent:
    def test_baking_twice_at_the_same_step_changes_nothing(self, dense):
        """The second bake samples frames that are already keys.

        If it were not idempotent, a double-click on the button would quietly
        degrade the curve -- and a bake that drifts is a bake nobody can trust
        to be safe to repeat.
        """
        from animkit.tools import keys

        cmds, node = dense
        keys.bake_every(step=3)
        once = (tuple(times(cmds, node)), tuple(values(cmds, node)))

        keys.bake_every(step=3)
        twice = (tuple(times(cmds, node)), tuple(values(cmds, node)))

        assert twice == once


class TestRefusesAndSurvives:
    def test_step_zero_is_refused_and_writes_nothing(self, dense):
        from animkit.tools import keys

        cmds, node = dense
        before = (tuple(times(cmds, node)), tuple(values(cmds, node)))
        assert keys.bake_every(step=0) == 0
        assert (tuple(times(cmds, node)), tuple(values(cmds, node))) == before

    def test_negative_step_is_refused(self, dense):
        from animkit.tools import keys

        cmds, node = dense
        assert keys.bake_every(step=-2) == 0

    def test_single_key_curve_is_left_alone(self, clean_scene):
        """One key has no range. Nothing to resample, and nothing may raise."""
        from animkit.tools import keys

        cmds = clean_scene
        node = cmds.createNode("transform", name="lonely")
        cmds.setKeyframe(node + ".tx", time=6, value=5)
        cmds.currentTime(6)
        cmds.select(node)
        cmds.selectKey(clear=True)

        assert keys.bake_every(step=2) == 0
        assert times(cmds, node) == [6.0]


class TestSelectedKeyMode:
    def test_bakes_only_the_selected_span(self, clean_scene):
        """Keys outside the selected extent are not touched.

        This is the mode where a bake could quietly eat the rest of the shot,
        so the assertion is on what is left OUTSIDE the span.
        """
        from animkit.core import layers
        from animkit.tools import keys

        cmds = clean_scene
        node = cmds.createNode("transform", name="ctrl")
        for frame in range(1, 22):
            cmds.setKeyframe(node + ".tx", time=frame, value=float(frame))
        curve = layers.resolve_curve(node + ".tx")

        cmds.select(node)
        cmds.selectKey(clear=True)
        # Frames 5..9 are indices 4..8.
        cmds.selectKey(curve, index=(4, 8))

        keys.bake_every(step=2)
        found = times(cmds, node)

        # Outside the span, every original frame is still there.
        for frame in list(range(1, 5)) + list(range(10, 22)):
            assert float(frame) in found, "frame %d outside the span was lost" % frame
        # Inside it, only the sampled ones.
        assert [f for f in found if 5.0 <= f <= 9.0] == [5.0, 7.0, 9.0]


class TestMultipleChannels:
    def test_every_animated_channel_is_baked(self, clean_scene):
        from animkit.tools import keys

        cmds = clean_scene
        node = cmds.createNode("transform", name="ctrl")
        for frame in range(1, 12):
            cmds.setKeyframe(node + ".tx", time=frame, value=float(frame))
            cmds.setKeyframe(node + ".ry", time=frame, value=float(frame) * 3)
        cmds.currentTime(6)
        cmds.select(node)
        cmds.selectKey(clear=True)

        keys.bake_every(step=5)

        assert times(cmds, node, "tx") == [1.0, 6.0, 11.0]
        assert times(cmds, node, "ry") == [1.0, 6.0, 11.0]

    def test_rotation_values_survive_the_unit_round_trip(self, clean_scene):
        """Rotation curves are stored in radians and reported in degrees.

        A bake that read one and wrote the other would be out by a factor of
        57.3 and would look like a rig explosion, so this pins the round trip.
        """
        from animkit.tools import keys

        cmds = clean_scene
        node = cmds.createNode("transform", name="ctrl")
        for frame, value in ((1, 0.0), (6, 45.0), (11, 90.0)):
            cmds.setKeyframe(node + ".ry", time=frame, value=value)
        cmds.currentTime(6)
        cmds.select(node)
        cmds.selectKey(clear=True)

        keys.bake_every(step=5)
        assert values(cmds, node, "ry") == pytest.approx([0.0, 45.0, 90.0])


class TestRegistry:
    def test_one_operation_per_bake_step(self):
        from animkit.tools import keys

        generated = [op for op in keys.OPERATIONS
                     if op.name.startswith("animkitKeysBake")]
        assert len(generated) == len(keys.BAKE_STEPS)

    def test_each_carries_its_own_step(self):
        from animkit.tools import keys

        for step in keys.BAKE_STEPS:
            op = keys.BY_NAME["animkitKeysBake%d" % step]
            assert op.kwargs == {"step": step}

    def test_the_generated_command_calls_bake_every(self):
        """The command string is what a hotkey actually runs."""
        from animkit.tools import keys

        op = keys.BY_NAME["animkitKeysBake2"]
        assert "bake_every" in op.command
        assert "step=2" in op.command
        assert "animkit.tools.keys" in op.command


class TestRangeIsClampedToPlayback:
    """The bug a real shot found: 81 keys became 2,529.

    A prop whose timeline read 0-120 carried curves running to frame 560.
    Baking on twos over the CURVE's extent produced 281 keys a channel across
    nine channels, most of them past the end of the range the animator could
    see -- from the operation with a reputation for thinning keys out.
    """

    def test_curve_beyond_the_playback_range_is_clamped(self, clean_scene):
        cmds = clean_scene
        from animkit.tools import keys

        node = cmds.createNode("transform", name="prop")
        for frame in (0, 140, 280, 420, 560):
            cmds.setKeyframe(node + ".tx", time=frame, value=float(frame))
        cmds.playbackOptions(edit=True, min=0, max=120)
        cmds.currentTime(0)
        cmds.select(node)
        cmds.selectKey(clear=True)

        keys.bake_every(step=2)
        times = cmds.keyframe(node + ".tx", q=True, timeChange=True) or []

        assert max(t for t in times if t <= 120) == 120.0
        assert [t for t in times if 0 < t < 120 and t % 2] == []
        # Everything past the range is untouched -- still exactly where it was.
        assert [t for t in times if t > 120] == [140.0, 280.0, 420.0, 560.0]

    def test_it_does_not_explode_the_key_count(self, clean_scene):
        """The regression in one number: 5 keys over 560 frames must not
        become hundreds because the timeline only covers 120."""
        cmds = clean_scene
        from animkit.tools import keys

        node = cmds.createNode("transform", name="prop")
        for frame in (0, 140, 280, 420, 560):
            cmds.setKeyframe(node + ".tx", time=frame, value=float(frame))
        cmds.playbackOptions(edit=True, min=0, max=120)
        cmds.currentTime(0)
        cmds.select(node)
        cmds.selectKey(clear=True)

        keys.bake_every(step=2)
        count = len(cmds.keyframe(node + ".tx", q=True, timeChange=True) or [])
        assert count < 70, "baked %d keys over a 120-frame range" % count

    def test_selected_keys_still_win_over_the_playback_range(self, clean_scene):
        """Explicit intent beats the timeline. If the animator picked keys
        outside the range, those are the ones they meant."""
        cmds = clean_scene
        from animkit.core import layers
        from animkit.tools import keys

        node = cmds.createNode("transform", name="prop")
        for frame in range(200, 221):
            cmds.setKeyframe(node + ".tx", time=frame, value=float(frame))
        cmds.playbackOptions(edit=True, min=0, max=120)
        curve = layers.resolve_curve(node + ".tx")
        cmds.select(node)
        cmds.selectKey(clear=True)
        cmds.selectKey(curve)

        assert keys.bake_every(step=5) > 0
        times = cmds.keyframe(node + ".tx", q=True, timeChange=True) or []
        assert min(times) == 200.0 and max(times) == 220.0
