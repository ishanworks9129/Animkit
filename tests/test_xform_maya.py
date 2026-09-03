"""The matrix layer. Needs mayapy.

Every convention in `core/xform.py` was verified against Maya rather than
assumed, and this is where those verifications live so they get re-run on every
new Maya version.

The fixture is deliberately hostile. Each bit of hostility is a real thing that
breaks a naive implementation:

  * the right side rotated 180 about Y  -> flipped joint orients
  * a joint with a non-zero jointOrient -> [RA][R][JO] must be unwound
  * a non-zero rotateAxis               -> same
  * different rotateOrder per side      -> decomposition must reorder
  * the mirror plane away from origin   -> reflection is not just "negate X"
  * three levels deep                   -> parents must be solved first
"""

import math

import pytest

from conftest import requires_maya  # noqa: F401

pytestmark = requires_maya

TOL = 1e-4


def approx_matrix(a, b, tol=TOL):
    return all(abs(a[i] - b[i]) <= tol for i in range(16))


@pytest.fixture
def rig(clean_scene):
    """A deliberately awkward but CONVENTIONAL symmetric rig.

    Conventional in the one way that matters: build offsets live in locked
    parent groups and on non-keyable joint translates, and the controls
    themselves zero to their attribute defaults at rest. That is what "zero out
    the controls" means, and it is the convention the derived rest pose relies
    on.

    Awkward in every way that breaks a naive mirror: the right side is rotated
    180 about Y, there is a non-zero rotateAxis, a joint with a jointOrient,
    a different rotateOrder per side, and the mirror plane is not at the origin.

    Returns (cmds, pairs, mirror_x) where pairs is [(left, right), ...] ordered
    parents-first.
    """
    cmds = clean_scene

    # The root stays at the origin: its translateX is posable, so the derived
    # rest pose zeroes it, and the symmetry plane is therefore the root's REST
    # position. Tests that need the character moved do it themselves, which is
    # the case that matters -- a rig sits at the origin in a test scene and
    # never in a shot.
    root = cmds.createNode("transform", name="root")

    made = {}
    for side, sign, flip, order in (("L", 1.0, 0.0, 0), ("R", -1.0, 180.0, 3)):
        # Build offset in a LOCKED group, not in the control's own channels.
        offset = cmds.createNode("transform", name=side + "_shoulderOffset",
                                 parent=root)
        cmds.setAttr(offset + ".translate", sign * 10, 0, 0)
        cmds.setAttr(offset + ".rotateY", flip)
        for channel in ("translateX", "translateY", "translateZ",
                        "rotateX", "rotateY", "rotateZ"):
            cmds.setAttr(offset + "." + channel, lock=True)

        shoulder = cmds.createNode("transform", name=side + "_shoulder",
                                   parent=offset)
        cmds.setAttr(shoulder + ".rotateOrder", order)
        cmds.setAttr(shoulder + ".rotateAxisZ", 15)

        # Joint: rotation is posable, translate is the bone length and is NOT.
        elbow = cmds.createNode("joint", name=side + "_elbow", parent=shoulder)
        cmds.setAttr(elbow + ".translate", 5, 0, 0)
        cmds.setAttr(elbow + ".jointOrientZ", 25)
        cmds.setAttr(elbow + ".rotateOrder", order)
        for channel in ("translateX", "translateY", "translateZ"):
            cmds.setAttr(elbow + "." + channel, lock=True)

        wrist = cmds.createNode("transform", name=side + "_wrist", parent=elbow)
        cmds.setAttr(wrist + ".translate", 4, 0, 0)
        cmds.setAttr(wrist + ".rotateOrder", order)
        for channel in ("translateX", "translateY", "translateZ"):
            cmds.setAttr(wrist + "." + channel, lock=True)

        made[side] = [shoulder, elbow, wrist]

    # Key the rotate channels. Rest is derived from which channels carry
    # animation -- see xform.animated_channels -- so an unkeyed control rests
    # where it stands.
    for side_nodes in made.values():
        for node in side_nodes:
            for channel in ("rotateX", "rotateY", "rotateZ"):
                cmds.setKeyframe(node + "." + channel, time=1, value=0.0)

    return cmds, list(zip(made["L"], made["R"])), root


