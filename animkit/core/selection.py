"""Turning "what the animator has selected" into a list of plugs.

Animators expect selection to be contextual: with channels highlighted in the
Channel Box, an operation applies to those channels only; with nothing
highlighted, it applies to every keyable channel on the selected controls.
Getting this wrong makes a tool feel blunt.
"""

import logging

import maya.api.OpenMaya as om2
from maya import cmds

from animkit.core import cache

log = logging.getLogger(__name__)

#: Keyable attributes that must never be given an INTERPOLATED value. Tweening
#: visibility to 0.37 is meaningless, so the tween and blend paths exclude it.
#:
#: NOT a general "animkit ignores this channel" list, and the difference is the
#: whole reason this has a name. Operations that act on keys the animator ALREADY
#: SET -- delete, offset, retime, snap, tangents, cycle -- have to reach every
#: keyed channel on the control, or they silently desynchronise it: an offset
#: moves the rotate keys a frame and strands the visibility key where it was, and
#: a delete leaves the control still showing as keyed after the animator asked
#: for it to be cleared. Those paths pass include_untweenable=True.
#:
#: Pressing S in Maya keys visibility along with everything else, so this is not
#: a rare case -- it is what any animator working with S has on every control.
UNTWEENABLE = frozenset(("visibility",))


def selected_nodes():
    return cmds.ls(selection=True, long=False) or []


def channelbox_attrs():
    """Attributes highlighted in the Channel Box, or [] if none are.

    Covers all four Channel Box regions -- main (transform), shape, history
    (input) and output. Missing the shape/history ones is a common bug: it
    makes the tool ignore highlighted rig attributes like ikFkBlend.
    """
    box = "mainChannelBox"
    attrs = []
    for flag in ("sma", "ssa", "sha", "soa"):
        try:
            attrs.extend(cmds.channelBox(box, q=True, **{flag: True}) or [])
        except Exception:
            pass
    return attrs


def _to_long_names(node, attrs):
    """Map attribute names to their long form, dropping any that do not exist.

    Only ever runs on Channel Box highlights -- a handful of attributes -- so
    the extra attributeQuery per name costs nothing. Do not use it on a full
    listAttr result.
    """
    out = []
    for attr in attrs:
        try:
            if not cmds.attributeQuery(attr, node=node, exists=True):
                continue
            long_name = cmds.attributeQuery(attr, node=node, longName=True)
        except Exception:
            continue
        out.append(long_name or attr)
    return out


def _is_tweenable(node, attr):
    """cmds reference implementation. Kept as the oracle the API path is tested against.

    Not on the hot path any more -- see _survey_node -- but this is the
    definition of "tweenable" that plugs_from_selection is contractually
    required to produce, and tests/test_selection_maya.py asserts the fast
    path agrees with it channel for channel.
    """
    if attr in UNTWEENABLE:
        return False
    plug = "{0}.{1}".format(node, attr)
    try:
        if cmds.getAttr(plug, lock=True):
            return False
        # Stated rather than implied. Callers reach this through
        # `listAttr -keyable`, which already filters on it, so this changes no
        # answer -- but the API path had no such caller and read the ATTRIBUTE's
        # keyable flag instead of the PLUG's, which is a different question with
        # the same name. Writing it out here is what makes the two comparable.
        if not cmds.getAttr(plug, keyable=True):
            return False
        if not cmds.getAttr(plug, settable=True):
            return False
        if cmds.getAttr(plug, type=True) in ("string", "matrix", "message"):
            return False
    except Exception:
        return False
    return True


