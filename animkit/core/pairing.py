"""Finding a control's opposite number, on any rig, without a config file.

Two mechanisms, and the order matters:

    NAMING PROPOSES.  A library of the conventions rigs actually use --
                      L_/R_, _l/_r, Left/Right, lf/rt, namespace splits. Fast,
                      and right most of the time.

    GEOMETRY DECIDES. A candidate is only accepted if its REST position is the
                      reflection of the original's, and its settable transform
                      channels match. If naming finds nothing, or finds
                      something that does not hold up, the search falls back to
                      geometry alone and pairs on reflected rest position.

Because geometry is the gate, naming is allowed to be liberal. A pattern that
matches `Clavicle` when it meant `L` costs nothing: the candidate fails the
position check and is discarded. That is what lets this work on a rig whose
convention nobody wrote down.

The precondition, and what happens when it fails
------------------------------------------------
All of this assumes the rig is SYMMETRIC AT REST. `analyse` verifies that
rather than trusting it, and callers refuse to mirror when it fails. A mirror on
an asymmetric rest pose does not produce an error, it produces a pose that is
subtly wrong -- so the check is the difference between a tool that says "I
cannot do this rig" and one that quietly corrupts work.

Call `analyse`, not `pair_up` + `check_symmetry` + `classify_unpaired`. Rest
asymmetry shows up at two different stages and refusing on only one of them is
the bug those three primitives keep producing -- see the Symmetry class.
"""

import logging
import re

from maya import cmds

from animkit.core import cache, xform

log = logging.getLogger(__name__)

#: Left/right token pairs. Order matters only for readability -- every match is
#: geometrically verified, so a wrong guess is discarded rather than used.
SIDE_TOKENS = (
    ("left", "right"),
    ("lft", "rgt"),
    ("lf", "rt"),
    ("lt", "rt"),
    ("l", "r"),
)

#: How a token may be delimited inside a node name. Anchored so that the `l`/`r`
#: pair cannot match the middle of "clavicle" or "spine".
_DELIMITERS = (
    r"(?<=^){0}(?=_)",        # L_arm
    r"(?<=_){0}(?=_)",        # arm_L_ctrl
    r"(?<=_){0}(?=$)",        # arm_L
    r"(?<=^){0}(?=[A-Z])",    # LArm
    r"(?<=[a-z]){0}(?=$)",    # armL  (only after lower case)
    r"(?<=:){0}(?=_)",        # ns:L_arm
    r"(?<=^){0}(?=\d)",       # L1_arm
)

#: Default tolerance for the geometric check, in scene units. Scaled by how far
#: the control sits from the mirror plane, because a fingertip on a giant rig
#: legitimately sits further off-plane than a whole small prop.
ABSOLUTE_TOLERANCE = 0.01
RELATIVE_TOLERANCE = 1e-3


# --- naming -----------------------------------------------------------------


def _variants(token):
    """Case variants of a token: l, L, Left, LEFT, left."""
    return {token, token.upper(), token.capitalize()}


def candidate_names(node):
    """Every plausible counterpart NAME for `node`, best guess first.

    Purely textual. Nothing here is trusted -- see counterpart().
    """
    found = []
    for left, right in SIDE_TOKENS:
        for source, target in ((left, right), (right, left)):
            for source_variant in _variants(source):
                # Match the target's case to the source's, so L_arm -> R_arm and
                # left_arm -> right_arm rather than L_arm -> right_arm.
                if source_variant.isupper():
                    target_variant = target.upper()
                elif source_variant[0].isupper():
                    target_variant = target.capitalize()
                else:
                    target_variant = target
                for pattern in _DELIMITERS:
                    regex = pattern.format(re.escape(source_variant))
                    try:
                        candidate, count = re.subn(regex, target_variant, node)
                    except re.error:
                        continue
                    if count and candidate != node and candidate not in found:
                        found.append(candidate)
    return found


# --- the search pool --------------------------------------------------------


def namespace_of(node):
    return node.rsplit(":", 1)[0] if ":" in node else ""


