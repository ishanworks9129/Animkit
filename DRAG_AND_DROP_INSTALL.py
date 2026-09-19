r"""animkit -- drag-and-drop installer for Autodesk Maya.

HOW TO USE
    1. Put this folder somewhere PERMANENT first. The installer points Maya at
       the folder it is sitting in; it does not copy the code anywhere. A
       Downloads or temp folder that gets cleaned out later takes animkit with
       it.
    2. Start Maya (2022-2026).
    3. Drag THIS FILE out of Explorer / Finder and drop it into a Maya 3D
       viewport.

WHAT IT DOES, EXACTLY
    - writes one file:   <maya prefs>/modules/animkit.mod
    - creates one shelf: "animkit", with four buttons
    - imports animkit and calls animkit.startup() in the running session, so
      there is nothing to restart

    That is the whole footprint. No files outside the Maya preferences folder,
    no registry keys, no admin rights, no network access, no environment
    variables set behind your back, nothing written into the Maya install.

TO UNINSTALL
    Drop this file in again and pick "Uninstall". It deletes the .mod and the
    shelf, and nothing else.

WHY A .mod AND NOT A COPY
    animkit is pure Python and ships as a folder. A Maya module file is one
    line of text pointing at that folder, which makes an update "replace the
    folder" rather than "run the installer again". It is also the deployment a
    studio will actually want -- see modules/animkit.mod.
"""

import os
import sys

MODULE_NAME = "animkit"
VERSION = "0.1.0"
SHELF = "animkit"

#: Versions this has been built and verified against. Outside the range the
#: installer warns and then lets you proceed anyway -- Maya's Python and Qt
#: are the only things that matter here, and both move slowly.
SUPPORTED = (2022, 2026)

#: (button name, tooltip, python command, overlay text)
SHELF_BUTTONS = (
    ("animkitPanel", "animkit -- the dockable panel: Tween, Keys, Pose, Sets, Ref",
     "import animkit.ui.panel as m; m.show()", "anim"),
    ("animkitStrip", "animkit -- the strip, docked above the time slider",
     "import animkit.ui.strip as m; m.show()", "strip"),
    ("animkitHelp", "animkit -- every operation and the key it is on right now",
     "import animkit.ui.help_ui as m; m.show()", "help"),
    ("animkitSelfTest", "animkit -- run the self-test, print a PASS/FAIL table",
     "import animkit.selftest as st; st.run()", "test"),
    ("animkitFeedback",
     "animkit -- export a feedback bundle to send back: the local usage log, "
     "a readable report and your settings. Nothing is sent anywhere; it "
     "writes a zip and shows it to you.",
     "import animkit.core.usage as u; u.export_and_reveal()", "send"),
)

#: Folder names that get emptied, and so make a bad install location.
VOLATILE = ("temp", "tmp", "downloads", "recycle")


# ---------------------------------------------------------------------------
# Finding ourselves
#
# Maya executes a dropped .py file and then calls onMayaDroppedPyFile(). What
# it does NOT reliably do across 2022-2026 is define __file__ while doing it,
# so there are three answers to "where am I" here and the last one asks.
# ---------------------------------------------------------------------------

def _this_file():
    try:
        path = __file__
    except NameError:
        path = ""
    if not path:
        import inspect
        try:
            path = inspect.getfile(inspect.currentframe())
        except Exception:
            path = ""
    return os.path.abspath(path) if path else ""


def _root_from(path):
    """Walk up from `path` until a folder holds the animkit package."""
    folder = os.path.dirname(path) if path else ""
    for _ in range(4):
        if not folder:
            break
        if os.path.isfile(os.path.join(folder, MODULE_NAME, "__init__.py")):
            return folder
        parent = os.path.dirname(folder)
        if parent == folder:
            break
        folder = parent
    return ""


def _root_by_asking():
    from maya import cmds
    picked = cmds.fileDialog2(
        fileMode=3,
        caption="animkit: pick the folder that contains the 'animkit' folder",
        okCaption="Install from here",
    )
    if not picked:
        return ""
    folder = picked[0]
    if os.path.isfile(os.path.join(folder, MODULE_NAME, "__init__.py")):
        return folder
    return ""


# ---------------------------------------------------------------------------
# The module file
# ---------------------------------------------------------------------------

def _modules_dir():
    """<maya prefs>/modules -- version-less, so one install serves 2022-2026.

    internalVar(userAppDir=True) is the only portable way to ask this. It is
    Documents/maya on Windows, Library/Preferences/Autodesk/maya on macOS and
    ~/maya on Linux, and hardcoding it wrong is the single most common way a
    Maya installer fails on somebody else's machine.
    """
    from maya import cmds
    return os.path.join(cmds.internalVar(userAppDir=True), "modules")


def _mod_path():
    return os.path.join(_modules_dir(), "%s.mod" % MODULE_NAME)


