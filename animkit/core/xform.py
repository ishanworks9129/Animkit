"""Matrix layer for pose work. No opinions about poses, mirroring or rigs.

Conventions, verified against Maya 2024 rather than assumed, because every one
of them is a plausible-looking way to be wrong:

    worldMatrix   = localMatrix * offsetParentMatrix * parentWorldMatrix
    (row vector -- matrices apply LEFT TO RIGHT, so `A * B` means A then B)

    local matrix  = [SP-1][S][SH][SP][ST][RP-1][RA][R][JO][RP][RT][T]

`MTransformationMatrix` round-trips a node's `.matrix` exactly, including
`rotateAxis` and a joint's `jointOrient` -- which is what makes it safe to
build a local matrix from channel values here rather than composing eleven
matrices by hand.

What "rest" means
-----------------
The rig at its default pose. Rig controls are conventionally zeroed at rest, so
a control's rest local matrix is its transformation with translate/rotate/scale
at their ATTRIBUTE DEFAULTS and every static part (pivots, rotateAxis,
jointOrient, offsetParentMatrix) left alone.

The line between "the rig was built this way" and "somebody posed this" is
drawn PER CHANNEL, by `animated_channels`: a channel carrying a curve is one
somebody is posing, so rest is its attribute default. Anything else -- a locked
offset group at (0, 150, 0), an FK joint whose translate is the bone length --
keeps its CURRENT value, because that value IS its rest value.

That distinction cannot be inferred perfectly. Nothing in a scene separates "rig
build offset" from "the animator moved it and did not key it" on a channel that
is posable and unkeyed. So the rest pose is DERIVED by the rule above and then
VERIFIED: animkit.core.pairing checks that the derived rest is actually
symmetric and refuses to mirror when it is not, rather than producing a result
that is plausibly wrong. `capture_rest()` is the escape hatch for a rig that
does not zero its controls.

Nothing here reads or writes animated values through getAttr/setAttr on the
assumption they round-trip. Matrices are read from worldMatrix/matrix plugs,
which are outputs, and channel VALUES are returned to the caller to write
through the curve layer -- see animkit.tools.pose.
"""

import logging
import math

import maya.api.OpenMaya as om2
from maya import cmds

from animkit.core import cache

log = logging.getLogger(__name__)

RAD_TO_DEG = 180.0 / math.pi
DEG_TO_RAD = math.pi / 180.0

#: The channels a pose is made of, in the order a human reads them.
TRANSLATE = ("translateX", "translateY", "translateZ")
ROTATE = ("rotateX", "rotateY", "rotateZ")
SCALE = ("scaleX", "scaleY", "scaleZ")
TRANSFORM_CHANNELS = TRANSLATE + ROTATE + SCALE

IDENTITY = om2.MMatrix()


# --- node access ------------------------------------------------------------


def dag_path(node):
    sel = om2.MSelectionList()
    sel.add(node)
    return sel.getDagPath(0)


def is_transform(node):
    try:
        return bool(cmds.ls(node, type="transform"))
    except Exception:
        return False


@cache.cached
def parent_of(node):
    """The transform parent of `node`, or None."""
    try:
        parents = cmds.listRelatives(node, parent=True, fullPath=False, type="transform")
    except Exception:
        return None
    return parents[0] if parents else None


def world_matrix(node):
    """Current world matrix. NOT cached -- it changes as a pose is applied."""
    return om2.MMatrix(cmds.getAttr(node + ".worldMatrix[0]"))


def local_matrix(node):
    """Current local matrix. NOT cached, same reason."""
    return om2.MMatrix(cmds.getAttr(node + ".matrix"))


@cache.cached
def offset_parent_matrix(node):
    """offsetParentMatrix, or identity on versions/nodes without it.

    Treated as static. A rig that ANIMATES offsetParentMatrix would need this
    re-read per evaluation; none seen in the wild, and the alternative is
    paying a getAttr per control per frame for a value that is almost always
    the identity.
    """
    try:
        if not cmds.attributeQuery("offsetParentMatrix", node=node, exists=True):
            return om2.MMatrix()
        return om2.MMatrix(cmds.getAttr(node + ".offsetParentMatrix"))
    except Exception:
        return om2.MMatrix()


