"""Reference media: image planes made from a drop.

What is asserted here is mostly the things that would be silently wrong rather
than loudly broken -- a sequence that loads as a single frame, a drop that
makes 240 planes, a `Remove` that eats the layout department's image plane.
None of those raises anything.

The fixtures write REAL image files, with Maya's own `MImage`, because the
loader's correctness rests on asking Maya whether it got pixels. A stub file
with a `.png` extension is exactly the case that must be refused, so it appears
here as a test rather than as a fixture.
"""

import os

import pytest

from conftest import requires_maya  # noqa: F401

pytestmark = requires_maya


# --- fixtures ---------------------------------------------------------------


def _png(path, width=64, height=32):
    """A real, readable image. Written with Maya's own writer, so no
    third-party dependency arrives with the test suite."""
    import maya.api.OpenMaya as om

    image = om.MImage()
    image.create(width, height, 4)
    image.writeToFile(path, "png")
    return path


def _sequence(folder, head="walk.", frames=range(1, 25), padding=4):
    made = []
    for frame in frames:
        made.append(_png(os.path.join(
            str(folder), "%s%0*d.png" % (head, padding, frame))))
    return made


@pytest.fixture
def sequence(tmp_path, clean_scene):
    """24 frames on disk and an empty scene. Returns the frame paths."""
    return _sequence(tmp_path, frames=range(1, 25))


# --- loading ----------------------------------------------------------------


class TestLoading:
    def test_a_sequence_loads_as_a_sequence(self, sequence, clean_scene):
        """The failure this guards is silent: without `useFrameExtension` the
        plane shows frame 1 for the whole shot and looks like a reference that
        will not play."""
        from animkit.tools import reference

        cmds = clean_scene
        found = reference.load(sequence[0])
        assert found is not None
        assert found.is_sequence is True
        assert cmds.getAttr(found.shape + ".useFrameExtension") is True

    def test_a_still_is_not_wired_to_time(self, tmp_path, clean_scene):
        """A single image with frame extension on makes Maya hunt for frames
        that do not exist, and the plane goes blank as soon as you scrub."""
        from animkit.tools import reference

        found = reference.load(_png(os.path.join(str(tmp_path), "plate.png")))
        assert found.is_sequence is False

    def test_the_reference_starts_on_the_frame_it_was_dropped_at(
        self, sequence, clean_scene, dropped_here
    ):
        """Maya draws image `frameExtension + frameOffset`, and frameExtension
        is time. Getting the sign of that wrong puts the reference twice as far
        from where it belongs, in the direction that looks like it worked."""
        from animkit.tools import reference

        cmds = clean_scene
        cmds.currentTime(10)
        found = reference.load(sequence[0])

        assert cmds.getAttr(found.shape + ".outputFrameExtension") == 1
        cmds.currentTime(15)
        assert cmds.getAttr(found.shape + ".outputFrameExtension") == 6

    def test_a_sequence_that_does_not_start_at_one(self, tmp_path, clean_scene):
        from animkit.tools import reference

        cmds = clean_scene
        made = _sequence(tmp_path, frames=range(101, 130))
        cmds.currentTime(1)
        found = reference.load(made[0])
        assert cmds.getAttr(found.shape + ".outputFrameExtension") == 101

    def test_dropping_every_frame_makes_one_plane_not_two_hundred(
        self, sequence, clean_scene
    ):
        """Selecting a sequence in Explorer selects every file in it. One
        image plane per frame is a scene that has to be undone before anything
        else can happen."""
        from animkit.tools import reference

        assert len(reference.drop(sequence)) == 1
        assert len(reference.references()) == 1

    def test_dropping_a_folder_is_the_same_gesture(self, sequence, tmp_path,
                                                   clean_scene):
        from animkit.tools import reference

        made = reference.drop([str(tmp_path)])
        assert len(made) == 1
        assert made[0].frame_range() == (1, 24)

    def test_an_unreadable_image_is_refused_and_leaves_no_plane(
        self, tmp_path, clean_scene
    ):
        """A file with a .png extension is not a png. Maya loads the plane
        happily and draws nothing, which reads as the tool having done
        nothing at all -- so the plane must not be left behind."""
        from animkit.tools import reference

        cmds = clean_scene
        broken = os.path.join(str(tmp_path), "broken.png")
        with open(broken, "w") as handle:
            handle.write("not an image")

        assert reference.load(broken) is None
        assert cmds.ls(type="imagePlane") == []

    def test_a_movie_with_no_decoder_is_KEPT(self, tmp_path, clean_scene):
        """The one place the judgement is inverted, deliberately.

        Whether Maya can decode a container is invisible from here, so an
        animator who dropped a perfectly good .mp4 must not be left with no
        evidence anything happened. A visible empty plane and a warning tells
        them what to do; a silent refusal does not.
        """
        from animkit.tools import reference

        fake = os.path.join(str(tmp_path), "take01.mp4")
        with open(fake, "w") as handle:
            handle.write("not a movie")

        found = reference.load(fake)
        assert found is not None
        assert found.loaded is False

    def test_a_file_type_animkit_does_not_load_is_refused(self, tmp_path,
                                                          clean_scene):
        from animkit.tools import reference

        cmds = clean_scene
        other = os.path.join(str(tmp_path), "shot.ma")
        with open(other, "w") as handle:
            handle.write("//Maya ASCII")

        assert reference.load(other) is None
        assert cmds.ls(type="imagePlane") == []

    def test_a_drop_of_nothing_usable_makes_nothing(self, clean_scene):
        from animkit.tools import reference

        assert reference.drop([]) == []
        assert reference.drop(["C:/gone/shot.ma"]) == []

    def test_a_whole_drop_is_one_undo_step(self, sequence, tmp_path,
                                           clean_scene):
        """Dropping four files is one gesture, so it is one Ctrl+Z."""
        from animkit.tools import reference

        cmds = clean_scene
        cmds.undoInfo(state=True, infinity=True)
        _sequence(tmp_path, head="run.", frames=range(1, 10))

        made = reference.drop([sequence[0],
                               os.path.join(str(tmp_path), "run.0001.png")])
        assert len(made) == 2

        cmds.undo()
        assert reference.references() == []

    def test_a_drop_does_not_steal_the_selection(self, sequence, clean_scene):
        """`cmds.imagePlane` selects what it creates and has no flag to stop it.

        What that looked like: drop a reference while posing, and the controls
        you had selected are gone -- replaced by an image plane -- from a
        gesture that has nothing to do with selection.

        The second half is worse because it is silent. Every operation here
        targets the selection first, so the plane left selected by the drop
        became the only thing the next Fade or Slip touched. Two references up,
        press Fade, one of them moves, and nothing anywhere says why.
        """
        from animkit.tools import reference

        cmds = clean_scene
        controls = [cmds.createNode("transform", name="ctrl_%d" % n)
                    for n in range(3)]
        cmds.select(controls)

        reference.load(sequence[0])
        assert set(cmds.ls(selection=True) or []) == set(controls)

    def test_a_drop_with_nothing_selected_leaves_nothing_selected(
        self, sequence, clean_scene
    ):
        from animkit.tools import reference

        cmds = clean_scene
        cmds.select(clear=True)
        reference.load(sequence[0])
        assert (cmds.ls(selection=True) or []) == []

    def test_a_pinned_reference_lands_on_the_camera_it_was_aimed_at(
        self, sequence, clean_scene
    ):
        from animkit.tools import reference

        cmds = clean_scene
        camera = cmds.camera(name="shotCam")[0]
        found = reference.load(sequence[0], camera=camera,
                               attach=reference.ATTACH_CAMERA)
        assert found.camera == camera


# --- recognition ------------------------------------------------------------


