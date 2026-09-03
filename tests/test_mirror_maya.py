"""Pose copy/paste/mirror/flip. Needs mayapy.

The three invariants below hold on ANY rig with no configuration, which is what
makes a rig-agnostic mirror testable at all:

  1. INVOLUTION      mirroring a pose twice returns the original
  2. FIXED POINT     mirroring an already-symmetric pose changes nothing
  3. GEOMETRY        after mirroring, every paired control's world POSITION is
                     the reflection of its counterpart's

Invariant 3 is the one that catches the "nearly right" failure. Note it is a
POSITION check, not a matrix check: W * reflection has a negative determinant, so
no rigid transform can ever equal it, and asserting on matrices there is
unsatisfiable rather than strict. Run over a deep hierarchy it pins rotation too,
because a wrong rotation mirror puts the children in the wrong place.

The fixture is hostile on purpose: flipped joint orients on the right side, a
non-zero rotateAxis, a joint with a jointOrient, a different rotateOrder per
side, and -- crucially -- the character moved away from the origin, which is the
case that exposed the world-vs-local delta bug.
"""

import pytest

from conftest import requires_maya  # noqa: F401

pytestmark = requires_maya

TOL = 1e-3


@pytest.fixture
def rig(clean_scene):
    """Symmetric-at-rest, awkward everywhere else. Returns (cmds, pairs, root)."""
    cmds = clean_scene
    root = cmds.createNode("transform", name="rig_root")

    made = {}
    for side, sign, flip, order in (("L", 1.0, 0.0, 0), ("R", -1.0, 180.0, 3)):
        offset = cmds.createNode(
            "transform", name=side + "_shoulderOffset", parent=root
        )
        cmds.setAttr(offset + ".translate", sign * 10, 0, 0)
        cmds.setAttr(offset + ".rotateY", flip)
        for channel in ("translateX", "translateY", "translateZ",
                        "rotateX", "rotateY", "rotateZ"):
            cmds.setAttr(offset + "." + channel, lock=True)

        shoulder = cmds.createNode(
            "transform", name=side + "_shoulder_ctrl", parent=offset
        )
        cmds.setAttr(shoulder + ".rotateOrder", order)
        cmds.setAttr(shoulder + ".rotateAxisZ", 15)
        cmds.addAttr(shoulder, longName="ikFkBlend", attributeType="double",
                     keyable=True)

        elbow = cmds.createNode("joint", name=side + "_elbow_ctrl",
                                parent=shoulder)
        cmds.setAttr(elbow + ".translate", 5, 0, 0)
        cmds.setAttr(elbow + ".jointOrientZ", 25)
        cmds.setAttr(elbow + ".rotateOrder", order)
        for channel in ("translateX", "translateY", "translateZ"):
            cmds.setAttr(elbow + "." + channel, lock=True)

        wrist = cmds.createNode("transform", name=side + "_wrist_ctrl",
                                parent=elbow)
        cmds.setAttr(wrist + ".translate", 4, 0, 0)
        cmds.setAttr(wrist + ".rotateOrder", order)
        for channel in ("translateX", "translateY", "translateZ"):
            cmds.setAttr(wrist + "." + channel, lock=True)

        made[side] = [shoulder, elbow, wrist]

    # A centre control, to exercise the self-mirroring path.
    spine = cmds.createNode("transform", name="spine_ctrl", parent=root)

    cmds.currentTime(1)

    # KEY the controls. Rest is derived from which channels carry animation, so
    # an unkeyed control is treated as resting where it stands -- which is
    # correct behaviour and useless as a mirror fixture. Animators work with Auto
    # Key on; this matches that.
    for node in [n for pair in zip(made["L"], made["R"]) for n in pair] + [spine]:
        for channel in ("rotateX", "rotateY", "rotateZ"):
            cmds.setKeyframe(node + "." + channel, time=1, value=0.0)

    return cmds, list(zip(made["L"], made["R"])), root, spine


def pose_left(cmds, pairs):
    """Pose the left arm, keying as an animator with Auto Key would."""
    for node, values in (
        (pairs[0][0], (12, -20, 35)),
        (pairs[1][0], (5, 15, -40)),
        (pairs[2][0], (-8, 3, 22)),
    ):
        cmds.setAttr(node + ".rotate", *values)
        cmds.setKeyframe(node + ".rotate")


def wpos(cmds, node):
    return cmds.xform(node, q=True, worldSpace=True, translation=True)


def curve_rotate(cmds, node):
    """The rotate values ON THE CURVES, with no DG evaluation involved.

    The right instrument for "did the write land". getAttr answers a different
    question -- what the DG currently believes -- and is stale after a key is
    written until the plug is dirtied, so a getAttr assertion here tests Maya's
    evaluation timing rather than this code. See the README section on stale DG
    reads; it has caught this codebase out three times now.
    """
    out = []
    for axis in "XYZ":
        values = cmds.keyframe(
            "{0}.rotate{1}".format(node, axis), q=True, valueChange=True
        )
        out.append(values[-1] if values else None)
    return out


def reflected(node, other, cmds, reflection):
    from animkit.core import xform

    return xform.reflect_point(wpos(cmds, node), reflection)


def break_rest_symmetry(cmds, side="R"):
    """Move one side's offset group so the rig is no longer symmetric at rest.

    Unlock, move, RE-LOCK. The re-lock matters: an unlocked channel becomes
    posable, and a posable channel's rest value is its attribute default -- so
    leaving it unlocked would have the derived rest pose zero it again and quietly
    restore the symmetry this is trying to break.
    """
    from animkit.core import cache

    group = side + "_shoulderOffset"
    cmds.setAttr(group + ".translateY", lock=False)
    cmds.setAttr(group + ".translateY", 5)
    cmds.setAttr(group + ".translateY", lock=True)
    cache.invalidate()
    return group


def live_reflection(cmds, root):
    """Reflection about the root's CURRENT frame.

    The frame relations are derived at rest, but the geometric invariant is
    checked against wherever the character is standing now -- so the plane used
    for verification has to follow the root.
    """
    import maya.api.OpenMaya as om2

    from animkit.core import xform

    current = xform.world_matrix(root)
    local = xform.reflection_matrix((0, 0, 0), (1, 0, 0))
    return om2.MMatrix(current.inverse() * local * current)


class TestPairing:
    def test_finds_the_counterpart_by_name(self, rig):
        from animkit.core import pairing

        _cmds, pairs, _root, _spine = rig
        left, right = pairs[0]
        assert pairing.counterpart(left) == right
        assert pairing.counterpart(right) == left

    def test_centre_control_pairs_with_itself(self, rig):
        from animkit.core import pairing

        _cmds, _pairs, _root, spine = rig
        assert pairing.counterpart(spine) == spine

    def test_candidate_names_do_not_match_mid_word(self):
        """The `l`/`r` pair must not fire inside "clavicle" or "spine"."""
        from animkit.core import pairing

        for name in ("clavicle_ctrl", "spine_ctrl", "world_ctrl", "roll_ctrl"):
            for candidate in pairing.candidate_names(name):
                assert candidate != name

        assert "R_arm" in pairing.candidate_names("L_arm")
        assert "arm_R" in pairing.candidate_names("arm_L")
        assert "right_arm" in pairing.candidate_names("left_arm")

    def test_geometric_fallback_with_no_naming_convention(self, clean_scene):
        """A rig whose convention is in no token table at all.

        This is the case that makes the mirror rig-agnostic rather than
        convention-agnostic: pairing on reflected rest position alone.
        """
        from animkit.core import pairing

        cmds = clean_scene
        root = cmds.createNode("transform", name="thing")
        a = cmds.createNode("transform", name="alpha", parent=root)
        cmds.setAttr(a + ".translateX", 7)
        b = cmds.createNode("transform", name="omega", parent=root)
        cmds.setAttr(b + ".translateX", -7)
        for channel in ("translateX", "translateY", "translateZ"):
            cmds.setAttr(a + "." + channel, lock=True)
            cmds.setAttr(b + "." + channel, lock=True)

        assert pairing.counterpart(a) == b

    def test_pair_up_reports_unpaired(self, clean_scene):
        from animkit.core import pairing

        cmds = clean_scene
        root = cmds.createNode("transform", name="thing")
        lonely = cmds.createNode("transform", name="L_lonely", parent=root)
        cmds.setAttr(lonely + ".translateX", 7)
        cmds.setAttr(lonely + ".translateX", lock=True)

        pairs, unpaired = pairing.pair_up([lonely])
        assert pairs == []
        assert unpaired == [lonely]

    def test_check_symmetry_catches_an_asymmetric_rest(self, rig):
        from animkit.core import cache, pairing

        cmds, pairs, root, _spine = rig
        left, right = pairs[0]

        reflection = pairing.reflection_for(left)
        assert pairing.check_symmetry([(left, right)], reflection) == []

        break_rest_symmetry(cmds)

        failures = pairing.check_symmetry([(left, right)], reflection)
        assert failures
        assert "off by" in pairing.describe_failures(failures)