def _write_mod(root):
    folder = _modules_dir()
    if not os.path.isdir(folder):
        os.makedirs(folder)

    # Only claim the startup folder if it is actually there. Pointing
    # MAYA_SCRIPT_PATH at a folder that does not exist earns a warning in the
    # tester's log at every launch, and the first thing anyone evaluating a
    # tool does is read the log.
    script_line = ""
    if os.path.isfile(os.path.join(root, "startup", "userSetup.py")):
        script_line = "MAYA_SCRIPT_PATH +:= startup\n"

    # Maya's .mod parser wants forward slashes on every platform.
    text = (
        "# Written by animkit's drag-and-drop installer.\n"
        "#\n"
        "# This file is one pointer and nothing else. animkit itself lives in\n"
        "# the folder named below -- MOVE OR DELETE THAT FOLDER AND animkit\n"
        "# STOPS LOADING. To update, replace the folder's contents; to move\n"
        "# it, edit the path on the '+ animkit' line or run the installer\n"
        "# again from the new location.\n"
        "#\n"
        "# To uninstall by hand: delete this file and restart Maya.\n"
        "\n"
        "+ %s %s %s\n"
        "PYTHONPATH +:= .\n"
        "%s"
    ) % (MODULE_NAME, VERSION, root.replace("\\", "/"), script_line)

    path = _mod_path()
    # Write beside the target, then replace. A half-written .mod is a Maya
    # that logs a module error at every launch until somebody finds this
    # file, which is a poor first impression for a tool being evaluated.
    tmp = path + ".tmp"
    handle = open(tmp, "w")
    try:
        handle.write(text)
    finally:
        handle.close()
    if os.path.exists(path):
        os.remove(path)
    os.rename(tmp, path)
    return path


# ---------------------------------------------------------------------------
# The shelf
# ---------------------------------------------------------------------------

def _build_shelf():
    """Create (or rebuild) the animkit shelf. Never touches another shelf."""
    from maya import cmds, mel

    top = mel.eval("$animkitTmp = $gShelfTopLevel")
    if cmds.shelfLayout(SHELF, exists=True):
        cmds.deleteUI(SHELF, layout=True)
    cmds.setParent(top)
    cmds.shelfLayout(SHELF)

    for name, tooltip, command, overlay in SHELF_BUTTONS:
        cmds.shelfButton(
            parent=SHELF,
            label=name,
            annotation=tooltip,
            command=command,
            sourceType="python",
            # pythonFamily.png ships with every Maya, so the button cannot
            # come up blank. animkit's own icons are drawn in code and there
            # is no PNG on disk to point at here.
            image="pythonFamily.png",
            image1="pythonFamily.png",
            imageOverlayLabel=overlay,
            overlayLabelColor=(0.85, 0.72, 0.35),
            overlayLabelBackColor=(0.0, 0.0, 0.0, 0.5),
            noDefaultPopup=True,
        )

    try:
        cmds.tabLayout(top, edit=True, selectTab=SHELF)
    except Exception:
        pass

    # Persist now rather than at Maya exit -- a tester who crashes Maya while
    # evaluating should not also lose the shelf.
    try:
        mel.eval("saveAllShelves $gShelfTopLevel;")
    except Exception:
        pass


def _remove_shelf():
    """Return True if there was actually a shelf to remove."""
    from maya import cmds

    found = False
    if cmds.shelfLayout(SHELF, exists=True):
        cmds.deleteUI(SHELF, layout=True)
        found = True
    saved = os.path.join(
        cmds.internalVar(userShelfDir=True), "shelf_%s.mel" % SHELF)
    if os.path.isfile(saved):
        os.remove(saved)
        found = True
    return found


# ---------------------------------------------------------------------------
# Install / uninstall
# ---------------------------------------------------------------------------

