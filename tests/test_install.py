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
        self.answers = []        # queued confirmDialog replies, in order
        self.dialogs = []        # (title, message) of everything shown
        self.ui = set()          # shelf layouts and buttons that "exist"
        self.controls = set()    # workspaceControls that "exist"
        self.state_removed = []  # workspaceControlState(remove=True) calls
        self.commands = {}       # runTimeCommand name -> category
        self.option_vars = set()

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
        # The WHOLE message, not its first line: half of what this installer
        # has to tell a tester is in the middle of a paragraph.
        self.dialogs.append((kwargs["title"], kwargs["message"]))
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
        self.controls.discard(name)

    def workspaceControl(self, name, **kwargs):
        if kwargs.get("q") or kwargs.get("query"):
            if kwargs.get("exists"):
                return name in self.controls
            return None
        self.controls.add(name)
        return name

    def workspaceControlState(self, name, **kwargs):
        if kwargs.get("remove"):
            self.state_removed.append(name)
            return True
        return None

    def runTimeCommand(self, name=None, **kwargs):
        query = kwargs.get("q") or kwargs.get("query")
        if query and kwargs.get("userCommandArray"):
            return sorted(self.commands)
        if query and kwargs.get("category"):
            return self.commands.get(name, "")
        if query and kwargs.get("exists"):
            return name in self.commands
        if kwargs.get("delete"):
            self.commands.pop(name, None)
            return True
        self.commands[name] = kwargs.get("category", "")
        return name

    def optionVar(self, **kwargs):
        if kwargs.get("q") or kwargs.get("query"):
            if kwargs.get("list"):
                return sorted(self.option_vars)
            return None
        target = kwargs.get("remove")
        if target:
            self.option_vars.discard(target)
            return True
        return None

    def setParent(self, *args, **kwargs):
        pass

    def shelfButton(self, **kwargs):
        self.ui.add(kwargs["label"])

    def tabLayout(self, *args, **kwargs):
        pass

    # -- what the tests ask about --
    def shown(self, fragment):
        return any(fragment in message for _, message in self.dialogs)

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
def fake_animkit(monkeypatch):
    """A stand-in animkit package: startup(), settings and transcode.

    Stubbed because startup() registers runTimeCommands and installs scene
    callbacks, which need a real Maya. What is under test is the installer.

    `transcode.is_available` is a plain attribute so a test can flip it --
    that one boolean is the difference between a tester whose dropped video
    works and one who gets a blank image plane.
    """
    package = types.ModuleType("animkit")
    package.__path__ = []
    package.startup = lambda *args, **kwargs: None
    package.armed = []
    package.install_viewport_drop = lambda: package.armed.append(True)

    core = types.ModuleType("animkit.core")
    core.__path__ = []

    settings = types.ModuleType("animkit.core.settings")
    settings.stored = {}
    settings.get = lambda key, default=None: settings.stored.get(key, "")
    settings.set = lambda key, value, write=True: settings.stored.__setitem__(key, value)

    transcode = types.ModuleType("animkit.core.transcode")
    transcode.is_available = lambda override=None: True

    ui = types.ModuleType("animkit.ui")
    ui.__path__ = []
    strip = types.ModuleType("animkit.ui.strip")
    strip.shown = []
    strip.show = lambda: strip.shown.append(True)

    core.settings = settings
    core.transcode = transcode
    ui.strip = strip
    package.core = core
    package.ui = ui

    for name, module in (("animkit", package),
                         ("animkit.core", core),
                         ("animkit.core.settings", settings),
                         ("animkit.core.transcode", transcode),
                         ("animkit.ui", ui),
                         ("animkit.ui.strip", strip)):
        monkeypatch.setitem(sys.modules, name, module)

    # One handle for the tests: .is_available, .strip, .settings, .package.
    transcode.strip = strip
    transcode.settings = settings
    transcode.package = package
    return transcode


@pytest.fixture
def installer(maya, fake_animkit):
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
    installer.uninstall(purge=False)

    assert not os.path.exists(installer._mod_path())
    assert "animkit" not in maya.ui


def test_uninstall_is_idempotent(installer, maya):
    """Dropping the file in twice by mistake must not be an error."""
    assert installer.uninstall(purge=False) is True
    assert installer.uninstall(purge=False) is True


# --- the panels, which are the part that follows Maya into the next session --


