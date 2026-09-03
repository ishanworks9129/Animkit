"""Tween session behaviour against real scenes. Needs mayapy.

The undo tests are the important ones. A tween that produces the right pose
but three undo entries -- or one that cannot be undone at all because it wrote
through the API -- will be reported as "it corrupts my scene", and they will
be right.
"""

import pytest

from conftest import requires_maya  # noqa: F401

pytestmark = requires_maya


@pytest.fixture
def scene(clean_scene):
    cmds = clean_scene
    node = cmds.polyCube(name="ctrl")[0]
    cmds.setKeyframe(node + ".tx", time=1, value=0)
    cmds.setKeyframe(node + ".tx", time=11, value=100)
    cmds.currentTime(6)
    cmds.select(node)
    return cmds, node


class TestSession:
    def test_begin_finds_the_selection(self, scene):
        """Plugs use LONG attribute names.

        cmds.listAttr returns translateX, not tx, so that is the form every
        plug string in a session takes. Pinned because the Channel Box reports
        short names and selection.py normalises them to match -- if that
        normalisation regresses, the same channel starts producing two
        different plug strings depending on how it was picked.
        """
        from animkit.tools import tween

        _cmds, node = scene
        s = tween.TweenSession()
        assert s.begin() is True
        assert any(e.plug == node + ".translateX" for e in s.entries)
        assert not any(e.plug == node + ".tx" for e in s.entries)

    def test_begin_returns_false_with_nothing_selected(self, scene):
        from animkit.tools import tween

        cmds, _node = scene
        cmds.select(clear=True)
        assert tween.TweenSession().begin() is False

    def test_commit_moves_toward_the_next_key(self, scene):
        from animkit.tools import tween

        cmds, node = scene
        before = cmds.getAttr(node + ".tx")
        s = tween.TweenSession()
        s.begin()
        s.commit(0.5)
        after = cmds.getAttr(node + ".tx")
        assert after > before
        assert after < 100.0

    def test_cancel_leaves_the_scene_untouched(self, scene):
        from animkit.tools import tween

        cmds, node = scene
        before = cmds.getAttr(node + ".tx")
        keys_before = cmds.keyframe(node + ".tx", q=True, keyframeCount=True)

        s = tween.TweenSession()
        s.begin()
        s.update(0.8)
        s.cancel()

        assert cmds.getAttr(node + ".tx") == pytest.approx(before)
        assert cmds.keyframe(node + ".tx", q=True, keyframeCount=True) == keys_before

    def test_drag_does_not_compound(self, scene):
        """Ten updates at 0.5 must equal one update at 0.5."""
        from animkit.tools import tween

        cmds, node = scene

        s = tween.TweenSession()
        s.begin()
        s.update(0.5)
        single = cmds.getAttr(node + ".tx")
        for _ in range(10):
            s.update(0.5)
        repeated = cmds.getAttr(node + ".tx")
        s.cancel()

        assert repeated == pytest.approx(single)


class TestUndo:
    def test_commit_is_exactly_one_undo_step(self, scene):
        from animkit.tools import tween

        cmds, node = scene
        before = cmds.getAttr(node + ".tx")

        s = tween.TweenSession()
        s.begin()
        s.update(0.7)
        s.commit(0.7)
        changed = cmds.getAttr(node + ".tx")
        assert changed != pytest.approx(before)

        cmds.undo()
        assert cmds.getAttr(node + ".tx") == pytest.approx(before)

    def test_undo_survives_a_drag_that_created_a_key(self, scene):
        """Current time sits between keys, so the tween inserts one."""
        from animkit.tools import tween

        cmds, node = scene
        cmds.currentTime(6)
        keys_before = cmds.keyframe(node + ".tx", q=True, keyframeCount=True)

        s = tween.TweenSession()
        s.begin()
        s.update(0.4)
        s.commit(0.4)
        assert cmds.keyframe(node + ".tx", q=True, keyframeCount=True) == keys_before + 1

        cmds.undo()
        assert cmds.keyframe(node + ".tx", q=True, keyframeCount=True) == keys_before

    def test_redo_reapplies(self, scene):
        from animkit.tools import tween

        cmds, node = scene
        s = tween.TweenSession()
        s.begin()
        s.commit(0.5)
        applied = cmds.getAttr(node + ".tx")

        cmds.undo()
        cmds.redo()
        assert cmds.getAttr(node + ".tx") == pytest.approx(applied)

    def test_zero_commit_writes_nothing(self, scene):
        from animkit.tools import tween

        cmds, node = scene
        keys_before = cmds.keyframe(node + ".tx", q=True, keyframeCount=True)
        before = cmds.getAttr(node + ".tx")

        s = tween.TweenSession()
        s.begin()
        s.commit(0.0)

        assert cmds.getAttr(node + ".tx") == pytest.approx(before)
        assert cmds.keyframe(node + ".tx", q=True, keyframeCount=True) == keys_before


class TestBoundaries:
    def test_last_key_has_no_forward_travel(self, scene):
        from animkit.tools import tween

        cmds, node = scene
        cmds.currentTime(11)
        before = cmds.getAttr(node + ".tx")

        s = tween.TweenSession()
        s.begin()
        s.commit(1.0)
        assert cmds.getAttr(node + ".tx") == pytest.approx(before)

    def test_static_channel_is_skipped_not_keyed(self, scene):
        from animkit.tools import tween

        cmds, node = scene
        s = tween.TweenSession()
        s.begin()
        s.commit(0.5)
        # .ty was never animated; the tween must not have keyed it.
        assert not cmds.keyframe(node + ".ty", q=True, keyframeCount=True)


