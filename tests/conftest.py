"""Test bootstrap.

Two tiers of test in this repo:

  tests/test_blend.py       plain CPython, no Maya, runs in milliseconds
  tests/test_*_maya.py      needs mayapy, builds real scenes

Run the fast tier constantly; run the Maya tier per commit and against every
rig in your test set. The Maya tier auto-skips under plain python so the fast
tier stays runnable anywhere.

NO QApplication IS CREATED HERE, AND THAT IS DELIBERATE
--------------------------------------------------------
Widgets cannot be constructed under mayapy, and it is tempting to fix that:
creating a QApplication *before* `maya.standalone.initialize()` does work, and
would make the panels testable headlessly. It was tried and backed out.

Two reasons. The order is unforgiving -- creating one AFTER standalone has
initialised does not raise, it takes mayapy down with a fatal error and leaves
a crash-recovery `.ma` in the temp directory. And more importantly, two tests
in `test_radial_maya.py` exist precisely BECAUSE this interpreter has only a
QGuiApplication: they pin the trap that `QApplication.instance() is not None`
is the wrong guard. A QApplication here would make both of them pass for the
wrong reason, and that trap cost a day to find.

So the UI is verified by `animkit.selftest.run()` inside a real Maya, and the
headless suite tests what can be tested without a widget -- which is why
`animkit.ui.radial_geom` and `animkit.core.media` exist as separate,
Qt-free modules.
"""

import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

try:
    import maya.standalone  # noqa: F401

    HAS_MAYA = True
except ImportError:
    HAS_MAYA = False

requires_maya = pytest.mark.skipif(
    not HAS_MAYA, reason="needs mayapy; run scripts/run_tests.ps1 -Maya"
)

_initialised = False


@pytest.fixture(scope="session", autouse=True)
def maya_session():
    """Boot maya.standalone once for the whole run, if it is available."""
    global _initialised
    if not HAS_MAYA:
        yield None
        return

    if not _initialised:
        import maya.standalone

        maya.standalone.initialize(name="python")
        _initialised = True

    yield True

    try:
        import maya.standalone

        maya.standalone.uninitialize()
    except Exception:
        pass


@pytest.fixture(scope="session", autouse=True)
def isolated_prefs(tmp_path_factory):
    """Run the whole suite against the SHIPPED defaults, never a real user's.

    `settings.directory()` resolves to Maya's user prefs, so without this the
    suite reads whatever the person running it happens to have saved. That is
    not a theoretical hazard: an animator setting `new drops: pinned to camera`
    in the Ref tab writes `reference.attach: camera`, and a dozen tests that
    say nothing about attachment start failing on a machine where the code is
    perfectly correct. `test_it_lands_where_the_camera_is_looking` already
    carried a hand-rolled guard against exactly this; this makes it the rule.

    It also stops the suite WRITING into those prefs. `settings.set(write=False)`
    still mutates the in-memory cache, so any later `save()` in the same
    process persists whatever a test poked in.

    Session-scoped, so it cannot use `monkeypatch` -- hence the manual restore.
    """
    from animkit.core import settings

    folder = str(tmp_path_factory.mktemp("animkit-prefs"))
    original = settings.directory
    settings.directory = lambda: folder
    settings.forget()
    try:
        yield folder
    finally:
        settings.directory = original
        settings.forget()


@pytest.fixture
def clean_scene(maya_session):
    """A brand new scene per test. Node names go stale otherwise."""
    if not HAS_MAYA:
        pytest.skip("needs mayapy")
    from maya import cmds

    cmds.file(new=True, force=True)
    yield cmds
