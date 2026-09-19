"""The strip and the help page, as far as they can be tested. Needs mayapy.

WHAT THIS FILE CANNOT DO, and why it still earns its place.

Qt widgets do not construct under mayapy at all -- `maya.standalone` leaves a
`QGuiApplication` behind, so even `QApplication.instance() is not None` passes
while the constructor still fails; `tests/conftest.py` explains why that is
deliberately not worked around. So nothing here builds a Strip or a HelpPage,
and neither does anything else: the docking, the layout and the look are the
GUI gate, and a human runs that.

What is left is still worth pinning, because it is where the silent breakage
lives:

  * the modules IMPORT -- a syntax error or a bad import in a uiScript module
    surfaces at Maya startup, inside a restored dockable panel, as an empty bar
  * their build functions EXIST under the names the uiScript strings name; a
    renamed function is a string that no longer resolves, and nothing catches
    that until a panel comes back blank after a restart
  * the commands are REGISTERED, so the strip and the help page are reachable
    from the Hotkey Editor like everything else
  * `catalogue` arranges the REAL registries, not just the fixtures the fast
    tier hands it
"""

import pytest

from conftest import requires_maya  # noqa: F401

pytestmark = requires_maya


class TestModulesLoad:
    """A uiScript that cannot import is an empty panel at startup."""

    def test_strip_imports(self):
        from animkit.ui import strip  # noqa: F401

    def test_help_imports(self):
        from animkit.ui import help_ui  # noqa: F401

    @pytest.mark.parametrize("module_name,function", [
        ("animkit.ui.strip", "build"),
        ("animkit.ui.help_ui", "build"),
        ("animkit.ui.panel", "build"),
    ])
    def test_the_build_function_named_by_the_uiscript_exists(
            self, module_name, function):
        """The uiScript is a STRING. Renaming build() breaks it silently."""
        import importlib

        module = importlib.import_module(module_name)
        assert callable(getattr(module, function, None))

    @pytest.mark.parametrize("module_name", [
        "animkit.ui.strip", "animkit.ui.help_ui",
    ])
    def test_build_code_names_this_module_and_its_build(self, module_name):
        """The one-liner has to actually resolve. Compile it and check it
        points where it says -- a hardcoded module path is a string that can
        disagree with the module it lives in."""
        import importlib

        module = importlib.import_module(module_name)
        code = module.BUILD_CODE
        compile(code, "<uiScript>", "exec")
        assert module_name in code
        assert "build()" in code


class TestCommandsAreRegistered:
    @pytest.mark.parametrize("name", [
        "animkitStripShow", "animkitHelpShow",
    ])
    def test_command_exists(self, name):
        from animkit import commands

        registered = {n for n, _a, _c in commands.COMMANDS}
        assert name in registered

    @pytest.mark.parametrize("name", [
        "animkitStripShow", "animkitHelpShow",
    ])
    def test_command_body_compiles(self, name):
        from animkit import commands

        for command_name, _annotation, body in commands.COMMANDS:
            if command_name == name:
                compile(body, "<runTimeCommand>", "exec")
                return
        pytest.fail("%s not found" % name)


class TestCatalogueOverTheRealRegistries:
    """The fast tier proves the arrangement; this proves it survives contact."""

    @staticmethod
    def _registries():
        from animkit.tools import keys, pose, reference, sets

        return (keys.OPERATIONS, pose.OPERATIONS, sets.OPERATIONS,
                reference.OPERATIONS)

    def test_every_operation_lands_in_exactly_one_row(self):
        from animkit.ui import catalogue

        registries = self._registries()
        total = sum(len(r) for r in registries)
        arranged = catalogue.rows(registries)
        placed = sum(len(ops) for _group, ops in arranged)
        assert placed == total

    def test_no_group_name_appears_twice(self):
        """A repeated heading reads as a bug in the strip."""
        from animkit.ui import catalogue

        names = [group for group, _ops in catalogue.rows(self._registries())]
        assert len(names) == len(set(names))

    def test_the_keys_groups_come_out_in_working_order(self):
        from animkit.ui import catalogue

        from animkit.tools import keys

        names = [g for g, _ in catalogue.rows([keys.OPERATIONS])]
        assert names == ["Timing", "Bake", "Tangents", "Cycle", "Edit"]

    def test_help_entries_cover_every_operation(self):
        from animkit.ui import catalogue

        registries = self._registries()
        total = sum(len(r) for r in registries)
        arranged = catalogue.help_entries(registries)
        assert sum(len(e) for _g, e in arranged) == total

    def test_every_help_entry_names_a_real_runtimecommand(self):
        """The third column is what an animator pastes into the Hotkey
        Editor. A name that is not registered sends them looking for nothing."""
        from animkit import commands
        from animkit.ui import catalogue

        registered = {n for n, _a, _c in commands.COMMANDS}
        arranged = catalogue.help_entries(self._registries())
        missing = [
            entry.command
            for _group, entries in arranged
            for entry in entries
            if entry.command not in registered
        ]
        assert missing == []

    def test_every_help_entry_has_something_to_say(self):
        """A blank tooltip makes a blank help row. The registries already
        require a tooltip; this is what notices when one arrives empty."""
        from animkit.ui import catalogue

        arranged = catalogue.help_entries(self._registries())
        empty = [
            entry.command
            for _group, entries in arranged
            for entry in entries
            if not (entry.tooltip or "").strip()
        ]
        assert empty == []