@cache.cached
def root_of(node):
    """The topmost transform ancestor of `node`.

    Used as the rig's own frame for the mirror plane. Rig-agnostic: whatever the
    top of the hierarchy is, its YZ is the plane of symmetry.
    """
    try:
        full = cmds.ls(node, long=True)
        if not full:
            return node
        parts = [p for p in full[0].split("|") if p]
        return parts[0] if parts else node
    except Exception:
        return node


@cache.cached
def search_pool(node):
    """Transforms that could be `node`'s counterpart.

    Everything under the same root and in the same namespace. Scoped that way so
    that two characters in one shot, or a rig and its reference copy, cannot
    pair across to each other.
    """
    root = root_of(node)
    namespace = namespace_of(node)
    try:
        found = cmds.listRelatives(
            root, allDescendents=True, type="transform", fullPath=False
        ) or []
    except Exception:
        found = []
    found.append(root)

    pool = []
    for candidate in found:
        short = candidate.split("|")[-1]
        if namespace_of(short) == namespace:
            pool.append(short)
    return tuple(pool)


def with_settable_channels(nodes):
    """Filter to nodes Maya would let a pose be written to. NOT a control filter.

    THE NAME IS THE WARNING. This used to be called `controls_in` and it was
    used as "which of these are controls", on the theory that a rigger locks
    the channels of everything that is not one. Riggers do not. On an
    AdvancedSkeleton character 1521 of 1529 transforms under the root pass this
    test -- AlignIKToWrist_L, BendElbow1_L, twist chains, constraint targets,
    stabilisers -- against roughly 80 real controls. Verifying symmetry across
    all of them fails on a perfectly good rig and reports it in the language of
    the rig's internals: "280 pair(s) off, e.g. AlignIKToWrist_L/
    AlignIKToWrist_R" tells an animator nothing they can act on.

    Use animkit.tools.pose.controls_in to ask which nodes are controls. It asks
    the rig -- controller tags, control shapes, control sets -- and falls back
    to this only when the rig declares nothing at all.

    What this IS good for: rejecting nodes that could not receive a pose even
    if they were controls, which is the last-resort fallback above and nothing
    more.
    """
    return [n for n in nodes if xform.settable_transform_channels(n)]


# --- geometry ---------------------------------------------------------------


def _tolerance(distance):
    return max(ABSOLUTE_TOLERANCE, RELATIVE_TOLERANCE * abs(distance))


def rest_position(node):
    matrix = xform.rest_world_matrix(node)
    return [matrix[12], matrix[13], matrix[14]]


def rest_offset(node, candidate, reflection):
    """(distance, tolerance) for "is `candidate` where `node` reflected lands".

    ONE definition of that measurement, because three things ask it -- accepting
    a candidate, verifying an accepted pair, and reporting a rejected one -- and
    when they each had their own copy the numbers in the diagnostics did not
    match the numbers the decisions were made on.

    An unreadable rest matrix returns an infinite distance, so it fails every
    comparison rather than passing one by accident.
    """
    try:
        want = xform.reflect_point(rest_position(node), reflection)
        got = rest_position(candidate)
    except Exception:
        return float("inf"), ABSOLUTE_TOLERANCE

    span = max(abs(v) for v in want) if want else 0.0
    distance = max(abs(got[i] - want[i]) for i in range(3))
    return distance, _tolerance(span)


def is_geometric_counterpart(node, candidate, reflection):
    """True if `candidate` sits where `node` reflected would sit, at rest.

    Also requires the settable transform channel sets to match. A control
    paired with something that has different channels is not a counterpart,
    whatever its name says -- and it would silently drop half the pose.
    """
    distance, tolerance = rest_offset(node, candidate, reflection)
    if distance > tolerance:
        return False

    return (xform.settable_transform_channels(node)
            == xform.settable_transform_channels(candidate))


def is_self_mirroring(node, reflection):
    """A centre control: its rest position reflects onto itself.

    Spine, head, root. They still mirror -- their own side-to-side component
    flips -- but their counterpart is themselves.
    """
    return is_geometric_counterpart(node, node, reflection)


# --- the public entry point -------------------------------------------------