def _maya_version():
    from maya import cmds
    try:
        return int(cmds.about(apiVersion=True) // 10000)
    except Exception:
        return 0


def _looks_volatile(root):
    parts = [part.lower() for part in root.replace("\\", "/").split("/")]
    return any(part in VOLATILE for part in parts)


def _dialog(title, message, buttons, default=None):
    from maya import cmds
    return cmds.confirmDialog(
        title=title,
        message=message,
        messageAlign="left",
        button=list(buttons),
        defaultButton=default or buttons[0],
        cancelButton=buttons[-1],
        dismissString=buttons[-1],
    )


def install(root=None):
    """Point Maya at `root`, and light animkit up in this session."""
    root = root or _root_from(_this_file()) or _root_by_asking()

    # Checked even when the caller passed `root` outright. Both paths that
    # WORK OUT the root already validate it, so this only fires for an
    # explicit argument -- and the cost of trusting that argument is a .mod
    # pointing at a folder with no package in it, which Maya reports as a
    # module error at every launch from then on. Better to refuse here than
    # to leave that behind on somebody else's machine.
    if not root or not os.path.isfile(
            os.path.join(root, MODULE_NAME, "__init__.py")):
        _dialog("animkit",
                "Could not find the animkit package%s.\n\n"
                "Drop this file from inside the unzipped animkit folder --\n"
                "it has to sit next to the 'animkit' folder itself."
                % (" in\n\n    %s" % root if root else ""),
                ["OK"])
        return False

    version = _maya_version()
    if version and not (SUPPORTED[0] <= version <= SUPPORTED[1]):
        answer = _dialog(
            "animkit",
            "This is Maya %d. animkit is built and verified against Maya\n"
            "%d-%d.\n\n"
            "It may well work -- only Maya's Python and Qt versions matter --\n"
            "but it is untested here." % ((version,) + SUPPORTED),
            ["Install anyway", "Cancel"], "Install anyway")
        if answer != "Install anyway":
            return False

    if _looks_volatile(root):
        answer = _dialog(
            "animkit",
            "animkit would be installed from:\n\n    %s\n\n"
            "That looks like a temporary or Downloads folder. Maya is pointed\n"
            "AT this folder rather than given a copy of it, so if it is\n"
            "cleaned out, animkit stops loading.\n\n"
            "Move the folder somewhere permanent and drop this file again --\n"
            "or continue, if you know it will stay put." % root,
            ["Continue anyway", "Cancel"], "Cancel")
        if answer != "Continue anyway":
            return False

    if os.path.isfile(_mod_path()):
        answer = _dialog(
            "animkit",
            "animkit is already installed.\n\n    %s\n\n"
            "Reinstall points Maya at the folder this file is in.\n"
            "Uninstall removes the module file and the shelf." % _mod_path(),
            ["Reinstall", "Uninstall", "Cancel"], "Reinstall")
        if answer == "Cancel":
            return False
        if answer == "Uninstall":
            return uninstall()

    try:
        mod = _write_mod(root)
    except Exception:
        import traceback
        traceback.print_exc()
        _dialog("animkit",
                "Could not write the module file:\n\n    %s\n\n"
                "See the Script Editor for the error." % _mod_path(), ["OK"])
        return False

    # The .mod is read at Maya startup, so this session needs the path adding
    # by hand. Doing that here is what makes the install work without a
    # restart.
    if root not in sys.path:
        sys.path.append(root)

    try:
        import animkit
        animkit.startup()
    except Exception:
        import traceback
        traceback.print_exc()
        _dialog("animkit",
                "The module file was written, but animkit failed to start in\n"
                "this session. See the Script Editor.\n\n"
                "Restarting Maya is worth trying before reporting it.", ["OK"])
        return False

    try:
        _build_shelf()
        shelf_note = 'An "animkit" shelf has been added.'
    except Exception:
        import traceback
        traceback.print_exc()
        shelf_note = ("The shelf could not be created -- see the Script\n"
                      "Editor. Everything else installed fine; open the panel\n"
                      "with:  import animkit.ui.panel as m; m.show()")

    print("animkit %s installed" % VERSION)
    print("  module file : %s" % mod)
    print("  code        : %s" % root)

    _dialog(
        "animkit installed",
        "animkit %s is installed and running in this session.\n\n"
        "%s\n\n"
        "Code   : %s\n"
        "Module : %s\n\n"
        "Start with the panel button -- or the self-test button first. It\n"
        "creates and deletes its own temporary nodes, touches nothing you\n"
        "selected, and prints a PASS/FAIL table. Run it twice; the two runs\n"
        "should match.\n\n"
        "Nothing else on this machine was touched." % (
            VERSION, shelf_note, root, mod),
        ["OK"])
    return True


def uninstall():
    from maya import cmds

    removed = []
    path = _mod_path()
    if os.path.isfile(path):
        os.remove(path)
        removed.append(path)
    try:
        if _remove_shelf():
            removed.append("the animkit shelf")
    except Exception:
        import traceback
        traceback.print_exc()

    if removed:
        print("animkit uninstalled: %s" % ", ".join(removed))
        detail = "\n".join("  removed  %s" % item for item in removed)
    else:
        print("animkit: nothing to uninstall")
        detail = "  nothing was installed"

    _dialog(
        "animkit",
        "animkit has been uninstalled.\n\n%s\n\n"
        "Restart Maya to unload the code itself -- Maya holds imported\n"
        "modules for the life of a session. The folder you installed from is\n"
        "untouched; delete it when you are done.\n\n"
        "Settings, if you changed any, remain in:\n    %s" % (
            detail, os.path.join(cmds.internalVar(userPrefDir=True), "animkit")),
        ["OK"])
    return True


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def onMayaDroppedPyFile(*args, **kwargs):
    """Called by Maya when this file is dropped into a viewport."""
    install()


if __name__ == "__main__":
    # Executed rather than dropped -- from the Script Editor, say.
    try:
        import maya.cmds  # noqa: F401
    except ImportError:
        sys.stdout.write(
            "animkit: this installer has to run inside Maya.\n"
            "Start Maya and drag this file into a 3D viewport.\n")
    else:
        install()