class TestVideo:
    """A dropped video, which is the case that does not work without help.

    Maya cannot decode .mp4 on an image plane at all -- `coverage` stays -1 in
    both Movie and Image File mode -- so every one of these would be a blank
    plane if the conversion were not happening.
    """

    @staticmethod
    def _video(path, seconds=2, fps=30, size="320x180", codec=None):
        import subprocess

        from animkit.core import transcode

        args = [
            transcode.executable(), "-hide_banner", "-loglevel", "error",
            "-y", "-f", "lavfi", "-i",
            "testsrc=size=%s:rate=%d:duration=%d" % (size, fps, seconds),
        ]
        if codec:
            args += ["-c:v", codec]
        if path.endswith(".mp4"):
            args += ["-pix_fmt", "yuv420p"]
        args.append(path)
        subprocess.check_call(args, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT)
        return path

    @pytest.fixture(autouse=True)
    def _isolated_cache(self, tmp_path, monkeypatch):
        """Convert into the test's own folder, not the animator's prefs."""
        from animkit.core import transcode

        cache = os.path.join(str(tmp_path), "refcache")
        monkeypatch.setattr(transcode, "cache_root", lambda: cache)
        return cache

    @pytest.mark.parametrize("name,codec", [
        ("take.mp4", None),
        ("take.mov", None),
        ("take.avi", "mjpeg"),
    ])
    def test_every_container_an_animator_has_becomes_a_real_reference(
        self, tmp_path, clean_scene, name, codec
    ):
        from animkit.tools import reference

        source = self._video(os.path.join(str(tmp_path), name),
                             seconds=1, fps=24, codec=codec)
        found = reference.load(source)

        assert found is not None, name
        assert found.loaded is True, "%s drew no pixels" % name
        assert found.is_sequence is True, name

    def test_the_plane_keeps_the_video_name_not_the_cache_name(
        self, tmp_path, clean_scene
    ):
        """`frame.####.jpg` is not what the animator dropped, and not what
        they should have to recognise in the panel."""
        from animkit.tools import reference

        source = self._video(os.path.join(str(tmp_path), "walk_v03.mp4"),
                             seconds=1, fps=24)
        found = reference.load(source)
        assert found.label == "walk_v03.mp4"

    def test_the_original_path_is_remembered(self, tmp_path, clean_scene):
        from animkit.tools import reference

        source = self._video(os.path.join(str(tmp_path), "walk.mp4"),
                             seconds=1, fps=24)
        found = reference.load(source)
        assert found.source == source

    def test_the_sequence_runs_at_the_scene_rate(self, tmp_path, clean_scene):
        """One second of footage must be one second of timeline, or every
        timing in the reference is off by the ratio of the two rates."""
        from animkit.tools import reference

        cmds = clean_scene
        cmds.currentUnit(time="film")           # 24fps
        source = self._video(os.path.join(str(tmp_path), "clip.mp4"),
                             seconds=2, fps=30)
        found = reference.load(source)

        span = found.frame_range()
        assert span is not None
        assert 46 <= span[1] <= 50, span

    def test_a_pal_scene_converts_at_25(self, tmp_path, clean_scene):
        from animkit.tools import reference

        cmds = clean_scene
        try:
            cmds.currentUnit(time="pal")        # 25fps
            source = self._video(os.path.join(str(tmp_path), "clip.mp4"),
                                 seconds=2, fps=30)
            found = reference.load(source)
            span = found.frame_range()
            assert 48 <= span[1] <= 52, span
        finally:
            cmds.currentUnit(time="film")

    def test_it_still_lands_on_the_frame_it_was_dropped_at(self, tmp_path,
                                                           clean_scene,
                                                           dropped_here):
        from animkit.tools import reference

        cmds = clean_scene
        cmds.currentTime(20)
        source = self._video(os.path.join(str(tmp_path), "clip.mp4"),
                             seconds=1, fps=24)
        found = reference.load(source)
        assert cmds.getAttr(found.shape + ".outputFrameExtension") == 1

    def test_slip_works_on_a_converted_video(self, tmp_path, clean_scene):
        """The whole point of converting: a video that behaves like every
        other reference, including the timing operations."""
        from animkit.tools import reference

        cmds = clean_scene
        cmds.currentTime(1)
        source = self._video(os.path.join(str(tmp_path), "clip.mp4"),
                             seconds=1, fps=24)
        found = reference.load(source)
        before = cmds.getAttr(found.shape + ".outputFrameExtension")

        reference.slip(4)
        assert cmds.getAttr(
            found.shape + ".outputFrameExtension") == before + 4

    def test_the_same_video_twice_reuses_the_conversion(self, tmp_path,
                                                        clean_scene):
        from animkit.core import transcode
        from animkit.tools import reference

        source = self._video(os.path.join(str(tmp_path), "clip.mp4"),
                             seconds=1, fps=24)
        reference.load(source)
        folders, _size = transcode.cache_size(root=transcode.cache_root())

        reference.load(source)
        again, _size2 = transcode.cache_size(root=transcode.cache_root())
        assert again == folders == 1

    def test_conversion_can_be_switched_off(self, tmp_path, clean_scene):
        """Off means the movie goes to Maya, which is what happened before --
        and on this platform that means a plane with no pixels, KEPT."""
        from animkit.core import settings
        from animkit.tools import reference

        try:
            settings.set("reference.convert_movies", False, write=False)
            source = self._video(os.path.join(str(tmp_path), "clip.mp4"),
                                 seconds=1, fps=24)
            found = reference.load(source)
            assert found is not None
            # Still pointing at the movie itself, not at a cached sequence.
            # `is_sequence` is NOT the check: a movie plane has frame
            # extension on too, because that is how Maya plays one.
            # Compared with separators normalised -- Maya rewrites `\` to `/`
            # when it stores `imageName`.
            assert (found.path.replace("\\", "/")
                    == source.replace("\\", "/"))
            assert found.loaded is False
        finally:
            settings.forget()

    def test_a_file_that_is_not_really_a_video_is_kept_and_says_so(
        self, tmp_path, clean_scene
    ):
        """Conversion fails, and the fallback is the old behaviour rather than
        a silent nothing."""
        from animkit.tools import reference

        source = os.path.join(str(tmp_path), "broken.mp4")
        with open(source, "w") as handle:
            handle.write("not a movie at all")

        found = reference.load(source)
        assert found is not None
        assert found.loaded is False

    def test_the_scene_frame_rate_is_read_from_maya_not_guessed(
        self, clean_scene
    ):
        from animkit.tools import reference

        cmds = clean_scene
        try:
            for unit, expected in (("film", 24.0), ("pal", 25.0),
                                   ("ntsc", 30.0)):
                cmds.currentUnit(time=unit)
                assert abs(reference.scene_fps() - expected) < 0.01, unit
        finally:
            cmds.currentUnit(time="film")


