"""What a dropped path is. Fast tier -- no Maya, real files in a tmp_path.

Every test here is a drop gesture an animator actually performs. The ones that
matter most are the two that look identical from the outside and must therefore
produce identical results: dragging one frame of a sequence, and dragging all
240 of them because that is what selecting a sequence looks like in Explorer.

Real files rather than a mocked listdir, because the thing being tested IS the
directory scan. A fake listing would pass with the regex that reports a padding
of 1 for `shot_010.0001.exr`, which is the bug this module exists to not have.
"""

import os

from animkit.core import media


def _touch(folder, name):
    path = os.path.join(str(folder), name)
    with open(path, "w") as handle:
        handle.write("x")
    return path


def _sequence(folder, head="walk.", tail=".png", frames=range(1, 25),
              padding=4):
    made = []
    for frame in frames:
        made.append(_touch(folder, "%s%0*d%s" % (head, padding, frame, tail)))
    return made


class TestKind:
    def test_images_and_movies_are_told_apart(self):
        assert media.kind("C:/ref/a.png") == media.KIND_IMAGE
        assert media.kind("C:/ref/a.EXR") == media.KIND_IMAGE
        assert media.kind("C:/ref/a.mp4") == media.KIND_MOVIE
        assert media.kind("C:/ref/a.MOV") == media.KIND_MOVIE

    def test_anything_else_is_refused_before_maya_sees_it(self):
        """A drop of a scene file or a spreadsheet must not reach imagePlane.

        Maya's own drop handling for those is somebody else's job, and this is
        why the viewport filter passes on what it does not recognise.
        """
        for path in ("shot.ma", "shot.mb", "notes.txt", "rig.fbx", "noext"):
            assert media.kind(path) is None, path

    def test_a_path_with_no_extension_is_not_a_crash(self):
        assert media.kind("") is None
        assert media.kind(None) is None


class TestFrameNumber:
    def test_the_frame_is_the_digit_run_the_extension_follows(self):
        """`shot_010.0001.exr` has two digit runs and only one is the frame.

        A lazy `.*` takes `010` and a greedy one backtracks to the single digit
        `1`, reporting a padding of 1. Both produce a reference that shows one
        frame for the whole shot and neither fails until somebody scrubs.
        """
        head, digits, tail = media.split_frame("C:/ref/shot_010.0001.exr")
        assert digits == "0001"
        assert tail == ".exr"
        assert head.replace("\\", "/") == "C:/ref/shot_010."

    def test_an_unpadded_frame_number(self):
        head, digits, tail = media.split_frame("C:/ref/ref1.png")
        assert (digits, tail) == ("1", ".png")

    def test_a_name_with_no_digits_at_all(self):
        assert media.split_frame("C:/ref/plate.png") is None

    def test_digits_in_a_directory_are_not_a_frame_number(self):
        """`C:/shots/2024/plate.png` is not frame 2024 of anything."""
        assert media.split_frame("C:/shots/2024/plate.png") is None


class TestSequences:
    def test_finds_the_whole_range_from_any_one_frame(self, tmp_path):
        _sequence(tmp_path, frames=range(1, 25))
        found = media.sequence_of(os.path.join(str(tmp_path), "walk.0013.png"))
        assert found is not None
        assert (found.first, found.last, found.count) == (1, 24, 24)
        assert found.padding == 4

    def test_a_lone_numbered_file_is_a_still_not_a_sequence(self, tmp_path):
        """`poster_01.jpg` on its own is a picture, and wiring frame extension
        to it makes Maya look for `poster_02.jpg` forever."""
        _touch(tmp_path, "poster_01.jpg")
        assert media.sequence_of(
            os.path.join(str(tmp_path), "poster_01.jpg")) is None

    def test_the_example_path_is_a_file_that_exists(self, tmp_path):
        """Maya is handed one real frame and derives the rest. Handing it a
        `####` pattern, or a frame that was never rendered, loads nothing."""
        _sequence(tmp_path, frames=range(7, 30))
        found = media.sequence_of(os.path.join(str(tmp_path), "walk.0020.png"))
        assert os.path.isfile(found.example)
        assert found.example.endswith("walk.0007.png")

    def test_two_sequences_in_one_folder_do_not_bleed(self, tmp_path):
        _sequence(tmp_path, head="walk.", frames=range(1, 10))
        _sequence(tmp_path, head="run.", frames=range(1, 100))
        walk = media.sequence_of(os.path.join(str(tmp_path), "walk.0003.png"))
        assert walk.count == 9

    def test_the_same_numbers_with_a_different_extension_are_a_different_sequence(
        self, tmp_path
    ):
        _sequence(tmp_path, tail=".png", frames=range(1, 10))
        _sequence(tmp_path, tail=".exr", frames=range(1, 40))
        found = media.sequence_of(os.path.join(str(tmp_path), "walk.0003.png"))
        assert found.count == 9

    def test_a_head_with_regex_characters_in_it_still_matches(self, tmp_path):
        """`shot[a]_` is a filename and also a character class. Unescaped it
        matches nothing, and the sequence quietly becomes a single still."""
        _sequence(tmp_path, head="shot[a]_v1+", frames=range(1, 6))
        found = media.sequence_of(
            os.path.join(str(tmp_path), "shot[a]_v1+0002.png"))
        assert found is not None and found.count == 5

    def test_gaps_are_reported(self, tmp_path):
        """Maya holds the last good frame across a hole rather than saying
        anything, so a half-rendered sequence reads as one that hitches."""
        _sequence(tmp_path, frames=[1, 2, 3, 7, 8])
        found = media.sequence_of(os.path.join(str(tmp_path), "walk.0001.png"))
        assert found.count == 5
        assert found.span == 8
        assert found.missing == (4, 5, 6)

    def test_only_the_padding_maya_will_look_for_is_counted(self, tmp_path):
        """A folder holding `walk.0001.png` and `walk.1.png` is one sequence to
        a person and two to Maya, which substitutes using the padding of the
        name it was given. Reporting the on-disk range would print a frame
        count that disagrees with what scrubbing shows."""
        _sequence(tmp_path, frames=range(1, 10), padding=4)
        _sequence(tmp_path, frames=range(20, 30), padding=2)
        found = media.sequence_of(os.path.join(str(tmp_path), "walk.0004.png"))
        assert found.count == 9
        assert found.mixed_padding is True

    def test_a_single_padding_is_not_reported_as_mixed(self, tmp_path):
        _sequence(tmp_path, frames=range(1, 10))
        found = media.sequence_of(os.path.join(str(tmp_path), "walk.0004.png"))
        assert found.mixed_padding is False

    def test_an_unreadable_folder_is_not_a_crash(self):
        """A dropped path can name a share that is down or a drive that was
        unplugged. A drag-and-drop handler does not get to raise."""
        assert media.sequence_of("Z:/nothing/here/walk.0001.png") is None

    def test_the_label_reads_as_a_sequence(self, tmp_path):
        _sequence(tmp_path, frames=range(1, 5))
        found = media.sequence_of(os.path.join(str(tmp_path), "walk.0001.png"))
        assert found.label == "walk.####.png"

    def test_path_for_rebuilds_a_frame(self, tmp_path):
        _sequence(tmp_path, frames=range(1, 5))
        found = media.sequence_of(os.path.join(str(tmp_path), "walk.0001.png"))
        assert os.path.isfile(found.path_for(3))


