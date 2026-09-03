"""Tween / blend engine.

The interaction contract, which is what makes this feel right or wrong:

  mouse-down  -> pick the target keys (those selected in the Graph Editor, or
                 the current time), insert keys if needed, snapshot every value
  drag        -> recompute from the SNAPSHOT each frame, never from the current
                 value (otherwise the drag compounds and the slider accelerates
                 away from the cursor)
  release     -> restore the snapshot silently, write the final value through
                 cmds, close the chunk

Why key creation goes through cmds and not the API
--------------------------------------------------
Value edits through MFnAnimCurve.setValue are safe and fast, and that is what
the drag loop uses. But TOPOLOGY edits through the API -- MFnAnimCurve.addKey
and .remove -- leave Maya's evaluation state inconsistent: the curve is
genuinely modified, yet a subsequent plain cmds.getAttr keeps serving the old
value, and a later cmds.setKeyframe on the same curve reads back stale too.

Symptom, if this is ever reintroduced: the tween visibly works -- the key on
the curve is correct, and further drags build on it correctly -- while getAttr
and anything downstream of DG evaluation report the pre-drag value. It looks
like the write failed. The write did not fail; the read did.

So keys are inserted with cmds.setKeyframe(insert=True), which also preserves
the surrounding curve shape properly -- something addKey with auto tangents
never did.

Undo
----
One chunk spans the whole drag, opened lazily before the first cmds write and
closed in commit() or cancel(). The chunk holds the key insertions and the
final cmds write, so a single Ctrl+Z reverts everything, including keys that
did not exist before. Selected-key mode inserts nothing, so cancelling that
drag leaves the undo stack completely untouched.

The API value writes in between are never recorded, which is exactly why
commit() rewinds them to the snapshot before writing the real value -- without
that rewind, undo would restore to whatever the API happened to leave behind
rather than to the pose the animator started from.

The blend maths lives in animkit.tools.blend, which imports nothing from Maya
so it can be tested in plain CPython -- see tests/test_blend.py.
"""

import logging

from maya import cmds

from animkit.core import curves, layers, targets as targets_mod
from animkit.tools import blend as blend_mod

log = logging.getLogger(__name__)


# --- blend modes ------------------------------------------------------------

# Re-exported so callers have one import. The maths itself lives in
# animkit.tools.blend, which imports nothing from Maya and is unit-tested
# in plain CPython.
MODE_BETWEEN = blend_mod.MODE_BETWEEN
MODE_LINEAR = blend_mod.MODE_LINEAR
MODE_AVERAGE = blend_mod.MODE_AVERAGE
MODE_EASE = blend_mod.MODE_EASE
MODES = blend_mod.MODES
MODE_LABELS = blend_mod.MODE_LABELS
MODE_TOOLTIPS = blend_mod.MODE_TOOLTIPS
blend = blend_mod.blend


# --- session ----------------------------------------------------------------


#: The Entry type now lives in animkit.core.targets, because every keyframe
#: operation needs it and not just this one. Aliased rather than re-exported
#: under a new name so existing callers and tests keep working.
_Entry = targets_mod.Entry