class TestFreeAndPinned:
    """The difference an animator actually feels.

    A camera-attached image plane is parented under the camera SHAPE and
    cannot be grabbed in the viewport -- and `lockedToCamera 0` does not free
    it, which is the obvious fix that does not work. Free vs pinned is decided
    at creation, and `pin()` is the only way across.
    """

    def test_a_reference_is_free_by_default(self, sequence, clean_scene):
        from animkit.tools import reference

        cmds = clean_scene
        found = reference.load(sequence[0])
        assert found.is_free is True
        assert found.camera is None
        assert cmds.listRelatives(found.transform, parent=True) is None

    def test_a_free_reference_can_be_moved_rotated_and_scaled(
        self, sequence, clean_scene
    ):
        """The whole point. A pinned plane's transform is under the camera and
        the manipulators do not reach it."""
        from animkit.tools import reference

        cmds = clean_scene
        found = reference.load(sequence[0])
        node = found.transform

        for attr in ("translateX", "rotateY", "scaleX"):
            assert cmds.getAttr(node + "." + attr, settable=True), attr
            assert not cmds.getAttr(node + "." + attr, lock=True), attr

        cmds.setAttr(node + ".translate", 3, 4, 5)
        cmds.setAttr(node + ".scale", 2, 2, 2)
        assert cmds.getAttr(node + ".translate")[0] == (3.0, 4.0, 5.0)
        assert cmds.getAttr(node + ".scale")[0] == (2.0, 2.0, 2.0)

    def test_a_free_plane_is_sized_to_the_image_not_left_square(
        self, tmp_path, clean_scene
    ):
        """Maya makes a free plane 10x10, so a 16:9 reference arrives
        letterboxed inside a square. `width`/`height` are what size a free
        plane -- `sizeX`/`sizeY` are the camera-attached pair and setting them
        here does nothing at all."""
        from animkit.tools import reference

        cmds = clean_scene
        wide = _png(os.path.join(str(tmp_path), "wide.png"),
                    width=320, height=180)
        found = reference.load(wide)

        ratio = (cmds.getAttr(found.shape + ".width")
                 / cmds.getAttr(found.shape + ".height"))
        assert abs(ratio - 320.0 / 180.0) < 0.01

    def test_a_tall_image_is_not_stretched_either(self, tmp_path, clean_scene):
        from animkit.tools import reference

        cmds = clean_scene
        tall = _png(os.path.join(str(tmp_path), "tall.png"),
                    width=180, height=320)
        found = reference.load(tall)
        ratio = (cmds.getAttr(found.shape + ".width")
                 / cmds.getAttr(found.shape + ".height"))
        assert abs(ratio - 180.0 / 320.0) < 0.01

    def test_a_free_plane_is_stood_up_in_front_of_the_camera(
        self, sequence, clean_scene
    ):
        """Created and left alone it lies flat at the origin, edge-on to a
        perspective view -- which looks exactly like nothing happened."""
        from animkit.tools import reference

        cmds = clean_scene
        camera = cmds.camera(name="shotCam")[0]
        cmds.setAttr(camera + ".translate", 0, 0, 50)
        cmds.camera(camera, edit=True, centerOfInterest=50)
        found = reference.load(sequence[0], camera=camera)

        position = cmds.xform(found.transform, q=True, worldSpace=True,
                              translation=True)
        # The camera looks down -Z from z=50, so the plane belongs in front of
        # it: nearer the origin, not behind the lens.
        assert position[2] < 50.0
        # A free plane's FACE is its local +Z and the quad stands vertically in
        # local XY, so facing a camera that already looks down -Z means leaving
        # the orientation alone. Same transform Maya's own free image plane
        # gets. Note this cannot distinguish a correct mapping from a wrong one
        # -- see TestFacing, which asserts on world axes, and the note in
        # `_place_free` about why even that needs a real viewport.
        assert cmds.getAttr(found.transform + ".rotate")[0] == (0.0, 0.0, 0.0)

    def test_it_lands_where_the_camera_is_looking(self, sequence, clean_scene):
        """A FIXED distance is wrong exactly where it matters. `front`, `side`
        and `top` sit 1000 units out, so 40 units in front of `front` builds a
        correctly sized reference 960 units from the character -- which reads
        as the drop having failed.

        The centre of interest is where the camera is aimed, so the reference
        lands on the origin for those three and beside the character for a
        framed persp.
        """
        from animkit.core import settings
        from animkit.tools import reference

        cmds = clean_scene
        # Set explicitly rather than leaning on the shipped default: the Maya
        # tier reads the REAL prefs file, so a value someone saved once would
        # otherwise decide whether this passes.
        try:
            settings.set("reference.distance", 0.0, write=False)
            found = reference.load(sequence[0], camera="front")
            position = cmds.xform(found.transform, q=True, worldSpace=True,
                                  translation=True)
            assert max(abs(value) for value in position) < 1.0, position
        finally:
            settings.forget()

    def test_an_explicit_distance_overrides(self, sequence, clean_scene):
        from animkit.core import settings
        from animkit.tools import reference

        cmds = clean_scene
        try:
            settings.set("reference.distance", 12.0, write=False)
            found = reference.load(sequence[0], camera="front")
            position = cmds.xform(found.transform, q=True, worldSpace=True,
                                  translation=True)
            # `front` looks down -Z from z=1000, so 12 units in front is 988.
            assert abs(position[2] - 988.0) < 1.0, position
        finally:
            settings.forget()

    def test_an_orthographic_view_sizes_from_its_width_not_a_field_of_view(
        self, sequence, clean_scene
    ):
        """Field of view means nothing on an orthographic camera, and reading
        it anyway produces a plane sized from a number that is not about
        size."""
        from animkit.tools import reference

        cmds = clean_scene
        cmds.setAttr("frontShape.orthographicWidth", 40.0)
        found = reference.load(sequence[0], camera="front")

        wide = cmds.getAttr(found.shape + ".width")
        assert abs(wide - 40.0 * reference.VIEW_FILL) < 0.5, wide

    def test_asking_for_a_pinned_reference_still_works(self, sequence,
                                                       clean_scene):
        from animkit.tools import reference

        found = reference.load(sequence[0],
                               attach=reference.ATTACH_CAMERA)
        assert found.is_free is False
        assert found.camera == "persp"
        assert "->" in (found.transform or "")

    def test_pin_converts_free_to_camera_and_back(self, sequence, clean_scene):
        from animkit.tools import reference

        found = reference.load(sequence[0])
        assert found.is_free is True

        reference.pin()
        assert reference.references()[0].is_free is False

        reference.pin()
        assert reference.references()[0].is_free is True

    def test_pin_keeps_the_image_and_the_timing(self, sequence, clean_scene):
        """Converting must not quietly reload or re-aim the reference."""
        from animkit.tools import reference

        cmds = clean_scene
        cmds.currentTime(12)
        found = reference.load(sequence[0])
        reference.set_opacity(0.4)
        before = (found.path, found.offset, found.opacity)

        reference.pin()
        after_entry = reference.references()[0]
        assert (after_entry.path, after_entry.offset,
                after_entry.opacity) == before

    def test_pin_puts_every_target_in_the_same_state(self, sequence, tmp_path,
                                                     clean_scene):
        from animkit.tools import reference

        _sequence(tmp_path, head="run.", frames=range(1, 10))
        made = reference.drop([sequence[0],
                               os.path.join(str(tmp_path), "run.0001.png")])
        reference.pin(nodes=[made[0]])

        reference.pin()
        assert [e.is_free for e in reference.references()] == [False, False]
        reference.pin()
        assert [e.is_free for e in reference.references()] == [True, True]

    def test_pin_is_one_undo_step(self, sequence, clean_scene):
        from animkit.tools import reference

        cmds = clean_scene
        cmds.undoInfo(state=True, infinity=True)
        reference.load(sequence[0])

        reference.pin()
        assert reference.references()[0].is_free is False
        cmds.undo()
        assert reference.references()[0].is_free is True

    def test_the_attach_setting_decides_a_drop(self, sequence, clean_scene):
        from animkit.core import settings
        from animkit.tools import reference

        try:
            settings.set("reference.attach", reference.ATTACH_CAMERA,
                         write=False)
            assert reference.load(sequence[0]).is_free is False
            reference.remove()

            settings.set("reference.attach", reference.ATTACH_FREE,
                         write=False)
            assert reference.load(sequence[0]).is_free is True
        finally:
            settings.forget()

    def test_only_a_free_reference_is_worth_framing(self, sequence,
                                                    clean_scene):
        """`framable` is the half of Frame that can be tested here. `viewFit`
        needs an active view and `mayapy` has none, so whether the camera
        actually moves is the in-Maya self-test's job."""
        from animkit.tools import reference

        reference.load(sequence[0])
        assert len(reference.framable()) == 1

        reference.pin()
        assert reference.framable() == []

    def test_frame_with_no_viewport_says_so_rather_than_raising(
        self, sequence, clean_scene
    ):
        """Bindable, so it gets pressed in every environment there is."""
        from animkit.tools import reference

        reference.load(sequence[0])
        assert reference.frame_reference() == 0

    def test_framing_does_not_change_the_selection(self, sequence,
                                                   clean_scene):
        """It selects the plane to fit the view on it, and has to put the
        animator's controls back -- same rule as a drop. Holds even when
        there is no viewport and the fit itself does nothing."""
        from animkit.tools import reference

        cmds = clean_scene
        control = cmds.createNode("transform", name="someControl")
        reference.load(sequence[0])
        cmds.select(control)

        reference.frame_reference()
        assert cmds.ls(selection=True) == [control]

    def test_pin_does_not_change_the_selection_either(self, sequence,
                                                      clean_scene):
        """`imagePlane -e -detach` selects what it converted, the same way
        creating one does."""
        from animkit.tools import reference

        cmds = clean_scene
        control = cmds.createNode("transform", name="someControl")
        reference.load(sequence[0])
        cmds.select(control)

        reference.pin()
        assert cmds.ls(selection=True) == [control]


@pytest.fixture
def dropped_here():
    """Put a new reference on the CURRENT frame.

    Not the default any more -- a drop lands at the start of the playback
    range, because placing it on whatever frame the animator happened to be
    parked on reads as the tool moving the reference for reasons of its own.
    The tests below are about the offset ARITHMETIC rather than about that
    policy, so they ask for the mode they mean.
    """
    from animkit.core import settings
    from animkit.tools import reference

    settings.set("reference.start_at", reference.START_CURRENT, write=False)
    yield
    settings.forget()


@pytest.fixture
def angled():
    """Force the exact-facing mode, which is not the default."""
    from animkit.core import settings
    from animkit.tools import reference

    settings.set("reference.orient", reference.ORIENT_VIEW, write=False)
    yield
    settings.forget()


@pytest.fixture
def upright():
    """Force the upright-and-turned-to-view mode, which is not the default."""
    from animkit.core import settings
    from animkit.tools import reference

    settings.set("reference.orient", reference.ORIENT_UPRIGHT, write=False)
    yield
    settings.forget()


class TestFacing:
    """The plane's FACE has to end up pointing at the camera.

    What the bug looked like: drop anything through an ordinary tumbled persp
    and the reference draws as a thin diagonal sliver cutting through the grid.
    Nothing warns, the Channel Box looks entirely reasonable, and the plane's
    rotate channels are exactly the camera's -- because the old code copied the
    camera's axes wholesale, which aligns the plane's +Y (its face) with the
    camera's UP vector. Perpendicular to the view direction. Edge-on, always,
    by construction.

    So these assert on world AXES. Rotate channels cannot see this bug: through
    an axis-aligned camera the right answer and the wrong one both leave the
    plane at rotate (0,0,0) -- which is exactly why the old test passed.
    """

    @staticmethod
    def _axes(cmds, node):
        """(X, Y, Z) of a node's world matrix, as unit vectors."""
        matrix = cmds.xform(node, q=True, worldSpace=True, matrix=True)
        out = []
        for row in range(3):
            vector = matrix[row * 4:row * 4 + 3]
            length = sum(value * value for value in vector) ** 0.5 or 1.0
            out.append([value / length for value in vector])
        return out

    @staticmethod
    def _dot(one, two):
        return sum(a * b for a, b in zip(one, two))

    def _assert_faces(self, cmds, transform, camera):
        plane = self._axes(cmds, transform)
        lens = self._axes(cmds, camera)

        # The face (+Z) looks back down the lens. A Maya camera looks along its
        # own -Z, so "back at it" is +Z.
        assert abs(self._dot(plane[2], lens[2]) - 1.0) < 1e-4, (
            "plane is not facing the camera: %r" % (plane[2],))
        # Image right stays right, and is NOT mirrored.
        assert abs(self._dot(plane[0], lens[0]) - 1.0) < 1e-4, (
            "image is rolled or mirrored: %r" % (plane[0],))
        # Image up matches the camera's up, so the picture is not upside down.
        assert abs(self._dot(plane[1], lens[1]) - 1.0) < 1e-4, (
            "image is upside down: %r" % (plane[1],))

    def test_it_faces_an_axis_aligned_camera(self, sequence, clean_scene,
                                             angled):
        from animkit.tools import reference

        cmds = clean_scene
        camera = cmds.camera(name="flatCam")[0]
        cmds.setAttr(camera + ".translate", 0, 0, 50)
        cmds.camera(camera, edit=True, centerOfInterest=50)
        found = reference.load(sequence[0], camera=camera)
        self._assert_faces(cmds, found.transform, camera)

    def test_it_faces_a_tumbled_camera(self, sequence, clean_scene, angled):
        """The case the animator actually hits. The default persp sits at
        rotate (-27.938, 45, 0), and that is where the sliver came from."""
        from animkit.tools import reference

        cmds = clean_scene
        camera = cmds.camera(name="tumbledCam")[0]
        cmds.setAttr(camera + ".translate", 28.0, 21.0, 28.0)
        cmds.setAttr(camera + ".rotate", -27.938, 45.0, 0.0)
        cmds.camera(camera, edit=True, centerOfInterest=44.82)
        found = reference.load(sequence[0], camera=camera)
        self._assert_faces(cmds, found.transform, camera)

    def test_it_faces_a_rolled_camera(self, sequence, clean_scene, angled):
        """A camera with roll on it is the case where getting the third row's
        sign wrong stops being invisible."""
        from animkit.tools import reference

        cmds = clean_scene
        camera = cmds.camera(name="rolledCam")[0]
        cmds.setAttr(camera + ".translate", 7.0, 13.0, 21.0)
        cmds.setAttr(camera + ".rotate", -31.0, 24.0, 11.0)
        found = reference.load(sequence[0], camera=camera)
        self._assert_faces(cmds, found.transform, camera)

    def test_the_placement_is_a_rotation_and_not_a_scale(self, sequence,
                                                         clean_scene, angled):
        """Remapping rows by hand is how a matrix quietly picks up a mirror or
        a scale. The plane must arrive at scale 1 with a right-handed frame, or
        the animator's own scale tool starts from a lie."""
        from animkit.tools import reference

        cmds = clean_scene
        camera = cmds.camera(name="oddCam")[0]
        cmds.setAttr(camera + ".rotate", -31.0, 24.0, 11.0)
        found = reference.load(sequence[0], camera=camera)

        for value in cmds.getAttr(found.transform + ".scale")[0]:
            assert abs(value - 1.0) < 1e-4, value

        one, two, three = self._axes(cmds, found.transform)
        cross = [one[1] * two[2] - one[2] * two[1],
                 one[2] * two[0] - one[0] * two[2],
                 one[0] * two[1] - one[1] * two[0]]
        assert abs(self._dot(cross, three) - 1.0) < 1e-4, "mirrored frame"

    def test_a_pinned_plane_is_left_alone(self, sequence, clean_scene):
        """Only a free plane is placed. A camera-attached one lives in the
        under-world and Maya orients it."""
        from animkit.tools import reference

        cmds = clean_scene
        found = reference.load(sequence[0], attach=reference.ATTACH_CAMERA)
        assert found.is_free is False
        assert cmds.getAttr(found.transform + ".rotate")[0] == (0.0, 0.0, 0.0)
        assert cmds.getAttr(found.transform + ".translate")[0] == (0.0, 0.0,
                                                                  0.0)


