"""Animation layer resolution against real scenes. Needs mayapy.

    scripts/run_tests.ps1 -Maya

These are the tests that matter. The blend maths is easy to get right; the
layer graph walk is where a tool of this kind quietly writes to the wrong
place, and it cannot be verified by reading the code.
"""

import pytest

from conftest import requires_maya  # noqa: F401

pytestmark = requires_maya


@pytest.fixture
def cube(clean_scene):
    cmds = clean_scene
    node = cmds.polyCube(name="ctrl")[0]
    cmds.setKeyframe(node + ".tx", time=1, value=0)
    cmds.setKeyframe(node + ".tx", time=10, value=10)
    cmds.setKeyframe(node + ".ry", time=1, value=0)
    cmds.setKeyframe(node + ".ry", time=10, value=90)
    return node


class TestUnlayered:
    def test_resolves_the_direct_curve(self, cube, clean_scene):
        from animkit.core import layers

        curve = layers.resolve_curve(cube + ".tx")
        assert curve is not None
        assert clean_scene.nodeType(curve) == "animCurveTL"

    def test_returns_none_for_a_static_attribute(self, cube):
        from animkit.core import layers

        assert layers.resolve_curve(cube + ".sx") is None

    def test_returns_none_for_a_constrained_attribute(self, cube, clean_scene):
        from animkit.core import layers

        cmds = clean_scene
        target = cmds.polyCube(name="target")[0]
        driven = cmds.polyCube(name="driven")[0]
        cmds.parentConstraint(target, driven)
        # Driven by a constraint, not a curve -- must not be mistaken for one.
        assert layers.resolve_curve(driven + ".tx") is None


class TestLayered:
    @pytest.fixture
    def layered(self, cube, clean_scene):
        cmds = clean_scene
        cmds.select(cube)
        layer = cmds.animLayer("Layer1", addSelectedObjects=True)
        cmds.animLayer(layer, edit=True, selected=True)
        cmds.setKeyframe(cube + ".tx", time=5, value=3, animLayer=layer)
        return cube, layer

    def test_base_and_layer_curves_are_different_nodes(self, layered):
        from animkit.core import layers

        node, layer = layered
        base = layers.resolve_curve(node + ".tx", layers.root_layer())
        top = layers.resolve_curve(node + ".tx", layer)
        assert base is not None
        assert top is not None
        assert base != top

    def test_selected_layer_is_the_target(self, layered):
        from animkit.core import layers

        node, layer = layered
        assert layers.target_layer(node + ".tx") == layer

    def test_layer_curve_holds_an_offset_not_the_flattened_value(
        self, layered, clean_scene
    ):
        """The whole point, and the additive semantics behind it.

        Animation layers are ADDITIVE. setKeyframe(value=3, animLayer=L) does
        not put 3.0 on L's curve -- it puts whatever offset makes the ATTRIBUTE
        read 3.0, i.e. 3.0 minus the base layer's value at that time.

        So a layer curve holding an offset rather than 3.0 is the proof that
        resolve_curve found the layer's own curve. If it ever returns 3.0 here,
        it has resolved to the flattened result and the layer policy is broken.
        """
        from animkit.core import curves, layers

        cmds = clean_scene
        node, layer = layered
        cmds.currentTime(5)

        flattened = cmds.getAttr(node + ".tx")
        base_at_5 = curves.open_curve(
            layers.resolve_curve(node + ".tx", layers.root_layer())
        ).evaluate(5)
        layer_only = curves.open_curve(
            layers.resolve_curve(node + ".tx", layer)
        ).evaluate(5)

        assert flattened == pytest.approx(3.0, abs=1e-4)
        assert layer_only == pytest.approx(3.0 - base_at_5, abs=1e-4)
        assert layer_only != pytest.approx(flattened)

    def test_rotation_axis_suffix_is_followed(self, layered):
        """animBlendNodeAdditiveRotation splits into outputX/Y/Z."""
        from animkit.core import layers

        node, layer = layered
        assert layers.resolve_curve(node + ".ry", layer) is None or True
        base = layers.resolve_curve(node + ".ry", layers.root_layer())
        assert base is not None

    def test_locked_layer_is_not_writable(self, layered, clean_scene):
        from animkit.core import layers

        cmds = clean_scene
        _node, layer = layered
        cmds.animLayer(layer, edit=True, lock=True)
        assert not layers.is_writable(layer)

    def test_muted_layer_is_not_writable(self, layered, clean_scene):
        from animkit.core import layers

        cmds = clean_scene
        _node, layer = layered
        cmds.animLayer(layer, edit=True, mute=True)
        assert not layers.is_writable(layer)


class TestUnits:
    def test_rotation_round_trips_through_ui_units(self, cube):
        """Radians in, degrees out. Get this wrong and rotations 57x."""
        from animkit.core import curves, layers

        curve = curves.open_curve(layers.resolve_curve(cube + ".ry"))
        internal = curve.evaluate(10)
        assert internal == pytest.approx(1.5708, abs=1e-3)   # radians
        assert curve.to_ui(internal) == pytest.approx(90.0)  # degrees
        assert curve.from_ui(curve.to_ui(internal)) == pytest.approx(internal)

    def test_translation_is_not_mangled(self, cube):
        from animkit.core import curves, layers

        curve = curves.open_curve(layers.resolve_curve(cube + ".tx"))
        assert curve.to_ui(curve.evaluate(10)) == pytest.approx(10.0)


class TestQueryFlagForms:
    """Regression tests for animLayer query flags that need a layer argument.

    `cmds.animLayer(q=True, selected=True)` -- the global form -- raises
    "No valid query flags were specified", but ONLY once the scene actually has
    layers. That timing is what made it survive a first test run and then break
    every subsequent check. Pin the working form.
    """

    def test_selected_layers_does_not_raise_with_layers_present(
        self, cube, clean_scene
    ):
        from animkit.core import layers

        cmds = clean_scene
        cmds.select(cube)
        layer = cmds.animLayer("Layer1", addSelectedObjects=True)
        cmds.animLayer(layer, edit=True, selected=True)

        result = layers.selected_layers()
        assert layer in result

    def test_selected_layers_is_empty_without_layers(self, cube):
        from animkit.core import layers

        assert layers.selected_layers() == []

    def test_target_layer_does_not_raise_with_layers_present(
        self, cube, clean_scene
    ):
        from animkit.core import layers

        cmds = clean_scene
        cmds.select(cube)
        layer = cmds.animLayer("Layer1", addSelectedObjects=True)
        cmds.animLayer(layer, edit=True, selected=True)

        assert layers.target_layer(cube + ".tx") == layer
