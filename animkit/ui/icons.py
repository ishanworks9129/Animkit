"""Vector icons for every operation. Drawn in code, shipped as no files at all.

WHY DRAWN AND NOT PNGs
----------------------
Three reasons, in order of how much they matter:

  1. A studio install is a `.mod` file and a folder. Adding a `resources/`
     directory of 23 PNGs means the install can now be half-done -- code copied,
     icons missed -- and the symptom is a panel of blank squares that still
     works, which nobody reports as a bug.

  2. DPI. A 16px PNG is a blurry 16px PNG on a 4K monitor. A painted path is
     sharp at any size, and `icon()` takes the size it is asked for.

  3. Theme. The same path is drawn in the group's accent colour, dimmed when
     disabled, and white on a hover state, from one definition. With bitmaps
     that is three files per icon.

WHAT AN ICON HAS TO DO HERE
---------------------------
Be recognisable at 16 logical pixels, which is smaller than it sounds. That
rules out anything with interior detail, so each of these is two or three
strokes on a notional 100x100 grid: the shape of the OPERATION rather than a
picture of the thing it acts on. `Faster` is two arrows closing, `Slower` is two
arrows opening, and neither is a clock.

Every icon is drawn into a QImage, NOT a QPixmap. QPixmap needs a QGuiApplication
and QImage does not, so the icon set is renderable -- and therefore testable, and
reviewable as a contact sheet -- under plain `mayapy` with no UI at all.
"""

import logging

from animkit.ui import style
from animkit.vendor import qt

QtCore = qt.QtCore
QtGui = qt.QtGui

log = logging.getLogger(__name__)

#: Everything is drawn on this notional grid and scaled to the requested size,
#: so one definition serves 16px in a panel and 48px in a radial wedge.
GRID = 100.0

#: Stroke width on the grid. Heavy enough to survive being scaled down to 16px.
STROKE = 9.0


# --- drawing helpers --------------------------------------------------------


def _pen(painter, colour, width=STROKE, cap=None):
    pen = QtGui.QPen(QtGui.QColor(colour))
    pen.setWidthF(width)
    pen.setCapStyle(cap or QtCore.Qt.RoundCap)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(QtCore.Qt.NoBrush)
    return pen


def _line(painter, x1, y1, x2, y2):
    painter.drawLine(QtCore.QPointF(x1, y1), QtCore.QPointF(x2, y2))


def _path(painter, points, close=False):
    path = QtGui.QPainterPath(QtCore.QPointF(*points[0]))
    for point in points[1:]:
        path.lineTo(QtCore.QPointF(*point))
    if close:
        path.closeSubpath()
    painter.drawPath(path)


def _curve(painter, start, control_a, control_b, end):
    path = QtGui.QPainterPath(QtCore.QPointF(*start))
    path.cubicTo(
        QtCore.QPointF(*control_a),
        QtCore.QPointF(*control_b),
        QtCore.QPointF(*end),
    )
    painter.drawPath(path)


def _arrow_head(painter, tip_x, tip_y, direction, size=18.0):
    """A filled triangle pointing left (-1) or right (+1)."""
    painter.setBrush(painter.pen().color())
    painter.setPen(QtCore.Qt.NoPen)
    poly = QtGui.QPolygonF([
        QtCore.QPointF(tip_x, tip_y),
        QtCore.QPointF(tip_x - direction * size, tip_y - size * 0.62),
        QtCore.QPointF(tip_x - direction * size, tip_y + size * 0.62),
    ])
    painter.drawPolygon(poly)


def _dot(painter, x, y, radius=8.0):
    painter.setBrush(painter.pen().color())
    painter.setPen(QtCore.Qt.NoPen)
    painter.drawEllipse(QtCore.QPointF(x, y), radius, radius)