class TestConventions:
    def test_world_is_local_times_offset_times_parent(self, rig):
        """worldMatrix = matrix * offsetParentMatrix * parentWorldMatrix.

        Row-vector order. Reversing this is the single easiest way to build a
        mirror that is subtly wrong everywhere.
        """
        from animkit.core import xform

        cmds, pairs, _root = rig
        child = pairs[0][0]
        parent = xform.parent_of(child)

        product = (
            xform.local_matrix(child)
            * xform.offset_parent_matrix(child)
            * xform.world_matrix(parent)
        )
        assert approx_matrix(product, xform.world_matrix(child))

    def test_offset_parent_matrix_is_honoured(self, clean_scene):
        """With a ROTATION in it, so the ordering test is not degenerate.

        Two pure translations commute, so an offsetParentMatrix holding only a
        translation cannot distinguish L*OPM*P from OPM*L*P. This one can.
        """
        import maya.api.OpenMaya as om2

        from animkit.core import xform

        cmds = clean_scene
        parent = cmds.createNode("transform", name="p")
        cmds.setAttr(parent + ".rotateY", 30)
        node = cmds.createNode("transform", name="n", parent=parent)
        cmds.setAttr(node + ".translate", 1, 2, 3)
        cmds.setAttr(node + ".rotateZ", 20)

        tm = om2.MTransformationMatrix()
        tm.setTranslation(om2.MVector(0, 7, 0), om2.MSpace.kTransform)
        tm.setRotation(om2.MEulerRotation(0.4, 0.2, 0.1))
        cmds.setAttr(node + ".offsetParentMatrix", list(tm.asMatrix()),
                     type="matrix")

        product = (
            xform.local_matrix(node)
            * xform.offset_parent_matrix(node)
            * xform.world_matrix(parent)
        )
        assert approx_matrix(product, xform.world_matrix(node))

    def test_compose_local_round_trips(self, rig):
        """Rebuilding .matrix from channel values must be exact.

        Covers a plain transform, one with rotateAxis, and a joint with
        jointOrient -- the three cases where a hand-rolled composition of the
        eleven-matrix product goes wrong.
        """
        from animkit.core import xform

        cmds, pairs, _root = rig
        for pair in pairs:
            for node in pair:
                rebuilt = xform.compose_local(
                    node,
                    translate=cmds.getAttr(node + ".translate")[0],
                    rotate=cmds.getAttr(node + ".rotate")[0],
                    scale=cmds.getAttr(node + ".scale")[0],
                )
                assert approx_matrix(rebuilt, xform.local_matrix(node)), node


