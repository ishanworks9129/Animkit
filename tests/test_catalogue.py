"""How the strip and the help page arrange the registries. Plain CPython.

`animkit.ui.catalogue` imports neither Qt nor Maya, which is the entire reason
it exists as its own module -- the strip and the help page cannot be tested at
all (Qt widgets will not construct under mayapy), so the decisions worth
testing were moved somewhere they could be.

The fakes here are deliberately not `registry.Operation`: importing that drags
in maya.cmds through tools.registry's neighbours and would push this file into
the Maya tier for no gain. Anything with the right attributes will do, which is
also a statement about how little catalogue actually requires.
"""

import collections
import io

import pytest

from animkit.ui import catalogue

Fake = collections.namedtuple(
    "Fake", ("name", "label", "tooltip", "group", "destructive")
)


def op(name, group, label=None, tooltip="does a thing", destructive=False):
    return Fake(name, label or name, tooltip, group, destructive)


class TestRows:
    def test_preserves_registry_order_and_does_not_sort(self):
        """The ordering assertion the whole module exists to protect.

        Alphabetising would put Bake before Timing and Cycle before Tangents,
        which is not how anyone works through them -- and it is the kind of
        change that no other test would notice.
        """
        registry = (
            op("a", "Timing"), op("b", "Timing"),
            op("c", "Bake"),
            op("d", "Tangents"),
        )
        assert [group for group, _ops in catalogue.rows([registry])] == [
            "Timing", "Bake", "Tangents"
        ]

    def test_keeps_operations_within_their_group(self):
        registry = (op("a", "Timing"), op("b", "Bake"), op("c", "Timing"))
        found = dict(catalogue.rows([registry]))
        assert [o.name for o in found["Timing"]] == ["a", "c"]
        assert [o.name for o in found["Bake"]] == ["b"]

    def test_walks_registries_in_the_order_given(self):
        keys = (op("k", "Timing"),)
        pose = (op("p", "Pose"),)
        assert [g for g, _ in catalogue.rows([keys, pose])] == ["Timing", "Pose"]
        assert [g for g, _ in catalogue.rows([pose, keys])] == ["Pose", "Timing"]

    def test_a_group_name_shared_across_registries_is_merged(self):
        """One heading, not two. A repeated heading reads as a bug in the
        panel rather than as the naming collision it actually is."""
        first = (op("a", "Pose"),)
        second = (op("b", "Pose"),)
        found = catalogue.rows([first, second])
        assert len(found) == 1
        assert [o.name for o in found[0][1]] == ["a", "b"]

    def test_empty_input_is_empty_output_not_an_error(self):
        assert catalogue.rows([]) == ()
        assert catalogue.rows([()]) == ()


class TestHelpEntries:
    def test_carries_the_command_name_through(self):
        """The runTimeCommand name is the actionable half of the page -- it is
        what gets pasted into the Hotkey Editor."""
        registry = (op("animkitKeysOffsetBack", "Timing", label="-1"),)
        (_group, entries), = catalogue.help_entries([registry])
        assert entries[0].command == "animkitKeysOffsetBack"
        assert entries[0].label == "-1"

    def test_unbound_is_the_default_and_is_not_blank(self):
        """A fresh install has no hotkeys at all, so this is the normal case
        and it has to read as a state rather than as missing data."""
        registry = (op("a", "Timing"),)
        (_group, entries), = catalogue.help_entries([registry])
        assert entries[0].binding == catalogue.UNBOUND
        assert entries[0].binding

    def test_a_binding_is_shown_when_there_is_one(self):
        registry = (op("a", "Timing"),)
        (_group, entries), = catalogue.help_entries(
            [registry], {"a": "Alt+K"}
        )
        assert entries[0].binding == "Alt+K"

    def test_bindings_for_other_commands_are_ignored(self):
        registry = (op("a", "Timing"),)
        (_group, entries), = catalogue.help_entries(
            [registry], {"somethingElse": "Alt+K"}
        )
        assert entries[0].binding == catalogue.UNBOUND

    def test_destructive_survives_the_trip(self):
        """The page colours these, so losing the flag would silently make
        Delete look like every other operation."""
        registry = (op("a", "Edit", destructive=True), op("b", "Edit"))
        (_group, entries), = catalogue.help_entries([registry])
        assert [e.destructive for e in entries] == [True, False]

    def test_order_matches_rows_exactly(self):
        """The help page and the strip must not disagree about order, or the
        page sends somebody looking for a button in the wrong place."""
        registry = (
            op("a", "Timing"), op("b", "Bake"), op("c", "Timing"),
        )
        rows = [(g, [o.name for o in ops])
                for g, ops in catalogue.rows([registry])]
        entries = [(g, [e.command for e in es])
                   for g, es in catalogue.help_entries([registry])]
        assert rows == entries


class TestCounts:
    def test_counts_groups_and_operations(self):
        registry = (op("a", "Timing"), op("b", "Timing"), op("c", "Bake"))
        assert catalogue.counts([registry]) == (2, 3)

    def test_bound_count_is_zero_on_a_fresh_install(self):
        registry = (op("a", "Timing"), op("b", "Bake"))
        assert catalogue.bound_count(catalogue.help_entries([registry])) == 0

    def test_bound_count_counts_only_bound_ones(self):
        registry = (op("a", "Timing"), op("b", "Bake"), op("c", "Bake"))
        arranged = catalogue.help_entries([registry], {"a": "K", "c": "Alt+J"})
        assert catalogue.bound_count(arranged) == 2


class TestNoHeavyImports:
    def test_catalogue_imports_neither_qt_nor_maya(self):
        """The property that keeps this file in the fast tier.

        Asserted rather than assumed: a `from animkit.ui import style` added
        for one colour would drag Qt in, and this whole file would start being
        skipped -- silently -- leaving the arrangement untested.

        READ FROM THE SOURCE, NOT FROM sys.modules. The obvious version of this
        test clears the animkit modules and then asserts nothing Qt-shaped got
        loaded, and it passes in the fast tier and fails under `-Maya`: the
        full suite shares one interpreter, so maya and PySide are already
        imported by the time this runs and the assertion is measuring the load
        order of other test files. Parsing the imports says what the module
        actually asks for, whatever else happens to be in memory.
        """
        import ast

        from animkit.ui import catalogue as module

        tree = ast.parse(io.open(module.__file__, encoding="utf-8").read())

        asked_for = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    asked_for.add(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                asked_for.add(node.module)

        heavy = sorted(
            name for name in asked_for
            if name.split(".")[0] in ("maya", "PySide2", "PySide6")
            or name.startswith("animkit.ui")
            or name.startswith("animkit.vendor")
        )
        assert heavy == [], "catalogue reached for %s" % (heavy,)
