"""Pose copy, paste, mirror and flip.

A Pose is a dict of {plug: value} in UI units plus the matrices needed to
mirror it. Capturing reads; applying writes through the same curve layer every
other operation uses, so a paste lands on the active animation layer, skips
locked ones, and is one undo step.

Mirroring is a TRANSFORM BETWEEN capture and apply, not a separate code path.
That is the whole reason copy/paste was built first: `paste` and `paste_mirrored`
differ by one function call, so there is no second implementation to keep in
step.

Why the mirror needs no per-rig configuration
---------------------------------------------
Because the rig's own rest pose is the configuration. For a control and its
counterpart, the constant

    Q = W_rest(source) * reflection * W_rest(target)^-1

is exactly the per-control "mirror axis table" that tools of this kind normally
hardcode -- derived from the rig instead of declared. Flipped joint orients, a
different rotation order per side, a non-zero rotateAxis and a mirror plane away
from the world origin all come out in the wash, because nothing ever assumed the
two sides' frames agreed.

The pose delta is LOCAL (parent space), not world. A world delta absorbs
ancestor motion, and conjugating it mirrors the character's placement too: with
the root at x=100 and only the arms selected, the mirrored arm lands at x=-110
instead of x=+90. See animkit.core.xform.pose_delta.

Custom attributes
-----------------
`ikFkBlend`, `fingerCurl`, `stretch` -- no matrix can say whether these should
negate, because they have no spatial meaning. They are copied across unchanged,
which is right the overwhelming majority of the time, and MIRROR_NEGATE_ATTRS is
the override for the rare signed one. That is a small list for exceptions, not a
config file the tool needs in order to work.
"""

import logging

from maya import cmds

from animkit.core import cache, curves, layers, pairing, targets as targets_mod
from animkit.core import undo, xform
from animkit.tools import registry

log = logging.getLogger(__name__)

#: Custom (non-transform) attribute names that should be negated when mirrored.
#: Deliberately empty. Add a name here only for a rig whose attribute genuinely
#: reverses sign across the body; copying unchanged is the correct default.
MIRROR_NEGATE_ATTRS = set()

#: The last copied pose, for the copy/paste pair of commands.
_clipboard = None


class Pose(object):
    """A captured pose: channel values, plus what is needed to mirror them."""

    def __init__(self, values=None, root=None):
        #: {plug: value in UI units}
        self.values = values or {}
        self.root = root

    def __len__(self):
        return len(self.values)

    def __bool__(self):
        return bool(self.values)

    __nonzero__ = __bool__

    @property
    def nodes(self):
        seen = []
        for plug in self.values:
            node = plug.split(".", 1)[0]
            if node not in seen:
                seen.append(node)
        return seen

    def channels_for(self, node):
        prefix = node + "."
        return dict(
            (plug[len(prefix):], value)
            for plug, value in self.values.items()
            if plug.startswith(prefix)
        )


# --- capture ----------------------------------------------------------------


def capture(nodes=None):
    """Read the current pose of `nodes` (default: the selection).

    Reads through getAttr, which is correct HERE and nowhere else in this
    codebase: a pose is the flattened result the animator is looking at, which
    is exactly what getAttr returns. The rule about never writing a getAttr
    value back still holds -- apply() writes through the curve layer, and the
    mirror recomputes channel values from matrices rather than echoing them.
    """
    from animkit.core import selection

    nodes = nodes if nodes is not None else selection.selected_nodes()
    if not nodes:
        return Pose()

    # DIRTY BEFORE READING. Not optional, and not defensive coding.
    #
    # A pose is captured through getAttr, and getAttr serves a stale value after
    # any topology change until the plug is dirtied -- which includes a key
    # having just been written. So the obvious sequence "pose it, key it, copy
    # it" captured the values from BEFORE the key: paste then wrote zeros and
    # looked like a broken clipboard. The same trap the README warns about for
    # tests, met on the read path of real code.
    try:
        cmds.dgdirty([n for n in nodes if cmds.objExists(n)])
    except Exception:
        log.debug("animkit: dgdirty before capture failed", exc_info=True)

    with cache.scope():
        values = {}
        for node in nodes:
            for attr in selection._survey_node(node):
                plug = "{0}.{1}".format(node, attr)
                try:
                    values[plug] = cmds.getAttr(plug)
                except Exception:
                    log.debug("animkit: could not read %s", plug, exc_info=True)
        root = pairing.root_of(nodes[0]) if nodes else None

    return Pose(values, root=root)


# --- apply ------------------------------------------------------------------


def _write(plug, value, layer, warning):
    """Write one channel value onto the curve for `plug` on `layer`.

    Goes through ensure_curve so a channel with no animation gets a curve on the
    right layer rather than a setAttr that the next keyframe would discard.
    """
    curve_name = layers.ensure_curve(plug, layer, time=curves.current_time())
    if not curve_name:
        return False

    kwargs = {}
    if layer and layer != layers.root_layer():
        kwargs["animLayer"] = layer
    try:
        cmds.setKeyframe(plug, value=value, **kwargs)
        return True
    except Exception:
        log.debug("animkit: could not key %s", plug, exc_info=True)
        return False


def apply(pose, label="paste pose"):
    """Write a Pose into the scene as one undo step. Returns channels written.

    Respects the layer policy: each plug resolves to the layer that would be
    keyed, locked and muted layers are skipped, and a multi-layer selection
    warns once.
    """
    if not pose:
        return 0

    written = 0
    with undo.LazyChunk("animkit: {0}".format(label)) as chunk:
        with cache.scope():
            warning = layers.LayerWarning()
            plan = []
            for plug, value in pose.values.items():
                node = plug.split(".", 1)[0]
                if not cmds.objExists(node):
                    continue
                layer = layers.target_layer(plug)
                warning.check(plug, layer)
                if layer and not layers.is_writable(layer):
                    continue
                plan.append((plug, value, layer))

            if not plan:
                return 0

            chunk.open()
            for plug, value, layer in plan:
                if _write(plug, value, layer, warning):
                    written += 1
                # ensure_curve may have built a blend network; anything cached
                # about this plug is now stale.
                cache.invalidate()

            try:
                cmds.dgdirty([p for p, _v, _l in plan])
            except Exception:
                log.debug("animkit: dgdirty failed", exc_info=True)
            warning.emit()

    return written


# --- stranded controls ------------------------------------------------------


