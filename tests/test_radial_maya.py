"""The radial's Maya-facing half: menu contents, press/release, binding.

The overlay itself is not exercised here. Building a QWidget needs a
QApplication, mayapy has none, and a frameless always-on-top window driven by
a held key is not a thing a test harness can drive anyway. That is exactly why
the two parts that CAN be wrong were pulled out of the widget:

    radial_geom.wedge_at   which wedge the cursor is over -- fast tier
    radial.invoke_item     what happens when a wedge fires -- here

What is left in the widget is painting and timers.
"""

import pytest

from conftest import requires_maya  # noqa: F401

pytestmark = requires_maya


class TestMenusCannotRot:
    """MENUS names commands by string, so something has to check the strings.

    A curated list is the right call -- keys.OPERATIONS has sixteen entries and
    a radial of sixteen is unusable -- but a hand-written list of names is a
    list that goes stale the first time an operation is renamed.
    """

    def test_every_named_command_resolves(self):
        from animkit.ui import radial

        known = radial._operations_by_name()
        for menu, names in radial.MENUS.items():
            for name in names:
                assert name in known, (
                    "radial menu %r names %r, which is in no registry"
                    % (menu, name)
                )

    def test_no_menu_is_wider_than_a_radial_can_be(self):
        from animkit.ui import radial

        for menu, names in radial.MENUS.items():
            assert len(names) <= radial.MAX_WEDGES, menu

    def test_every_menu_has_a_press_command(self):
        from animkit import commands
        from animkit.ui import radial

        registered = {name for name, _a, _c in commands.COMMANDS}
        for menu in list(radial.MENUS) + ["sets"]:
            assert menu in radial._MENU_COMMANDS
            assert radial._MENU_COMMANDS[menu] in registered

    def test_the_release_command_is_registered(self):
        from animkit import commands
        from animkit.ui import radial

        registered = {name for name, _a, _c in commands.COMMANDS}
        assert radial.RELEASE_COMMAND in registered

    def test_registered_radial_commands_compile(self):
        from animkit import commands

        for name, _annotation, command in commands.COMMANDS:
            if name.startswith("animkitRadial"):
                compile(command, "<cmd>", "exec")


class TestItemsFor:
    def test_pose_menu_has_a_wedge_per_name(self):
        from animkit.ui import radial

        items = radial.items_for("pose")
        assert len(items) == len(radial.MENUS["pose"])
        assert all(item.label for item in items)

    def test_a_destructive_operation_stays_marked(self):
        """The radial paints it differently, so the flag has to survive."""
        from animkit.ui import radial

        items = radial.items_for("keys")
        assert any(item.destructive for item in items)

    def test_an_unknown_menu_yields_nothing(self, clean_scene):
        from animkit.ui import radial

        assert radial.items_for("no such menu") == []

    def test_a_menu_naming_a_dead_command_drops_only_that_wedge(
        self, monkeypatch
    ):
        """Seven working commands beat a radial that refuses to open."""
        from animkit.ui import radial

        broken = ("animkitPoseCopy", "animkitPoseNoSuchThing",
                  "animkitPosePaste")
        monkeypatch.setitem(radial.MENUS, "broken", broken)

        items = radial.items_for("broken")
        assert len(items) == 2


class TestSetsMenu:
    """The reason the radial was worth building: sets at a flick."""

    @staticmethod
    def _character(cmds):
        root = cmds.createNode("transform", name="rig_root")
        controls = []
        for side in ("L", "R"):
            ctrl = cmds.createNode(
                "transform", name=side + "_hand_ctrl", parent=root
            )
            cmds.createNode("nurbsCurve", parent=ctrl)
            controls.append(ctrl)
        return root, controls

    def test_built_from_the_scene(self, clean_scene):
        from animkit.tools import sets
        from animkit.ui import radial

        cmds = clean_scene
        _root, controls = self._character(cmds)
        cmds.select(controls[0])
        sets.store("left")
        cmds.select(controls[1])
        sets.store("right")

        cmds.select(controls[0])
        labels = [item.label for item in radial.items_for("sets")]
        assert labels == ["left", "right"]

    def test_firing_a_set_wedge_selects_it(self, clean_scene):
        from animkit.tools import sets
        from animkit.ui import radial

        cmds = clean_scene
        _root, controls = self._character(cmds)
        cmds.select(controls[1])
        sets.store("right")

        cmds.select(controls[0])
        items = radial.items_for("sets")
        assert radial.invoke_item(items, 0) == "right"
        assert cmds.ls(selection=True) == [controls[1]]

    def test_an_empty_scene_shows_nothing_rather_than_an_empty_wheel(
        self, clean_scene
    ):
        from animkit.ui import radial

        clean_scene.select(clear=True)
        assert radial.items_for("sets") == []
        assert radial.show("sets") is None


