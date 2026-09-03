"""Can Python sample a motion arc fast enough? Measure before building.

    & "C:\\Program Files\\Autodesk\\Maya2024\\bin\\mayapy.exe" scripts\\arc_bench.py

    # or in Maya, on the rig you actually animate
    import sys; sys.path.append(r"C:\\Users\\ishan\\Desktop\\Animbot")
    import scripts.arc_bench as b; b.run_on_selection()

WHAT THIS IS FOR
----------------
Phase 5 -- drawing motion arcs in the viewport -- is gated in AGENTS.md on
profiling that had not been done. This is that profiling. The rule it exists to
enforce: do not go to C++ on the assumption that Python is too slow, because
going C++ buys a per-Maya-version compile matrix, which was the single largest
cost in the original plan.

WHAT ACTUALLY COSTS ANYTHING
----------------------------
Not the drawing. An arc is a polyline of a few hundred points and
`MUIDrawManager` eats that without noticing. The cost is EVALUATING the
control's world position at every frame in the range, which means asking Maya
to evaluate the rig N times.

So the number that decides Phase 5 is: how long does it take to sample one
control across a shot-length range? And that splits by method, which is the
whole point of measuring rather than guessing:

  playhead   cmds.currentTime(f) then read the matrix. The obvious way, and
             the one that makes every other tool's arc tracker slow: moving
             the playhead re-evaluates the WHOLE scene, every character, every
             deformer, on every sample.
  timed get  cmds.getAttr(plug, time=f). Evaluates that plug at that time
             without moving the playhead.
  context    API 2.0 MDGContext around an MPlug read. Same idea, no command
             engine.

BUDGETS
-------
An arc is not recomputed per frame -- it is recomputed when the animation
changes, or the range does. So:

  resample   under ~100ms feels instant when you let go of a key; over ~500ms
             and the arc lags the edit badly enough to be worse than no arc.
  redraw     the per-refresh cost of drawing already-sampled points. Must fit
             a 16ms frame with room to spare, and will.

Headless there is no viewport, so this measures the sampling only -- which is
the part in question. Treat it as a floor, and run `run_on_selection()` on a
production rig before believing anything.
"""

import argparse
import statistics
import sys
import time

PREFIX = "animkitArcBench_"

#: Timed passes per method. THREE WAS NOT ENOUGH: the same 0-bystander
#: fixture measured 15.9ms and 51.9ms on two runs half an hour apart, a 3x
#: spread on the number that is supposed to decide Phase 5. `scripts/bench.py`
#: settled on 15 for the same reason and this matches it.
REPEATS = 15

#: Under this, a resample feels instant.
INSTANT_MS = 100.0
#: Over this, the arc lags the edit enough to be a nuisance.
LAGGY_MS = 500.0


def _stats(samples):
    ordered = sorted(samples)
    return {
        "best": ordered[0],
        "median": statistics.median(ordered),
        "worst": ordered[-1],
    }


# --- the sampling methods ---------------------------------------------------


def sample_playhead(nodes, start, end):
    """Move the playhead, read the matrix. What most tools do."""
    from maya import cmds

    restore = cmds.currentTime(q=True)
    points = dict((node, []) for node in nodes)
    try:
        for frame in range(start, end + 1):
            cmds.currentTime(frame)
            for node in nodes:
                matrix = cmds.getAttr(node + ".worldMatrix")
                points[node].append((matrix[12], matrix[13], matrix[14]))
    finally:
        cmds.currentTime(restore)
    return points


def sample_timed_get(nodes, start, end):
    """`getAttr -time`. Evaluates the plug without moving the playhead."""
    from maya import cmds

    points = dict((node, []) for node in nodes)
    for node in nodes:
        plug = node + ".worldMatrix"
        for frame in range(start, end + 1):
            matrix = cmds.getAttr(plug, time=frame)
            points[node].append((matrix[12], matrix[13], matrix[14]))
    return points


