"""Radial menu geometry. Fast tier -- no Maya, no Qt.

This is the only part of the radial that can be wrong rather than merely ugly,
and it is the part that cannot be tested through the widget: a frameless,
always-on-top overlay driven by a held hotkey does not submit to a test
harness. So it lives on its own and is pinned here.
"""

import math

import pytest

from animkit.ui import radial_geom as geom


class TestWedgeAt:
    def test_straight_up_is_item_zero(self):
        """Item 0 at 12 o'clock, matching Maya's marking menus."""
        assert geom.wedge_at(0, -100, 4) == 0
        assert geom.wedge_at(0, -100, 8) == 0

    def test_items_run_clockwise(self):
        """The direction the hands already know. Backwards is invisible on a
        symmetric layout, which is the layout you would test with."""
        assert geom.wedge_at(100, 0, 4) == 1     # east
        assert geom.wedge_at(0, 100, 4) == 2     # south
        assert geom.wedge_at(-100, 0, 4) == 3    # west

    def test_screen_y_points_down(self):
        """dy is DOWN, as Qt hands it over. Getting this backwards mirrors the
        whole menu vertically."""
        assert geom.wedge_at(0, -100, 2) == 0    # up
        assert geom.wedge_at(0, 100, 2) == 1     # down

    def test_eight_way_compass(self):
        r = 100
        d = r / math.sqrt(2)
        assert geom.wedge_at(0, -r, 8) == 0      # N
        assert geom.wedge_at(d, -d, 8) == 1      # NE
        assert geom.wedge_at(r, 0, 8) == 2       # E
        assert geom.wedge_at(d, d, 8) == 3       # SE
        assert geom.wedge_at(0, r, 8) == 4       # S
        assert geom.wedge_at(-d, d, 8) == 5      # SW
        assert geom.wedge_at(-r, 0, 8) == 6      # W
        assert geom.wedge_at(-d, -d, 8) == 7     # NW

    def test_the_centre_is_cancel(self):
        """A radial fires on key release, so "I changed my mind" has to be
        expressible, and not moving is the only gesture available."""
        assert geom.wedge_at(0, 0, 8) is None
        assert geom.wedge_at(5, 5, 8) is None

    def test_just_outside_the_dead_zone_picks(self):
        assert geom.wedge_at(0, -(geom.DEAD_ZONE + 1), 8) == 0

    def test_dead_zone_is_configurable(self):
        assert geom.wedge_at(0, -40, 8, dead_zone=100) is None
        assert geom.wedge_at(0, -40, 8, dead_zone=10) == 0

    def test_no_items_picks_nothing(self):
        assert geom.wedge_at(0, -100, 0) is None

    def test_one_item_covers_the_whole_ring(self):
        for dx, dy in ((0, -100), (100, 0), (0, 100), (-100, 0)):
            assert geom.wedge_at(dx, dy, 1) == 0

    def test_every_direction_lands_on_a_real_wedge(self):
        """No angle may fall through, and none may index past the end."""
        for count in range(1, 13):
            for degrees in range(0, 360):
                radians = math.radians(degrees)
                dx = 100 * math.sin(radians)
                dy = -100 * math.cos(radians)
                index = geom.wedge_at(dx, dy, count)
                assert index is not None
                assert 0 <= index < count

    def test_the_boundary_between_two_wedges_does_not_skip(self):
        """Exactly on a boundary must pick one of the two neighbours."""
        count = 6
        step = 360.0 / count
        for i in range(count):
            radians = math.radians(i * step + step / 2.0)
            dx = 100 * math.sin(radians)
            dy = -100 * math.cos(radians)
            assert geom.wedge_at(dx, dy, count) in (i, (i + 1) % count)


class TestRoundTrip:
    def test_a_wedge_centre_selects_its_own_wedge(self):
        """The property that matters: point at a label, get that item.

        Anything else means the drawing and the picking disagree, which reads
        as a radial that fires the wrong command about a sixth of the time.
        """
        for count in range(1, 13):
            for index in range(count):
                dx, dy = geom.wedge_centre(index, count, 100.0)
                assert geom.wedge_at(dx, dy, count) == index

    def test_centres_sit_on_the_requested_radius(self):
        for index in range(8):
            dx, dy = geom.wedge_centre(index, 8, 120.0)
            assert math.hypot(dx, dy) == pytest.approx(120.0)

    def test_layout_returns_one_point_per_item(self):
        assert len(geom.layout(5, 90.0)) == 5
        assert geom.layout(0, 90.0) == []


class TestWedgeSpan:
    def test_the_top_wedge_straddles_twelve_oclock(self):
        """Qt measures counter-clockwise from east; this module measures
        clockwise from north. One subtraction, in one place."""
        start, span = geom.wedge_span(0, 4)
        assert span == 90.0
        assert start == 45.0          # 45 deg -> 135 deg spans the top

    def test_spans_tile_the_circle_exactly(self):
        for count in range(1, 13):
            total = sum(geom.wedge_span(i, count)[1] for i in range(count))
            assert total == pytest.approx(360.0)

    def test_consecutive_wedges_touch(self):
        count = 7
        for i in range(count - 1):
            start_a, span_a = geom.wedge_span(i, count)
            start_b, _span_b = geom.wedge_span(i + 1, count)
            assert start_a - span_a == pytest.approx(start_b)

    def test_no_items_is_not_an_error(self):
        assert geom.wedge_span(0, 0) == (0.0, 0.0)


class TestOuterRadius:
    def test_a_small_menu_stays_small(self):
        assert geom.outer_radius(3) == geom.outer_radius(4)

    def test_it_grows_with_the_item_count(self):
        assert geom.outer_radius(8) > geom.outer_radius(4)

    def test_it_never_collapses(self):
        assert geom.outer_radius(0) > 0
