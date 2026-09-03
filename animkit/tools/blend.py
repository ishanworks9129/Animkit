"""Blend maths. Deliberately imports nothing from Maya.

Keeping this file Maya-free means the interpolation behaviour -- the part
animators will actually argue about -- can be tested in plain CPython in
milliseconds, without launching mayapy. Everything that needs the DG lives in
tween.py.
"""

MODE_BETWEEN = "between"
MODE_LINEAR = "linear"
MODE_AVERAGE = "average"
MODE_EASE = "ease"

MODES = (MODE_BETWEEN, MODE_LINEAR, MODE_AVERAGE, MODE_EASE)

MODE_LABELS = {
    MODE_BETWEEN: "Between",
    MODE_LINEAR: "Linear",
    MODE_AVERAGE: "Average",
    MODE_EASE: "Ease",
}

MODE_TOOLTIPS = {
    MODE_BETWEEN: "0 holds the current pose; drag blends toward the previous "
                  "or next key. The everyday mode.",
    MODE_LINEAR: "Classic tweenMachine: -1 sits on the previous key, 0 is the "
                 "midpoint between neighbours, +1 sits on the next key. "
                 "Ignores the current pose entirely.",
    MODE_AVERAGE: "Blends toward the average of both neighbours. Good for "
                  "flattening a pose without picking a side.",
    MODE_EASE: "Like Between, but eased -- slow at the extremes, quick "
               "through the middle. Softer for favouring.",
}


def _smoothstep(x):
    return x * x * (3.0 - 2.0 * x)


def blend(mode, current, prev, nxt, t):
    """Return the blended value.

    current/prev/nxt share a unit space (internal units, in practice).
    prev or nxt may be None at the ends of a curve; they fall back to
    `current`, so the slider simply has no travel in that direction rather
    than snapping the pose somewhere arbitrary.

    t is nominally -1..1 but is deliberately NOT clamped here. Values outside
    that range extrapolate, which is the overshoot behaviour animators use to
    punch a pose past its neighbour. Clamping belongs in the UI, not the
    maths.
    """
    if prev is None:
        prev = current
    if nxt is None:
        nxt = current

    if mode == MODE_LINEAR:
        a = (t + 1.0) * 0.5
        return prev + (nxt - prev) * a

    if mode == MODE_AVERAGE:
        target = (prev + nxt) * 0.5
        return current + (target - current) * abs(t)

    if mode == MODE_EASE:
        a = abs(t)
        a = _smoothstep(a) if a <= 1.0 else a
        target = nxt if t >= 0 else prev
        return current + (target - current) * a

    # MODE_BETWEEN (default)
    target = nxt if t >= 0 else prev
    return current + (target - current) * abs(t)
