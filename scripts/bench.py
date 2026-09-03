"""Performance bench for the tween path. Measure before optimising.

    # headless, from a PowerShell terminal
    & "C:\\Program Files\\Autodesk\\Maya2024\\bin\\mayapy.exe" scripts\\bench.py

    # inside Maya, against the rig you actually animate on
    import sys; sys.path.append(r"C:\\Users\\ishan\\Desktop\\Animbot")
    import scripts.bench as bench
    bench.run_on_selection()

Two numbers decide whether any optimisation is worth doing:

    begin()   runs once at mouse-down. A slow one is a click that does not
              feel connected to the tool. Budget: under ~100ms is invisible,
              over ~250ms feels broken.
    update()  runs once per mouse-move, so it must fit inside a frame.
              Budget: 16ms. Over that and the slider lags the cursor.

READ THIS BEFORE ACTING ON THE NUMBERS
--------------------------------------
Headless (mayapy) there is no viewport, so update() here measures the animkit
share of the frame budget and nothing else. Real Maya adds redraw, the Channel
Box, Cached Playback invalidation and whatever else is docked. Treat the
headless number as a FLOOR: if it is already over 16ms headless, it is
definitely too slow. If it is 2ms headless, that does not prove the slider
feels good -- only run_on_selection() on a production rig proves that.

The synthetic rig is also optimistic in a specific way: its controls are plain
transforms with ~10 keyable channels. Real rig controls carry custom
attributes, and plugs_from_selection() costs are per ATTRIBUTE, not per node.
Use --attrs to model that, or just run on the real thing.
"""

import argparse
import statistics
import sys
import time

PREFIX = "animkitBench_"

# One mouse-move must fit in a frame at 60fps.
FRAME_BUDGET_MS = 16.0


# --- measurement ------------------------------------------------------------


class Result(object):
    """Timing stats for one repeatedly-measured operation, in milliseconds."""

    def __init__(self, label, samples, budget=None, note=""):
        self.label = label
        self.samples = sorted(samples)
        self.budget = budget
        self.note = note

    @property
    def best(self):
        return self.samples[0] if self.samples else float("nan")

    @property
    def median(self):
        return statistics.median(self.samples) if self.samples else float("nan")

    @property
    def worst(self):
        return self.samples[-1] if self.samples else float("nan")

    @property
    def over_budget(self):
        return self.budget is not None and self.median > self.budget

    def row(self, width):
        flag = "  "
        if self.budget is not None:
            flag = "!!" if self.over_budget else "ok"
        line = "  {0} {1} {2:>9.2f} {3:>9.2f} {4:>9.2f}".format(
            flag, self.label.ljust(width), self.best, self.median, self.worst
        )
        if self.budget is not None:
            line += "   <{0:.0f}".format(self.budget)
        else:
            line += "      -"
        if self.note:
            line += "  " + self.note
        return line


def measure(label, fn, repeat=15, setup=None, teardown=None, budget=None, note=""):
    """Time fn() `repeat` times. setup/teardown are not counted.

    Reports median rather than mean. One GC pause or one Maya idle event skews
    a mean badly at these timescales, and the median is what the hand actually
    feels.
    """
    samples = []
    for _ in range(repeat):
        state = setup() if setup else None
        start = time.perf_counter()
        fn(state)
        samples.append((time.perf_counter() - start) * 1000.0)
        if teardown:
            teardown(state)
    return Result(label, samples, budget=budget, note=note)