def _key(painter, x, y, size=11.0):
    """A keyframe: Maya draws them as diamonds, so these do too."""
    painter.setBrush(painter.pen().color())
    painter.setPen(QtCore.Qt.NoPen)
    poly = QtGui.QPolygonF([
        QtCore.QPointF(x, y - size),
        QtCore.QPointF(x + size, y),
        QtCore.QPointF(x, y + size),
        QtCore.QPointF(x - size, y),
    ])
    painter.drawPolygon(poly)


# --- timing -----------------------------------------------------------------


def _offset_back(p, c):
    _pen(p, c)
    _line(p, 74, 50, 34, 50)
    _arrow_head(p, 26, 50, -1)
    _pen(p, c, STROKE * 0.7)
    _line(p, 82, 22, 82, 78)


def _offset_forward(p, c):
    _pen(p, c)
    _line(p, 26, 50, 66, 50)
    _arrow_head(p, 74, 50, 1)
    _pen(p, c, STROKE * 0.7)
    _line(p, 18, 22, 18, 78)


def _retime_faster(p, c):
    """Two arrows closing on the centre. Spacing halves."""
    _pen(p, c)
    _line(p, 12, 50, 34, 50)
    _arrow_head(p, 42, 50, 1)
    _line(p, 88, 50, 66, 50)
    _arrow_head(p, 58, 50, -1)
    _pen(p, c, STROKE * 0.6)
    _line(p, 50, 20, 50, 80)


def _retime_slower(p, c):
    """Two arrows opening from the centre. Spacing doubles."""
    _pen(p, c)
    _line(p, 42, 50, 20, 50)
    _arrow_head(p, 12, 50, -1)
    _line(p, 58, 50, 80, 50)
    _arrow_head(p, 88, 50, 1)
    _pen(p, c, STROKE * 0.6)
    _line(p, 50, 20, 50, 80)


def _snap(p, c):
    """A key landing ON a whole frame. The arrow is what makes it an action."""
    _pen(p, c, STROKE * 0.45)
    for x in (20, 50, 80):
        _line(p, x, 14, x, 86)
    _pen(p, c, STROKE * 0.75)
    _line(p, 36, 50, 44, 50)
    _arrow_head(p, 52, 50, 1, 12)
    _pen(p, c)
    _key(p, 66, 50, 13)


# --- tangents ---------------------------------------------------------------
#
# Each of these is the SHAPE OF THE CURVE the tangent produces, between two
# keys. That is the only thing that distinguishes them from each other, and it
# is what an animator is actually picturing when they reach for the button.


def _handle(p, c, x, y, dx, dy):
    """The tangent handle on a key: a thin bar through it.

    This is the whole reason the four tangent icons are now telling apart. The
    curve shape between two keys is nearly the same for auto, spline and flat
    at 16 pixels; the ANGLE OF THE HANDLE is not.
    """
    _pen(p, c, STROKE * 0.5)
    _line(p, x - dx, y - dy, x + dx, y + dy)
    _dot(p, x - dx, y - dy, 5.0)
    _dot(p, x + dx, y + dy, 5.0)


def _tangent_auto(p, c):
    """Smooth, and flattened at the extreme -- which is what auto actually does."""
    _pen(p, c, STROKE * 0.9)
    _curve(p, (10, 78), (34, 78), (36, 30), (58, 30))
    _curve(p, (58, 30), (76, 30), (74, 52), (92, 52))
    _handle(p, c, 58, 30, 20, 0)
    _pen(p, c)
    _key(p, 58, 30, 10)


def _tangent_spline(p, c):
    """Smooth, and the handle follows the curve -- so it overshoots."""
    _pen(p, c, STROKE * 0.9)
    _curve(p, (10, 82), (32, 82), (36, 40), (56, 34))
    _curve(p, (56, 34), (78, 28), (74, 20), (92, 18))
    _handle(p, c, 56, 34, 19, -6)
    _pen(p, c)
    _key(p, 56, 34, 10)


