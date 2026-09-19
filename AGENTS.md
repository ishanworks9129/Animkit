# AGENTS.md

Orientation for an AI agent working in this repo. Read this before touching
anything; read [README.md](README.md) when you need the *reasoning* behind a
decision, because most of it is recorded there and in the module docstrings
rather than here.

**The one-line version:** this is a Maya animation toolkit that refuses to
guess. Every rig-facing question is asked *of the scene* rather than matched
against a naming convention, every write is one undo step, and every behaviour
that surprised us once is now pinned by a test with a comment saying why.

---

## Tech stack

| Layer | What | Why it is that |
|---|---|---|
| Host | **Autodesk Maya 2022–2026** | The target. 2024 is what is installed here and what everything is verified against. |
| Language | **Python 3.7–3.11** | Maya's bundled interpreter. 2022 ships 3.7, 2026 ships 3.11 — so no `match`, no `:=` in anything that must run on 3.7, and `%`-formatting is used throughout for consistency with the existing code. |
| Maya API | **`maya.cmds`** for scene edits, **`maya.api.OpenMaya` (API 2.0)** for hot-path reads | cmds is undoable and correct; API 2.0 skips the command engine, which took `plugs_from_selection` from ~11,400 round-trips and 278 ms at mouse-down to zero. Both are used deliberately — see "Which API" below. |
| GUI | **PySide2 (Qt5)** on Maya 2022–2024, **PySide6 (Qt6)** on 2025–2026 | Whatever Maya ships. Imported *only* through [animkit/vendor/qt.py](animkit/vendor/qt.py). |
| Tests | **pytest**, run under `mayapy` | Not bundled with Maya; `scripts/run_tests.ps1` installs it `--user` on first run. |
| Shell | **PowerShell** on Windows | `scripts/run_tests.ps1`. A Bash tool is also available in this environment. |
| Runtime deps | **no Python packages**; one optional bundled binary | Stdlib + Maya only: `contextlib`, `functools`, `hashlib`, `json`, `logging`, `math`, `os`, `re`, `subprocess`, `time`, `traceback`. Do not add a third-party Python dependency — a studio install is a `.mod` file and a folder, and it has to stay that way. The exception is [animkit/vendor/ffmpeg/](animkit/vendor/ffmpeg/): a separate program run over a subprocess, optional at runtime, bundled because Maya cannot decode `.mp4` or `.mov` on an image plane. It is still just a folder. |
| Packaging | **`.mod` file**, pure Python | [modules/animkit.mod](modules/animkit.mod). No compiled extension anywhere, which is what keeps one build serving every Maya version. [DRAG_AND_DROP_INSTALL.py](DRAG_AND_DROP_INSTALL.py) writes that `.mod` for a tester; [startup/userSetup.py](startup/userSetup.py) is what calls `startup()` at launch; [scripts/make_release.ps1](scripts/make_release.ps1) builds the hand-off zip and refuses to ship a personal path, a stray `.pyc`, or an ffmpeg built `--enable-gpl`. The bundled Windows binary is **LGPL v3** and ships by default; `-NoFFmpeg` opts out. |
| VCS | **none — this is not a git repo** | Do not run `git` commands expecting history. There is no baseline to diff against; the test suite is the safety net. |

The package is called `animkit`, **not** `animbot`, so it can be installed
alongside a real Animbot licence without a `sys.modules` collision. Keep it that
way.

---

## What is built

About 14,400 lines of tool code against 7,600 lines of tests. Phases 1, 3 and 4
are in, plus reference media; Phase 5 (viewport drawing) is deliberately not
started.

### Core (`animkit/core/`) — no opinions about tools

