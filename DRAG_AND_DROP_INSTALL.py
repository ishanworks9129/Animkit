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

#: Every workspaceControl animkit can create.
#:
#: Hardcoded rather than read from the package, because uninstall has to work
#: at the exact moment the package is unreachable -- that is rather the point
#: of it. tests/test_install.py greps CONTROL_NAME out of every animkit/ui
#: module and asserts this list covers them, so it cannot drift quietly.
#:
#: Removing these is not optional tidying. A workspaceControl is retained in
#: Maya's saved workspace and rebuilt at the next launch by running its
#: uiScript -- "import animkit.ui.strip as m; m.build()" -- so an uninstall
#: that leaves them behind leaves a Maya that reaches for a deleted module
#: every time it starts.
CONTROLS = (
    "animkitStrip",
    "animkitPanel",
    "animkitHelp",
    "animkitTweenPanel",
    "animkitKeysPanel",
    "animkitSetsPanel",
    "animkitRefPanel",
)


# ---------------------------------------------------------------------------
# Finding ourselves
#
# Maya does not exec a dropped .py file -- it importlib.import_module()s it and
# then calls onMayaDroppedPythonFile() on the result, so on the drop path
# __file__ is set by the import machinery and the first answer below is the
# one that fires.
#
# The other two are for the paths that are not a drop: pasted into the Script
# Editor, run from a shelf button, called by a studio's own setup script. They
# cost nothing and they are the difference between "it says where to point it"
# and a traceback.
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

def _media_note():
    """What dropping a video will actually do on THIS machine.

    Asked at install time on purpose. animkit converts a dropped video to an
    image sequence before Maya sees it, because Maya cannot decode .mp4 or
    .mov on an image plane at all -- so with no ffmpeg the plane is created,
    a warning is printed, and nothing draws. That is a perfectly clear
    failure to somebody who read the README and a baffling one to everybody
    else, and "the reference feature is broken" is what gets reported.

    Photos and image sequences never need ffmpeg and are unaffected.
    """
    try:
        from animkit.core import settings, transcode

        if transcode.is_available(settings.get("reference.ffmpeg") or None):
            return "Video, image and audio reference: all working."
    except Exception:
        import traceback
        traceback.print_exc()
        return ""

    return (
        "Photo and image-sequence reference: working.\n"
        "VIDEO and MP3 reference: NOT working on this machine -- no ffmpeg.\n"
        "  Maya cannot decode .mp4 or .mov on an image plane, so animkit\n"
        "  converts first, and this build ships without the converter.\n"
        "  Drop any ffmpeg into animkit/vendor/ffmpeg/win64/ffmpeg.exe, or\n"
        "  put one on PATH, and it is picked up with no other change."
    )


def _open_strip():
    """Open the strip and ask for it back on every launch. Never raises.

    THIS IS NOT THE SAME AS animkit OPENING ITSELF UNINVITED.
    animkit's rule is that it puts nothing on screen and nothing on a hotkey
    that the animator did not ask for, and this does not break it: somebody
    dragged an installer into their viewport thirty seconds ago. Showing them
    the thing they just installed IS the invitation being honoured. An
    installer that ends with a dialog and no visible tool has told the
    animator that something happened somewhere, and left them to find it.

    The startup setting is written here for the same reason and is stated in
    the dialog rather than done quietly -- a preference somebody has to
    discover was changed for them is exactly the kind of surprise the rule is
    about. One line in the Script Editor puts it back.

    Returns what the install dialog should say about it.
    """
    try:
        from animkit.core import settings
        import animkit.ui.strip as strip

        strip.show()
    except Exception:
        import traceback
        traceback.print_exc()
        return ("The strip could not be opened -- see the Script Editor.\n"
                'Open it from the "strip" button on the shelf.')

    # Arm the viewport for dropped media in THIS session too, not just from
    # the next launch. Otherwise the first thing a tester tries -- dragging a
    # video onto the viewport, because the README says to -- falls through to
    # Maya and reports "No translator found".
    #
    # ITS OWN try, deliberately. Sharing one with the strip above meant a
    # failure here reported "The strip could not be opened" over a strip that
    # was open and fine, which is a worse bug than the one it was hiding:
    # a misleading diagnosis costs more than a missing feature.
    try:
        import animkit

        animkit.install_viewport_drop()
    except Exception:
        import traceback
        traceback.print_exc()

    try:
        settings.set("ui.open_at_startup", "strip")
        return ("The strip is open, docked above the time slider, and set to\n"
                "open with Maya from now on. To stop that:\n"
                '    from animkit.core import settings\n'
                '    settings.set("ui.open_at_startup", "")')
    except Exception:
        import traceback
        traceback.print_exc()
        return ("The strip is open, docked above the time slider. It could\n"
                "not be set to open with Maya -- see the Script Editor.")


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

    media = _media_note()
    strip_note = _open_strip()

    print("animkit %s installed" % VERSION)
    print("  module file : %s" % mod)
    print("  code        : %s" % root)
    if media:
        print("  %s" % media.replace("\n", "\n  "))

    _dialog(
        "animkit installed",
        "animkit %s is installed and running in this session.\n\n"
        "%s\n%s\n\n"
        "Code   : %s\n"
        "Module : %s\n\n"
        "%s\n\n"
        "Try the self-test button on the shelf first. It creates and deletes\n"
        "its own temporary nodes, touches nothing you selected, and prints a\n"
        "PASS/FAIL table. Run it twice; the two runs should match.\n\n"
        "Nothing else on this machine was touched." % (
            VERSION, shelf_note, strip_note, root, mod, media),
        ["OK"])
    return True