def reflection_for(node, root=None):
    """The reflection matrix for the rig `node` belongs to."""
    root = root or root_of(node)
    return xform.reflection_matrix(*xform.mirror_plane(root))


def counterpart(node, reflection=None, pool=None):
    """`node`'s opposite control, `node` itself if it is a centre control, or None.

    Naming first because it is fast and unambiguous when it works; geometry
    always, because it is the only thing that can be trusted.
    """
    reflection = reflection if reflection is not None else reflection_for(node)

    for candidate in candidate_names(node):
        if not cmds.objExists(candidate):
            continue
        if is_geometric_counterpart(node, candidate, reflection):
            return candidate

    if is_self_mirroring(node, reflection):
        return node

    # Naming found nothing usable. Pair on geometry alone -- this is the path
    # for a rig whose convention is not in SIDE_TOKENS at all.
    for candidate in (pool if pool is not None else search_pool(node)):
        if candidate == node:
            continue
        if is_geometric_counterpart(node, candidate, reflection):
            return candidate

    return None


def name_suspects(node, reflection=None):
    """Named candidates that LOOK right but fail the geometry check.

    The difference between two very different messages. A control with no
    candidate at all has a naming problem, or no counterpart in the rig. A
    control whose obvious counterpart exists, carries the same channels, and
    simply is not where the reflection says it should be, means the RIG is not
    symmetric at rest -- and telling that animator "no counterpart found" sends
    them hunting through naming conventions for a problem that is not there.
    """
    reflection = reflection if reflection is not None else reflection_for(node)
    suspects = []
    for candidate in candidate_names(node):
        if not cmds.objExists(candidate):
            continue
        if (xform.settable_transform_channels(node)
                != xform.settable_transform_channels(candidate)):
            continue
        if not is_geometric_counterpart(node, candidate, reflection):
            suspects.append(candidate)
    return suspects


def classify_unpaired(unpaired, reflection):
    """Split unpaired controls into (asymmetric, missing).

    asymmetric  a plausible counterpart exists but is in the wrong place
    missing     nothing that could be a counterpart was found at all
    """
    asymmetric = []
    missing = []
    for node in unpaired:
        suspects = name_suspects(node, reflection)
        if suspects:
            asymmetric.append((node, suspects[0]))
        else:
            missing.append(node)
    return asymmetric, missing


def pair_up(nodes, reflection=None):
    """Pair a list of controls. Returns (pairs, unpaired).

    pairs is [(node, counterpart), ...] with each unordered pair appearing once,
    plus centre controls as (node, node).

    A STAGE, NOT AN ANSWER. Its result is not verified and its `unpaired` is not
    classified, and a caller that acts on either without doing both has the bug
    described in `analyse`. Call `analyse` unless you are specifically testing
    this stage.
    """
    if not nodes:
        return [], []

    reflection = (
        reflection if reflection is not None else reflection_for(nodes[0])
    )

    pairs = []
    unpaired = []
    seen = set()

    for node in nodes:
        if node in seen:
            continue
        other = counterpart(node, reflection=reflection)
        if other is None:
            unpaired.append(node)
            seen.add(node)
            continue
        seen.add(node)
        seen.add(other)
        pairs.append((node, other))

    return pairs, unpaired


# --- the precondition -------------------------------------------------------


def check_symmetry(pairs, reflection):
    """Verify the rig is symmetric AT REST. Returns a list of failures.

    Each failure is (node, counterpart, distance). An empty list means every
    frame relation derived from these rest poses is trustworthy.

    This exists because a mirror on an asymmetric rest pose fails silently. It
    produces a pose, the silhouette reads, and one limb is wrong. Callers refuse
    on a non-empty result rather than proceeding -- see animkit.tools.pose.
    """
    failures = []
    for a, b in pairs:
        if a == b:
            continue
        distance, tolerance = rest_offset(a, b, reflection)
        if distance > tolerance:
            failures.append((a, b, distance))
    return failures