class CallCounter(object):
    """Count cmds calls made inside a block.

    The point is not the timing -- it is proving WHICH call is being made
    12,000 times, so an optimisation targets the right one. Monkeypatches
    maya.cmds for the duration and always puts it back.
    """

    WATCHED = (
        "getAttr", "attributeQuery", "listAttr", "listConnections",
        "nodeType", "animLayer", "keyframe", "setKeyframe", "objExists",
    )

    def __init__(self):
        self.counts = {}
        self._originals = {}

    def __enter__(self):
        from maya import cmds

        for name in self.WATCHED:
            original = getattr(cmds, name, None)
            if original is None:
                continue
            self._originals[name] = original
            setattr(cmds, name, self._wrap(name, original))
        return self

    def _wrap(self, name, original):
        counts = self.counts

        def wrapper(*args, **kwargs):
            counts[name] = counts.get(name, 0) + 1
            return original(*args, **kwargs)

        return wrapper

    def __exit__(self, *exc):
        from maya import cmds

        for name, original in self._originals.items():
            setattr(cmds, name, original)
        self._originals.clear()
        return False

    @property
    def total(self):
        return sum(self.counts.values())

    def report(self, title):
        print("")
        print("  {0}".format(title))
        if not self.counts:
            print("    (no watched cmds calls)")
            return
        for name, count in sorted(self.counts.items(), key=lambda kv: -kv[1]):
            print("    {0:>7}  cmds.{1}".format(count, name))
        print("    {0:>7}  TOTAL".format(self.total))


# --- fixture ----------------------------------------------------------------


