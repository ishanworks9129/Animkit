"""Keyframe operations: offset, retime, tangents, holds, snapping, cycles, delete.

Every operation in this module goes through `_apply`, and that is the whole
design. `_apply` gives each one, for free and identically:

  * exactly ONE undo chunk, opened lazily so an operation that finds nothing
    to do leaves no entry on the queue at all
  * the target set from animkit.core.targets -- selected Graph Editor keys if
    there are any, otherwise the current time on the selected controls
  * the layer policy: writes land on the active layer, locked and muted layers
    are skipped, and a multi-layer selection warns once
  * a dgdirty afterwards, because topology changes leave DG reads stale

Add a new operation by writing the two or three lines that touch the curve and
handing them to `_apply`. Do not open your own chunk, and do not resolve your
own targets -- an operation that does either is an operation that gets the
layer policy subtly wrong six months from now.

Everything writes by (curve, index)
-----------------------------------
Never by (plug, time). The curve was already layer-resolved when the target
set was built, so writing to it cannot disagree with that resolution -- and it
is the only form that works for a key the animator selected in the Graph
Editor, which may be nowhere near the current time.

Ordering, which is not optional
-------------------------------
Key indices are ordered by time. Anything that MOVES or DELETES keys therefore
invalidates the indices of the keys after it, and two keys moved carelessly
can land on top of each other and merge. `_move_keys` and `delete` both handle
this explicitly; if you add another topology-changing operation, it has to as
well.
"""

import logging

from maya import cmds

from animkit.core import targets as targets_mod
from animkit.core import undo
from animkit.tools import registry

log = logging.getLogger(__name__)


# --- tangent and cycle vocabularies -----------------------------------------

#: preset -> (in tangent, out tangent). Stepped is deliberately asymmetric:
#: Maya's own "Stepped" leaves the in tangent alone and makes the out tangent
#: hold, which is what produces a pose-to-pose block rather than a flat curve.
TANGENT_PRESETS = {
    "auto": ("auto", "auto"),
    "spline": ("spline", "spline"),
    "linear": ("linear", "linear"),
    "flat": ("flat", "flat"),
    "plateau": ("plateau", "plateau"),
    "stepped": (None, "step"),
}

#: Infinity mode -> the enum value on the animCurve's preInfinity/postInfinity.
#:
#: Set through setAttr on the RESOLVED CURVE rather than through
#: cmds.setInfinity on the plug. cmds.setInfinity applied to an animCurve node
#: is silently ignored -- it returns cleanly, changes nothing, and querying the
#: curve node returns None -- so an operation built on it reports success while
#: doing nothing at all. setAttr on the curve is undoable, unambiguous about
#: which layer it hit, and consistent with the rule that everything in this
#: codebase acts on a curve that layers.resolve_curve chose.
#:
#: MIND THE GAP: the enum is Constant=0, Linear=1, Cycle=3, Cycle with
#: offset=4, Oscillate=5. There is no 2. Passing 2 is silently rejected and
#: leaves the value at whatever it already was.
CYCLE_MODES = {
    "constant": 0,
    "linear": 1,
    "cycle": 3,
    "cycleRelative": 4,
    "oscillate": 5,
}


# --- the shared spine -------------------------------------------------------


def _apply(label, fn, plugs=None, use_selected_keys=True, insert=False,
           time=None):
    """Run `fn(targets)` as exactly one undoable operation.

    Returns the number of keys (or curves) affected, so a caller can tell
    "nothing was selected" from "it worked". Returns 0 without opening a chunk
    when there is nothing to act on.
    """
    with undo.LazyChunk("animkit: {0}".format(label)) as chunk:
        resolved = targets_mod.resolve_targets(
            plugs=plugs,
            use_selected_keys=use_selected_keys,
            insert=insert,
            time=time,
            before_write=chunk.open,
            # EVERY operation in this module acts on keys the animator already
            # set -- it moves them, retimes them, retangents them or removes
            # them. None of them invent a value, so none of them have any reason
            # to skip visibility, and skipping it is how Delete came to leave a
            # control still reading as keyed after clearing it. Tweening is the
            # path that must not touch visibility, and it resolves its own
            # targets. See animkit.core.selection.UNTWEENABLE.
            include_untweenable=True,
        )

        if not resolved.entries:
            return 0

        chunk.open()
        try:
            count = fn(resolved)
        except Exception:
            log.exception("animkit: %s failed", label)
            raise

        resolved.dirty()
        if resolved.warning:
            resolved.warning.emit()
        return count if count is not None else len(resolved.entries)