| Module | What it owns |
|---|---|
| `layers.py` | Animation-layer-aware curve resolution. **Read this first** — everything that writes goes through it. |
| `targets.py` | "Which keys am I acting on." Shared by the tween and every keyframe op. |
| `curves.py` | `MFnAnimCurve` access with unit-safe conversion. |
| `selection.py` | Selection + Channel Box highlight → plug list. Owns `UNTWEENABLE`. |
| `xform.py` | Matrix layer: rest poses, reflection, frame relations, decomposition. |
| `pairing.py` | Finds a control's counterpart; `analyse()` is the single symmetry verdict. |
| `rest_store.py` | Persists a captured rest pose in the scene. Identified by connection, never by name. |
| `cache.py` | Scoped memoization. Read its docstring before extending it. |
| `undo.py` | `undo_chunk`, `LazyChunk`, refresh suspension, panic button. |
| `settings.py` | JSON prefs that cannot break Maya launch. Includes `ui.open_at_startup` ("", "strip", "panel", "both") -- read by `animkit.open_startup_ui()`, which is deliberately NOT part of `startup()` so that function can keep its no-Qt guarantee. |
| `usage.py` | Local usage log for a build handed to testers. Operation names and counts only -- no node names, no paths, no scene names, and a failure records its exception CLASS not its message. Never touches the network. No Maya and no Qt, so it tests in plain CPython. |
| `scene.py` | `MSceneMessage` callbacks → cache invalidation. |
| `media.py` | What a dropped path is: an image, a movie, a sound, or one frame of a sequence. No Maya and no Qt, so it tests in plain CPython. |
| `transcode.py` | Video → image sequence and sound → wav via the bundled ffmpeg, cached. No Maya and no Qt either — it runs real conversions in the fast tier. |

### Tools (`animkit/tools/`) — the operations

| Module | What it does |
|---|---|
| `blend.py` | Blend maths. Imports nothing from Maya, so it tests in plain CPython. |
| `tween.py` | `TweenSession` — snapshot / update / commit for the drag. |
| `keys.py` | **22** keyframe operations, in groups `Timing, Bake, Tangents, Cycle, Edit`. The seven Bake entries are GENERATED from `BAKE_LABELS`, so the set of steps lives in one place. |
| `pose.py` | **10** pose operations: copy/paste/mirror/flip/reset/rest capture, plus mirror and flip across a frame range. Also `rig_controls()` and `controls_in()` — which nodes are controls. |
| `sets.py` | **7** selection-set operations. Named sets stored on the rig as tagged `objectSet`s. |
| `reference.py` | **11** reference-media operations. Image planes made from a dropped video or image sequence, tagged so no other image plane in the shot is ever touched. Free by default — a real transform you can move, rotate and scale. |
| `audio.py` | **5** sound operations. An `audio` node on the time slider made from a dropped mp3/wav, tagged so no other audio node in the shot is touched. Maya reads wav and aiff only, so anything else is converted first. |
| `registry.py` | The one `Operation` class every registry shares. `invoke()` is the single funnel for the panel, strip and radial -- which is why the usage hook is four lines there rather than fifty-five elsewhere. A hotkey does NOT pass through it. |

### UI (`animkit/ui/`)

| Module | What it is |
|---|---|
| `panel.py` | **The front door.** One dockable panel, five tabs. |
| `strip.py` | The horizontal bar docked against the time slider. Same operations, one row. Maya's time slider is itself a `workspaceControl`, which is what lets this dock beside it instead of reaching into Maya's own layout. |
| `help_ui.py` | "What can animkit do" — generated from the registries, showing each operation, what it is for, its `runTimeCommand` name, and the key it is on **right now**. |
| `catalogue.py` | How the strip and the help page arrange the registries. **No Qt, no Maya** — which is why it is the only part of either that has tests. |
| `style.py` | Palette, metrics, stylesheet. The only place a colour is defined. |
| `icons.py` | 37 vector icons, drawn in code. No image files ship. |
| `mayawin.py` | `workspaceControl` plumbing. Every trap here cost a day. |
| `tween_ui.py` | The slider. |
| `keys_ui.py` | Keyframe + pose button panel. |
| `sets_ui.py` | Selection sets panel. Reads the scene; caches nothing. |
| `reference_ui.py` | Reference panel: a drop zone, and one row per reference that is up. |
| `viewport_drop.py` | The event filters that make a 3D viewport and the TIME SLIDER accept a dropped file. A viewport drop carries the camera; a timeline drop carries the frame under the cursor. |
| `radial_geom.py` | Radial menu geometry. **No Qt, no Maya** — which is why it is testable. |
| `radial.py` | The held-hotkey radial overlay, its three menus, and its bindings. |