class TestRest:
    def test_rest_world_matches_the_actually_zeroed_rig(self, rig):
        """The load-bearing claim: rest matrices can be computed WITHOUT
        touching the scene.

        Computed analytically first, then the rig is really zeroed and the
        result compared against Maya's own worldMatrix. If these disagree the
        mirror is built on sand, because every frame relation comes from here.
        """
        from animkit.core import xform

        cmds, pairs, _root = rig
        nodes = [n for pair in pairs for n in pair]

        cmds.setAttr(pairs[0][0] + ".rotate", 11, 22, 33)
        cmds.setKeyframe(pairs[0][0] + ".rotate")
        cmds.setAttr(pairs[1][0] + ".rotate", -5, 7, 3)
        cmds.setKeyframe(pairs[1][0] + ".rotate")
        cmds.setAttr(pairs[2][0] + ".rotate", 4, -9, 12)
        cmds.setKeyframe(pairs[2][0] + ".rotate")

        computed = {n: xform.rest_world_matrix(n) for n in nodes}
        animated = {n: xform.animated_channels(n) for n in nodes}

        from animkit.core import cache
        cache.invalidate()
        # Zero only the ANIMATED channels -- exactly what the derived rest pose
        # claims. Bone lengths and offset groups stay put.
        for node in nodes:
            defaults = xform.channel_defaults(node)
            for channel in animated[node]:
                cmds.setAttr(node + "." + channel, defaults[channel])

        for node in nodes:
            assert approx_matrix(computed[node], xform.world_matrix(node)), node

    def test_rest_is_symmetric_on_a_symmetric_rig(self, rig):
        """The precondition the whole mirror rests on, and the thing
        pairing.analyse reports on a real rig."""
        from animkit.core import xform

        _cmds, pairs, root = rig
        refl = xform.reflection_matrix(*xform.mirror_plane(root))

        for a, b in pairs:
            pos_a = xform.rest_world_matrix(a)
            pos_b = xform.rest_world_matrix(b)
            want = xform.reflect_point(
                [pos_a[12], pos_a[13], pos_a[14]], refl
            )
            got = [pos_b[12], pos_b[13], pos_b[14]]
            assert got == pytest.approx(want, abs=TOL), (a, b)

    def test_mirror_plane_comes_from_the_root_at_rest(self, rig):
        """ANIMATING the character must not move the plane the frame relations
        were derived against.

        Note the emphasis. Rest is decided by which channels carry animation, so
        an unkeyed root that somebody drags IS at rest wherever it lands, and the
        plane follows it -- which is what you want, because the character and its
        symmetry plane move together. Once the root is animated, its rest is its
        default and posing it no longer moves the plane.
        """
        from animkit.core import cache, xform

        cmds, _pairs, root = rig
        cmds.setKeyframe(root + ".translateX", time=1, value=0.0)
        cache.invalidate()
        before = xform.mirror_plane(root)

        cmds.setAttr(root + ".translateX", 100)
        cmds.setKeyframe(root + ".translateX")
        cache.invalidate()
        after = xform.mirror_plane(root)

        assert before[0] == pytest.approx(after[0], abs=TOL)
        assert before[1] == pytest.approx(after[1], abs=TOL)

    def test_static_ancestor_keeps_its_current_matrix(self, clean_scene):
        """A rigger's offset group at (0, 150, 0) with locked channels is AT
        rest there. Zeroing it would move the whole rest frame."""
        from animkit.core import xform

        cmds = clean_scene
        offset = cmds.createNode("transform", name="offsetGrp")
        cmds.setAttr(offset + ".translate", 0, 150, 0)
        for channel in xform.TRANSFORM_CHANNELS:
            cmds.setAttr(offset + "." + channel, lock=True)

        ctrl = cmds.createNode("transform", name="ctrl", parent=offset)
        cmds.setAttr(ctrl + ".translate", 5, 0, 0)

        # The control is animated, so its rest is its default. The offset group
        # is not, so its rest is where the rigger put it.
        cmds.setKeyframe(ctrl + ".translate", time=1)
        assert xform.animated_channels(offset) == frozenset()
        assert xform.animated_channels(ctrl)

        rest = xform.rest_world_matrix(ctrl)
        assert [rest[12], rest[13], rest[14]] == pytest.approx([0, 150, 0], abs=TOL)