@cache.cached
def settable_transform_channels(node):
    """The transform channels Maya will let anything write: keyable, unlocked, settable.

    A PROPERTY OF THE CHANNELS, NOT A VERDICT ABOUT THE NODE
    -------------------------------------------------------
    It was called `posable_channels`, and that name cost real work. It reads
    like "the channels this control poses with", so a non-empty result reads
    like "this is a control" -- and four separate call sites came to lean on
    exactly that reading. On a rig whose rigger did not bother locking the
    internals, the two differ by 1521 nodes to 80: AlignIKToWrist_L, twist
    chains, stabilisers and constraint targets all answer this question yes,
    because nobody locked them, and not one of them is a control. Three of
    those call sites produced confusing diagnostics. The fourth reset the rig's
    plumbing and destroyed a day's work.

    So the name now says what is actually measured -- Maya's own word for it,
    `getAttr(settable=True)` -- and says nothing about the node. To ask whether
    a node is a control, ask animkit.tools.pose.controls_in, which asks the rig
    (controller tags, control shapes, control sets) instead of guessing from
    lock state.

    It is also NOT how the rest pose is decided -- see animated_channels for
    that, and read the warning there before changing either.
    """
    from animkit.core import selection

    survey = set(selection._survey_node(node))
    return frozenset(c for c in TRANSFORM_CHANNELS if c in survey)


@cache.cached
def _node_has_animation(node):
    """Cheap per-node gate, so plumbing costs one query instead of nine.

    Asks for animCurve CONNECTIONS rather than for a keyframe count. The two
    look interchangeable and are not: `cmds.keyframe(node, keyframeCount=True)`
    returns 0 for a node whose only curve sits on a NON-KEYABLE plug, so a
    channel the rigger hid and something then animated was invisible to every
    caller of animated_channels -- which includes the rest pose, and therefore
    the mirror.

    Measured on Maya 2024: a transform with one key on a `setAttr -keyable
    false` rotateZ gives keyframeCount 0 and listConnections 1.

    Still one command per node, so the gate keeps the property it exists for.
    """
    try:
        return bool(cmds.listConnections(
            node, source=True, destination=False, type="animCurve"
        ))
    except Exception:
        return False


@cache.cached
def animated_channels(node):
    """The transform channels driven by ANIMATION. This decides the rest pose.

    WHY THIS IS NOT settable_transform_channels
    -------------------------------------------
    It used to be, and that was wrong in a way that only a production rig
    revealed. "Keyable, unlocked and settable" describes what a rigger left
    editable, not what an animator poses -- and riggers do not bother locking
    the rig's internals. On an AdvancedSkeleton character, 1521 of 1529
    transforms under the root came back "posable", so the rest pose zeroed
    almost every node in every parent chain and the entire rest hierarchy
    collapsed onto the world origin.

    The failure was silent and worse than an error. With every control's rest at
    (0, 0, 0), a control and its counterpart both reflect onto the origin, so
    they pair "successfully", check_symmetry finds nothing wrong, and the frame
    relation Q degenerates to a plain world-X reflection -- the naive
    channel-negation mirror this whole approach exists to avoid. The precondition
    check passed a rig it could not actually mirror.

    Animation is the honest signal. A channel with an animCurve is one somebody
    is posing, so its rest is the attribute default. Everything else -- static
    offset groups, constraint-driven plumbing, an FK joint's bone length -- keeps
    its current value, because that value IS its rest value.

    It also removes the special case the old rule needed: an FK joint animated on
    rotate but not translate now keeps its translate automatically, with no
    reliance on the rigger having locked it.

    The residual ambiguity is a control posed but NOT keyed: its rest is taken to
    be where it currently is, so it has no delta and will not mirror. That is
    reported rather than guessed -- see unkeyed_but_posed.
    """
    if not _node_has_animation(node):
        return frozenset()

    from animkit.core import layers

    found = set()
    for channel in TRANSFORM_CHANNELS:
        plug = "{0}.{1}".format(node, channel)
        try:
            if cmds.keyframe(plug, q=True, keyframeCount=True):
                found.add(channel)
                continue
        except Exception:
            pass
        try:
            if layers.resolve_curve(plug) is not None:
                found.add(channel)
        except Exception:
            pass
    return frozenset(found)