def sample_context(nodes, start, end):
    """API 2.0 MDGContext around an MPlug read. No command engine."""
    import maya.api.OpenMaya as om

    points = dict((node, []) for node in nodes)
    selection = om.MSelectionList()
    for node in nodes:
        selection.add(node + ".worldMatrix")

    normal = om.MDGContext.kNormal
    for index, node in enumerate(nodes):
        plug = selection.getPlug(index)
        plug = plug.elementByLogicalIndex(0) if plug.isArray else plug
        found = []
        for frame in range(start, end + 1):
            context = om.MDGContext(om.MTime(frame, om.MTime.uiUnit()))
            # MDGContextGuard IS API 1.0 ONLY. maya.api.OpenMaya has no such
            # class on 2024, so this method raised AttributeError on every row
            # and the first run of this bench silently compared two methods
            # while printing three. makeCurrent() returns the context it
            # displaced; restoring THAT rather than kNormal keeps the read
            # correct if a caller is already inside a context of its own.
            previous = context.makeCurrent()
            try:
                matrix = om.MFnMatrixData(plug.asMObject()).matrix()
            finally:
                (previous if previous is not None else normal).makeCurrent()
            found.append((matrix[12], matrix[13], matrix[14]))
        points[node] = found
    return points


METHODS = (
    ("playhead", sample_playhead),
    ("timed get", sample_timed_get),
    ("context", sample_context),
)


# --- the fixture ------------------------------------------------------------


