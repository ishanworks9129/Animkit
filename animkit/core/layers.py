"""Animation-layer-aware curve resolution.

THE RULE FOR THIS CODEBASE: no module anywhere calls listConnections() looking
for an animCurve, and no module calls cmds.getAttr() expecting to read animated
values it intends to write back. Everything goes through resolve_curve().

Why this exists
---------------
On a rig with animation layers, the animCurve driving an attribute is not
connected to that attribute. The graph looks like::

    animCurve (BaseAnimation) --> blend.inputA --+
                                                 +--> blend.output --> ctrl.tx
    animCurve (Layer1)        --> blend.inputB --+

...and layers stack, so blend.inputA is frequently the output of the blend
node one layer down. cmds.getAttr('ctrl.tx') returns the flattened result of
that whole stack, which is the one number you must never write back.

Tools that ignore this appear to work perfectly on unlayered rigs and then
silently corrupt animation the first time a supervisor adds a layer.

Policy
------
WRITE TO THE ACTIVE LAYER. Warn -- loudly, once per operation -- when more
than one layer affects the selection. Refusing outright just gets the tool
bypassed; silence gets it uninstalled.
"""

import logging

from maya import cmds

from animkit.core import cache, scene

log = logging.getLogger(__name__)

# blendNode name -> layer name. Rebuilt lazily on miss.
_blend_to_layer = {}
_map_valid = False


@scene.register_invalidator
def invalidate():
    global _map_valid
    _blend_to_layer.clear()
    _map_valid = False


# --- layer discovery --------------------------------------------------------


@cache.cached
def scene_has_layers():
    """True if the scene has any animation layer beyond the implicit base."""
    return bool(cmds.ls(type="animLayer"))


@cache.cached
def root_layer():
    try:
        return cmds.animLayer(q=True, root=True)
    except Exception:
        return None


@cache.cached
def all_layers():
    return cmds.ls(type="animLayer") or []


@cache.cached
def selected_layers():
    """Layers currently selected in the Anim Layer editor.

    Cached only inside a cache.scope(), and that restriction is the whole
    point: the animator can click a different layer at any moment and no
    scene callback fires when they do. A cache that outlived one operation
    would write to the layer they had selected last time.

    -selected must be queried PER LAYER, exactly like -lock and -mute below.
    The global form `cmds.animLayer(q=True, selected=True)` raises
    "No valid query flags were specified" -- and it raises only once the scene
    actually has layers, which is why it survived the first test run and broke
    every check on the second.
    """
    result = []
    for layer in all_layers():
        try:
            if cmds.animLayer(layer, q=True, selected=True):
                result.append(layer)
        except Exception:
            log.debug("animkit: -selected query failed on %s", layer, exc_info=True)
    return result


@cache.cached
def is_writable(layer):
    """A locked or muted layer must never be written to."""
    if not layer:
        return True
    try:
        if cmds.animLayer(layer, q=True, lock=True):
            return False
        if cmds.animLayer(layer, q=True, mute=True):
            return False
    except Exception:
        pass
    return True


@cache.cached
def target_layer(plug):
    """Which layer a key on plug should be written to, or None for no layers.

    Mirrors Maya's own setKeyframe behaviour: the selected layer wins; failing
    that, the layer already animating this attribute; failing that, the root.
    """
    if not scene_has_layers():
        return None

    selected = selected_layers()
    if len(selected) == 1:
        return selected[0]

    try:
        best = cmds.animLayer([plug], q=True, bestAnimLayer=True)
        if best:
            return best[0]
    except Exception:
        log.debug("animkit: bestAnimLayer query failed for %s", plug, exc_info=True)

    if selected:
        return selected[0]
    return root_layer()


def affecting_layers(plug):
    """Every layer that currently holds a curve for plug, top layer first.

    Built on the same blend-node walk as resolve_curve rather than on an
    animLayer query flag. Deliberate: this feeds the multi-layer warning, which
    is the only safety net in the layer policy. A query flag that turns out not
    to exist on some Maya version would fail into an empty list and silently
    disable that warning -- the exact failure mode the policy exists to
    prevent. A walk we own fails loudly instead.
    """
    if not scene_has_layers():
        return []
    found = []
    _walk_layers(plug, found, 0)
    return found


def _walk_layers(plug, found, depth):
    if depth > 64:
        log.warning("animkit: blend network too deep at %s, giving up", plug)
        return

    src = _source_plug(plug)
    if not src:
        return

    node, attr = src.split(".", 1)

    if _is_anim_curve(node):
        # Bottom of the stack: a raw curve belongs to the base layer.
        root = root_layer()
        if root and root not in found:
            found.append(root)
        return

    if not _is_blend_node(node):
        return

    suffix = _output_suffix(attr)
    layer = _layer_for_blend_node(node)
    if layer and layer not in found and _curve_from(node + ".inputB" + suffix):
        found.append(layer)

    _walk_layers(node + ".inputA" + suffix, found, depth + 1)