@cache.cached
def unkeyed_but_posed(node):
    """True if the node sits away from its defaults with no animation on it.

    The ambiguous case: nothing in the scene distinguishes "the rigger built it
    here" from "the animator moved it and did not key it". Rest is taken to be
    where it is, so it will not mirror -- and callers say so out loud instead of
    picking one interpretation silently.
    """
    # A captured rest pose is authoritative, so an off-default channel is
    # expected rather than suspicious -- that is the entire point of capturing
    # one. Without this, a rig that needed the escape hatch would be refused by
    # the very check the escape hatch exists to satisfy.
    if node in _rest_overrides:
        return False

    settable = settable_transform_channels(node)
    animated = animated_channels(node)
    defaults = channel_defaults(node)
    for channel in settable:
        if channel in animated:
            continue
        try:
            value = cmds.getAttr("{0}.{1}".format(node, channel))
        except Exception:
            continue
        if abs(value - defaults.get(channel, 0.0)) > 1e-4:
            return True
    return False


# --- composition ------------------------------------------------------------


@cache.cached
def channel_defaults(node):
    """{channel: default value} in UI units, for the transform channels.

    Cached because it is nine attributeQuery calls and several callers want it
    for the same node. Uncached and called per node across a rig it was 13,365
    of the 19,580 cmds calls in a scene-wide reset.
    """
    out = {}
    for channel in TRANSFORM_CHANNELS:
        try:
            values = cmds.attributeQuery(channel, node=node, listDefault=True)
            out[channel] = float(values[0]) if values else 0.0
        except Exception:
            out[channel] = 1.0 if channel in SCALE else 0.0
    return out


def compose_local(node, translate=None, rotate=None, scale=None):
    """Local matrix for `node` with these channel values, static parts kept.

    Values are in UI units (degrees for rotation). None means "leave the
    node's current value for that triple".

    This is the safe direction: MTransformationMatrix already holds the node's
    pivots, rotateAxis, shear and jointOrient, so overriding only t/r/s cannot
    lose any of them. Verified round-tripping .matrix on a plain transform, a
    transform with rotateAxis, and a joint with jointOrient.
    """
    fn = om2.MFnTransform(dag_path(node))
    tm = om2.MTransformationMatrix(fn.transformation())

    if translate is not None:
        tm.setTranslation(om2.MVector(*translate), om2.MSpace.kTransform)
    if rotate is not None:
        order = cmds.getAttr(node + ".rotateOrder")
        tm.setRotation(
            om2.MEulerRotation([v * DEG_TO_RAD for v in rotate], order)
        )
    if scale is not None:
        tm.setScale(list(scale), om2.MSpace.kTransform)

    return tm.asMatrix()


#: node -> world matrix, for rigs that do not zero their controls at rest.
#: See set_rest_override.
_rest_overrides = {}


def set_rest_override(node, matrix):
    """Declare a node's rest world matrix explicitly.

    The escape hatch for a rig whose controls do NOT zero to their attribute
    defaults at rest. Capture it with capture_rest() while the rig is at rest,
    and every frame relation derived afterwards uses it.

    This is not a config file and not a naming convention -- it is one snapshot
    of the scene, taken by the person who can see whether the rig is at rest.
    """
    _rest_overrides[node] = om2.MMatrix(matrix)


def capture_rest(nodes):
    """Snapshot the CURRENT world matrices as the rest pose. Returns the count.

    Only correct if the rig is genuinely at rest right now, which is why it is
    a deliberate action rather than something inferred.
    """
    for node in nodes:
        try:
            set_rest_override(node, world_matrix(node))
        except Exception:
            log.debug("animkit: could not capture rest for %s", node, exc_info=True)
    cache.invalidate()
    return len(_rest_overrides)


def clear_rest_overrides():
    _rest_overrides.clear()
    cache.invalidate()


@cache.cached
def rest_local_matrix(node):
    """Local matrix with the ANIMATED channels at their defaults.

    Everything else keeps its current value. See animated_channels -- the choice
    of signal there is the difference between a working mirror and a rest
    hierarchy collapsed onto the origin.
    """
    animated = animated_channels(node)
    if not animated:
        return local_matrix(node)

    defaults = channel_defaults(node)
    current = {}
    for triple in (TRANSLATE, ROTATE, SCALE):
        values = cmds.getAttr(node + "." + triple[0][:-1])[0]
        for channel, value in zip(triple, values):
            current[channel] = value

    def resolve(triple):
        return [
            defaults[c] if c in animated else current[c]
            for c in triple
        ]

    return compose_local(
        node,
        translate=resolve(TRANSLATE),
        rotate=resolve(ROTATE),
        scale=resolve(SCALE),
    )


