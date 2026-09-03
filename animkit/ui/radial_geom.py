"""Radial menu geometry. No Qt, no Maya, no state.

Separated from the widget on purpose. "Which wedge is the cursor over" is the
only part of a radial menu that can be WRONG rather than merely ugly, and it is
the part that is impossible to test through a frameless always-on-top overlay
driven by a held hotkey. Here it is nine lines of trigonometry that the fast
test tier covers in milliseconds.

Conventions, stated because every one of them is a plausible way to be wrong:

    SCREEN COORDINATES. dx is right, dy is DOWN, because that is what Qt and
    every windowing system hand you. Getting this backwards mirrors the menu
    vertically and is invisible on a symmetric layout -- which is exactly the
    layout you test with.

    ITEM 0 IS AT THE TOP, and items run CLOCKWISE from there. That is what
    Maya's own marking menus do, so it is what the hands already know.

    THE CENTRE IS CANCEL. Releasing without moving must do nothing: a radial
    fires on key release, so "I changed my mind" has to be expressible, and the
    only gesture available is "did not move". A dead zone in the middle is that
    gesture.
"""

import math

#: Radius of the cancel zone, in pixels. Generous on purpose. The alternative
#: to releasing inside it is releasing on a wedge you did not want, and the
#: cost of the two mistakes is not remotely symmetric.
DEAD_ZONE = 26.0

#: Where wedge labels sit, as a fraction of the outer radius.
LABEL_RADIUS = 0.66


def wedge_at(dx, dy, count, dead_zone=DEAD_ZONE):
    """Which wedge the cursor at (dx, dy) from the centre is over, or None.

    None means cancel -- inside the dead zone, or there is nothing to pick.
    """
    if count <= 0:
        return None
    if math.hypot(dx, dy) < dead_zone:
        return None

    # atan2(dx, -dy): zero pointing up the screen, increasing clockwise. The
    # argument order is not a typo -- it is what rotates the frame a quarter
    # turn so that "up" is zero instead of "right".
    angle = math.atan2(dx, -dy)
    if angle < 0.0:
        angle += 2.0 * math.pi

    step = 2.0 * math.pi / count
    return int(round(angle / step)) % count


def wedge_centre(index, count, radius):
    """(dx, dy) of a wedge's middle, for placing its label."""
    if count <= 0:
        return 0.0, 0.0
    angle = index * (2.0 * math.pi / count)
    return radius * math.sin(angle), -radius * math.cos(angle)


def wedge_span(index, count):
    """(start, span) in DEGREES for Qt's drawPie: counter-clockwise from east.

    Qt measures angles counter-clockwise from 3 o'clock; this module measures
    them clockwise from 12 o'clock. The conversion is one subtraction, and
    doing it here rather than in the paint code keeps the widget free of any
    geometry it could get subtly wrong.
    """
    if count <= 0:
        return 0.0, 0.0
    step = 360.0 / count
    return 90.0 - index * step - step / 2.0, step


def layout(count, radius):
    """[(dx, dy)] label positions for every wedge, item 0 first."""
    return [wedge_centre(i, count, radius) for i in range(count)]


def outer_radius(count, minimum=96.0, per_item=11.0):
    """How big the menu has to be to fit `count` labels without crowding.

    A radial with three items should not be the size of one with eight. The
    growth is deliberately slow: past about eight items a radial is the wrong
    control anyway, and making it enormous does not fix that.
    """
    return max(minimum, minimum + per_item * max(0, count - 4))