def _tangent_linear(p, c):
    """Straight in, straight out, and a corner you can see."""
    _pen(p, c, STROKE * 0.9)
    _path(p, [(10, 84), (52, 32), (92, 62)])
    _pen(p, c)
    _key(p, 52, 32, 10)


def _tangent_flat(p, c):
    """The handle is HORIZONTAL. That is the entire definition."""
    _pen(p, c, STROKE * 0.9)
    _curve(p, (10, 80), (34, 80), (34, 34), (54, 34))
    _curve(p, (54, 34), (74, 34), (72, 74), (92, 74))
    _handle(p, c, 54, 34, 26, 0)
    _pen(p, c)
    _key(p, 54, 34, 10)


def _tangent_stepped(p, c):
    _pen(p, c)
    _path(p, [(14, 76), (52, 76), (52, 28), (90, 28)])
    _key(p, 14, 76)
    _key(p, 52, 28)


def _hold(p, c):
    """Flatten the target key back onto the PREVIOUS key's value.

    Two keys, the second dragged down onto the first's level. The arrow is the
    operation; the dashed target line is where it lands.
    """
    _pen(p, c, STROKE * 0.55)
    _line(p, 20, 36, 88, 36)
    _pen(p, c)
    _key(p, 22, 36, 10)
    _key(p, 78, 76, 10)
    _pen(p, c, STROKE * 0.75)
    _line(p, 78, 64, 78, 52)
    _arrow_head(p, 78, 44, 1, 14)
    p.save()
    p.translate(78, 44)
    p.rotate(-90)
    p.translate(-78, -44)
    p.restore()


# --- cycle ------------------------------------------------------------------


def _wave(p, x0, width, height=22.0, y=50.0):
    """One period of the motion, for the cycle family."""
    _curve(p, (x0, y + height), (x0 + width * 0.28, y - height * 1.5),
           (x0 + width * 0.72, y + height * 1.5), (x0 + width, y - height))


#: Nothing may be drawn outside this. A round-capped stroke of width STROKE
#: centred on the boundary spills half its width past it and is CLIPPED, and a
#: clipped icon reads as cut off rather than as wrong -- so it survives a
#: glance at the panel and ships. test_icons_stay_inside_their_box enforces it.
INSET = 8.0


def _cycle(p, c):
    """The SAME motion repeating. Not a circular arrow -- see _reset.

    Three of these were circular arrows and the contact sheet showed all three
    as one picture. What a cycle actually is, to an animator, is the curve
    happening again before and after the keys they set, so that is what it
    draws: the real period solid, the repeats faded.
    """
    _pen(p, c, STROKE * 0.55)
    _wave(p, INSET, 28)
    _wave(p, 64, 28)
    _pen(p, c, STROKE * 1.0)
    _wave(p, 36, 28)


def _cycle_offset(p, c):
    """The same, accumulating -- so each period starts where the last ended."""
    _pen(p, c, STROKE * 0.55)
    p.save()
    p.translate(0, 18)
    _wave(p, INSET, 28, height=14)
    p.restore()
    p.save()
    p.translate(0, -18)
    _wave(p, 64, 28, height=14)
    p.restore()
    _pen(p, c, STROKE * 1.0)
    _wave(p, 36, 28, height=14)


def _cycle_clear(p, c):
    """Hold the ends: the motion happens once, and the ends run flat."""
    _pen(p, c, STROKE * 0.9)
    _line(p, INSET, 70, 28, 70)
    _line(p, 72, 30, 100 - INSET, 30)
    _pen(p, c, STROKE * 1.0)
    _wave(p, 28, 44, height=16, y=50)
    _pen(p, c)
    _key(p, 28, 70, 9)
    _key(p, 72, 30, 9)


# --- edit -------------------------------------------------------------------


def _delete(p, c):
    _pen(p, c)
    _line(p, 26, 26, 74, 74)
    _line(p, 74, 26, 26, 74)


# --- pose -------------------------------------------------------------------