def _off_default(node, attr):
    """True if `attr` sits away from its OWN declared default.

    attributeQuery -listDefault, never a hardcoded zero. A rig attribute whose
    rigger declared 30 the default is at rest at 30, and treating 0 as its rest
    is how a control ends up with keys on channels nobody touched.
    """
    plug = "{0}.{1}".format(node, attr)
    try:
        declared = cmds.attributeQuery(attr, node=node, listDefault=True)
        if not declared:
            return False
        current = cmds.getAttr(plug)
    except Exception:
        return False

    try:
        return abs(float(current) - float(declared[0])) > 1e-6
    except (TypeError, ValueError):
        # Enum, bool, or something exotic. Compare directly rather than guess.
        return current != declared[0]


def _posed_extra_channels(node, selection):
    """Non-transform channels the animator has posed. See key_stranded.

    include_untweenable=True on the survey: visibility is excluded from the
    tween path because it cannot be interpolated, which says nothing about
    whether it belongs in a pose. Switched off, it plainly does.
    """
    transform = set(xform.TRANSFORM_CHANNELS)
    out = []
    for attr in selection._survey_node(node, True):
        if attr in transform:
            continue
        plug = "{0}.{1}".format(node, attr)
        try:
            if cmds.keyframe(plug, q=True, keyframeCount=True):
                continue  # already animated; not ours to touch
        except Exception:
            continue
        if _off_default(node, attr):
            out.append(attr)
    return sorted(out)


def key_stranded(nodes, warning=None):
    """Set a key on any control that is posed but carries no animation.

    THE AMBIGUITY THIS RESOLVES, and why keying is the right way to resolve it.
    A control away from its defaults with no animation is unreadable: nothing in
    the scene distinguishes "the animator posed this" from "the rigger built it
    here", so its rest is taken to be where it stands and it cannot mirror. See
    xform.unkeyed_but_posed.

    Refusing was the old behaviour, and the advice that went with it -- "turn on
    Auto Key" -- CANNOT WORK. Maya's Auto Key only updates channels that already
    carry a curve; it never creates the first one. So a control whose keys were
    undone away stops keying silently on every subsequent move, with Auto Key on
    the whole time, and the animator does exactly as they were told and is
    refused again. That reads as the mirror randomly breaking, and it was.

    A control the animator has visibly posed is not actually ambiguous about
    what they wanted. Key it and carry on.

    WHAT GETS KEYED, and why it is neither "the transform set" nor "everything"
    ---------------------------------------------------------------------------
    THE WHOLE POSABLE TRANSFORM SET, always. Not just the off-default channels:
    one key on rotateY would clear the refusal and leave rotateX and rotateZ
    curveless, so the next pose on either would strand the control all over
    again -- and Auto Key still could not save it. Keying the set is what makes
    the control keyable from then on.

    PLUS any other keyable channel the animator has actually POSED, meaning one
    sitting away from its own attribute default. Visibility switched off, an
    ikFkBlend dialled to 0.5, a finger curl -- those are part of the pose, and
    leaving them curveless leaves Auto Key unable to key them later, which is
    the very trap this function exists to close. It just closes it one channel
    at a time instead of everywhere at once.

    NOT the channels sitting at their defaults. That is the whole difference
    between this and pressing S. S keys all twenty-two channels on an IK leg --
    Volume, Fatness 1, Roll Start Angle, every one of them noise the animator
    never touched. Reading each channel's own default via attributeQuery means a
    rigger who parked Roll Start Angle at 30 and declared 30 the default gets no
    key, while an animator who moved it to 45 does.

    The residual ambiguity is a rigger who added an attribute WITHOUT a default
    and left it off zero: that reads as posed and gets keyed. Bounded -- it can
    only happen on a control already being auto-keyed -- and it errs toward
    keying something the animator can see, which is the safe direction.

    Caller is responsible for being inside an undo chunk. Returns the plugs keyed.
    """
    from animkit.core import selection

    keyed = []

    for node in nodes:
        try:
            if not cmds.objExists(node) or not xform.unkeyed_but_posed(node):
                continue
        except Exception:
            continue

        channels = sorted(xform.settable_transform_channels(node)
                          - xform.animated_channels(node))
        channels.extend(_posed_extra_channels(node, selection))

        # Grouped by layer so a nine-channel control costs one setKeyframe
        # rather than nine, and so the layer policy is applied per plug rather
        # than assumed uniform across the node.
        groups = {}
        for channel in channels:
            plug = "{0}.{1}".format(node, channel)
            layer = layers.target_layer(plug)
            if warning is not None:
                warning.check(plug, layer)
            if layer and not layers.is_writable(layer):
                continue
            groups.setdefault(layer, []).append(plug)

        for layer, plugs in groups.items():
            kwargs = {}
            if layer and layer != layers.root_layer():
                kwargs["animLayer"] = layer
            # No value= : setKeyframe with no value keys what the channel
            # already reads, which is the pose the animator put there. Passing
            # a getAttr value back would be the one thing this codebase never
            # does, and there is no reason to start here.
            try:
                cmds.setKeyframe(plugs, **kwargs)
                keyed.extend(plugs)
            except Exception:
                log.debug("animkit: batched key of stranded %s failed", node,
                          exc_info=True)
                for plug in plugs:
                    try:
                        cmds.setKeyframe(plug, **kwargs)
                        keyed.append(plug)
                    except Exception:
                        log.debug("animkit: could not key stranded %s", plug,
                                  exc_info=True)

    if keyed:
        # The graph changed under any surrounding cache.scope(): every one of
        # unkeyed_but_posed, animated_channels, _node_has_animation and
        # _source_plug is cached and is about to be asked again by mirror_pose.
        cache.invalidate()
        try:
            cmds.dgdirty(keyed)
        except Exception:
            log.debug("animkit: dgdirty after keying stranded failed",
                      exc_info=True)

    return keyed


# --- the mirror -------------------------------------------------------------


class MirrorRefused(Exception):
    """The rig is not symmetric at rest, so a mirror cannot be trusted."""