class TestTheAnimationGate:
    """`_node_has_animation` is the cheap gate every rest question goes through.

    It rejects ~1500 plumbing nodes with one query so the expensive per-channel
    work only runs on the few dozen that matter. That makes it load-bearing:
    a node it wrongly rejects has NO animated channels as far as the rest of
    the codebase is concerned, so its rest pose, its mirror and its reset are
    all computed from the wrong premise.
    """

    def test_sees_a_curve_on_a_non_keyable_plug(self, clean_scene):
        """The one it used to miss.

        `cmds.keyframe(node, keyframeCount=True)` returns 0 for a node whose
        only curve sits on a plug the rigger set non-keyable -- measured on
        Maya 2024. Riggers hide channels constantly, so a hidden-but-animated
        channel was invisible to animated_channels, and therefore to the rest
        pose and the mirror.
        """
        from animkit.core import cache, xform

        cmds = clean_scene
        node = cmds.createNode("transform", name="hidden_but_animated")
        cmds.setAttr(node + ".rotateZ", keyable=False, channelBox=True)
        cmds.setKeyframe(node + ".rotateZ", time=1, value=15.0)
        cache.invalidate()

        # The old gate, kept here so the trap is visible rather than asserted
        # about in a comment.
        assert cmds.keyframe(node, q=True, keyframeCount=True) == 0

        assert xform._node_has_animation(node)
        assert "rotateZ" in xform.animated_channels(node)

    def test_still_rejects_a_node_with_no_animation(self, clean_scene):
        """The gate's whole purpose. If it stops rejecting, the scene-wide
        paths go back to costing 13,365 attributeQuery calls."""
        from animkit.core import cache, xform

        cmds = clean_scene
        node = cmds.createNode("transform", name="plumbing")
        cmds.setAttr(node + ".translateX", 7.0)
        cache.invalidate()

        assert not xform._node_has_animation(node)
        assert xform.animated_channels(node) == frozenset()

    def test_sees_an_ordinary_keyed_channel(self, clean_scene):
        from animkit.core import cache, xform

        cmds = clean_scene
        node = cmds.createNode("transform", name="keyed")
        cmds.setKeyframe(node + ".translateX", time=1, value=0.0)
        cache.invalidate()

        assert xform._node_has_animation(node)
        assert "translateX" in xform.animated_channels(node)


class TestReflection:
    def test_plane_through_origin(self):
        from animkit.core import xform

        refl = xform.reflection_matrix((0, 0, 0), (1, 0, 0))
        assert xform.reflect_point([5, 1, 2], refl) == pytest.approx([-5, 1, 2])

    def test_plane_off_origin(self):
        from animkit.core import xform

        refl = xform.reflection_matrix((3, 0, 0), (1, 0, 0))
        assert xform.reflect_point([13, 1, 2], refl) == pytest.approx([-7, 1, 2])
        assert xform.reflect_point([3, 9, 9], refl) == pytest.approx([3, 9, 9])

    def test_determinant_is_negative(self):
        """It is a mirror. Anything that "fixes" this sign breaks the
        conjugation that depends on it cancelling."""
        from animkit.core import xform

        refl = xform.reflection_matrix((0, 0, 0), (1, 0, 0))
        assert refl.det4x4() < 0

    def test_arbitrary_normal(self):
        from animkit.core import xform

        refl = xform.reflection_matrix((0, 0, 0), (1, 1, 0))
        got = xform.reflect_point([1, 0, 0], refl)
        assert got == pytest.approx([0, -1, 0], abs=TOL)

    def test_degenerate_normal_does_not_raise(self):
        from animkit.core import xform

        refl = xform.reflection_matrix((0, 0, 0), (0, 0, 0))
        assert xform.reflect_point([5, 1, 2], refl) == pytest.approx([-5, 1, 2])


class TestFrameRelation:
    def test_determinant_is_negative_for_every_pair(self, rig):
        from animkit.core import xform

        _cmds, pairs, root = rig
        refl = xform.reflection_matrix(*xform.mirror_plane(root))
        for a, b in pairs:
            q = xform.frame_relation(a, b, refl)
            assert q.det4x4() < 0, (a, b)

    def test_flipped_orient_shows_up_in_q(self, rig):
        """The whole point of deriving Q rather than declaring an axis table.

        On this rig the right shoulder is rotated 180 about Y, so Q for that
        pair is NOT the plain diag(-1, 1, 1) that a hardcoded table would use.
        """
        from animkit.core import xform

        _cmds, pairs, root = rig
        refl = xform.reflection_matrix(*xform.mirror_plane(root))
        q = xform.frame_relation(pairs[0][0], pairs[0][1], refl)
        naive = xform.reflection_matrix((0, 0, 0), (1, 0, 0))
        assert not approx_matrix(q, naive)

    def test_mirror_delta_preserves_determinant(self, rig):
        """Conjugating by a reflection gives back a PROPER transform -- the two
        reflections cancel. This is why no handedness fix is needed."""
        from animkit.core import xform

        cmds, pairs, root = rig
        refl = xform.reflection_matrix(*xform.mirror_plane(root))
        a, b = pairs[0]
        cmds.setAttr(a + ".rotate", 12, -20, 35)
        cmds.setKeyframe(a + ".rotate")

        delta = xform.pose_delta(a)
        mirrored = xform.mirror_delta(delta, xform.frame_relation(a, b, refl))
        assert mirrored.det4x4() == pytest.approx(delta.det4x4(), abs=TOL)
        assert mirrored.det4x4() > 0


