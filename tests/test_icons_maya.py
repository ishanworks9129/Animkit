"""The icon set and the style layer.

Testable at all only because icons render into a **QImage**, which needs no
QGuiApplication, rather than a QPixmap, which does. That one choice is what
lets a headless suite assert that every operation has an icon and that no two
of them are the same picture.

What is NOT asserted here is whether an icon is any *good*. Nothing automated
can tell you that Mirror and Flip read as the same shape at 16px --
`icons.contact_sheet()` renders them side by side for a human to look at, and
that is how the four tangent icons got redrawn.
"""

import pytest

from conftest import requires_maya  # noqa: F401

pytestmark = requires_maya


class TestEveryOperationHasAnIcon:
    """The registries are the source of truth; the icon set has to keep up.

    Adding an operation and forgetting its icon is silent -- the button falls
    back to its text label and looks merely inconsistent, which nobody reports.
    """

    def test_every_keyframe_operation(self):
        from animkit.tools import keys
        from animkit.ui import icons

        missing = [op.name for op in keys.OPERATIONS
                   if not icons.has_icon(op.name)]
        assert missing == []

    def test_every_pose_operation(self):
        from animkit.tools import pose
        from animkit.ui import icons

        missing = [op.name for op in pose.OPERATIONS
                   if not icons.has_icon(op.name)]
        assert missing == []

    def test_no_icon_is_defined_for_a_command_that_does_not_exist(self):
        """The other direction: an icon left behind by a renamed operation.

        Harmless, but it is dead code that looks like coverage.
        """
        from animkit import commands
        from animkit.ui import icons

        registered = {name for name, _a, _c in commands.COMMANDS}
        # These four are panel furniture rather than runTimeCommands.
        furniture = {"animkitSetRecall", "animkitSetDelete", "animkitRefresh"}
        orphans = [
            name for name in icons.PAINTERS
            if name not in registered and name not in furniture
        ]
        assert orphans == []


class TestRendering:
    def test_every_icon_renders_without_raising(self):
        from animkit.ui import icons

        for name in icons.PAINTERS:
            image = icons.image(name, size=64)
            assert not image.isNull(), name

    def test_an_unknown_name_gives_a_blank_image_not_an_error(self):
        """Called from a button constructor, so it must not raise."""
        from animkit.ui import icons

        image = icons.image("animkitNoSuchThing", size=32)
        assert not image.isNull()

    def test_icons_actually_draw_something(self):
        """A painter that silently draws nothing is the failure mode a
        `renders without raising` test sails straight past."""
        from animkit.ui import icons

        for name in icons.PAINTERS:
            image = icons.image(name, size=64)
            painted = sum(
                1
                for y in range(0, 64, 2)
                for x in range(0, 64, 2)
                if image.pixelColor(x, y).alpha() > 0
            )
            assert painted > 8, "%s drew almost nothing" % name

    def test_icons_stay_inside_their_box(self):
        """A stroke running off the edge is clipped, and the icon reads as cut
        off rather than as wrong -- so it survives a glance at the panel."""
        from animkit.ui import icons

        size = 64
        for name in icons.PAINTERS:
            image = icons.image(name, size=size)
            edge = []
            for i in range(size):
                edge.append(image.pixelColor(i, 0).alpha())
                edge.append(image.pixelColor(i, size - 1).alpha())
                edge.append(image.pixelColor(0, i).alpha())
                edge.append(image.pixelColor(size - 1, i).alpha())
            assert max(edge) == 0, "%s touches the edge of its box" % name

    def test_scales_to_any_size(self):
        from animkit.ui import icons

        for size in (16, 24, 32, 64, 128):
            image = icons.image("animkitPoseMirror", size=size)
            assert image.width() == size
            assert image.height() == size