def _copy(p, c):
    _pen(p, c, STROKE * 0.8)
    p.drawRoundedRect(QtCore.QRectF(18, 18, 44, 44), 6, 6)
    p.drawRoundedRect(QtCore.QRectF(38, 38, 44, 44), 6, 6)


def _paste(p, c):
    _pen(p, c, STROKE * 0.8)
    p.drawRoundedRect(QtCore.QRectF(22, 26, 56, 58), 6, 6)
    _pen(p, c, STROKE * 0.9)
    _line(p, 38, 20, 62, 20)


def _paste_mirrored(p, c):
    _pen(p, c, STROKE * 0.8)
    p.drawRoundedRect(QtCore.QRectF(52, 26, 40, 58), 6, 6)
    _pen(p, c, STROKE * 0.6)
    _line(p, 34, 14, 34, 90)
    _pen(p, c, STROKE * 0.8)
    _line(p, 26, 55, 12, 55)
    _arrow_head(p, 6, 55, -1, 13)


def _mirror(p, c):
    """A shape, the plane, and the same shape reflected."""
    _pen(p, c, STROKE * 0.6)
    _line(p, 50, 10, 50, 90)
    _pen(p, c, STROKE * 0.9)
    _path(p, [(38, 26), (14, 50), (38, 74)], close=True)
    _path(p, [(62, 26), (86, 50), (62, 74)], close=True)


def _flip(p, c):
    """Mirror's two shapes, EXCHANGING. Filled, so it reads at 16px.

    Same visual language as _mirror on purpose -- they are the same operation
    with and without a swap -- but the shapes have crossed over, and the arrows
    say which way.
    """
    _pen(p, c, STROKE * 0.5)
    _line(p, 50, 6, 50, 94)
    _pen(p, c, STROKE * 0.9)
    p.setBrush(QtGui.QColor(c))
    poly_left = QtGui.QPolygonF([
        QtCore.QPointF(40, 14), QtCore.QPointF(14, 34), QtCore.QPointF(40, 54),
    ])
    poly_right = QtGui.QPolygonF([
        QtCore.QPointF(60, 46), QtCore.QPointF(86, 66), QtCore.QPointF(60, 86),
    ])
    p.drawPolygon(poly_left)
    p.drawPolygon(poly_right)
    _pen(p, c, STROKE * 0.6)
    _line(p, 58, 24, 78, 24)
    _arrow_head(p, 86, 24, 1, 11)
    _line(p, 42, 76, 22, 76)
    _arrow_head(p, 14, 76, -1, 11)


def _capture_rest(p, c):
    """Pin the current pose down as the reference."""
    _pen(p, c, STROKE * 0.85)
    _line(p, 50, 16, 50, 62)
    _dot(p, 50, 16, 11)
    _pen(p, c)
    _line(p, 16, 74, 84, 74)


def _clear_rest(p, c):
    _pen(p, c, STROKE * 0.7)
    _line(p, 40, 20, 40, 58)
    _dot(p, 40, 20, 9)
    _pen(p, c, STROKE * 0.9)
    _line(p, 14, 70, 66, 70)
    _pen(p, c, STROKE * 0.8)
    _line(p, 66, 22, 94, 50)
    _line(p, 94, 22, 66, 50)


def _reset(p, c):
    """Back to the default. A control dropping onto its baseline.

    Deliberately NOT a circular arrow: _refresh is one, _cycle used to be one,
    and at 16px three circular arrows are one icon with three meanings.
    """
    _pen(p, c, STROKE * 0.55)
    _line(p, 12, 78, 88, 78)
    _pen(p, c)
    _key(p, 50, 24, 12)
    _pen(p, c, STROKE * 0.8)
    _line(p, 50, 40, 50, 58)
    _arrow_head(p, 50, 66, 1, 14)
    p.save()
    p.translate(50, 66)
    p.rotate(90)
    p.translate(-50, -66)
    p.restore()


# --- selection sets ---------------------------------------------------------