class TestPoseDelta:
    def test_delta_is_local_not_world(self, rig):
        """Moving an ANCESTOR must not change a control's pose delta.

        A world-space delta absorbs ancestor motion, and conjugating it then
        mirrors the ancestors too: with the root at x=100 the mirrored arm lands
        at x=-110 instead of x=+90. This is the assertion that pins the
        formulation.
        """
        from animkit.core import cache, xform

        cmds, pairs, root = rig
        node = pairs[0][0]
        cmds.setAttr(node + ".rotate", 12, -20, 35)
        cmds.setKeyframe(node + ".rotate")

        before = xform.pose_delta(node)
        cmds.setAttr(root + ".translateX", 100)
        cache.invalidate()
        after = xform.pose_delta(node)

        assert approx_matrix(before, after), "pose delta absorbed ancestor motion"

    def test_delta_round_trips(self, rig):
        from animkit.core import xform

        cmds, pairs, _root = rig
        node = pairs[0][0]
        cmds.setAttr(node + ".rotate", 12, -20, 35)
        cmds.setKeyframe(node + ".rotate")

        delta = xform.pose_delta(node)
        assert approx_matrix(
            xform.local_from_delta(node, delta), xform.local_matrix(node)
        )

    def test_rest_pose_has_identity_delta(self, rig):
        from animkit.core import xform

        _cmds, pairs, _root = rig
        for pair in pairs:
            for node in pair:
                assert approx_matrix(xform.pose_delta(node), xform.IDENTITY), node


class TestDecomposition:
    def test_analytic_agrees_with_maya(self, rig):
        """The analytic decomposition against Maya's own, on every node of the
        awkward rig. If these ever disagree, trust the proxy."""
        from animkit.core import xform

        cmds, pairs, _root = rig
        cmds.setAttr(pairs[0][0] + ".rotate", 12, -20, 35)
        cmds.setKeyframe(pairs[0][0] + ".rotate")
        cmds.setAttr(pairs[1][0] + ".rotate", 5, 15, -40)
        cmds.setKeyframe(pairs[1][0] + ".rotate")

        for pair in pairs:
            for node in pair:
                world = xform.world_matrix(node)
                analytic = xform.channels_from_world(node, world)
                proxy = xform.channels_via_proxy(node, world)
                assert analytic is not None, node
                assert proxy is not None, node

                # Compared as MATRICES, not channel by channel. An orientation
                # has many Euler representations -- 180 and -180 are the same
                # pose -- so comparing numbers would fail on a difference that
                # does not exist. The matrix is the thing that has to agree.
                def compose(values):
                    return xform.compose_local(
                        node,
                        translate=[values[c] for c in xform.TRANSLATE],
                        rotate=[values[c] for c in xform.ROTATE],
                        scale=[values[c] for c in xform.SCALE],
                    )

                assert approx_matrix(compose(analytic), compose(proxy), 1e-3), node

    def test_round_trips_through_world(self, rig):
        """Decomposing a node's own world matrix must return its own channels."""
        from animkit.core import xform

        cmds, pairs, _root = rig
        node = pairs[0][0]
        cmds.setAttr(node + ".rotate", 12, -20, 35)
        cmds.setKeyframe(node + ".rotate")
        cmds.setAttr(node + ".translate", 11.5, 2.0, -1.0)

        values = xform.channels_from_world(node, xform.world_matrix(node))
        assert values["translateX"] == pytest.approx(11.5, abs=TOL)
        assert values["rotateY"] == pytest.approx(-20.0, abs=1e-3)

    def test_awkward_transform_is_detected(self, clean_scene):
        """A node with a rotate pivot cannot be decomposed analytically, and
        must be routed to the proxy rather than silently mis-decomposed."""
        from animkit.core import xform

        cmds = clean_scene
        node = cmds.createNode("transform", name="pivoted")
        cmds.setAttr(node + ".rotatePivot", 1, 2, 3)

        assert xform.has_awkward_transform(node)
        assert xform.channels_from_local(node, xform.local_matrix(node)) is None
        assert xform.channels_from_world(node, xform.world_matrix(node)) is not None

    def test_proxy_leaves_nothing_behind(self, rig):
        from animkit.core import xform

        cmds, pairs, _root = rig
        xform.channels_via_proxy(pairs[0][0], xform.world_matrix(pairs[0][0]))
        assert not cmds.ls("animkitProxy_*")

    def test_proxy_works_on_locked_channels(self, clean_scene):
        """Rigs lock channels. xform silently refuses on a locked one, so the
        proxy has to unlock its own copy."""
        from animkit.core import xform

        cmds = clean_scene
        node = cmds.createNode("transform", name="locked")
        cmds.setAttr(node + ".translate", 1, 2, 3)
        cmds.setAttr(node + ".translateY", lock=True)

        values = xform.channels_via_proxy(node, xform.world_matrix(node))
        assert values is not None
        assert values["translateY"] == pytest.approx(2.0, abs=TOL)