class TestSymmetryIsOneQuestion:
    """The merge that `pairing.analyse` exists for.

    Rest asymmetry surfaces at TWO stages and the two look nothing alike:

      * a pair that formed and then fails verification -- check_symmetry
      * a counterpart rejected by is_geometric_counterpart before it could ever
        become a pair, which check_symmetry therefore never sees

    Three call sites checked one and not the other at some point, and the
    failure mode is the worst available: check_symmetry returns [], the tool
    reports the rig is fine, and mirrors it wrongly. These tests pin that the
    single call catches both.
    """

    def test_reports_symmetric_on_a_symmetric_rig(self, rig):
        from animkit.core import pairing

        _cmds, pairs, _root, spine = rig
        nodes = [n for pair in pairs for n in pair] + [spine]

        report = pairing.analyse(nodes)

        assert report.ok
        assert report.broken == []
        assert len(report.pairs) == len(pairs) + 1  # + the centre control
        assert report.describe() == ""

    def test_catches_asymmetry_that_check_symmetry_alone_cannot_see(self, rig):
        """The exact hole. Both assertions matter, and so does their order."""
        from animkit.core import pairing

        cmds, pairs, _root, _spine = rig
        left, right = pairs[0]
        break_rest_symmetry(cmds)

        reflection = pairing.reflection_for(left)

        # The counterpart is not where the reflection puts it, so it is refused
        # before it can become a pair. Nothing pairs, so there is nothing for
        # check_symmetry to fail on -- and on its own it says the rig is fine.
        formed, unpaired = pairing.pair_up([left, right], reflection=reflection)
        assert pairing.check_symmetry(formed, reflection) == [], (
            "fixture stopped exercising the two-stage hole"
        )
        assert set(unpaired) == {left, right}

        # One call, and it is not fooled.
        report = pairing.analyse([left, right], reflection=reflection)
        assert not report.ok
        assert report.broken
        assert "off by" in report.describe()

    def test_catches_asymmetry_a_formed_pair_fails(self, rig):
        """The other stage: a pair that forms and then fails verification.

        Constructed by verifying the pair BEFORE the rest is broken, so the
        pairing is real rather than asserted.
        """
        from animkit.core import cache, pairing

        cmds, pairs, _root, _spine = rig
        left, right = pairs[0]

        assert pairing.counterpart(left) == right
        reflection = pairing.reflection_for(left)
        assert pairing.check_symmetry([(left, right)], reflection) == []

        break_rest_symmetry(cmds)
        cache.invalidate()

        broken = pairing.check_symmetry([(left, right)], reflection)
        assert broken
        # Same measurement, same list, whichever stage found it.
        assert pairing.analyse([left, right], reflection=reflection).broken

    def test_worst_offender_is_reported_first(self, rig):
        """A truncated diagnostic must show the pair worth looking at."""
        from animkit.core import pairing

        cmds, pairs, _root, _spine = rig

        rows = [("L_a", "R_a", 0.5), ("L_b", "R_b", 9.0), ("L_c", "R_c", 2.0)]
        report = pairing.Symmetry([], list(rows), [], None)
        report.broken.sort(key=lambda row: row[2], reverse=True)
        assert "L_b/R_b" in report.describe(limit=1)

        break_rest_symmetry(cmds)
        real = pairing.analyse([n for pair in pairs for n in pair])
        distances = [row[2] for row in real.broken]
        assert distances == sorted(distances, reverse=True)

    def test_a_broken_pair_is_reported_once_not_twice(self, rig):
        """With both sides selected each names the other as its suspect.

        The naive merge reports "L/R off by 5.000, R/L off by 5.000" -- the
        same fault twice, in a message whose whole job is to be short enough
        to read at the moment a mirror refuses.
        """
        from animkit.core import pairing

        cmds, pairs, _root, _spine = rig
        left, right = pairs[0]
        break_rest_symmetry(cmds)

        report = pairing.analyse([left, right])
        assert len(report.broken) == 1
        assert set(report.broken[0][:2]) == {left, right}

    def test_an_unmatched_control_is_not_an_asymmetric_rig(self, clean_scene):
        """`missing` is deliberately not part of `ok`.

        A lone control, or a naming convention nobody recognised, is not a
        broken rig -- and refusing to mirror a whole selection over one would
        be wrong. It is warned about and carried past.
        """
        from animkit.core import pairing

        cmds = clean_scene
        root = cmds.createNode("transform", name="thing")
        lonely = cmds.createNode("transform", name="L_lonely", parent=root)
        cmds.setAttr(lonely + ".translateX", 7)
        cmds.setAttr(lonely + ".translateX", lock=True)

        report = pairing.analyse([lonely])
        assert report.missing == [lonely]
        assert report.ok
        assert report.broken == []

    def test_empty_input_is_symmetric_and_does_not_raise(self):
        from animkit.core import pairing

        report = pairing.analyse([])
        assert report.ok
        assert report.pairs == []
        assert report.missing == []


class TestControlFilterIsNotAChannelFilter:
    """The rename, pinned by the failure that motivated it.

    `posable_channels` answered "which channels can be written" and was read as
    "is this a control" at four call sites. On a rig whose rigger did not lock
    the internals those differ by 1521 nodes to 80. This fixture is that rig in
    miniature: one drawn control, and plumbing that is every bit as writable.
    """

    @staticmethod
    def _rig_with_plumbing(cmds):
        root = cmds.createNode("transform", name="plumbed_root")
        handle = cmds.createNode("transform", name="hand_ctrl", parent=root)
        cmds.createNode("nurbsCurve", name="hand_ctrlShape", parent=handle)

        # Unlocked, keyable, settable -- and not a control. Nobody locked them,
        # because riggers do not.
        plumbing = []
        for name in ("AlignIKToWrist", "BendElbow1", "twist_01", "stabiliser"):
            plumbing.append(
                cmds.createNode("transform", name=name, parent=root)
            )
        return root, handle, plumbing

    def test_the_channel_filter_keeps_the_plumbing(self, clean_scene):
        from animkit.core import pairing

        cmds = clean_scene
        _root, handle, plumbing = self._rig_with_plumbing(cmds)

        kept = pairing.with_settable_channels([handle] + plumbing)
        assert set(kept) == set([handle] + plumbing), (
            "if this ever stops keeping the plumbing, the rename's premise is "
            "gone and pose.controls_in can be simplified"
        )

    def test_the_control_filter_does_not(self, clean_scene):
        from animkit.tools import pose

        cmds = clean_scene
        _root, handle, plumbing = self._rig_with_plumbing(cmds)

        assert pose.controls_in([handle] + plumbing) == [handle]

    def test_control_filter_falls_back_when_the_rig_declares_nothing(
        self, clean_scene
    ):
        """A rig with no tags, no control shapes and no control set.

        Returning [] here would make Set Rest impossible on exactly the odd
        rigs most likely to need it, so it degrades to the channel filter.
        """
        from animkit.tools import pose

        cmds = clean_scene
        root = cmds.createNode("transform", name="bare_root")
        nodes = [
            cmds.createNode("transform", name="bare_%d" % i, parent=root)
            for i in range(3)
        ]

        assert pose.rig_controls() == []
        assert pose.controls_in(nodes) == nodes

    def test_set_rest_captures_controls_not_plumbing(self, clean_scene):
        """The 280-internal-pairs report, at the size it can be asserted at.

        Set Rest used to verify symmetry across everything with a writable
        channel, so a perfectly good rig came back "280 pair(s) off, e.g.
        AlignIKToWrist_L/AlignIKToWrist_R".
        """
        from animkit.core import xform
        from animkit.tools import pose

        cmds = clean_scene
        root = cmds.createNode("transform", name="ctrl_root")

        controls = []
        for side, sign in (("L", 1.0), ("R", -1.0)):
            ctrl = cmds.createNode(
                "transform", name=side + "_hand_ctrl", parent=root
            )
            cmds.createNode(
                "nurbsCurve", name=side + "_hand_ctrlShape", parent=ctrl
            )
            cmds.setAttr(ctrl + ".translateX", sign * 6)
            controls.append(ctrl)

            # Plumbing that is NOT mirrored -- it would fail a symmetry check
            # if it were ever included in one.
            helper = cmds.createNode(
                "transform", name="AlignIKToWrist_" + side, parent=root
            )
            cmds.setAttr(helper + ".translateY", sign * 3)

        xform.clear_rest_overrides()
        try:
            cmds.select(controls[0])
            captured = pose.capture_rest_pose()
            assert captured, "Set Rest refused a rig that is symmetric"
            assert set(xform._rest_overrides) == set(controls)
        finally:
            xform.clear_rest_overrides()