def mirror_pose(pose, nodes=None, swap=False):
    """Return a new Pose with `pose` mirrored across the rig's symmetry plane.

    swap=False  mirror onto the opposite controls (mirror)
    swap=True   exchange the two sides (flip)

    Raises MirrorRefused when the rig is not symmetric at rest. Refusing is the
    point: a mirror on an asymmetric rest pose does not fail loudly, it produces
    a pose whose silhouette reads and whose limb is wrong.
    """
    nodes = nodes if nodes is not None else pose.nodes
    if not nodes:
        return Pose()

    with cache.scope():
        # FIRST, before anything else can produce a more confusing message.
        #
        # A control moved without a key has no animation, so its rest pose is
        # wherever it now stands. That does not merely stop it mirroring -- it
        # moves its rest position, which breaks rest symmetry, which makes the
        # symmetry check refuse with "the rig is not symmetric at rest". True,
        # and useless: the rig is fine, the missing key is the problem. Checking
        # here means the animator is told the thing they can act on.
        stranded = [n for n in nodes if xform.unkeyed_but_posed(n)]
        if stranded:
            raise MirrorRefused(
                "%d control(s) are away from their default values but have no "
                "keys, so animkit cannot tell a pose from a rig offset and reads "
                "them as resting where they stand: %s. Set a key on them and "
                "mirror again. Auto Key CANNOT do this -- it only updates "
                "channels that already carry a curve, so a control whose keys "
                "were undone away stops keying silently no matter how it is set."
                % (len(stranded), ", ".join(stranded[:5]))
            )

        reflection = pairing.reflection_for(nodes[0], root=pose.root)

        # ONE call, deliberately. Rest asymmetry surfaces at two stages -- a
        # pair that fails verification, and a counterpart rejected before it
        # could become a pair -- and checking only one of them reports success
        # on a rig that cannot be mirrored. See pairing.Symmetry.
        symmetry = pairing.analyse(nodes, reflection=reflection)
        if not symmetry.ok:
            raise MirrorRefused(
                "rig is not symmetric at rest: {0}. Put the rig at rest and run "
                "capture_rest_pose() if its controls do not zero to their "
                "defaults.".format(symmetry.describe())
            )

        pairs = symmetry.pairs
        if symmetry.missing:
            cmds.warning(
                "animkit: no counterpart found for %d control(s): %s"
                % (len(symmetry.missing), ", ".join(symmetry.missing[:5]))
            )

        # DIRECTION IS DECIDED BY WHICH SIDE IS POSED, not by selection order.
        #
        # pair_up walks the selection and makes the first node it meets the
        # source, so with both members of a pair selected the direction came
        # down to click order -- invisible to the animator, and reversed as
        # often as not. Worse, selecting the wrong side alone mirrored REST onto
        # the posed side and destroyed the pose, while printing "nothing to
        # mirror". The posed side is what the animator means every time, and it
        # is knowable, so ask the scene instead of the selection list.
        selected = set(nodes)
        values = {}
        moved = 0
        ambiguous = []
        destructive = []

        for a, b in pairs:
            if swap or a == b:
                # A flip exchanges both sides, and a centre control mirrors onto
                # itself. Neither poses a direction question.
                directions = [(a, b)]
                if swap and a != b:
                    directions.append((b, a))
            else:
                source, target = a, b
                if b in selected:
                    a_posed, b_posed = _is_posed(a), _is_posed(b)
                    if a_posed and b_posed:
                        ambiguous.append((a, b))
                        continue
                    if b_posed and not a_posed:
                        source, target = b, a
                if not _is_posed(source) and _is_posed(target):
                    # Mirroring rest onto a posed control ERASES it. That is a
                    # real request to make one side match the other, but it is
                    # far more often the wrong control selected, and it is not
                    # worth losing a pose over. Reset does this deliberately.
                    destructive.append((source, target))
                    continue
                directions = [(source, target)]

            for source, target in directions:
                values.update(_mirror_one(pose, source, target, reflection))
                if _has_pose(pose, source):
                    moved += 1

        if ambiguous:
            cmds.warning(
                "animkit: both sides of %d pair(s) are posed and both are "
                "selected, so there is no way to tell which way you meant: %s. "
                "Select only the side you posed, or use Flip to swap them."
                % (len(ambiguous), pairing.describe_pairs(ambiguous))
            )

        if destructive:
            cmds.warning(
                "animkit: skipped %d pair(s) that would have erased a pose -- "
                "the selected control is at rest and its counterpart is posed: "
                "%s. Select the posed side to mirror it across, or use Reset if "
                "you meant to clear it."
                % (len(destructive), pairing.describe_pairs(destructive))
            )

        # NOTHING TO MIRROR MEANS NOTHING WRITTEN. Not "nothing worth warning
        # about" -- nothing written. Saying "nothing to mirror" and then writing
        # nine channels of rest pose over a posed control is how this warning
        # came to be printed at the exact moment a pose was being destroyed.
        #
        # Unconditional on `moved`, deliberately. Gating this on there being no
        # skipped pairs left a hole: one pair at rest and another skipped as
        # ambiguous still wrote the resting pair's values back over itself, for
        # no change and one undo entry the animator did not ask for.
        if pairs and not moved:
            # The WARNING is what gets suppressed when a pair has already been
            # reported. A skip is explained in terms the animator can act on;
            # following it with "everything is at its rest pose" tells them a
            # second, different, and wrong story.
            if not ambiguous and not destructive:
                cmds.warning(
                    "animkit: nothing to mirror -- every selected control is at "
                    "its rest pose, so there is no pose to reflect. If a control "
                    "LOOKS posed but reads as at rest, it has no keys: animkit "
                    "cannot tell a pose from a rig offset without one. Press S "
                    "on it, then try again -- Auto Key will not do it, because "
                    "it only updates channels that already carry a curve."
                )
            return Pose(root=pose.root)

    return Pose(values, root=pose.root)


def _is_posed(node):
    """True if `node` currently sits away from its own rest pose.

    Reads the SCENE, not a captured Pose. _is_at_rest can only answer for a
    control whose channels are in the pose, i.e. one that was selected -- and
    the control this has to answer for is usually the counterpart, which is
    precisely the one that was not.

    Counts custom attributes as well as the transform delta. An ikFkBlend
    dialled across or a finger curl set is a pose with no transform motion at
    all, and a rule that could not see it would call a posed control "at rest"
    and let the mirror run the wrong way over it.
    """
    from animkit.core import selection

    try:
        delta = xform.pose_delta(node)
        if any(abs(delta[i] - xform.IDENTITY[i]) > 1e-6 for i in range(16)):
            return True
    except Exception:
        return False

    transform = set(xform.TRANSFORM_CHANNELS)
    for attr in selection._survey_node(node, True):
        if attr not in transform and _off_default(node, attr):
            return True
    return False


def _has_pose(pose, node):
    """True if `node`'s CAPTURED channels carry something to reflect.

    The gate for "nothing to mirror", and it has to agree with _is_posed or the
    two disagree about the same control. Transform channels via the rest delta,
    everything else against its own declared default.
    """
    if not _is_at_rest(pose, node):
        return True
    transform = set(xform.TRANSFORM_CHANNELS)
    for attr in pose.channels_for(node):
        if attr not in transform and _off_default(node, attr):
            return True
    return False