class TweenSession(object):
    """One drag, from mouse-down to release.

    Usage::

        s = TweenSession(mode=MODE_BETWEEN)
        if s.begin():
            s.update(0.35)      # repeatedly, during the drag
            s.commit(0.35)      # once, on release

    begin() picks its own target set:

      keys selected in the Graph Editor -> those exact keys, wherever they sit
      nothing selected                  -> the current time, inserting keys as
                                           needed on the animated channels of
                                           the selected controls

    begin() may open an undo chunk. Every path out of an active session must go
    through commit() or cancel() so it gets closed -- see the try/finally in
    animkit.ui.tween_ui.
    """

    TARGET_TIME = "time"
    TARGET_KEYS = "keys"

    def __init__(self, mode=MODE_BETWEEN):
        self.mode = mode
        self.time = None
        self.target = None
        self.entries = []
        self.warning = None
        self._active = False
        self._chunk_open = False

    @property
    def active(self):
        return self._active

    # --- undo chunk lifecycle ----------------------------------------------

    def _open_chunk(self):
        """Idempotent. Called lazily, right before the first cmds write.

        Selected-key mode inserts nothing, so it opens no chunk until commit --
        which means cancelling that drag leaves the undo stack untouched
        instead of stacking up no-op entries.
        """
        if not self._chunk_open:
            cmds.undoInfo(openChunk=True, chunkName="animkit: tween")
            self._chunk_open = True

    def _close_chunk(self):
        if self._chunk_open:
            # Flip the flag first: if closeChunk itself raises we must not be
            # left believing a chunk is still open.
            self._chunk_open = False
            try:
                cmds.undoInfo(closeChunk=True)
            except Exception:
                log.exception("animkit: failed to close undo chunk")

    def _layer_kwargs(self, layer):
        if layer and layer != layers.root_layer():
            return {"animLayer": layer}
        return {}

    # --- begin --------------------------------------------------------------

    def begin(self, plugs=None, use_selected_keys=True):
        """Snapshot state. Returns False if there is nothing to tween.

        The work of deciding WHICH keys is not done here any more -- it is
        animkit.core.targets.resolve_targets(), which every keyframe operation
        shares. What stays here is the part that is specific to tweening: a key
        with no neighbour on either side has nothing to blend toward, so it is
        dropped from the set.

        `before_write` is how the lazy undo chunk survives the move. It fires
        once, immediately before the first key insertion, so a drag that
        inserts nothing opens no chunk at all and cancelling it leaves the undo
        stack completely untouched.
        """
        self.entries = []
        self.time = curves.current_time()

        resolved = targets_mod.resolve_targets(
            plugs=plugs,
            use_selected_keys=use_selected_keys,
            insert=True,
            time=self.time,
            before_write=self._open_chunk,
        )

        self.target = resolved.mode
        self.warning = resolved.warning

        # A lone key, or a first/last key in selected-key mode, has nothing to
        # blend toward in either direction. Tweening it would be a no-op that
        # still costs an undo entry.
        self.entries = [
            e for e in resolved.entries
            if not (e.prev is None and e.next is None)
        ]

        self._active = bool(self.entries)
        if not self._active:
            self._close_chunk()
        return self._active

    # --- drag ---------------------------------------------------------------

    def update(self, t):
        """Preview the blend at `t`. Fast, non-undoable API value writes."""
        if not self._active:
            return
        for e in self.entries:
            value = blend(self.mode, e.snapshot, e.prev, e.next, t)
            e.curve.set_value_fast(e.index, value)
        self._dirty()

    def _dirty(self):
        """Propagate API value edits into the DG.

        LOAD-BEARING. Do not remove as redundant.

        A bare setValue happens to be visible to plain getAttr on Maya 2024,
        which makes this look unnecessary. It is not. Any TOPOLOGY change --
        including cmds.setKeyframe creating a key, and including an undo of one
        -- leaves DG reads serving the pre-change value until the plug is
        dirtied. Verified with a probe that uses no animkit code at all:
        setKeyframe wrote key=75.0 while getAttr still reported 50.0.

        Interactively this is invisible, because Maya evaluates on idle and the
        viewport looks correct. It bites scripts, batch jobs and tests, which
        read between refreshes. One batched call for the whole selection.
        """
        if not self.entries:
            return
        try:
            cmds.dgdirty([e.dirty_target for e in self.entries])
        except Exception:
            log.exception("animkit: dgdirty failed")

    def _rewind_values(self):
        """Undo the API value writes so cmds sees pre-drag values.

        Only pre-existing keys need this. Keys we inserted have their value
        written by commit() anyway, and are removed wholesale by cancel().
        """
        for e in reversed(self.entries):
            if e.created:
                continue
            try:
                e.curve.set_value_fast(e.index, e.snapshot)
            except Exception:
                log.exception("animkit: rewind failed for %s", e.curve_name)
        self._dirty()

    # --- end ----------------------------------------------------------------

    def cancel(self):
        """Abandon the drag and leave the scene as it was.

        Inserted keys are removed with cutKey inside the still-open chunk rather
        than by calling cmds.undo() after closing it. An undo() here would eat
        the animator's previous operation on any drag where we had nothing of
        our own to revert.
        """
        if not self._active:
            self._close_chunk()
            return
        try:
            self._rewind_values()
            for e in self.entries:
                if not e.created:
                    continue
                try:
                    cmds.cutKey(e.curve_name, index=(e.index, e.index), clear=True)
                except Exception:
                    log.exception(
                        "animkit: could not remove inserted key on %s", e.curve_name
                    )
        finally:
            self._close_chunk()
            self._active = False
            self.entries = []

    def commit(self, t):
        """Finish the drag: one undo entry containing the whole change."""
        if not self._active:
            self._close_chunk()
            return

        if abs(t) < 1e-6:
            self.cancel()
            return

        try:
            finals = [
                (e, blend(self.mode, e.snapshot, e.prev, e.next, t))
                for e in self.entries
            ]

            self._rewind_values()
            self._open_chunk()

            for e, value in finals:
                # Written by (curve, index) rather than (plug, time). The curve
                # was already layer-resolved in begin(), so there is no second
                # resolution here to disagree with the first -- and it is the
                # only form that works for a key the animator selected in the
                # Graph Editor, which may sit nowhere near the current time.
                try:
                    cmds.keyframe(
                        e.curve_name,
                        edit=True,
                        index=(e.index, e.index),
                        valueChange=e.curve.to_ui(value),
                        absolute=True,
                    )
                except Exception:
                    log.exception("animkit: commit failed for %s", e.curve_name)

            self._dirty()

            if self.warning:
                self.warning.emit()
        finally:
            self._close_chunk()
            self._active = False
            self.entries = []

    def force_close(self):
        """Last-resort cleanup for an abandoned session."""
        self._close_chunk()
        self._active = False
        self.entries = []


def tween_once(t, mode=MODE_BETWEEN):
    """Apply a tween at value `t` with no drag. For hotkeys and numeric entry."""
    session = TweenSession(mode=mode)
    if not session.begin():
        return False
    try:
        session.commit(t)
    except Exception:
        session.force_close()
        raise
    return True