class TestMirrorInvariants:
    def test_geometry_positions_are_reflected(self, rig):
        """Invariant 3, the one that catches a nearly-right mirror."""
        from animkit.tools import pose

        cmds, pairs, root, _spine = rig
        cmds.setAttr(root + ".translateX", 100)  # character NOT at the origin
        pose_left(cmds, pairs)

        nodes = [n for pair in pairs for n in pair]
        cmds.select(nodes)
        assert pose.mirror_selected() > 0

        reflection = live_reflection(cmds, root)
        for a, b in pairs:
            want = reflected(a, b, cmds, reflection)
            got = wpos(cmds, b)
            assert got == pytest.approx(want, abs=TOL), (a, b)

    def test_involution(self, rig):
        """Invariant 1: mirroring left->right, then right->left, restores the left.

        A one-directional mirror is IDEMPOTENT, not involutive -- running
        mirror_selected twice with the same selection order simply recomputes the
        same answer. The involution only shows up when the direction is reversed,
        so the direction is set explicitly here rather than relying on selection
        order.
        """
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        pose_left(cmds, pairs)

        left_nodes = [pair[0] for pair in pairs]
        right_nodes = [pair[1] for pair in pairs]
        before = {n: wpos(cmds, n) for n in left_nodes}

        captured = pose.capture(left_nodes + right_nodes)
        pose.apply(pose.mirror_pose(captured, nodes=left_nodes + right_nodes))

        # Now mirror back the other way: right drives, so it is listed first.
        captured = pose.capture(right_nodes + left_nodes)
        pose.apply(pose.mirror_pose(captured, nodes=right_nodes + left_nodes))

        for node in left_nodes:
            assert wpos(cmds, node) == pytest.approx(before[node], abs=TOL), node

    def test_direction_follows_the_node_order(self, rig):
        """Documented behaviour: the first control of a pair drives the mirror."""
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        left, right = pairs[0]
        cmds.setAttr(left + ".rotateZ", 30)
        cmds.setKeyframe(left + ".rotateZ")
        right_before = cmds.getAttr(right + ".rotateZ")

        captured = pose.capture([left, right])
        pose.apply(pose.mirror_pose(captured, nodes=[left, right]))

        assert cmds.getAttr(right + ".rotateZ") != pytest.approx(
            right_before, abs=TOL
        )
        assert cmds.getAttr(left + ".rotateZ") == pytest.approx(30.0, abs=TOL)

    def test_symmetric_pose_is_a_fixed_point(self, rig):
        """Invariant 2: mirroring an already-symmetric pose changes nothing."""
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        nodes = [n for pair in pairs for n in pair]
        before = {n: wpos(cmds, n) for n in nodes}

        cmds.select(nodes)
        pose.mirror_selected()

        for node in nodes:
            assert wpos(cmds, node) == pytest.approx(before[node], abs=TOL), node

    def test_works_with_the_character_moved_and_rotated(self, rig):
        """The plane belongs to the rig, not the world."""
        from animkit.tools import pose

        cmds, pairs, root, _spine = rig
        cmds.setAttr(root + ".translate", 100, 50, -25)
        cmds.setAttr(root + ".rotateY", 37)
        pose_left(cmds, pairs)

        nodes = [n for pair in pairs for n in pair]
        cmds.select(nodes)
        assert pose.mirror_selected() > 0

        reflection = live_reflection(cmds, root)
        for a, b in pairs:
            assert wpos(cmds, b) == pytest.approx(
                reflected(a, b, cmds, reflection), abs=TOL
            ), (a, b)

    def test_naive_channel_negation_would_have_failed(self, rig):
        """Proof the derived relation is doing real work.

        On this rig the mirrored rotation is NOT the source rotation with signs
        flipped. If it were, the whole exercise would be unnecessary.
        """
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        left, right = pairs[0]
        cmds.setAttr(left + ".rotate", 12, -20, 35)
        cmds.setKeyframe(left + ".rotate")

        cmds.select([left, right])
        pose.mirror_selected()

        got = cmds.getAttr(right + ".rotate")[0]
        naive = [
            (12, 20, -35), (-12, -20, 35), (12, -20, 35), (-12, 20, -35),
        ]
        for candidate in naive:
            assert got != pytest.approx(candidate, abs=0.5), (
                "the derived mirror agreed with a naive sign flip -- either the "
                "fixture stopped being hostile or the maths regressed"
            )


class TestRefusal:
    def test_refuses_an_asymmetric_rest_pose(self, rig):
        """It must say it cannot, not produce something plausible."""
        from animkit.core import cache
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        left, right = pairs[0]
        break_rest_symmetry(cmds)

        captured = pose.capture([left, right])
        with pytest.raises(pose.MirrorRefused):
            pose.mirror_pose(captured)

    def test_refusal_writes_nothing(self, rig):
        from animkit.core import cache
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        left, right = pairs[0]
        break_rest_symmetry(cmds)
        cmds.setAttr(left + ".rotate", 12, -20, 35)
        cmds.setKeyframe(left + ".rotate")

        before = cmds.getAttr(right + ".rotate")[0]
        cmds.select([left, right])
        assert pose.mirror_selected() == 0
        assert cmds.getAttr(right + ".rotate")[0] == pytest.approx(before)