def _is_at_rest(pose, node):
    """True if this node's captured pose is its rest pose (identity delta)."""
    channels = pose.channels_for(node)
    if not channels:
        return True
    try:
        local = xform.compose_local(
            node,
            translate=_triple(channels, xform.TRANSLATE, node),
            rotate=_triple(channels, xform.ROTATE, node),
            scale=_triple(channels, xform.SCALE, node),
        )
        delta = xform.pose_delta(node, local=local)
    except Exception:
        return False
    return all(
        abs(delta[i] - xform.IDENTITY[i]) < 1e-6 for i in range(16)
    )


def _mirror_one(pose, source, target, reflection):
    """Channel values that put `target` in `source`'s mirrored pose."""
    out = {}

    from animkit.core import selection

    source_channels = pose.channels_for(source)
    if not source_channels:
        return out

    # TWO different gates, and conflating them drops every custom attribute.
    # settable_transform_channels() is intersected with TRANSFORM_CHANNELS and
    # by construction never mentions ikFkBlend, so using it for custom
    # attributes silently discards them -- the pose applies, the limb mirrors,
    # and the IK/FK switch quietly does not come with it.
    settable = xform.settable_transform_channels(target)
    writable = set(selection._survey_node(target))

    # --- transform channels, through the derived frame relation --------------
    transform_channels = [
        c for c in xform.TRANSFORM_CHANNELS if c in source_channels
    ]
    if transform_channels:
        local = xform.compose_local(
            source,
            translate=_triple(source_channels, xform.TRANSLATE, source),
            rotate=_triple(source_channels, xform.ROTATE, source),
            scale=_triple(source_channels, xform.SCALE, source),
        )
        delta = xform.pose_delta(source, local=local)
        relation = xform.frame_relation(source, target, reflection)
        mirrored = xform.mirror_delta(delta, relation)
        target_local = xform.local_from_delta(target, mirrored)

        values = xform.channels_for_local(target, target_local)
        if values is None:
            cmds.warning(
                "animkit: could not solve a mirrored pose for %s" % target
            )
        else:
            for channel in transform_channels:
                if channel not in settable:
                    continue
                out["{0}.{1}".format(target, channel)] = values[channel]

    # --- everything else: copied across, optionally negated -----------------
    for channel, value in source_channels.items():
        if channel in xform.TRANSFORM_CHANNELS:
            continue
        if channel not in writable:
            continue
        if channel in MIRROR_NEGATE_ATTRS:
            try:
                value = -value
            except TypeError:
                pass
        out["{0}.{1}".format(target, channel)] = value

    return out


def _triple(channels, names, node):
    """Values for a channel triple, falling back to the node's current value."""
    out = []
    for name in names:
        if name in channels:
            out.append(channels[name])
        else:
            try:
                out.append(cmds.getAttr("{0}.{1}".format(node, name)))
            except Exception:
                out.append(1.0 if name in xform.SCALE else 0.0)
    return out


# --- operations -------------------------------------------------------------


def copy_pose(nodes=None):
    """Capture the selection into the clipboard. Returns channels captured."""
    global _clipboard
    _clipboard = capture(nodes)
    if not _clipboard:
        cmds.warning("animkit: nothing selected to copy")
    return len(_clipboard)


def paste_pose():
    """Apply the clipboard as-is."""
    if not _clipboard:
        cmds.warning("animkit: no pose copied yet")
        return 0
    return apply(_clipboard, label="paste pose")


def _mirrored_write(nodes, label, swap=False, pose=None):
    """Key any stranded control, mirror, and write the result. One undo step.

    THE CHUNK IS OPENED HERE, not left to apply(). Keying a stranded control is
    a scene write like any other; left to apply()'s own chunk it would land
    outside it and a mirror would cost the animator two Ctrl+Z presses. The
    second press would then strand the control again -- undoing the very key
    that made the first press work -- which is precisely the trap key_stranded
    exists to close. Splitting the chunk would rebuild it.

    apply()'s LazyChunk nests inside this one; Maya collapses nested chunks into
    the outermost, so the animator still gets exactly one entry. And because
    both chunks are lazy, an operation that keys nothing and writes nothing
    still leaves the undo queue untouched.
    """
    with undo.LazyChunk("animkit: {0}".format(label)) as chunk:
        with cache.scope():
            stranded = [n for n in nodes if xform.unkeyed_but_posed(n)]
            if stranded:
                chunk.open()
                keyed = key_stranded(stranded)
                if keyed:
                    print(
                        "animkit: keyed %d posed-but-unkeyed control(s) so they "
                        "could be mirrored: %s"
                        % (len(stranded), ", ".join(sorted(stranded)[:5]))
                    )

        if pose is None:
            pose = capture(nodes)
        if not pose:
            cmds.warning("animkit: nothing selected to %s" % label.split()[0])
            return 0

        try:
            mirrored = mirror_pose(pose, nodes=nodes, swap=swap)
        except MirrorRefused as exc:
            cmds.warning("animkit: %s" % exc)
            return 0

        return apply(mirrored, label=label)


def paste_mirrored():
    """Apply the clipboard onto the opposite controls."""
    if not _clipboard:
        cmds.warning("animkit: no pose copied yet")
        return 0
    # The clipboard's OWN nodes, not the current selection: the stranded control
    # that blocks this mirror is the source the pose was read from.
    return _mirrored_write(
        _clipboard.nodes, "paste mirrored pose", pose=_clipboard
    )


def mirror_selected():
    """Mirror the selected controls onto their counterparts."""
    from animkit.core import selection

    nodes = selection.selected_nodes()
    if not nodes:
        cmds.warning("animkit: nothing selected to mirror")
        return 0
    return _mirrored_write(nodes, "mirror pose")


def flip_selected():
    """Swap the pose of the selected controls with their counterparts."""
    from animkit.core import selection

    nodes = selection.selected_nodes()
    if not nodes:
        cmds.warning("animkit: nothing selected to flip")
        return 0
    return _mirrored_write(nodes, "flip pose", swap=True)