class TestBindingsAreBestEffort:
    def test_current_bindings_returns_a_dict_headlessly(self):
        """Headless there is no hotkey system at all -- assignCommand returns
        None. That has to read as "nothing is bound", not as an exception, or
        the help page cannot open under mayapy or in a fresh Maya."""
        from animkit import commands

        found = commands.current_bindings()
        assert isinstance(found, dict)

    @pytest.mark.parametrize("keys,expected", [
        (None, None),
        ([], None),
        (["NONE", "0", "0"], None),
        (["k", "0", "0"], "k"),
        (["k", "1", "0"], "Alt+k"),
        (["k", "0", "1"], "Ctrl+k"),
        (["k", "1", "1"], "Ctrl+Alt+k"),
        (["k", 1, 0], "Alt+k"),
        (["k"], "k"),
    ])
    def test_key_label_tolerates_what_maya_returns(self, keys, expected):
        """The shape of keyString varies by version and cannot be checked
        without a UI, so the parser is written to tolerate rather than assume.
        These pin the tolerating."""
        from animkit import commands

        assert commands._key_label(keys) == expected


class TestDockTargets:
    """Where the strip asks to be put, and how that list is read.

    The docking itself is the GUI gate -- nothing here can dock anything. What
    it can check is the decision made before Maya is asked, which is where the
    bug actually was: `dockToControl` was being applied as an EDIT after the
    control had been created floating, and Maya ignores that without
    complaining, so the log said "docked" while the bar sat in the middle of
    the screen.
    """

    def test_a_single_pair_is_accepted(self):
        from animkit.ui import mayawin

        assert mayawin.dock_candidates(("TimeSlider", "top")) == [
            ("TimeSlider", "top")
        ]

    def test_a_list_of_pairs_is_accepted(self):
        from animkit.ui import mayawin

        assert mayawin.dock_candidates(
            [("graphEditor1Window", "top"), ("TimeSlider", "top")]
        ) == [("graphEditor1Window", "top"), ("TimeSlider", "top")]

    def test_nothing_is_no_candidates_not_an_error(self):
        from animkit.ui import mayawin

        assert mayawin.dock_candidates(None) == []
        assert mayawin.dock_candidates(()) == []

    def test_order_is_preserved(self):
        """First existing target wins, so the order IS the preference."""
        from animkit.ui import mayawin

        pairs = [("a", "top"), ("b", "bottom"), ("c", "left")]
        assert mayawin.dock_candidates(pairs) == [tuple(p) for p in pairs]

    def test_unknown_targets_resolve_to_nothing(self):
        """Headless there are no workspaceControls at all, so this also pins
        the fresh-Maya case: nothing to dock to means float, not raise."""
        from animkit.ui import mayawin

        assert mayawin.first_dockable(
            [("animkitNoSuchControl", "top")]
        ) is None

    def test_can_dock_to_is_false_and_does_not_raise_headlessly(self):
        from animkit.ui import mayawin

        assert mayawin.can_dock_to("animkitNoSuchControl") is False

    def test_the_strip_docks_against_the_time_slider_first(self):
        """The whole reason the strip exists is that the time slider is what
        an animator watches while they work, so the bar has to be AGAINST it.

        This led with ("MainPane", "bottom") once, which docks under the
        viewport and therefore above everything stacked at the bottom -- with
        a Graph Editor open, or merely collapsed to its title bar, the strip
        sat above that with the time slider somewhere further down. Above the
        timeline in a technical sense, and useless in the sense that matters.
        """
        from animkit.ui import strip

        assert strip.DOCK_TARGETS[0] == ("TimeSlider", "top")

    def test_the_strip_names_a_fallback_that_always_exists(self):
        """graphEditor1Window does not exist until the Graph Editor has been
        opened once, so a list naming only it would float on a fresh Maya and
        dock on a used one. At least one candidate has to be one that is
        always there."""
        from animkit.ui import strip

        names = [name for name, _side in strip.DOCK_TARGETS]
        assert len(names) >= 2
        assert "TimeSlider" in names

    def test_every_dock_target_is_a_pair_of_strings(self):
        from animkit.ui import strip

        for target in strip.DOCK_TARGETS:
            assert len(target) == 2
            assert all(isinstance(part, str) for part in target)