class TestNearestEuler:
    """A mirror that writes -180 where the animator had +180 produces the same
    pose and the opposite interpolation. Both keys look right and the limb
    spins between them."""

    def test_prefers_the_representation_near_the_reference(self):
        import maya.api.OpenMaya as om2

        from animkit.core import xform

        euler = om2.MEulerRotation(0.0, math.pi, 0.0)
        near = xform.nearest_euler(euler, [0.0, -math.pi, 0.0])
        assert near.y == pytest.approx(-math.pi, abs=1e-6)

    def test_keeps_whole_turns(self):
        import maya.api.OpenMaya as om2

        from animkit.core import xform

        euler = om2.MEulerRotation(0.0, 0.1, 0.0)
        near = xform.nearest_euler(euler, [0.0, 4.0 * math.pi + 0.1, 0.0])
        assert near.y == pytest.approx(4.0 * math.pi + 0.1, abs=1e-6)

    def test_is_the_same_orientation(self):
        import maya.api.OpenMaya as om2

        from animkit.core import xform

        euler = om2.MEulerRotation(0.3, 2.0, -1.1)
        near = xform.nearest_euler(euler, [10.0, -10.0, 10.0])
        assert approx_matrix(near.asMatrix(), euler.asMatrix(), 1e-6)

    def test_decomposition_stays_near_the_current_channels(self, rig):
        """The whole point, end to end: decomposing a node's own matrix must
        return the numbers already on the channels, not an equivalent set."""
        from animkit.core import xform

        cmds, pairs, _root = rig
        node = pairs[0][0]
        cmds.setAttr(node + ".rotate", 200, 30, -190)
        cmds.setKeyframe(node + ".rotate")
        values = xform.channels_from_world(node, xform.world_matrix(node))
        assert values["rotateX"] == pytest.approx(200.0, abs=1e-3)
        assert values["rotateZ"] == pytest.approx(-190.0, abs=1e-3)


class TestDepth:
    def test_orders_parents_before_children(self, rig):
        from animkit.core import xform

        _cmds, pairs, _root = rig
        left = [pair[0] for pair in pairs]
        depths = [xform.depth(n) for n in left]
        assert depths == sorted(depths)
        assert depths[0] < depths[-1]
        assert all(d > 0 for d in depths)