class TestFrontIsTheDefault:
    """A new reference lands at rotate (0,0,0), whatever the viewport is doing.

    Two complaints, one cause. Orienting to the camera means the same file
    lands differently every time -- and through the default persp, which sits
    at exactly 45 degrees of yaw, the board comes in at rotate (0, 45, 0). In
    the `front` view that foreshortens the picture to cos(45) = 71% of its
    width, which does not read as "a board seen at an angle", it reads as the
    reference having been STRETCHED.
    """

    def test_it_matches_mayas_own_free_image_plane(self, sequence,
                                                   clean_scene):
        """The benchmark, asserted against Maya itself rather than against a
        number copied out of the docs."""
        from animkit.tools import reference

        cmds = clean_scene
        native = cmds.imagePlane()
        native_rotate = cmds.getAttr(native[0] + ".rotate")[0]
        cmds.delete(native[0])

        found = reference.load(sequence[0])
        assert cmds.getAttr(found.transform + ".rotate")[0] == native_rotate

    def test_the_viewport_angle_does_not_change_it(self, sequence,
                                                   clean_scene):
        """The whole point of the default: predictable, not camera-relative."""
        from animkit.tools import reference

        cmds = clean_scene
        for rotation in ((-27.938, 45.0, 0.0), (0.0, 0.0, 0.0),
                         (-31.0, 24.0, 11.0), (12.0, -170.0, -4.0)):
            cmds.file(new=True, force=True)
            camera = cmds.camera(name="anyCam")[0]
            cmds.setAttr(camera + ".translate", 28.0, 21.0, 28.0)
            cmds.setAttr(camera + ".rotate", *rotation)
            found = reference.load(sequence[0], camera=camera)
            for value in cmds.getAttr(found.transform + ".rotate")[0]:
                assert abs(value) < 1e-6, (rotation, value)

    def test_it_is_square_on_in_the_front_view(self, tmp_path, clean_scene):
        """The stretch, stated as geometry: the board's width has to project
        to its FULL width through `front`, not to 71% of it."""
        from animkit.tools import reference

        cmds = clean_scene
        wide = _png(os.path.join(str(tmp_path), "wide.png"),
                    width=320, height=180)
        found = reference.load(wide, camera="persp")

        # The board's own X axis, in world. Through `front` the screen is the
        # world XY plane, so an unforeshortened board has no Z in its width.
        across = cmds.xform(found.transform, q=True, worldSpace=True,
                            matrix=True)[0:3]
        assert abs(abs(across[0]) - 1.0) < 1e-6, across
        assert abs(across[2]) < 1e-6, "foreshortened in front: %r" % (across,)

    def test_the_picture_keeps_its_aspect(self, tmp_path, clean_scene):
        """Nothing about standing the board up may stretch the image itself."""
        from animkit.tools import reference

        cmds = clean_scene
        wide = _png(os.path.join(str(tmp_path), "wide.png"),
                    width=320, height=180)
        found = reference.load(wide)
        ratio = (cmds.getAttr(found.shape + ".width")
                 / cmds.getAttr(found.shape + ".height"))
        assert abs(ratio - 320.0 / 180.0) < 0.01, ratio

    def test_a_row_still_runs_along_world_x(self, tmp_path, clean_scene):
        """Front-facing boards are coplanar, so a row of them shares a Z."""
        from animkit.tools import reference

        cmds = clean_scene
        paths = []
        for index in range(3):
            folder = os.path.join(str(tmp_path), "b%d" % index)
            os.makedirs(folder)
            paths.append(_png(os.path.join(folder, "board.png"), 320, 180))
        made = reference.drop(paths, camera="persp")
        assert len(made) == 3

        spots = [cmds.xform(entry.transform, q=True, worldSpace=True,
                            translation=True) for entry in made]
        assert max(s[2] for s in spots) - min(s[2] for s in spots) < 1e-4
        assert max(s[1] for s in spots) - min(s[1] for s in spots) < 1e-4
        across = sorted(s[0] for s in spots)
        for left, right in zip(across, across[1:]):
            assert right - left > 1e-6


class TestUpright:
    """A new board STANDS UP. It does not lean.

    Facing the camera exactly means inheriting its pitch and roll, so a board
    dropped through an ordinary tumbled persp arrives at rotate (62.062, 45, 0)
    -- leaning in world space, skew from every angle except the one it was
    dropped from, and three fiddly channels away from straight. Upright takes
    the yaw and nothing else.
    """

    # --- the arithmetic, with no scene ---

    def test_a_front_camera_gives_maya_native_identity(self):
        """The benchmark. Maya's own Create > Free Image Plane makes a plane at
        rotate (0,0,0), vertical, readable through `front`. Upright through a
        camera already looking down -Z must produce exactly that."""
        from animkit.tools import reference

        right, up, normal = reference.upright_axes([0.0, 0.0, 1.0])
        assert right == [1.0, 0.0, 0.0]
        assert up == [0.0, 1.0, 0.0]
        assert normal == [0.0, 0.0, 1.0]

    def test_pitch_is_discarded_and_yaw_is_kept(self):
        from animkit.tools import reference

        # A camera looking down at 45 degrees from the +Z side.
        _right, up, normal = reference.upright_axes([0.0, 0.7071, 0.7071])
        assert abs(normal[1]) < 1e-9, "the board is still leaning"
        assert abs(normal[2] - 1.0) < 1e-6, "the yaw was lost with the pitch"
        assert up == [0.0, 1.0, 0.0]

    def test_it_stays_right_handed(self):
        """X cross Y must equal Z, or the image comes out mirrored."""
        from animkit.tools import reference

        for forward in ([0.0, 0.0, 1.0], [1.0, 0.0, 0.0], [0.6, 0.5, -0.62]):
            right, up, normal = reference.upright_axes(forward)
            cross = [right[1] * up[2] - right[2] * up[1],
                     right[2] * up[0] - right[0] * up[2],
                     right[0] * up[1] - right[1] * up[0]]
            assert max(abs(a - b) for a, b in zip(cross, normal)) < 1e-6, forward

    def test_a_camera_looking_straight_down_has_no_upright(self):
        """Every vertical plane is edge-on to a top view, so there is no
        upright answer and the caller must fall back rather than divide by a
        length of zero."""
        from animkit.tools import reference

        assert reference.upright_axes([0.0, 1.0, 0.0]) is None
        assert reference.upright_axes([0.0, -1.0, 0.0]) is None

    # --- and in a real scene ---

    def test_a_board_dropped_through_a_tumbled_persp_stands_up(
        self, sequence, clean_scene, upright
    ):
        """The complaint, exactly: rotate (62.062, 45, 0) instead of a board."""
        from animkit.tools import reference

        cmds = clean_scene
        camera = cmds.camera(name="tumbledCam")[0]
        cmds.setAttr(camera + ".translate", 28.0, 21.0, 28.0)
        cmds.setAttr(camera + ".rotate", -27.938, 45.0, 0.0)
        cmds.camera(camera, edit=True, centerOfInterest=44.82)

        found = reference.load(sequence[0], camera=camera)
        rotate = cmds.getAttr(found.transform + ".rotate")[0]
        assert abs(rotate[0]) < 1e-3, rotate
        assert abs(rotate[1] - 45.0) < 1e-3, rotate
        assert abs(rotate[2]) < 1e-3, rotate

    def test_the_board_is_vertical_and_its_up_is_world_up(self, sequence,
                                                          clean_scene,
                                                          upright):
        """The invariant behind the tidy rotate values, and the one that still
        holds for a camera at an angle no round number describes."""
        from animkit.tools import reference

        cmds = clean_scene
        camera = cmds.camera(name="oddCam")[0]
        cmds.setAttr(camera + ".translate", 7.0, 13.0, 21.0)
        cmds.setAttr(camera + ".rotate", -31.0, 24.0, 11.0)
        found = reference.load(sequence[0], camera=camera)

        matrix = cmds.xform(found.transform, q=True, worldSpace=True,
                            matrix=True)
        up, normal = matrix[4:7], matrix[8:11]
        assert abs(normal[1]) < 1e-4, "the board is leaning: %r" % (normal,)
        # Image up is the board's +Y, and it must be world up.
        assert abs(up[1] - 1.0) < 1e-4, "the board is rolled: %r" % (up,)

    def test_a_camera_roll_does_not_roll_the_board(self, sequence,
                                                   clean_scene, upright):
        """Somebody with a rolled shot camera should still get a level board."""
        from animkit.tools import reference

        cmds = clean_scene
        camera = cmds.camera(name="rolledCam")[0]
        cmds.setAttr(camera + ".rotate", 0.0, 0.0, 30.0)
        found = reference.load(sequence[0], camera=camera)

        up = cmds.xform(found.transform, q=True, worldSpace=True,
                        matrix=True)[4:7]
        assert abs(up[1] - 1.0) < 1e-4, up

    def test_a_top_view_still_gets_a_flat_board(self, sequence, clean_scene,
                                                upright):
        """The fallback. A top-down reference belongs in the ground plane, and
        this must not produce a NaN or an edge-on plane getting there."""
        from animkit.tools import reference

        cmds = clean_scene
        found = reference.load(sequence[0], camera="top")

        normal = cmds.xform(found.transform, q=True, worldSpace=True,
                            matrix=True)[8:11]
        # Facing straight up at the top camera, which is what "flat" means.
        assert abs(abs(normal[1]) - 1.0) < 1e-4, normal
        for value in cmds.getAttr(found.transform + ".rotate")[0]:
            assert value == value, "NaN in the rotation"

    def test_a_row_of_upright_boards_is_coplanar(self, tmp_path, clean_scene,
                                                 upright):
        """Laid out along the BOARD's right, not the camera's, so a rolled
        camera does not stagger them out of their shared plane."""
        from animkit.tools import reference

        cmds = clean_scene
        camera = cmds.camera(name="rolledCam")[0]
        cmds.setAttr(camera + ".translate", 0.0, 0.0, 60.0)
        cmds.setAttr(camera + ".rotate", 0.0, 0.0, 20.0)
        cmds.camera(camera, edit=True, centerOfInterest=60.0)

        paths = []
        for index in range(3):
            folder = os.path.join(str(tmp_path), "c%d" % index)
            os.makedirs(folder)
            paths.append(_png(os.path.join(folder, "board.png"), 320, 180))
        made = reference.drop(paths, camera=camera)
        assert len(made) == 3

        heights = [cmds.xform(entry.transform, q=True, worldSpace=True,
                              translation=True)[1] for entry in made]
        assert max(heights) - min(heights) < 1e-4, heights

    def test_the_setting_chooses(self, sequence, clean_scene, angled):
        """With upright off, the board leans with the camera again."""
        from animkit.tools import reference

        cmds = clean_scene
        camera = cmds.camera(name="tiltCam")[0]
        cmds.setAttr(camera + ".translate", 28.0, 21.0, 28.0)
        cmds.setAttr(camera + ".rotate", -27.938, 45.0, 0.0)
        found = reference.load(sequence[0], camera=camera)

        normal = cmds.xform(found.transform, q=True, worldSpace=True,
                            matrix=True)[8:11]
        assert abs(normal[1] - 0.4685) < 1e-3, normal