# --- graph walk -------------------------------------------------------------


def _rebuild_map():
    global _map_valid
    _blend_to_layer.clear()
    for layer in all_layers():
        try:
            nodes = cmds.animLayer(layer, q=True, blendNodes=True) or []
        except Exception:
            nodes = []
        for node in nodes:
            _blend_to_layer[node] = layer
    _map_valid = True


def _layer_for_blend_node(node):
    if not _map_valid:
        _rebuild_map()
    if node in _blend_to_layer:
        return _blend_to_layer[node]
    # New blend node created since the last rebuild.
    _rebuild_map()
    return _blend_to_layer.get(node)


@cache.cached
def _source_plug(plug):
    conns = cmds.listConnections(plug, s=True, d=False, p=True, scn=True)
    return conns[0] if conns else None


@cache.cached
def _is_anim_curve(node):
    return "animCurve" in (cmds.nodeType(node, inherited=True) or [])


@cache.cached
def _is_blend_node(node):
    # Covers every animBlendNodeAdditive{DA,DL,F,I16,...}, Boolean, Enum,
    # Rotation, Scale and Time variant in one check.
    return "animBlendNodeBase" in (cmds.nodeType(node, inherited=True) or [])


def _output_suffix(attr):
    """Map an output attr name to its axis suffix: output -> '', outputX -> X."""
    if attr.startswith("output"):
        return attr[len("output"):]
    return ""


def resolve_curve(plug, layer=None, _depth=0):
    """Return the animCurve node that layer uses to drive plug.

    layer=None means "the layer Maya would key to" (see target_layer).
    Returns None when no curve exists on that layer for that plug -- that is a
    normal result, not an error. Use ensure_curve() if you need one made.
    """
    if _depth > 64:
        log.warning("animkit: blend network too deep at %s, giving up", plug)
        return None

    src = _source_plug(plug)
    if not src:
        return None

    node = src.split(".", 1)[0]

    if _is_anim_curve(node):
        # Unlayered plug. Only the base/root layer can claim this curve.
        if layer in (None, root_layer()):
            return node
        return None

    if not _is_blend_node(node):
        # Driven by a constraint, expression, plain connection -- not ours.
        return None

    if layer is None:
        layer = target_layer(plug)

    attr = src.split(".", 1)[1]
    suffix = _output_suffix(attr)
    this_layer = _layer_for_blend_node(node)

    if this_layer == layer:
        return _curve_from(node + ".inputB" + suffix)

    # Not this layer -- descend to the stack below it.
    return resolve_curve(node + ".inputA" + suffix, layer, _depth + 1)


def _curve_from(plug):
    src = _source_plug(plug)
    if not src:
        return None
    node = src.split(".", 1)[0]
    return node if _is_anim_curve(node) else None


def ensure_curve(plug, layer=None, time=None):
    """Resolve a curve for plug, creating one on layer if absent.

    Building the blend-node network by hand is a mistake -- let Maya do it via
    setKeyframe, which gets the node types and the stacking order right.
    Caller is responsible for being inside an undo chunk.
    """
    if layer is None:
        layer = target_layer(plug)

    curve = resolve_curve(plug, layer)
    if curve:
        return curve

    kwargs = {"insert": False}
    if time is not None:
        kwargs["time"] = (time, time)
    if layer and layer != root_layer():
        kwargs["animLayer"] = layer

    try:
        cmds.setKeyframe(plug, **kwargs)
    except Exception:
        log.debug("animkit: could not key %s on layer %s", plug, layer, exc_info=True)
        return None

    # We just changed the graph. Anything a surrounding cache.scope() recorded
    # about this plug -- above all _source_plug, which is now pointing at a
    # blend node that did not exist a line ago -- is stale, and the very next
    # resolve_curve below would read it. See animkit.core.cache.
    cache.invalidate()

    return resolve_curve(plug, layer)


# --- policy enforcement -----------------------------------------------------


class LayerWarning(object):
    """Collects layer ambiguity so an operation warns once, not 300 times."""

    def __init__(self):
        self.multi = set()
        self.locked = set()

    def check(self, plug, layer):
        others = affecting_layers(plug)
        if len(others) > 1:
            self.multi.update(others)
        if layer and not is_writable(layer):
            self.locked.add(layer)

    def emit(self):
        if self.locked:
            cmds.warning(
                "animkit: skipped locked/muted layer(s): %s"
                % ", ".join(sorted(self.locked))
            )
        if self.multi:
            cmds.warning(
                "animkit: selection is driven by %d layers (%s). Wrote to the "
                "active layer only." % (len(self.multi), ", ".join(sorted(self.multi)))
            )