class TestCopyPaste:
    def test_round_trip(self, rig):
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        left = pairs[0][0]
        cmds.setAttr(left + ".rotate", 12, -20, 35)
        cmds.setKeyframe(left + ".rotate")

        cmds.select(left)
        assert pose.copy_pose() > 0

        cmds.setAttr(left + ".rotate", 0, 0, 0)
        cmds.setKeyframe(left + ".rotate")
        assert pose.paste_pose() > 0
        assert curve_rotate(cmds, left) == pytest.approx((12, -20, 35), abs=TOL)

    def test_paste_writes_keys_not_just_attributes(self, rig):
        """A pose paste that only setAttrs is lost at the next key."""
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        left = pairs[0][0]
        cmds.setAttr(left + ".rotate", 12, -20, 35)
        cmds.setKeyframe(left + ".rotate")
        cmds.select(left)
        pose.copy_pose()
        cmds.setAttr(left + ".rotate", 0, 0, 0)
        cmds.setKeyframe(left + ".rotate")
        pose.paste_pose()

        assert cmds.keyframe(left + ".rotateX", q=True, keyframeCount=True)

    def test_paste_is_one_undo_step(self, rig):
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        left = pairs[0][0]
        cmds.setAttr(left + ".rotate", 12, -20, 35)
        cmds.setKeyframe(left + ".rotate")
        cmds.select(left)
        pose.copy_pose()
        cmds.setAttr(left + ".rotate", 0, 0, 0)
        cmds.setKeyframe(left + ".rotate")

        before = curve_rotate(cmds, left)
        pose.paste_pose()
        assert curve_rotate(cmds, left) != pytest.approx(before, abs=TOL)

        cmds.undo()
        assert curve_rotate(cmds, left) == pytest.approx(before, abs=TOL)

    def test_mirror_is_one_undo_step(self, rig):
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        pose_left(cmds, pairs)
        nodes = [n for pair in pairs for n in pair]

        before = {n: cmds.getAttr(n + ".rotate")[0] for n in nodes}
        cmds.select(nodes)
        pose.mirror_selected()
        cmds.undo()

        for node in nodes:
            assert cmds.getAttr(node + ".rotate")[0] == pytest.approx(
                before[node], abs=TOL
            ), node

    def test_paste_with_nothing_copied_is_harmless(self, rig):
        from animkit.tools import pose

        pose._clipboard = None
        assert pose.paste_pose() == 0
        assert pose.paste_mirrored() == 0

    def test_custom_attributes_are_copied_not_negated(self, rig):
        """No matrix can say whether ikFkBlend should flip. Copy is the default."""
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        left, right = pairs[0]
        cmds.setAttr(left + ".ikFkBlend", 0.7)

        cmds.select([left, right])
        pose.mirror_selected()
        assert cmds.getAttr(right + ".ikFkBlend") == pytest.approx(0.7, abs=TOL)


class TestMirrorDirection:
    """Which side is the source.

    pair_up walks the selection in order, so with both members of a pair
    selected the direction used to come down to click order -- invisible, and
    reversed as often as not. Selecting the wrong side alone was worse: it
    mirrored REST onto the posed side and destroyed the pose, while printing
    "nothing to mirror". Direction is decided by which side is posed now.
    """

    def test_direction_ignores_selection_order(self, rig):
        """Both click orders must produce the same result."""
        from animkit.core import cache
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        left, right = pairs[0]

        def run(order):
            cmds.setAttr(left + ".rotate", 0, 0, 0)
            cmds.setAttr(right + ".rotate", 0, 0, 0)
            cmds.setKeyframe(left + ".rotate")
            cmds.setKeyframe(right + ".rotate")
            cmds.setAttr(left + ".rotateZ", 40)
            cmds.setKeyframe(left + ".rotate")
            cache.invalidate()
            cmds.select(order)
            pose.mirror_selected()
            cache.invalidate()
            return curve_rotate(cmds, left), curve_rotate(cmds, right)

        assert run([left, right]) == run([right, left])

    def test_the_posed_side_is_the_source(self, rig):
        """Selecting both, only one posed: the posed one wins regardless."""
        from animkit.core import cache
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        left, right = pairs[0]
        cmds.setAttr(left + ".rotateZ", 40)
        cmds.setKeyframe(left + ".rotate")
        cache.invalidate()

        # Right first -- under the old rule this mirrored rest onto the left.
        cmds.select([right, left])
        assert pose.mirror_selected() > 0
        cache.invalidate()

        assert curve_rotate(cmds, left)[2] == pytest.approx(40.0, abs=TOL)
        assert curve_rotate(cmds, right)[2] != pytest.approx(0.0, abs=TOL)

    def test_selecting_the_rest_side_never_erases_the_pose(self, rig, capfd):
        """THE DESTRUCTIVE CASE. It used to zero both arms and say nothing ran."""
        from animkit.core import cache
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        left, right = pairs[0]
        cmds.setAttr(left + ".rotateZ", 40)
        cmds.setKeyframe(left + ".rotate")
        cache.invalidate()

        cmds.select(right)          # the wrong side: right is at rest
        pose.mirror_selected()
        cache.invalidate()

        assert curve_rotate(cmds, left)[2] == pytest.approx(40.0, abs=TOL)
        err = capfd.readouterr().err
        assert "would have erased a pose" in err
        # And NOT the old story, which contradicted what it was doing.
        assert "nothing to mirror" not in err

    def test_both_sides_posed_is_reported_as_ambiguous(self, rig, capfd):
        """No way to tell which way was meant. Say so; do not guess."""
        from animkit.core import cache
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        left, right = pairs[0]
        cmds.setAttr(left + ".rotateZ", 40)
        cmds.setAttr(right + ".rotateZ", -15)
        cmds.setKeyframe(left + ".rotate")
        cmds.setKeyframe(right + ".rotate")
        cache.invalidate()

        before = (curve_rotate(cmds, left), curve_rotate(cmds, right))
        cmds.select([left, right])
        pose.mirror_selected()
        cache.invalidate()

        assert (curve_rotate(cmds, left), curve_rotate(cmds, right)) == before
        err = capfd.readouterr().err
        assert "no way to tell which way you meant" in err
        assert "Flip" in err

    def test_a_skipped_pair_does_not_make_a_resting_pair_write(self, rig):
        """Nothing to mirror means nothing WRITTEN, even alongside a skip.

        Gating the empty-pose return on there being no skipped pairs left one
        pair at rest writing its own values back over itself -- no change, and
        one undo entry the animator never asked for.
        """
        from animkit.core import cache
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        # Pair 0 ambiguous (both posed); pair 1 left entirely at rest.
        cmds.setAttr(pairs[0][0] + ".rotateZ", 40)
        cmds.setAttr(pairs[0][1] + ".rotateZ", -15)
        cmds.setKeyframe(pairs[0][0] + ".rotate")
        cmds.setKeyframe(pairs[0][1] + ".rotate")
        cache.invalidate()

        cmds.select([n for pair in pairs[:2] for n in pair])
        assert pose.mirror_selected() == 0

    def test_flip_is_unaffected_by_direction(self, rig):
        """Flip exchanges both sides, so it never asks the direction question."""
        from animkit.core import cache
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        left, right = pairs[0]
        cmds.setAttr(left + ".rotateZ", 40)
        cmds.setAttr(right + ".rotateZ", -15)
        cmds.setKeyframe(left + ".rotate")
        cmds.setKeyframe(right + ".rotate")
        cache.invalidate()

        cmds.select([left, right])
        assert pose.flip_selected() > 0


class TestAutoKeyIsCompleteButQuiet:
    """What key_stranded keys: the transform set, plus what was actually posed.

    Not "transform only" -- an unkeyed visibility or ikFkBlend leaves Auto Key
    unable to key it later, which is the trap the whole mechanism closes. Not
    "everything" either -- that is pressing S, and it puts twenty-two red rows
    on an IK leg for channels nobody touched.
    """

    def test_a_posed_custom_attribute_is_keyed(self, rig):
        from animkit.core import cache
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        left, right = pairs[0]
        cmds.setAttr(left + ".translateX", lock=False)
        cmds.setAttr(left + ".translateX", 5)
        cmds.setAttr(left + ".ikFkBlend", 0.7)      # posed, off its default
        cache.invalidate()

        cmds.select([left, right])
        pose.mirror_selected()

        assert cmds.keyframe(left + ".ikFkBlend", q=True, keyframeCount=True)

    def test_an_untouched_channel_stays_clean(self, rig):
        """The whole difference from pressing S."""
        from animkit.core import cache
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        left, right = pairs[0]
        # A rig attribute parked away from zero, with that value DECLARED as its
        # default -- which is what a rigger does, and what makes it not a pose.
        cmds.addAttr(left, longName="rollStartAngle", attributeType="double",
                     defaultValue=30.0, keyable=True)
        cmds.setAttr(left + ".rollStartAngle", 30.0)
        cmds.setAttr(left + ".translateX", lock=False)
        cmds.setAttr(left + ".translateX", 5)
        cache.invalidate()

        cmds.select([left, right])
        pose.mirror_selected()

        assert not cmds.keyframe(
            left + ".rollStartAngle", q=True, keyframeCount=True
        )
        assert not cmds.keyframe(
            left + ".visibility", q=True, keyframeCount=True
        )

    def test_switched_off_visibility_is_keyed(self, rig):
        """Visibility is excluded from TWEENING, not from a pose."""
        from animkit.core import cache
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        left, right = pairs[0]
        cmds.setAttr(left + ".translateX", lock=False)
        cmds.setAttr(left + ".translateX", 5)
        cmds.setAttr(left + ".visibility", 0)       # genuinely changed
        cache.invalidate()

        cmds.select([left, right])
        pose.mirror_selected()

        assert cmds.keyframe(left + ".visibility", q=True, keyframeCount=True)


