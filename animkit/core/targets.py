"""Answering "which keys am I acting on".

Lifted out of TweenSession.begin(), because every keyframe operation needs the
same answer and it is not a small answer. The rules it encodes:

  keys selected in the Graph Editor -> exactly those keys, wherever they sit
  nothing selected                  -> the current time, on the animated
                                       channels of the selected controls

and, in the current-time case, the whole layer policy: resolve each plug to
the curve on the layer that would be written to, skip locked and muted layers,
and collect a warning when more than one layer is involved.

Why an Entry is (curve, index) and not (plug, time)
---------------------------------------------------
Because that is what lets the two modes above share every line of code after
resolution. A key is a key wherever it sits on the timeline. It is also the
only identity that survives the two things that break the other one: a key the
animator selected in the Graph Editor may be nowhere near the current time,
and the curve has already been layer-resolved, so writing by curve cannot
disagree with the resolution that chose it.

Insertion, chunks and who owns them
-----------------------------------
resolve_targets() NEVER opens an undo chunk. When insert=True it does write to
the scene, so the caller must already be inside one -- pass `before_write` and
it will be called once, immediately before the first write, which is how a
caller opens its chunk lazily and keeps a read-only resolution free of undo
entries entirely.
"""

import logging

from maya import cmds

from animkit.core import cache, curves, layers, selection

log = logging.getLogger(__name__)

#: How the target set was chosen.
TARGET_TIME = "time"
TARGET_KEYS = "keys"


class Entry(object):
    """One key an operation is going to act on.

    plug and layer are None in selected-key mode: the animator picked keys on
    a specific curve, so no plug resolution was needed to find them and there
    is no layer question to answer.

    prev/next/snapshot are the state of the key at resolution time. Tweening
    needs all three; most keyframe operations ignore them, and that is fine --
    they cost one API read each and having them means an operation never has
    to go back to the scene for something it could have been handed.
    """

    __slots__ = (
        "curve_name", "curve", "index", "snapshot", "prev", "next",
        "created", "layer", "plug", "time",
    )

    def __init__(self, curve_name, curve, index, snapshot, prev, nxt,
                 created=False, layer=None, plug=None, time=None):
        self.curve_name = curve_name
        self.curve = curve
        self.index = index
        self.snapshot = snapshot
        self.prev = prev
        self.next = nxt
        self.created = created
        self.layer = layer
        self.plug = plug
        self.time = time

    @property
    def dirty_target(self):
        return self.plug or self.curve_name

    def __repr__(self):
        return "<Entry {0}[{1}] @{2}>".format(
            self.curve_name, self.index, self.time
        )


class Targets(object):
    """The resolved target set, plus how it was chosen and what to warn about."""

    def __init__(self, entries, mode, time, warning):
        self.entries = entries
        self.mode = mode
        self.time = time
        self.warning = warning

    def __bool__(self):
        return bool(self.entries)

    __nonzero__ = __bool__  # py2-era Maya versions

    def __len__(self):
        return len(self.entries)

    def __iter__(self):
        return iter(self.entries)

    @property
    def curve_names(self):
        """Distinct curves, in first-seen order.

        For the operations that act on a CURVE rather than on keys --
        setInfinity, for one -- where hitting the same curve once per selected
        key would be wrong as well as slow.
        """
        seen = []
        found = set()
        for entry in self.entries:
            if entry.curve_name not in found:
                found.add(entry.curve_name)
                seen.append(entry.curve_name)
        return seen

    def by_curve(self, descending=False):
        """{curve_name: [indices]}, sorted.

        Descending is not a cosmetic option. Deleting or re-timing keys by
        index invalidates every index after the one you touched, so anything
        that changes curve topology must walk each curve from the end.
        """
        grouped = {}
        for entry in self.entries:
            grouped.setdefault(entry.curve_name, []).append(entry.index)
        for indices in grouped.values():
            indices.sort(reverse=descending)
        return grouped

    def dirty(self):
        """Propagate edits into the DG.

        Same reasoning as TweenSession._dirty(): a topology change leaves DG
        reads serving the pre-change value until the plug is dirtied. One
        batched call for the whole set.

        Targets are filtered for existence first. Deleting the last key on a
        curve removes the curve node with it, so an operation that cleared a
        channel would otherwise hand dgdirty a dead node name, fail the whole
        batched call, and print a traceback for something that worked
        perfectly. TweenSession._dirty() skips this check deliberately -- it
        runs per mouse-move on plugs it just resolved, where an objExists per
        entry would cost more than the call it is protecting.
        """
        if not self.entries:
            return
        alive = []
        for entry in self.entries:
            target = entry.dirty_target
            try:
                if cmds.objExists(target.split(".", 1)[0]):
                    alive.append(target)
            except Exception:
                continue
        if not alive:
            return
        try:
            cmds.dgdirty(alive)
        except Exception:
            log.exception("animkit: dgdirty failed")


# --- resolution -------------------------------------------------------------


