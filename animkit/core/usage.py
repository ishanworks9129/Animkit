"""Local usage log, for a build that has been handed to testers.

    from animkit.core import usage
    usage.record("animkitTweenNext30", result=14)
    usage.summary()                  # {"animkitTweenNext30": 14, ...}
    usage.export()                   # a zip to mail back

WHAT THIS IS FOR
    Handing a build to somebody outside the repo and then finding out which
    operations they actually reached for, which ones raised on a real rig, and
    which Maya they ran it in. It answers "was this worth building" with
    something other than a memory of a conversation.

WHAT IT RECORDS
    An operation name, a timestamp, and -- when the operation returns one -- a
    count of how many things it touched. Once per session it also records the
    Maya version, the animkit version and build id, the Python version and the
    platform.

WHAT IT DELIBERATELY DOES NOT RECORD, AND WHY THAT IS NOT NEGOTIABLE
    No node names. No attribute names. No file paths. No scene name. No
    arguments to any operation. Not one character of the rig.

    A tester is very often working on somebody else's show under somebody
    else's NDA, and a log full of `char_hero_L_arm_IK_ctrl` is a log that
    names an unannounced production. The cost of that landing in a zip in
    somebody's inbox is not worth any amount of insight into which operation
    was called.

    This is also why a failed operation records its exception CLASS and not
    its message: `RuntimeError` is useful, and "No object matches name:
    char_hero_L_arm_IK_ctrl" is the exact thing this module must never write
    down.

WHAT IT NEVER DOES
    Touch the network. There is no HTTP client in animkit and this module does
    not add one -- it appends to a file in the Maya prefs folder and stops
    there. Getting it back is a person exporting a zip and sending it, which
    means a person decided to.

THE RULES IT INHERITS FROM settings.py
    Nothing here raises. It is called from `Operation.invoke`, which is to say
    from the middle of an animator's keyframe operation, and a tool that
    breaks the operation because it could not write a log file deserves
    everything it gets. Every public function swallows and logs.
"""

import json
import logging
import os
import platform
import sys
import time

from animkit.core import settings

log = logging.getLogger(__name__)

FILENAME = "usage.jsonl"
ROTATED = "usage.1.jsonl"

#: Rotate past this. A tester who leaves Maya open for a fortnight should not
#: find animkit has quietly eaten a gigabyte; two files cap the whole thing at
#: roughly 4 MB, which is still tens of thousands of operations.
MAX_BYTES = 2 * 1024 * 1024

#: Set once per session so the environment record is not written per click.
_session_written = False


# --- location ---------------------------------------------------------------


def path():
    """The log file, beside settings.json. Never raises."""
    return os.path.join(settings.directory(), FILENAME)


def rotated_path():
    return os.path.join(settings.directory(), ROTATED)


def enabled():
    """Is logging on? Off means every record() below is a no-op."""
    try:
        return bool(settings.get("usage.log"))
    except Exception:
        # settings.get is contractually incapable of raising, so this is
        # belt-and-braces -- but this module's whole job is to not be the
        # reason something broke.
        return False


# --- build identity ----------------------------------------------------------


def build():
    """Which build this is: (id, recipient). ("dev", "") in a working copy.

    animkit/_build.py is rewritten by scripts/make_release.ps1 when a package
    is cut for a named recipient, so a log that comes back can be matched to
    the zip it came from without asking anybody to remember.
    """
    try:
        from animkit import _build

        return (getattr(_build, "ID", "dev"), getattr(_build, "RECIPIENT", ""))
    except Exception:
        return ("dev", "")


def _environment():
    maya_version = ""
    try:
        from maya import cmds

        maya_version = str(cmds.about(version=True))
    except Exception:
        # mayapy without maya.standalone initialised, or plain CPython under
        # the fast test tier. Not worth a log line.
        pass

    build_id, recipient = build()
    try:
        from animkit import __version__ as animkit_version
    except Exception:
        animkit_version = "?"

    return {
        "e": "session",
        "maya": maya_version,
        "animkit": animkit_version,
        "build": build_id,
        "recipient": recipient,
        "py": "%d.%d.%d" % sys.version_info[:3],
        "os": platform.system(),
    }


