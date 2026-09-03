"""Selection surveying, and the cache that makes it fast. Needs mayapy.

The point of this file is one property: the OpenMaya survey in
`selection._survey_node` must agree with the cmds implementation in
`selection._is_tweenable`, channel for channel, on the node types that
actually differ. A cube agrees with everything -- constrained, driven, locked
and layered controls are where an attribute survey goes wrong, so those are
the fixtures.

If you ever make the survey faster again, this is the file that says whether
you also made it wrong.
"""

import pytest

from conftest import requires_maya  # noqa: F401

pytestmark = requires_maya


def _oracle(cmds, node):
    """What plugs_from_selection is contractually required to return.

    Deliberately the slow, obvious implementation, built from the same
    _is_tweenable the module still ships. If this and the fast path disagree,
    the fast path is wrong -- not this.
    """
    from animkit.core import selection

    out = []
    for attr in cmds.listAttr(node, keyable=True, unlocked=True) or []:
        if not cmds.attributeQuery(attr, node=node, exists=True):
            continue
        if selection._is_tweenable(node, attr):
            out.append("{0}.{1}".format(node, attr))
    return sorted(out)


@pytest.fixture
def rig(clean_scene):
    """One control of each kind that makes an attribute survey disagree."""
    cmds = clean_scene

    # A channel the rigger took out of the animator's reach: unlocked, still
    # settable, visible in the Channel Box, NOT keyable. Face boards are rigged
    # this way as a matter of course.
    #
    # This case was absent, and its absence let the fast path disagree with the
    # oracle for as long as both have existed: MFnAttribute.keyable is the
    # attribute's definition and never goes False, while `listAttr -keyable`
    # reads the plug. Adding the fixture is what turned that into a failure
    # somebody could see.
    hidden = cmds.createNode("transform", name="hiddenChannels")
    cmds.setAttr(hidden + ".rotateZ", 15.0)
    cmds.setAttr(hidden + ".rotateZ", keyable=False, channelBox=True)
    cmds.setAttr(hidden + ".scaleX", 2.0)
    cmds.setAttr(hidden + ".scaleX", keyable=False, channelBox=False)

    plain = cmds.createNode("transform", name="plain")
    cmds.addAttr(plain, longName="ikFkBlend", attributeType="double", keyable=True)
    cmds.setKeyframe(plain + ".tx", time=1, value=0)
    cmds.setKeyframe(plain + ".tx", time=11, value=10)

    locked = cmds.createNode("transform", name="locked")
    cmds.setAttr(locked + ".translateY", lock=True)
    cmds.setAttr(locked + ".scaleX", lock=True)

    driven = cmds.createNode("transform", name="driven")
    source = cmds.createNode("transform", name="source")
    cmds.connectAttr(source + ".ty", driven + ".rotateY")

    constrained = cmds.createNode("transform", name="constrained")
    cmds.pointConstraint(source, constrained)

    layered = cmds.createNode("transform", name="layered")
    cmds.setKeyframe(layered + ".tx", time=1, value=0)
    cmds.select(layered)
    layer = cmds.animLayer("L1", addSelectedObjects=True)
    cmds.setKeyframe(layered + ".tx", time=1, value=3, animLayer=layer)

    return cmds, {
        "hidden": hidden,
        "plain": plain,
        "locked": locked,
        "driven": driven,
        "constrained": constrained,
        "layered": layered,
        "source": source,
    }


#: Every control in the `rig` fixture. Hand-writing this list is how the
#: non-keyable case sat in the fixture without ever being run -- so it is
#: derived from the fixture now, and a control added there is covered by the
#: equivalence test the moment it exists.
KINDS = ["plain", "locked", "driven", "constrained", "layered", "hidden",
         "source"]