class Symmetry(object):
    """Whether a set of controls is symmetric at rest, and what is wrong if not.

    WHY ONE OBJECT AND NOT THREE CALLS
    ----------------------------------
    "A pair that fails check_symmetry" and "two controls that fail to pair at
    all" are the SAME CONDITION seen at two stages. A counterpart that is not
    where the reflection says it should be is rejected by
    is_geometric_counterpart before it ever becomes a pair -- so it never
    reaches check_symmetry, which then reports success on a rig it cannot
    mirror. Detecting it means running pair_up, then check_symmetry on what
    paired, then classify_unpaired on what did not, and refusing if EITHER
    fires.

    Three call sites did that by hand and every one of them, at some point, did
    only half of it. So there is one call now, `analyse`, and one thing to check:

        report = pairing.analyse(nodes)
        if not report.ok:
            refuse(report.describe())

    `broken` merges both stages into one list of (a, b, distance) -- the same
    measurement in both cases, via rest_offset -- worst offender first, because
    a truncated diagnostic should show the pair most worth looking at rather
    than whichever came first.

    `missing` is deliberately NOT part of `ok`. A control with no counterpart at
    all is usually a lone control or a naming convention nobody recognised, not
    a broken rig, and refusing to mirror an entire selection over it would be
    wrong. Callers warn about it and carry on.
    """

    def __init__(self, pairs, broken, missing, reflection):
        #: [(node, counterpart)] -- accepted pairs, centre controls as (n, n).
        self.pairs = pairs
        #: [(a, b, distance)] -- rest asymmetry, from EITHER stage.
        self.broken = broken
        #: [node] -- no plausible counterpart found at all.
        self.missing = missing
        self.reflection = reflection

    @property
    def ok(self):
        """True if every relation derived from these rest poses is trustworthy."""
        return not self.broken

    def __len__(self):
        return len(self.pairs)

    def describe(self, limit=5):
        """One human-readable line naming the pairs that are off, worst first."""
        return describe_failures(self.broken, limit=limit)

    def __repr__(self):
        return "<Symmetry {0} pair(s), {1} broken, {2} unmatched>".format(
            len(self.pairs), len(self.broken), len(self.missing)
        )


def analyse(nodes, reflection=None):
    """Pair `nodes` and verify the rig is symmetric at rest. Returns a Symmetry.

    The one call. See Symmetry for why it is one call and not three.
    """
    if not nodes:
        return Symmetry([], [], [], reflection)

    reflection = (
        reflection if reflection is not None else reflection_for(nodes[0])
    )

    pairs, unpaired = pair_up(nodes, reflection=reflection)
    broken = list(check_symmetry(pairs, reflection))
    seen = {frozenset((a, b)) for a, b, _d in broken}

    # The second stage of the same condition: a counterpart that LOOKS right by
    # name, carries the same channels, and is not where the reflection puts it.
    # It never became a pair, so check_symmetry above could not have seen it.
    asymmetric, missing = classify_unpaired(unpaired, reflection)
    for node, suspect in asymmetric:
        # ONCE PER PAIR. With both sides of a broken pair in the selection each
        # names the other as its suspect, so the naive merge reports
        # "L/R off by 5.000, R/L off by 5.000" -- the same fault, twice, in a
        # message whose whole job is to be short enough to read.
        key = frozenset((node, suspect))
        if key in seen:
            continue
        seen.add(key)
        distance, _tol = rest_offset(node, suspect, reflection)
        broken.append((node, suspect, distance))

    broken.sort(key=lambda row: row[2], reverse=True)
    return Symmetry(pairs, broken, missing, reflection)


def describe_pairs(pairs, limit=5):
    """One human-readable line for a list of (node, counterpart)."""
    if not pairs:
        return ""
    shown = ", ".join("{0}/{1}".format(a, b) for a, b in pairs[:limit])
    extra = "" if len(pairs) <= limit else " (+{0} more)".format(
        len(pairs) - limit
    )
    return shown + extra


def describe_failures(failures, limit=5):
    """One human-readable line for a check_symmetry result."""
    if not failures:
        return ""
    shown = ", ".join(
        "{0}/{1} off by {2:.3f}".format(a, b, d) for a, b, d in failures[:limit]
    )
    extra = "" if len(failures) <= limit else " (+{0} more)".format(
        len(failures) - limit
    )
    return shown + extra