def _sets_store(p, c):
    _pen(p, c, STROKE * 0.8)
    p.drawRoundedRect(QtCore.QRectF(16, 20, 68, 60), 6, 6)
    _pen(p, c, STROKE * 0.9)
    _line(p, 50, 34, 50, 62)
    _line(p, 36, 48, 64, 48)


def _sets_recall(p, c):
    _pen(p, c, STROKE * 0.8)
    p.drawRoundedRect(QtCore.QRectF(16, 20, 68, 60), 6, 6)
    _pen(p, c)
    _line(p, 34, 52, 46, 64)
    _line(p, 46, 64, 68, 36)


def _sets_delete(p, c):
    _pen(p, c, STROKE * 0.8)
    p.drawRoundedRect(QtCore.QRectF(16, 20, 68, 60), 6, 6)
    _pen(p, c)
    _line(p, 36, 36, 64, 64)
    _line(p, 64, 36, 36, 64)


def _refresh(p, c):
    _pen(p, c)
    rect = QtCore.QRectF(24, 24, 52, 52)
    p.drawArc(rect, 50 * 16, 270 * 16)
    _arrow_head(p, 76, 30, 1, 15)


# --- reference media --------------------------------------------------------
#
# These started out sharing a container -- a film frame with sprocket holes,
# so the group would read as a group. The contact sheet killed it. A frame plus
# an interior glyph is four strokes of perimeter before the glyph is drawn at
# all, and at 16px the eight of them were eight identical smudges with
# something indistinct inside. It is the same failure the four tangent icons
# had, arrived at from the opposite direction.
#
# So there is no container. Group identity comes from the accent colour, which
# is how every other group here already does it -- nothing binds the five
# Timing icons together either. What each of these is instead is one bold
# silhouette, chosen to be a shape nothing else in the set uses.


def _down_arrow_head(p, tip_x, tip_y, size=14.0):
    """A filled triangle pointing down. `_arrow_head` only points sideways."""
    p.setBrush(p.pen().color())
    p.setPen(QtCore.Qt.NoPen)
    p.drawPolygon(QtGui.QPolygonF([
        QtCore.QPointF(tip_x, tip_y),
        QtCore.QPointF(tip_x - size * 0.62, tip_y - size),
        QtCore.QPointF(tip_x + size * 0.62, tip_y - size),
    ]))


def _solid(p, points):
    """A filled polygon in the current pen colour. Call `_pen` first.

    Solid shapes survive being scaled to 16px in a way outlines do not, which
    is why most of the reference icons are built from them.
    """
    p.setBrush(p.pen().color())
    p.setPen(QtCore.Qt.NoPen)
    p.drawPolygon(QtGui.QPolygonF(
        [QtCore.QPointF(x, y) for x, y in points]
    ))


def _ref_load(p, c):
    """Media arriving: an arrow down into an open tray."""
    _pen(p, c)
    _line(p, 50, 16, 50, 48)
    _down_arrow_head(p, 50, 62, 17)
    _pen(p, c, STROKE * 0.8)
    _path(p, [(18, 62), (18, 84), (82, 84), (82, 62)])


def _ref_toggle(p, c):
    """An eye. Nothing else in the set is one, at any size."""
    _pen(p, c, STROKE * 0.85)
    path = QtGui.QPainterPath(QtCore.QPointF(14, 50))
    path.cubicTo(QtCore.QPointF(34, 20), QtCore.QPointF(66, 20),
                 QtCore.QPointF(86, 50))
    path.cubicTo(QtCore.QPointF(66, 80), QtCore.QPointF(34, 80),
                 QtCore.QPointF(14, 50))
    p.drawPath(path)
    _dot(p, 50, 50, 11)


def _ref_slip_back(p, c):
    """One frame earlier: the media stepping back to a mark.

    Solid triangle against a bar, not the thin arrow-and-bar that Offset and
    Retime already use -- at 16px the difference between an outline and a
    filled shape survives where the difference between two arrangements of
    strokes does not.
    """
    _pen(p, c)
    _line(p, 20, 24, 20, 76)
    _solid(p, [(34, 50), (82, 20), (82, 80)])


