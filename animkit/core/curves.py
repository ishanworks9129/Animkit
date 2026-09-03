"""animCurve access via the OpenMaya 2.0 API.

Two things this module exists to get right:

1. SPEED. cmds.setKeyframe on a 300-control rig is roughly two orders of
   magnitude slower than MFnAnimCurve.setValue. That gap is the difference
   between a slider that feels attached to the mouse and one that does not.

2. UNITS. MFnAnimCurve works in Maya's INTERNAL units -- radians for rotation,
   centimetres for translation. cmds.* works in UI units -- degrees, and
   whatever the user set in preferences. Mixing them silently multiplies
   rotation values by 57.3. Everything in this module stays in internal units;
   conversion happens only at to_ui() / from_ui(), on the way to cmds.
"""

import logging

import maya.api.OpenMaya as om2
import maya.api.OpenMayaAnim as oma2

log = logging.getLogger(__name__)


def mobject(name):
    sel = om2.MSelectionList()
    sel.add(name)
    return sel.getDependNode(0)


def current_time():
    return oma2.MAnimControl.currentTime().value


class Curve(object):
    """Thin MFnAnimCurve wrapper with unit-safe accessors.

    Instances are cheap but hold an MObject, so do not cache them across a
    scene change -- see animkit.core.scene.
    """

    def __init__(self, name):
        self.name = name
        self.fn = oma2.MFnAnimCurve(mobject(name))
        self._type = self.fn.animCurveType

    # --- units --------------------------------------------------------------

    def to_ui(self, value):
        """Internal units -> UI units (what cmds.* expects)."""
        if self._type == oma2.MFnAnimCurve.kAnimCurveTA:
            return om2.MAngle(value, om2.MAngle.kRadians).asUnits(om2.MAngle.uiUnit())
        if self._type == oma2.MFnAnimCurve.kAnimCurveTL:
            return om2.MDistance(value, om2.MDistance.kCentimeters).asUnits(
                om2.MDistance.uiUnit()
            )
        return value

    def from_ui(self, value):
        """UI units -> internal units."""
        if self._type == oma2.MFnAnimCurve.kAnimCurveTA:
            return om2.MAngle(value, om2.MAngle.uiUnit()).asRadians()
        if self._type == oma2.MFnAnimCurve.kAnimCurveTL:
            return om2.MDistance(value, om2.MDistance.uiUnit()).asCentimeters()
        return value

    # --- reads --------------------------------------------------------------

    @property
    def num_keys(self):
        return self.fn.numKeys

    def key_time(self, index):
        return self.fn.input(index).value

    def key_value(self, index):
        return self.fn.value(index)

    def evaluate(self, time):
        """Value the curve holds at `time`, in internal units.

        This is the point of using the API rather than cmds.getAttr: it gives
        the value the CURVE would produce, not the value the attribute is
        currently showing after constraints, layers and everything else in the
        graph has had its say.
        """
        return self.fn.evaluate(om2.MTime(time, om2.MTime.uiUnit()))

    def index_at(self, time, tolerance=1e-5):
        """Index of the key at `time`, or None."""
        n = self.fn.numKeys
        lo, hi = 0, n - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            t = self.key_time(mid)
            if abs(t - time) <= tolerance:
                return mid
            if t < time:
                lo = mid + 1
            else:
                hi = mid - 1
        return None

    def neighbours(self, time):
        """Return (prev_value, next_value) in internal units.

        Either may be None when the curve has nothing on that side. Callers
        decide the fallback -- for tweening, falling back to the current value
        means the slider simply has no travel in that direction, which is the
        behaviour animators expect at the first and last key.
        """
        n = self.fn.numKeys
        if n == 0:
            return None, None

        prev_i = None
        next_i = None
        for i in range(n):
            t = self.key_time(i)
            if t < time - 1e-5:
                prev_i = i
            elif t > time + 1e-5:
                next_i = i
                break

        prev_v = self.key_value(prev_i) if prev_i is not None else None
        next_v = self.key_value(next_i) if next_i is not None else None
        return prev_v, next_v

    def neighbours_by_index(self, index):
        """Return (prev_value, next_value) for the keys either side of `index`.

        Preferred over neighbours(time). It works for a key anywhere on the
        curve -- which selected-key mode needs -- and it stays correct after an
        insertion, where a time-based lookup would find the newly inserted key
        as its own neighbour.
        """
        n = self.fn.numKeys
        prev_v = self.key_value(index - 1) if index - 1 >= 0 else None
        next_v = self.key_value(index + 1) if index + 1 < n else None
        return prev_v, next_v

    # --- writes -------------------------------------------------------------

    def set_value_fast(self, index, value):
        """Write a key value with no undo record. Drag-loop use only.

        Anything written this way MUST be reverted with the same mechanism
        before the operation commits through cmds -- see tools/tween.py for
        the snapshot/restore/commit pattern that keeps undo honest.
        """
        self.fn.setValue(index, value)

    def add_key(self, time, value, tangent=None):
        """Insert a key through the API. Returns its index.

        NOT USED BY THE TWEEN PATH, and think hard before using it anywhere.

        API topology edits (addKey / remove) leave Maya's evaluation state
        inconsistent: the curve changes, but plain cmds.getAttr and any later
        cmds.setKeyframe on the same curve keep reading the pre-edit value.
        The failure looks like a write that did nothing, and it cost a debugging
        session to pin down. Value edits via set_value_fast are fine; topology
        edits should go through cmds.setKeyframe(insert=True), which also
        preserves the surrounding curve shape rather than flattening it to auto
        tangents the way this does.

        Kept because it is the correct call for a curve you have just built and
        that nothing is evaluating yet.
        """
        tangent = tangent if tangent is not None else oma2.MFnAnimCurve.kTangentAuto
        mtime = om2.MTime(time, om2.MTime.uiUnit())
        return self.fn.addKey(mtime, value, tangent, tangent)


def open_curve(name):
    """Curve(name), or None if the node vanished. Node names go stale."""
    try:
        return Curve(name)
    except Exception:
        log.debug("animkit: could not open curve %r", name, exc_info=True)
        return None
