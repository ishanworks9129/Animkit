"""DRAG_AND_DROP_INSTALL.py -- the thing a tester actually runs first.

Fast tier, against a stubbed `maya.cmds`. Maya is not booted here on purpose:
what these tests cover is the file's own logic -- where it decides the package
root is, what it writes into the .mod, and which of its dialogs lead to an
install and which do not. None of that needs a Maya, and all of it is the part
that fails on somebody else's machine rather than on this one.

The installer is the one file in this repo with no second chance. An animator
who drags it in and gets a traceback does not drag it in again, and an
evaluation that ends in the first thirty seconds ends there for good.
"""

import importlib.util
import os
import sys
import types

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALLER = os.path.join(REPO, "DRAG_AND_DROP_INSTALL.py")


class FakeCmds(object):
    """Just enough maya.cmds for the installer, and a record of what it did."""

    def __init__(self, app_dir, version=20240000):
        self.app_dir = app_dir
        self.version = version
        self.answers = []      # queued confirmDialog replies, in order
        self.dialogs = []      # (title, first line) of everything shown
        self.ui = set()        # shelf layouts and buttons that "exist"

    # -- the handful the installer calls --
    def internalVar(self, **kwargs):
        if kwargs.get("userAppDir"):
            return self.app_dir + "/"
        if kwargs.get("userPrefDir"):
            return os.path.join(self.app_dir, "prefs") + "/"
        if kwargs.get("userShelfDir"):
            return os.path.join(self.app_dir, "prefs", "shelves") + "/"
        raise AssertionError("unexpected internalVar%r" % (kwargs,))

    def about(self, **kwargs):
        return self.version

    def confirmDialog(self, **kwargs):
        self.dialogs.append(
            (kwargs["title"], kwargs["message"].splitlines()[0]))
        if self.answers:
            return self.answers.pop(0)
        return kwargs["button"][0]

    def fileDialog2(self, **kwargs):
        return None

    def shelfLayout(self, name, **kwargs):
        if kwargs.get("exists"):
            return name in self.ui
        self.ui.add(name)
        return name

    def deleteUI(self, name, **kwargs):
        self.ui.discard(name)

    def setParent(self, *args, **kwargs):
        pass

    def shelfButton(self, **kwargs):
        self.ui.add(kwargs["label"])

    def tabLayout(self, *args, **kwargs):
        pass

    # -- what the tests ask about --
    def shown(self, fragment):
        return any(fragment in line for _, line in self.dialogs)

    @property
    def buttons(self):
        return {name for name in self.ui if name.startswith("animkit")
                and name != "animkit"}


@pytest.fixture
def maya(tmp_path, monkeypatch):
    """A stubbed maya package, installed into sys.modules for the test."""
    app_dir = tmp_path / "maya"
    (app_dir / "prefs" / "shelves").mkdir(parents=True)

    cmds = FakeCmds(str(app_dir))
    mel = types.SimpleNamespace(eval=lambda text: "ShelfTopLevel")

    package = types.ModuleType("maya")
    package.cmds = cmds
    package.mel = mel
    monkeypatch.setitem(sys.modules, "maya", package)
    monkeypatch.setitem(sys.modules, "maya.cmds", cmds)
    monkeypatch.setitem(sys.modules, "maya.mel", mel)
    return cmds