@cache.cached
def rest_world_matrix(node, _depth=0):
    """World matrix the node would have with the whole rig at its default pose.

    Walks to the top of the DAG composing rest local matrices, so an ancestor
    that is itself a posed control does not contaminate the answer with its
    current pose. That is the entire reason this is not just
    `local_rest * parentWorldMatrix`.
    """
    if _depth > 256:
        log.warning("animkit: DAG deeper than 256 at %s, giving up", node)
        return om2.MMatrix()

    if node in _rest_overrides:
        return om2.MMatrix(_rest_overrides[node])

    result = rest_local_matrix(node) * offset_parent_matrix(node)
    parent = parent_of(node)
    if parent:
        result = result * rest_world_matrix(parent, _depth + 1)
    return result


def rest_channels(node):
    """{channel: value} in UI units at a CAPTURED rest pose, or None.

    Only answers for a node whose rest was captured -- see set_rest_override.
    A DERIVED rest deliberately cannot answer this, and that is the point: for
    an unkeyed channel the derived rest is its CURRENT value, so the answer
    would always be "it is already at rest" for a control the animator can see
    is not.

    Returning None is therefore meaningful. It says "nothing in this scene
    knows where rest is for this node", which is exactly the condition
    reset_to_default has to refuse on rather than guess at.
    """
    if node not in _rest_overrides:
        return None

    try:
        rest_world = om2.MMatrix(_rest_overrides[node])
        parent = parent_of(node)
        parent_rest = rest_world_matrix(parent) if parent else IDENTITY
        local = rest_world * (
            offset_parent_matrix(node) * parent_rest
        ).inverse()
        return channels_for_local(node, local)
    except Exception:
        log.debug("animkit: could not solve rest channels for %s", node,
                  exc_info=True)
        return None


# --- reflection -------------------------------------------------------------


def reflection_matrix(point=(0.0, 0.0, 0.0), normal=(1.0, 0.0, 0.0)):
    """Reflection across the plane through `point` with the given normal.

    Determinant is -1 by construction: this is a mirror, not a rotation. Do not
    try to "fix" that here -- the sign cancels in the conjugation that uses it,
    which is precisely why that formulation needs no handedness correction.
    """
    n = om2.MVector(*normal)
    if n.length() < 1e-9:
        n = om2.MVector(1.0, 0.0, 0.0)
    n.normalize()

    linear = om2.MMatrix()
    for i in range(3):
        for j in range(3):
            linear.setElement(i, j, (1.0 if i == j else 0.0) - 2.0 * n[i] * n[j])

    to_origin = om2.MMatrix()
    back = om2.MMatrix()
    for i in range(3):
        to_origin.setElement(3, i, -point[i])
        back.setElement(3, i, point[i])

    return to_origin * linear * back


def reflect_point(point, reflection):
    p = om2.MPoint(point[0], point[1], point[2]) * reflection
    return [p.x, p.y, p.z]


# --- the mirror maths -------------------------------------------------------


def frame_relation(source, target, reflection):
    """Q: maps `source` local rest space -> reflected world -> `target` local rest.

        Q = W_rest(source) * reflection * W_rest(target)^-1

    On a symmetric rig this comes out a signed permutation with determinant -1
    -- which is exactly the per-control "mirror axis table" that tools of this
    kind normally hardcode. Derived from the rig instead of declared, so a
    right side built with flipped joint orients, a different rotation order or
    a non-zero rotateAxis is handled without anyone writing it down.
    """
    return (
        rest_world_matrix(source)
        * reflection
        * rest_world_matrix(target).inverse()
    )


def pose_delta(node, local=None):
    """The node's motion away from its own rest, in PARENT space.

        d = L_posed * L_rest^-1

    LOCAL, not world, and that is not a detail. A world-space delta
    (W_posed * W_rest^-1) absorbs every ancestor transform, so conjugating it
    mirrors the ancestors too. Measured: with the character root at x=100 and
    only the arms selected, the world formulation puts the mirrored arm at
    x=-110 instead of x=+90 -- it flips the character's placement along with the
    pose, and the arm leaves the body behind.

    Rigs sit at the origin in a test scene and never in a shot, which is exactly
    why this has to be pinned by a test that moves the root.

    Working locally also means a hierarchy needs no ordering: a local matrix
    does not depend on where its parent ended up, so parents and children can be
    solved in any order.
    """
    current = local_matrix(node) if local is None else local
    return current * rest_local_matrix(node).inverse()