# --- writing -----------------------------------------------------------------


def _stamp():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _rotate_if_large():
    target = path()
    try:
        if os.path.getsize(target) < MAX_BYTES:
            return
    except OSError:
        return  # no file yet, which is the common case

    try:
        old = rotated_path()
        if os.path.exists(old):
            os.remove(old)
        os.replace(target, old)
    except Exception:
        log.debug("animkit: could not rotate the usage log", exc_info=True)


def _append(record):
    """One JSON object, one line. Never raises.

    Appended immediately rather than buffered and flushed at exit: the session
    worth reading is usually the one that ended in a Maya crash, and a buffer
    is exactly what a crash throws away.
    """
    if not enabled():
        return False

    record["t"] = _stamp()
    try:
        folder = settings.directory()
        if not os.path.isdir(folder):
            os.makedirs(folder)
        _rotate_if_large()

        line = (json.dumps(record, sort_keys=True) + "\n").encode("utf-8")

        # Binary, and with a newline check, for one reason: a crash partway
        # through a write leaves a line with no terminator, and appending
        # straight onto it welds the next record to the broken one -- so a
        # single crash costs TWO records instead of one, and the second is
        # invisible because it was never written badly, just written second.
        # Found by tests/test_usage.py, not by reasoning about it.
        with open(path(), "ab+") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell():
                handle.seek(-1, os.SEEK_END)
                if handle.read(1) != b"\n":
                    handle.write(b"\n")
            handle.write(line)
        return True
    except Exception:
        # A read-only prefs folder, a full disk, a network home directory that
        # has gone away. All of it is silent by design -- see the module
        # docstring.
        log.debug("animkit: could not write the usage log", exc_info=True)
        return False


def session_start():
    """Record the environment, once. Called from animkit.startup()."""
    global _session_written
    if _session_written or not enabled():
        return False
    _session_written = True
    return _append(_environment())


def record(name, result=None, error=None):
    """Record one operation. Never raises, whatever is passed to it.

    `result` is kept only when it is a plain int, because that is what the
    operations that return anything return: a count of what they touched.
    Anything else is dropped rather than repr'd -- a repr is precisely how a
    node name ends up in a file this module promises will not contain one.
    """
    try:
        if not enabled():
            return False

        entry = {"op": str(name)}

        # bool is an int in Python, and `"n": true` in the log would be a
        # small lie about what the operation returned.
        if isinstance(result, int) and not isinstance(result, bool):
            entry["n"] = result

        if error is not None:
            # Class name only. The message is where the rig names live.
            entry["err"] = type(error).__name__

        return _append(entry)
    except Exception:
        log.debug("animkit: usage.record failed", exc_info=True)
        return False


# --- reading -----------------------------------------------------------------


def entries():
    """Every record, oldest first, across both log files. Never raises."""
    out = []
    for target in (rotated_path(), path()):
        try:
            with open(target, "r") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        # A line torn in half by a crash mid-write. Skip it;
                        # one bad line must not cost the other 4,000.
                        continue
        except IOError:
            continue
        except Exception:
            log.debug("animkit: could not read %s", target, exc_info=True)
    return out


def summary(records=None):
    """{operation name: times invoked}, commonest first. Never raises."""
    counts = {}
    for entry in (records if records is not None else entries()):
        name = entry.get("op")
        if name:
            counts[name] = counts.get(name, 0) + 1
    return counts


def failures(records=None):
    """{operation name: {exception class: count}}. The interesting half."""
    out = {}
    for entry in (records if records is not None else entries()):
        name, kind = entry.get("op"), entry.get("err")
        if name and kind:
            out.setdefault(name, {})
            out[name][kind] = out[name].get(kind, 0) + 1
    return out


def sessions(records=None):
    """Every session record, oldest first."""
    return [e for e in (records if records is not None else entries())
            if e.get("e") == "session"]


def clear():
    """Delete the log. Never raises; returns what it removed."""
    removed = []
    for target in (path(), rotated_path()):
        try:
            if os.path.isfile(target):
                os.remove(target)
                removed.append(target)
        except Exception:
            log.warning("animkit: could not delete %s", target, exc_info=True)
    return removed


