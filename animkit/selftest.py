"""In-Maya smoke test. Run this before trusting anything else.

    import animkit.selftest as st; st.run()

It builds its own temporary cube, exercises the whole stack against it, then
deletes it. It does NOT open a new scene and does NOT touch any node you did
not select -- safe to run in a shot file, though there is no reason to.

Restores your object selection and current time on the way out. It DOES
clear any Graph Editor key selection -- that state cannot be saved and
restored, and a stale one would change which code paths run.

Run it twice. The first run creates BaseAnimation as a side effect of the
layer checks; if the second run does not match the first, cleanup is leaking
state and the two runs are exercising different code paths.
"""

import logging
import os
import time
import traceback

from maya import cmds

log = logging.getLogger(__name__)

PREFIX = "animkitSelftest_"


class _Report(object):
    def __init__(self):
        self.rows = []

    def add(self, name, ok, detail=""):
        """ok=True/False is a gate. ok=None is a measurement.

        Timings are deliberately NOT pass/fail. What counts as too slow
        depends on the rig, the machine and how much of it is selected, so a
        hard threshold here would either fire constantly on a heavy shot or
        never fire at all. Print the number, mark the ones worth a second
        look, and let the person reading decide.
        """
        self.rows.append((name, ok, detail))

    def info(self, name, detail=""):
        self.add(name, None, detail)

    def check(self, name, fn):
        try:
            ok, detail = fn()
            self.add(name, ok, detail)
        except Exception as exc:
            self.add(name, False, "{0}: {1}".format(type(exc).__name__, exc))
            log.debug("animkit selftest %s raised", name, exc_info=True)

    def measure(self, name, fn):
        """Run fn and record what it reports, never failing the run on time."""
        try:
            detail = fn()
            self.info(name, detail)
        except Exception as exc:
            self.info(name, "could not measure: {0}: {1}".format(
                type(exc).__name__, exc))
            log.debug("animkit selftest %s raised", name, exc_info=True)

    def emit(self):
        width = max(len(r[0]) for r in self.rows) + 2
        gates = [r for r in self.rows if r[1] is not None]
        passed = sum(1 for r in gates if r[1])
        print("")
        print("=" * (width + 30))
        print("animkit selftest")
        print("=" * (width + 30))
        for name, ok, detail in self.rows:
            flag = "TIME" if ok is None else ("PASS" if ok else "FAIL")
            line = "  {0} {1}".format(flag, name.ljust(width))
            if detail:
                line += " " + detail
            print(line)
        print("-" * (width + 30))
        print("  {0}/{1} passed".format(passed, len(gates)))
        if passed != len(gates):
            print("")
            print("  Failures above are real. Do not build on top of them.")
        print("=" * (width + 30))
        return passed == len(gates)


def _cleanup(remove_all_layers=False):
    for node in cmds.ls(PREFIX + "*") or []:
        try:
            cmds.delete(node)
        except Exception:
            pass
    for layer in cmds.ls(type="animLayer") or []:
        # BaseAnimation is created implicitly alongside the first real layer
        # and is NOT prefixed, so a prefix-only sweep leaves it behind and the
        # next run silently exercises different code paths. Only remove it
        # when the scene had no layers before we started.
        if layer.startswith(PREFIX) or remove_all_layers:
            try:
                cmds.delete(layer)
            except Exception:
                pass


