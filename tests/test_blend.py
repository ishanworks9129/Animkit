"""Blend maths. Runs under plain CPython -- no Maya, no mayapy, milliseconds.

    python -m pytest tests/test_blend.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from animkit.tools import blend  # noqa: E402

PREV, CUR, NEXT = 0.0, 10.0, 100.0


class TestBetween:
    """0 must hold the pose exactly. This is the one animators notice."""

    def test_zero_is_identity(self):
        assert blend.blend(blend.MODE_BETWEEN, CUR, PREV, NEXT, 0.0) == CUR

    def test_full_right_lands_on_next_key(self):
        assert blend.blend(blend.MODE_BETWEEN, CUR, PREV, NEXT, 1.0) == NEXT

    def test_full_left_lands_on_prev_key(self):
        assert blend.blend(blend.MODE_BETWEEN, CUR, PREV, NEXT, -1.0) == PREV

    def test_half_is_halfway(self):
        got = blend.blend(blend.MODE_BETWEEN, CUR, PREV, NEXT, 0.5)
        assert got == pytest.approx(CUR + (NEXT - CUR) * 0.5)

    def test_overshoot_extrapolates_past_the_neighbour(self):
        got = blend.blend(blend.MODE_BETWEEN, CUR, PREV, NEXT, 1.5)
        assert got > NEXT


class TestLinear:
    """tweenMachine semantics: the current pose is irrelevant."""

    def test_zero_is_the_midpoint_not_the_pose(self):
        got = blend.blend(blend.MODE_LINEAR, CUR, PREV, NEXT, 0.0)
        assert got == pytest.approx((PREV + NEXT) * 0.5)

    def test_ends_land_on_the_keys(self):
        assert blend.blend(blend.MODE_LINEAR, CUR, PREV, NEXT, -1.0) == PREV
        assert blend.blend(blend.MODE_LINEAR, CUR, PREV, NEXT, 1.0) == NEXT

    def test_ignores_current_value(self):
        a = blend.blend(blend.MODE_LINEAR, 5.0, PREV, NEXT, 0.25)
        b = blend.blend(blend.MODE_LINEAR, 9999.0, PREV, NEXT, 0.25)
        assert a == b


class TestAverage:
    def test_zero_is_identity(self):
        assert blend.blend(blend.MODE_AVERAGE, CUR, PREV, NEXT, 0.0) == CUR

    def test_full_lands_on_the_average(self):
        got = blend.blend(blend.MODE_AVERAGE, CUR, PREV, NEXT, 1.0)
        assert got == pytest.approx((PREV + NEXT) * 0.5)

    def test_direction_does_not_matter(self):
        a = blend.blend(blend.MODE_AVERAGE, CUR, PREV, NEXT, 0.7)
        b = blend.blend(blend.MODE_AVERAGE, CUR, PREV, NEXT, -0.7)
        assert a == pytest.approx(b)


class TestEase:
    def test_zero_is_identity(self):
        assert blend.blend(blend.MODE_EASE, CUR, PREV, NEXT, 0.0) == CUR

    def test_ends_match_between(self):
        for t in (-1.0, 1.0):
            assert blend.blend(blend.MODE_EASE, CUR, PREV, NEXT, t) == pytest.approx(
                blend.blend(blend.MODE_BETWEEN, CUR, PREV, NEXT, t)
            )

    def test_eases_in_below_the_linear_ramp(self):
        # smoothstep sits under the straight line in the first half
        eased = blend.blend(blend.MODE_EASE, CUR, PREV, NEXT, 0.25)
        linear = blend.blend(blend.MODE_BETWEEN, CUR, PREV, NEXT, 0.25)
        assert eased < linear


class TestBoundaryKeys:
    """First and last key: the slider must have no travel, not jump."""

    def test_no_next_key_means_no_rightward_travel(self):
        got = blend.blend(blend.MODE_BETWEEN, CUR, PREV, None, 1.0)
        assert got == CUR

    def test_no_prev_key_means_no_leftward_travel(self):
        got = blend.blend(blend.MODE_BETWEEN, CUR, None, NEXT, -1.0)
        assert got == CUR

    def test_isolated_key_never_moves(self):
        for mode in blend.MODES:
            for t in (-1.0, -0.5, 0.0, 0.5, 1.0):
                assert blend.blend(mode, CUR, None, None, t) == pytest.approx(CUR)


class TestNoCompounding:
    """The bug the snapshot design exists to prevent.

    Repeatedly blending from the PREVIOUS RESULT accelerates away; blending
    from the snapshot every time is stable. This test pins the property that
    TweenSession.update() relies on.
    """

    def test_snapshot_blend_is_idempotent(self):
        once = blend.blend(blend.MODE_BETWEEN, CUR, PREV, NEXT, 0.5)
        twice = blend.blend(blend.MODE_BETWEEN, CUR, PREV, NEXT, 0.5)
        assert once == twice

    def test_compounding_would_drift(self):
        # Demonstrates why we must NOT feed the result back in.
        naive = CUR
        for _ in range(5):
            naive = blend.blend(blend.MODE_BETWEEN, naive, PREV, NEXT, 0.5)
        stable = blend.blend(blend.MODE_BETWEEN, CUR, PREV, NEXT, 0.5)
        assert naive != pytest.approx(stable)


@pytest.mark.parametrize("mode", blend.MODES)
def test_every_mode_has_a_label_and_tooltip(mode):
    assert blend.MODE_LABELS.get(mode)
    assert blend.MODE_TOOLTIPS.get(mode)