class TestSurveyMatchesOracle:
    def test_every_fixture_control_is_covered(self, rig):
        """The list above must not drift from the fixture.

        A case present in the fixture and absent from the parametrize list is
        worse than no case at all: it looks like coverage in the source and
        runs nothing.
        """
        _cmds, nodes = rig
        assert sorted(KINDS) == sorted(nodes)

    @pytest.mark.parametrize("kind", KINDS)
    def test_fast_path_agrees_with_cmds(self, rig, kind):
        from animkit.core import selection

        cmds, nodes = rig
        node = nodes[kind]
        assert sorted(selection.plugs_from_selection([node])) == _oracle(cmds, node)

    def test_a_non_keyable_channel_is_excluded(self, rig):
        """`setAttr -keyable false` takes a channel out of the animator's reach.

        The fast path read `MFnAttribute.keyable` -- the ATTRIBUTE's definition,
        which is True for translateX on every transform ever made and never goes
        False -- instead of `MPlug.isKeyable`, which is what that setAttr sets.
        So every channel a rigger had hidden still came back writable.

        It matters most where riggers hide the most: on a facial control board,
        where a reset with include_unkeyed then zeroed channels that were not
        even visible in the Channel Box.
        """
        from animkit.core import selection

        _cmds, nodes = rig
        node = nodes["hidden"]
        plugs = selection.plugs_from_selection([node])

        assert node + ".rotateZ" not in plugs, "channelBox-visible but not keyable"
        assert node + ".scaleX" not in plugs, "hidden and not keyable"
        # ...and the ones the rigger left alone still come through.
        assert node + ".translateX" in plugs
        assert node + ".rotateX" in plugs

    def test_a_non_keyable_channel_is_not_settable_transform_either(self, rig):
        """The same fix, seen from the pose side.

        settable_transform_channels feeds reset, mirror and the pairing channel
        comparison, and all three read the survey -- so this is where the fix
        actually stops a face board being flattened.
        """
        from animkit.core import xform

        _cmds, nodes = rig
        channels = xform.settable_transform_channels(nodes["hidden"])

        assert "rotateZ" not in channels
        assert "scaleX" not in channels
        assert "translateX" in channels

    def test_locked_channels_are_excluded(self, rig):
        from animkit.core import selection

        _cmds, nodes = rig
        plugs = selection.plugs_from_selection([nodes["locked"]])
        assert nodes["locked"] + ".translateY" not in plugs
        assert nodes["locked"] + ".scaleX" not in plugs
        assert nodes["locked"] + ".translateX" in plugs

    def test_connection_driven_channel_is_excluded(self, rig):
        """The case cmds.listAttr(settable=True) gets WRONG.

        listAttr happily returns a channel that has an incoming connection.
        Both getAttr(settable=True) and MPlug.isFreeToChange() refuse it. If
        the survey is ever "simplified" to a single listAttr call, this is the
        test that fails.
        """
        from animkit.core import selection

        cmds, nodes = rig
        assert cmds.listAttr(
            nodes["driven"], keyable=True, unlocked=True, settable=True
        ).count("rotateY") == 1
        plugs = selection.plugs_from_selection([nodes["driven"]])
        assert nodes["driven"] + ".rotateY" not in plugs

    def test_visibility_is_never_offered(self, rig):
        from animkit.core import selection

        _cmds, nodes = rig
        plugs = selection.plugs_from_selection([nodes["plain"]])
        assert not any(p.endswith(".visibility") for p in plugs)

    def test_custom_rig_attrs_are_included(self, rig):
        from animkit.core import selection

        _cmds, nodes = rig
        plugs = selection.plugs_from_selection([nodes["plain"]])
        assert nodes["plain"] + ".ikFkBlend" in plugs

    def test_plugs_use_long_names(self, rig):
        from animkit.core import selection

        _cmds, nodes = rig
        plugs = selection.plugs_from_selection([nodes["plain"]])
        assert nodes["plain"] + ".translateX" in plugs
        assert nodes["plain"] + ".tx" not in plugs

    def test_missing_node_yields_nothing_rather_than_raising(self, clean_scene):
        from animkit.core import selection

        assert selection.plugs_from_selection(["doesNotExist"]) == []


class TestCacheScope:
    def test_uncached_outside_a_scope(self, rig):
        """No scope means no memory. This is the property that makes the cache safe."""
        from animkit.core import cache

        assert not cache.active()
        assert cache.stats()["stores"] == {}

    def test_scope_memoizes_and_then_forgets(self, rig):
        from animkit.core import cache, layers

        with cache.scope():
            assert cache.active()
            layers.root_layer()
            layers.root_layer()
            assert cache.stats()["stores"]
        assert not cache.active()
        assert cache.stats()["stores"] == {}

    def test_scope_is_reentrant(self, rig):
        """A keyframe op must be able to open a scope without knowing its caller did."""
        from animkit.core import cache, layers

        with cache.scope():
            with cache.scope():
                layers.root_layer()
            # The inner exit must NOT have dropped the outer scope.
            assert cache.active()
            assert cache.stats()["stores"]
        assert not cache.active()

    def test_selected_layer_change_is_seen_between_scopes(self, rig):
        """The staleness bug the scoped design exists to prevent.

        Clicking a different layer in the Anim Layer editor fires no scene
        callback. A cache that outlived one operation would keep writing to
        the layer that was selected last time.
        """
        from animkit.core import cache, layers

        cmds, _nodes = rig
        cmds.animLayer("L1", edit=True, selected=True)
        with cache.scope():
            first = layers.selected_layers()
        assert "L1" in first

        cmds.animLayer("L1", edit=True, selected=False)
        with cache.scope():
            second = layers.selected_layers()
        assert "L1" not in second

    def test_invalidate_drops_answers_mid_scope(self, rig):
        from animkit.core import cache, layers

        cmds, nodes = rig
        node = nodes["plain"]
        with cache.scope():
            assert layers.resolve_curve(node + ".ty") is None
            cmds.setKeyframe(node + ".ty", time=1, value=0)
            cache.invalidate()
            assert layers.resolve_curve(node + ".ty") is not None

    def test_scene_change_clears_everything(self, rig):
        from animkit.core import cache, layers, scene

        with cache.scope():
            layers.root_layer()
            scene.invalidate_all()
            assert cache.stats()["stores"] == {}
        assert not cache.active()