def run():
    """Returns True if every check passed."""
    from animkit.core import curves, layers, selection
    from animkit.tools import tween
    from animkit.vendor import qt

    report = _Report()
    saved_selection = cmds.ls(selection=True, long=True) or []
    saved_time = cmds.currentTime(q=True)

    had_layers = bool(cmds.ls(type="animLayer"))
    _cleanup()
    cube = None

    try:
        # --- environment ---------------------------------------------------
        report.add(
            "environment",
            True,
            "Maya {0}, {1}".format(
                cmds.about(version=True), qt.QT_BINDING
            ),
        )

        # --- fixture -------------------------------------------------------
        cube = cmds.polyCube(name=PREFIX + "ctrl")[0]
        cmds.setKeyframe(cube + ".tx", time=1, value=0.0)
        cmds.setKeyframe(cube + ".tx", time=11, value=100.0)
        cmds.setKeyframe(cube + ".ry", time=1, value=0.0)
        cmds.setKeyframe(cube + ".ry", time=11, value=90.0)
        cmds.currentTime(6)
        # A key selection left in the Graph Editor would send every
        # current-time check down the selected-key path instead. Clear it.
        cmds.selectKey(clear=True)

        # --- curve resolution ----------------------------------------------
        def _resolve():
            curve = layers.resolve_curve(cube + ".tx")
            return bool(curve), str(curve)

        report.check("resolve_curve (unlayered)", _resolve)

        def _static():
            return layers.resolve_curve(cube + ".sx") is None, "static attr -> None"

        report.check("static attr not mistaken for animated", _static)

        # --- units ----------------------------------------------------------
        def _units():
            curve = curves.open_curve(layers.resolve_curve(cube + ".ry"))
            internal = curve.evaluate(11)
            ui = curve.to_ui(internal)
            ok = abs(internal - 1.5707963) < 1e-3 and abs(ui - 90.0) < 1e-3
            return ok, "{0:.4f} rad -> {1:.2f} deg".format(internal, ui)

        report.check("rotation unit conversion", _units)

        # --- the load-bearing assumption ------------------------------------
        def _propagate_raw():
            """Bare API write, no dgdirty. Documents the actual behaviour.

            Must use plain current-time getAttr -- the same path the drag and
            the viewport use. An earlier version of this check queried
            getAttr(plug, time=1), which forces a context re-evaluation and
            therefore passed while the real drag path was silently stale.
            """
            cmds.currentTime(1)
            curve = curves.open_curve(layers.resolve_curve(cube + ".tx"))
            index = curve.index_at(1)
            before = curve.key_value(index)
            curve.set_value_fast(index, curve.from_ui(42.0))
            seen = cmds.getAttr(cube + ".tx")
            curve.set_value_fast(index, before)
            cmds.dgdirty(cube + ".tx")
            propagates = abs(seen - 42.0) < 1e-3
            return True, (
                "bare setValue {0} visible to plain getAttr "
                "(informational)".format("IS" if propagates else "is NOT")
            )

        report.check("api write behaviour (no dgdirty)", _propagate_raw)

        def _propagate_dirty():
            """With dgdirty -- this is what TweenSession.update() does."""
            cmds.currentTime(1)
            curve = curves.open_curve(layers.resolve_curve(cube + ".tx"))
            index = curve.index_at(1)
            before = curve.key_value(index)
            curve.set_value_fast(index, curve.from_ui(42.0))
            cmds.dgdirty(cube + ".tx")
            seen = cmds.getAttr(cube + ".tx")
            curve.set_value_fast(index, before)
            cmds.dgdirty(cube + ".tx")
            ok = abs(seen - 42.0) < 1e-3
            return ok, "setValue + dgdirty -> getAttr sees {0:.2f}".format(seen)

        report.check("api_write_propagates (with dgdirty)", _propagate_dirty)

        # --- primitive probe: no animkit code involved ----------------------
        def _raw_setkeyframe():
            """Does cmds.setKeyframe(time=, value=) do what we assume?

            Reads the result two ways:
              cmds.keyframe(q, valueChange) -- the key value, straight off the
                                               curve, no DG evaluation
              cmds.getAttr                  -- through DG evaluation
            If these disagree, the write worked and the read is stale. If both
            are wrong, the write is wrong.
            """
            probe = cmds.polyCube(name=PREFIX + "probe")[0]
            cmds.setKeyframe(probe + ".tx", time=1, value=0.0)
            cmds.setKeyframe(probe + ".tx", time=11, value=100.0)
            cmds.currentTime(6)
            cmds.setKeyframe(probe + ".tx", time=(6, 6), value=75.0)
            key = cmds.keyframe(probe + ".tx", q=True, time=(6, 6),
                                valueChange=True)
            attr = cmds.getAttr(probe + ".tx")
            cmds.delete(probe)
            ok = bool(key) and abs(key[0] - 75.0) < 1e-3
            return ok, "key={0} getAttr={1:.2f}".format(key, attr)

        report.check("cmds.setKeyframe(value=) writes the key", _raw_setkeyframe)

        # --- tween session --------------------------------------------------
        def _tween():
            cmds.select(cube)
            cmds.currentTime(6)
            before = cmds.getAttr(cube + ".tx")
            session = tween.TweenSession()
            if not session.begin():
                return False, "begin() found nothing to tween"
            session.update(0.5)
            during = cmds.getAttr(cube + ".tx")
            session.commit(0.5)
            after = cmds.getAttr(cube + ".tx")
            key = cmds.keyframe(cube + ".tx", q=True, time=(6, 6),
                                valueChange=True)
            ok = after > before and after < 100.0
            return ok, (
                "before={0:.2f} during_drag={1:.2f} after_commit={2:.2f} "
                "key_on_curve={3}".format(before, during, after, key)
            )

        report.check("tween commit moves toward next key", _tween)

        def _no_compound():
            cmds.select(cube)
            cmds.currentTime(6)
            session = tween.TweenSession()
            session.begin()
            session.update(0.5)
            once = cmds.getAttr(cube + ".tx")
            for _ in range(10):
                session.update(0.5)
            many = cmds.getAttr(cube + ".tx")
            session.cancel()
            ok = abs(once - many) < 1e-6
            return ok, "{0:.4f} vs {1:.4f} after 10 updates".format(once, many)

        report.check("drag does not compound", _no_compound)

        def _key_at(time):
            """Key value straight off the curve -- no DG evaluation involved.

            getAttr is the wrong instrument here. Undo is a topology change, and
            topology changes leave DG reads stale until the plug is dirtied (see
            the probe above). Asserting on getAttr would be testing evaluation
            timing, not undo correctness.
            """
            vals = cmds.keyframe(
                cube + ".tx", q=True, time=(time, time), valueChange=True
            )
            return vals[0] if vals else None

        def _key_count():
            return cmds.keyframe(cube + ".tx", q=True, keyframeCount=True)

        def _undo_value():
            """Undo of a tween on an EXISTING key."""
            cmds.select(cube)
            cmds.currentTime(6)
            before = _key_at(6)
            keys_before = _key_count()

            session = tween.TweenSession()
            if not session.begin():
                return False, "begin() found nothing to tween"
            session.update(0.7)
            session.commit(0.7)
            moved = _key_at(6)

            cmds.undo()
            restored = _key_at(6)
            keys_after = _key_count()

            ok = (
                before is not None
                and moved is not None
                and restored is not None
                and abs(moved - before) > 1e-4
                and abs(restored - before) < 1e-4
                and keys_after == keys_before
            )
            return ok, (
                "key {0:.2f} -> {1:.2f} -> undo -> {2:.2f} | count {3} -> {4}".format(
                    before, moved, restored, keys_before, keys_after
                )
            )

        report.check("undo restores a tweened key", _undo_value)

        def _undo_inserted():
            """Undo of a tween that had to INSERT a key.

            The harder case, and the one the single-chunk design exists for:
            begin() inserts the key, commit() writes its value, and one Ctrl+Z
            must remove the key entirely rather than leaving an orphan behind.
            """
            cmds.select(cube)
            cmds.currentTime(8)  # deliberately between keys
            if _key_at(8) is not None:
                return False, "fixture already has a key at frame 8"
            keys_before = _key_count()

            if not tween.tween_once(0.5):
                return False, "tween_once found nothing to tween"
            keys_mid = _key_count()
            inserted = _key_at(8)

            cmds.undo()
            keys_after = _key_count()
            leftover = _key_at(8)

            ok = (
                keys_mid == keys_before + 1
                and inserted is not None
                and keys_after == keys_before
                and leftover is None
            )
            return ok, (
                "count {0} -> {1} -> undo -> {2}, inserted key {3}".format(
                    keys_before, keys_mid, keys_after,
                    "removed" if leftover is None else "LEFT BEHIND",
                )
            )

        report.check("undo removes an inserted key", _undo_inserted)

        def _cancel():
            cmds.select(cube)
            cmds.currentTime(6)
            before = cmds.getAttr(cube + ".tx")
            keys_before = cmds.keyframe(cube + ".tx", q=True, keyframeCount=True)
            session = tween.TweenSession()
            session.begin()
            session.update(0.9)
            session.cancel()
            ok = (
                abs(cmds.getAttr(cube + ".tx") - before) < 1e-4
                and cmds.keyframe(cube + ".tx", q=True, keyframeCount=True)
                == keys_before
            )
            return ok, "cancel left no trace"

        report.check("cancel leaves scene untouched", _cancel)

        def _boundary():
            cmds.select(cube)
            cmds.currentTime(11)  # last key, no next
            before = cmds.getAttr(cube + ".tx")
            tween.tween_once(1.0)
            ok = abs(cmds.getAttr(cube + ".tx") - before) < 1e-4
            return ok, "last key has no forward travel"

        report.check("boundary key does not jump", _boundary)

        # --- selected-key mode ----------------------------------------------
        def _selected_keys_mode():
            """Tween keys picked in the Graph Editor, away from current time.

            Two things must hold: the selected key moves, and NOTHING else on
            the curve does. A tween that quietly drags its neighbours along is
            worse than one that does nothing.
            """
            curve_name = layers.resolve_curve(cube + ".tx")
            cmds.select(cube)
            cmds.currentTime(1)  # deliberately NOT where the selected key is
            cmds.selectKey(clear=True)
            cmds.selectKey(curve_name, time=(6, 6))

            detected = selection.selected_keys()
            if not detected:
                return False, "selected_keys() found nothing"

            def kv(time):
                vals = cmds.keyframe(
                    cube + ".tx", q=True, time=(time, time), valueChange=True
                )
                return vals[0] if vals else None

            before = (kv(1), kv(6), kv(11))
            if None in before:
                return False, "fixture keys missing: {0}".format(before)

            session = tween.TweenSession()
            if not session.begin():
                cmds.selectKey(clear=True)
                return False, "begin() found nothing in selected-key mode"
            target = session.target
            session.commit(0.5)

            after = (kv(1), kv(6), kv(11))
            cmds.selectKey(clear=True)

            ok = (
                target == tween.TweenSession.TARGET_KEYS
                and abs(after[1] - before[1]) > 1e-4
                and abs(after[0] - before[0]) < 1e-6
                and abs(after[2] - before[2]) < 1e-6
            )
            return ok, "target={0}, only key@6 moved: {1} -> {2}".format(
                target, tuple(round(v, 2) for v in before),
                tuple(round(v, 2) for v in after),
            )

        report.check("selected-key mode tweens only those keys", _selected_keys_mode)

        # --- layers ---------------------------------------------------------
        def _layers():
            cmds.select(cube)
            layer = cmds.animLayer(PREFIX + "L1", addSelectedObjects=True)
            cmds.animLayer(layer, edit=True, selected=True)
            cmds.setKeyframe(cube + ".tx", time=1, value=0.0, animLayer=layer)
            cmds.setKeyframe(cube + ".tx", time=11, value=20.0, animLayer=layer)

            base = layers.resolve_curve(cube + ".tx", layers.root_layer())
            top = layers.resolve_curve(cube + ".tx", layer)
            if not base or not top or base == top:
                return False, "base={0} layer={1}".format(base, top)

            affecting = layers.affecting_layers(cube + ".tx")
            ok = layer in affecting and len(affecting) >= 2
            return ok, "affecting_layers -> {0}".format(affecting)

        report.check("layer curves resolve separately", _layers)

        def _layer_write():
            cmds.select(cube)
            cmds.currentTime(6)
            layer = PREFIX + "L1"
            if not cmds.objExists(layer):
                return False, "fixture layer missing"
            cmds.animLayer(layer, edit=True, selected=True)
            base_curve = layers.resolve_curve(cube + ".tx", layers.root_layer())
            base_before = curves.open_curve(base_curve).evaluate(6)
            tween.tween_once(0.6)
            base_after = curves.open_curve(base_curve).evaluate(6)
            ok = abs(base_before - base_after) < 1e-6
            return ok, "base layer untouched while Layer1 active"

        report.check("writes respect the active layer", _layer_write)

        # --- timing ---------------------------------------------------------
        def _timing():
            """How long the drag path takes on a small multi-control selection.

            Reported, never asserted. This runs on whatever machine the
            animator has, against a fixture sized to be quick rather than
            representative, so a threshold here would be noise. The two
            numbers to read:

              begin()   once at mouse-down. Over ~250ms and the click stops
                        feeling connected to the tool.
              update()  once per MOUSE-MOVE, so it has to fit in a frame.
                        Over 16ms and the slider visibly lags the cursor.

            For a real answer, run scripts/bench.py against a real rig with a
            real full-body selection. This check exists to catch an order-of-
            magnitude regression on a new Maya version, not to size a rig.
            """
            # Deselect every anim layer for the duration. The layer checks
            # above leave one active, and a control that is not a member of
            # the active layer has nothing to tween on it -- correct behaviour
            # from the tool, but it would silently reduce this check to
            # "skipped" forever. Restored below, because the selftest promises
            # not to disturb the scene it runs in.
            was_selected = []
            for layer in cmds.ls(type="animLayer") or []:
                try:
                    if cmds.animLayer(layer, q=True, selected=True):
                        was_selected.append(layer)
                        cmds.animLayer(layer, edit=True, selected=False)
                except Exception:
                    pass

            controls = 25
            channels = ("translateX", "translateY", "translateZ",
                        "rotateX", "rotateY", "rotateZ")
            nodes = []
            for i in range(controls):
                node = cmds.createNode(
                    "transform", name="{0}perf{1:02d}".format(PREFIX, i)
                )
                for channel in channels:
                    cmds.setKeyframe(node + "." + channel, time=1, value=0.0)
                    cmds.setKeyframe(node + "." + channel, time=11, value=10.0)
                nodes.append(node)

            cmds.select(nodes)
            cmds.currentTime(6)
            cmds.selectKey(clear=True)

            plugs = selection.plugs_from_selection()

            start = time.perf_counter()
            session = tween.TweenSession()
            started = session.begin()
            begin_ms = (time.perf_counter() - start) * 1000.0

            def _restore_layers():
                for layer in was_selected:
                    try:
                        cmds.animLayer(layer, edit=True, selected=True)
                    except Exception:
                        pass

            if not started:
                session.force_close()
                cmds.delete(nodes)
                _restore_layers()
                return "begin() found nothing to tween -- timing skipped"

            try:
                # Median of a handful, not a single sample: one GC pause in a
                # 3ms operation reads as a 3x regression otherwise.
                samples = []
                for i in range(11):
                    t = ((i % 10) - 5) / 10.0
                    start = time.perf_counter()
                    session.update(t)
                    samples.append((time.perf_counter() - start) * 1000.0)
                samples.sort()
                update_ms = samples[len(samples) // 2]

                start = time.perf_counter()
                session.commit(0.5)
                commit_ms = (time.perf_counter() - start) * 1000.0
            finally:
                session.force_close()
                cmds.delete(nodes)
                _restore_layers()

            verdict = "" if update_ms <= 16.0 else "  <-- OVER FRAME BUDGET"
            return (
                "{0} plugs | begin {1:.1f}ms  update {2:.1f}ms/move  "
                "commit {3:.1f}ms{4}".format(
                    len(plugs), begin_ms, update_ms, commit_ms, verdict
                )
            )

        report.measure("drag timing (25 controls)", _timing)

        def _cache_scope():
            """The scope must not outlive the operation that opened it.

            A cache still active after begin() returned would serve the next
            drag whatever the layer selection was during this one.
            """
            from animkit.core import cache

            before = cache.active()
            cmds.select(cube)
            cmds.currentTime(6)
            session = tween.TweenSession()
            session.begin()
            during = cache.active()
            session.cancel()
            after = cache.active()
            ok = not before and not during and not after
            return ok, "no cache scope leaks past begin()"

        report.check("cache scope closes with the operation", _cache_scope)

        # --- settings -------------------------------------------------------
        def _settings():
            """Round-trip a value, then deliberately corrupt the file.

            The second half is the point. startup() runs from userSetup.py, so
            a settings file that cannot be parsed must degrade to shipped
            defaults rather than raising -- an exception there breaks Maya
            launch for every animator with the module installed, before there
            is any UI to report it in.

            This writes garbage to the REAL settings file, so it saves the
            original bytes first and puts them back in a finally. If this
            check ever fails partway through, check that your settings
            survived before doing anything else.
            """
            import os

            import animkit
            from animkit.core import settings

            target = settings.path()
            existed = os.path.exists(target)
            original = None
            if existed:
                try:
                    with open(target, "rb") as handle:
                        original = handle.read()
                except Exception:
                    return False, "could not back up the real settings file"

            notes = []
            try:
                settings.set("tween.mode", "linear")
                settings.forget()
                roundtrip = settings.get("tween.mode") == "linear"
                notes.append("round-trip {0}".format("ok" if roundtrip else "FAILED"))

                folder = os.path.dirname(target)
                if folder and not os.path.isdir(folder):
                    os.makedirs(folder)
                with open(target, "w") as handle:
                    handle.write("{ this is definitely not json")
                settings.forget()

                startup_ok = True
                try:
                    animkit.startup()
                except Exception as exc:
                    startup_ok = False
                    notes.append("startup RAISED {0}".format(type(exc).__name__))

                defaults_ok = settings.load() == settings.DEFAULTS
                notes.append(
                    "corrupt file -> {0}".format(
                        "defaults" if defaults_ok else "NOT defaults"
                    )
                )
            finally:
                settings.forget()
                try:
                    if existed and original is not None:
                        with open(target, "wb") as handle:
                            handle.write(original)
                    elif os.path.exists(target):
                        os.remove(target)
                except Exception:
                    notes.append("WARNING: could not restore your settings file")
                settings.load()

            return (roundtrip and startup_ok and defaults_ok), ", ".join(notes)

        report.check("settings survive a corrupt file", _settings)

        # --- keyframe operations --------------------------------------------
        def _keys_fixture():
            """A fresh control with keys at 1/6/11, cursor on 6, no layers active."""
            from animkit.tools import keys  # noqa: F401

            for layer in cmds.ls(type="animLayer") or []:
                try:
                    if cmds.animLayer(layer, q=True, selected=True):
                        cmds.animLayer(layer, edit=True, selected=False)
                except Exception:
                    pass

            node = cmds.createNode("transform", name=PREFIX + "keys")
            cmds.setKeyframe(node + ".tx", time=1, value=0.0)
            cmds.setKeyframe(node + ".tx", time=6, value=50.0)
            cmds.setKeyframe(node + ".tx", time=11, value=100.0)
            cmds.select(node)
            cmds.currentTime(6)
            cmds.selectKey(clear=True)
            return node

        def _all_ops_run():
            """Every registered operation, invoked once. Nothing may raise.

            Cheap, and it is the check that would have caught both of the bugs
            this phase shipped with: a cycle that reported success while
            changing nothing, and a key offset that produced an invisible
            sub-frame duplicate.
            """
            from animkit.tools import keys

            failed = []
            for op in keys.OPERATIONS:
                node = _keys_fixture()
                try:
                    op.invoke()
                except Exception as exc:
                    failed.append("{0} ({1})".format(op.name, type(exc).__name__))
                finally:
                    try:
                        cmds.delete(node)
                    except Exception:
                        pass
            ok = not failed
            return ok, (
                "{0} operations ran".format(len(keys.OPERATIONS)) if ok
                else "RAISED: " + ", ".join(failed)
            )

        report.check("every keyframe operation runs", _all_ops_run)

        def _op_undo():
            """One operation, one undo step -- asserted on curve state.

            getAttr is the wrong instrument: moving a key is a topology change,
            and DG reads stay stale until the plug is dirtied.
            """
            from animkit.tools import keys

            node = _keys_fixture()
            try:
                def state():
                    return (
                        tuple(cmds.keyframe(node + ".tx", q=True,
                                            timeChange=True) or []),
                        tuple(cmds.keyframe(node + ".tx", q=True,
                                            valueChange=True) or []),
                    )

                before = state()
                moved = keys.offset(frames=2)
                after = state()
                cmds.undo()
                restored = state()
                ok = moved > 0 and after != before and restored == before
                return ok, "times {0} -> {1} -> undo -> {2}".format(
                    before[0], after[0], restored[0]
                )
            finally:
                try:
                    cmds.delete(node)
                except Exception:
                    pass

        report.check("keyframe op is one undo step", _op_undo)

        def _op_merges_not_nudges():
            """A key moved onto an occupied frame must MERGE, not land at
            1.0000001700680272.

            Maya's own behaviour is to nudge the key a sliver aside, which
            reads as the right frame in the Graph Editor and plays back wrong.
            keys._move_keys clears the destination first; this is the check
            that says it still does.
            """
            from animkit.tools import keys

            node = _keys_fixture()
            try:
                keys.offset(frames=-5)  # key at 6 lands on the key at 1
                times = sorted(cmds.keyframe(node + ".tx", q=True,
                                             timeChange=True) or [])
                ok = times == [1.0, 11.0]
                return ok, "times after collision: {0}".format(times)
            finally:
                try:
                    cmds.delete(node)
                except Exception:
                    pass

        report.check("moved key merges onto an occupied frame", _op_merges_not_nudges)

        def _cycle_applies():
            """setInfinity on an animCurve NODE is silently ignored -- it
            returns cleanly and changes nothing. keys.set_cycle goes through
            setAttr on the resolved curve instead. This check exists because
            the failure mode is an operation that reports success."""
            from animkit.tools import keys

            node = _keys_fixture()
            try:
                before = cmds.setInfinity(node + ".tx", q=True, preInfinite=True)
                keys.set_cycle(mode="cycle")
                after = cmds.setInfinity(node + ".tx", q=True, preInfinite=True)
                ok = after == ["cycle"] and before != after
                return ok, "preInfinity {0} -> {1}".format(before, after)
            finally:
                try:
                    cmds.delete(node)
                except Exception:
                    pass

        report.check("cycle actually reaches the curve", _cycle_applies)

        # --- mirror ---------------------------------------------------------
        def _mirror_fixture(root_x=0.0):
            """A small symmetric rig with a DELIBERATELY flipped right side.

            The 180-degree Y rotation on the right offset group is the whole
            point: a mirror built on a hardcoded axis table passes on a
            symmetric cube and fails here, because the two sides' frames do not
            agree.
            """
            root = cmds.createNode("transform", name=PREFIX + "mirrorRoot")
            made = {}
            for side, sign, flip in (("L", 1.0, 0.0), ("R", -1.0, 180.0)):
                offset = cmds.createNode(
                    "transform", name="{0}{1}_offset".format(PREFIX, side),
                    parent=root,
                )
                cmds.setAttr(offset + ".translate", sign * 10, 0, 0)
                cmds.setAttr(offset + ".rotateY", flip)
                for channel in ("translateX", "translateY", "translateZ",
                                "rotateX", "rotateY", "rotateZ"):
                    cmds.setAttr(offset + "." + channel, lock=True)

                ctrl = cmds.createNode(
                    "transform", name="{0}{1}_arm_ctrl".format(PREFIX, side),
                    parent=offset,
                )
                tip = cmds.createNode(
                    "transform", name="{0}{1}_tip_ctrl".format(PREFIX, side),
                    parent=ctrl,
                )
                cmds.setAttr(tip + ".translate", 5, 0, 0)
                for channel in ("translateX", "translateY", "translateZ"):
                    cmds.setAttr(tip + "." + channel, lock=True)
                made[side] = [ctrl, tip]

            cmds.setAttr(root + ".translateX", root_x)

            # KEY the controls. The rest pose is derived from which channels
            # carry animation, so an unkeyed control is taken to rest wherever it
            # stands -- correct behaviour, and useless as a mirror fixture.
            for pair in zip(made["L"], made["R"]):
                for node in pair:
                    for channel in ("rotateX", "rotateY", "rotateZ"):
                        cmds.setKeyframe(node + "." + channel, time=1, value=0.0)

            return root, list(zip(made["L"], made["R"]))

        def _mirror_geometry():
            """The invariant that catches a nearly-right mirror.

            Every paired control's world POSITION must be the reflection of its
            counterpart's. Deliberately run with the character AWAY FROM THE
            ORIGIN, because that is the case that distinguishes a local pose
            delta from a world one -- a world delta mirrors the character's
            placement too and puts the arm 200 units from where it belongs.

            Position, not matrix: W * reflection has a negative determinant, so
            no rigid transform can equal it and a matrix assertion there is
            unsatisfiable rather than strict.
            """
            import maya.api.OpenMaya as om2

            from animkit.core import xform
            from animkit.tools import pose

            root, pairs = _mirror_fixture(root_x=100.0)
            try:
                cmds.setAttr(pairs[0][0] + ".rotate", 12, -20, 35)
                cmds.setKeyframe(pairs[0][0] + ".rotate")
                cmds.setAttr(pairs[1][0] + ".rotate", -8, 3, 22)
                cmds.setKeyframe(pairs[1][0] + ".rotate")

                nodes = [n for pair in pairs for n in pair]
                cmds.select(nodes)
                written = pose.mirror_selected()
                if not written:
                    return False, "mirror wrote nothing"

                current = xform.world_matrix(root)
                plane = xform.reflection_matrix((0, 0, 0), (1, 0, 0))
                reflection = om2.MMatrix(current.inverse() * plane * current)

                worst = 0.0
                for a, b in pairs:
                    want = xform.reflect_point(
                        cmds.xform(a, q=True, ws=True, t=True), reflection
                    )
                    got = cmds.xform(b, q=True, ws=True, t=True)
                    worst = max(
                        worst, max(abs(got[i] - want[i]) for i in range(3))
                    )
                return worst < 1e-3, (
                    "worst position error {0:.6f} across {1} pairs, "
                    "character at x=100".format(worst, len(pairs))
                )
            finally:
                try:
                    cmds.delete(root)
                except Exception:
                    pass

        report.check("mirror reflects world positions", _mirror_geometry)

        def _mirror_flip_involution():
            """Flipping twice must return the original pose."""
            from animkit.tools import pose

            root, pairs = _mirror_fixture()
            try:
                cmds.setAttr(pairs[0][0] + ".rotate", 12, -20, 35)
                cmds.setKeyframe(pairs[0][0] + ".rotate")
                nodes = [n for pair in pairs for n in pair]

                def snapshot():
                    return [
                        cmds.xform(n, q=True, ws=True, t=True) for n in nodes
                    ]

                before = snapshot()
                cmds.select(nodes)
                pose.flip_selected()
                cmds.select(nodes)
                pose.flip_selected()
                after = snapshot()

                worst = max(
                    abs(before[i][axis] - after[i][axis])
                    for i in range(len(nodes)) for axis in range(3)
                )
                return worst < 1e-3, "worst drift {0:.6f} after two flips".format(
                    worst
                )
            finally:
                try:
                    cmds.delete(root)
                except Exception:
                    pass

        report.check("flip twice is identity", _mirror_flip_involution)

        def _mirror_refuses_asymmetric():
            """An asymmetric rest pose must be REFUSED, not guessed at.

            This is the check that separates "the tool says it cannot do this
            rig" from "the tool silently corrupts a pose". A mirror on an
            asymmetric rest does not raise on its own -- it produces a pose
            whose silhouette reads and whose limb is wrong.
            """
            from animkit.core import cache
            from animkit.tools import pose

            root, pairs = _mirror_fixture()
            try:
                group = PREFIX + "R_offset"
                cmds.setAttr(group + ".translateY", lock=False)
                cmds.setAttr(group + ".translateY", 5)
                cmds.setAttr(group + ".translateY", lock=True)
                cache.invalidate()

                left, right = pairs[0]
                cmds.setAttr(left + ".rotate", 12, -20, 35)
                cmds.setKeyframe(left + ".rotate")
                before = cmds.getAttr(right + ".rotate")[0]

                cmds.select([left, right])
                written = pose.mirror_selected()
                after = cmds.getAttr(right + ".rotate")[0]

                unchanged = all(
                    abs(before[i] - after[i]) < 1e-6 for i in range(3)
                )
                return (written == 0 and unchanged), (
                    "refused and wrote nothing" if written == 0
                    else "WROTE {0} channels on an asymmetric rig".format(written)
                )
            finally:
                try:
                    cmds.delete(root)
                except Exception:
                    pass

        report.check("mirror refuses an asymmetric rest pose",
                     _mirror_refuses_asymmetric)

        def _pose_round_trip():
            from animkit.tools import pose

            root, pairs = _mirror_fixture()
            try:
                ctrl = pairs[0][0]
                cmds.setAttr(ctrl + ".rotate", 12, -20, 35)
                cmds.setKeyframe(ctrl + ".rotate")
                cmds.select(ctrl)
                pose.copy_pose()
                cmds.setAttr(ctrl + ".rotate", 0, 0, 0)
                pose.paste_pose()

                # Read the CURVE, not the attribute. getAttr is stale after a
                # key is written until the plug is dirtied, so asserting on it
                # here would test Maya's evaluation timing rather than whether
                # the paste landed. Same rule as the undo checks above.
                got = []
                for axis in "XYZ":
                    values = cmds.keyframe(
                        "{0}.rotate{1}".format(ctrl, axis), q=True,
                        valueChange=True,
                    )
                    got.append(values[-1] if values else 0.0)
                ok = all(
                    abs(got[i] - v) < 1e-3
                    for i, v in enumerate((12.0, -20.0, 35.0))
                )
                keyed = bool(
                    cmds.keyframe(ctrl + ".rotateX", q=True, keyframeCount=True)
                )
                return (ok and keyed), "pasted {0} and keyed it: {1}".format(
                    ["%.1f" % v for v in got], keyed
                )
            finally:
                try:
                    cmds.delete(root)
                except Exception:
                    pass

        report.check("pose copy/paste round-trips", _pose_round_trip)

        # --- selection sets ---------------------------------------------
        #
        # Sets are stored as objectSets carrying tag attributes, which is the
        # kind of thing that works in every Maya until the one where addAttr
        # or a message connection behaves differently on a referenced rig.
        # Assert on the SCENE afterwards, never on the return value.

        def _selection_sets():
            from animkit.tools import sets

            root = cmds.createNode("transform", name=PREFIX + "setRoot")
            a = cmds.createNode("transform", name=PREFIX + "setA", parent=root)
            b = cmds.createNode("transform", name=PREFIX + "setB", parent=root)
            made = None
            try:
                cmds.select([a, b])
                made = sets.store(PREFIX + "pair")
                if made is None:
                    return False, "store() returned nothing"

                cmds.select(clear=True)
                count = sets.recall(PREFIX + "pair")
                selected = set(cmds.ls(selection=True) or [])

                # The property a name-based store cannot hold: rename a member
                # and the set still resolves it.
                renamed = cmds.rename(a, PREFIX + "setA_renamed")
                cmds.select(clear=True)
                sets.recall(PREFIX + "pair")
                after_rename = set(cmds.ls(selection=True) or [])

                ok = (
                    count == 2
                    and selected == {a, b}
                    and after_rename == {renamed, b}
                    and made.slot == 1
                )
                return ok, "recalled {0}, survived a rename: {1}, slot {2}".format(
                    count, after_rename == {renamed, b}, made.slot
                )
            finally:
                for node in ([made.node] if made is not None else []) + [root]:
                    try:
                        if cmds.objExists(node):
                            cmds.delete(node)
                    except Exception:
                        pass

        report.check("selection sets store and recall", _selection_sets)

        def _reference_media():
            """A dropped sequence must load as a sequence and land on now.

            Everything about this check is environment-specific, which is why
            it is here and not only in the test suite: whether Maya's image
            reader is present, whether it will read a png this machine wrote,
            and whether setting `useFrameExtension` still wires time to
            `frameExtension` by itself on this version. The last one is not
            documented anywhere and is what the loader depends on instead of
            writing an expression.

            Writes three real frames to Maya's temp dir and deletes them.
            """
            import maya.api.OpenMaya as om
            from animkit.tools import reference

            folder = os.path.join(
                cmds.internalVar(userTmpDir=True) or "", PREFIX + "media"
            )
            if not os.path.isdir(folder):
                os.makedirs(folder)

            frames = []
            made = None
            try:
                for number in (1, 2, 3):
                    path = os.path.join(folder, "%sref.%04d.png"
                                        % (PREFIX, number))
                    image = om.MImage()
                    image.create(32, 18, 4)
                    image.writeToFile(path, "png")
                    frames.append(path)

                cmds.currentTime(6)
                # Every frame at once, which is what selecting a sequence in
                # Explorer produces. One plane is the correct answer.
                made = reference.drop(frames, frame=6)
                if len(made) != 1:
                    return False, "a 3-frame drop made %d plane(s)" % len(made)

                entry = made[0]
                shown = cmds.getAttr(entry.shape + ".outputFrameExtension")
                # `frame=6` is passed explicitly rather than leaning on where a
                # drop lands by default: that is the `reference.start_at`
                # setting, this self-test reads the animator's REAL prefs, and
                # what is being checked here is the offset arithmetic -- which
                # must not start passing or failing on somebody's preference.
                ok = (
                    entry.loaded
                    and entry.is_sequence
                    and shown == 1
                    and entry.frame_range() == (1, 3)
                )
                return ok, (
                    "1 plane from 3 files, pixels read: {0}, frame 1 shown at "
                    "time 6: {1}".format(entry.loaded, shown == 1)
                )
            finally:
                try:
                    if made:
                        reference.remove(nodes=made)
                except Exception:
                    pass
                for path in frames:
                    try:
                        os.remove(path)
                    except Exception:
                        pass
                try:
                    os.rmdir(folder)
                except Exception:
                    pass

        report.check("reference media loads and lands on now", _reference_media)

        def _reference_free_plane():
            """A dropped reference must be an object the animator can move.

            Two things here are only true in a real Maya and both are silent
            when wrong. A free image plane is sized by `width`/`height` and
            NOT by `sizeX`/`sizeY` -- set the wrong pair and it stays Maya's
            square 10x10, letterboxing every reference. And `pin()` renames
            the node as it reparents it, so a stale name held across the call
            fails on the next setAttr while pointing at a node that exists.
            """
            import maya.api.OpenMaya as om
            from animkit.tools import reference

            folder = os.path.join(
                cmds.internalVar(userTmpDir=True) or "", PREFIX + "media"
            )
            if not os.path.isdir(folder):
                os.makedirs(folder)
            path = os.path.join(folder, PREFIX + "wide.png")
            made = None
            try:
                image = om.MImage()
                image.create(320, 180, 4)     # 16:9
                image.writeToFile(path, "png")

                made = reference.load(path, attach=reference.ATTACH_FREE)
                if made is None:
                    return False, "the reference did not load"

                node = made.transform
                movable = all(
                    cmds.getAttr(node + "." + attr, settable=True)
                    and not cmds.getAttr(node + "." + attr, lock=True)
                    for attr in ("translateX", "rotateY", "scaleX")
                )
                top_level = cmds.listRelatives(node, parent=True) is None
                ratio = (cmds.getAttr(made.shape + ".width")
                         / cmds.getAttr(made.shape + ".height"))
                shaped = abs(ratio - 320.0 / 180.0) < 0.01

                reference.pin()
                pinned = not reference.references()[0].is_free
                reference.pin()
                freed = reference.references()[0].is_free

                ok = movable and top_level and shaped and pinned and freed
                return ok, (
                    "free and movable: {0}, built 16:9 not square: {1}, "
                    "pin round-trips: {2}".format(
                        movable and top_level, shaped, pinned and freed)
                )
            finally:
                try:
                    reference.remove(nodes=reference.references())
                except Exception:
                    pass
                for leftover in (path,):
                    try:
                        os.remove(leftover)
                    except Exception:
                        pass
                try:
                    os.rmdir(folder)
                except Exception:
                    pass

        report.check("reference plane is movable and correctly shaped",
                     _reference_free_plane)

        def _video_conversion():
            """A dropped video has to become a reference, not a blank plane.

            The single most environment-dependent thing animkit does. It
            depends on a binary existing at the right path for THIS platform,
            being executable (which a copied file on macOS often is not), and
            on Maya reading back what it wrote. None of that is knowable
            anywhere but here.

            Makes its own two-second clip with ffmpeg, converts it, loads it,
            and deletes everything including the cache entry.
            """
            import subprocess

            from animkit.core import settings as prefs, transcode
            from animkit.tools import reference

            binary = transcode.executable(
                prefs.get("reference.ffmpeg") or None)
            if binary is None:
                return False, (
                    "no ffmpeg found -- expected one bundled at "
                    "animkit/vendor/ffmpeg/<platform>/, so a dropped video "
                    "will draw nothing"
                )

            folder = os.path.join(
                cmds.internalVar(userTmpDir=True) or "", PREFIX + "video"
            )
            if not os.path.isdir(folder):
                os.makedirs(folder)
            source = os.path.join(folder, PREFIX + "clip.mp4")
            made = None
            cache = None
            try:
                subprocess.check_call(
                    [binary, "-hide_banner", "-loglevel", "error", "-y",
                     "-f", "lavfi", "-i",
                     "testsrc=size=320x180:rate=30:duration=2",
                     "-pix_fmt", "yuv420p", source],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                )

                rate = reference.scene_fps()
                made = reference.load(source)
                if made is None:
                    return False, "the video did not load at all"
                cache = os.path.dirname(made.path)

                span = made.frame_range()
                expected = int(2 * rate)
                # Two seconds of footage at the scene rate, within a frame
                # either way for how ffmpeg rounds the last one.
                timed = span is not None and abs(span[1] - expected) <= 2
                ok = made.loaded and made.is_sequence and timed
                return ok, (
                    "{0} -> {1} frames at {2:.3f} fps, pixels read: {3}"
                    .format(os.path.basename(source),
                            span[1] if span else 0, rate, made.loaded)
                )
            finally:
                try:
                    if made is not None:
                        reference.remove(nodes=[made])
                except Exception:
                    pass
                try:
                    os.remove(source)
                except Exception:
                    pass
                for target in (cache, folder):
                    if not target:
                        continue
                    try:
                        for name in os.listdir(target):
                            os.remove(os.path.join(target, name))
                        os.rmdir(target)
                    except Exception:
                        pass

        report.check("dropped video converts to a sequence", _video_conversion)

        def _viewport_drop():
            """Whether a viewport can actually be hooked for drops.

            The only check here that cannot be written as a test: it needs a
            real model panel, which mayapy does not have. Headless it reports a
            measurement rather than a pass, because "no viewports" is not a
            failure -- it is the correct answer under mayapy and in batch.

            Leaves the session as it found it. An animator running the
            self-test has not asked for their drop handling to change.
            """
            from animkit.ui import viewport_drop

            panels = viewport_drop.model_panels()
            was_installed = viewport_drop.is_installed()
            if not panels:
                report.info("viewport drop",
                            "no model panels -- headless, nothing to hook")
                return

            try:
                hooked = viewport_drop.install()
                missed = [p for p in panels if p not in hooked]
                detail = "%d of %d panel(s) accept drops" % (
                    len(hooked), len(panels))
                if missed:
                    detail += "; NOT hooked: " + ", ".join(missed[:3])
                report.add("viewport drop", not missed, detail)
            finally:
                if not was_installed:
                    try:
                        viewport_drop.uninstall()
                    except Exception:
                        pass

        try:
            _viewport_drop()
        except Exception as exc:
            report.add("viewport drop", False,
                       "{0}: {1}".format(type(exc).__name__, exc))

        def _radial_menus():
            """Every radial wedge must resolve to a real command.

            MENUS names commands by string, so this is where a renamed
            operation shows up -- as a radial with a hole in it rather than as
            an error anybody would notice.
            """
            from animkit.ui import radial

            known = radial._operations_by_name()
            missing = [
                name
                for names in radial.MENUS.values()
                for name in names
                if name not in known
            ]
            widest = max(len(n) for n in radial.MENUS.values())
            ok = not missing and widest <= radial.MAX_WEDGES
            detail = "{0} menu(s), widest {1} wedge(s)".format(
                len(radial.MENUS), widest
            )
            if missing:
                detail += "; UNRESOLVED: " + ", ".join(missing)
            return ok, detail

        report.check("radial menus resolve", _radial_menus)

        def _radial_release_is_safe():
            """The release command gets pressed with nothing open, constantly.

            Bound on its own by mistake, or pressed while a dialog has focus.
            A traceback here would fire in the middle of an animator's day and
            leave the overlay on screen over the viewport.
            """
            from animkit.ui import radial

            radial.cancel()
            first = radial.release()
            second = radial.release()
            return (
                first is None and second is None and not radial.is_open()
            ), "release with nothing open does nothing, twice"

        report.check("radial release is safe with nothing open",
                     _radial_release_is_safe)

        def _commands_registered():
            """Every registered command must be valid Python that imports.

            A runTimeCommand's body is a STRING. Nothing compiles it until an
            animator presses the key, so a typo in one ships silently and
            surfaces as a hotkey that does nothing.
            """
            from animkit import commands

            broken = []
            for name, _annotation, command in commands.COMMANDS:
                try:
                    compile(command, "<runTimeCommand %s>" % name, "exec")
                except Exception as exc:
                    broken.append("%s (%s)" % (name, exc))
            duplicates = len(commands.COMMANDS) != len(
                {n for n, _a, _c in commands.COMMANDS}
            )
            detail = "%d command(s)" % len(commands.COMMANDS)
            if broken:
                detail += "; WILL NOT COMPILE: " + ", ".join(broken[:3])
            if duplicates:
                detail += "; DUPLICATE NAMES"
            return (not broken and not duplicates), detail

        report.check("every runTimeCommand compiles", _commands_registered)

    finally:
        _cleanup(remove_all_layers=not had_layers)
        try:
            cmds.currentTime(saved_time)
        except Exception:
            pass
        try:
            existing = [n for n in saved_selection if cmds.objExists(n)]
            if existing:
                cmds.select(existing)
            else:
                cmds.select(clear=True)
        except Exception:
            pass

    return report.emit()


def run_safe():
    """run() with a traceback guard, for shelf buttons."""
    try:
        return run()
    except Exception:
        traceback.print_exc()
        return False