def _ref_slip_forward(p, c):
    _pen(p, c)
    _line(p, 80, 24, 80, 76)
    _solid(p, [(66, 50), (18, 20), (18, 80)])


def _ref_sync(p, c):
    """The reference's first frame butted up against the time cursor."""
    _pen(p, c, STROKE * 0.8)
    _line(p, 20, 14, 20, 86)
    _pen(p, c)
    _solid(p, [(30, 32), (86, 32), (86, 68), (30, 68)])


def _ref_fade_down(p, c):
    """A ramp coming down. The editing convention for a fade, and a large
    solid shape rather than two nearly-identical half-filled circles."""
    _pen(p, c)
    _solid(p, [(16, 76), (84, 76), (16, 24)])


def _ref_fade_up(p, c):
    _pen(p, c)
    _solid(p, [(16, 76), (84, 76), (84, 24)])


def _ref_pin(p, c):
    """A push pin: the reference held against the camera, or let go.

    Nothing else in the set is a pin, and a pin is one of the few shapes that
    still reads as itself at 16px because its silhouette is asymmetric.
    """
    _pen(p, c, STROKE * 0.9)
    _line(p, 50, 56, 50, 86)
    _pen(p, c)
    _solid(p, [(26, 50), (74, 50), (62, 20), (38, 20)])
    _pen(p, c, STROKE * 0.8)
    _line(p, 22, 52, 78, 52)


def _ref_frame(p, c):
    """Look at it: a viewfinder's corner brackets around a centre mark.

    Corner brackets, not a full rectangle, so it does not read as another
    box-with-something-in-it at a glance.
    """
    _pen(p, c, STROKE * 0.85)
    for x_sign, y_sign in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
        x = 50 + x_sign * 34
        y = 50 + y_sign * 30
        _line(p, x, y, x - x_sign * 16, y)
        _line(p, x, y, x, y - y_sign * 14)
    _pen(p, c)
    _dot(p, 50, 50, 9)


def _ref_arrange(p, c):
    """Three boards standing in a row.

    Bars of equal height and unequal width, because the thing being said is
    "side by side, all facing you" -- and equal widths would read as a bar
    chart or a set of tabs rather than as reference boards of different
    shapes lined up.
    """
    _pen(p, c, STROKE * 0.9)
    for x, half in ((21, 11), (50, 15), (81, 9)):
        _path(p, [(x - half, 28), (x + half, 28),
                  (x + half, 72), (x - half, 72)], close=True)


def _ref_remove(p, c):
    """Eject. `Delete` is already a bare X and `Set Delete` is an X in a box,
    so a third X would be a third meaning for one picture."""
    _pen(p, c)
    _solid(p, [(50, 20), (86, 58), (14, 58)])
    _pen(p, c, STROKE)
    _line(p, 18, 76, 82, 76)


def _mirror_range(p, c):
    """Mirror, but across the timeline rather than on one frame.

    The mirror glyph pushed into the top half with a keyed timeline underneath.
    The timeline is the whole difference from `_mirror`, and `_flip_range` uses
    the same device -- so the pair still reads as a pair, which is the point.

    `_pen` is re-set before every `_solid` and every `_key`: both leave the
    painter on NoPen, and the next shape would take its colour from that.
    """
    _pen(p, c, STROKE * 0.5)
    _line(p, 50, 8, 50, 54)
    _pen(p, c, STROKE * 0.8)
    _solid(p, [(40, 18), (20, 32), (40, 46)])
    _pen(p, c, STROKE * 0.8)
    _solid(p, [(60, 18), (80, 32), (60, 46)])
    _pen(p, c, STROKE * 0.45)
    _line(p, 14, 78, 86, 78)
    for x in (22, 50, 78):
        _pen(p, c)
        _key(p, x, 78, 8)


