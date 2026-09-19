"""What operations exist, arranged for display. No Qt, and no Maya.

Separated for the same reason `radial_geom` is separated from `radial`: the
arrangement is the part with decisions in it, and a module that imports Qt
cannot be tested in the fast tier at all. Everything here takes registries as
ARGUMENTS rather than importing them, so a test hands it a fixture and the
callers hand it the real thing.

Two consumers, one arrangement:

  `ui.strip`    the horizontal bar docked by the time slider -- one row per
                group, in registry order
  `ui.help_ui`  the "what can this thing do" page -- the same rows, plus what
                each operation is for and which key it is on right now

Sharing the arrangement is the point. A help page that listed the operations in
a different order from the strip would be a worse help page, and keeping two
orderings in step by hand is exactly the drift house rule 5 exists to prevent.
"""

import collections

#: What to show when an operation has no hotkey. animkit binds none by default
#: (house rule 6), so this is the normal case and must not read as an error.
UNBOUND = "unbound"

#: One line of the help page.
Entry = collections.namedtuple(
    "Entry", ("label", "tooltip", "command", "binding", "destructive")
)


def rows(registries):
    """`((group, (operation, ...)), ...)` across every registry, in order.

    REGISTRY ORDER IS THE READING ORDER and must never be sorted.
    "Timing, Bake, Tangents, Cycle, Edit" is how an animator works through
    them; "Bake, Cycle, Edit, Tangents, Timing" is not, and alphabetising it
    would be a silent downgrade that no test would notice.

    Groups are merged by NAME across registries, first-seen wins the position.
    Two registries using one group name is not expected -- keys owns Timing and
    pose owns Pose -- but emitting the same heading twice would read as a bug
    in the panel rather than as the honest report of a naming collision it
    actually is.
    """
    order = []
    grouped = {}
    for operations in registries:
        for operation in operations:
            group = operation.group
            if group not in grouped:
                grouped[group] = []
                order.append(group)
            grouped[group].append(operation)
    return tuple((group, tuple(grouped[group])) for group in order)


def help_entries(registries, bindings=None):
    """The same rows, as `((group, (Entry, ...)), ...)`.

    `bindings` maps runTimeCommand name -> the key it is on. Passed in rather
    than looked up here, because asking Maya is the one part of this that needs
    Maya -- see `commands.current_bindings`. Anything missing from it reads as
    UNBOUND, which is the truthful answer for a tool that ships no hotkeys.
    """
    bindings = bindings or {}
    out = []
    for group, operations in rows(registries):
        entries = tuple(
            Entry(
                label=operation.label,
                tooltip=operation.tooltip,
                command=operation.name,
                binding=bindings.get(operation.name) or UNBOUND,
                destructive=bool(operation.destructive),
            )
            for operation in operations
        )
        out.append((group, entries))
    return tuple(out)


def counts(registries):
    """`(groups, operations)`, for the help page's one line of summary."""
    arranged = rows(registries)
    return len(arranged), sum(len(ops) for _group, ops in arranged)


def bound_count(entries_by_group):
    """How many operations in a help_entries() result carry a hotkey."""
    return sum(
        1
        for _group, entries in entries_by_group
        for entry in entries
        if entry.binding != UNBOUND
    )