def _prefs_dir():
    from maya import cmds
    return os.path.join(cmds.internalVar(userPrefDir=True), "animkit")


def _remove_controls():
    """Delete every animkit panel AND Maya's saved state for it.

    deleteUI alone is not enough: Maya persists a workspaceControl's
    definition, uiScript included, in the workspace, and reuses it when a
    control of that name next appears. The state is purged even for a control
    that does not currently exist, because the state is exactly the thing
    that outlives the control.
    """
    from maya import cmds

    removed = []
    for name in CONTROLS:
        try:
            if cmds.workspaceControl(name, q=True, exists=True):
                cmds.deleteUI(name)
                removed.append(name)
        except Exception:
            import traceback
            traceback.print_exc()
        try:
            cmds.workspaceControlState(name, remove=True)
        except Exception:
            # No saved state, or a Maya that will not be queried about it.
            # Either way there is nothing here worth a traceback.
            pass
    return removed


#: The category animkit registers its runTimeCommands under.
COMMAND_CATEGORY = "Custom Scripts.animkit"


def _remove_runtime_commands():
    """Delete animkit's runTimeCommands. Deliberately does not import animkit.

    These are NOT session-scoped, which is the thing that makes this worth
    doing properly. They are registered with `default=False`, so Maya writes
    every one of them into prefs/userRunTimeCommands.mel when it exits -- on
    this machine that is 236 lines. Leave them and they come back in the next
    Maya forever, listed in the Hotkey Editor under a category whose commands
    all raise ImportError because the package they name is gone.

    Scanning for them beats calling animkit.commands.unregister(), because
    the case that needs cleaning most is the one where animkit can no longer
    be imported at all -- a deleted folder, a .mod removed by hand. Matching
    on the category first and the name prefix second finds them either way.
    """
    from maya import cmds

    try:
        names = cmds.runTimeCommand(q=True, userCommandArray=True) or []
    except Exception:
        return []

    removed = []
    for name in names:
        try:
            category = cmds.runTimeCommand(name, q=True, category=True) or ""
        except Exception:
            category = ""
        if category != COMMAND_CATEGORY and not name.startswith("animkit"):
            continue
        try:
            if cmds.runTimeCommand(name, q=True, exists=True):
                cmds.runTimeCommand(name, e=True, delete=True)
                removed.append(name)
        except Exception:
            import traceback
            traceback.print_exc()
    return removed


def _remove_option_vars():
    """Delete animkit's optionVars -- the saved panel geometry.

    Maya writes these into prefs/userPrefs.mel: animkitPanelState,
    animkitTweenControlState and friends. Harmless, invisible, and they
    outlive everything else, so an "uninstall" that leaves them has not
    quite told the truth.
    """
    from maya import cmds

    try:
        names = cmds.optionVar(q=True, list=True) or []
    except Exception:
        return []

    removed = []
    for name in names:
        if not name.startswith("animkit"):
            continue
        try:
            cmds.optionVar(remove=name)
            removed.append(name)
        except Exception:
            import traceback
            traceback.print_exc()
    return removed