def _flip_range(p, c):
    """Flip, across the timeline. Mirror's two shapes, exchanged."""
    _pen(p, c, STROKE * 0.5)
    _line(p, 50, 8, 50, 54)
    _pen(p, c, STROKE * 0.8)
    _solid(p, [(42, 10), (22, 24), (42, 38)])
    _pen(p, c, STROKE * 0.8)
    _solid(p, [(58, 26), (78, 40), (58, 54)])
    _pen(p, c, STROKE * 0.45)
    _line(p, 14, 78, 86, 78)
    for x in (22, 50, 78):
        _pen(p, c)
        _key(p, x, 78, 8)


#: Seven-segment geometry for the bake numerals, on the usual 0-100 canvas.
#: a=top, b=upper right, c=lower right, d=bottom, e=lower left, f=upper left,
#: g=middle.
_SEG = {
    "a": ((32, 18), (68, 18)),
    "b": ((68, 18), (68, 42)),
    "c": ((68, 42), (68, 66)),
    "d": ((32, 66), (68, 66)),
    "e": ((32, 42), (32, 66)),
    "f": ((32, 18), (32, 42)),
    "g": ((32, 42), (68, 42)),
}

#: Which segments each bake step lights. 1 is drawn as a stem and a flag
#: instead, because seven-segment "1" is a bare right-hand bar that reads as a
#: stray line rather than as a number.
_DIGITS = {
    2: "abged",
    3: "abgcd",
    4: "fgbc",
    5: "afgcd",
    6: "afgecd",
    7: "abc",
}


def _bake(step):
    """The step as a NUMERAL over a timeline. One painter per step.

    THE NUMBER HAS TO BE IN THE PICTURE, and that is not a stylistic choice.
    `panel.OperationButton` draws an icon OR a label and never both -- so an
    operation that has an icon has thrown its label away. Seven bake buttons
    sharing a wordless "sampling density" mark therefore reach the animator as
    seven identical buttons with no numbers anywhere, which is precisely what
    the first draft of these did: the contact sheet at 16px showed steps 1
    through 5 as the same row of dots over a line.

    A digit is the one thing that stays legible at 16px and is unambiguously
    different from its neighbours, which is also what makes the distinctness
    test pass honestly rather than by four pixels.

    The rule underneath is the one the tangent icons learned: draw the thing
    that has to be TOLD APART, not the thing that is being described.
    """
    def paint(p, c):
        _pen(p, c, STROKE * 0.8)
        if step == 1:
            _line(p, 50, 18, 50, 66)
            _line(p, 40, 27, 50, 18)
        else:
            for segment in _DIGITS.get(step, "abgcd"):
                (x1, y1), (x2, y2) = _SEG[segment]
                _line(p, x1, y1, x2, y2)
        # The timeline the number applies to. Shared by all seven, so it says
        # "frames" without competing with the digit for legibility.
        _pen(p, c, STROKE * 0.45)
        _line(p, 20, 84, 80, 84)

    return paint


# --- the registry -----------------------------------------------------------