@pytest.fixture
def installer(maya, monkeypatch):
    """The installer, freshly loaded, with animkit.startup() stubbed out.

    Stubbed because startup() registers runTimeCommands and scene callbacks,
    which need a real Maya. What is under test is the installer, not startup.
    """
    fake = types.ModuleType("animkit")
    fake.startup = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "animkit", fake)

    spec = importlib.util.spec_from_file_location("dd_install", INSTALLER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mod_text(installer):
    with open(installer._mod_path()) as handle:
        return handle.read()


# --- finding the package ----------------------------------------------------


def test_finds_the_root_from_the_dropped_file(installer):
    assert installer._root_from(INSTALLER) == REPO


def test_finds_the_root_from_a_file_nested_below_it(installer):
    """So the installer keeps working if it is ever moved into a subfolder."""
    assert installer._root_from(os.path.join(REPO, "a", "b", "x.py")) == REPO


def test_gives_up_rather_than_guessing(installer, tmp_path):
    assert installer._root_from(str(tmp_path / "nothing" / "x.py")) == ""


def test_a_root_without_the_package_is_refused(installer, maya, tmp_path):
    assert installer.install(root=str(tmp_path)) is False
    assert not os.path.exists(installer._mod_path())


# --- the volatile-folder warning --------------------------------------------


@pytest.mark.parametrize("folder", [
    r"C:\Users\a\Downloads\animkit",
    r"C:\Users\a\AppData\Local\Temp\animkit",
    "/home/a/tmp/animkit",
])
def test_volatile_folders_are_flagged(installer, folder):
    assert installer._looks_volatile(folder) is True


@pytest.mark.parametrize("folder", [
    r"C:\pipeline\animkit",
    r"C:\contemporary\animkit",      # substring match would fire here
    "/mnt/share/tools/animkit",
])
def test_ordinary_folders_are_not_flagged(installer, folder):
    assert installer._looks_volatile(folder) is False


def test_cancelling_the_volatile_warning_installs_nothing(installer, maya):
    maya.answers = ["Cancel"]
    assert installer.install(root=r"C:\Users\a\Downloads\animkit") is False
    assert not os.path.exists(installer._mod_path())


# --- what it writes ---------------------------------------------------------


def test_install_writes_a_usable_mod(installer, maya):
    assert installer.install(root=REPO) is True

    text = mod_text(installer)
    # Forward slashes on every platform: Maya's .mod parser wants them.
    assert "+ animkit %s %s" % (installer.VERSION, REPO.replace("\\", "/")) in text
    assert "PYTHONPATH +:= ." in text


def test_it_claims_the_startup_folder_when_there_is_one(installer, maya):
    installer.install(root=REPO)
    assert "MAYA_SCRIPT_PATH +:= startup" in mod_text(installer)


def test_it_does_not_claim_a_startup_folder_that_is_absent(
        installer, maya, tmp_path):
    """A path Maya cannot find is a warning in the tester's log every launch."""
    root = tmp_path / "copy"
    (root / "animkit").mkdir(parents=True)
    (root / "animkit" / "__init__.py").write_text("")

    assert installer.install(root=str(root)) is True
    assert "MAYA_SCRIPT_PATH" not in mod_text(installer)


def test_it_leaves_no_temp_file_behind(installer, maya):
    installer.install(root=REPO)
    assert not os.path.exists(installer._mod_path() + ".tmp")


def test_it_puts_the_root_on_sys_path_for_this_session(installer, maya, monkeypatch):
    monkeypatch.setattr(sys, "path", [p for p in sys.path if p != REPO])
    installer.install(root=REPO)
    assert REPO in sys.path


def test_it_builds_the_whole_shelf(installer, maya):
    installer.install(root=REPO)
    assert maya.buttons == {name for name, _, _, _ in installer.SHELF_BUTTONS}


# --- reinstall and uninstall ------------------------------------------------


def test_reinstalling_asks_first(installer, maya):
    installer.install(root=REPO)
    maya.dialogs = []
    maya.answers = ["Reinstall"]

    assert installer.install(root=REPO) is True
    assert maya.shown("animkit is already installed.")


def test_choosing_uninstall_from_that_prompt_uninstalls(installer, maya):
    installer.install(root=REPO)
    maya.answers = ["Uninstall"]

    installer.install(root=REPO)
    assert not os.path.exists(installer._mod_path())


def test_uninstall_removes_the_mod_and_the_shelf(installer, maya):
    installer.install(root=REPO)
    installer.uninstall()

    assert not os.path.exists(installer._mod_path())
    assert "animkit" not in maya.ui


def test_uninstall_is_idempotent(installer, maya):
    """Dropping the file in twice by mistake must not be an error."""
    assert installer.uninstall() is True
    assert installer.uninstall() is True


# --- the Maya version gate --------------------------------------------------


def test_an_unsupported_maya_warns_but_can_proceed(installer, maya):
    maya.version = 20190000
    maya.answers = ["Install anyway"]

    assert installer.install(root=REPO) is True
    assert maya.shown("This is Maya 2019. animkit is built and verified against")


def test_an_unsupported_maya_can_be_declined(installer, maya):
    maya.version = 20190000
    maya.answers = ["Cancel"]

    assert installer.install(root=REPO) is False
    assert not os.path.exists(installer._mod_path())


def test_a_supported_maya_is_not_questioned(installer, maya):
    assert installer.install(root=REPO) is True
    assert not maya.shown("animkit is built and verified against")


# --- the entry point Maya itself calls --------------------------------------


def test_the_drop_entry_point_installs(installer, maya):
    installer.onMayaDroppedPyFile("whatever/maya/passes/in")
    assert os.path.exists(installer._mod_path())