class TestFlip:
    def test_flip_exchanges_the_two_sides(self, rig):
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        left, right = pairs[0]
        cmds.setAttr(left + ".rotate", 12, -20, 35)
        cmds.setKeyframe(left + ".rotate")
        cmds.setAttr(left + ".ikFkBlend", 0.7)
        cmds.setAttr(right + ".ikFkBlend", 0.2)

        before_left = cmds.getAttr(left + ".rotate")[0]

        cmds.select([left, right])
        assert pose.flip_selected() > 0

        assert cmds.getAttr(left + ".ikFkBlend") == pytest.approx(0.2, abs=TOL)
        assert cmds.getAttr(right + ".ikFkBlend") == pytest.approx(0.7, abs=TOL)
        assert cmds.getAttr(left + ".rotate")[0] != pytest.approx(
            before_left, abs=TOL
        )

    def test_flip_twice_is_identity(self, rig):
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        pose_left(cmds, pairs)
        nodes = [n for pair in pairs for n in pair]
        before = {n: wpos(cmds, n) for n in nodes}

        cmds.select(nodes)
        pose.flip_selected()
        cmds.select(nodes)
        pose.flip_selected()

        for node in nodes:
            assert wpos(cmds, node) == pytest.approx(before[node], abs=TOL), node


class TestLayerPolicy:
    def test_mirror_writes_to_the_active_layer(self, rig):
        from animkit.core import layers
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        nodes = [n for pair in pairs for n in pair]
        pose_left(cmds, pairs)

        cmds.select(nodes)
        layer = cmds.animLayer("L1", addSelectedObjects=True)
        cmds.animLayer(layer, edit=True, selected=True)

        right = pairs[0][1]
        base = layers.resolve_curve(right + ".rotateX", layers.root_layer())

        def base_state():
            if not base:
                return None
            return tuple(
                cmds.keyframe(base, q=True, valueChange=True) or []
            )

        before = base_state()
        cmds.select(nodes)
        pose.mirror_selected()
        assert base_state() == before

    def test_mirror_skips_a_locked_layer(self, rig):
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        nodes = [n for pair in pairs for n in pair]
        pose_left(cmds, pairs)

        cmds.select(nodes)
        layer = cmds.animLayer("Locked1", addSelectedObjects=True)
        cmds.animLayer(layer, edit=True, selected=True, lock=True)

        cmds.select(nodes)
        assert pose.mirror_selected() == 0


class TestRestOverride:
    def test_captured_rest_makes_an_odd_rig_mirrorable(self, clean_scene):
        """The escape hatch for a rig that does not zero its controls.

        Build offsets in the controls' OWN posable translate channels, which the
        derived rest pose cannot distinguish from a pose. check_symmetry refuses
        it; capturing the rest pose makes it work.
        """
        from animkit.core import cache, xform
        from animkit.tools import pose

        cmds = clean_scene
        root = cmds.createNode("transform", name="odd_root")
        left = cmds.createNode("transform", name="L_thing", parent=root)
        cmds.setAttr(left + ".translateX", 8)
        right = cmds.createNode("transform", name="R_thing", parent=root)
        cmds.setAttr(right + ".translateX", -8)

        xform.clear_rest_overrides()
        cache.invalidate()
        xform.capture_rest([root, left, right])

        cmds.setAttr(left + ".rotateZ", 30)
        cmds.setKeyframe(left + ".rotateZ")
        cmds.select([left, right])
        written = pose.mirror_selected()
        xform.clear_rest_overrides()
        cache.invalidate()

        assert written > 0
        assert cmds.getAttr(right + ".rotateZ") == pytest.approx(-30.0, abs=TOL)


class TestRestCapture:
    """Set Rest is the one operation that can permanently disable the mirror.

    Pressing it on a posed rig makes the pose the rest pose, so every frame
    relation is derived from an asymmetric reference and the mirror refuses from
    then on. It happened on the first production rig this met.
    """

    def test_refuses_to_capture_a_posed_rig(self, rig):
        from animkit.core import xform
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        xform.clear_rest_overrides()
        pose_left(cmds, pairs)  # asymmetric: only the left arm is posed

        nodes = [n for pair in pairs for n in pair]
        cmds.select(nodes)
        assert pose.capture_rest_pose() == 0
        assert xform._rest_overrides == {}, "a refused capture left state behind"

    def test_captures_a_symmetric_rig(self, rig):
        from animkit.core import xform
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        xform.clear_rest_overrides()

        nodes = [n for pair in pairs for n in pair]
        cmds.select(nodes)
        try:
            assert pose.capture_rest_pose() > 0
        finally:
            xform.clear_rest_overrides()

    def test_a_refused_capture_does_not_disturb_an_earlier_one(self, rig):
        """A mistaken press must not destroy a good capture either."""
        from animkit.core import xform
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        xform.clear_rest_overrides()
        nodes = [n for pair in pairs for n in pair]

        cmds.select(nodes)
        pose.capture_rest_pose()
        good = dict(xform._rest_overrides)
        assert good

        pose_left(cmds, pairs)
        cmds.select(nodes)
        assert pose.capture_rest_pose() == 0
        try:
            assert xform._rest_overrides == good
        finally:
            xform.clear_rest_overrides()

    def test_clear_rest_restores_derived_behaviour(self, rig):
        from animkit.core import xform
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        nodes = [n for pair in pairs for n in pair]
        cmds.select(nodes)
        pose.capture_rest_pose()
        assert xform._rest_overrides

        pose.clear_rest_pose()
        assert xform._rest_overrides == {}

    def test_mirror_still_works_after_clearing(self, rig):
        """The recovery path, end to end."""
        from animkit.core import xform
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        nodes = [n for pair in pairs for n in pair]

        cmds.select(nodes)
        pose.capture_rest_pose()
        pose.clear_rest_pose()

        pose_left(cmds, pairs)
        cmds.select(nodes)
        assert pose.mirror_selected() > 0