### Entry points

- `animkit.startup()` — idempotent, called from `userSetup.py`, does no UI work.
- `animkit.ui.panel.show()` — the panel an animator opens. `animkitShow`.
- `animkit.commands` — registers **74** `runTimeCommand`s under
  *Custom Scripts → animkit*. Binds **no hotkeys**.
- `animkit.selftest.run()` — **34** checks against nodes it creates and deletes,
  plus a viewport-drop check that is a measurement headless and a gate in a
  real Maya.
- `scripts/rig_probe.py` — read-only diagnosis of why a mirror is not working.
- `scripts/bench.py` — performance. Run it before optimising anything.

---

## How to verify a change

Three gates, three different jobs. A change is not done until the ones it
touches are green.

```powershell
# fast tier -- plain CPython, ~16s (it runs real ffmpeg conversions)
powershell -ExecutionPolicy Bypass -File .\scripts\run_tests.ps1

# full tier -- boots maya.standalone, ~90s, run before calling anything done
powershell -ExecutionPolicy Bypass -File .\scripts\run_tests.ps1 -Maya -MayaVersion 2024
```

```python
# in Maya, or headless via mayapy: environment-specific behaviour
import animkit.selftest as st; st.run()
```

**Current baseline: 149 fast, and 903 under `-Maya`, and 34/34 self-test,
identical across two consecutive runs.**

The suite runs against the SHIPPED settings defaults — `conftest.isolated_prefs`
points `settings.directory()` at a throwaway folder. Before that fixture existed
the Maya tier read the prefs of whoever ran it, so an animator who had set
`new drops: pinned to camera` in the Ref tab failed a dozen tests that say
nothing about attachment. If your change moves those numbers
down, that is the finding — report it, do not paper over it.

