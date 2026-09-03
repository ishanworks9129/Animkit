"""Target resolution -- the contract every operation depends on. Needs mayapy.

This is the module the tween and every keyframe operation share, so a change
here changes all of them at once. The things worth pinning are its promises:

  * a Graph Editor selection wins over the current time
  * selected-key mode resolves NO layer and inserts NO keys
  * insert=False never creates a key, whatever the caller does
  * before_write fires exactly once, and only when something is written
  * nothing is left cached when it returns
"""

import pytest

from conftest import requires_maya  # noqa: F401

pytestmark = requires_maya


@pytest.fixture
def scene(clean_scene):
    cmds = clean_scene
    node = cmds.createNode("transform", name="ctrl")
    cmds.setKeyframe(node + ".tx", time=1, value=0)
    cmds.setKeyframe(node + ".tx", time=11, value=100)
    cmds.currentTime(6)
    cmds.select(node)
    cmds.selectKey(clear=True)
    return cmds, node


class TestModeSelection:
    def test_current_time_when_no_keys_selected(self, scene):
        from animkit.core import targets

        _cmds, _node = scene
        resolved = targets.resolve_targets(insert=True)
        assert resolved.mode == targets.TARGET_TIME

    def test_selected_keys_win(self, scene):
        from animkit.core import layers, targets

        cmds, node = scene
        curve = layers.resolve_curve(node + ".tx")
        cmds.selectKey(curve, time=(11, 11))
        resolved = targets.resolve_targets()
        cmds.selectKey(clear=True)

        assert resolved.mode == targets.TARGET_KEYS
        assert [e.time for e in resolved.entries] == [11.0]

    def test_explicit_plugs_ignore_the_key_selection(self, scene):
        """An explicit plug list is an explicit request, not a suggestion."""
        from animkit.core import layers, targets

        cmds, node = scene
        curve = layers.resolve_curve(node + ".tx")
        cmds.selectKey(curve, time=(11, 11))
        resolved = targets.resolve_targets(plugs=[node + ".translateX"], insert=True)
        cmds.selectKey(clear=True)

        assert resolved.mode == targets.TARGET_TIME

    def test_nothing_selected_is_empty_not_an_error(self, scene):
        from animkit.core import targets

        cmds, _node = scene
        cmds.select(clear=True)
        resolved = targets.resolve_targets(insert=True)
        assert len(resolved) == 0
        assert not resolved


class TestInsertion:
    def test_insert_true_creates_the_missing_key(self, scene):
        from animkit.core import targets

        cmds, node = scene
        before = cmds.keyframe(node + ".tx", q=True, keyframeCount=True)
        resolved = targets.resolve_targets(insert=True)
        assert cmds.keyframe(node + ".tx", q=True, keyframeCount=True) == before + 1
        assert any(e.created for e in resolved.entries)

    def test_insert_false_creates_nothing(self, scene):
        """The default, and the right default: an operation that keys a channel
        the animator did not ask about is an operation they have to undo."""
        from animkit.core import targets

        cmds, node = scene
        before = cmds.keyframe(node + ".tx", q=True, keyframeCount=True)
        resolved = targets.resolve_targets(insert=False)
        assert cmds.keyframe(node + ".tx", q=True, keyframeCount=True) == before
        assert len(resolved) == 0

    def test_insert_false_still_finds_an_existing_key(self, scene):
        from animkit.core import targets

        cmds, node = scene
        cmds.currentTime(1)  # there IS a key here
        resolved = targets.resolve_targets(insert=False)
        assert len(resolved) == 1
        assert not resolved.entries[0].created

    def test_selected_key_mode_never_inserts(self, scene):
        """Even with insert=True. The animator picked existing keys."""
        from animkit.core import layers, targets

        cmds, node = scene
        curve = layers.resolve_curve(node + ".tx")
        cmds.currentTime(6)  # no key here
        cmds.selectKey(curve, time=(11, 11))
        before = cmds.keyframe(node + ".tx", q=True, keyframeCount=True)

        targets.resolve_targets(insert=True)
        cmds.selectKey(clear=True)

        assert cmds.keyframe(node + ".tx", q=True, keyframeCount=True) == before

    def test_static_channel_is_never_keyed(self, scene):
        from animkit.core import targets

        cmds, node = scene
        targets.resolve_targets(insert=True)
        assert not cmds.keyframe(node + ".ty", q=True, keyframeCount=True)


