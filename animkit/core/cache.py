"""Memoization with a lifetime you can actually reason about.

Why this is scoped rather than a plain long-lived cache
-------------------------------------------------------
The obvious design is a dict keyed on node name, cleared by a scene callback.
It is also wrong here, and wrong in a way that produces the exact bug the
layer policy exists to prevent.

`layers.selected_layers()` changes when the animator clicks a different row in
the Anim Layer editor. Attribute lock state changes when they lock a channel.
A layer gets muted. NONE of those fire an MSceneMessage. A cache invalidated
only on scene change would happily serve the previous answer, and the tool
would write to the layer they were looking at a minute ago. Silently.

So the rule here is: **a cache exists only for the duration of one operation.**

    with cache.scope():
        plugs = selection.plugs_from_selection()
        ...                     # 2700 repeated queries collapse to a handful

Outside a scope every wrapped function calls straight through, uncached and
correct. That makes the worst case "slow", never "wrong" -- and it means
forgetting to open a scope costs performance, not correctness, which is the
right way round for a mistake to fail.

The scene callback wired up at the bottom is a backstop for the case where a
scope is somehow left open across a file open, not the primary mechanism.

Mutating inside a scope
-----------------------
A scope assumes the scene is not changing underneath it. Anything that edits
the scene mid-scope -- inserting a key, creating a curve -- must call
`invalidate()` afterwards, or later reads in the same scope will serve the
pre-edit answer. `layers.ensure_curve` does this. If you add another mutator,
it does too.
"""

import contextlib
import functools
import logging

from animkit.core import scene

log = logging.getLogger(__name__)

_depth = 0
_stores = {}

# Diagnostics, for the bench and the self-test. Never used for behaviour.
_hits = 0
_misses = 0
_calls_uncached = 0


@contextlib.contextmanager
def scope():
    """Memoize wrapped queries for the duration of the block.

    Re-entrant: nested scopes share one store and only the outermost clears
    it, so a keyframe op can open a scope without caring whether its caller
    already did.
    """
    global _depth
    _depth += 1
    try:
        yield
    finally:
        _depth -= 1
        if _depth <= 0:
            _depth = 0
            _stores.clear()


def active():
    return _depth > 0


def invalidate():
    """Drop everything cached so far, without closing the scope.

    Call after any scene edit made inside a scope.
    """
    _stores.clear()


def cached(fn):
    """Memoize fn on its positional arguments, but only inside a scope.

    Arguments must be hashable; anything else falls through uncached rather
    than raising, so a caller passing a list gets a correct slow answer
    instead of a TypeError from the cache layer.
    """
    key = "{0}.{1}".format(fn.__module__, fn.__name__)

    @functools.wraps(fn)
    def wrapper(*args):
        global _hits, _misses, _calls_uncached
        if _depth == 0:
            _calls_uncached += 1
            return fn(*args)
        try:
            hash(args)
        except TypeError:
            _calls_uncached += 1
            return fn(*args)
        store = _stores.setdefault(key, {})
        # `in` rather than .get(): these functions return None as a real
        # answer -- "no curve on this layer" -- and a sentinel-free .get()
        # would re-query every single time for exactly the plugs that are
        # cheapest to get wrong.
        if args in store:
            _hits += 1
            return store[args]
        _misses += 1
        value = fn(*args)
        store[args] = value
        return value

    wrapper.uncached = fn
    wrapper.cache_key = key
    return wrapper


# --- diagnostics ------------------------------------------------------------


def reset_stats():
    global _hits, _misses, _calls_uncached
    _hits = _misses = _calls_uncached = 0


def stats():
    return {
        "hits": _hits,
        "misses": _misses,
        "uncached": _calls_uncached,
        "depth": _depth,
        "stores": {k: len(v) for k, v in _stores.items()},
    }


@scene.register_invalidator
def _on_scene_change():
    """Backstop only. See the module docstring for why scopes do the real work."""
    global _depth
    _depth = 0
    _stores.clear()