class TestLayerPolicy:
    def test_writes_land_on_the_selected_layer(self, scene):
        from animkit.core import curves, layers
        from animkit.tools import tween

        cmds, node = scene
        cmds.select(node)
        layer = cmds.animLayer("Layer1", addSelectedObjects=True)
        cmds.animLayer(layer, edit=True, selected=True)
        cmds.setKeyframe(node + ".tx", time=1, value=0, animLayer=layer)
        cmds.setKeyframe(node + ".tx", time=11, value=20, animLayer=layer)
        cmds.currentTime(6)
        cmds.select(node)

        base_curve = layers.resolve_curve(node + ".tx", layers.root_layer())
        base_before = curves.open_curve(base_curve).evaluate(6)

        s = tween.TweenSession()
        s.begin()
        s.commit(0.6)

        base_after = curves.open_curve(base_curve).evaluate(6)
        assert base_after == pytest.approx(base_before), "base layer was modified"

    def test_locked_layer_is_skipped(self, scene):
        from animkit.tools import tween

        cmds, node = scene
        cmds.select(node)
        layer = cmds.animLayer("Locked1", addSelectedObjects=True)
        cmds.animLayer(layer, edit=True, selected=True, lock=True)

        s = tween.TweenSession()
        began = s.begin()
        if began:
            s.cancel()
        # Either nothing to tween, or nothing on the locked layer was collected.
        assert all(e.layer != layer for e in s.entries)


class TestSelectedKeyMode:
    """Graph Editor key selection takes priority over current time."""

    @pytest.fixture
    def curve(self, scene):
        from animkit.core import layers

        cmds, node = scene
        cmds.setKeyframe(node + ".tx", time=6, value=50)
        cmds.selectKey(clear=True)
        return cmds, node, layers.resolve_curve(node + ".tx")

    def test_selected_keys_are_discovered(self, curve):
        from animkit.core import selection

        cmds, _node, curve_name = curve
        cmds.selectKey(curve_name, time=(6, 6))
        found = selection.selected_keys()
        assert curve_name in found
        assert found[curve_name] == [1]  # keys at 1, 6, 11 -> index 1

    def test_no_selection_means_empty(self, curve):
        from animkit.core import selection

        cmds, _node, _curve_name = curve
        cmds.selectKey(clear=True)
        assert selection.selected_keys() == {}

    def test_session_reports_key_target(self, curve):
        from animkit.tools import tween

        cmds, _node, curve_name = curve
        cmds.selectKey(curve_name, time=(6, 6))
        s = tween.TweenSession()
        assert s.begin() is True
        assert s.target == tween.TweenSession.TARGET_KEYS
        s.cancel()

    def test_works_away_from_current_time(self, curve):
        """The whole point: the key need not be under the time cursor."""
        from animkit.tools import tween

        cmds, node, curve_name = curve
        cmds.currentTime(1)
        cmds.selectKey(curve_name, time=(6, 6))

        before = cmds.keyframe(node + ".tx", q=True, time=(6, 6),
                               valueChange=True)[0]
        tween.tween_once(0.5)
        after = cmds.keyframe(node + ".tx", q=True, time=(6, 6),
                              valueChange=True)[0]
        assert after > before

    def test_neighbours_are_untouched(self, curve):
        from animkit.tools import tween

        cmds, node, curve_name = curve
        cmds.selectKey(curve_name, time=(6, 6))

        def kv(t):
            return cmds.keyframe(node + ".tx", q=True, time=(t, t),
                                 valueChange=True)[0]

        first, last = kv(1), kv(11)
        tween.tween_once(0.5)
        assert kv(1) == pytest.approx(first)
        assert kv(11) == pytest.approx(last)

    def test_inserts_no_keys(self, curve):
        """Selected-key mode must never change curve topology."""
        from animkit.tools import tween

        cmds, node, curve_name = curve
        cmds.currentTime(3)  # no key here; time mode would insert one
        cmds.selectKey(curve_name, time=(6, 6))

        before = cmds.keyframe(node + ".tx", q=True, keyframeCount=True)
        tween.tween_once(0.5)
        assert cmds.keyframe(node + ".tx", q=True, keyframeCount=True) == before

    def test_multiple_keys_each_use_own_neighbours(self, curve):
        from animkit.tools import tween

        cmds, node, curve_name = curve
        cmds.setKeyframe(node + ".tx", time=8, value=60)
        cmds.selectKey(clear=True)
        cmds.selectKey(curve_name, time=(6, 6))
        cmds.selectKey(curve_name, time=(8, 8), add=True)

        def kv(t):
            return cmds.keyframe(node + ".tx", q=True, time=(t, t),
                                 valueChange=True)[0]

        six, eight = kv(6), kv(8)
        tween.tween_once(0.5)
        # Both moved, and neither landed on the other's target.
        assert kv(6) != pytest.approx(six)
        assert kv(8) != pytest.approx(eight)

    def test_undo_is_one_step(self, curve):
        from animkit.tools import tween

        cmds, node, curve_name = curve
        cmds.selectKey(curve_name, time=(6, 6))

        def kv():
            return cmds.keyframe(node + ".tx", q=True, time=(6, 6),
                                 valueChange=True)[0]

        before = kv()
        tween.tween_once(0.5)
        assert kv() != pytest.approx(before)
        cmds.undo()
        assert kv() == pytest.approx(before)
