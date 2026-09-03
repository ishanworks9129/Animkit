"""One Operation class, shared by every tool registry.

An Operation is the single source of truth for a user-facing command: the
button panel reads it, `animkit.commands` turns it into a runTimeCommand, and
the parametrised test harness covers it. Adding one to a registry adds it to
all three without touching any of them.

There were two copies of this class -- one in tools.keys, one in tools.pose --
and they had already drifted: pose's could not carry kwargs, and each hardcoded
its own module path into the generated command string. A third copy for
selection sets would have made the drift permanent, so there is one now.

The command string derives its module from `fn.__module__` rather than being
told. A hardcoded path is a string that can disagree with the function it
claims to call, and the failure shows up as a broken hotkey in someone else's
Maya rather than as anything visible here.
"""


class Operation(object):
    """One user-facing operation.

    name         the runTimeCommand name, unique across every registry
    label        the button text, short enough for a quarter-panel button
    tooltip      one sentence; the panel appends the runTimeCommand name
    fn           the function to call
    kwargs       fixed arguments, baked into the generated command string
    group        panel grouping, in registry order
    destructive  the panel colours it and the radial keeps it off the top wedge
    """

    def __init__(self, name, label, tooltip, fn, kwargs=None, group="",
                 destructive=False):
        self.name = name
        self.label = label
        self.tooltip = tooltip
        self.fn = fn
        self.kwargs = kwargs or {}
        self.group = group
        self.destructive = destructive

    def invoke(self, **overrides):
        call = dict(self.kwargs)
        call.update(overrides)
        return self.fn(**call)

    @property
    def command(self):
        """The Python one-liner registered as a runTimeCommand."""
        module = self.fn.__module__
        alias = module.rsplit(".", 1)[-1][0]
        args = ", ".join(
            "{0}={1!r}".format(key, value)
            for key, value in sorted(self.kwargs.items())
        )
        return "import {0} as {1}; {1}.{2}({3})".format(
            module, alias, self.fn.__name__, args
        )

    def __repr__(self):
        return "<Operation {0}>".format(self.name)


def ordered_groups(operations):
    """Group names in first-seen registry order.

    Registry order is the intended panel order, so it must not be sorted
    alphabetically -- "Timing, Tangents, Cycle, Edit" is how an animator works
    through them; "Cycle, Edit, Tangents, Timing" is not.
    """
    seen = []
    for op in operations:
        if op.group not in seen:
            seen.append(op.group)
    return tuple(seen)
