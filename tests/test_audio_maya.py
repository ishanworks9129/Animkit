"""Sound on the timeline: an audio node made from a drop.

The formats matter more than anything else here. Maya's audio node reads wav
and aiff and refuses everything else -- and it refuses with "cannot find file",
against a path that is plainly on disk. An animator who drops the mp3 their
director emailed them is sent looking for a file that is not missing, so the
conversion IS the feature and most of these tests are about it.

The fixtures write REAL sound files with the bundled ffmpeg, for the same
reason the reference fixtures write real images: whether Maya took the file is
the only question that matters, and only Maya can answer it.
"""

import os

import pytest

from conftest import requires_maya  # noqa: F401

pytestmark = requires_maya


def _tone(folder, name, seconds=3):
    """A real sound file, in whatever format the extension asks for."""
    import subprocess

    from animkit.core import transcode

    binary = transcode.executable()
    if binary is None:
        pytest.skip("no ffmpeg")
    path = os.path.join(str(folder), name)
    subprocess.check_call(
        [binary, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i",
         "sine=frequency=440:duration=%d" % seconds, path],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    return path


@pytest.fixture
def film(clean_scene):
    """24fps and a 0-200 range, so the frame maths is predictable."""
    cmds = clean_scene
    cmds.currentUnit(time="film")
    cmds.playbackOptions(minTime=0, maxTime=200)
    return cmds


class TestFormats:
    def test_a_wav_loads(self, film, tmp_path):
        from animkit.tools import audio

        clip = audio.load(_tone(tmp_path, "take.wav"))
        assert clip is not None
        assert clip.length == 72          # 3 seconds at 24fps

    def test_an_mp3_loads_even_though_maya_cannot_read_one(self, film,
                                                           tmp_path):
        """The whole point. Maya refuses an mp3 outright -- asserted here so
        the fixture cannot quietly stop being the hard case -- so this passing
        at all means the conversion ran."""
        from animkit.tools import audio

        source = _tone(tmp_path, "dialogue.mp3")
        with pytest.raises(Exception):
            film.sound(file=source.replace("\\", "/"))

        clip = audio.load(source)
        assert clip is not None
        assert clip.length == 72
        assert clip.path.lower().endswith(".wav")
        assert clip.source == source

    def test_the_label_stays_the_file_the_animator_dropped(self, film,
                                                           tmp_path):
        """A hashed name in a cache folder is not what they dropped and is not
        what they should have to recognise in a panel."""
        from animkit.tools import audio

        clip = audio.load(_tone(tmp_path, "dialogue.mp3"))
        assert clip.label == "dialogue.mp3"

    def test_a_wav_is_used_where_it_lies(self, film, tmp_path):
        """Nothing to gain by rewriting a file Maya already reads, and a cache
        full of copies of healthy files is disk spent on nothing."""
        from animkit.tools import audio

        source = _tone(tmp_path, "take.wav")
        clip = audio.load(source)
        assert os.path.normcase(clip.path) == os.path.normcase(source)

    def test_the_same_sound_twice_reuses_the_conversion(self, film, tmp_path):
        from animkit.tools import audio

        source = _tone(tmp_path, "dialogue.mp3")
        first = audio.load(source)
        second = audio.load(source)
        assert first.path == second.path

    def test_a_file_that_is_not_a_sound_is_refused(self, film, tmp_path):
        from animkit.tools import audio

        path = os.path.join(str(tmp_path), "notreally.mp3")
        with open(path, "w") as handle:
            handle.write("nope")
        assert audio.load(path) is None
        assert audio.clips() == []

    def test_a_video_is_not_a_sound(self, film, tmp_path):
        from animkit.tools import audio

        assert audio.load(os.path.join(str(tmp_path), "shot.mp4")) is None


class TestPlacement:
    def test_it_starts_at_the_range_start(self, film, tmp_path):
        from animkit.tools import audio

        film.currentTime(59)
        assert audio.load(_tone(tmp_path, "take.wav")).starts_at == 0

    def test_the_start_frame_can_be_set(self, film, tmp_path):
        from animkit.tools import audio

        clip = audio.load(_tone(tmp_path, "take.wav"))
        audio.set_start(101, nodes=[clip])
        assert clip.starts_at == 101

    def test_slip_moves_it_later(self, film, tmp_path):
        from animkit.tools import audio

        clip = audio.load(_tone(tmp_path, "take.wav"))
        audio.set_start(50, nodes=[clip])
        audio.slip(4, nodes=[clip])
        assert clip.starts_at == 54

    def test_sync_starts_it_here(self, film, tmp_path):
        from animkit.tools import audio

        clip = audio.load(_tone(tmp_path, "take.wav"))
        film.currentTime(77)
        audio.sync_to_current(nodes=[clip])
        assert clip.starts_at == 77

    def test_mute_flips_every_target_the_same_way(self, film, tmp_path):
        """Two sounds half-muted is a state nobody asked for."""
        from animkit.tools import audio

        one = audio.load(_tone(tmp_path, "a.wav"))
        two = audio.load(_tone(tmp_path, "b.wav"))
        audio.set_muted(True, nodes=[one])
        audio.toggle_mute(nodes=[one, two])
        assert one.muted is True and two.muted is True


class TestRecognition:
    def test_an_audio_node_animkit_did_not_make_is_invisible(self, film,
                                                             tmp_path):
        """A shot may already have sound the layout department set up."""
        from animkit.tools import audio

        source = _tone(tmp_path, "theirs.wav")
        film.sound(file=source.replace("\\", "/"))
        assert audio.clips() == []

    def test_remove_does_not_touch_a_foreign_audio_node(self, film, tmp_path):
        from animkit.tools import audio

        theirs = film.sound(
            file=_tone(tmp_path, "theirs.wav").replace("\\", "/"))
        audio.load(_tone(tmp_path, "ours.wav"))

        audio.remove()
        assert film.objExists(theirs)
        assert audio.clips() == []

    def test_remove_leaves_no_node_of_ours_behind(self, film, tmp_path):
        from animkit.tools import audio

        audio.load(_tone(tmp_path, "a.wav"))
        audio.load(_tone(tmp_path, "b.wav"))
        assert audio.remove() == 2
        assert audio.clips() == []


class TestTimelineDrop:
    """Dropping on the TIME SLIDER means "start it here".

    The mapping is arithmetic and it is worth pinning: an off-by-one lands a
    dialogue track a frame out, and an animator would blame the sound rather
    than the drop.
    """

    def test_the_ends_of_the_slider_are_the_ends_of_the_range(self):
        from animkit.ui import viewport_drop

        assert viewport_drop.frame_at(0.0, 0, 200) == 0
        assert viewport_drop.frame_at(1.0, 0, 200) == 200
        assert viewport_drop.frame_at(0.5, 0, 200) == 100

    def test_a_range_that_does_not_start_at_zero(self):
        from animkit.ui import viewport_drop

        assert viewport_drop.frame_at(0.0, 1001, 1101) == 1001
        assert viewport_drop.frame_at(0.5, 1001, 1101) == 1051

    def test_it_lands_on_a_whole_frame(self):
        """A sound cannot start on frame 87.4, and a `setAttr` of one would
        put every sample a fraction out."""
        from animkit.ui import viewport_drop

        for fraction in (0.137, 0.333, 0.6666, 0.99):
            found = viewport_drop.frame_at(fraction, 0, 211)
            assert found == int(found)

    def test_a_drop_past_the_edge_clamps(self):
        """A drop registered a pixel outside the control belongs at the end of
        the range, not past it."""
        from animkit.ui import viewport_drop

        assert viewport_drop.frame_at(-0.2, 0, 200) == 0
        assert viewport_drop.frame_at(1.4, 0, 200) == 200

    def test_a_dropped_frame_places_the_sound(self, film, tmp_path):
        """What the timeline drop actually does, through the same entry point
        it uses -- `reference.drop` with a frame."""
        from animkit.tools import audio, reference

        reference.drop([_tone(tmp_path, "dialogue.mp3")], frame=120)
        clips = audio.clips()
        assert len(clips) == 1
        assert clips[0].starts_at == 120

    def test_it_beats_the_drops_start_setting(self, film, tmp_path):
        """Dropping AT a frame is the animator naming one, so it has to win
        over the default -- otherwise the gesture does nothing."""
        from animkit.core import settings
        from animkit.tools import audio, reference

        try:
            settings.set("reference.start_at", "range", write=False)
            reference.drop([_tone(tmp_path, "dialogue.mp3")], frame=88)
        finally:
            settings.forget()
        assert audio.clips()[0].starts_at == 88


class TestDropping:
    def test_a_drop_of_sound_makes_no_image_plane(self, film, tmp_path):
        """A drop routinely mixes a dialogue track in with the video, and the
        sound used to be refused as 'not an image or a movie'."""
        from animkit.tools import audio, reference

        made = reference.drop([_tone(tmp_path, "dialogue.mp3")])
        assert made == []
        assert film.ls(type="imagePlane") == []
        assert len(audio.clips()) == 1

    def test_a_mixed_drop_routes_each_half(self, film, tmp_path):
        import maya.api.OpenMaya as om
        from animkit.tools import audio, reference

        picture = os.path.join(str(tmp_path), "plate.png")
        image = om.MImage()
        image.create(64, 32, 4)
        image.writeToFile(picture, "png")

        made = reference.drop([picture, _tone(tmp_path, "dialogue.mp3")])
        assert len(made) == 1
        assert len(audio.clips()) == 1

    def test_several_sounds_all_load(self, film, tmp_path):
        """Maya plays one at a time, which is a display limit and not a reason
        to refuse the second file."""
        from animkit.tools import audio

        made = audio.drop([_tone(tmp_path, "a.wav"), _tone(tmp_path, "b.wav")])
        assert len(made) == 2
        assert all(clip.length == 72 for clip in made)

    def test_a_drop_of_nothing_usable_makes_nothing(self, film):
        from animkit.tools import audio

        assert audio.drop([]) == []
        assert audio.drop(["C:/gone/take.wav"]) == []

    def test_the_time_slider_calls_degrade_rather_than_raise(self, film,
                                                             tmp_path):
        """There is no playback slider under mayapy, and every one of these has
        to be a no-op rather than an exception -- a batch session still opens
        scenes that contain sound."""
        from animkit.tools import audio

        clip = audio.load(_tone(tmp_path, "take.wav"))
        assert audio.active() is None
        assert audio.activate(clip) is False
        assert audio.activate(None) is False
        assert clip.active is False