def capture_rest_pose(nodes=None, force=False):
    """Declare the CURRENT pose to be the rig's rest pose.

    The escape hatch for a rig whose controls do not zero to their attribute
    defaults. Put the rig at rest, run this once, and every frame relation
    afterwards is derived from that snapshot instead of from the defaults.

    VERIFIES WHAT IT CAPTURED, and reverts if it is not symmetric.

    That check is not optional, because the failure without it is brutal: press
    this with the rig posed and the pose becomes the rest pose, every frame
    relation is derived from an asymmetric reference, and the mirror refuses
    from then on -- while this function prints a cheerful success message. It
    happened on the first real rig it met, on a posed arm, and the animator had
    no way to tell what they had done or how to undo it.

    So an asymmetric capture is rolled back rather than kept. force=True keeps
    it anyway, for a rig that really is asymmetric at rest and where mirroring
    is not the point.
    """
    from animkit.core import selection

    nodes = nodes if nodes is not None else selection.selected_nodes()
    if not nodes:
        cmds.warning("animkit: select a rig control, with the rig AT REST")
        return 0

    previous = dict(xform._rest_overrides)

    with cache.scope():
        pool = set(nodes)
        for node in list(nodes):
            pool.update(pairing.search_pool(node))
        # Controls only, ASKED OF THE RIG. Verifying symmetry across the rig's
        # internals fails on a perfectly good rig and reports it in the language
        # of the plumbing -- see controls_in.
        controls = sorted(controls_in(sorted(pool)))

    if not controls:
        cmds.warning("animkit: found no posable controls to capture a rest from")
        return 0

    count = xform.capture_rest(controls)

    with cache.scope():
        symmetry = pairing.analyse(
            controls, reflection=pairing.reflection_for(nodes[0])
        )

    if not symmetry.ok and not force:
        # Put back whatever was there before, so a mistaken press leaves no
        # trace rather than quietly disabling the mirror.
        xform.clear_rest_overrides()
        for node, matrix in previous.items():
            xform.set_rest_override(node, matrix)
        cmds.warning(
            "animkit: NOT captured -- the rig is not symmetric in this pose, so "
            "it cannot be a rest pose (%d pair(s) off, e.g. %s). Put the rig on "
            "its bind/default pose and try again. Most rigs need no rest "
            "capture at all."
            % (len(symmetry.broken), symmetry.describe(limit=3))
        )
        return 0

    # Written into the SCENE, not just this session. Without this the animator
    # does the thing every ambiguous Reset tells them to do, it works, and it is
    # gone when they reopen the shot tomorrow -- which made the advice hollow.
    stored = 0
    try:
        from animkit.core import rest_store

        stored = rest_store.save()
    except Exception:
        log.exception("animkit: captured a rest pose but could not store it")
        cmds.warning(
            "animkit: captured a rest pose, but could not save it into the "
            "scene -- it will be lost when you reopen. See the Script Editor."
        )

    print(
        "animkit: captured a rest pose for %d control(s) (%d pair(s) verified "
        "symmetric, %d saved in the scene)"
        % (count, len(symmetry.pairs), stored)
    )
    return count


def animated_plugs(nodes):
    """{node: [channels]} for every transform channel carrying animation.

    "Where the rig would be with no animation" is exactly "every animated
    channel at its attribute default", so animation is the right thing to
    enumerate. It also excludes the rig's plumbing for free: a constraint-driven
    node has no animation, so resetting never fights a constraint.
    """
    found = {}
    for node in nodes:
        channels = sorted(xform.animated_channels(node))
        if channels:
            found[node] = channels
    return found


#: Shape types an animator can see and click in the viewport. A CONTROL IS
#: DRAWN; rig plumbing is not. Every rig in the world draws its controls,
#: because a control the animator cannot see is a control they cannot use --
#: which makes this the one property that does not vary by rig, studio or
#: naming convention.
#:
#: nurbsCurve covers bezierCurve too (it derives from it), and locator covers
#: the rigs that use plain locators as controls.
CONTROL_SHAPE_TYPES = ("nurbsCurve", "locator")

#: How much of a set has to be control-shaped before the set counts as a
#: control set. A majority, not all: a legitimate control set often carries a
#: stray joint-driven control or a group along with the real ones.
CONTROL_SET_RATIO = 0.5


def _has_control_shape(node):
    """True if `node` carries a drawable, non-intermediate shape.

    Visibility is deliberately NOT checked. A rig hides its FK controls while in
    IK, and a hidden control is still a control -- that is a mode, not a
    classification, and resetting "the whole rig" has to reach both sides of an
    IK/FK switch.
    """
    try:
        shapes = cmds.listRelatives(node, shapes=True, fullPath=True) or []
    except Exception:
        return False
    for shape in shapes:
        try:
            if cmds.getAttr(shape + ".intermediateObject"):
                continue
            kinds = cmds.nodeType(shape, inherited=True) or []
        except Exception:
            continue
        if any(kind in kinds for kind in CONTROL_SHAPE_TYPES):
            return True
    return False


def _shaped_controls():
    """Every posable transform carrying a control shape, via the shapes.

    Listed from the SHAPES rather than by walking transforms: one ls call
    returns every curve in the scene, against ~1500 transforms to test on a
    character rig.
    """
    found = []
    seen = set()
    for kind in CONTROL_SHAPE_TYPES:
        try:
            shapes = cmds.ls(type=kind, long=True) or []
        except Exception:
            continue
        for shape in shapes:
            try:
                if cmds.getAttr(shape + ".intermediateObject"):
                    continue
                parents = cmds.listRelatives(shape, parent=True) or []
            except Exception:
                continue
            for node in parents:
                short = node.split("|")[-1]
                if short in seen:
                    continue
                seen.add(short)
                if xform.settable_transform_channels(short):
                    found.append(short)
    return found


def _control_set_members(shaped):
    """Members of any objectSet that is MOSTLY control-shaped.

    This is how a control set is recognised WITHOUT knowing its name. A set
    whose members are mostly things the animator can click is a control set --
    whether the rig calls it ControlSet, rig_controllers_grp, anim_ctrls or
    something nobody has ever seen before. A set of joints scores zero and is
    rejected on the same rule, with no list of forbidden names to maintain.

    Its value over the shape rule alone is the members that have NO shape: a
    joint used directly as a control comes along because the set it sits in has
    already proved what kind of set it is.
    """
    extra = []
    try:
        sets = cmds.ls(type="objectSet") or []
    except Exception:
        return extra

    for node in sets:
        members = [m.split("|")[-1] for m in _set_members(node)]
        transforms = []
        for member in members:
            try:
                if "transform" in (cmds.nodeType(member, inherited=True) or []):
                    transforms.append(member)
            except Exception:
                continue
        if len(transforms) < 2:
            continue
        hits = sum(1 for t in transforms if t in shaped)
        if hits < len(transforms) * CONTROL_SET_RATIO:
            continue
        for member in transforms:
            if member not in shaped and xform.settable_transform_channels(member):
                extra.append(member)
    return extra


def _set_members(node, _depth=0):
    """Members of an objectSet, flattened through nested sets."""
    if _depth > 8:
        return []
    try:
        members = cmds.sets(node, q=True) or []
    except Exception:
        return []
    out = []
    for member in members:
        try:
            if cmds.objectType(member, isAType="objectSet"):
                out.extend(_set_members(member, _depth + 1))
                continue
        except Exception:
            pass
        out.append(member)
    return out