def test_the_control_list_covers_every_panel_animkit_can_open():
    """The installer hardcodes the workspaceControl names, because uninstall
    has to work when the package is already unreachable. This is what stops
    that list drifting away from the modules it mirrors."""
    import re

    ui_dir = os.path.join(REPO, "animkit", "ui")
    defined = set()
    for name in os.listdir(ui_dir):
        if not name.endswith(".py"):
            continue
        with open(os.path.join(ui_dir, name)) as handle:
            match = re.search(r"^CONTROL_NAME\s*=\s*['\"]([^'\"]+)['\"]",
                              handle.read(), re.M)
        if match:
            defined.add(match.group(1))

    assert defined, "no CONTROL_NAME found -- has the UI layout changed?"

    spec = importlib.util.spec_from_file_location("dd_probe", INSTALLER)
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)

    missing = defined - set(probe.CONTROLS)
    assert not missing, (
        "these panels would survive an uninstall and have Maya rebuild them "
        "from a deleted module at every launch: %s" % ", ".join(sorted(missing)))


def test_uninstall_deletes_the_panels_and_their_saved_state(installer, maya):
    """Retained controls are rebuilt at launch by running their uiScript. Left
    behind, they point Maya at a module that is no longer there."""
    maya.controls.update(("animkitStrip", "animkitPanel"))

    installer.uninstall(purge=False)

    assert maya.controls == set()
    # State is purged for every control, present or not: the saved state is
    # precisely the thing that outlives the control.
    assert set(maya.state_removed) == set(installer.CONTROLS)


def test_uninstall_purges_state_even_with_no_control_open(installer, maya):
    installer.uninstall(purge=False)
    assert set(maya.state_removed) == set(installer.CONTROLS)


# --- the traces Maya writes into its own prefs and keeps forever ------------


def test_uninstall_deletes_the_runtime_commands(installer, maya):
    """They are registered default=False, so Maya writes every one into
    prefs/userRunTimeCommands.mel at exit and brings them back next launch --
    pointing at a package that is no longer there."""
    maya.commands = {
        "animkitShow": installer.COMMAND_CATEGORY,
        "animkitTweenNext30": installer.COMMAND_CATEGORY,
        "myOwnCommand": "Custom Scripts",
    }

    installer.uninstall(purge=False)

    assert set(maya.commands) == {"myOwnCommand"}, "somebody else's command went"


def test_it_finds_them_by_category_even_if_renamed(installer, maya):
    """The category is the reliable marker; the name prefix is the backstop."""
    maya.commands = {"somethingElse": installer.COMMAND_CATEGORY}
    installer.uninstall(purge=False)
    assert maya.commands == {}


def test_it_finds_them_by_name_even_if_the_category_is_gone(installer, maya):
    maya.commands = {"animkitStripShow": ""}
    installer.uninstall(purge=False)
    assert maya.commands == {}


def test_uninstall_deletes_the_saved_panel_positions(installer, maya):
    """animkitPanelState and friends live in userPrefs.mel and outlive
    everything else."""
    maya.option_vars = {"animkitPanelState", "animkitTweenControlState",
                        "somebodyElsesPref"}

    installer.uninstall(purge=False)

    assert maya.option_vars == {"somebodyElsesPref"}


def test_the_command_sweep_does_not_need_animkit_to_be_importable(
        installer, maya, monkeypatch):
    """The case that most needs cleaning is the one where the folder is
    already gone, so this must not go through animkit.commands."""
    for name in list(sys.modules):
        if name == "animkit" or name.startswith("animkit."):
            monkeypatch.delitem(sys.modules, name, raising=False)

    maya.commands = {"animkitShow": installer.COMMAND_CATEGORY}
    assert installer._remove_runtime_commands() == ["animkitShow"]


# --- settings and the usage log are a separate question ---------------------


def test_uninstall_keeps_the_log_by_default(installer, maya):
    """It is the tester's own data, and the thing they were going to send."""
    prefs = installer._prefs_dir()
    os.makedirs(prefs)
    open(os.path.join(prefs, "usage.jsonl"), "w").close()

    maya.answers = ["Keep them"]
    installer.uninstall()

    assert os.path.isdir(prefs)
    assert maya.shown("Remove animkit's settings and usage log as well?")


def test_uninstall_can_purge_everything(installer, maya):
    prefs = installer._prefs_dir()
    os.makedirs(prefs)
    open(os.path.join(prefs, "usage.jsonl"), "w").close()

    maya.answers = ["Remove everything"]
    installer.uninstall()

    assert not os.path.exists(prefs)