@cache.cached
def _survey_node(node, include_untweenable=False):
    """Every tweenable attribute on `node`, long names, via OpenMaya.

    include_untweenable adds back the channels in UNTWEENABLE, for operations
    that move or remove existing keys rather than writing interpolated values.
    See that constant for why the two need separating.

    This replaced a loop of three cmds.getAttr calls plus one
    cmds.attributeQuery PER ATTRIBUTE. On a 300-control selection that was
    ~11,400 command-engine round trips at mouse-down and 278ms of a 100ms
    budget. The API asks the same questions of the same data without paying
    the command engine each time.

    Every check below is the API equivalent of one the cmds version made, and
    a probe against constrained, directly-connected, locked and layered
    controls produced identical channel lists. Do not "simplify" this to
    cmds.listAttr(settable=True) -- that flag does NOT exclude a
    connection-driven channel, which is precisely the case that matters. It
    returns a constrained translateX happily; getAttr(settable=True) and
    MPlug.isFreeToChange() both refuse it.

    Cached per node, because a Channel Box query and a plug survey both want
    it and a drag asks for it repeatedly.
    """
    try:
        sel = om2.MSelectionList()
        sel.add(node)
        obj = sel.getDependNode(0)
        fn = om2.MFnDependencyNode(obj)
    except Exception:
        log.debug("animkit: could not open %r for survey", node, exc_info=True)
        return []

    out = []
    for i in range(fn.attributeCount()):
        try:
            attr = fn.attribute(i)
            afn = om2.MFnAttribute(attr)
            if not afn.keyable:
                continue
            name = afn.name
            if name in UNTWEENABLE and not include_untweenable:
                continue
            plug = om2.MPlug(obj, attr)
            if plug.isNull or plug.isLocked:
                continue
            # TWO DIFFERENT "keyable"s, and taking the wrong one shipped.
            #
            #   MFnAttribute.keyable  the ATTRIBUTE's definition. True for
            #                         translateX on every transform that has
            #                         ever existed, and nothing a rigger does
            #                         to a node ever changes it.
            #   MPlug.isKeyable       THIS plug's state, which is what
            #                         `setAttr -keyable false` sets.
            #
            # Only the second one answers "did the rigger take this channel out
            # of the animator's reach". Checking only the first meant every
            # channel a rigger had hidden still came back as writable -- and on
            # a facial control board a rigger hides nearly all of them, so a
            # reset with include_unkeyed zeroed channels nobody could even see
            # in the Channel Box.
            #
            # The cmds oracle in _is_tweenable was right the whole time; it
            # reads `listAttr -keyable`, which is the plug state. This is the
            # fast path being made to agree with the spec it was measured
            # against -- see tests/test_selection_maya.py.
            if not plug.isKeyable:
                continue
            if plug.isFreeToChange() != om2.MPlug.kFreeToChange:
                continue
        except Exception:
            # One bad attribute must not lose the other nine on the node.
            continue
        out.append(name)
    return out


def plugs_from_selection(nodes=None, include_untweenable=False):
    """Return the plug strings an operation should act on.

    Channel Box highlight narrows the set; otherwise every keyable, settable,
    unlocked attribute on each selected node is used.

    include_untweenable is for operations that act on keys the animator already
    set, rather than writing new interpolated values -- see UNTWEENABLE.

    Wrap a call in `cache.scope()` if you are going to make several, or if you
    are about to resolve layers for the result -- see animkit.core.cache.
    """
    nodes = nodes if nodes is not None else selected_nodes()
    if not nodes:
        return []

    highlighted = channelbox_attrs()
    plugs = []

    for node in nodes:
        tweenable = _survey_node(node, include_untweenable)
        if highlighted:
            # The Channel Box reports short names (tx); the survey reports
            # long ones (translateX). Normalise so one channel always produces
            # one plug string regardless of how the animator selected it.
            # Intersected with the survey rather than trusted on its own: a
            # highlighted channel can still be locked or constraint-driven.
            wanted = set(_to_long_names(node, highlighted))
            candidates = [a for a in tweenable if a in wanted]
        else:
            candidates = tweenable

        plugs.extend("{0}.{1}".format(node, attr) for attr in candidates)

    return plugs


def selected_keys():
    """Keys selected in the Graph Editor, as {curve_name: [indices]}.

    Empty dict when nothing is selected, which is the signal to fall back to
    current-time behaviour.

    Returns curve names rather than plugs deliberately. The user selected keys
    on a specific curve, so that curve IS the answer -- no layer resolution
    needed, and no risk of resolving to a different layer's curve than the one
    they were looking at.
    """
    try:
        curve_names = cmds.keyframe(q=True, selected=True, name=True) or []
    except Exception:
        log.debug("animkit: selected-key query failed", exc_info=True)
        return {}

    result = {}
    for curve in curve_names:
        try:
            indices = cmds.keyframe(curve, q=True, selected=True, indexValue=True)
        except Exception:
            continue
        if indices:
            result[curve] = sorted({int(i) for i in indices})
    return result