def _move_keys(curve_name, moves):
    """Move keys on one curve. `moves` is [(old_time, new_time)].

    Two hazards, both of which produce a result that looks right at a glance.

    1. LANDING ON AN OCCUPIED FRAME. Maya does not merge. Asked to move a key
       onto a frame that already has one, cmds.keyframe nudges it a sliver
       aside instead -- frame 1.0000001700680272, which reads as "1.0" in the
       Graph Editor and plays back wrong. (`option="insert"` and
       `"segmentOver"` refuse the move outright, which is no better.) So any
       non-target key sitting on a destination frame is removed first, and the
       move then genuinely merges.

    2. INDEX INVALIDATION. Those deletions renumber everything after them, so
       indices are re-resolved from time AFTER the deletions and never carried
       across them.

    Keys moving later are then applied from the latest destination backwards,
    keys moving earlier from the earliest forwards, so a key never lands on a
    target key that has not vacated yet. That ordering also preserves relative
    order, which is what keeps the re-resolved indices valid for the whole
    loop.
    """
    tolerance = 1e-6
    old_times = [old for old, _new in moves]

    def is_moving(t):
        return any(abs(t - old) <= tolerance for old in old_times)

    # 1. Clear non-target occupants of every destination frame.
    for _old, new in moves:
        try:
            occupants = cmds.keyframe(
                curve_name, q=True, time=(new, new), timeChange=True
            ) or []
        except Exception:
            continue
        for occupant in occupants:
            if is_moving(occupant):
                continue
            try:
                cmds.cutKey(curve_name, time=(occupant, occupant), clear=True)
            except Exception:
                log.exception(
                    "animkit: could not clear frame %s on %s", occupant, curve_name
                )

    # 2. Re-resolve indices now that the deletions have renumbered the curve.
    resolved = []
    for old, new in moves:
        try:
            found = cmds.keyframe(
                curve_name, q=True, time=(old, old), indexValue=True
            )
        except Exception:
            found = None
        if not found:
            continue
        resolved.append((found[0], old, new))

    # 3. Move, far end first.
    later = sorted([m for m in resolved if m[2] > m[1]], key=lambda m: -m[2])
    earlier = sorted([m for m in resolved if m[2] < m[1]], key=lambda m: m[2])

    moved = 0
    for index, _old, new in later + earlier:
        try:
            cmds.keyframe(
                curve_name, edit=True, index=(index, index),
                timeChange=new, absolute=True,
            )
            moved += 1
        except Exception:
            log.exception(
                "animkit: could not move key %d on %s", index, curve_name
            )
    return moved


# --- time operations --------------------------------------------------------


def offset(frames=1, **kwargs):
    """Shift the target keys along the timeline by `frames`.

    Offsetting a key past its neighbour merges them, which is Maya's own
    behaviour for a key landing on an occupied frame and not something worth
    inventing a different answer for.
    """
    frames = float(frames)
    if abs(frames) < 1e-9:
        return 0

    def run(resolved):
        moves = {}
        for entry in resolved.entries:
            moves.setdefault(entry.curve_name, []).append(
                (entry.time, entry.time + frames)
            )
        return sum(_move_keys(name, m) for name, m in moves.items())

    return _apply("offset keys", run, **kwargs)