def rig_controls():
    """Every transform that is an animation control, on any rig, or [].

    ASK THE RIG, DO NOT GUESS ITS NAMING. This is the signal whose absence meant
    a scene-wide reset could never touch an unkeyed channel: all_rig_nodes()
    cannot tell a control from a twist joint, so the only safe answer was to
    leave every unkeyed channel alone.

    Three sources, and the order is the point:

      1. MAYA'S CONTROLLER TAG. `cmds.controller` exists precisely to mean "this
         is an animation control". A rig that uses it has already answered, in
         Maya's own vocabulary rather than in a convention we had to learn.

      2. THE SHAPE. A control is DRAWN -- a curve or a locator the animator can
         see and click -- and rig plumbing is not. Offset groups, extra groups,
         constraint targets and stabilisers are empty transforms. That is what
         makes this rig-agnostic: it is not a property of any studio's naming,
         it is what makes a control usable at all. The 1521-of-1529 problem
         collapses here, because not one of those 1521 internals is drawn.

      3. A CONTROL SET, RECOGNISED BY ITS CONTENTS. A set whose members are
         mostly control-shaped is a control set whatever it is called, so no
         list of blessed names is needed and none is kept. It contributes the
         members that have no shape of their own -- a joint used directly as a
         control -- because the set has already proved what it is.

    NOT A NAMING CONVENTION, at any point. `*_ctrl` also matches the offset and
    extra groups on every rig that names its plumbing after the control it
    carries, which is most of them, and a wrong answer here resets the plumbing.

    False positives are cheap by construction: a stray curve that is not a
    control still only gets reset on channels that are posable AND off their
    own default, so an IK-spline curve sitting at the origin is a no-op. False
    negatives are cheap too -- the animator selects that control by hand, which
    is what they had to do for everything before this existed.

    Returns [] when nothing can be established, which is the signal to fall back
    to all_rig_nodes() and leave unkeyed channels alone.
    """
    tagged = []
    seen = set()
    try:
        for tag in cmds.ls(type="controller") or []:
            names = cmds.listConnections(
                tag + ".controllerObject", source=True, destination=False
            ) or []
            for name in names:
                short = name.split("|")[-1]
                if short not in seen and cmds.objExists(short):
                    seen.add(short)
                    tagged.append(short)
    except Exception:
        log.debug("animkit: controller-tag scan failed", exc_info=True)

    if tagged:
        # An explicit declaration beats anything inferred. A rig that tags its
        # controls has said which they are; second-guessing that with geometry
        # could only ever add nodes it deliberately left out.
        return tagged

    shaped = _shaped_controls()
    if not shaped:
        return []

    known = set(shaped)
    return shaped + [n for n in _control_set_members(known) if n not in known]


def controls_in(nodes):
    """The nodes in `nodes` that are animation controls, in the given order.

    THE FUNCTION THAT USED TO LIVE IN core.pairing AND WAS WRONG. It filtered on
    "has at least one settable transform channel", which on a rig whose rigger
    did not lock the internals keeps 1521 of 1529 nodes -- so Set Rest verified
    symmetry across the whole plumbing and reported "280 pair(s) off, e.g.
    AlignIKToWrist_L/AlignIKToWrist_R" on a rig that was completely fine.

    It lives here now because this is where the rig gets ASKED rather than
    guessed at: rig_controls() reads controller tags, control shapes and control
    sets, so this is an intersection with a known-good answer.

    The fallback matters as much as the main path. A rig that declares nothing
    at all leaves rig_controls() empty, and returning [] there would make Set
    Rest impossible on exactly the rigs most likely to need it -- the odd ones.
    So it degrades to the old channel test, which is a bad control filter but a
    correct "could this receive a pose" filter, and that is the best available
    answer when the rig has said nothing.

    Short names throughout, matching pairing.search_pool and rig_controls.
    """
    if not nodes:
        return []

    declared = set(rig_controls())
    if declared:
        return [n for n in nodes if n.split("|")[-1] in declared]

    log.debug(
        "animkit: no controller tags, control shapes or control sets on this "
        "rig -- falling back to the settable-channel filter"
    )
    return pairing.with_settable_channels(nodes)


def all_rig_nodes():
    """Every transform in the scene, grouped under each top-level assembly.

    The FALLBACK for a rig that declares no controls -- see rig_controls(). This
    walk cannot tell a control from a twist joint, so a caller that lands here
    must leave unkeyed channels alone.
    """
    out = []
    try:
        roots = cmds.ls(assemblies=True, long=False) or []
    except Exception:
        return out
    for root in roots:
        try:
            if not cmds.listRelatives(root, shapes=False, children=True):
                continue
            found = cmds.listRelatives(
                root, allDescendents=True, type="transform", fullPath=False
            ) or []
        except Exception:
            continue
        out.append(root)
        out.extend(n.split("|")[-1] for n in found)
    return out


def _report_ambiguous_reset(plugs):
    """Say what was left alone, and name both ways to make it resettable.

    A warning nobody can act on is noise, so this names the controls, says why,
    and gives the two removals. The face-board case is called out by name
    because it is the one that looks most like a bug to the person reading it:
    they pressed Reset, the controls did not move, and the reason is a property
    of their rig rather than of the button.
    """
    nodes = []
    for plug in plugs:
        node = plug.split(".", 1)[0]
        if node not in nodes:
            nodes.append(node)

    cmds.warning(
        "animkit: left %d unkeyed channel(s) on %d control(s) alone -- they "
        "sit off their defaults with no keys, so animkit cannot tell a pose "
        "from where the rig was BUILT. On a facial control board, for example, "
        "every control sits off zero by design and zeroing them collapses the "
        "board. To reset them anyway: key them first (an animated channel's "
        "rest is its default), or put the rig on its neutral pose and press "
        "Set Rest so animkit knows where rest is. e.g. %s"
        % (len(plugs), len(nodes), ", ".join(nodes[:4]))
    )