class TestHeight:
    """A one-row bar has to ask Maya for a one-row control.

    Docked above the time slider, Maya hands a new control a share of the
    space its neighbour had -- which made a row of 40-pixel buttons open
    inside a 300-pixel slab of empty grey. Nothing here can see a pixel; what
    it can see is the request, which is where the omission was.
    """

    def test_the_strip_asks_for_a_one_row_height(self):
        from animkit.ui import strip, style

        assert strip.INITIAL_HEIGHT > 0
        # Tied to the button metric, not typed as a number, so it follows if
        # the buttons ever change size.
        assert strip.INITIAL_HEIGHT > style.ICON_BUTTON
        assert strip.INITIAL_HEIGHT < style.ICON_BUTTON * 3

    @staticmethod
    def _recorder(monkeypatch, reject_height=False, dockable="TimeSlider"):
        """Stand in for cmds.workspaceControl and record the CREATE calls.

        It has to answer the exists-query falsely, or show_workspace_control
        takes its "already there, just restore it" branch and never creates
        anything -- which is what the first version of these tests did, and
        they passed while proving nothing.
        """
        from animkit.ui import mayawin

        calls = []

        def fake(name, **kwargs):
            if kwargs.get("q") or kwargs.get("query"):
                return False
            if kwargs.get("e") or kwargs.get("edit"):
                return name
            calls.append(dict(kwargs))
            if reject_height and "heightProperty" in kwargs:
                raise RuntimeError("Invalid flag: heightProperty")
            return name

        monkeypatch.setattr(mayawin.cmds, "workspaceControl", fake)
        monkeypatch.setattr(mayawin, "is_built", lambda name: True)
        monkeypatch.setattr(mayawin, "first_dockable", lambda dock_to: dockable)
        return calls

    def test_height_reaches_maya_as_a_fixed_property(self, monkeypatch):
        """initialHeight alone only says where the stretching starts from.
        heightProperty is the flag that stops it stretching."""
        from animkit.ui import mayawin

        calls = self._recorder(monkeypatch)
        mayawin.show_workspace_control(
            "animkitHeightProbe", "animkit", "pass", width=900, height=46,
            dock_to=(("TimeSlider", "top"),))

        assert len(calls) == 1
        assert calls[0].get("initialHeight") == 46
        assert calls[0].get("heightProperty") == "fixed"
        assert calls[0].get("dockToControl") == "TimeSlider"

    def test_no_height_flags_when_no_height_is_asked_for(self, monkeypatch):
        from animkit.ui import mayawin

        calls = self._recorder(monkeypatch, dockable=None)
        mayawin.show_workspace_control("animkitHeightProbe", "animkit", "pass")

        assert len(calls) == 1
        assert "heightProperty" not in calls[0]
        assert "initialHeight" not in calls[0]

    def test_a_maya_that_rejects_the_height_flags_keeps_its_dock(self, monkeypatch):
        """Losing the height costs a badly-sized panel. Losing the dock costs
        a floating one. The retry must give up the cheaper thing first."""
        from animkit.ui import mayawin

        calls = self._recorder(monkeypatch, reject_height=True)
        mayawin.show_workspace_control(
            "animkitHeightProbe", "animkit", "pass", height=46,
            dock_to=(("TimeSlider", "top"),))

        assert len(calls) == 2, "should retry once, not fall straight to floating"
        assert calls[-1].get("dockToControl") == "TimeSlider", "the dock survived"
        assert "floating" not in calls[-1]
        assert "heightProperty" not in calls[-1]