class TestIconsAreDistinguishable:
    """The collision the contact sheet found, pinned so it cannot come back.

    Cycle, Reset and Refresh were three circular arrows -- one picture, three
    meanings -- and the four tangent icons were four versions of the same
    diagonal. Both are now different shapes, and this fails if they converge
    again.
    """

    @staticmethod
    def _signature(name, size=32):
        """A coarse bitmap of where the icon has ink."""
        from animkit.ui import icons

        image = icons.image(name, size=size)
        return tuple(
            image.pixelColor(x, y).alpha() > 40
            for y in range(0, size, 2)
            for x in range(0, size, 2)
        )

    @staticmethod
    def _difference(a, b):
        return sum(1 for x, y in zip(a, b) if x != y)

    def test_no_two_icons_are_the_same_picture(self):
        from animkit.ui import icons

        signatures = {n: self._signature(n) for n in icons.PAINTERS}
        names = sorted(signatures)
        clashes = []
        for i, first in enumerate(names):
            for second in names[i + 1:]:
                if signatures[first] == signatures[second]:
                    clashes.append((first, second))
        assert clashes == []

    @pytest.mark.parametrize("pair", [
        ("animkitKeysCycle", "animkitPoseReset"),
        ("animkitKeysCycle", "animkitRefresh"),
        ("animkitPoseReset", "animkitRefresh"),
        ("animkitKeysTangentAuto", "animkitKeysTangentSpline"),
        ("animkitKeysTangentLinear", "animkitKeysTangentFlat"),
        ("animkitPoseMirror", "animkitPoseFlip"),
        ("animkitKeysOffsetBack", "animkitKeysOffsetForward"),
        # The reference group's mirror pairs, and the two collisions its first
        # draft had: every icon in it shared a film-frame container, so at 16px
        # they were eight identical rectangles with a smudge inside. The
        # container is gone; these assert the shapes that replaced it stay
        # apart.
        ("animkitRefFadeDown", "animkitRefFadeUp"),
        ("animkitRefSlipBack", "animkitRefSlipForward"),
        ("animkitRefSlipBack", "animkitKeysOffsetBack"),
        ("animkitRefSync", "animkitRefSlipBack"),
        ("animkitRefRemove", "animkitKeysDelete"),
        ("animkitRefRemove", "animkitSetDelete"),
    ])
    def test_the_pairs_that_actually_collided(self, pair):
        """Named individually because these are the ones that DID collide.

        A generic "all icons differ" test passes on two shapes that differ by
        four pixels. These have to differ properly.
        """
        first, second = pair
        difference = self._difference(
            self._signature(first), self._signature(second)
        )
        assert difference >= 24, (
            "%s and %s differ by only %d cells -- they will read as the same "
            "icon at 16px" % (first, second, difference)
        )


class TestStyle:
    def test_style_object_name_cannot_collide_with_a_control_name(self):
        """`MQtUtil.findControl(name)` matches on objectName, and the styled
        root carries one too.

        What that looked like: `style.apply_to` set the objectName to
        "animkitPanel", which is also `panel.CONTROL_NAME`, so findControl
        returned the widget INSIDE the workspaceControl instead of the control.
        `is_built` then read the built-marker off the wrong widget and reported
        a perfectly good panel as empty -- so every `show()` logged a uiScript
        failure and rebuilt a panel that had built fine, and `show(tab=...)`
        silently never switched tab.

        Nothing about that is visible from reading either module on its own.
        """
        from animkit.ui import (keys_ui, panel, reference_ui, sets_ui, style,
                                tween_ui)

        controls = {
            module.CONTROL_NAME
            for module in (panel, sets_ui, tween_ui, keys_ui, reference_ui)
        }
        assert style.ROOT_OBJECT_NAME not in controls, (
            "%r is both the styled root's objectName and a workspaceControl "
            "name; findControl will return the wrong widget"
            % style.ROOT_OBJECT_NAME
        )

    def test_the_stylesheet_selects_on_the_name_apply_to_sets(self):
        """A rename that misses one of the two leaves the panel unstyled, and
        an unstyled panel still works -- so nothing reports it."""
        from animkit.ui import style

        assert "#%s" % style.ROOT_OBJECT_NAME in style.sheet()

    def test_every_group_has_an_accent(self):
        from animkit.tools import keys
        from animkit.ui import style

        for group in keys.GROUPS:
            assert group in style.ACCENTS, group

    def test_an_unknown_group_falls_back_rather_than_raising(self):
        from animkit.ui import style

        assert style.accent_for("no such group") == style.ACCENT_DEFAULT

    def test_the_stylesheet_has_no_unfilled_placeholders(self):
        """A stray `{name}` silently disables every rule after it in Qt."""
        from animkit.ui import style

        import re

        # Qt stylesheets legitimately contain braces, so this looks for the
        # str.format syntax specifically: `{name}` that never got substituted.
        leftovers = re.findall(r"\{[a-z_]+\}", style.sheet())
        assert leftovers == []

    def test_px_scales_and_never_returns_zero(self):
        from animkit.ui import style

        assert style.px(16) >= 1
        assert style.px(1) >= 1
        assert isinstance(style.px(22), int)

    def test_scale_is_safe_with_no_screen(self):
        """Read on every metric, including under mayapy where there is no
        screen at all. Returning 0 there would collapse the whole panel."""
        from animkit.ui import style

        assert style.scale() > 0
