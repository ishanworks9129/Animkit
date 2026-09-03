"""Scene-level callbacks and cache invalidation.

Stale node names after a scene change are the most common crash source in
tools of this kind. Every module that caches anything keyed on a node name
registers an invalidator here.
"""

import logging

import maya.api.OpenMaya as om2

log = logging.getLogger(__name__)

_callback_ids = []
_invalidators = []
_after_open = []


def register_invalidator(fn):
    """Register a zero-arg callable to run whenever the scene changes."""
    if fn not in _invalidators:
        _invalidators.append(fn)
    return fn


def register_after_open(fn):
    """Register a zero-arg callable to run once a scene has finished loading.

    Separate from the invalidators on purpose. An invalidator runs on
    kBeforeOpen too, and anything that READS the scene must not run then --
    it would read a file that is halfway through being replaced. Use this for
    "load my data back out of the new scene", and register_invalidator for
    "throw away what I cached about the old one".
    """
    if fn not in _after_open:
        _after_open.append(fn)
    return fn


def invalidate_all(*args, **kwargs):
    for fn in _invalidators:
        try:
            fn()
        except Exception:
            log.exception("animkit: invalidator %r failed", fn)


def run_after_open(*args, **kwargs):
    for fn in _after_open:
        try:
            fn()
        except Exception:
            log.exception("animkit: after-open handler %r failed", fn)


def install_callbacks():
    """Idempotent. Safe to call repeatedly from userSetup.py or a reload."""
    remove_callbacks()

    events = (
        om2.MSceneMessage.kBeforeOpen,
        om2.MSceneMessage.kAfterOpen,
        om2.MSceneMessage.kBeforeNew,
        om2.MSceneMessage.kAfterNew,
        om2.MSceneMessage.kAfterImport,
        om2.MSceneMessage.kAfterCreateReference,
        om2.MSceneMessage.kAfterRemoveReference,
    )
    for event in events:
        _callback_ids.append(om2.MSceneMessage.addCallback(event, invalidate_all))

    # Events after which the scene is readable again. Anything that loads data
    # OUT of the scene hangs off these, never off the full list above.
    for event in (
        om2.MSceneMessage.kAfterOpen,
        om2.MSceneMessage.kAfterNew,
        om2.MSceneMessage.kAfterImport,
        om2.MSceneMessage.kAfterCreateReference,
        om2.MSceneMessage.kAfterRemoveReference,
    ):
        _callback_ids.append(
            om2.MSceneMessage.addCallback(event, run_after_open)
        )

    log.debug("animkit: installed %d scene callbacks", len(_callback_ids))


def remove_callbacks():
    while _callback_ids:
        cid = _callback_ids.pop()
        try:
            om2.MMessage.removeCallback(cid)
        except Exception:
            pass