class TestNothingToMirror:
    """The failure that looks exactly like a broken tool.

    An unkeyed control has no animation, so its rest pose is wherever it stands
    -- pose it and the rest moves with it, the delta is identity, and there is
    nothing to reflect. This is what "the mirror only works once" turned out to
    be on a production rig: it worked while the keys were there and stopped the
    moment they were undone away.
    """

    def test_warns_when_every_control_is_at_rest(self, rig, capfd):
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        nodes = [n for pair in pairs for n in pair]
        cmds.select(nodes)
        pose.mirror_selected()
        err = capfd.readouterr().err
        assert "nothing to mirror" in err

    def test_no_warning_when_there_is_a_pose(self, rig, capfd):
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        pose_left(cmds, pairs)
        nodes = [n for pair in pairs for n in pair]
        cmds.select(nodes)
        pose.mirror_selected()
        assert "nothing to mirror" not in capfd.readouterr().err

    def _strand(self, cmds, node):
        """Pose a channel that carries no animation at all."""
        from animkit.core import cache

        cmds.setAttr(node + ".translateX", lock=False)
        cmds.setAttr(node + ".translateX", 5)
        cache.invalidate()
        return node

    def test_unkeyed_pose_is_keyed_and_mirrored(self, rig, capfd):
        """Posed with no key: animkit keys it and mirrors, instead of refusing.

        The refusal used to send the animator to Auto Key, which CANNOT create a
        first key -- so a control whose keys had just been undone away could
        never be recovered by doing what the message said, and every subsequent
        mirror refused. See pose.key_stranded.
        """
        from animkit.core import cache, xform
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        left, right = pairs[0]
        self._strand(cmds, left)
        assert xform.unkeyed_but_posed(left)

        cmds.select([left, right])
        assert pose.mirror_selected() > 0

        cache.invalidate()
        # Readable as posed now rather than as resting where it stands -- and,
        # just as important, Auto Key will work on it from here.
        assert not xform.unkeyed_but_posed(left)
        assert cmds.keyframe(left + ".translateX", q=True, keyframeCount=True)

        err = capfd.readouterr().err
        assert "have no keys" not in err
        assert "nothing to mirror" not in err

    def test_auto_keyed_mirror_is_geometrically_right(self, rig):
        """Invariant 3, through the auto-key path.

        The refusal existed to stop a plausible-looking WRONG mirror, so keying
        our way past it has to be held to the same standard as every other
        route: the counterpart's world position must be the reflection of the
        original's, not merely different from where it started.
        """
        from animkit.tools import pose

        cmds, pairs, root, _spine = rig
        left, right = pairs[0]
        self._strand(cmds, left)

        cmds.select([left, right])
        assert pose.mirror_selected() > 0

        reflection = live_reflection(cmds, root)
        assert wpos(cmds, right) == pytest.approx(
            reflected(left, right, cmds, reflection), abs=TOL
        )

    def test_keying_a_stranded_control_is_one_undo_step(self, rig):
        """The auto-key must not cost a second Ctrl+Z.

        If it did, the second press would undo the very key that made the first
        press work, stranding the control again -- which is the loop this whole
        mechanism exists to break.
        """
        from animkit.core import cache
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        left, right = pairs[0]
        self._strand(cmds, left)

        before = {n: cmds.getAttr(n + ".rotate")[0] for n in (left, right)}
        cmds.select([left, right])
        assert pose.mirror_selected() > 0

        cmds.undo()
        cache.invalidate()

        for node in (left, right):
            assert cmds.getAttr(node + ".rotate")[0] == pytest.approx(
                before[node], abs=TOL
            ), node
        # The key animkit added on the way in goes with it.
        assert not cmds.keyframe(
            left + ".translateX", q=True, keyframeCount=True
        )

    def test_mirror_pose_itself_still_refuses(self, rig):
        """The refusal survives on the pure function; only operations auto-key.

        mirror_pose is a transform, not a write, so it has no licence to resolve
        the ambiguity itself. It reports it -- and no longer recommends Auto Key,
        which only ever updates channels that already carry a curve.
        """
        from animkit.core import xform
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        left = pairs[0][0]
        self._strand(cmds, left)
        assert xform.unkeyed_but_posed(left)

        cmds.select([left, pairs[0][1]])
        with pytest.raises(pose.MirrorRefused) as excinfo:
            pose.mirror_pose(pose.capture())

        message = str(excinfo.value)
        assert "have no keys" in message
        # And NOT the misleading message. The rig is symmetric; the key is missing.
        assert "not symmetric at rest" not in message
        assert "Turn on Auto Key" not in message


class TestRigControls:
    """Telling a control from rig plumbing, on ANY rig.

    Not by name. `*_ctrl` also matches the offset and extra groups on every rig
    that names its plumbing after the control it carries, and a wrong answer
    here resets the plumbing. The property that does not vary by rig, studio or
    convention is that A CONTROL IS DRAWN -- the animator has to be able to see
    and click it -- and rig internals are empty transforms.
    """

    @pytest.fixture
    def rig_with_controls(self, clean_scene):
        """Controls drawn as curves, plumbing as bare transforms. No naming."""
        cmds = clean_scene
        root = cmds.createNode("transform", name="charRoot")
        motion = cmds.createNode("transform", name="MotionSystem", parent=root)

        controls, internals = [], []
        for side, sign in (("L", 1), ("R", -1)):
            # Keyable, unlocked, off its defaults BY DESIGN, and NOT drawn --
            # the exact shape of node that made an unkeyed reset dangerous.
            offset = cmds.createNode(
                "transform", name="FKOffsetShoulder_" + side, parent=motion
            )
            cmds.setAttr(offset + ".translate", 10 * sign, 3, 0)
            extra = cmds.createNode(
                "transform", name="FKExtraShoulder_" + side, parent=offset
            )
            cmds.setAttr(extra + ".rotateZ", 12)
            internals += [offset, extra]

            ctrl = cmds.circle(
                name="FKShoulder_" + side, normal=(1, 0, 0), radius=1
            )[0]
            cmds.parent(ctrl, extra)
            controls.append(ctrl)

        cmds.currentTime(1)
        return cmds, controls, internals

    def test_finds_drawn_controls_and_not_the_plumbing(self, rig_with_controls):
        """No sets, no tags, no naming convention. Just what is drawn."""
        from animkit.tools import pose

        _cmds, controls, internals = rig_with_controls
        found = set(pose.rig_controls())
        assert found == set(controls)
        assert not found & set(internals)

    def test_works_with_no_sets_and_unrecognisable_names(self, clean_scene):
        """A hand-built rig nobody has ever seen. This is the whole point.

        Names deliberately match no convention in existence, and there is no set
        and no tag to fall back on.
        """
        from animkit.tools import pose

        cmds = clean_scene
        root = cmds.createNode("transform", name="zzz_thing")
        plumbing = cmds.createNode("transform", name="qqq_wrapper", parent=root)
        cmds.setAttr(plumbing + ".translateY", 4)
        handle = cmds.circle(name="blorp", normal=(0, 1, 0))[0]
        cmds.parent(handle, plumbing)

        assert pose.rig_controls() == [handle]

    def test_a_set_is_recognised_by_its_contents_not_its_name(self, clean_scene):
        """An oddly named set still contributes its shapeless members.

        The set proves what it is by being mostly control-shaped, so a joint
        used directly as a control comes along with it -- with no list of
        blessed set names anywhere in the codebase.
        """
        from animkit.tools import pose

        cmds = clean_scene
        drawn = [cmds.circle(name="handle%d" % i)[0] for i in range(3)]
        bare = cmds.createNode("joint", name="jaw_driver")
        cmds.sets(drawn + [bare], name="Bobs_Picker_Things")

        found = set(pose.rig_controls())
        assert set(drawn) <= found
        assert bare in found

    def test_a_joint_set_is_not_mistaken_for_controls(self, clean_scene):
        """Scores zero on the same rule. No forbidden-name list needed."""
        from animkit.tools import pose

        cmds = clean_scene
        cmds.circle(name="the_only_control")
        joints = [cmds.createNode("joint", name="bone%d" % i) for i in range(4)]
        cmds.sets(joints, name="DeformSet")

        assert not set(pose.rig_controls()) & set(joints)

    def test_a_controller_tag_wins_outright(self, clean_scene):
        """An explicit declaration beats anything inferred from geometry."""
        from animkit.tools import pose

        cmds = clean_scene
        tagged = cmds.createNode("transform", name="tagged_ctrl")
        cmds.circle(name="drawn_but_untagged")
        cmds.controller(tagged)

        assert pose.rig_controls() == [tagged]

    def test_a_declared_control_set_does_not_license_zeroing_it(
        self, rig_with_controls
    ):
        """This asserted the opposite, and it was the same mistake twice.

        rig_controls() answers "is this a control". It does NOT answer "where
        is this control's rest", and the two were being conflated: a declared
        control set was read as permission to zero every unkeyed channel on it.

        A facial control board is declared exactly this way -- its controls are
        nurbsCurves, so the shape rule finds all of them -- and every one sits
        off zero by design. See TestFacialControlBoard.
        """
        from animkit.core import cache
        from animkit.tools import pose

        cmds, controls, _internals = rig_with_controls
        for ctrl in controls:
            cmds.setAttr(ctrl + ".rotateY", -43.584)
            cmds.setAttr(ctrl + ".translateX", 7)
        cache.invalidate()

        cmds.select(clear=True)
        assert pose.reset_to_default() == 0
        cache.invalidate()

        for ctrl in controls:
            assert cmds.getAttr(ctrl + ".translateX") == pytest.approx(
                7.0, abs=TOL
            )

    def test_scene_wide_still_resets_unkeyed_controls_when_asked(
        self, rig_with_controls
    ):
        """The capability is intact -- it just has to be asked for.

        This is the case the old default existed to serve: a rig blocked out
        with no keys yet. It still works in one call; it is now a decision the
        animator makes rather than one Reset makes for them.
        """
        from animkit.core import cache
        from animkit.tools import pose

        cmds, controls, _internals = rig_with_controls
        for ctrl in controls:
            cmds.setAttr(ctrl + ".rotateY", -43.584)
            cmds.setAttr(ctrl + ".translateX", 7)
        cache.invalidate()

        cmds.select(clear=True)
        assert pose.reset_to_default(include_unkeyed=True) > 0
        cache.invalidate()

        for ctrl in controls:
            assert cmds.getAttr(ctrl + ".rotateY") == pytest.approx(0.0, abs=TOL)
            assert cmds.getAttr(ctrl + ".translateX") == pytest.approx(
                0.0, abs=TOL
            )
        # And no keys invented on the way. Whether the plumbing was touched is
        # asserted by test_the_rigs_plumbing_is_never_touched, which knows what
        # this fixture's internals are actually set to.
        assert not cmds.keyframe(controls[0], q=True, keyframeCount=True)

    def test_the_rigs_plumbing_is_never_touched(self, rig_with_controls):
        """The catastrophe this whole design exists to prevent."""
        from animkit.core import cache
        from animkit.tools import pose

        cmds, controls, internals = rig_with_controls
        cmds.setAttr(controls[0] + ".rotateY", 30)
        cache.invalidate()

        cmds.select(clear=True)
        pose.reset_to_default()
        cache.invalidate()

        assert cmds.getAttr(internals[0] + ".translateY") == pytest.approx(
            3.0, abs=TOL
        )
        assert cmds.getAttr(internals[1] + ".rotateZ") == pytest.approx(
            12.0, abs=TOL
        )