def _animated_chain(name, depth, frames):
    """One animated transform chain, `depth` groups deep. Returns the leaf."""
    from maya import cmds

    parent = None
    for level in range(depth):
        parent = cmds.createNode(
            "transform", name="%s_%d" % (name, level), parent=parent)
    for frame in (1, frames // 2, frames):
        cmds.setKeyframe(parent + ".translateX", time=frame, value=frame * 0.5)
        cmds.setKeyframe(parent + ".translateY", time=frame,
                         value=(frame % 20) * 1.5)
        cmds.setKeyframe(parent + ".rotateY", time=frame, value=frame * 2)
    return parent


def build_rig(controls=1, depth=3, frames=120, bystanders=0):
    """A hierarchy deep enough that evaluation is not free.

    Depth matters more than control count for this measurement: a world
    position is the product of every parent matrix above it, so a control three
    groups down costs three times the matrix work of one at the root.

    BYSTANDERS ARE THE POINT, not padding. Moving the playhead re-evaluates the
    SCENE; a timed read evaluates only what the sampled plug depends on. With
    nothing else in the file those are the same work -- and the first run of
    this bench duly reported the playhead as the FASTEST method, the exact
    opposite of the effect it exists to measure, because a three-node chain
    alone in an empty scene is not a shot. Each bystander is an animated chain
    the sampled controls do not depend on: the playhead pays for all of them on
    every sample, a timed read pays for none.
    """
    cleanup()
    made = []
    for index in range(controls):
        made.append(
            _animated_chain("%sctrl%d" % (PREFIX, index), depth, frames))
    for index in range(bystanders):
        _animated_chain("%sload%d" % (PREFIX, index), depth, frames)
    return made


def cleanup():
    from maya import cmds

    for node in cmds.ls(PREFIX + "*") or []:
        try:
            if cmds.objExists(node):
                cmds.delete(node)
        except Exception:
            pass


# --- running ----------------------------------------------------------------


#: Two methods agreeing to this many units are reading the same frame.
AGREE_EPS = 1e-4


def _drift(reference, found):
    """Worst distance between two methods' sampled points.

    A method that is fast because it reads the WRONG VALUE wins a timing
    bench outright, so this is not optional decoration. `context` in
    particular reads through an evaluation context rather than moving time,
    and "the context did not apply and every sample came back at the current
    frame" is a silent failure that looks exactly like a very fast method.
    """
    worst = 0.0
    for node, points in found.items():
        other = reference.get(node) or []
        if len(other) != len(points):
            return float("inf")
        for index, point in enumerate(points):
            ax, ay, az = other[index]
            bx, by, bz = point
            worst = max(worst, ((ax - bx) ** 2 + (ay - by) ** 2
                                + (az - bz) ** 2) ** 0.5)
    return worst


def measure(nodes, start, end, repeats=REPEATS):
    """Time every method over the range. Returns {name: stats}."""
    results = {}
    reference = None
    for name, method in METHODS:
        samples = []
        found = None
        # ONE DISCARDED WARMUP PASS. The first call into a method pays for
        # whatever Maya builds lazily behind it, and at a low repeat count
        # that one-off lands squarely in the reported best. Failures are
        # swallowed here on purpose -- the timed loop below reports them.
        try:
            method(nodes, start, end)
        except Exception:
            pass
        for _ in range(repeats):
            began = time.time()
            try:
                found = method(nodes, start, end)
            except Exception as exc:
                results[name] = {"error": "%s: %s" % (type(exc).__name__, exc)}
                found = None
                break
            samples.append((time.time() - began) * 1000.0)
        if samples:
            stats = _stats(samples)
            stats["points"] = sum(len(v) for v in found.values())
            # THE FIRST METHOD TO SUCCEED IS THE REFERENCE, and METHODS is
            # ordered so that is the playhead -- the only one that genuinely
            # moves time, and so the only one that cannot be wrong about what
            # a frame looks like. It is the slow honest answer the clever
            # ones have to match.
            if reference is None:
                reference = found
                stats["drift"] = 0.0
            else:
                stats["drift"] = _drift(reference, found)
            results[name] = stats
    return results


def _verdict(ms):
    if ms < INSTANT_MS:
        return "instant"
    if ms < LAGGY_MS:
        return "usable"
    return "TOO SLOW"


def report(title, results, frames, nodes):
    print("")
    print("  %s -- %d frame(s) x %d control(s)" % (title, frames, nodes))
    for name, _fn in METHODS:
        stats = results.get(name)
        if not stats:
            continue
        if "error" in stats:
            print("    %-10s  %s" % (name, stats["error"]))
            continue
        drift = stats.get("drift")
        agrees = ""
        if drift is not None and drift > AGREE_EPS:
            agrees = "   DIVERGES BY %.4g" % drift
        print("    %-10s  best %7.1fms  median %7.1fms   %-9s%s"
              % (name, stats["best"], stats["median"],
                 _verdict(stats["median"]), agrees))


def run(frames=120, controls=1, depth=3, repeats=REPEATS, loads=(0, 300)):
    """The headless bench. Builds its own rig and deletes it."""
    from maya import cmds

    print("=" * 68)
    print("animkit arc bench -- Maya %s" % cmds.about(version=True))
    print("=" * 68)
    print("Budgets: a resample under %.0fms is instant, over %.0fms is too slow."
          % (INSTANT_MS, LAGGY_MS))
    print("Drawing the sampled points is not measured -- a few hundred points")
    print("through MUIDrawManager is not where the time goes.")
    print("")
    print("A 'bystander' is an animated chain the sampled control does NOT")
    print("depend on -- another character in the shot. They are what separates")
    print("the methods: the playhead re-evaluates every one of them on every")
    print("sample and a timed read does not. At 0 bystanders all three methods")
    print("are measuring the same work and the comparison means nothing.")

    try:
        for load in loads:
            for count in (1, 5):
                nodes = build_rig(controls=count, depth=depth, frames=frames,
                                  bystanders=load)
                title = "depth %d, %d bystander(s)" % (depth, load)
                for span in (24, frames, frames * 2):
                    results = measure(nodes, 1, span, repeats=repeats)
                    report(title, results, span, count)
                cleanup()
    finally:
        cleanup()

    print("")
    print("-" * 68)
    print("Read this before acting on it: headless has no viewport, so these")
    print("are the SAMPLING cost only and are a floor. Run run_on_selection()")
    print("on a production rig before deciding anything.")
    print("=" * 68)


def run_on_selection(repeats=REPEATS):
    """Measure against whatever is selected, over the playback range."""
    from maya import cmds

    nodes = cmds.ls(selection=True, long=True) or []
    if not nodes:
        cmds.warning("animkit: select a control to measure the arc cost on")
        return None
    start = int(cmds.playbackOptions(q=True, min=True))
    end = int(cmds.playbackOptions(q=True, max=True))

    print("=" * 68)
    print("animkit arc bench -- %d control(s), frames %d-%d"
          % (len(nodes), start, end))
    print("=" * 68)
    results = measure(nodes, start, end, repeats=repeats)
    report("selection", results, end - start + 1, len(nodes))
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description="Arc sampling bench")
    parser.add_argument("--frames", type=int, default=120)
    parser.add_argument("--depth", type=int, default=3)
    parser.add_argument("--repeats", type=int, default=REPEATS)
    parser.add_argument(
        "--bystanders", default="0,300",
        help="comma-separated scene loads to sweep (default 0,300)")
    args = parser.parse_args(argv)
    loads = tuple(int(n) for n in args.bystanders.split(",") if n.strip())

    import maya.standalone

    maya.standalone.initialize(name="python")
    try:
        run(frames=args.frames, depth=args.depth, repeats=args.repeats,
            loads=loads)
    finally:
        try:
            maya.standalone.uninitialize()
        except Exception:
            pass


if __name__ == "__main__":
    main(sys.argv[1:])
