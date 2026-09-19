"""animkit.core.usage -- the local usage log.

Fast tier: usage.py imports no Maya and no Qt, deliberately, so all of this
runs in plain CPython like settings, media and transcode.

The tests that matter most here are the negative ones. This module writes a
file that gets mailed to whoever shipped the build, so "does it record the
operation name" is the easy half; "does it record ANYTHING ELSE" is the half
that would cost somebody their NDA.
"""

import json
import os

import pytest

from animkit.core import settings, usage


@pytest.fixture
def log(tmp_path, monkeypatch):
    """A usage log in a temp dir, with logging on and no cached settings."""
    monkeypatch.setattr(settings, "directory", lambda: str(tmp_path))
    monkeypatch.setattr(settings, "_cache", None, raising=False)
    monkeypatch.setattr(usage, "_session_written", False, raising=False)
    settings.load(force=True)
    settings.set("usage.log", True, write=False)
    return usage


def lines(log):
    with open(log.path()) as handle:
        return [json.loads(line) for line in handle if line.strip()]


# --- the basics -------------------------------------------------------------


def test_records_an_operation(log):
    assert log.record("animkitTweenNext30", result=14) is True
    entry, = lines(log)
    assert entry["op"] == "animkitTweenNext30"
    assert entry["n"] == 14
    assert entry["t"]


def test_summary_counts_and_orders(log):
    for _ in range(3):
        log.record("animkitMirror")
    log.record("animkitFlip")
    assert log.summary() == {"animkitMirror": 3, "animkitFlip": 1}


def test_session_record_is_written_once(log):
    assert log.session_start() is True
    assert log.session_start() is False
    assert len([e for e in lines(log) if e.get("e") == "session"]) == 1


def test_disabled_is_a_no_op(log):
    settings.set("usage.log", False, write=False)
    assert log.record("animkitMirror") is False
    assert not os.path.exists(log.path())


# --- what it must never write down ------------------------------------------


def test_an_exception_records_its_class_not_its_message(log):
    """The message is where rig names live. See the module docstring."""
    secret = "char_hero_L_arm_IK_ctrl"
    log.record("animkitMirror", error=RuntimeError("No object matches name: " + secret))

    entry, = lines(log)
    assert entry["err"] == "RuntimeError"

    with open(log.path()) as handle:
        assert secret not in handle.read()


def test_a_non_int_result_is_dropped_rather_than_repred(log):
    """A repr is exactly how a node name gets into a file that promises none."""
    log.record("animkitMirror", result=["char_hero_L_arm_IK_ctrl"])

    entry, = lines(log)
    assert "n" not in entry

    with open(log.path()) as handle:
        assert "char_hero" not in handle.read()


def test_a_bool_result_is_not_stored_as_a_count(log):
    """bool is an int in Python; '"n": true' would misreport what it returned."""
    log.record("animkitMirror", result=True)
    entry, = lines(log)
    assert "n" not in entry


def test_export_redacts_a_settings_path(log, tmp_path):
    import zipfile

    settings.set("reference.ffmpeg", "C:/Users/someone/shows/unannounced/ffmpeg.exe",
                 write=False)
    log.record("animkitMirror")

    target = log.export(str(tmp_path / "out"))
    assert target and os.path.isfile(target)

    with zipfile.ZipFile(target) as archive:
        shipped = json.loads(archive.read("settings.json").decode("utf-8"))
        assert shipped["reference.ffmpeg"] == "<set>"
        assert "unannounced" not in archive.read("report.txt").decode("utf-8")


# --- it must never be the reason something broke ----------------------------


def test_record_survives_an_unwritable_directory(log, monkeypatch):
    monkeypatch.setattr(settings, "directory", lambda: "\x00 not a path")
    assert log.record("animkitMirror") is False  # no raise


def test_record_survives_an_unserialisable_name(log):
    class Awkward(object):
        def __str__(self):
            raise ValueError("no")

    assert log.record(Awkward()) is False  # no raise


def test_a_torn_line_does_not_cost_the_rest(log):
    log.record("animkitMirror")
    with open(log.path(), "a") as handle:
        handle.write('{"op": "animkitFl')  # a crash mid-write
    log.record("animkitFlip")

    assert log.summary() == {"animkitMirror": 1, "animkitFlip": 1}


def test_report_never_raises_on_an_empty_log(log):
    assert "animkit usage report" in log.report()


# --- rotation ---------------------------------------------------------------


def test_it_rotates_rather_than_growing_without_limit(log, monkeypatch):
    monkeypatch.setattr(usage, "MAX_BYTES", 200)
    for index in range(50):
        log.record("animkitMirror%d" % index)

    assert os.path.isfile(log.rotated_path())
    assert os.path.getsize(log.path()) < 1000
    # Rotated records are still read back, so a count does not reset to zero
    # the moment the file turns over.
    assert len(log.summary()) > 1


# --- the hook ---------------------------------------------------------------


def test_operation_invoke_records_and_returns_untouched(log):
    from animkit.tools.registry import Operation

    op = Operation("animkitThing", "Thing", "does a thing", lambda: 7)
    assert op.invoke() == 7
    assert log.summary() == {"animkitThing": 1}


def test_operation_invoke_reraises_and_records_the_failure(log):
    from animkit.tools.registry import Operation

    def explode():
        raise RuntimeError("No object matches name: char_hero_ctrl")

    op = Operation("animkitBoom", "Boom", "explodes", explode)
    with pytest.raises(RuntimeError):
        op.invoke()

    assert log.failures() == {"animkitBoom": {"RuntimeError": 1}}
    with open(log.path()) as handle:
        assert "char_hero" not in handle.read()


def test_a_broken_log_does_not_break_the_operation(log, monkeypatch):
    """The whole contract: a failure to log is not a failure to operate."""
    from animkit.tools.registry import Operation

    monkeypatch.setattr(usage, "_append", lambda record: 1 / 0)

    op = Operation("animkitThing", "Thing", "does a thing", lambda: 7)
    assert op.invoke() == 7
