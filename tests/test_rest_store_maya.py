"""A captured rest pose, persisted in the scene.

Until this existed, `xform._rest_overrides` was a module-level dict, which made
Set Rest last exactly as long as the Python session. That is not a missing
feature so much as a broken promise: every ambiguous Reset prints "press Set
Rest so animkit knows where rest is", the animator does it, it works, and it is
gone when they reopen the shot.

The dict also OUTLIVED its scene, which is worse than losing it -- a rest
captured for `ctrl` in one shot was still keyed on that name when a different
shot with a different `ctrl` was opened.
"""

import os
import tempfile

import pytest

from conftest import requires_maya  # noqa: F401

pytestmark = requires_maya

TOL = 1e-3


@pytest.fixture
def store_scene(clean_scene):
    """A tiny board, plus a temp path to round-trip it through."""
    from animkit.core import cache, rest_store, xform

    cmds = clean_scene
    rest_store.clear()

    root = cmds.createNode("transform", name="face_rig")
    board = cmds.createNode("transform", name="board", parent=root)
    controls = []
    for index in range(3):
        ctrl = cmds.createNode(
            "transform", name="ctrlBoxBrow_%d" % index, parent=board
        )
        cmds.createNode("nurbsCurve", parent=ctrl)
        cmds.setAttr(ctrl + ".translateX", (index - 1) * 2.0)
        controls.append(ctrl)
    cache.invalidate()

    path = os.path.join(
        tempfile.gettempdir(), "animkit_rest_store_test.ma"
    ).replace("\\", "/")

    yield cmds, controls, path

    rest_store.clear()
    xform.clear_rest_overrides()
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def _reopen(cmds, path):
    cmds.file(rename=path)
    cmds.file(save=True, type="mayaAscii", force=True)
    cmds.file(new=True, force=True)
    cmds.file(path, open=True, force=True)


class TestRoundTrip:
    def test_a_captured_rest_survives_save_and_reopen(self, store_scene):
        """The promise the warning makes. It has to be true tomorrow."""
        from animkit.core import rest_store, xform

        cmds, controls, path = store_scene
        xform.capture_rest(controls)
        assert rest_store.save() == 3

        _reopen(cmds, path)

        assert len(xform._rest_overrides) == 3
        assert set(xform._rest_overrides) == set(controls)

    def test_the_matrices_come_back_unchanged(self, store_scene):
        from animkit.core import rest_store, xform

        cmds, controls, path = store_scene
        xform.capture_rest(controls)
        before = {
            n: list(m) for n, m in xform._rest_overrides.items()
        }
        rest_store.save()

        _reopen(cmds, path)

        for node, matrix in before.items():
            got = list(xform._rest_overrides[node])
            assert got == pytest.approx(matrix, abs=TOL), node

    def test_a_scene_with_no_stored_rest_ends_up_with_none(self, store_scene):
        """The stale-name bug, from the other side.

        Opening a shot that never had a rest captured must not inherit the
        previous shot's, keyed on names that happen to match.
        """
        from animkit.core import rest_store, xform

        cmds, controls, path = store_scene
        xform.capture_rest(controls)
        rest_store.save()
        _reopen(cmds, path)
        assert xform._rest_overrides

        cmds.file(new=True, force=True)
        # A different scene, with a control of the SAME name.
        cmds.createNode("transform", name="ctrlBoxBrow_0")
        rest_store.load()
        assert xform._rest_overrides == {}


class TestIdentification:
    def test_controls_are_found_by_connection_not_by_name(self, store_scene):
        """Renaming a control must not orphan its rest.

        The same reason selection sets are stored this way: a name-keyed store
        rots on rename, on namespace, and on a second copy of one rig.
        """
        from animkit.core import rest_store, xform

        cmds, controls, path = store_scene
        xform.capture_rest(controls)
        rest_store.save()

        renamed = cmds.rename(controls[0], "ctrlBoxBrow_renamed")
        rest_store.load()

        assert renamed in xform._rest_overrides
        assert controls[0] not in xform._rest_overrides

    def test_a_deleted_control_drops_out(self, store_scene):
        """Its rest goes with it, rather than lingering under a name something
        else may reuse."""
        from animkit.core import rest_store, xform

        cmds, controls, path = store_scene
        xform.capture_rest(controls)
        rest_store.save()

        cmds.delete(controls[0])
        rest_store.load()

        assert len(xform._rest_overrides) == 2
        assert controls[0] not in xform._rest_overrides

    def test_the_store_is_recognised_by_tag_not_by_node_name(self, store_scene):
        """An animator may rename it in the Outliner. Nothing reads its name."""
        from animkit.core import rest_store, xform

        cmds, controls, _path = store_scene
        xform.capture_rest(controls)
        rest_store.save()

        node = rest_store._store_nodes()[0]
        cmds.rename(node, "somebody_renamed_this")

        assert len(rest_store._store_nodes()) == 1
        assert rest_store.load() == 3


class TestWriting:
    def test_saving_replaces_rather_than_merges(self, store_scene):
        """A captured rest is a snapshot. Merging two taken at different
        moments produces a rest pose that never existed."""
        from animkit.core import cache, rest_store, xform

        cmds, controls, _path = store_scene
        xform.capture_rest(controls)
        rest_store.save()

        xform.clear_rest_overrides()
        cache.invalidate()
        xform.capture_rest([controls[0]])
        assert rest_store.save() == 1

        assert len(rest_store._store_nodes()) == 1
        rest_store.load()
        assert set(xform._rest_overrides) == {controls[0]}

    def test_clear_removes_it_from_the_scene_too(self, store_scene):
        from animkit.core import rest_store, xform

        cmds, controls, path = store_scene
        xform.capture_rest(controls)
        rest_store.save()

        rest_store.clear()
        assert rest_store._store_nodes() == []
        assert xform._rest_overrides == {}

        _reopen(cmds, path)
        assert xform._rest_overrides == {}

    def test_saving_nothing_leaves_no_node_behind(self, store_scene):
        from animkit.core import rest_store, xform

        _cmds, _controls, _path = store_scene
        xform.clear_rest_overrides()
        assert rest_store.save() == 0
        assert rest_store._store_nodes() == []


class TestSetRestEndToEnd:
    def test_set_rest_then_reopen_then_reset(self, store_scene):
        """The whole point, in one test.

        Press Set Rest on the neutral pose, close and reopen the shot, drag a
        control off its slot, press Reset -- and it goes back to its slot rather
        than to the origin.
        """
        from animkit.core import cache, rest_store, xform
        from animkit.tools import pose

        cmds, controls, path = store_scene

        cmds.select(controls)
        assert pose.capture_rest_pose() > 0
        assert rest_store._store_nodes(), "Set Rest did not write to the scene"

        neutral = [cmds.getAttr(c + ".translateX") for c in controls]
        _reopen(cmds, path)
        cache.invalidate()

        moved = controls[1]
        cmds.setAttr(moved + ".translateX", 9.0)
        cache.invalidate()

        cmds.select(controls)
        pose.reset_to_default()

        after = [cmds.getAttr(c + ".translateX") for c in controls]
        assert after == pytest.approx(neutral, abs=TOL)

    def test_clear_rest_removes_the_stored_copy(self, store_scene):
        from animkit.core import rest_store
        from animkit.tools import pose

        cmds, controls, _path = store_scene
        cmds.select(controls)
        pose.capture_rest_pose()
        assert rest_store._store_nodes()

        pose.clear_rest_pose()
        assert rest_store._store_nodes() == []