def mirror_delta(delta, relation):
    """Re-express a motion in the mirrored frame.

        D_target = Q^-1 * D_source * Q

    A similarity transform, so det(D_target) == det(D_source): conjugating by a
    matrix that contains a reflection gives back a PROPER transform. The two
    reflections cancel. That is why this formulation needs no handedness fix,
    unlike reflecting a world matrix directly -- which produces a left-handed
    frame that no rigid transform can equal.
    """
    return relation.inverse() * delta * relation


def local_from_delta(node, delta):
    """Turn a parent-space motion back into a local matrix for `node`."""
    return delta * rest_local_matrix(node)


def mirror_plane(root):
    """(point, normal) of a rig's symmetry plane, from its root at REST.

    The plane is the root's own YZ, taken from the REST world matrix rather than
    the current one. Both halves of that matter:

      * its own, not the world's -- a rig built at x=500, or rotated, is still
        symmetric about itself
      * at rest, because the frame relations are derived at rest, and a plane
        measured from a moved root would not match them

    The character being moved afterwards is handled by the delta being local.
    """
    matrix = rest_world_matrix(root)
    point = (matrix[12], matrix[13], matrix[14])
    normal = (matrix[0], matrix[1], matrix[2])
    return point, normal


# --- decomposition ----------------------------------------------------------


def _rotation_offsets(node):
    """(rotateAxis, jointOrient) as 4x4 matrices, identity when absent.

    Both are static and both sit either side of the rotate channels in the
    local matrix product: [RA][R][JO]. They have to come out before the rotate
    channel can be read back.
    """
    def euler_matrix(values):
        return om2.MEulerRotation(
            [v * DEG_TO_RAD for v in values], om2.MEulerRotation.kXYZ
        ).asMatrix()

    rotate_axis = om2.MMatrix()
    joint_orient = om2.MMatrix()
    try:
        if cmds.attributeQuery("rotateAxis", node=node, exists=True):
            rotate_axis = euler_matrix(cmds.getAttr(node + ".rotateAxis")[0])
    except Exception:
        pass
    try:
        if cmds.attributeQuery("jointOrient", node=node, exists=True):
            joint_orient = euler_matrix(cmds.getAttr(node + ".jointOrient")[0])
    except Exception:
        pass
    return rotate_axis, joint_orient


@cache.cached
def has_awkward_transform(node):
    """True if the node has features the analytic decomposition cannot honour.

    Non-zero rotate/scale pivots or pivot translations, or shear. Those add
    terms to the local matrix product that cannot be recovered from a bare
    matrix, so such a node is routed through the proxy decomposition instead of
    being silently mis-decomposed.
    """
    for attr in ("rotatePivot", "scalePivot",
                 "rotatePivotTranslate", "scalePivotTranslate", "shear"):
        try:
            if not cmds.attributeQuery(attr, node=node, exists=True):
                continue
            values = cmds.getAttr(node + "." + attr)[0]
        except Exception:
            continue
        if any(abs(v) > 1e-9 for v in values):
            return True
    return False


def nearest_euler(euler, reference):
    """Pick the representation of `euler` closest to `reference` (radians).

    An orientation has infinitely many Euler representations, and Maya's
    decomposition returns whichever one it likes -- typically the one in
    -180..180. That is numerically correct and animation-hostile: writing -180
    where the animator had +180 is the same pose but interpolates the opposite
    way round, so a mirror can produce a limb that spins between two keys while
    both keys look right.

    Tries the alternate solution and whole turns on each axis, and keeps the
    candidate nearest the value already on the channel.
    """
    candidates = [om2.MEulerRotation(euler), euler.alternateSolution()]

    best = None
    best_cost = None
    two_pi = 2.0 * math.pi
    for candidate in candidates:
        snapped = om2.MEulerRotation(candidate)
        for axis in range(3):
            delta = reference[axis] - snapped[axis]
            snapped[axis] += round(delta / two_pi) * two_pi
        cost = sum(abs(snapped[a] - reference[a]) for a in range(3))
        if best_cost is None or cost < best_cost:
            best, best_cost = snapped, cost
    return best