class TestBeforeWrite:
    def test_fires_once_when_inserting(self, scene):
        from animkit.core import targets

        _cmds, _node = scene
        calls = []
        targets.resolve_targets(insert=True, before_write=lambda: calls.append(1))
        assert len(calls) == 1

    def test_does_not_fire_when_nothing_is_written(self, scene):
        """The whole reason it exists: a read-only resolution must not cause a
        caller to open an undo chunk it will have nothing to put in."""
        from animkit.core import targets

        cmds, _node = scene
        cmds.currentTime(1)  # key already here, nothing to insert
        calls = []
        targets.resolve_targets(insert=True, before_write=lambda: calls.append(1))
        assert calls == []

    def test_does_not_fire_in_selected_key_mode(self, scene):
        from animkit.core import layers, targets

        cmds, node = scene
        curve = layers.resolve_curve(node + ".tx")
        cmds.selectKey(curve, time=(11, 11))
        calls = []
        targets.resolve_targets(insert=True, before_write=lambda: calls.append(1))
        cmds.selectKey(clear=True)
        assert calls == []

    def test_does_not_fire_with_nothing_selected(self, scene):
        from animkit.core import targets

        cmds, _node = scene
        cmds.select(clear=True)
        calls = []
        targets.resolve_targets(insert=True, before_write=lambda: calls.append(1))
        assert calls == []


class TestLayerPolicy:
    def test_entries_carry_the_active_layer(self, scene):
        from animkit.core import targets

        cmds, node = scene
        cmds.select(node)
        layer = cmds.animLayer("L1", addSelectedObjects=True)
        cmds.animLayer(layer, edit=True, selected=True)
        cmds.setKeyframe(node + ".tx", time=1, value=0, animLayer=layer)
        cmds.setKeyframe(node + ".tx", time=11, value=5, animLayer=layer)
        cmds.select(node)

        resolved = targets.resolve_targets(insert=True)
        assert resolved.entries
        assert all(e.layer == layer for e in resolved.entries)

    def test_locked_layer_yields_no_entries(self, scene):
        from animkit.core import targets

        cmds, node = scene
        cmds.select(node)
        layer = cmds.animLayer("Locked1", addSelectedObjects=True)
        cmds.animLayer(layer, edit=True, selected=True, lock=True)
        cmds.select(node)

        resolved = targets.resolve_targets(insert=True)
        assert all(e.layer != layer for e in resolved.entries)

    def test_selected_key_mode_resolves_no_layer(self, scene):
        """By design: the curve the animator selected keys on IS the target, so
        there is no layer question to answer and no way to answer it wrong."""
        from animkit.core import layers, targets

        cmds, node = scene
        cmds.select(node)
        layer = cmds.animLayer("L1", addSelectedObjects=True)
        cmds.animLayer(layer, edit=True, selected=True)
        cmds.setKeyframe(node + ".tx", time=1, value=0, animLayer=layer)
        cmds.setKeyframe(node + ".tx", time=11, value=5, animLayer=layer)

        curve = layers.resolve_curve(node + ".tx", layer)
        cmds.selectKey(curve, time=(11, 11))
        resolved = targets.resolve_targets()
        cmds.selectKey(clear=True)

        assert resolved.mode == targets.TARGET_KEYS
        assert all(e.layer is None for e in resolved.entries)
        assert all(e.curve_name == curve for e in resolved.entries)


class TestContainer:
    def test_by_curve_descending_for_topology_edits(self, scene):
        from animkit.core import layers, targets

        cmds, node = scene
        cmds.setKeyframe(node + ".tx", time=6, value=50)
        curve = layers.resolve_curve(node + ".tx")
        cmds.selectKey(clear=True)
        cmds.selectKey(curve)

        resolved = targets.resolve_targets()
        cmds.selectKey(clear=True)

        grouped = resolved.by_curve(descending=True)
        assert grouped[curve] == sorted(grouped[curve], reverse=True)

    def test_curve_names_are_deduplicated(self, scene):
        from animkit.core import layers, targets

        cmds, node = scene
        cmds.setKeyframe(node + ".tx", time=6, value=50)
        curve = layers.resolve_curve(node + ".tx")
        cmds.selectKey(clear=True)
        cmds.selectKey(curve)

        resolved = targets.resolve_targets()
        cmds.selectKey(clear=True)

        assert len(resolved.entries) == 3
        assert resolved.curve_names == [curve]


class TestCacheHygiene:
    def test_no_scope_is_left_open(self, scene):
        from animkit.core import cache, targets

        _cmds, _node = scene
        targets.resolve_targets(insert=True)
        assert not cache.active()
        assert cache.stats()["stores"] == {}