#: runTimeCommand name -> painter. An operation with no entry falls back to its
#: text label, so a new operation is never invisible -- just unillustrated.
PAINTERS = {
    "animkitKeysOffsetBack": _offset_back,
    "animkitKeysOffsetForward": _offset_forward,
    "animkitKeysRetimeFaster": _retime_faster,
    "animkitKeysRetimeSlower": _retime_slower,
    "animkitKeysSnapToFrame": _snap,

    "animkitKeysTangentAuto": _tangent_auto,
    "animkitKeysTangentSpline": _tangent_spline,
    "animkitKeysTangentLinear": _tangent_linear,
    "animkitKeysTangentFlat": _tangent_flat,
    "animkitKeysTangentStepped": _tangent_stepped,
    "animkitKeysHold": _hold,

    "animkitKeysCycle": _cycle,
    "animkitKeysCycleOffset": _cycle_offset,
    "animkitKeysCycleClear": _cycle_clear,

    "animkitKeysDelete": _delete,

    "animkitPoseCopy": _copy,
    "animkitPosePaste": _paste,
    "animkitPosePasteMirrored": _paste_mirrored,
    "animkitPoseMirror": _mirror,
    "animkitPoseFlip": _flip,
    "animkitPoseMirrorRange": _mirror_range,
    "animkitPoseFlipRange": _flip_range,
    "animkitPoseCaptureRest": _capture_rest,
    "animkitPoseClearRest": _clear_rest,
    "animkitPoseReset": _reset,

    "animkitSetStore": _sets_store,
    "animkitSetRecall": _sets_recall,
    "animkitSetDelete": _sets_delete,
    "animkitRefresh": _refresh,

    "animkitRefLoad": _ref_load,
    "animkitRefToggle": _ref_toggle,
    "animkitRefSlipBack": _ref_slip_back,
    "animkitRefSlipForward": _ref_slip_forward,
    "animkitRefSync": _ref_sync,
    "animkitRefFadeDown": _ref_fade_down,
    "animkitRefFadeUp": _ref_fade_up,
    "animkitRefPin": _ref_pin,
    "animkitRefArrange": _ref_arrange,
    "animkitRefFrame": _ref_frame,
    "animkitRefRemove": _ref_remove,
}


# Bake: one painter per step in keys.BAKE_STEPS. Deliberately NOT imported
# from there -- animkit.ui does not need animkit.tools in order to draw a
# picture, and adding that import would drag maya.cmds into the icon set. The
# drift this risks is already covered: test_every_keyframe_operation fails the
# moment a bake step exists without an icon.
PAINTERS.update(
    ("animkitKeysBake%d" % _step, _bake(_step)) for _step in range(1, 8)
)


def has_icon(name):
    return name in PAINTERS


def image(name, size=64, colour=style.TEXT):
    """Render one icon to a QImage. Needs no QGuiApplication.

    That is what makes the icon set testable, and it is why nothing here
    touches QPixmap until `icon()` is asked for one.
    """
    painter_fn = PAINTERS.get(name)
    canvas = QtGui.QImage(
        int(size), int(size), QtGui.QImage.Format_ARGB32_Premultiplied
    )
    canvas.fill(QtCore.Qt.transparent)
    if painter_fn is None:
        return canvas

    painter = QtGui.QPainter(canvas)
    try:
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.scale(size / GRID, size / GRID)
        painter_fn(painter, colour)
    except Exception:
        log.debug("animkit: could not paint icon %r", name, exc_info=True)
    finally:
        painter.end()
    return canvas


def icon(name, size=None, colour=style.TEXT):
    """A QIcon for a button. Returns an empty QIcon for an unknown name."""
    size = size or style.px(style.ICON_SIZE)
    pixmap = QtGui.QPixmap.fromImage(image(name, size=size, colour=colour))
    return QtGui.QIcon(pixmap)


def contact_sheet(size=64, columns=7, colour=style.TEXT,
                  background=style.SURFACE):
    """Every icon on one QImage, for looking at them together.

    A contact sheet is how you find out that Mirror and Flip read as the same
    picture at 16px. Looking at them one at a time never reveals it.
    """
    names = sorted(PAINTERS)
    rows = (len(names) + columns - 1) // columns
    pad = size // 4
    cell = size + pad

    canvas = QtGui.QImage(
        columns * cell + pad, rows * cell + pad,
        QtGui.QImage.Format_ARGB32_Premultiplied,
    )
    canvas.fill(QtGui.QColor(background))

    painter = QtGui.QPainter(canvas)
    try:
        for index, name in enumerate(names):
            x = pad + (index % columns) * cell
            y = pad + (index // columns) * cell
            painter.drawImage(x, y, image(name, size=size, colour=colour))
    finally:
        painter.end()
    return canvas