class TestSideBySide:
    """Several files in one drop land in a row.

    The bug this class exists for does not raise and does not warn: every free
    plane is stood up in front of the camera, so four dropped at once are four
    planes in exactly the same place. The animator sees ONE reference and
    reports that the other three did not load.
    """

    # --- the arithmetic, with no scene at all ---

    def test_one_board_is_centred(self):
        from animkit.tools import reference

        _wide, _high, offset = reference.slot_fit(16.0, 9.0, 16.0 / 9.0)
        assert abs(offset) < 1e-9

    def test_a_row_is_centred_on_what_the_camera_is_looking_at(self):
        """Not laid out from the left edge. The animator dropped the files
        while looking at something, and the row belongs around it."""
        from animkit.tools import reference

        for count in range(1, 6):
            offsets = [reference.slot_fit(16.0, 9.0, 1.0, slot, count)[2]
                       for slot in range(count)]
            assert abs(sum(offsets)) < 1e-9, (count, offsets)

    def test_boards_are_left_to_right_in_the_order_dropped(self):
        from animkit.tools import reference

        offsets = [reference.slot_fit(16.0, 9.0, 1.0, slot, 4)[2]
                   for slot in range(4)]
        assert offsets == sorted(offsets)

    def test_boards_do_not_overlap(self):
        """The whole point. Two boards that touch read as one picture; two
        that overlap are the original bug wearing a smaller hat."""
        from animkit.tools import reference

        for count in (2, 3, 7):
            fitted = [reference.slot_fit(16.0, 9.0, 16.0 / 9.0, slot, count)
                      for slot in range(count)]
            for left, right in zip(fitted, fitted[1:]):
                gap = right[2] - left[2]
                assert gap > (left[0] + right[0]) / 2.0, (count, left, right)

    def test_each_board_keeps_its_own_shape(self):
        """A portrait phone clip beside a 16:9 screen recording stays
        portrait. Squeezing both into a shared shape would distort the one
        thing a reference has to be honest about."""
        from animkit.tools import reference

        for aspect in (16.0 / 9.0, 9.0 / 16.0, 1.0):
            wide, high, _offset = reference.slot_fit(16.0, 9.0, aspect, 1, 3)
            assert abs(wide / high - aspect) < 1e-6

    def test_a_slot_out_of_range_is_clamped_rather_than_flung_off_screen(self):
        """Nothing in the drop path can produce one, but a caller passing
        slot=9 of 3 should get an edge board, not a reference somewhere off in
        the dark that reads as a failed load."""
        from animkit.tools import reference

        edge = reference.slot_fit(16.0, 9.0, 1.0, 2, 3)
        assert reference.slot_fit(16.0, 9.0, 1.0, 9, 3) == edge
        assert reference.slot_fit(16.0, 9.0, 1.0, -4, 3) == \
            reference.slot_fit(16.0, 9.0, 1.0, 0, 3)

    def test_no_slots_is_one_slot_and_not_a_division_by_zero(self):
        from animkit.tools import reference

        assert reference.slot_fit(16.0, 9.0, 1.0, 0, 0)[2] == 0.0

    # --- and in a real scene ---

    @staticmethod
    def _separate(tmp_path, count, width=320, height=180):
        """`count` stills that are NOT one sequence.

        A folder per file, because `board0.png .. board2.png` side by side is
        an image sequence and `core.media` is right to collapse it to one
        reference -- which is a different feature and has its own tests. What
        is wanted here is genuinely separate takes.
        """
        made = []
        for index in range(count):
            folder = os.path.join(str(tmp_path), "take%d" % index)
            os.makedirs(folder)
            made.append(_png(os.path.join(folder, "board.png"),
                             width=width, height=height))
        return made

    def test_three_dropped_files_land_in_three_different_places(
        self, tmp_path, clean_scene
    ):
        from animkit.tools import reference

        cmds = clean_scene
        made = reference.drop(self._separate(tmp_path, 3), camera="front")
        assert len(made) == 3

        spots = [tuple(round(value, 3) for value in
                       cmds.xform(entry.transform, q=True, worldSpace=True,
                                  translation=True))
                 for entry in made]
        assert len(set(spots)) == 3, spots

    def test_a_single_drop_is_still_centred(self, sequence, clean_scene):
        """The row must not cost the ordinary case anything -- one file has
        always landed where the camera is looking and still does."""
        from animkit.core import settings
        from animkit.tools import reference

        cmds = clean_scene
        try:
            settings.set("reference.distance", 0.0, write=False)
            found = reference.load(sequence[0], camera="front")
            position = cmds.xform(found.transform, q=True, worldSpace=True,
                                  translation=True)
            assert max(abs(value) for value in position) < 1.0, position
        finally:
            settings.forget()

    def test_the_row_stays_inside_the_view(self, tmp_path, clean_scene):
        """Four boards share the same width one board would have had. Spilling
        past the frame would put the outer two off screen, which is the bug
        again with extra steps."""
        from animkit.tools import reference

        cmds = clean_scene
        cmds.setAttr("frontShape.orthographicWidth", 40.0)
        made = reference.drop(self._separate(tmp_path, 4), camera="front")

        span = 40.0 * reference.VIEW_FILL
        for entry in made:
            centre = cmds.xform(entry.transform, q=True, worldSpace=True,
                                translation=True)[0]
            half = cmds.getAttr(entry.shape + ".width") / 2.0
            assert abs(centre) + half <= span / 2.0 + 1e-6, (centre, half)

    def test_a_pinned_drop_is_not_spread_out(self, tmp_path, clean_scene):
        """A camera-attached plane fills the frame by definition, so there is
        no row to lay out -- and it lives in the camera's under-world, where a
        world-space position is not the plane's to set. The slot has to be
        ignored here rather than applied to a transform that does not own it.
        """
        from animkit.core import settings
        from animkit.tools import reference

        cmds = clean_scene
        paths = self._separate(tmp_path, 3)
        try:
            settings.set("reference.attach", reference.ATTACH_CAMERA,
                         write=False)
            made = reference.drop(paths, camera="front")
        finally:
            settings.forget()

        assert len(made) == 3
        assert all(entry.is_free is False for entry in made)
        spots = [tuple(round(value, 3) for value in
                       cmds.xform(entry.transform, q=True, worldSpace=True,
                                  translation=True))
                 for entry in made]
        assert len(set(spots)) == 1, spots


