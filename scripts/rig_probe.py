"""Report what animkit sees when it looks at your rig. Read-only.

    import sys; sys.path.append(r"C:\\Users\\ishan\\Desktop\\Animbot")
    import scripts.rig_probe as probe
    probe.run()          # on the current selection

Nothing here writes to the scene, creates a node, or leaves an undo entry. It
answers the questions that decide whether a mirror can work, in the order they
are asked:

    1. what is the rig root, and where is its symmetry plane
    2. does each selected control resolve a REST pose, and where
    3. does naming propose a counterpart, and does geometry accept it
    4. what pairing.analyse() pairs up
    5. every way the rest pose is asymmetric, in one list

When a mirror does nothing, exactly one of those is the reason, and this says
which. Paste the whole output.
"""

import sys

MAX_ROWS = 40


def _p(text=""):
    print(text)
    sys.stdout.flush()


def _fmt(values, spec="%8.3f"):
    return "[" + ", ".join(spec % v for v in values) + "]"


def run(nodes=None, verbose=True):
    from maya import cmds

    from animkit.core import cache, pairing, selection, xform
    from animkit.tools import pose as pose_mod

    nodes = nodes if nodes is not None else selection.selected_nodes()
    if not nodes:
        _p("nothing selected -- select some rig controls and run again")
        return

    _p("")
    _p("=" * 78)
    _p("  animkit rig probe -- Maya %s, %d node(s) selected"
       % (cmds.about(version=True), len(nodes)))
    _p("=" * 78)

    with cache.scope():
        # --- 1. root and plane ----------------------------------------------
        root = pairing.root_of(nodes[0])
        _p("")
        _p("1. RIG ROOT AND SYMMETRY PLANE")
        _p("   root_of(%s)" % nodes[0])
        _p("     -> %r" % root)
        _p("   namespace  : %r" % pairing.namespace_of(nodes[0]))
        try:
            point, normal = xform.mirror_plane(root)
            _p("   plane point (root rest position) : %s" % _fmt(point))
            _p("   plane normal (root rest X axis)  : %s" % _fmt(normal))
            reflection = xform.reflection_matrix(point, normal)
        except Exception as exc:
            _p("   *** could not derive a mirror plane: %s: %s"
               % (type(exc).__name__, exc))
            return

        pool = pairing.search_pool(nodes[0])
        settable = pairing.with_settable_channels(pool)
        controls = pose_mod.controls_in(pool)
        _p("   search pool: %d transform(s) under the root in this namespace"
           % len(pool))
        _p("   of which     %d are CONTROLS -- controller tags, control"
           % len(controls))
        _p("                shapes or a control set, asked of the rig")
        _p("   for contrast %d merely have a settable transform channel."
           % len(settable))
        _p("                A big gap here is NORMAL and is exactly why the")
        _p("                control question is asked of the rig: riggers do")
        _p("                not lock their internals, so the settable count")
        _p("                counts the plumbing too. Set Rest uses CONTROLS.")
        if len(pool) < 2:
            _p("   *** the pool is too small to find any counterpart. If the rig")
            _p("       is referenced, root_of may be picking the wrong top node.")

        # --- 2. rest poses ---------------------------------------------------
        _p("")
        _p("2. REST POSE PER CONTROL")
        _p("   A control whose rest position equals its CURRENT position while")
        _p("   the control is posed means the derived rest is wrong -- animkit")
        _p("   thinks the pose IS the rest, so there is no delta to mirror.")
        _p("")
        _p("   %-34s %-26s %s" % ("control", "rest position", "current position"))
        _p("   " + "-" * 72)
        for node in nodes[:MAX_ROWS]:
            try:
                rest = pairing.rest_position(node)
                now = cmds.xform(node, q=True, worldSpace=True, translation=True)
                moved = any(abs(rest[i] - now[i]) > 1e-4 for i in range(3))
                flag = "" if moved else "   <- rest == current"
                _p("   %-34s %s %s%s"
                   % (node[-34:], _fmt(rest), _fmt(now), flag))
            except Exception as exc:
                _p("   %-34s *** %s: %s" % (node[-34:], type(exc).__name__, exc))

        _p("")
        _p("   settable transform channels (what Maya lets animkit write):")
        for node in nodes[:5]:
            try:
                channels = sorted(xform.settable_transform_channels(node))
                survey = sorted(selection._survey_node(node))
            except Exception as exc:
                _p("     %-30s *** %s" % (node[-30:], exc))
                continue
            _p("     %-30s transform=%d  all=%d"
               % (node[-30:], len(channels), len(survey)))
            if not channels:
                _p("       *** NO settable transform channels. Locked, non-")
                _p("           keyable or constraint-driven -- animkit will")
                _p("           not pose it.")

        # --- 3. pairing ------------------------------------------------------
        _p("")
        _p("3. COUNTERPART SEARCH")
        _p("   naming proposes, geometry decides. 'named' is what the token")
        _p("   library suggested and that actually exists in the scene.")
        _p("")
        for node in nodes[:MAX_ROWS]:
            named = [n for n in pairing.candidate_names(node) if cmds.objExists(n)]
            try:
                found = pairing.counterpart(node, reflection=reflection)
            except Exception as exc:
                found = "*** %s: %s" % (type(exc).__name__, exc)
            _p("   %s" % node)
            _p("       named candidates : %s"
               % (", ".join(named[:4]) if named else "(none exist)"))
            _p("       counterpart      : %r" % (found,))

            if isinstance(found, str) and found and not found.startswith("***"):
                continue

            # Say WHY it failed, per candidate.
            for candidate in named[:4]:
                try:
                    want = xform.reflect_point(
                        pairing.rest_position(node), reflection
                    )
                    got = pairing.rest_position(candidate)
                    distance, tolerance = pairing.rest_offset(
                        node, candidate, reflection
                    )
                    same_channels = (
                        xform.settable_transform_channels(node)
                        == xform.settable_transform_channels(candidate)
                    )
                    _p("       %-28s rest %s vs wanted %s"
                       % (candidate[-28:], _fmt(got), _fmt(want)))
                    _p("       %-28s off by %.4f (tolerance %.4f), match: %s"
                       % ("", distance, tolerance, same_channels))
                except Exception as exc:
                    _p("       %-28s *** %s" % (candidate[-28:], exc))

        # --- 4/5. pairing and symmetry, in ONE call --------------------------
        symmetry = pairing.analyse(nodes, reflection=reflection)
        pairs = symmetry.pairs
        _p("")
        _p("4. PAIRING RESULT")
        _p("   paired   : %d" % len(pairs))
        for a, b in pairs[:MAX_ROWS]:
            kind = "CENTRE (mirrors onto itself)" if a == b else ""
            _p("       %-32s -> %-32s %s" % (a[-32:], b[-32:], kind))
        _p("   unmatched: %d (no plausible counterpart at all)"
           % len(symmetry.missing))
        for node in symmetry.missing[:MAX_ROWS]:
            _p("       %s" % node)

        _p("")
        _p("5. REST ASYMMETRY -- BOTH KINDS, ONE LIST")
        _p("   A pair that fails verification and a counterpart rejected")
        _p("   before it could become a pair are the SAME fault, and this")
        _p("   used to be two separate reports that were easy to read one")
        _p("   of. analyse() merges them, worst offender first.")
        _p("   pairs off by more than tolerance: %d" % len(symmetry.broken))
        for a, b, distance in symmetry.broken[:MAX_ROWS]:
            _p("       %-32s vs %-22s off by %.4f"
               % (a[-32:], b[-22:], distance))

        # --- verdict ---------------------------------------------------------
        _p("")
        _p("=" * 78)
        if pairs and symmetry.ok:
            _p("  VERDICT: mirror should work on this selection.")
            _p("  If it did nothing anyway, the write side is the problem, not")
            _p("  the pairing -- check for a locked or muted animation layer.")
        elif not symmetry.ok:
            _p("  VERDICT: the rig is not symmetric at REST as animkit derives it.")
            _p("  Put the rig at its rest pose and press Set Rest (or call")
            _p("  animkit.tools.pose.capture_rest_pose()), then try again.")
        elif not pairs:
            _p("  VERDICT: nothing paired. Either the naming convention is not in")
            _p("  pairing.SIDE_TOKENS and the geometric fallback found nothing, or")
            _p("  the rest positions are wrong -- compare sections 2 and 3 above.")
        _p("=" * 78)
        _p("")

    return {
        "root": root,
        "controls": controls,
        "pairs": pairs,
        "broken": symmetry.broken,
        "missing": symmetry.missing,
        "symmetric": symmetry.ok,
    }


def layers_report():
    """Anything about animation layers that would stop a write landing."""
    from maya import cmds

    from animkit.core import layers

    _p("")
    _p("ANIMATION LAYERS")
    found = layers.all_layers()
    if not found:
        _p("   none -- writes go to the base curves")
        return
    for layer in found:
        try:
            selected = cmds.animLayer(layer, q=True, selected=True)
            locked = cmds.animLayer(layer, q=True, lock=True)
            muted = cmds.animLayer(layer, q=True, mute=True)
        except Exception as exc:
            _p("   %-24s *** %s" % (layer, exc))
            continue
        notes = []
        if selected:
            notes.append("SELECTED")
        if locked:
            notes.append("LOCKED -- animkit will refuse to write here")
        if muted:
            notes.append("MUTED -- animkit will refuse to write here")
        _p("   %-24s %s" % (layer, ", ".join(notes) or "-"))
