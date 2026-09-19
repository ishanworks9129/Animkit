# animkit — evaluation build

Animation tooling for Autodesk Maya 2022–2026. A tween/blend slider, 22
keyframe operations, pose copy/paste/mirror/flip that needs no rig
configuration, named selection sets stored on the rig, video and image
reference on the viewport, and a held-hotkey radial to reach any of it without
looking.

Pure Python. No third-party packages, no compiled plug-in, no licence server,
no network access.

The package is called `animkit`, **not** `animbot`, so it installs alongside a
real Animbot licence without a `sys.modules` collision.

---

## Install — 30 seconds

1. **Put this folder somewhere permanent first.** The installer points Maya at
   this folder; it does not copy the code. A Downloads folder that gets
   cleaned out later takes animkit with it. (The installer will warn you if
   you try.)
2. Start Maya.
3. Drag **`DRAG_AND_DROP_INSTALL.py`** out of Explorer / Finder and drop it
   into a Maya 3D viewport.

That is it. It works immediately, with no restart: an **animkit** shelf
appears, and the strip opens docked above the time slider. The dialog tells
you what was written and what was changed.

To uninstall, drop the same file in again and pick **Uninstall**. It removes
the module file, the shelf, every animkit panel and Maya's saved state for
them, and asks separately whether to delete your settings and usage log —
they are yours, and they are what you were going to send back.

Then **restart Maya** before doing anything else. Maya holds imported modules
for the life of a session, so animkit's code is still loaded until you do.

### If you would rather not run an installer

Everything it does, you can do by hand:

```python
import sys
sys.path.append(r"<this folder>")

import animkit
animkit.startup()

import animkit.ui.panel as panel
panel.show()
```

Or copy `modules/animkit.mod`, edit the one path in it, and drop it in
`<maya prefs>/modules/`.

---

## Exactly what the installer touches

Worth knowing before it runs on a studio machine:

| It does | It does not |
|---|---|
| Write `<maya prefs>/modules/animkit.mod` — one pointer, ~10 lines | Copy anything into the Maya installation |
| Create a shelf named `animkit` | Touch any other shelf, or your hotkeys |
| `sys.path.append` this folder, then call `animkit.startup()` | Modify your `userSetup.py` |
| — | Write to the registry, or need admin rights |
| — | Reach the network. There is no HTTP client anywhere in the code |
| — | Bind a single hotkey. Every command is a `runTimeCommand`; nothing is on a key until you put it there |
| Keep a local usage log — see below | Send it anywhere, or record anything about your rig or your shot |
| Open the strip, and set `ui.open_at_startup` so it opens with Maya | Open anything else, then or later — the dialog names this one, and it is one line to undo |

The installer is ~350 lines of readable Python at the root of this folder, and
so is everything else here. Read it before you run it — that is rather the
point of shipping it uncompiled.

After a Maya restart, `startup/userSetup.py` (reached through the module's
`MAYA_SCRIPT_PATH`) calls `animkit.startup()` for you. That registers the
commands and the scene callbacks and touches no Qt at all — it cannot stop
Maya launching, because it defers its work and swallows its own exceptions.

Opening a panel is a *separate* call made straight after, and it opens only
what `ui.open_at_startup` names. Set it to `""` and the launch path touches no
UI code whatsoever — it does not even import Qt.

Delete the `MAYA_SCRIPT_PATH` line from the `.mod` if your studio drives
startup centrally.

---

## The usage log, stated plainly

This is an evaluation build, so it keeps a **local** log of which operations
you use. It exists to answer "which of these 55 operations does anybody
actually reach for", which is not a question a conversation answers well.

**What it records:** the operation name, how many things it touched, and — once
per session — your Maya version, the animkit version, the build id, and your
OS.

**What it does not record:** node names, attribute names, file paths, scene
names. Nothing about the rig or the shot. When an operation raises, the log
gets the exception's *class* (`RuntimeError`) and never its message, because
the message is where rig names live. There is a test that greps a rig name out
of a failure and asserts it did not reach the file.

**Where it goes:** `<maya prefs>/animkit/usage.jsonl`. Nowhere else. animkit
has no network code of any kind — grep it — so the log reaches us only if you
click **send** on the shelf, which writes a zip and shows it to you in
Explorer, and then you decide whether to mail it. Open the zip first; it is
plain text and that is deliberate.

**Turning it off:** the **help** page has the same explanation with a *Stop
logging* button and a *Delete the log* button next to it. Or set
`usage.log: false` in `<maya prefs>/animkit/settings.json`. Nothing else
changes if you do — there is no nagging and no reduced functionality.

Hotkey presses are not logged, incidentally. They go straight into the module
through Maya's `runTimeCommand`, bypassing the one place the log is written.
Worth knowing when you read a report that says you never used the tween.

## Verify it before trusting it

Click **test** on the animkit shelf, or:

```python
import animkit.selftest as st
st.run()
```

34 checks against temporary nodes it creates and deletes itself. It does not
open a new scene and does not touch any node you did not select; your
selection, current time and animation-layer selection are restored on exit. It
prints a PASS/FAIL table plus `TIME` rows in milliseconds — what counts as too
slow depends on the rig, so it reports the number rather than judging it.

**Run it twice.** The layer checks create `BaseAnimation` as a side effect; if
run 2 does not match run 1, cleanup is leaking state. That is exactly how one
real bug was found.

Verified here on Maya 2024 / PySide2: 33/33, identical across two runs.

---

## What to try first

Open the panel (**anim** on the shelf) and:

1. **Tween** — select an animated control, put the time cursor between two
   keys, drag the bar. Right-click it for settings: overshoot, between vs.
   selected-key mode, the quick buttons.
2. **Pose** — mirror and flip work on any rig with no configuration. It asks
   the scene rather than matching a naming convention, so it should work on
   rigs whose names it has never seen. **This is the part worth trying to
   break.** Bring your strangest rig.
3. **Keys** — 22 operations, all sharing one target resolver, so selected keys
   / Channel Box highlight / whole curve behave consistently across all 22.
4. **Ref** — drag a **video, a photo or an image sequence** from Explorer onto
   the viewport. It comes up on that view's camera starting at the current
   frame, as a real transform you can move, rotate and scale. Select every
   frame of a sequence in Explorer and drag the lot; that is what selecting a
   sequence looks like to a file manager.

   A dropped video is converted to an image sequence first, with a progress
   bar, because Maya cannot decode `.mp4` or `.mov` on an image plane at all.
   The converted frames are cached; the **Ref** tab says how much disk that
   is costing and has a button to clear it. Dropping an `.mp3` on the
   timeline works the same way.
5. **The radial** — nothing is bound by default, on purpose:

   ```python
   import animkit.ui.radial as radial
   radial.install_hotkey("c", "pose", alt=True)   # hold Alt+C and flick
   ```

   It refuses to overwrite a key that already has something on it.

6. **The strip** (**strip** on the shelf) — the same operations in one row,
   docked between the viewport and whatever is stacked below it, so it sits
   right above the time slider where you are already looking. Drag it
   somewhere else and Maya remembers; animkit never re-docks a control you
   have moved.

### What opens by itself, and how to stop it

Installing opens the strip straight away and sets it to open with Maya from
then on. The install dialog says so at the time — this is the one thing
animkit puts on screen without being asked separately, on the grounds that
you asked by dragging an installer into your viewport.

It is one line to stop:

```python
from animkit.core import settings
settings.set("ui.open_at_startup", "")        # or "panel", or "both"
```

Everything else stays opt-in. **No hotkey is bound at all** — not one — and
nothing else opens on its own. Bind what you want in the Hotkey Editor by the
names on the **help** page.

Maya also restores whatever you left open when you quit, independently of
that setting, because every animkit panel is a `workspaceControl` and Maya
saves those with the workspace. So if you drag the strip somewhere you prefer,
it stays there: animkit never re-docks a control you have moved.

**help** on the shelf lists every operation, what it is for, its
`runTimeCommand` name, and the key it is on *right now*.

---

## Known limits, stated up front

- **Viewport drawing is not built.** No ghosting, no motion trails drawn in
  the viewport. That was deliberately left for last.
- **Windows is the verified platform.** The code is platform-agnostic and
  macOS and Linux should work, but neither has been run here.
- **Maya 2024 is what everything is verified against.** 2022 and 2026 are
  supported by construction — pure Python, and Qt goes through one shim — but
  are not what the numbers above came from.
- **Almost all of this package is ffmpeg.** The 53 MB download unpacks to
  about 127 MB, of which 126 MB is one `ffmpeg.exe` and the rest is the
  actual tool. Maya cannot decode `.mp4` or `.mov` on an image plane **at
  all** — measured, not assumed; `coverageX` stays `-1` in both Movie and
  Image File mode — so animkit converts before Maya ever sees the file.
  It is a static **LGPL v3** build (`n8.1.2-54-gc573a95381`, from
  [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds)); the licence
  is at `animkit/vendor/ffmpeg/LICENSE` and the corresponding source is
  <https://git.ffmpeg.org/ffmpeg.git> at commit `c573a95381`.

  It is invoked as a separate program over a subprocess, which is aggregation
  rather than linking, so nothing about animkit's own licensing follows from
  it. Delete the binary if your pipeline would rather supply its own — set
  `reference.ffmpeg`, or put one on `PATH`, and everything keeps working. The
  installer reports which case you are in.

## Feedback that is most useful

The rig that broke pose mirroring, with the node names. The operation that did
the wrong thing on a real shot. Anything that took more than one undo to back
out of — every write is supposed to be exactly one undo step, and a place
where it is not is a bug worth hearing about immediately.