def reset_to_default(nodes=None, include_unkeyed=None):
    """Put controls back where they would be with no animation on them.

    include_unkeyed=None (the default) means AUTO:

        with a selection      reset animated AND unkeyed channels
        with nothing selected reset animated channels only, and REPORT the
                              unkeyed ones rather than guessing at them

    THE LINE, AND WHERE IT WAS DRAWN WRONG
    --------------------------------------
    An unkeyed channel sitting off its default is either a pose or the rig's
    build offset, and nothing in the scene tells them apart -- that is stated in
    xform's module docstring, and the mirror path REFUSES on it rather than
    guess (see unkeyed_but_posed and MirrorRefused).

    Reset cannot refuse outright, because clearing a pose blocked out with no
    keys yet is the state people most want it for. So it needs someone to
    resolve the ambiguity, and exactly two things can:

        a captured rest pose   the animator said where rest IS. Authoritative,
                               per control, and it survives reopening the shot.
        an explicit selection  the animator picked THESE controls and pressed
                               Reset. That is a statement about them.

    The bug was treating a third thing as equivalent: `rig_controls()`. Pressing
    Reset with NOTHING selected swept in every control-shaped node on the rig --
    which on a facial control board is all of them, sitting off zero by design,
    none of them keyed -- and zeroed them. The board collapsed onto one point.

    rig_controls() answers "is this a control". It does not answer "where is
    this control's rest", and only the second question was ever being asked.

    Measured on the rig this was found on: nothing selected reached 48 controls
    with unkeyed off-default channels; the animator's own ~200-control selection
    reached exactly one -- the FK shoulder they had just rotated, which is
    precisely the control they wanted reset.

    A face board is still resettable, and permanently: press Set Rest on the
    neutral pose once. That is stored in the scene now rather than in this
    session, so it holds for every future open of the shot.

    With nothing selected this resets every rig in the scene, which is the common
    reason for wanting it -- getting a shot back to its bind pose before
    capturing a rest pose, or before starting over.

    Defaults come from attributeQuery -listDefault, not from a hardcoded zero,
    so a rigger who gave a control a non-zero default gets that default back
    rather than the origin.

    Animated channels are RE-KEYED at their default rather than merely set: a
    plain setAttr on an animated channel is overwritten by its curve at the next
    evaluation, so the control would visibly snap back.

    WHY THIS IS NOT SIMPLY ON EVERYWHERE
    ------------------------------------
    It used to be unconditional, and it destroyed a production rig on its first
    outing. The idea was to also zero posable-but-unanimated channels sitting
    away from their defaults -- a control somebody dragged without keying. The
    flaw is that "posable" does not mean "control": on an AdvancedSkeleton
    character, 1521 of 1529 transforms under the root are keyable and unlocked
    because riggers do not bother locking internals, and every one of them sits
    at a non-default value BY DESIGN. Zeroing them does not reset a pose, it
    dismantles the rig.

    There is no reliable way to tell "a control the animator dragged" from "a node
    the rigger placed" -- so the SELECTION is used as that signal, which is the
    one place the information actually exists. Scene-wide it stays off and an
    explicit include_unkeyed=True is refused outright.

    One undo step. Returns the number of channels changed.
    """
    from animkit.core import selection

    explicit = nodes is not None or bool(selection.selected_nodes())
    nodes = nodes if nodes is not None else selection.selected_nodes()

    # TWO DIFFERENT DECLARATIONS, and conflating them is what collapsed a face
    # board. Both were called "declared" and treated as equivalent:
    #
    #   explicit   the animator SELECTED these. That is a statement about the
    #              controls they want reset, made per control, by the only
    #              party who knows.
    #   rig_controls()
    #              the RIG says these are controls. True, and it says nothing
    #              whatever about whether the animator wants any of them reset.
    #
    # Pressing Reset with nothing selected went down the second path, swept in
    # every control-shaped node on the rig -- including a face board's, which
    # sit off zero by design -- and zeroed their unkeyed channels.
    #
    # Measured on the rig this was found on: nothing selected reached 48
    # controls with unkeyed off-default channels; the animator's own
    # ~200-control selection reached exactly one, the FK shoulder they had just
    # rotated. The selection is the honest signal. The sweep is not.
    declared = explicit
    if not nodes:
        nodes = rig_controls()
        declared = bool(nodes)
        if not nodes:
            nodes = all_rig_nodes()
    if not nodes:
        cmds.warning("animkit: found nothing to reset")
        return 0

    # WHO IS ALLOWED TO RESOLVE THE AMBIGUITY BELOW.
    #
    # An unkeyed channel sitting off its default is either a pose or the rig's
    # build offset, and nothing in the scene tells them apart. Exactly two
    # things can answer it, and both are a person:
    #
    #   a captured rest pose   the animator said where rest IS. Authoritative,
    #                          per control, and it now survives reopening the
    #                          shot -- see animkit.core.rest_store.
    #   an explicit selection  the animator picked THESE controls and pressed
    #                          Reset. That is a statement about them.
    #
    # What may NOT answer it is a rig_controls() sweep. It says "these are
    # controls", which is true of a facial control board and tells you nothing
    # about where a board control's rest is. Conflating the two is what
    # collapsed a board on a press with NOTHING selected.
    resolve_ambiguity = include_unkeyed is True or explicit

    if include_unkeyed is None:
        # `declared`, so the scene-wide press still LOOKS at unkeyed channels
        # and can therefore report them. Whether it may ZERO one is a separate
        # question with a stricter answer -- see resolve_ambiguity above.
        #
        # The two gates were one gate, which is how "is this a control" came to
        # authorise "and I know where its rest is".
        include_unkeyed = declared

    if include_unkeyed and not declared:
        # Refused rather than obeyed. With nothing declared this walks the rig's
        # internals -- see the docstring for what that did the one time it was
        # allowed.
        cmds.warning(
            "animkit: refusing to reset unkeyed channels across the whole scene "
            "-- with no control set and no controller tags to go on, that "
            "reaches the rig's internal nodes, which sit off their defaults by "
            "design. Select the controls you mean and run it again."
        )
        include_unkeyed = False
        resolve_ambiguity = False

    # Read fresh. getAttr is stale after any key write, so a reset run straight
    # after keying would compare against pre-key values and skip channels it
    # should have reset.
    try:
        cmds.dgdirty(nodes)
    except Exception:
        log.debug("animkit: dgdirty before reset failed", exc_info=True)

    changed = 0
    with undo.LazyChunk("animkit: reset pose") as chunk:
        with cache.scope():
            warning = layers.LayerWarning()
            keyed_plan = []
            plain_plan = []
            ambiguous = []

            for node in nodes:
                # CHEAPEST QUESTION FIRST. On a rig, all but a few dozen nodes
                # carry no animation, and one keyframeCount query rejects each of
                # them. Asking anything else first -- defaults, posability,
                # existence -- pays for ~1500 nodes to answer about ~80: it was
                # 13,365 attributeQuery calls and a full second of a scene-wide
                # reset that had nothing to do.
                animated = xform.animated_channels(node)
                if not animated and not include_unkeyed:
                    continue

                defaults = xform.channel_defaults(node)

                # A CAPTURED rest beats an attribute default, for both kinds of
                # channel. That is what Set Rest is for, and until now Reset did
                # not consult it at all -- so the one button that exists to say
                # "rest is HERE" had no effect on the one operation that most
                # needs to know.
                captured = xform.rest_channels(node)
                target = dict(defaults)
                if captured:
                    target.update(
                        (c, v) for c, v in captured.items() if c in target
                    )

                for channel in sorted(animated):
                    plug = "{0}.{1}".format(node, channel)
                    try:
                        if abs(cmds.getAttr(plug) - target[channel]) <= 1e-6:
                            continue
                    except Exception:
                        pass
                    layer = layers.target_layer(plug)
                    warning.check(plug, layer)
                    if layer and not layers.is_writable(layer):
                        continue
                    keyed_plan.append((plug, target[channel], layer))

                if not include_unkeyed:
                    continue
                for channel in sorted(
                    xform.settable_transform_channels(node) - animated
                ):
                    plug = "{0}.{1}".format(node, channel)
                    try:
                        current = cmds.getAttr(plug)
                    except Exception:
                        continue
                    if abs(current - target[channel]) <= 1e-6:
                        continue

                    if captured is None and not resolve_ambiguity:
                        # Off its default, no keys, no captured rest, and
                        # nobody has said which it is. Report it; do not guess.
                        ambiguous.append(plug)
                        continue

                    plain_plan.append((plug, target[channel]))

            if ambiguous:
                _report_ambiguous_reset(ambiguous)

            if not keyed_plan and not plain_plan:
                if ambiguous:
                    # Already reported, in terms the animator can act on. Saying
                    # "already at the default pose" on top of it would be a
                    # second and contradictory story about the same press.
                    pass
                elif include_unkeyed:
                    print(
                        "animkit: already at the default pose; nothing to reset"
                    )
                else:
                    # Do NOT claim the rig is at its default pose here. Scene-wide
                    # this deliberately ignores unkeyed channels, so a rig posed
                    # with no keys at all -- the state people most often want
                    # cleared -- is sitting right there looking posed while the
                    # tool says it is already clean. Say which question was
                    # actually asked.
                    print(
                        "animkit: no ANIMATED channel is off its default; "
                        "nothing to reset. Unkeyed channels were left alone "
                        "because this rig declares no control set and no "
                        "controller tags -- select the controls you mean and "
                        "run it again to clear an unkeyed pose."
                    )
                return 0

            chunk.open()

            # BATCHED. setKeyframe takes many plugs at once, and a reset only
            # ever writes two values -- 0 for translate/rotate, 1 for scale -- so
            # the whole rig collapses into a couple of calls per layer instead of
            # one per channel. No ensure_curve either: every plug here was
            # selected BECAUSE it already has a curve.
            groups = {}
            for plug, value, layer in keyed_plan:
                groups.setdefault((round(value, 6), layer), []).append(plug)

            for (value, layer), plugs in groups.items():
                kwargs = {"value": value}
                if layer and layer != layers.root_layer():
                    kwargs["animLayer"] = layer
                try:
                    cmds.setKeyframe(plugs, **kwargs)
                    changed += len(plugs)
                except Exception:
                    # Fall back to one at a time so a single bad plug cannot cost
                    # the whole group.
                    log.debug("animkit: batched setKeyframe failed", exc_info=True)
                    for plug in plugs:
                        try:
                            cmds.setKeyframe(plug, **kwargs)
                            changed += 1
                        except Exception:
                            log.debug("animkit: could not reset %s", plug,
                                      exc_info=True)

            for plug, value in plain_plan:
                try:
                    cmds.setAttr(plug, value)
                    changed += 1
                except Exception:
                    log.debug("animkit: could not reset %s", plug, exc_info=True)

            try:
                cmds.dgdirty([p for p, _v, _l in keyed_plan]
                             + [p for p, _v in plain_plan])
            except Exception:
                log.debug("animkit: dgdirty failed", exc_info=True)
            warning.emit()

    if explicit:
        scope = "the selection"
    elif declared:
        scope = "%d declared rig control(s)" % len(nodes)
    else:
        scope = "every rig in the scene"
    print("animkit: reset %d channel(s) on %s" % (changed, scope))
    return changed