Note what `-Maya` actually runs: `pytest tests/`, so it collects **both** tiers
— the Maya-only tests plus the fast ones, in one number. Do not add the two
figures in the baseline together: 842 ALREADY INCLUDES the 134, so the sum is a
number that matches no command anyone can run. This note has been wrong twice
for exactly that reason ("128 fast + 659 Maya tier", then "542 Maya-only plus
the 131 fast ones" alongside a headline of 678), which is why it now says what
the runner prints and nothing else: `run_tests.ps1 -Maya` prints 842.

Run the self-test **twice**. If run 2 differs from run 1, cleanup is leaking
state and the two runs exercised different code paths.

`mayapy` lives at
`C:\Program Files\Autodesk\Maya2024\bin\mayapy.exe`, and the suite needs
`PYTHONPATH` pointing at the repo root.

---

## Rules that are not style preferences

Each of these exists because breaking it produced a real bug. They are the
things most likely to be violated by a plausible-looking change.

### 1. Ask the rig. Never match a naming convention.

`*_ctrl` also matches the offset and extra groups on every rig that names its
plumbing after the control it carries, which is most of them. Controller tags,
control shapes and control-set membership are properties of what a control *is*;
a name is a property of what one studio typed. This rule is why the mirror needs
no per-rig configuration, and it is the single most important idea in the repo.

Corollary: **lock state is not a control signal.** Riggers do not lock their
internals. `settable_transform_channels` answers "can Maya write this channel"
and says nothing about whether the node is a control — the rename that made that
explicit is described in the README, and it is there because reading it the other
way destroyed a day's work.

### 2. Never assert on `getAttr`. Assert on curve state.

`getAttr` reads the result of DG evaluation, which is **stale after any topology
change until the plug is dirtied** — including a key having just been written. A
`getAttr` assertion tests Maya's evaluation timing, not your correctness. Use
`cmds.keyframe(q=True, valueChange=True)`.

This has caught the codebase out three times, including on a *read* path:
`pose.capture()` calls `cmds.dgdirty` before reading, and that is not defensive
coding — without it, "pose it, key it, copy it" captured pre-key values.

### 3. One user-visible operation is exactly one undo step.

Everything that writes goes inside `undo.undo_chunk` or `undo.LazyChunk`. Use
`LazyChunk` when the operation might decide there is nothing to do — a hotkey
pressed with nothing selected must not put an entry in the undo queue, or Ctrl+Z
stops being a reliable way back.

### 4. `cache.scope()` is per operation, and mutating inside one invalidates it.

Layer selection and lock state change without firing any `MSceneMessage`, so a
long-lived cache would serve the layer the animator was looking at a minute ago.
Outside a scope everything calls straight through — so forgetting a scope costs
performance, never correctness. If you edit the scene inside a scope, call
`cache.invalidate()`.

### 5. The registry is the single source of truth.

`keys.OPERATIONS`, `pose.OPERATIONS`, `sets.OPERATIONS`. The panel, the
`runTimeCommand` registration and the parametrised test harness all read them, so
an operation added to a registry appears in all three for free — and is covered
by tests the moment it exists. Never hand-list an operation anywhere else.

`registry.Operation.command` derives its module from `fn.__module__` rather than
being told, because a hardcoded module path is a string that can disagree with
the function it claims to call, and the symptom is a broken hotkey in someone
else's Maya.

### 6. Bind no hotkeys. Register `runTimeCommand`s.

Animators rebind everything, and binding directly stomps whatever was on that
key. `radial.install_hotkey` is the one exception and it is *opt-in*, refuses to
overwrite an existing binding without `force=True`, and says what it did.

### 7. Refuse rather than produce something plausibly wrong.

A mirror on an asymmetric rest pose does not fail loudly — it produces a pose
whose silhouette reads and whose limb is wrong. `pairing.analyse` verifies the
precondition and callers raise `MirrorRefused`. When you add an operation that
depends on a precondition, check it and refuse; do not guess.

**Apply this consistently across operations.** Reset used to zero unkeyed
channels that the mirror path refused to touch — the same ambiguity, two code
paths, and the destructive one was the one guessing. It collapsed a facial
control board. If one operation refuses on a condition, ask why another
operation is happy to act on it.

The specific unanswerable question, which will come up again: **on an unkeyed
channel, nothing separates "rig build offset" from "the animator moved it and
did not key it".** Only a person answers it — a captured rest pose
(`xform.rest_channels`), or an explicit selection. A `rig_controls()` sweep does
**not**: it answers "is this a control", which is true of a face board and says
nothing about where a board control's rest is. Treating those as equivalent
collapsed one.

### 8. Say something when nothing happened.

Silence after a button press or a hotkey reads as a broken tool. Every "did
nothing" path names *which* question was actually asked — see the messages in
`reset_to_default` and `sets.recall_slot` for the tone.

### 9. Colours live in `style.py`. Icons are drawn, not shipped.

A `setStyleSheet("color: rgb(210,140,140)")` in a widget is how a tool ends up
with four slightly different reds and no way to change any of them. Every
metric goes through `style.px()` so it scales with the screen — a hardcoded
pixel size is correct on exactly one monitor.

Icons render into a **QImage**, not a QPixmap, because QPixmap needs a
QGuiApplication and QImage does not. That is what makes the icon set testable
headlessly, and it is why `icons.contact_sheet()` can render every icon side by
side for a human to look at. Run it before adding an icon — it is how four
tangent icons that were the same smudge got found.

### 10. Import Qt only through `animkit/vendor/qt.py`.

`from PySide2 import ...` anywhere else is the single change that makes a
2024-only tool crash on 2026.

### 11. `workspaceControl` build functions must work from a cold interpreter.

Maya restores docked panels on startup by executing a stored `uiScript` string
*as Python*, before anything your tool has done that session. A build function
that assumes module-level state works until the first Maya restart after someone
docks it.

---

## Which API to reach for

| Job | Use | Why |
|---|---|---|
| Any scene edit | `maya.cmds` | It is undoable. API 2.0 writes are not, without an `MPxCommand`. |
| Surveying attributes on many nodes | `maya.api.OpenMaya` | `selection._survey_node` replaced ~11,400 cmds round-trips at mouse-down with zero. |
| Reading a matrix | `cmds.getAttr` on `worldMatrix`/`matrix` | Outputs, so they are honest. |
| Reading an animated value | the **curve**, via `layers.resolve_curve` | Not `getAttr` — see rule 2. |

Do not "simplify" `_survey_node` to `cmds.listAttr(settable=True)`. That flag does
**not** exclude a connection-driven channel, which is precisely the case that
matters.

---

## House style

- Docstrings explain **why**, and name the failure that motivated the code. A
  docstring that only restates the signature is noise; the ones here are the
  actual documentation and are expected to be long where the reasoning is.
- Comments in ALL CAPS mark a decision that looks wrong until you know the
  reason. Do not delete one without understanding it.
- `%`-formatting, not f-strings — 3.7 compatibility and consistency.
- Every bug gets a test **before** it gets a fix, and the test's docstring says
  what the failure looked like.
- Tests assert **invariants**, not effects, where an operation may legitimately
  be identity on a given fixture. Forcing "it must change something" only makes
  the fixture lie.
- Fixtures are hostile on purpose. The mirror rig has a 180°-flipped right side,
  `rotateAxis`, `jointOrient`, a different `rotateOrder` per side, three levels
  of hierarchy, and the character moved off the origin —
  `test_naive_channel_negation_would_have_failed` exists so the fixture cannot
  quietly stop being hostile.

---

## Traps this codebase has already paid for

The full list, with measurements, is under **Maya behaviour worth knowing** in
the README. The ones most likely to bite a new change:

- **Maya's audio node reads `wav` and `aiff` and nothing else** — measured on
  2024/Windows against a tone written in six formats. `mp3`, `m4a`, `ogg` and
  `flac` all fail, and they fail as `cmds.sound` raising **"cannot find file"**
  against a path that is plainly on disk. Never pass that error on to an
  animator; it sends them hunting for a file that is not missing.
- A **free image plane's face is its local +Z**, and the quad stands vertically
  in local XY — the same transform `Create > Free Image Plane` gives. **You
  cannot verify this from the test tier**: headless Maya computes no bounding
  box for an image plane, Maya Software will not render a detached one, and
  rotate channels are no help because through an axis-aligned camera every
  candidate axis mapping yields `(0,0,0)`. The only instruments are a real
  viewport and Maya's native import. A previous change got this backwards on a
  plausible-looking derivation, shipped it green, and tipped every reference
  onto the ground — if you are tempted to remap those rows, open Maya.
- `cmds.setInfinity` on an animCurve **node** is silently ignored — pass the
  plug.
- Moving a key onto an occupied frame **nudges** rather than merging.
- Plug strings need **long** attribute names; the Channel Box reports short ones.
- Animation layers are **additive**, so a layer curve holds an offset, not a
  value.
- Saved `workspaceControl` state survives `deleteUI` — a broken `uiScript` comes
  back after you fix it unless you purge the state.
- Qt widgets cannot be constructed under `mayapy`, **and the obvious guard for
  that is wrong**: `maya.standalone` leaves a `QGuiApplication`, so
  `QApplication.instance() is not None` passes and the constructor still fails.
  Check `isinstance(..., QApplication)`.
- **There are two `keyable`s.** `MFnAttribute.keyable` is the attribute's
  definition and is `True` for `translateX` on every transform forever;
  `MPlug.isKeyable` is the plug state that `setAttr -keyable false` sets. Only
  the second answers "did the rigger hide this channel". The API survey read the
  first for as long as it existed.
- `cmds.keyframe(node, q=True, keyframeCount=True)` returns **0** for a node
  whose only curve is on a non-keyable plug. Use
  `listConnections(type="animCurve")` when you need "does this node have
  animation at all".
- **Scene callbacks on the same message fire in registration order, and nothing
  declares that order.** A clear-handler and a load-handler both on `kAfterOpen`
  is a coin flip — observed loading correctly in one scene and empty in another
  from identical code. Make one handler do both.
- **`MQtUtil.findControl(name)` matches on objectName and returns the FIRST
  hit.** `style.apply_to` gives every styled root an objectName too, and it used
  to be `"animkitPanel"` — which is also `panel.CONTROL_NAME`. So findControl
  returned the widget *inside* the workspaceControl instead of the control:
  `is_built` read the marker off the wrong widget and reported a correctly built
  panel as empty, every `show()` logged a uiScript failure and rebuilt a panel
  that was fine, and `show(tab=...)` silently never switched tab. The styled
  root is `style.ROOT_OBJECT_NAME` now and a test asserts it collides with no
  control name.
- **`setAttr imagePlane.useFrameExtension 1` wires time to `frameExtension`
  by itself**, via a `timeToUnitConversion` node. Do not write the expression
  Maya's own UI writes — you get "already controlled by an expression,
  keyframe, or other connection".
- **`imagePlane.coverageX` is -1 until Maya has read pixels**, and the image's
  real width afterwards. That is a genuine "did the file load" answer, which is
  why `reference.load` refuses an unreadable image instead of leaving a blank
  plane on the camera.
- **`imagePlane.type` is `Image File:Texture:Movie`** — a movie is `2`, not a
  special node.
- **The image an image plane draws is `frameExtension + frameOffset`**, so
  `frameOffset` is the only thing that decides which frame of a reference is on
  screen now. Every timing operation in `tools.reference` is arithmetic on it.
- **`cmds.imagePlane` selects what it creates and has no flag to stop it.** A
  drop that does not put the selection back steals the animator's controls, and
  — worse, because it is silent — leaves the new plane selected so the next
  reference operation acts only on that one.
- **An image plane's parent path is Maya's under-world form**,
  `|persp|perspShape->|imagePlane1`. Read the camera from the `.message`
  connection into `cameraShape.imagePlane[]`, never by parsing that.
- **A camera-attached image plane cannot be made movable.** It is parented
  under the camera SHAPE and the move tool does not reach it, and
  `lockedToCamera 0` does NOT free it — the plane stays parented either way.
  Free vs attached is decided when the plane is CREATED
  (`cmds.imagePlane()` vs `cmds.imagePlane(camera=...)`), and the only way
  across afterwards is `imagePlane -e -detach` / `-e -camera`.
- **A free image plane is sized by `width`/`height`, not `sizeX`/`sizeY`.**
  They are separate, unconnected attributes: setting `sizeX` on a free plane
  changes nothing at all and it stays Maya's square 10x10, letterboxing every
  reference. `sizeX`/`sizeY` are the camera-attached pair (aperture inches).
  Measured, not read — and `exactWorldBoundingBox` on an image plane is
  degenerate under `mayapy`, so it can only be measured in a real viewport.
- **Attaching or detaching an image plane RENAMES it**, because the name
  encodes the parent: `imagePlaneShape1` free, `perspShape->imagePlaneShape1`
  attached. Any name held across that call is stale and the next `setAttr`
  fails with "No object matches name" while pointing at a node that plainly
  exists. Carry the UUID across instead.
- **Place things at the camera's `centerOfInterest`, not at a fixed
  distance.** `front`, `side` and `top` sit 1000 units out, so anything put
  40 units in front of `front` is 960 units from the character — correctly
  sized, and completely somewhere else.
- **Maya cannot decode `.mp4` or `.mov` on an image plane.** Measured on
  2024/Windows: `coverage` stays -1 for both, in both Movie and Image File
  mode, so the plane loads and silently draws nothing. A MJPEG `.avi` reads
  fine — which is the trap, because it is the format nobody has. This is why
  `core.transcode` exists.
- **Convert video at the SCENE's frame rate, not the source's.** At the scene
  rate frame N of the sequence is frame N of the timeline; keeping a 30fps
  source in a 24fps scene leaves every timing 25% out, consistently, and
  invisibly until somebody tries to match a contact. Read the rate from
  `MTime`, not by mapping the `currentUnit` string — that string is an open
  set (`film`, `ntsc`, `ntscf`, `23.976fps`, ...) and a lookup table goes stale.
- **The mirror plane is NOT always the rig root's YZ.** `xform.mirror_plane`
  reads it off the root's rest matrix, which is right whenever the root
  TRANSFORM carries the placement -- and wrong on a rig that bakes the
  placement into where its joints were built, leaving an identity group on
  top. Measured on a production rig: root at the origin, character at x=-520
  rotated twelve degrees, every left/right pair a thousand units from where
  the root's plane said. `pairing._plane_for_root` now tries the root first
  and fits the plane from the control pairs when the root's pairs almost
  nothing.
- **A derived rest pose cannot see through a constraint.**
  `xform.rest_world_matrix` composes rest LOCAL matrices up the DAG, so a posed
  ancestor is correctly zeroed -- but a CONSTRAINED ancestor's local matrix is
  written by its constraint, so its rest is wherever the constraint currently
  puts it. Measured: an arm hanging off `FKParentConstraintToScapula_R` had its
  rest position drift up to 6 units between frames, and failed the symmetry
  check by 8-11 against a tolerance of 0.76. A captured rest pose
  (`xform._rest_overrides`) short-circuits the whole chain and is the answer
  for those rigs.
- **A facial control board can never pass a symmetry check.** Its `_L`/`_R`
  widgets are squares on a flat panel laid out by spacing -- 24.219 apart on
  every pair, on one measured rig -- so they are not reflections of each other
  and no pose makes them so. One board blocks `capture_rest_pose` for the whole
  character. It is deliberately not auto-excluded: a board pair 24 units out
  and an arm somebody moved 24 units are the same measurement.
- **A QApplication created after `maya.standalone.initialize()` is a fatal
  crash, not an exception** — mayapy dies and writes a crash-recovery `.ma`.
  Creating one *before* initialize works and would make widgets testable
  headlessly; `tests/conftest.py` explains why that is deliberately not done.

---

## Working notes

- **Do not add a third-party runtime *Python* dependency.** See the stack
  table. The one exception is the ffmpeg binary in
  [animkit/vendor/ffmpeg/](animkit/vendor/ffmpeg/), which is a separate program
  invoked over a subprocess, is optional at runtime, and is there because Maya
  cannot decode `.mp4` or `.mov` on an image plane at all. Read that folder's
  README before touching it — the bundled build is GPL and the licence note
  matters.
- **Do not create a git repo or commit** unless asked; there is no history here.
- Prefer extending a registry over adding a bespoke entry point.
- When a name has misled you, the fix is the name — not a comment. There is a
  worked example of exactly that in the README under *Two pieces of debt
  cleared*.
- Phase 5 (viewport drawing via `MPxDrawOverride` / `MUIDrawManager`) is gated on
  profiling that has not been done. Do not start it on the assumption that
  Python is too slow; measure with `scripts/bench.py` first. Going C++ buys a
  per-Maya-version compile matrix, which was the largest cost in the original
  plan.