def channels_from_local(node, matrix, reference=None):
    """Decompose a LOCAL matrix into channel values, in UI units.

    Returns {channel: value} covering translate/rotate/scale, or None if the
    node has transform features this cannot handle -- see
    has_awkward_transform. Callers fall back to channels_via_proxy.

    `reference` is the rotation to stay near, in DEGREES, defaulting to the
    node's current rotate channels. See nearest_euler for why that matters.
    """
    if has_awkward_transform(node):
        return None

    tm = om2.MTransformationMatrix(matrix)
    translate = tm.translation(om2.MSpace.kTransform)
    scale = tm.scale(om2.MSpace.kTransform)

    # [RA][R][JO] -> R = RA^-1 * total * JO^-1
    rotate_axis, joint_orient = _rotation_offsets(node)
    total = tm.rotation(asQuaternion=False).asMatrix()
    rotation = rotate_axis.inverse() * total * joint_orient.inverse()

    order = cmds.getAttr(node + ".rotateOrder")
    euler = om2.MTransformationMatrix(rotation).rotation(asQuaternion=False)
    euler.reorderIt(order)

    if reference is None:
        reference = cmds.getAttr(node + ".rotate")[0]
    euler = nearest_euler(euler, [v * DEG_TO_RAD for v in reference])

    return {
        "translateX": translate.x, "translateY": translate.y,
        "translateZ": translate.z,
        "rotateX": euler.x * RAD_TO_DEG, "rotateY": euler.y * RAD_TO_DEG,
        "rotateZ": euler.z * RAD_TO_DEG,
        "scaleX": scale[0], "scaleY": scale[1], "scaleZ": scale[2],
    }


def channels_via_proxy_local(node, local):
    """channels_via_proxy for a LOCAL matrix. See that function.

    cmds.xform without -worldSpace sets the local matrix, so Maya performs the
    same exact decomposition against the node's own pivots and orients.
    """
    return _via_proxy(node, local, world_space=False)


def channels_via_proxy(node, world):
    """Decompose by letting Maya do it, on a throwaway duplicate of `node`.

    The oracle, and the fallback for nodes with pivots or shear. `duplicate
    -parentOnly` gives a node with the same parent, rotation order, rotateAxis,
    jointOrient, pivots and offsetParentMatrix, and no animation on it -- so
    cmds.xform can set a world matrix and Maya performs the exact
    decomposition its own tools would.

    Slower than the analytic path by roughly the cost of a duplicate, which is
    why it is not the default. Correct by construction, which is why the tests
    assert the analytic path agrees with it.
    """
    return _via_proxy(node, world, world_space=True)


def _via_proxy(node, matrix, world_space):
    proxy = None
    try:
        proxy = cmds.duplicate(node, parentOnly=True,
                               name="animkitProxy_solver")[0]
        # Unlock, or xform silently refuses on a rig that locks channels.
        for channel in TRANSFORM_CHANNELS:
            try:
                cmds.setAttr(proxy + "." + channel, lock=False)
            except Exception:
                pass
        if world_space:
            cmds.xform(proxy, worldSpace=True, matrix=list(matrix))
        else:
            cmds.xform(proxy, matrix=list(matrix))
        return dict(
            (channel, cmds.getAttr(proxy + "." + channel))
            for channel in TRANSFORM_CHANNELS
        )
    except Exception:
        log.exception("animkit: proxy decomposition failed for %s", node)
        return None
    finally:
        if proxy and cmds.objExists(proxy):
            try:
                cmds.delete(proxy)
            except Exception:
                pass


def channels_for_local(node, local, reference=None):
    """Channel values, UI units, that give `node` this LOCAL matrix.

    The path the mirror uses. Analytic where possible, Maya's own decomposition
    for nodes with pivots or shear.
    """
    values = channels_from_local(node, local, reference=reference)
    if values is None:
        values = channels_via_proxy_local(node, local)
    return values


def channels_from_world(node, world, reference=None):
    """Channel values, in UI units, that would put `node` at `world`.

    Analytic where possible, Maya's own decomposition where not.
    """
    parent = parent_of(node)
    parent_world = rest_world_matrix(parent) if parent else om2.MMatrix()
    if parent:
        # The CURRENT parent world matrix, not the rest one: by the time a
        # child is solved its parent has already been posed, and asking for a
        # local matrix relative to the wrong parent frame is how a hierarchy
        # mirror lands the parent correctly and every child wrongly.
        parent_world = world_matrix(parent)

    local = world * (offset_parent_matrix(node) * parent_world).inverse()

    values = channels_from_local(node, local, reference=reference)
    if values is None:
        values = channels_via_proxy(node, world)
    return values


def depth(node):
    """How deep in the DAG, so a hierarchy can be posed parents-first.

    Not cosmetic: a child's local matrix is computed against its parent's
    CURRENT world matrix, so the parent has to have been written already.
    """
    try:
        full = cmds.ls(node, long=True)
        return full[0].count("|") if full else 0
    except Exception:
        return 0