def clear_rest_pose():
    """Forget any captured rest pose and go back to deriving it.

    The way out of a bad capture. Most rigs zero their controls at rest and need
    no capture at all, so this is also the right first thing to try when a
    mirror starts refusing.
    """
    had = len(xform._rest_overrides)
    try:
        from animkit.core import rest_store

        rest_store.clear()
    except Exception:
        log.exception("animkit: could not clear the stored rest pose")
        xform.clear_rest_overrides()
    print(
        "animkit: cleared a captured rest pose for %d nodes; rest is derived "
        "again" % had if had else
        "animkit: no captured rest pose to clear; rest was already derived"
    )
    return had


# --- registry ---------------------------------------------------------------


#: The shared Operation class -- see animkit.tools.registry.
Operation = registry.Operation


OPERATIONS = (
    Operation("animkitPoseCopy", "Copy",
              "Copy the selected controls' pose", copy_pose, group="Pose"),
    Operation("animkitPosePaste", "Paste",
              "Paste the copied pose onto the same controls", paste_pose,
              group="Pose"),
    Operation("animkitPosePasteMirrored", "Paste Opp.",
              "Paste the copied pose onto the opposite controls",
              paste_mirrored, group="Pose"),
    Operation("animkitPoseMirror", "Mirror",
              "Mirror the selected controls onto their counterparts",
              mirror_selected, group="Pose"),
    Operation("animkitPoseFlip", "Flip",
              "Swap the pose of the selected controls with their counterparts",
              flip_selected, group="Pose"),
    Operation("animkitPoseCaptureRest", "Set Rest",
              "Declare the current pose to be this rig's rest pose. Only needed "
              "for a rig whose controls do not zero to their defaults -- put the "
              "rig on its bind pose FIRST. Refuses if the pose is not symmetric.",
              capture_rest_pose, group="Pose"),
    Operation("animkitPoseClearRest", "Clear Rest",
              "Forget a captured rest pose and derive it again. Try this first "
              "if the mirror starts refusing.",
              clear_rest_pose, group="Pose"),
    Operation("animkitPoseReset", "Reset",
              "Put the selected controls back where they would be with no "
              "animation. With nothing selected, resets every rig in the scene.",
              reset_to_default, group="Pose"),
)

BY_NAME = dict((op.name, op) for op in OPERATIONS)