def resolve_targets(plugs=None, use_selected_keys=True, insert=False,
                    time=None, before_write=None, include_untweenable=False):
    """Return a Targets for whatever the animator is pointing at.

    plugs             explicit plug list, or None to use the current selection
    use_selected_keys honour a Graph Editor key selection (ignored if plugs
                      is given -- an explicit plug list is an explicit request)
    insert            create a key at `time` on channels that have a curve but
                      no key there. Tweening needs this; most keyframe
                      operations should not, because a key the animator did
                      not ask for is worse than an operation that did nothing.
    before_write      called once immediately before the first scene write.
                      Open your undo chunk here.
    include_untweenable
                      reach channels that must never be given an interpolated
                      value, such as visibility. False for anything that blends
                      values; True for operations that move or remove keys the
                      animator already set, which otherwise cannot clean or
                      retime a channel they can plainly see is keyed. See
                      animkit.core.selection.UNTWEENABLE.

    The whole resolution runs inside one cache.scope(), which is what keeps a
    300-control selection to a handful of layer queries instead of several per
    plug. The scope is closed before this returns, so nothing downstream can
    read a stale answer.
    """
    warning = layers.LayerWarning()
    when = curves.current_time() if time is None else float(time)

    with cache.scope():
        if use_selected_keys and plugs is None:
            selected = selection.selected_keys()
            if selected:
                entries = _from_selected_keys(selected)
                return Targets(entries, TARGET_KEYS, when, warning)

        entries = _from_time(plugs, when, insert, warning, before_write,
                             include_untweenable)
        return Targets(entries, TARGET_TIME, when, warning)


def _from_selected_keys(selected):
    """Keys the animator picked in the Graph Editor.

    No insertion and no layer resolution: the selected curve IS the target, by
    definition. Nothing here can write to a layer they were not looking at.
    """
    entries = []
    for curve_name, indices in selected.items():
        curve = curves.open_curve(curve_name)
        if curve is None:
            continue
        num = curve.num_keys
        for index in indices:
            if index < 0 or index >= num:
                continue
            prev_v, next_v = curve.neighbours_by_index(index)
            entries.append(
                Entry(
                    curve_name=curve_name,
                    curve=curve,
                    index=index,
                    snapshot=curve.key_value(index),
                    prev=prev_v,
                    nxt=next_v,
                    time=curve.key_time(index),
                )
            )
    return entries


def _from_time(plugs, when, insert, warning, before_write,
               include_untweenable=False):
    """Keys at `when` on the selected controls, honouring the layer policy."""
    if plugs is None:
        plugs = selection.plugs_from_selection(
            include_untweenable=include_untweenable
        )
    if not plugs:
        return []

    # Pass 1: survey what is available before touching the scene.
    candidates = []
    for plug in plugs:
        layer = layers.target_layer(plug)
        warning.check(plug, layer)

        if layer and not layers.is_writable(layer):
            continue

        curve_name = layers.resolve_curve(plug, layer)
        if not curve_name:
            # No animation on this plug on this layer. Never create one here --
            # an operation that keys a static channel is an operation the
            # animator has to undo.
            continue

        curve = curves.open_curve(curve_name)
        if curve is None or curve.num_keys == 0:
            continue

        has_key = curve.index_at(when) is not None
        if not has_key:
            if not insert:
                continue
            if curve.neighbours(when) == (None, None):
                continue

        candidates.append((plug, layer, curve_name, has_key))

    if not candidates:
        return []

    inserting = insert and any(not has_key for _p, _l, _c, has_key in candidates)
    if inserting and before_write is not None:
        before_write()

    # Pass 2: insert the keys we need, through cmds. insert=True holds the pose
    # and preserves the surrounding curve shape; the API's addKey does neither,
    # and leaves later cmds reads on the same curve stale. See tools/tween.py.
    if inserting:
        for plug, layer, _curve_name, has_key in candidates:
            if has_key:
                continue
            kwargs = {"insert": True, "time": (when, when)}
            if layer and layer != layers.root_layer():
                kwargs["animLayer"] = layer
            try:
                cmds.setKeyframe(plug, **kwargs)
            except Exception:
                log.exception("animkit: could not insert key on %s", plug)
        # The graph changed under the cache scope this runs inside.
        cache.invalidate()

    # Pass 3: re-open the curves and read indices. Inserting a key shifts every
    # index after the insertion point, so anything sampled in pass 1 is already
    # stale. Neighbours are read BY INDEX here, which stays correct across the
    # insertion -- a time-based lookup would find the newly inserted key as its
    # own neighbour.
    entries = []
    for plug, layer, curve_name, has_key in candidates:
        curve = curves.open_curve(curve_name)
        if curve is None:
            continue
        index = curve.index_at(when)
        if index is None:
            # Insertion failed (locked channel, refused layer). Skip rather
            # than writing to the wrong key.
            continue
        prev_v, next_v = curve.neighbours_by_index(index)
        entries.append(
            Entry(
                curve_name=curve_name,
                curve=curve,
                index=index,
                snapshot=curve.key_value(index),
                prev=prev_v,
                nxt=next_v,
                created=not has_key,
                layer=layer,
                plug=plug,
                time=when,
            )
        )
    return entries