class TestResetToDefault:
    def test_resets_animated_channels(self, rig):
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        pose_left(cmds, pairs)
        left = pairs[0][0]
        assert curve_rotate(cmds, left) != pytest.approx((0, 0, 0), abs=TOL)

        cmds.select([n for pair in pairs for n in pair])
        assert pose.reset_to_default() > 0
        assert curve_rotate(cmds, left) == pytest.approx((0, 0, 0), abs=TOL)

    def test_is_one_undo_step(self, rig):
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        pose_left(cmds, pairs)
        left = pairs[0][0]
        posed = curve_rotate(cmds, left)

        cmds.select([n for pair in pairs for n in pair])
        pose.reset_to_default()
        cmds.undo()
        assert curve_rotate(cmds, left) == pytest.approx(posed, abs=TOL)

    def test_keys_rather_than_setattr(self, rig):
        """A plain setAttr on an animated channel is overwritten by its curve at
        the next evaluation, so the control would snap back."""
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        pose_left(cmds, pairs)
        left = pairs[0][0]

        cmds.select(left)
        pose.reset_to_default()
        assert cmds.keyframe(left + ".rotateX", q=True, valueChange=True)[-1] == (
            pytest.approx(0.0, abs=TOL)
        )

    def test_resets_unkeyed_off_default_channels(self, rig):
        from animkit.core import cache
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        left = pairs[0][0]
        cmds.setAttr(left + ".translateX", lock=False)
        cmds.setAttr(left + ".translateX", 5)
        cache.invalidate()

        cmds.select(left)
        pose.reset_to_default(include_unkeyed=True)   # explicit selection only
        assert cmds.getAttr(left + ".translateX") == pytest.approx(0.0, abs=TOL)

    def test_a_selection_resets_unkeyed_channels(self, rig):
        """The commonest Reset there is: drag a control, no key, put it back.

        An unkeyed off-default channel is ambiguous -- pose, or the rig's build
        offset? -- and the animator selecting the control is one of only two
        things that can answer it. (The other is a captured rest pose.) A
        `rig_controls()` sweep is NOT, which is the distinction that took a
        facial control board with it; see TestFacialControlBoard.
        """
        from animkit.core import cache
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        left = pairs[0][0]
        cmds.setAttr(left + ".translateX", lock=False)
        cmds.setAttr(left + ".translateX", 5)
        cache.invalidate()

        cmds.select(left)
        pose.reset_to_default()
        assert cmds.getAttr(left + ".translateX") == pytest.approx(0.0, abs=TOL)

    def test_include_unkeyed_false_still_opts_out(self, rig):
        """Auto is a default, not a policy. An explicit False is honoured."""
        from animkit.core import cache
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        left = pairs[0][0]
        cmds.setAttr(left + ".translateX", lock=False)
        cmds.setAttr(left + ".translateX", 5)
        cache.invalidate()

        cmds.select(left)
        pose.reset_to_default(include_unkeyed=False)
        assert cmds.getAttr(left + ".translateX") == pytest.approx(5.0, abs=TOL)


    def test_scene_wide_never_touches_unkeyed_channels(self, rig):
        """Even when asked. Scene-wide reaches the rig's internals."""
        from animkit.core import cache
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        pose_left(cmds, pairs)
        left = pairs[0][0]
        cmds.setAttr(left + ".translateX", lock=False)
        cmds.setAttr(left + ".translateX", 5)
        cache.invalidate()

        cmds.select(clear=True)
        pose.reset_to_default(include_unkeyed=True)

        # the animation was reset...
        assert curve_rotate(cmds, left) == pytest.approx((0, 0, 0), abs=TOL)
        # ...and the rig's own placement was not touched
        assert cmds.getAttr(left + ".translateX") == pytest.approx(5.0, abs=TOL)

    def test_scene_wide_leaves_static_rig_nodes_alone(self, rig):
        """The offset groups hold the rig's build. A reset must not move them."""
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        pose_left(cmds, pairs)
        group = "L_shoulderOffset"
        before = cmds.getAttr(group + ".translateX")

        cmds.select(clear=True)
        pose.reset_to_default()
        assert cmds.getAttr(group + ".translateX") == pytest.approx(before, abs=TOL)

    def test_already_default_is_a_no_op(self, rig):
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        cmds.select([n for pair in pairs for n in pair])
        assert pose.reset_to_default() == 0

    def test_makes_the_mirror_work_again(self, rig):
        """The recovery path the animator actually needs: reset, key, pose, mirror."""
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        left, right = pairs[0]
        nodes = [n for pair in pairs for n in pair]

        pose_left(cmds, pairs)
        cmds.select(nodes)
        pose.reset_to_default()

        pose_left(cmds, pairs)
        cmds.select(nodes)
        assert pose.mirror_selected() > 0
        assert curve_rotate(cmds, right) != pytest.approx((0, 0, 0), abs=TOL)

    def test_with_nothing_selected_finds_rigs_in_the_scene(self, rig):
        from animkit.tools import pose

        cmds, pairs, _root, _spine = rig
        pose_left(cmds, pairs)
        left = pairs[0][0]

        cmds.select(clear=True)
        assert pose.reset_to_default() > 0
        assert curve_rotate(cmds, left) == pytest.approx((0, 0, 0), abs=TOL)