def retime(factor=2.0, pivot=None, **kwargs):
    """Scale the target keys' spacing about `pivot` (default: current time).

    factor < 1 pulls them together (faster), > 1 spreads them apart (slower).
    """
    factor = float(factor)
    if abs(factor - 1.0) < 1e-9 or factor <= 0.0:
        return 0

    def run(resolved):
        centre = resolved.time if pivot is None else float(pivot)
        moves = {}
        for entry in resolved.entries:
            new_time = centre + (entry.time - centre) * factor
            if abs(new_time - entry.time) < 1e-9:
                continue
            moves.setdefault(entry.curve_name, []).append(
                (entry.time, new_time)
            )
        return sum(_move_keys(name, m) for name, m in moves.items())

    return _apply("retime keys", run, **kwargs)


def snap_to_frame(**kwargs):
    """Round the target keys onto whole frames.

    The cleanup after a retime, or after anything that dragged keys onto
    sub-frame times where the Graph Editor shows them looking correct and the
    playback does not.
    """
    def run(resolved):
        moves = {}
        for entry in resolved.entries:
            new_time = float(round(entry.time))
            if abs(new_time - entry.time) < 1e-9:
                continue
            moves.setdefault(entry.curve_name, []).append(
                (entry.time, new_time)
            )
        return sum(_move_keys(name, m) for name, m in moves.items())

    return _apply("snap keys to frame", run, **kwargs)


# --- shape operations -------------------------------------------------------


def set_tangents(preset="auto", **kwargs):
    """Apply a tangent preset to the target keys. See TANGENT_PRESETS."""
    if preset not in TANGENT_PRESETS:
        cmds.warning(
            "animkit: unknown tangent preset %r (have: %s)"
            % (preset, ", ".join(sorted(TANGENT_PRESETS)))
        )
        return 0

    in_tangent, out_tangent = TANGENT_PRESETS[preset]

    def run(resolved):
        done = 0
        for entry in resolved.entries:
            flags = {}
            if in_tangent:
                flags["inTangentType"] = in_tangent
            if out_tangent:
                flags["outTangentType"] = out_tangent
            # An asymmetric preset needs the tangent unlocked, or Maya keeps
            # in and out welded together and quietly ignores half the request.
            if in_tangent != out_tangent:
                flags["lock"] = False
            try:
                cmds.keyTangent(
                    entry.curve_name, edit=True,
                    index=(entry.index, entry.index), **flags
                )
                done += 1
            except Exception:
                log.exception(
                    "animkit: could not set tangents on %s[%d]",
                    entry.curve_name, entry.index,
                )
        return done

    return _apply("tangents: {0}".format(preset), run, **kwargs)


def hold(**kwargs):
    """Flatten each target key back onto the value of the key before it.

    The everyday way to build a moving hold: put the time cursor where the
    hold should end, run this, and the pose stops changing between the two
    keys. A key with nothing before it is left alone rather than guessed at.
    """
    def run(resolved):
        done = 0
        for entry in resolved.entries:
            if entry.prev is None:
                continue
            try:
                cmds.keyframe(
                    entry.curve_name, edit=True,
                    index=(entry.index, entry.index),
                    valueChange=entry.curve.to_ui(entry.prev),
                    absolute=True,
                )
                done += 1
            except Exception:
                log.exception(
                    "animkit: could not hold %s[%d]",
                    entry.curve_name, entry.index,
                )
        return done

    # insert=True: a hold is a thing you ask for at a frame that does not have
    # a key yet, far more often than not.
    kwargs.setdefault("insert", True)
    return _apply("hold", run, **kwargs)


def set_cycle(mode="cycle", **kwargs):
    """Set pre- and post-infinity on the target CURVES. See CYCLE_MODES.

    Per curve, not per key: infinity is a property of the curve, so a
    selection of six keys on one curve must set it once, not six times.
    """
    if mode not in CYCLE_MODES:
        cmds.warning(
            "animkit: unknown cycle mode %r (have: %s)"
            % (mode, ", ".join(sorted(CYCLE_MODES)))
        )
        return 0

    value = CYCLE_MODES[mode]

    def run(resolved):
        done = 0
        for curve_name in resolved.curve_names:
            try:
                cmds.setAttr(curve_name + ".preInfinity", value)
                cmds.setAttr(curve_name + ".postInfinity", value)
                done += 1
            except Exception:
                log.exception("animkit: could not set infinity on %s", curve_name)
        return done

    return _apply("cycle: {0}".format(mode), run, **kwargs)


