"""Settings persistence, and above all its failure modes. No Maya needed.

`animkit.startup()` runs from userSetup.py. If anything in the settings path
can raise, it does not break animkit -- it degrades Maya launch for every
animator who has the module installed, before there is any UI to report it in.

So the interesting tests here are not the round trip. They are the corrupt
file, the empty file, the wrong types, the directory-where-a-file-should-be,
and the unwritable location. Every one of them must end at shipped defaults.
"""

import json
import os

import pytest

from conftest import REPO  # noqa: F401  (ensures the repo is importable)

from animkit.core import settings


@pytest.fixture
def prefs(tmp_path, monkeypatch):
    """Point settings at a throwaway directory, never the real prefs.

    Without this the suite would overwrite the settings of whoever ran it,
    which is a rude way to find out your tests touch real state.
    """
    monkeypatch.setattr(settings, "directory", lambda: str(tmp_path))
    settings.forget()
    yield tmp_path
    settings.forget()


def _write(prefs, text):
    target = os.path.join(str(prefs), settings.FILENAME)
    with open(target, "w") as handle:
        handle.write(text)
    return target


class TestRoundTrip:
    def test_defaults_when_no_file_exists(self, prefs):
        assert settings.load() == settings.DEFAULTS
        assert settings.get("tween.mode") == "between"

    def test_set_then_reload_from_disk(self, prefs):
        settings.set("tween.mode", "linear")
        settings.forget()
        assert settings.get("tween.mode") == "linear"

    def test_value_actually_reaches_the_file(self, prefs):
        settings.set("tween.numeric_limit", 1.5)
        with open(os.path.join(str(prefs), settings.FILENAME)) as handle:
            assert json.load(handle)["tween.numeric_limit"] == 1.5

    def test_list_round_trips(self, prefs):
        settings.set("tween.quick_buttons", [-0.5, 0.5])
        settings.forget()
        assert settings.get("tween.quick_buttons") == [-0.5, 0.5]

    def test_update_writes_once(self, prefs):
        settings.update({"tween.mode": "ease", "tween.overshoot": 3.0})
        settings.forget()
        assert settings.get("tween.mode") == "ease"
        assert settings.get("tween.overshoot") == 3.0

    def test_reset_restores_defaults(self, prefs):
        settings.set("tween.mode", "average")
        settings.reset()
        settings.forget()
        assert settings.get("tween.mode") == settings.DEFAULTS["tween.mode"]


class TestCorruptFileFallsBackToDefaults:
    """The design requirement. Every case here must NOT raise."""

    @pytest.mark.parametrize(
        "content",
        [
            "",                                   # empty, e.g. crash mid-write
            "   ",                                # whitespace only
            "{",                                  # truncated
            "not json at all",                    # someone opened it in Notepad
            "[1, 2, 3]",                          # valid JSON, wrong shape
            '"a string"',                         # valid JSON, wrong shape
            "null",                               # valid JSON, wrong shape
            '{"tween.mode": 42}',                 # right key, wrong type
            '{"tween.overshoot": "banana"}',      # right key, wrong type
            '{"tween.quick_buttons": "nope"}',    # right key, wrong type
            '{"tween.quick_buttons": [1, "x"]}',  # bad element inside a list
            '{"unknown.key": 1}',                 # key animkit does not know
        ],
    )
    def test_load_survives(self, prefs, content):
        _write(prefs, content)
        loaded = settings.load(force=True)
        assert loaded == settings.DEFAULTS

    def test_partial_file_keeps_the_good_values(self, prefs):
        """A bad value must not discard the good ones sitting next to it."""
        _write(prefs, '{"tween.mode": "linear", "tween.overshoot": "banana"}')
        loaded = settings.load(force=True)
        assert loaded["tween.mode"] == "linear"
        assert loaded["tween.overshoot"] == settings.DEFAULTS["tween.overshoot"]

    def test_nan_and_infinity_are_rejected(self, prefs):
        """json.loads accepts NaN and Infinity. Every clamp downstream then fails
        open: max(-limit, min(limit, x)) silently returns NaN, and the widget
        ends up with a value no comparison can catch."""
        _write(prefs, '{"tween.overshoot": NaN, "tween.numeric_limit": Infinity}')
        loaded = settings.load(force=True)
        assert loaded["tween.overshoot"] == settings.DEFAULTS["tween.overshoot"]
        assert loaded["tween.numeric_limit"] == settings.DEFAULTS["tween.numeric_limit"]

    def test_bool_is_not_accepted_as_a_number(self, prefs):
        _write(prefs, '{"tween.overshoot": true}')
        assert settings.load(force=True)["tween.overshoot"] == (
            settings.DEFAULTS["tween.overshoot"]
        )

    def test_directory_where_the_file_should_be(self, prefs):
        os.mkdir(os.path.join(str(prefs), settings.FILENAME))
        assert settings.load(force=True) == settings.DEFAULTS

    def test_unwritable_location_does_not_raise(self, tmp_path, monkeypatch):
        missing = tmp_path / "no" / "such" / "place"
        monkeypatch.setattr(settings, "directory", lambda: str(missing))
        monkeypatch.setattr(
            os, "makedirs", lambda *a, **k: (_ for _ in ()).throw(OSError("denied"))
        )
        settings.forget()
        assert settings.save() is False
        assert settings.load() == settings.DEFAULTS
        settings.forget()

    def test_directory_never_raises_even_without_maya(self, prefs, monkeypatch):
        """settings.directory() must survive being called with no Maya at all."""
        monkeypatch.setattr(settings, "directory", settings.directory)
        assert isinstance(settings.directory(), str)


class TestSetRefusesBadInput:
    def test_unknown_key_is_refused(self, prefs):
        assert settings.set("tween.nonsense", 1) is None
        assert "tween.nonsense" not in settings.load()

    def test_wrong_type_is_refused(self, prefs):
        assert settings.set("tween.overshoot", "banana") is None
        assert settings.get("tween.overshoot") == settings.DEFAULTS["tween.overshoot"]

    def test_int_is_accepted_for_a_float_setting(self, prefs):
        assert settings.set("tween.overshoot", 3) == 3.0


class TestAtomicWrite:
    def test_no_temp_file_left_behind(self, prefs):
        settings.set("tween.mode", "ease")
        leftovers = [n for n in os.listdir(str(prefs)) if n.endswith(".tmp")]
        assert leftovers == []

    def test_existing_file_survives_a_failed_write(self, prefs, monkeypatch):
        settings.set("tween.mode", "linear")
        good = open(os.path.join(str(prefs), settings.FILENAME)).read()

        def boom(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(os, "replace", boom)
        settings.set("tween.mode", "ease")

        assert open(os.path.join(str(prefs), settings.FILENAME)).read() == good
