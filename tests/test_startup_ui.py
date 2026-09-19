"""animkit.open_startup_ui -- opening a panel when Maya starts.

Fast tier. The UI modules are stubbed, so none of this needs Maya or Qt; what
is under test is the decision, not the widgets.

The contract that matters: this runs inside userSetup.py's deferred call on
every Maya launch of every machine animkit is installed on. Whatever it does
when the setting is empty, malformed, or names a panel that will not open, it
must not be the reason somebody's Maya came up wrong.
"""

import sys
import types

import pytest

import animkit
from animkit.core import settings


@pytest.fixture
def ui(tmp_path, monkeypatch):
    """Stubbed strip and panel modules that record show() calls."""
    monkeypatch.setattr(settings, "directory", lambda: str(tmp_path))
    monkeypatch.setattr(settings, "_cache", None, raising=False)
    settings.load(force=True)

    shown = []

    def fake(name):
        module = types.ModuleType(name)
        module.show = lambda: shown.append(name)
        monkeypatch.setitem(sys.modules, name, module)
        return module

    fake("animkit.ui.strip")
    fake("animkit.ui.panel")
    return shown


def test_nothing_is_opened_by_default(ui):
    """The default must be silence. A tool that plants itself in the layout
    of every animator who installs it is a tool the lead uninstalls."""
    assert settings.get("ui.open_at_startup") == ""
    assert animkit.open_startup_ui() == ()
    assert ui == []


def test_strip_opens_the_strip(ui):
    settings.set("ui.open_at_startup", "strip", write=False)
    assert animkit.open_startup_ui() == ("animkit.ui.strip",)
    assert ui == ["animkit.ui.strip"]


def test_panel_opens_the_panel(ui):
    settings.set("ui.open_at_startup", "panel", write=False)
    animkit.open_startup_ui()
    assert ui == ["animkit.ui.panel"]


def test_both_opens_both(ui):
    settings.set("ui.open_at_startup", "both", write=False)
    animkit.open_startup_ui()
    assert ui == ["animkit.ui.strip", "animkit.ui.panel"]


@pytest.mark.parametrize("written", ["Strip", " STRIP ", "sTrIp"])
def test_case_and_whitespace_are_forgiven(ui, written):
    """It is a value somebody types into a JSON file by hand."""
    settings.set("ui.open_at_startup", written, write=False)
    animkit.open_startup_ui()
    assert ui == ["animkit.ui.strip"]


def test_an_unknown_value_opens_nothing_and_does_not_raise(ui):
    settings.set("ui.open_at_startup", "timeline", write=False)
    assert animkit.open_startup_ui() == ()
    assert ui == []


def test_one_panel_failing_does_not_cost_the_other(ui, monkeypatch):
    def explode():
        raise RuntimeError("no Qt today")

    monkeypatch.setattr(sys.modules["animkit.ui.strip"], "show", explode)
    settings.set("ui.open_at_startup", "both", write=False)

    assert animkit.open_startup_ui() == ("animkit.ui.panel",)
    assert ui == ["animkit.ui.panel"]


def test_a_broken_settings_read_does_not_raise(ui, monkeypatch):
    """This runs on every Maya launch. It never gets to be the reason one
    failed."""
    monkeypatch.setattr(settings, "get", lambda *a, **k: 1 / 0)
    assert animkit.open_startup_ui() == ()


def test_no_ui_module_is_imported_when_the_setting_is_empty(monkeypatch, tmp_path):
    """Importing Qt on every Maya launch to then open nothing would make
    every animator's startup slower for nobody's benefit."""
    monkeypatch.setattr(settings, "directory", lambda: str(tmp_path))
    monkeypatch.setattr(settings, "_cache", None, raising=False)
    settings.load(force=True)

    imported = []
    real = __import__

    def watching(name, *args, **kwargs):
        if name.startswith("animkit.ui"):
            imported.append(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", watching)
    animkit.open_startup_ui()
    assert imported == []


def test_the_userSetup_calls_it_after_startup():
    """Order is not decoration: a panel must not be built before the
    runTimeCommands its buttons carry have been registered."""
    import os
    import re

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "startup", "userSetup.py")) as handle:
        source = handle.read()

    assert re.search(r"animkit\.startup\(\).*animkit\.open_startup_ui\(\)",
                     source, re.S), "open_startup_ui must follow startup()"


# --- arming the viewport ----------------------------------------------------
#
# "drag a video onto the viewport" is a headline feature whose event filter had
# exactly one caller: the Ref tab building. Until somebody opened that one tab,
# a dropped .mp4 fell through to Maya, which tried to open it as a scene file
# and said "No translator found" -- which reads as the feature being broken.


@pytest.fixture
def drop(monkeypatch):
    """Stub viewport_drop in BOTH places `from ... import` can find it.

    `from animkit.ui import viewport_drop` reads the ATTRIBUTE off the
    package when the real module has already been imported, and only falls
    back to sys.modules when it has not. Stubbing sys.modules alone therefore
    works in the fast tier, where nothing else imports it, and silently does
    nothing under mayapy, where another test imported it first -- a test that
    passes or fails on the order the suite happens to run in. animkit.ui is
    an empty __init__, so importing it here costs nothing.
    """
    import animkit.ui

    module = types.ModuleType("animkit.ui.viewport_drop")
    module.calls = []
    module.install_if_wanted = lambda: module.calls.append(True) or ["modelPanel4"]

    monkeypatch.setitem(sys.modules, "animkit.ui.viewport_drop", module)
    monkeypatch.setattr(animkit.ui, "viewport_drop", module, raising=False)
    return module


def test_it_arms_the_viewport(drop):
    assert animkit.install_viewport_drop() == ["modelPanel4"]
    assert drop.calls == [True]


def test_it_goes_through_install_if_wanted_so_the_setting_still_wins(drop):
    """Not install() directly -- reference.viewport_drop has to keep meaning
    what it says."""
    animkit.install_viewport_drop()
    assert drop.calls, "install_if_wanted was not the entry point"


def test_a_maya_with_nothing_to_hook_does_not_raise(drop):
    def explode():
        raise RuntimeError("no model panels")

    drop.install_if_wanted = explode
    assert animkit.install_viewport_drop() == []


def test_the_userSetup_arms_it_after_startup():
    import os
    import re

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "startup", "userSetup.py")) as handle:
        source = handle.read()

    assert re.search(
        r"animkit\.startup\(\).*animkit\.install_viewport_drop\(\)",
        source, re.S), "install_viewport_drop must follow startup()"