def build_rig(controls=300, keys=20, extra_attrs=0, layers=0, spacing=5):
    """Build a synthetic rig and select every control. Returns the node list.

    Deliberately plain transforms, not meshes: this bench measures the anim
    data path, and a poly cube per control would spend the whole run in mesh
    evaluation instead.
    """
    from maya import cmds

    nodes = []
    for i in range(controls):
        node = cmds.createNode("transform", name="{0}ctrl{1:03d}".format(PREFIX, i))
        for a in range(extra_attrs):
            attr = "custom{0}".format(a)
            cmds.addAttr(node, longName=attr, attributeType="double", keyable=True)
        nodes.append(node)

    # Key the channels a real control carries animation on. visibility is
    # deliberately left unkeyed -- selection.py skips it, and keying it here
    # would flatter the numbers by making a skipped attribute look cheap.
    channels = ["translateX", "translateY", "translateZ",
                "rotateX", "rotateY", "rotateZ"]
    channels += ["custom{0}".format(a) for a in range(extra_attrs)]

    for node in nodes:
        for k in range(keys):
            frame = 1 + k * spacing
            for channel in channels:
                cmds.setKeyframe(
                    "{0}.{1}".format(node, channel), time=frame, value=float(k)
                )

    for n in range(layers):
        layer = cmds.animLayer("{0}L{1}".format(PREFIX, n))
        cmds.select(nodes)
        cmds.animLayer(layer, edit=True, addSelectedObjects=True)
        for node in nodes:
            for channel in channels:
                cmds.setKeyframe(
                    "{0}.{1}".format(node, channel),
                    time=1, value=0.0, animLayer=layer,
                )
        cmds.animLayer(layer, edit=True, selected=True)

    cmds.select(nodes)
    # Between keys, so begin() has to insert -- the expensive path.
    cmds.currentTime(1 + spacing // 2)
    cmds.selectKey(clear=True)
    return nodes


def teardown_rig():
    from maya import cmds

    for node in cmds.ls(PREFIX + "*") or []:
        try:
            cmds.delete(node)
        except Exception:
            pass


# --- the bench --------------------------------------------------------------


def bench_all(repeat=15, drag_moves=40, label=""):
    """Time the whole tween path against whatever is currently selected."""
    from animkit.core import cache
    from animkit.core import layers as layers_mod
    from animkit.core import selection
    from animkit.tools import tween

    results = []

    plugs = selection.plugs_from_selection()
    nodes = selection.selected_nodes()
    scale = "{0} nodes, {1} plugs".format(len(nodes), len(plugs))

    if not plugs:
        print("  nothing selected, or nothing keyable selected -- aborting")
        return None, scale

    # --- mouse-down components ----------------------------------------------
    results.append(
        measure(
            "selection.plugs_from_selection",
            lambda _: selection.plugs_from_selection(),
            repeat=repeat,
        )
    )

    # Both raw and scoped, because the difference IS the cache. begin() runs
    # these inside a scope; anything calling them in a bare loop does not, and
    # would be reading the raw row.
    def scoped(fn):
        def run(_):
            with cache.scope():
                return fn()
        return run

    results.append(
        measure(
            "layers.resolve_curve  raw",
            lambda _: [layers_mod.resolve_curve(p) for p in plugs],
            repeat=repeat,
        )
    )
    results.append(
        measure(
            "layers.resolve_curve  in scope",
            scoped(lambda: [layers_mod.resolve_curve(p) for p in plugs]),
            repeat=repeat,
        )
    )

    results.append(
        measure(
            "layers.target_layer   raw",
            lambda _: [layers_mod.target_layer(p) for p in plugs],
            repeat=repeat,
        )
    )
    results.append(
        measure(
            "layers.target_layer   in scope",
            scoped(lambda: [layers_mod.target_layer(p) for p in plugs]),
            repeat=repeat,
        )
    )

    # --- begin(), the mouse-down cost ---------------------------------------
    def begin_setup():
        return tween.TweenSession()

    def begin_teardown(session):
        session.cancel()

    results.append(
        measure(
            "TweenSession.begin  [inserting]",
            lambda s: s.begin(),
            repeat=repeat,
            setup=begin_setup,
            teardown=begin_teardown,
            budget=100.0,
            note="mouse-down",
        )
    )

    # Same call with keys already present: begin() then skips pass 2 entirely,
    # which is the common case on a rig that is already blocked out.
    session = tween.TweenSession()
    if session.begin():
        session.commit(0.01)
    results.append(
        measure(
            "TweenSession.begin  [keys exist]",
            lambda s: s.begin(),
            repeat=repeat,
            setup=begin_setup,
            teardown=begin_teardown,
            budget=100.0,
            note="mouse-down",
        )
    )

    # --- update(), the per-mouse-move cost ----------------------------------
    session = tween.TweenSession()
    if not session.begin():
        print("  begin() found nothing to tween -- aborting")
        return None, scale

    try:
        samples = []
        for i in range(drag_moves):
            t = ((i % 20) - 10) / 10.0
            start = time.perf_counter()
            session.update(t)
            samples.append((time.perf_counter() - start) * 1000.0)
        results.append(
            Result(
                "TweenSession.update",
                samples,
                budget=FRAME_BUDGET_MS,
                note="PER MOUSE-MOVE",
            )
        )

        start = time.perf_counter()
        session.commit(0.5)
        commit_ms = (time.perf_counter() - start) * 1000.0
        results.append(
            Result("TweenSession.commit", [commit_ms], budget=250.0, note="on release")
        )
    finally:
        session.force_close()

    print_table(results, scale, label)
    return results, scale


def print_table(results, scale, label=""):
    width = max(len(r.label) for r in results)
    title = "animkit bench" + (" -- " + label if label else "")
    print("")
    print("=" * (width + 56))
    print("  {0}".format(title))
    print("  {0}".format(scale))
    print("=" * (width + 56))
    print("     {0} {1:>9} {2:>9} {3:>9}   {4}".format(
        "operation".ljust(width), "best", "median", "worst", "budget (ms)"))
    print("-" * (width + 56))
    for r in results:
        print(r.row(width))
    print("-" * (width + 56))

    from animkit.core import cache

    stats = cache.stats()
    if stats["hits"] or stats["misses"]:
        total = stats["hits"] + stats["misses"]
        print("  cache: {0} hits / {1} lookups ({2:.0f}%), {3} calls outside "
              "any scope".format(stats["hits"], total,
                                 100.0 * stats["hits"] / max(1, total),
                                 stats["uncached"]))

    hot = [r for r in results if r.over_budget]
    if hot:
        print("")
        print("  OVER BUDGET: {0}".format(", ".join(r.label for r in hot)))
        print("  What is left, in the order worth fixing. Confirm with --calls")
        print("  before touching any of it -- the first round of this was")
        print("  guessed wrong once already.")
        print("")
        print("  begin() too slow:")
        print("    1. layers._source_plug / _is_anim_curve are still cmds")
        print("       (listConnections + nodeType per plug). ~4500 calls on")
        print("       300 controls, and the blend-node walk multiplies it per")
        print("       LAYER -- a 2-layer rig costs more per plug than an")
        print("       unlayered one 3x its size. Moving the walk to")
        print("       MPlug.source() would take most of it, but this is the")
        print("       safety-critical path the whole layer policy rests on,")
        print("       so it needs its own probe against constrained, driven")
        print("       and deeply layered rigs first.")
        print("    2. selection._survey_node iterates every attribute on the")
        print("       node to find the ~10 keyable ones.")
        print("")
        print("  update() too slow:")
        print("    cmds.dgdirty is ~90% of it and is NOT removable -- a")
        print("    topology change leaves DG reads stale without it. Measured:")
        print("    12.3ms of a 13.6ms update() on 1800 keys, against 1.4ms for")
        print("    all the blend maths and curve writes put together. Fewer")
        print("    keys per drag is the only real lever; dgdirty(allPlugs=True)")
        print("    is faster on a synthetic scene and much worse on a real one.")
    else:
        print("")
        print("  Everything inside budget. Do not optimise this.")
    print("=" * (width + 56))


def count_calls(repeat=1):
    """Show which cmds calls the mouse-down path actually makes."""
    from animkit.core import selection
    from animkit.tools import tween

    with CallCounter() as counter:
        for _ in range(repeat):
            selection.plugs_from_selection()
    counter.report("cmds calls per plugs_from_selection()")

    with CallCounter() as counter:
        for _ in range(repeat):
            session = tween.TweenSession()
            session.begin()
            session.cancel()
    counter.report("cmds calls per begin() + cancel()")


# --- entry points -----------------------------------------------------------


def run_on_selection(repeat=15, drag_moves=40, calls=False):
    """Bench whatever the animator has selected. The number that matters.

    Safe to run in a shot file: builds nothing, deletes nothing. It DOES tween
    the selection and commit once, so it leaves a couple of undo entries --
    Ctrl+Z after it and you are back where you were.
    """
    results, _scale = bench_all(repeat=repeat, drag_moves=drag_moves,
                                label="live selection")
    if calls:
        count_calls()
    return results


def run_synthetic(controls=300, keys=20, extra_attrs=0, layers=0,
                  repeat=15, drag_moves=40, calls=False, keep=False):
    """Build a synthetic rig, bench it, delete it."""
    from maya import cmds

    print("  building {0} controls x {1} keys{2}{3}...".format(
        controls, keys,
        " + {0} custom attrs".format(extra_attrs) if extra_attrs else "",
        " + {0} anim layer(s)".format(layers) if layers else "",
    ))
    start = time.perf_counter()
    build_rig(controls=controls, keys=keys, extra_attrs=extra_attrs, layers=layers)
    print("  built in {0:.1f}s".format(time.perf_counter() - start))

    try:
        label = "{0} controls, {1} keys, {2} layers".format(controls, keys, layers)
        results, _scale = bench_all(repeat=repeat, drag_moves=drag_moves, label=label)
        if calls:
            count_calls()
        return results
    finally:
        if not keep:
            cmds.select(clear=True)
            teardown_rig()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--controls", type=int, default=300)
    parser.add_argument("--keys", type=int, default=20)
    parser.add_argument("--attrs", type=int, default=0,
                        help="custom keyable attrs per control, as real rigs have")
    parser.add_argument("--layers", type=int, default=0,
                        help="animation layers to stack on the rig")
    parser.add_argument("--repeat", type=int, default=15)
    parser.add_argument("--moves", type=int, default=40,
                        help="simulated mouse-moves per drag")
    parser.add_argument("--calls", action="store_true",
                        help="also count the cmds calls the path makes")
    parser.add_argument("--keep", action="store_true",
                        help="leave the synthetic rig in the scene")
    args = parser.parse_args(argv)

    import maya.standalone

    maya.standalone.initialize(name="python")
    try:
        from maya import cmds

        cmds.file(new=True, force=True)
        run_synthetic(
            controls=args.controls,
            keys=args.keys,
            extra_attrs=args.attrs,
            layers=args.layers,
            repeat=args.repeat,
            drag_moves=args.moves,
            calls=args.calls,
            keep=args.keep,
        )
    finally:
        try:
            maya.standalone.uninitialize()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    import os

    # Run from a repo checkout without needing PYTHONPATH set.
    _repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _repo not in sys.path:
        sys.path.insert(0, _repo)
    sys.exit(main())