def _remove_prefs():
    import shutil

    folder = _prefs_dir()
    if not os.path.isdir(folder):
        return False
    shutil.rmtree(folder)
    return True


def uninstall(purge=None):
    """Remove animkit. `purge` also deletes settings and the usage log.

    purge=None asks, which is the drag-and-drop path. Pass True or False to
    script it.

    The log is a separate question from the install because it is the
    tester's own data and the thing they were going to send back -- deleting
    it as a side effect of "uninstall" would throw away the only record of
    what they found.
    """
    removed = []

    if purge is None and os.path.isdir(_prefs_dir()):
        answer = _dialog(
            "animkit",
            "Remove animkit's settings and usage log as well?\n\n    %s\n\n"
            "Keep them and a later reinstall picks up where this left off --\n"
            "including anything you were going to send back.\n\n"
            "Remove everything for a genuinely clean slate, which is what you\n"
            "want if you are about to test the install again." % _prefs_dir(),
            ["Keep them", "Remove everything", "Cancel"], "Keep them")
        if answer == "Cancel":
            return False
        purge = answer == "Remove everything"

    path = _mod_path()
    if os.path.isfile(path):
        os.remove(path)
        removed.append(path)

    # Before the shelf, so a half-failed uninstall still takes the panels --
    # they are the part that follows Maya into the next session.
    try:
        controls = _remove_controls()
        if controls:
            removed.append("%d panel(s): %s" % (len(controls), ", ".join(controls)))
    except Exception:
        import traceback
        traceback.print_exc()

    try:
        if _remove_shelf():
            removed.append("the animkit shelf")
    except Exception:
        import traceback
        traceback.print_exc()

    try:
        commands = _remove_runtime_commands()
        if commands:
            removed.append("%d runTimeCommand(s)" % len(commands))
    except Exception:
        import traceback
        traceback.print_exc()

    try:
        option_vars = _remove_option_vars()
        if option_vars:
            removed.append("%d saved panel position(s)" % len(option_vars))
    except Exception:
        import traceback
        traceback.print_exc()

    if purge:
        try:
            if _remove_prefs():
                removed.append("settings and the usage log")
        except Exception:
            import traceback
            traceback.print_exc()

    if removed:
        print("animkit uninstalled:")
        for item in removed:
            print("  removed  %s" % item)
        detail = "\n".join("  removed  %s" % item for item in removed)
    else:
        print("animkit: nothing to uninstall")
        detail = "  nothing was installed"

    kept = ""
    if not purge and os.path.isdir(_prefs_dir()):
        kept = "\n\nKept, as asked:\n    %s" % _prefs_dir()

    _dialog(
        "animkit",
        "animkit has been uninstalled.\n\n%s%s\n\n"
        "RESTART MAYA before installing again. Maya holds imported modules\n"
        "for the life of a session, so animkit's code is still loaded in this\n"
        "one and a reinstall now would re-use it rather than reading the\n"
        "folder afresh.\n\n"
        "The folder you installed from is untouched; delete it when you are\n"
        "done with it." % (detail, kept),
        ["OK"])
    return True


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

#: The name Maya looks for, and it is not negotiable or guessable. From
#: maya/app/general/executeDroppedPythonFile.py:
#:
#:     MY_DROP_FUNC = 'onMayaDroppedPythonFile'
#:     ...
#:     if hasattr(loadedModule, MY_DROP_FUNC):
#:         ret = loadedModule.onMayaDroppedPythonFile(obj)
#:     else:
#:         cmds.warning(... kDropFuncMissing ...)
#:
#: Get the name wrong and the drop does nothing except print that warning,
#: which is what "onMayaDroppedPyFile" -- a name that reads fine and is not
#: the one -- did here. tests/test_install.py now reads this constant out of
#: the installed Maya rather than trusting either of us.
DROP_FUNCTION = "onMayaDroppedPythonFile"


def onMayaDroppedPythonFile(obj=None):
    """Called by Maya when this file is dropped into a viewport.

    Maya passes the object under the mouse, which animkit does not care
    about, and uses the return value as the drop's success flag.

    Note that Maya *imports* this file to find this function, so `__file__`
    is set by importlib and the module stays in sys.modules afterwards. A
    second drop therefore re-uses the loaded module and calls straight in
    here without re-executing the file -- which is fine, because everything
    above is definitions.
    """
    return install()


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