class TestJoiningTheRow:
    """A reference loaded LATER must not land on top of the ones already up.

    What this looked like: load one reference, look at it, load a second, and
    the second is stood up in front of the camera exactly where the first was.
    Two boards intersecting at the origin, and the animator reasonably reports
    that the second file did not load. Laying out within a single drop does not
    help -- each of these drops has one file in it.
    """

    @staticmethod
    def _one(tmp_path, name, width=320, height=180):
        folder = os.path.join(str(tmp_path), name)
        os.makedirs(folder)
        return _png(os.path.join(folder, "board.png"),
                    width=width, height=height)

    @staticmethod
    def _spot(cmds, entry):
        return tuple(round(value, 3) for value in
                     cmds.xform(entry.transform, q=True, worldSpace=True,
                                translation=True))

    def test_a_second_load_does_not_land_on_the_first(self, tmp_path,
                                                      clean_scene):
        from animkit.tools import reference

        cmds = clean_scene
        reference.drop([self._one(tmp_path, "a")], camera="front")
        reference.drop([self._one(tmp_path, "b")], camera="front")

        found = reference.references()
        assert len(found) == 2
        spots = [self._spot(cmds, entry) for entry in found]
        assert len(set(spots)) == 2, spots

    def test_the_row_grows_and_stays_inside_the_view(self, tmp_path,
                                                     clean_scene):
        """Four separate loads must end up looking like one drop of four."""
        from animkit.tools import reference

        cmds = clean_scene
        cmds.setAttr("frontShape.orthographicWidth", 40.0)
        for index in range(4):
            reference.drop([self._one(tmp_path, "take%d" % index)],
                           camera="front")

        found = reference.references()
        assert len(found) == 4
        span = 40.0 * reference.VIEW_FILL
        for entry in found:
            centre = cmds.xform(entry.transform, q=True, worldSpace=True,
                                translation=True)[0]
            half = cmds.getAttr(entry.shape + ".width") / 2.0
            assert abs(centre) + half <= span / 2.0 + 1e-6, (centre, half)

        spots = sorted(self._spot(cmds, entry)[0] for entry in found)
        for left, right in zip(spots, spots[1:]):
            assert right - left > 1e-6

    def test_a_reference_the_animator_moved_is_left_alone(self, tmp_path,
                                                          clean_scene):
        """The reason PLACED_ATTR exists. Dragging a board somebody parked
        against a wall is a worse bug than the stacking this fixes."""
        from animkit.tools import reference

        cmds = clean_scene
        first = reference.drop([self._one(tmp_path, "parked")],
                               camera="front")[0]
        cmds.xform(first.transform, worldSpace=True,
                   translation=(123.0, 45.0, -67.0))
        parked = self._spot(cmds, first)

        reference.drop([self._one(tmp_path, "new")], camera="front")
        assert self._spot(cmds, first) == parked

    def test_resizing_counts_as_touching_it_too(self, tmp_path, clean_scene):
        """Scaling a board is positioning it. Re-flowing would reset the size,
        which is the same surprise as moving it."""
        from animkit.tools import reference

        cmds = clean_scene
        first = reference.drop([self._one(tmp_path, "sized")],
                               camera="front")[0]
        cmds.setAttr(first.shape + ".width", 3.5)
        cmds.setAttr(first.shape + ".height", 2.0)

        reference.drop([self._one(tmp_path, "other")], camera="front")
        assert abs(cmds.getAttr(first.shape + ".width") - 3.5) < 1e-4

    def test_a_pinned_reference_is_not_dragged_into_the_row(self, tmp_path,
                                                            clean_scene):
        from animkit.core import settings
        from animkit.tools import reference

        cmds = clean_scene
        try:
            settings.set("reference.attach", reference.ATTACH_CAMERA,
                         write=False)
            reference.drop([self._one(tmp_path, "pinned")], camera="front")
        finally:
            settings.forget()

        reference.drop([self._one(tmp_path, "free")], camera="front")
        pinned = [e for e in reference.references() if not e.is_free]
        assert len(pinned) == 1
        assert self._spot(cmds, pinned[0]) == (0.0, 0.0, 0.0)

    def test_a_failed_load_does_not_shuffle_the_existing_row(self, tmp_path,
                                                             clean_scene):
        """Moving the boards for a drop that produced nothing is gratuitous."""
        from animkit.tools import reference

        cmds = clean_scene
        first = reference.drop([self._one(tmp_path, "kept")],
                               camera="front")[0]
        before = self._spot(cmds, first)

        broken = os.path.join(str(tmp_path), "broken.png")
        with open(broken, "w") as handle:
            handle.write("not an image")
        assert reference.drop([broken], camera="front") == []
        assert self._spot(cmds, first) == before


class TestArrange:
    """The explicit tidy-up. Moves references whether or not they were touched,
    because asking for it is the permission."""

    @staticmethod
    def _one(tmp_path, name):
        folder = os.path.join(str(tmp_path), name)
        os.makedirs(folder)
        return _png(os.path.join(folder, "board.png"), width=320, height=180)

    def test_it_lays_a_scattered_set_into_a_row(self, tmp_path, clean_scene):
        from animkit.tools import reference

        cmds = clean_scene
        cmds.setAttr("frontShape.orthographicWidth", 40.0)
        made = [reference.drop([self._one(tmp_path, "s%d" % i)],
                               camera="front")[0] for i in range(3)]
        for index, entry in enumerate(made):
            cmds.xform(entry.transform, worldSpace=True,
                       translation=(index * 40.0, index * 11.0, index * -7.0))

        assert reference.arrange(camera="front") == 3

        span = 40.0 * reference.VIEW_FILL
        for entry in made:
            centre = cmds.xform(entry.transform, q=True, worldSpace=True,
                                translation=True)
            half = cmds.getAttr(entry.shape + ".width") / 2.0
            assert abs(centre[0]) + half <= span / 2.0 + 1e-6
            assert abs(centre[1]) < 1e-6 and abs(centre[2]) < 1e-6

    def test_it_moves_a_reference_the_animator_placed(self, tmp_path,
                                                      clean_scene):
        """The opposite of a drop, deliberately."""
        from animkit.tools import reference

        cmds = clean_scene
        entry = reference.drop([self._one(tmp_path, "parked")],
                               camera="front")[0]
        cmds.xform(entry.transform, worldSpace=True,
                   translation=(200.0, 0.0, 0.0))
        reference.arrange(camera="front")
        assert cmds.xform(entry.transform, q=True, worldSpace=True,
                          translation=True)[0] < 100.0

    def test_it_reads_left_to_right_through_the_camera(self, tmp_path,
                                                       clean_scene):
        """Order comes from the camera's own right axis, not world X. Through a
        camera looking down +X those are opposites, and sorting on world X
        would silently reverse the row."""
        from animkit.tools import reference

        cmds = clean_scene
        made = [reference.drop([self._one(tmp_path, "o%d" % i)],
                               camera="front")[0] for i in range(3)]
        labels = {}
        for index, entry in enumerate(made):
            cmds.xform(entry.transform, worldSpace=True,
                       translation=(-index * 30.0, 0.0, 0.0))
            labels[entry.shape] = index

        reference.arrange(camera="front")
        ordered = sorted(
            reference.references(),
            key=lambda e: cmds.xform(e.transform, q=True, worldSpace=True,
                                     translation=True)[0])
        # They were scattered right-to-left, so the row must come back in that
        # same reading order rather than in whatever order the scene lists.
        assert [labels[e.shape] for e in ordered] == [2, 1, 0]

    def test_it_says_so_when_there_is_nothing_to_arrange(self, clean_scene):
        from animkit.tools import reference

        assert reference.arrange() == 0

    def test_arranging_is_one_undo_step(self, tmp_path, clean_scene):
        from animkit.tools import reference

        cmds = clean_scene
        cmds.undoInfo(state=True, infinity=True)
        made = [reference.drop([self._one(tmp_path, "u%d" % i)],
                               camera="front")[0] for i in range(3)]
        before = [cmds.xform(e.transform, q=True, worldSpace=True,
                             translation=True) for e in made]

        reference.arrange(camera="front")
        cmds.undo()

        after = [cmds.xform(e.transform, q=True, worldSpace=True,
                            translation=True) for e in made]
        for was, now in zip(before, after):
            assert max(abs(a - b) for a, b in zip(was, now)) < 1e-4