# --- destructive ------------------------------------------------------------


def delete(**kwargs):
    """Remove the target keys.

    Deletes each curve from its highest index down. Removing a key renumbers
    every key after it, so ascending order would delete the wrong keys from
    the second one onward -- and it would look like it worked.
    """
    def run(resolved):
        removed = 0
        for curve_name, indices in resolved.by_curve(descending=True).items():
            for index in indices:
                try:
                    cmds.cutKey(
                        curve_name, index=(index, index), clear=True
                    )
                    removed += 1
                except Exception:
                    log.exception(
                        "animkit: could not delete %s[%d]", curve_name, index
                    )
        return removed

    return _apply("delete keys", run, **kwargs)


# --- registry ---------------------------------------------------------------


#: The shared Operation class. See animkit.tools.registry -- there used to be a
#: copy of it here and another in tools.pose, and they had already drifted.
Operation = registry.Operation


OPERATIONS = (
    Operation("animkitKeysOffsetBack", "-1", "Move the target keys one frame earlier",
              offset, {"frames": -1}, group="Timing"),
    Operation("animkitKeysOffsetForward", "+1", "Move the target keys one frame later",
              offset, {"frames": 1}, group="Timing"),
    Operation("animkitKeysRetimeFaster", "Faster",
              "Halve the spacing of the target keys about the current time",
              retime, {"factor": 0.5}, group="Timing"),
    Operation("animkitKeysRetimeSlower", "Slower",
              "Double the spacing of the target keys about the current time",
              retime, {"factor": 2.0}, group="Timing"),
    Operation("animkitKeysSnapToFrame", "Snap",
              "Round the target keys onto whole frames",
              snap_to_frame, group="Timing"),

    Operation("animkitKeysTangentAuto", "Auto", "Auto tangents",
              set_tangents, {"preset": "auto"}, group="Tangents"),
    Operation("animkitKeysTangentSpline", "Spline", "Spline tangents",
              set_tangents, {"preset": "spline"}, group="Tangents"),
    Operation("animkitKeysTangentLinear", "Linear", "Linear tangents",
              set_tangents, {"preset": "linear"}, group="Tangents"),
    Operation("animkitKeysTangentFlat", "Flat", "Flat tangents",
              set_tangents, {"preset": "flat"}, group="Tangents"),
    Operation("animkitKeysTangentStepped", "Step",
              "Stepped: the pose holds until the next key",
              set_tangents, {"preset": "stepped"}, group="Tangents"),
    Operation("animkitKeysHold", "Hold",
              "Flatten the target key back onto the previous key's value",
              hold, group="Tangents"),

    Operation("animkitKeysCycle", "Cycle",
              "Repeat the curve before and after its keys",
              set_cycle, {"mode": "cycle"}, group="Cycle"),
    Operation("animkitKeysCycleOffset", "Cycle+",
              "Repeat with offset, so the motion accumulates",
              set_cycle, {"mode": "cycleRelative"}, group="Cycle"),
    Operation("animkitKeysCycleClear", "Hold Ends",
              "Stop cycling: hold the first and last values",
              set_cycle, {"mode": "constant"}, group="Cycle"),

    Operation("animkitKeysDelete", "Delete", "Remove the target keys",
              delete, group="Edit", destructive=True),
)

#: name -> Operation, for lookups from the UI and the command layer.
BY_NAME = dict((op.name, op) for op in OPERATIONS)

#: Ordered group names, for laying the panel out.
GROUPS = registry.ordered_groups(OPERATIONS)