class TestInvokeItem:
    """What happens on release. The one decision the widget delegates."""

    @staticmethod
    def _items(log):
        from animkit.ui import radial

        return [
            radial.Item("one", "", lambda: log.append("one")),
            radial.Item("two", "", lambda: log.append("two")),
        ]

    def test_fires_the_wedge(self):
        from animkit.ui import radial

        log = []
        assert radial.invoke_item(self._items(log), 1) == "two"
        assert log == ["two"]

    def test_the_dead_zone_fires_nothing(self):
        """Releasing without moving must be expressible, and must do NOTHING.

        The pose radial carries Reset. "I changed my mind" firing a command is
        not a cosmetic bug.
        """
        from animkit.ui import radial

        log = []
        assert radial.invoke_item(self._items(log), None) is None
        assert log == []

    def test_an_index_past_the_end_fires_nothing(self):
        from animkit.ui import radial

        log = []
        assert radial.invoke_item(self._items(log), 7) is None
        assert radial.invoke_item(self._items(log), -1) is None
        assert log == []

    def test_a_failing_item_does_not_escape(self, clean_scene):
        """This runs inside a hotkey release handler. A traceback escaping it
        leaves the overlay on screen, over the viewport."""
        from animkit.ui import radial

        def boom():
            raise RuntimeError("no")

        items = [radial.Item("boom", "", boom)]
        assert radial.invoke_item(items, 0) == "boom"


class TestPressAndRelease:
    """Two separate runTimeCommand invocations with nothing between them but
    module state, which is the shape Maya's hotkey system imposes."""

    def test_release_with_nothing_open_is_harmless(self, clean_scene):
        """It WILL be pressed with nothing open -- bound on its own by
        mistake, or pressed while a dialog has focus."""
        from animkit.ui import radial

        radial._active = None
        assert radial.release() is None
        assert not radial.is_open()

    def test_show_with_nothing_to_show_opens_nothing(self, clean_scene):
        from animkit.ui import radial

        radial._active = None
        assert radial.show("no such menu") is None
        assert not radial.is_open()

    def test_a_gui_application_is_not_a_widget_application(self):
        """The check that the obvious version of this guard gets wrong.

        mayapy running maya.standalone has a QGuiApplication, so
        `QApplication.instance()` returns something truthy and an
        `is not None` guard sails straight through into a QWidget constructor
        that cannot work. Only a real QApplication can host widgets.
        """
        from animkit.ui import radial
        from animkit.vendor import qt

        instance = qt.QtWidgets.QApplication.instance()
        assert instance is not None, (
            "this interpreter has no Qt application at all, so the trap this "
            "guards against is not present and the test below proves nothing"
        )
        assert not isinstance(instance, qt.QtWidgets.QApplication)
        assert radial._can_build_widgets() is False

    def test_show_without_a_widget_application_opens_nothing(self, clean_scene):
        """mayapy, batch renders, farm jobs.

        Only meaningful BECAUSE it runs under mayapy, where the interpreter
        genuinely cannot build a widget -- see the test above.
        """
        from animkit.ui import radial

        radial._active = None
        assert radial.show("pose") is None
        assert not radial.is_open()

    def test_cancel_is_safe_when_nothing_is_open(self, clean_scene):
        from animkit.ui import radial

        radial._active = None
        assert radial.cancel() is False

    def test_auto_repeat_does_not_stack_overlays(self, clean_scene):
        """Windows fires the press command over and over while a key is held.

        A second overlay per repeat would leave a stack of them behind, since
        only the last one ever gets a release.
        """
        from animkit.ui import radial

        class FakeRadial(object):
            fired = False

            def fire(self):
                FakeRadial.fired = True
                return "one"

        radial._active = FakeRadial()
        try:
            assert radial.show("pose") is radial._active
        finally:
            radial._active = None

    def test_release_clears_the_active_menu_even_if_firing_fails(
        self, clean_scene
    ):
        """Otherwise the next press is swallowed by the auto-repeat guard and
        the radial never opens again for the rest of the session."""
        from animkit.ui import radial

        class Exploding(object):
            def fire(self):
                raise RuntimeError("no")

            def dismiss(self):
                pass

        radial._active = Exploding()
        assert radial.release() is None
        assert radial._active is None


class TestBinding:
    """animkit binds no hotkeys on its own. install_hotkey is opt-in, and it
    must not take a key an animator has had bound for ten years."""

    def test_an_unknown_menu_is_refused(self, clean_scene):
        from animkit.ui import radial

        assert radial.install_hotkey("c", "no such menu", alt=True) is False

    def test_binding_without_a_ui_degrades_rather_than_raising(self):
        """mayapy, batch renders and farm jobs have no hotkey system at all.

        cmds.hotkeySet raises there, and a tool that lets that escape turns a
        headless job into a failed one over a keyboard shortcut nobody in that
        session could have pressed.
        """
        from animkit.ui import radial

        assert radial.install_hotkey("c", "pose", alt=True) is False

    def test_describe_reads_like_a_hotkey(self):
        from animkit.ui import radial

        assert radial._describe("c", True, False, False) == "Alt+C"
        assert radial._describe("v", True, True, True) == "Ctrl+Alt+Shift+V"

    def test_every_menu_command_names_a_real_menu(self):
        from animkit.ui import radial

        for menu in radial._MENU_COMMANDS:
            assert menu == "sets" or menu in radial.MENUS