# --- exporting ---------------------------------------------------------------

#: Settings whose VALUE is a path the tester typed, and so is the one place a
#: username or a show name can get into an exported settings file.
REDACT = ("reference.ffmpeg",)


def _redacted_settings():
    try:
        data = dict(settings.load())
    except Exception:
        return {}
    for key in REDACT:
        if data.get(key):
            data[key] = "<set>"
    return data


def report(records=None):
    """The human-readable summary that goes in the zip. Never raises."""
    try:
        records = entries() if records is None else records
        counts = summary(records)
        broken = failures(records)
        runs = sessions(records)
        build_id, recipient = build()

        lines = [
            "animkit usage report",
            "generated  %s" % _stamp(),
            "build      %s" % build_id,
            "recipient  %s" % (recipient or "(unnamed build)"),
            "sessions   %d" % len(runs),
            "operations %d invocations across %d distinct operations"
            % (sum(counts.values()), len(counts)),
            "",
        ]

        if runs:
            last = runs[-1]
            lines += [
                "Most recent environment",
                "  Maya    %s" % (last.get("maya") or "?"),
                "  animkit %s" % (last.get("animkit") or "?"),
                "  Python  %s" % (last.get("py") or "?"),
                "  OS      %s" % (last.get("os") or "?"),
                "",
            ]

        if counts:
            lines.append("Operations, commonest first")
            for name, count in sorted(
                    counts.items(), key=lambda pair: (-pair[1], pair[0])):
                lines.append("  %6d  %s" % (count, name))
            lines.append("")

        if broken:
            lines.append("Operations that raised -- the part worth reading")
            for name in sorted(broken):
                for kind, count in sorted(broken[name].items()):
                    lines.append("  %6d  %-34s %s" % (count, name, kind))
            lines.append("")
        else:
            lines.append("No operation raised.")
            lines.append("")

        lines += [
            "This report contains no node names, no file paths and no scene",
            "names -- see animkit/core/usage.py for why that is deliberate.",
        ]
        return "\n".join(lines)
    except Exception:
        log.warning("animkit: could not build the usage report", exc_info=True)
        return "animkit: the usage report could not be generated."


def export(destination=None):
    """Zip the log, the report and the settings. Returns a path, or "".

    This is the whole "send it back" mechanism: a file the tester can look
    inside before deciding to mail it, which is the only honest way to collect
    anything from somebody else's machine.
    """
    import zipfile

    try:
        build_id, recipient = build()
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
        name = "animkit-feedback-%s-%s.zip" % (recipient or build_id, stamp)

        if destination is None:
            destination = os.path.join(settings.directory(), name)
        elif os.path.isdir(destination):
            destination = os.path.join(destination, name)

        folder = os.path.dirname(destination)
        if folder and not os.path.isdir(folder):
            os.makedirs(folder)

        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("report.txt", report())
            archive.writestr(
                "settings.json",
                json.dumps(_redacted_settings(), indent=2, sort_keys=True))
            for target, arcname in ((rotated_path(), ROTATED), (path(), FILENAME)):
                if os.path.isfile(target):
                    archive.write(target, arcname)

        log.info("animkit: wrote %s", destination)
        return destination
    except Exception:
        log.warning("animkit: could not export the usage log", exc_info=True)
        return ""


def reveal(target):
    """Show `target` in the platform's file browser. Never raises.

    Deliberately not a Maya call: this module stays importable in plain
    CPython so it can be tested in the fast tier, like settings and media.
    """
    import subprocess

    try:
        if not target or not os.path.exists(target):
            return False
        folder = os.path.dirname(os.path.abspath(target))
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.abspath(target)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", os.path.abspath(target)])
        else:
            subprocess.Popen(["xdg-open", folder])
        return True
    except Exception:
        log.debug("animkit: could not reveal %s", target, exc_info=True)
        return False


def export_and_reveal():
    """What the shelf button and the Help page's button both call."""
    target = export()
    if target:
        reveal(target)
    return target
