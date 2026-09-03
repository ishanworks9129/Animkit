"""Undo chunking and refresh suspension.

Rule for this codebase: every user-visible operation is exactly one undo step,
and survives being undone mid-drag. Anything that writes to the scene goes
inside `undo_chunk`.
"""

import contextlib
import logging

from maya import cmds

log = logging.getLogger(__name__)

_refresh_depth = 0


@contextlib.contextmanager
def undo_chunk(name="animkit"):
    """Collapse everything inside into a single undo entry.

    The finally: is not optional. An exception with an open chunk leaves
    Maya's undo queue in a state where the next Ctrl+Z eats an unrelated
    operation.
    """
    cmds.undoInfo(openChunk=True, chunkName=name)
    try:
        yield
    finally:
        cmds.undoInfo(closeChunk=True)


class LazyChunk(object):
    """An undo chunk that is only opened if something is actually written.

        with LazyChunk("animkit: offset keys") as chunk:
            ...survey the scene, decide there is nothing to do, return...
            chunk.open()          # only reached when there IS something
            ...write...

    The difference matters more than it looks. An operation bound to a hotkey
    gets pressed when nothing is selected, or with the time cursor nowhere
    near a key, constantly. If that opens and closes a chunk regardless, the
    animator's undo queue fills with entries that undo nothing, and Ctrl+Z
    stops being a reliable way back to the last real change.

    open() is idempotent, so passing it as a `before_write` callback and then
    calling it again in the normal path is safe and expected.
    """

    def __init__(self, name="animkit"):
        self.name = name
        self._open = False

    def open(self):
        if not self._open:
            cmds.undoInfo(openChunk=True, chunkName=self.name)
            self._open = True

    @property
    def is_open(self):
        return self._open

    def close(self):
        if self._open:
            # Flip the flag first: if closeChunk raises we must not be left
            # believing a chunk is still open.
            self._open = False
            try:
                cmds.undoInfo(closeChunk=True)
            except Exception:
                log.exception("animkit: failed to close undo chunk %r", self.name)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


@contextlib.contextmanager
def suspend_refresh():
    """Suspend viewport refresh for the duration of the block.

    Maya does not reference-count refresh suspension, so we do it here --
    a nested block must not un-suspend early.

    Caveat worth knowing before you lean on this: with Cached Playback
    enabled, suspending refresh across scene edits can leave the cache
    showing stale frames until the next invalidation. For drag operations
    (where every frame is being rewritten anyway) that is fine. For anything
    that edits a frame range you are not currently sitting on, prefer letting
    Maya refresh normally and optimising the write path instead.
    """
    global _refresh_depth
    if _refresh_depth == 0:
        cmds.refresh(suspend=True)
    _refresh_depth += 1
    try:
        yield
    finally:
        _refresh_depth -= 1
        if _refresh_depth <= 0:
            _refresh_depth = 0
            cmds.refresh(suspend=False)
            cmds.refresh()


def force_resume_refresh():
    """Panic button. Bind this to a shelf button during development.

    If a tool ever dies with refresh suspended, the viewport freezes with no
    indication why. This gets it back without restarting Maya.
    """
    global _refresh_depth
    _refresh_depth = 0
    cmds.refresh(suspend=False)
    cmds.refresh()