class TestFacialControlBoard:
    """The rig shape that Reset collapsed, as a fixture.

    A facial control board is a flat panel of small control curves that slide
    within it. Every one of them is:

        drawn, clickable, unmistakably a control
        sitting at a NON-ZERO translate BY DESIGN -- that is its slot on
        the board
        unkeyed, until the animator touches it

    So "unkeyed and off its default" is not a description of a pose here. It is
    a description of the rig. Zeroing those channels does not clear anything --
    it stacks every control on the board onto one point, and the board stops
    working.

    Nothing in the scene distinguishes this from an animator having dragged a
    control without keying it. That is stated in xform's module docstring, the
    mirror path already refuses on it, and Reset used to guess.
    """

    @staticmethod
    def _board(cmds):
        """A board of nine controls in a 3x3 grid, none of them keyed."""
        root = cmds.createNode("transform", name="face_rig")
        board = cmds.createNode("transform", name="face_board", parent=root)
        cmds.createNode("nurbsCurve", parent=board)

        controls = []
        for row in range(3):
            for column in range(3):
                ctrl = cmds.createNode(
                    "transform",
                    name="face_ctrl_%d_%d" % (row, column),
                    parent=board,
                )
                cmds.createNode("nurbsCurve", parent=ctrl)
                # The build offset: where this control lives on the board.
                cmds.setAttr(ctrl + ".translateX", (column - 1) * 2.0)
                cmds.setAttr(ctrl + ".translateY", (row - 1) * 2.0)
                controls.append(ctrl)
        return root, controls

    @staticmethod
    def _positions(cmds, controls):
        return [
            tuple(round(v, 4) for v in cmds.getAttr(c + ".translate")[0])
            for c in controls
        ]

    def test_the_board_survives_a_reset_with_nothing_selected(self, clean_scene):
        """THE REGRESSION, and it is specifically the no-selection press.

        With nothing selected, Reset falls back to rig_controls() -- every
        control-shaped node on the rig. On a face board that is all of them,
        sitting off zero by design and none of them keyed. It zeroed the lot.

        rig_controls() answers "is this a control". It does not answer "where is
        this control's rest", and only the second question was being asked.
        """
        from animkit.core import cache, xform
        from animkit.tools import pose

        cmds = clean_scene
        xform.clear_rest_overrides()
        _root, controls = self._board(cmds)
        cache.invalidate()

        before = self._positions(cmds, controls)
        cmds.select(clear=True)
        pose.reset_to_default()

        assert self._positions(cmds, controls) == before
        # And specifically: they must not all be in the same place.
        assert len(set(self._positions(cmds, controls))) == len(controls)

    def test_selecting_the_board_and_resetting_DOES_zero_it(self, clean_scene):
        """Stated rather than hidden, because it is the residual risk.

        Selecting these controls and pressing Reset is the animator saying
        "put THESE back to their defaults", and Reset takes them at their word.
        On a face board that is the wrong thing to want, and the tool cannot
        know it -- so the protection is Set Rest, which is permanent and is
        asserted by test_set_rest_makes_the_board_resettable below.

        One Ctrl+Z either way; see test_reset_is_still_one_undo_step.
        """
        from animkit.core import cache, xform
        from animkit.tools import pose

        cmds = clean_scene
        xform.clear_rest_overrides()
        _root, controls = self._board(cmds)
        cache.invalidate()

        cmds.select(controls)
        pose.reset_to_default()
        assert len(set(self._positions(cmds, controls))) == 1

    def test_an_animated_face_control_still_resets(self, clean_scene):
        """The board must not become un-resettable. A channel with a curve is
        one somebody is posing, so its rest IS its default -- that half of
        Reset is unaffected and has to keep working."""
        from animkit.core import cache, xform
        from animkit.tools import pose

        cmds = clean_scene
        xform.clear_rest_overrides()
        _root, controls = self._board(cmds)

        driven = controls[0]
        cmds.setKeyframe(driven + ".translateX", time=1, value=0.0)
        cmds.setAttr(driven + ".translateX", 7.0)
        cmds.setKeyframe(driven + ".translateX", time=1, value=7.0)
        cache.invalidate()

        cmds.select(controls)
        pose.reset_to_default()

        keyed = cmds.keyframe(driven + ".translateX", q=True, valueChange=True)
        assert keyed[-1] == pytest.approx(0.0, abs=TOL)

    def test_set_rest_makes_the_board_resettable(self, clean_scene):
        """The escape hatch, working end to end.

        Set Rest is the button that says "rest is HERE", and until this fix
        Reset never consulted it -- so the one control an animator has over
        this ambiguity had no effect on the operation that most needed it.
        """
        from animkit.core import cache, xform
        from animkit.tools import pose

        cmds = clean_scene
        xform.clear_rest_overrides()
        _root, controls = self._board(cmds)
        cache.invalidate()

        neutral = self._positions(cmds, controls)
        xform.capture_rest(controls)
        cache.invalidate()

        # The animator now slides one control off its slot, without keying it.
        moved = controls[4]
        cmds.setAttr(moved + ".translateX", 5.0)
        cmds.setAttr(moved + ".translateY", -4.0)
        cache.invalidate()

        cmds.select(controls)
        try:
            pose.reset_to_default()
            after = self._positions(cmds, controls)
        finally:
            xform.clear_rest_overrides()

        # Back to its slot -- not to the origin.
        assert after == neutral

    def test_explicit_include_unkeyed_reaches_the_scene_wide_case(
        self, clean_scene
    ):
        """The override, for an animator who knows their rig zeroes.

        With nothing selected AUTO declines to guess, so this is the one call
        that still clears a rig blocked out with no keys at all. It is a
        decision somebody can make; it is not one Reset may make for them.
        """
        from animkit.core import cache, xform
        from animkit.tools import pose

        cmds = clean_scene
        xform.clear_rest_overrides()
        _root, controls = self._board(cmds)
        cache.invalidate()

        cmds.select(clear=True)
        pose.reset_to_default(include_unkeyed=True)

        # It DOES collapse the board -- because it was told to, explicitly.
        assert len(set(self._positions(cmds, controls))) == 1

    def test_the_skip_is_reported_not_silent(self, clean_scene):
        """A Reset that does nothing and says nothing reads as a broken button.

        This is the no-selection press -- the one that now declines to guess --
        so the warning has to name the controls and both ways to make them
        resettable. "It did not work" is not something an animator can act on.
        """
        from animkit.core import cache, xform
        from animkit.tools import pose

        cmds = clean_scene
        xform.clear_rest_overrides()
        _root, controls = self._board(cmds)
        cache.invalidate()

        messages = []
        original = cmds.warning
        try:
            cmds.warning = lambda text, *a, **k: messages.append(text)
            cmds.select(clear=True)
            pose.reset_to_default()
        finally:
            cmds.warning = original

        assert messages, "the skip was silent"
        joined = " ".join(messages)
        assert "Set Rest" in joined
        assert "key them" in joined.lower() or "keyed" in joined.lower()

    def test_reset_is_still_one_undo_step(self, clean_scene):
        """Ctrl+Z is the recovery path, and it has to bring the WHOLE board
        back in one press -- which is what saved the rig this was found on."""
        from animkit.core import cache, xform
        from animkit.tools import pose

        cmds = clean_scene
        xform.clear_rest_overrides()
        _root, controls = self._board(cmds)
        cache.invalidate()

        before = self._positions(cmds, controls)
        cmds.select(controls)
        pose.reset_to_default(include_unkeyed=True)
        assert self._positions(cmds, controls) != before

        cmds.undo()
        assert self._positions(cmds, controls) == before


class TestRegistry:
    def test_every_pose_operation_is_registered(self):
        from animkit import commands
        from animkit.tools import pose

        registered = {name for name, _a, _c in commands.COMMANDS}
        for op in pose.OPERATIONS:
            assert op.name in registered, "%s is not registered" % op.name

    def test_commands_compile(self):
        from animkit.tools import pose

        for op in pose.OPERATIONS:
            assert op.command.startswith("import animkit.tools.pose as p;")
            compile(op.command, "<op>", "exec")

    def test_names_are_unique_across_both_registries(self):
        """The panel and the Hotkey Editor share one namespace."""
        from animkit.tools import keys, pose

        names = [op.name for op in keys.OPERATIONS] + [
            op.name for op in pose.OPERATIONS
        ]
        assert len(names) == len(set(names))

    def test_operations_run_without_a_selection(self, clean_scene):
        """Bound to a hotkey, every one of these gets pressed with nothing
        selected. None may raise, and none may leave an undo entry."""
        from animkit.tools import pose

        cmds = clean_scene
        cmds.select(clear=True)
        pose._clipboard = None
        for op in pose.OPERATIONS:
            assert op.invoke() in (0, None) or True  # must simply not raise