def test_cancelling_the_purge_prompt_uninstalls_nothing(installer, maya):
    installer.install(root=REPO)
    os.makedirs(installer._prefs_dir(), exist_ok=True)

    maya.answers = ["Cancel"]
    assert installer.uninstall() is False
    assert os.path.exists(installer._mod_path()), "the .mod should survive Cancel"


def test_it_does_not_ask_when_there_is_nothing_to_purge(installer, maya):
    installer.uninstall()
    assert not maya.shown("Remove animkit's settings and usage log")


def test_uninstall_tells_you_to_restart_before_reinstalling(installer, maya):
    """A reinstall in the same session re-uses the modules Maya still holds,
    so a 'clean' retest would not be one."""
    installer.uninstall(purge=False)
    assert maya.shown("RESTART MAYA before installing again")


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


# --- what the animator sees when the drop finishes --------------------------
#
# An installer that ends with a dialog and no visible tool has told somebody
# that something happened somewhere and left them to go looking. The strip is
# opened as part of installing, which is not animkit opening itself uninvited
# -- the invitation was dragging the installer into the viewport.


def test_installing_opens_the_strip(installer, maya, fake_animkit):
    installer.install(root=REPO)
    assert fake_animkit.strip.shown == [True]


def test_installing_asks_for_the_strip_on_every_launch(installer, maya, fake_animkit):
    installer.install(root=REPO)
    assert fake_animkit.settings.stored.get("ui.open_at_startup") == "strip"


def test_the_dialog_says_it_changed_that_setting(installer, maya, fake_animkit):
    """A preference somebody has to discover was changed for them is exactly
    the surprise the no-uninvited-UI rule exists to prevent."""
    installer.install(root=REPO)

    final = [message for title, message in maya.dialogs
             if title == "animkit installed"][-1]
    assert "open with Maya from now on" in final
    assert 'settings.set("ui.open_at_startup", "")' in final, (
        "the dialog must say how to undo it")


def test_installing_arms_the_viewport_for_dropped_media(installer, maya, fake_animkit):
    """Otherwise the first thing a tester does -- drag a video onto the
    viewport, because the README says to -- falls through to Maya, which tries
    to open the .mp4 as a scene and prints "No translator found"."""
    installer.install(root=REPO)
    assert fake_animkit.package.armed == [True]


def test_a_viewport_drop_failure_is_not_reported_as_a_strip_failure(
        installer, maya, fake_animkit):
    """These two shared a try block once, so a failure arming the viewport
    announced "The strip could not be opened" over a strip that was open and
    working. A wrong diagnosis costs more than the missing feature does."""
    def explode():
        raise RuntimeError("no model panels")

    fake_animkit.package.install_viewport_drop = explode

    assert installer.install(root=REPO) is True
    final = [message for title, message in maya.dialogs
             if title == "animkit installed"][-1]
    assert "could not be opened" not in final
    assert "docked above the time slider" in final
    assert fake_animkit.strip.shown == [True]


def test_a_strip_that_will_not_open_does_not_fail_the_install(
        installer, maya, fake_animkit):
    def explode():
        raise RuntimeError("no Qt")

    fake_animkit.strip.show = explode

    assert installer.install(root=REPO) is True
    final = [message for title, message in maya.dialogs
             if title == "animkit installed"][-1]
    assert "could not be opened" in final
    assert '"strip" button on the shelf' in final


def test_the_setting_failing_still_leaves_the_strip_open(
        installer, maya, fake_animkit):
    def explode(key, value, write=True):
        raise RuntimeError("read-only prefs")

    fake_animkit.settings.set = explode

    assert installer.install(root=REPO) is True
    assert fake_animkit.strip.shown == [True]
    final = [message for title, message in maya.dialogs
             if title == "animkit installed"][-1]
    assert "docked above the time slider" in final


# --- what a dropped video will actually do ----------------------------------
#
# This build ships no ffmpeg, so on most tester machines video and mp3
# reference do NOT work while photos and image sequences do. The install
# dialog has to say so, because the alternative is a tester dropping an mp4,
# getting a blank image plane, and reporting the feature as broken.


def test_it_says_so_when_ffmpeg_is_missing(installer, maya, fake_animkit):
    fake_animkit.is_available = lambda override=None: False

    note = installer._media_note()
    assert "NOT working" in note
    assert "Photo and image-sequence reference: working." in note
    # The fix, in the same breath as the problem.
    assert "ffmpeg.exe" in note and "PATH" in note


def test_it_says_nothing_alarming_when_ffmpeg_is_there(installer, fake_animkit):
    fake_animkit.is_available = lambda override=None: True
    assert installer._media_note() == "Video, image and audio reference: all working."