class TestHoldingTheRange:
    """A reference must not break itself when you scrub off the end of it.

    Maya draws image `frameExtension + frameOffset` and nothing stops that sum
    leaving the sequence. Before the clamp: Sync at frame 100 on a sequence
    starting at 1 sets the offset to -99, so scrubbing back to frame 0 asked
    Maya for image -99, and the plane went blank or drew the magenta
    missing-image pattern. Worse and more common: a reference loaded at frame 1
    in a scene whose range starts at 0 was broken on the FIRST frame of the
    shot, every time, with no warning.

    All of it is asserted through `outputFrameExtension`, which is the number
    Maya actually looks the file up by. `coverageX` cannot be used -- measured,
    it keeps reporting the last image that DID load, so it reads as healthy
    while the plane draws nothing.
    """

    @staticmethod
    def _shown(cmds, entry, frame):
        cmds.currentTime(frame)
        cmds.dgdirty(entry.shape)
        return cmds.getAttr(entry.shape + ".outputFrameExtension")

    def test_scrubbing_before_the_start_holds_frame_one(self, sequence,
                                                        clean_scene):
        from animkit.tools import reference

        cmds = clean_scene
        cmds.currentTime(1)
        found = reference.load(sequence[0])
        for frame in (0, -5, -100):
            assert self._shown(cmds, found, frame) == 1, frame

    def test_scrubbing_past_the_end_holds_the_last_frame(self, sequence,
                                                         clean_scene):
        from animkit.tools import reference

        cmds = clean_scene
        cmds.currentTime(1)
        found = reference.load(sequence[0])
        for frame in (24, 25, 500):
            assert self._shown(cmds, found, frame) == 24, frame

    def test_sync_far_up_the_timeline_does_not_break_frame_zero(
        self, sequence, clean_scene
    ):
        """The reported case. Sync at 100 is correct and means the reference
        covers 100 onwards -- it must HOLD before that, not ask for image
        -99."""
        from animkit.tools import reference

        cmds = clean_scene
        cmds.currentTime(100)
        found = reference.load(sequence[0])
        reference.sync_to_current(nodes=[found])
        assert cmds.getAttr(found.shape + ".frameOffset") == -99

        assert self._shown(cmds, found, 100) == 1
        assert self._shown(cmds, found, 110) == 11
        for frame in (0, 50, 99):
            assert self._shown(cmds, found, frame) == 1, frame
        assert self._shown(cmds, found, 200) == 24

    def test_the_frames_in_between_are_untouched(self, sequence, clean_scene):
        """Holding at the ends must not disturb the part that already worked --
        frame N of the reference on frame N of the timeline is the whole point
        of the tool."""
        from animkit.tools import reference

        cmds = clean_scene
        cmds.currentTime(1)
        found = reference.load(sequence[0])
        for frame in range(1, 25):
            assert self._shown(cmds, found, frame) == frame

    def test_an_offset_of_zero_no_longer_breaks_it(self, sequence,
                                                   clean_scene):
        """Typing 0 into the spinbox used to ask for image 0, which is not a
        file that exists in a sequence numbered from 1."""
        from animkit.tools import reference

        cmds = clean_scene
        found = reference.load(sequence[0])
        reference.set_offset(0, nodes=[found])
        assert self._shown(cmds, found, 0) == 1
        assert self._shown(cmds, found, 12) == 12

    def test_slip_still_moves_it(self, sequence, clean_scene):
        """The clamp must not turn into a lock."""
        from animkit.tools import reference

        cmds = clean_scene
        cmds.currentTime(10)
        found = reference.load(sequence[0])
        before = self._shown(cmds, found, 10)
        reference.slip(3, nodes=[found])
        assert self._shown(cmds, found, 10) == before + 3

    def test_removing_a_reference_takes_its_helper_nodes(self, sequence,
                                                         clean_scene):
        """Three utility nodes per reference, none of them under the transform.
        Left behind they accumulate invisibly across a day of dropping and
        removing reference."""
        from animkit.tools import reference

        cmds = clean_scene
        found = reference.load(sequence[0])
        helpers = reference.timing_nodes(found.shape)
        assert len(helpers) == 3

        reference.remove(nodes=[found])
        assert [node for node in helpers if cmds.objExists(node)] == []

    def test_removing_one_leaves_anothers_helpers_alone(self, sequence,
                                                        tmp_path,
                                                        clean_scene):
        from animkit.tools import reference

        cmds = clean_scene
        _sequence(tmp_path, head="run.", frames=range(1, 10))
        first = reference.load(sequence[0])
        second = reference.load(os.path.join(str(tmp_path), "run.0001.png"))
        kept = reference.timing_nodes(second.shape)
        assert kept

        reference.remove(nodes=[first])
        assert all(cmds.objExists(node) for node in kept)

    def test_a_still_gets_no_clamp(self, tmp_path, clean_scene):
        """One image has no range to hold, and no frame extension to clamp."""
        from animkit.tools import reference

        found = reference.load(_png(os.path.join(str(tmp_path), "one.png")))
        assert found.is_sequence is False
        assert reference.timing_nodes(found.shape) == []

    def test_pinning_keeps_the_clamp(self, sequence, clean_scene):
        """`pin` renames the shape, and a connection held by name would not
        survive that."""
        from animkit.tools import reference

        cmds = clean_scene
        cmds.currentTime(1)
        found = reference.load(sequence[0])
        reference.pin(nodes=[found])

        moved = reference.references()[0]
        assert moved.is_free is False
        assert len(reference.timing_nodes(moved.shape)) == 3
        assert self._shown(cmds, moved, 0) == 1


class TestWhereADropStarts:
    """A drop must not move the reference in time for reasons of its own.

    The original behaviour put a new reference on the frame the animator
    happened to be sitting on. Drop a video while parked on frame 59 and it
    silently started at 59, and the only visible trace was `offset -58`.
    """

    @staticmethod
    def _mode(value):
        from animkit.core import settings

        settings.set("reference.start_at", value, write=False)

    def test_it_lands_at_the_start_of_the_range_by_default(self, sequence,
                                                           clean_scene):
        from animkit.tools import reference

        cmds = clean_scene
        cmds.playbackOptions(minTime=0, maxTime=200)
        cmds.currentTime(59)
        found = reference.load(sequence[0])
        assert found.starts_at == 0
        assert found.offset == 1

    def test_the_current_frame_is_still_available(self, sequence,
                                                  clean_scene):
        from animkit.core import settings
        from animkit.tools import reference

        cmds = clean_scene
        cmds.playbackOptions(minTime=0, maxTime=200)
        cmds.currentTime(59)
        try:
            self._mode(reference.START_CURRENT)
            found = reference.load(sequence[0])
        finally:
            settings.forget()
        assert found.starts_at == 59
        assert found.offset == -58

    def test_native_numbering_applies_no_offset(self, tmp_path, clean_scene):
        """A render numbered 101-200 belongs at 101-200 -- that IS its timing,
        and shifting it is the tool inventing one."""
        from animkit.core import settings
        from animkit.tools import reference

        cmds = clean_scene
        cmds.playbackOptions(minTime=0, maxTime=200)
        cmds.currentTime(59)
        made = _sequence(tmp_path, head="plate.", frames=range(101, 130))
        try:
            self._mode(reference.START_NATIVE)
            found = reference.load(made[0])
        finally:
            settings.forget()
        assert found.offset == 0
        assert found.starts_at == 101

    def test_an_explicit_frame_still_wins(self, sequence, clean_scene):
        """The setting is a default, not a policy -- a caller that says where
        it wants the reference gets it."""
        from animkit.tools import reference

        cmds = clean_scene
        cmds.playbackOptions(minTime=0, maxTime=200)
        assert reference.load(sequence[0], frame=42).starts_at == 42

    def test_a_range_that_does_not_start_at_zero(self, sequence, clean_scene):
        from animkit.tools import reference

        cmds = clean_scene
        cmds.playbackOptions(minTime=1001, maxTime=1100)
        cmds.currentTime(1050)
        assert reference.load(sequence[0]).starts_at == 1001


class TestStartFrame:
    """Timing expressed as the frame it starts on, not as Maya's offset.

    `frameOffset` is unreadable as a quantity. A video dropped on frame 32
    reports -31, which says nothing to anybody who does not already know the
    sequence is numbered from 1 and that Maya adds the offset to time. The
    panel asked animators to reason in that number and then to type into it.
    """

    def test_it_reports_the_frame_it_starts_on(self, sequence, clean_scene,
                                               dropped_here):
        from animkit.tools import reference

        cmds = clean_scene
        cmds.currentTime(32)
        found = reference.load(sequence[0])
        assert found.offset == -31
        assert found.starts_at == 32

    def test_setting_it_moves_the_reference(self, sequence, clean_scene):
        from animkit.tools import reference

        cmds = clean_scene
        found = reference.load(sequence[0])
        reference.set_start(60, nodes=[found])
        assert found.starts_at == 60
        cmds.currentTime(60)
        cmds.dgdirty(found.shape)
        assert cmds.getAttr(found.shape + ".outputFrameExtension") == 1

    def test_it_survives_a_round_trip(self, sequence, clean_scene):
        """Reading and writing must agree, or the spinbox fights the animator:
        it would show a value, be handed that same value back on the next
        refresh, and move the reference again."""
        from animkit.tools import reference

        found = reference.load(sequence[0])
        for frame in (0, 1, 47, -12, 300):
            reference.set_start(frame, nodes=[found])
            assert found.starts_at == frame

    def test_sync_is_the_same_as_starting_here(self, sequence, clean_scene):
        from animkit.tools import reference

        cmds = clean_scene
        cmds.currentTime(70)
        found = reference.load(sequence[0])
        reference.set_start(5, nodes=[found])
        reference.sync_to_current(nodes=[found])
        assert found.starts_at == 70

    def test_slip_moves_the_start_the_other_way(self, sequence, clean_scene):
        """Slipping forward shows a LATER frame now, which means the reference
        started EARLIER. Getting that sign backwards would make the spinbox
        crawl the wrong way under the hotkeys."""
        from animkit.tools import reference

        found = reference.load(sequence[0])
        before = found.starts_at
        reference.slip(3, nodes=[found])
        assert found.starts_at == before - 3

    def test_a_still_starts_where_it_was_dropped(self, tmp_path, clean_scene):
        """No sequence, so no range to read -- it must not raise, and it must
        not report something absurd."""
        from animkit.tools import reference

        cmds = clean_scene
        cmds.currentTime(9)
        found = reference.load(_png(os.path.join(str(tmp_path), "one.png")))
        assert found.is_sequence is False
        assert isinstance(found.starts_at, int)