class TestResolve:
    def test_dragging_every_frame_gives_one_reference_not_two_hundred(
        self, tmp_path
    ):
        """THE case this module exists for. Selecting a sequence in Explorer
        selects every file in it, and 240 image planes stacked on the camera is
        a scene that has to be undone before anything else can happen."""
        made = _sequence(tmp_path, frames=range(1, 241))
        items = media.resolve(made)
        assert len(items) == 1
        assert items[0].frame_count == 240

    def test_dragging_one_frame_gives_the_same_thing(self, tmp_path):
        """Both gestures mean 'load this sequence', so both must produce it."""
        made = _sequence(tmp_path, frames=range(1, 241))
        one = media.resolve([made[100]])
        every = media.resolve(made)
        assert len(one) == 1
        assert one[0].key == every[0].key
        assert one[0].path == every[0].path

    def test_a_folder_is_scanned(self, tmp_path):
        """An animator who just exported a sequence drags the folder."""
        _sequence(tmp_path, frames=range(1, 13))
        items = media.resolve([str(tmp_path)])
        assert len(items) == 1 and items[0].frame_count == 12

    def test_a_folder_scan_is_not_recursive(self, tmp_path):
        """`Documents` dropped by accident must produce nothing, not a walk of
        every image on the machine."""
        nested = tmp_path / "inner"
        nested.mkdir()
        _sequence(nested, frames=range(1, 5))
        assert media.resolve([str(tmp_path)]) == []

    def test_movies_are_never_collapsed_into_one_another(self, tmp_path):
        """`take_01.mov` .. `take_09.mov` is nine takes, not nine frames.
        Collapsing them would silently discard eight of the files."""
        made = [_touch(tmp_path, "take_%02d.mov" % n) for n in range(1, 10)]
        items = media.resolve(made)
        assert len(items) == 9
        assert all(item.kind == media.KIND_MOVIE for item in items)

    def test_drop_order_is_kept(self, tmp_path):
        first = _touch(tmp_path, "b.mp4")
        second = _touch(tmp_path, "a.mp4")
        items = media.resolve([first, second])
        assert [os.path.basename(i.path) for i in items] == ["b.mp4", "a.mp4"]

    def test_unloadable_files_are_dropped_and_reportable(self, tmp_path):
        good = _touch(tmp_path, "ref.mp4")
        bad = _touch(tmp_path, "notes.txt")
        assert [i.path for i in media.resolve([good, bad])] == [good]
        assert media.rejected([good, bad]) == [bad]

    def test_a_folder_is_not_reported_as_rejected(self, tmp_path):
        """The animator dropped the folder, not the readme inside it."""
        assert media.rejected([str(tmp_path)]) == []

    def test_a_drop_of_nothing_usable_is_empty_not_an_error(self):
        """The drop handler says so; resolve only decides what is there."""
        assert media.resolve([]) == []
        assert media.resolve(None) == []
        assert media.resolve(["C:/gone/shot.ma"]) == []

    def test_a_still_reports_one_frame(self, tmp_path):
        path = _touch(tmp_path, "plate.jpg")
        item = media.resolve([path])[0]
        assert item.is_sequence is False
        assert (item.first, item.last, item.frame_count) == (1, 1, 1)
        assert item.label == "plate.jpg"