def test_the_note_reaches_the_install_dialog(installer, maya, fake_animkit):
    """Not just printed to the Script Editor, which nobody has open yet."""
    fake_animkit.is_available = lambda override=None: False
    installer.install(root=REPO)

    final = [message for title, message in maya.dialogs
             if title == "animkit installed"]
    assert final, "no install-confirmation dialog was shown"
    assert "VIDEO and MP3 reference: NOT working" in final[-1]


def test_a_missing_ffmpeg_never_blocks_the_install(installer, maya, fake_animkit):
    """A reference feature that cannot run is not a reason to refuse to
    install the other fifty operations."""
    fake_animkit.is_available = lambda override=None: False
    assert installer.install(root=REPO) is True
    assert os.path.exists(installer._mod_path())


def test_a_broken_transcode_module_does_not_break_the_install(
        installer, maya, fake_animkit):
    def explode(override=None):
        raise RuntimeError("no")

    fake_animkit.is_available = explode
    assert installer._media_note() == ""
    assert installer.install(root=REPO) is True


# --- the entry point Maya itself calls --------------------------------------


def test_the_drop_entry_point_installs(installer, maya):
    installer.onMayaDroppedPythonFile("pSphere1")   # Maya passes the hit object
    assert os.path.exists(installer._mod_path())


def test_the_drop_entry_point_reports_success_to_maya(installer, maya):
    """Maya uses the return value as the drop's success flag."""
    assert installer.onMayaDroppedPythonFile("pSphere1") is True


def test_the_drop_entry_point_takes_maya_s_single_argument(installer):
    """Maya calls it as onMayaDroppedPythonFile(obj) -- one positional."""
    import inspect

    signature = inspect.signature(installer.onMayaDroppedPythonFile)
    signature.bind("pSphere1")          # raises TypeError if it would not take it


# The previous version of this test called `onMayaDroppedPyFile`, which is not
# a name Maya has ever looked for -- so it asserted our own mistake back at us
# and passed while a real drop did nothing but print a warning. These two read
# the answer out of the installed Maya instead.

def _maya_drop_function_names():
    """Every MY_DROP_FUNC in every Maya installed on this machine.

    Parsed out of the file rather than imported, because importing it pulls in
    maya.cmds and this is the fast tier.
    """
    import glob
    import re

    found = {}
    pattern = os.path.join(
        r"C:\Program Files\Autodesk", "Maya*", "Python*", "Lib", "site-packages",
        "maya", "app", "general", "executeDroppedPythonFile.py")
    for path in glob.glob(pattern):
        with open(path) as handle:
            match = re.search(r"MY_DROP_FUNC\s*=\s*['\"]([^'\"]+)['\"]",
                              handle.read())
        if match:
            found[path.split(os.sep)[3]] = match.group(1)
    return found


def test_the_constant_matches_what_this_machine_s_maya_looks_for():
    names = _maya_drop_function_names()
    if not names:
        pytest.skip("no Maya installation found to check against")
    assert set(names.values()) == {"onMayaDroppedPythonFile"}, names


def test_maya_s_own_loading_procedure_finds_the_drop_function(monkeypatch):
    """Reproduce executeDroppedPythonFile() exactly, minus Maya.

    Maya does not exec the dropped file, it imports it by FILENAME STEM and
    then looks for the drop function on the result. Loading it any other way
    -- as the fixture above does, under a tidy module name -- tests a code
    path a real drop never takes. This is the closest thing to the real
    mechanism that runs without a Maya, and it is what the rest of this file
    was missing.
    """
    import importlib

    directory, base = os.path.split(INSTALLER)
    module_name = os.path.splitext(base)[0]

    monkeypatch.syspath_prepend(directory)
    monkeypatch.delitem(sys.modules, module_name, raising=False)

    loaded = importlib.import_module(module_name)
    try:
        assert hasattr(loaded, "onMayaDroppedPythonFile"), (
            "Maya imports %s and calls onMayaDroppedPythonFile() on it; "
            "without that name the drop prints a warning and does nothing"
            % module_name)
    finally:
        sys.modules.pop(module_name, None)


def test_the_installer_defines_exactly_that_function(installer):
    names = _maya_drop_function_names()
    if not names:
        pytest.skip("no Maya installation found to check against")

    for version, wanted in names.items():
        assert hasattr(installer, wanted), (
            "Maya %s calls %s() and the installer does not define it -- a drop "
            "would print 'does not contain drop function' and do nothing"
            % (version, wanted))
    assert installer.DROP_FUNCTION in set(names.values())