class TestRecognition:
    def test_an_image_plane_animkit_did_not_make_is_invisible(
        self, sequence, clean_scene
    ):
        """A shot already has image planes the layout department put there.
        Deciding ownership from `imagePlane*` would delete one of those."""
        from animkit.tools import reference

        cmds = clean_scene
        theirs = cmds.imagePlane(camera="persp")[1]
        reference.load(sequence[0])

        assert len(reference.references()) == 1
        assert theirs not in [entry.shape for entry in reference.references()]

    def test_remove_does_not_touch_a_foreign_image_plane(self, sequence,
                                                         clean_scene):
        from animkit.tools import reference

        cmds = clean_scene
        theirs = cmds.imagePlane(camera="persp")[1]
        reference.load(sequence[0])

        reference.remove()
        assert reference.references() == []
        assert cmds.objExists(theirs)

    def test_renaming_the_node_changes_nothing(self, sequence, clean_scene):
        """Recognition is by tag. An animator tidying the Outliner is not
        allowed to break the tool."""
        from animkit.tools import reference

        cmds = clean_scene
        found = reference.load(sequence[0])
        cmds.rename(found.transform, "animatorRenamedThis")

        assert len(reference.references()) == 1

    def test_the_camera_is_read_from_the_connection(self, sequence,
                                                    clean_scene):
        """A PINNED image plane's parent path is Maya's under-world form,
        `|persp|perspShape->|imagePlane1`, and is not something to be parsing.

        Only pinned: a free reference has no camera connection at all, which is
        exactly how `is_free` answers the question.
        """
        from animkit.tools import reference

        found = reference.load(sequence[0], camera="persp",
                               attach=reference.ATTACH_CAMERA)
        assert found.camera == "persp"
        assert "->" in (found.transform or "")

    def test_remove_leaves_no_orphan_transform(self, sequence, clean_scene):
        """Deleting the shape alone leaves an empty image plane transform
        under the camera -- not in the Outliner's default view, and not
        selectable in the viewport."""
        from animkit.tools import reference

        cmds = clean_scene
        reference.load(sequence[0])
        reference.remove()

        assert cmds.ls(type="imagePlane") == []
        assert [n for n in (cmds.ls(type="transform") or [])
                if "imagePlane" in n] == []


# --- targeting --------------------------------------------------------------


class TestTargeting:
    def test_nothing_selected_acts_on_every_reference(self, sequence,
                                                      tmp_path, clean_scene):
        from animkit.tools import reference

        cmds = clean_scene
        _sequence(tmp_path, head="run.", frames=range(1, 10))
        reference.drop([sequence[0],
                        os.path.join(str(tmp_path), "run.0001.png")])
        cmds.select(clear=True)

        assert reference.slip(1) == 2

    def test_selecting_the_transform_targets_its_shape(self, sequence,
                                                       tmp_path, clean_scene):
        """The Outliner selects transforms and every operation acts on the
        shape, so a selection that is not walked down one level silently means
        'all of them' -- the opposite of what the animator just asked for."""
        from animkit.tools import reference

        cmds = clean_scene
        _sequence(tmp_path, head="run.", frames=range(1, 10))
        made = reference.drop([sequence[0],
                               os.path.join(str(tmp_path), "run.0001.png")])
        cmds.select(made[0].transform)

        assert reference.slip(1) == 1
        assert made[0].offset != made[1].offset

    def test_a_selection_with_no_reference_in_it_acts_on_everything(
        self, sequence, clean_scene
    ):
        """A control is selected for most of an animator's day. Reading that
        as 'act on nothing' would make every reference hotkey dead most of the
        time."""
        from animkit.tools import reference

        cmds = clean_scene
        reference.load(sequence[0])
        cmds.select(cmds.createNode("transform", name="someControl"))

        assert reference.slip(1) == 1


# --- the operations ---------------------------------------------------------


class TestOperations:
    def test_slip_moves_the_reference_in_time(self, sequence, clean_scene):
        from animkit.tools import reference

        cmds = clean_scene
        cmds.currentTime(1)
        found = reference.load(sequence[0])
        before = cmds.getAttr(found.shape + ".outputFrameExtension")

        reference.slip(3)
        assert cmds.getAttr(found.shape + ".outputFrameExtension") == before + 3

    def test_sync_puts_the_first_frame_on_the_current_frame(self, sequence,
                                                            clean_scene):
        from animkit.tools import reference

        cmds = clean_scene
        cmds.currentTime(1)
        found = reference.load(sequence[0])
        reference.slip(-40)

        cmds.currentTime(33)
        reference.sync_to_current()
        assert cmds.getAttr(found.shape + ".outputFrameExtension") == 1

    def test_fade_is_clamped_at_both_ends(self, sequence, clean_scene):
        """A hotkey held down runs off the end of the range, and a negative
        alphaGain is a plane that never comes back however many times you
        press the other one."""
        from animkit.tools import reference

        found = reference.load(sequence[0])
        for _ in range(20):
            reference.fade(-0.2)
        assert found.opacity == 0.0

        for _ in range(20):
            reference.fade(0.2)
        assert found.opacity == 1.0

    def test_toggle_puts_every_reference_in_the_same_state(
        self, sequence, tmp_path, clean_scene
    ):
        """Inverting each one leaves two references half-toggled, which is a
        state nobody asked for. If any is showing, the gesture means hide."""
        from animkit.tools import reference

        _sequence(tmp_path, head="run.", frames=range(1, 10))
        made = reference.drop([sequence[0],
                               os.path.join(str(tmp_path), "run.0001.png")])
        reference.set_visible(False, nodes=[made[0]])

        reference.toggle()
        assert [entry.visible for entry in made] == [False, False]

        reference.toggle()
        assert [entry.visible for entry in made] == [True, True]

    def test_set_offset_is_absolute(self, sequence, clean_scene):
        from animkit.tools import reference

        found = reference.load(sequence[0])
        reference.set_offset(-7)
        assert found.offset == -7
        reference.set_offset(-7)
        assert found.offset == -7

    def test_every_operation_is_one_undo_step(self, sequence, clean_scene):
        """The registry drives the panel and the hotkeys, so every entry in it
        gets pressed with a hotkey eventually."""
        from animkit.tools import reference

        cmds = clean_scene
        cmds.undoInfo(state=True, infinity=True)
        reference.load(sequence[0])

        for operation in reference.OPERATIONS:
            if operation.name in ("animkitRefLoad", "animkitRefRemove"):
                continue  # one opens a file browser, the other is covered above
            before = [(e.offset, e.opacity, e.visible)
                      for e in reference.references()]
            operation.invoke()
            cmds.undo()
            after = [(e.offset, e.opacity, e.visible)
                     for e in reference.references()]
            assert before == after, operation.name

    def test_operations_on_an_empty_scene_do_nothing_and_say_so(
        self, clean_scene
    ):
        """Every one of these is bindable, so every one gets pressed with
        nothing in the scene. None may raise."""
        from animkit.tools import reference

        for operation in reference.OPERATIONS:
            if operation.name == "animkitRefLoad":
                continue  # opens a file browser
            assert operation.invoke() == 0, operation.name


# --- the registry -----------------------------------------------------------


class TestRegistry:
    def test_every_reference_operation_has_an_icon(self):
        from animkit.tools import reference
        from animkit.ui import icons

        missing = [op.name for op in reference.OPERATIONS
                   if not icons.has_icon(op.name)]
        assert missing == []

    def test_every_reference_operation_is_a_runtime_command(self):
        from animkit import commands
        from animkit.tools import reference

        registered = {name for name, _a, _c in commands.COMMANDS}
        missing = [op.name for op in reference.OPERATIONS
                   if op.name not in registered]
        assert missing == []

    def test_the_generated_command_strings_compile(self):
        """A runTimeCommand body is a string nothing compiles until an animator
        presses the key."""
        from animkit.tools import reference

        for operation in reference.OPERATIONS:
            compile(operation.command, "<%s>" % operation.name, "exec")

    def test_the_file_filter_is_built_from_the_extension_sets(self):
        """Typed out by hand it disagrees with the loader the first time either
        changes, and the symptom is a browser that will not show a file the
        tool would have loaded."""
        from animkit.core import media
        from animkit.tools import reference

        built = reference._file_filter()
        for ext in list(media.IMAGE_EXTENSIONS) + list(media.MOVIE_EXTENSIONS):
            assert "*.%s" % ext in built, ext


# --- viewport drop ----------------------------------------------------------


class TestViewportDrop:
    """Under mayapy there are no model panels, so what is testable here is
    that none of it raises when there is nothing to hook. That is exactly the
    case that runs at Maya launch on a machine restoring a workspace, and a
    traceback there degrades the animator's startup."""

    def test_install_with_no_viewports_is_quiet(self):
        from animkit.ui import viewport_drop

        assert viewport_drop.install() == []
        assert viewport_drop.is_installed() is False

    def test_uninstall_with_nothing_installed_is_quiet(self):
        from animkit.ui import viewport_drop

        assert viewport_drop.uninstall() is True

    def test_a_drag_of_something_that_is_not_media_is_passed_through(self):
        """Dropping a .ma on the viewport has to keep meaning whatever Maya
        means by it."""
        from animkit.ui import viewport_drop

        assert viewport_drop._worth_taking(["C:/shots/shot.ma"]) is False
        assert viewport_drop._worth_taking([]) is False
        assert viewport_drop._worth_taking(["C:/ref/walk.0001.png"]) is True

    def test_the_setting_survives_a_round_trip(self):
        from animkit.core import settings
        from animkit.ui import viewport_drop

        try:
            viewport_drop.set_enabled(False)
            assert settings.get("reference.viewport_drop") is False
            viewport_drop.set_enabled(True)
            assert settings.get("reference.viewport_drop") is True
        finally:
            settings.forget()
