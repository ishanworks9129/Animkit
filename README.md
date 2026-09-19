# animkit

Animation tooling for Autodesk Maya. Layer-aware data layer, Qt 5/6 shim, a
tween/blend slider, twenty-two keyframe operations behind one shared target
resolver, pose copy/paste/mirror/flip that works on any rig with no
configuration, named selection sets stored on the rig, and a held-hotkey radial
to reach any of it without looking.

The package is called `animkit`, not `animbot`, so it can be installed
alongside a real Animbot licence without a `sys.modules` collision.

Targets Maya 2022–2026 (Python 3.7–3.11, PySide2 and PySide6).

Working on this with an AI agent? Start it on [AGENTS.md](AGENTS.md) — stack,
layout, how to verify a change, and the ten rules that are not style
preferences. This file is the *reasoning*; that one is the orientation.

---

## Try it in Maya, right now

```python
import sys
sys.path.append(r"C:\Users\ishan\Desktop\Animbot")

import animkit
animkit.startup()

import animkit.ui.panel as panel
panel.show()
```

Select an animated control, put the time cursor between two keys, and drag.
Right-click the tween panel for its settings.

Then drag a video or an image sequence from Explorer straight onto the viewport
— it comes up as reference on that view's camera, starting on the frame you are
sitting on. The **Ref** tab has the same drop zone, plus fade, slip and hide.

For the radial, bind a key to hold — nothing is bound by default:

```python
import animkit.ui.radial as radial
radial.install_hotkey("c", "pose", alt=True)   # then hold Alt+C and flick
```

## Verify it before trusting it

```python
import animkit.selftest as st
st.run()
```

34 checks against temporary nodes it creates and deletes itself. It does not
open a new scene and does not touch any node you did not select; your
selection, current time and animation-layer selection are restored on exit.
Prints a PASS/FAIL table, plus `TIME` rows that report milliseconds instead of
passing or failing — what counts as too slow depends on the rig, so the
self-test prints the number and lets you judge.

It also, deliberately, corrupts your `settings.json` and checks that
`startup()` still comes up on defaults. It backs the file up first and restores
it in a `finally`.

Run it **twice**. The layer checks create `BaseAnimation` as a side effect, and
if run 2 does not match run 1 then cleanup is leaking state and the two runs
are testing different code paths. That is exactly how the
`animLayer -selected` bug was found.

Verified on **Maya 2024 / PySide2**: 33/33, identical across two consecutive
runs.

Four of those thirty-four exist because a `runTimeCommand` body is a *string*
that nothing compiles until an animator presses the key: the self-test compiles
all 72, checks that every radial wedge still resolves to a real operation, and
presses the radial's release command with nothing open — which is what happens
whenever somebody binds it on its own by mistake.

A further check reports whether a 3D viewport could actually be hooked for
drops. It is a measurement rather than a gate under `mayapy`, where there are no
model panels at all, and a gate in a real Maya — which is the only place the
answer means anything.

While iterating on the code:

```python
import animkit
animkit.reload_all()   # close the panel first
```

## Install properly

**Drag [DRAG_AND_DROP_INSTALL.py](DRAG_AND_DROP_INSTALL.py) into a Maya
viewport.** It writes one `.mod` into `<maya prefs>/modules/`, adds an
`animkit` shelf, and calls `startup()` in the running session, so there is
nothing to restart. Drop it in again to uninstall. It points Maya at the
folder it is sitting in rather than copying anything, so put the folder
somewhere permanent first — and it warns you if that looks like Downloads.

By hand, if you would rather: edit the path in
[modules/animkit.mod](modules/animkit.mod) and drop it in
`%USERPROFILE%\Documents\maya\modules\`. Startup comes from
[startup/userSetup.py](startup/userSetup.py), which the `.mod` reaches through
`MAYA_SCRIPT_PATH` — that folder holds one file on purpose, because pointing
at `scripts/` instead would put `bench.py` on every animator's import path.
Studios that drive startup from a managed `userSetup.py` should delete that
line and use [userSetup_example.py](userSetup_example.py) instead.

To hand a build to someone outside the repo, run
[scripts/make_release.ps1](scripts/make_release.ps1). It stages only what a
tester needs, refuses to zip a package with a personal path or a stray `.pyc`
in it, and verifies the bundled ffmpeg is an LGPL build before it will
ship one.

For a studio, put the repo on a network share and put one `.mod` in a path on
`MAYA_MODULE_PATH`. That is the entire deployment story for a pure-Python
tool — no installer, no licence server, `git pull` to update.

## Opening it at startup

animkit opens nothing on its own *except* at the moment it is installed, and
that exception is the point rather than a hole in the rule. House rule 6
applied to screen space says a tool must not put itself in the layout of every
animator who installs it — but somebody who has just dragged an installer into
their viewport has asked for the tool, and an installer that ends with a dialog
and no visible tool has told them something happened somewhere and left them to
find it. So DRAG_AND_DROP_INSTALL.py opens the strip, sets
`ui.open_at_startup` to "strip", and **says both in the dialog**. A preference
somebody has to discover was changed for them is the surprise the rule is
actually about; one that is announced as it happens, with the line to undo it,
is not.

Two mechanisms, and they answer different questions.

**Maya's own restore** handles "it was open when I quit". Every panel here is
a `workspaceControl`, Maya writes the retained ones into the saved workspace,
and recreates them at launch by running `uiScript` -- which is why every
`build()` in [animkit/ui/](animkit/ui/) is documented as cold-interpreter safe
and calls `animkit.startup()` itself before touching anything. That path was
dead until recently: `mayawin.show_workspace_control` passed `retain=False`,
so a closed control was dropped rather than saved, and the restore it was all
written for could never fire.

**`ui.open_at_startup`** handles "it should be there every morning whether or
not I left it open". Empty by default; `"strip"`, `"panel"` or `"both"` open
one or both. It is read by `animkit.open_startup_ui()`, which
[startup/userSetup.py](startup/userSetup.py) calls immediately after
`startup()` inside the same deferred call -- one call, so a panel cannot be
built before the `runTimeCommand`s its buttons carry have been registered.

`open_startup_ui()` is deliberately **not** part of `startup()`. That function
runs from userSetup.py where Maya's UI does not reliably exist yet, and the
rule that it touches no Qt is the whole reason animkit cannot break a Maya
launch. Splitting the UI open into a separate call is what lets the tool open
a panel at startup without giving up that guarantee. It also imports no UI
module at all when the setting is empty, which is the common case -- dragging
Qt into every Maya launch to then open nothing would slow down every
animator's startup for nobody.

## Finding out what testers actually used

Two mechanisms, and neither one is a server.

**Per-recipient builds.** `make_release.ps1 -Recipient "studio-x"` rewrites
[animkit/_build.py](animkit/_build.py) in the staged copy, so the zip carries
a build id and a recipient name. A usage log that comes back can be matched to
the handover it came from, and so can a copy that turns up somewhere it was
not sent. This needs nothing running on anybody's machine, which is what makes
it the cheapest tracking available and the first one to reach for.

**A local usage log.** [animkit/core/usage.py](animkit/core/usage.py) appends
an operation name and a count to a JSONL file beside `settings.json`. A shelf
button and the Help page both export it as a zip the tester can read before
deciding to send it.

Three decisions in there are worth keeping:

**It stays local, and that is the feature.** The strongest sentence in the
tester README is that a TD can grep the whole codebase and find no HTTP client
— which is exactly the sentence a telemetry endpoint would cost. It is also
the practical answer: a studio box is firewalled, and a blocking HTTP call
from a panel button is a Maya hang with your name on it. Collection is a
person exporting a zip and choosing to send it.

**It records nothing about the rig.** No node names, no attribute names, no
file paths, no scene names. A tester is usually working on somebody else's
show under somebody else's NDA, and a log full of `char_hero_L_arm_IK_ctrl`
names an unannounced production. This is why a failed operation records
`RuntimeError` and never the message — the message is where the names live,
and `tests/test_usage.py` asserts a rig name put into an exception does not
reach the file.

**The hook goes in `Operation.invoke()`, and so it misses hotkeys.** Every
click from the panel, the strip and the radial funnels through that one
method, which is what makes 55 operations a four-line change. A
`runTimeCommand` does not: `Operation.command` builds a string that calls
straight into the module. Closing that gap means routing every hotkey through
a logging shim, which changes the command string animators read in the Hotkey
Editor and on the Help page. An honest command string is worth more than a
complete count, so the gap is documented rather than closed.

---

## Layout

| Path | What it does |
|---|---|
| [animkit/vendor/qt.py](animkit/vendor/qt.py) | PySide2/PySide6 shim. The only file allowed to import a Qt binding. |
| [animkit/core/layers.py](animkit/core/layers.py) | Animation-layer-aware curve resolution. **Read this one first.** |
| [animkit/core/targets.py](animkit/core/targets.py) | "Which keys am I acting on." Shared by the tween and every keyframe op. |
| [animkit/core/cache.py](animkit/core/cache.py) | Scoped memoization. Read the docstring before extending it. |
| [animkit/core/settings.py](animkit/core/settings.py) | JSON prefs that cannot break Maya launch. |
| [animkit/core/usage.py](animkit/core/usage.py) | Local usage log for a tester build. Operation names and counts, never a node name. No network, no Maya, no Qt. |
| [animkit/core/xform.py](animkit/core/xform.py) | Matrix layer: rest poses, reflection, frame relations, decomposition. |
| [animkit/core/pairing.py](animkit/core/pairing.py) | Finds a control's counterpart, and `analyse()` — the one symmetry verdict. |
| [animkit/core/rest_store.py](animkit/core/rest_store.py) | Persists a captured rest pose in the scene, by connection not by name. |
| [animkit/core/curves.py](animkit/core/curves.py) | `MFnAnimCurve` access with unit-safe conversion. |
| [animkit/core/undo.py](animkit/core/undo.py) | Undo chunking, refresh suspension, panic button. |
| [animkit/core/scene.py](animkit/core/scene.py) | `MSceneMessage` callbacks, cache invalidation. |
| [animkit/core/selection.py](animkit/core/selection.py) | Selection + Channel Box highlight → plug list. |
| [animkit/core/media.py](animkit/core/media.py) | What a dropped path is: image, movie, or one frame of a sequence. No Maya, no Qt. |
| [animkit/core/transcode.py](animkit/core/transcode.py) | Video → cached image sequence, via the bundled ffmpeg. No Maya, no Qt. |
| [animkit/tools/blend.py](animkit/tools/blend.py) | Blend maths. Imports nothing from Maya. |
| [animkit/tools/tween.py](animkit/tools/tween.py) | `TweenSession` — snapshot / update / commit. |
| [animkit/tools/keys.py](animkit/tools/keys.py) | Keyframe operations + the registry that drives panel, hotkeys and tests. |
| [animkit/tools/pose.py](animkit/tools/pose.py) | Pose capture/apply, mirror and flip. Also `rig_controls` / `controls_in` — which nodes are controls. |
| [animkit/tools/sets.py](animkit/tools/sets.py) | Named selection sets, stored on the rig as tagged `objectSet`s. |
| [animkit/tools/reference.py](animkit/tools/reference.py) | Video and image reference as tagged image planes, free or pinned to a camera. |
| [animkit/tools/registry.py](animkit/tools/registry.py) | The one `Operation` class every registry shares. |
| [animkit/ui/panel.py](animkit/ui/panel.py) | The dockable panel. Tween, Keys, Pose, Sets and Ref in one control. |
| [animkit/ui/style.py](animkit/ui/style.py) | Palette, metrics, stylesheet. The only place a colour is defined. |
| [animkit/ui/icons.py](animkit/ui/icons.py) | 37 vector icons, drawn in code — no image files ship. |
| [animkit/ui/mayawin.py](animkit/ui/mayawin.py) | `workspaceControl` plumbing. |
| [animkit/ui/tween_ui.py](animkit/ui/tween_ui.py) | The slider widget. |
| [animkit/ui/keys_ui.py](animkit/ui/keys_ui.py) | Keyframe operation button panel. |
| [animkit/ui/sets_ui.py](animkit/ui/sets_ui.py) | Selection sets panel. Reads the scene; caches nothing. |
| [animkit/ui/reference_ui.py](animkit/ui/reference_ui.py) | Reference panel: a drop zone and a row per reference. |
| [animkit/ui/viewport_drop.py](animkit/ui/viewport_drop.py) | The event filter that lets a 3D viewport accept a dropped file. |
| [animkit/ui/radial_geom.py](animkit/ui/radial_geom.py) | Radial menu geometry. No Qt, no Maya — so it is testable. |
| [animkit/ui/radial.py](animkit/ui/radial.py) | The held-hotkey radial overlay and its bindings. |
| [scripts/bench.py](scripts/bench.py) | Performance bench. Run it before optimising anything. |
| [animkit/commands.py](animkit/commands.py) | `runTimeCommand` registration. |

---

## Four decisions already baked in

**1. Animation layers are handled in the data layer, not bolted on later.**
Nothing in this codebase calls `listConnections` looking for an animCurve, and
nothing calls `getAttr` on a value it intends to write back. Everything goes
through `layers.resolve_curve()`, which walks the `animBlendNode*` network to
find the curve for a specific layer. `getAttr` returns the *flattened* result
of the whole layer stack — writing that back is how tools silently corrupt
layered animation.

Policy: **write to the active layer, warn once per operation when more than
one layer is involved, skip locked and muted layers.** Refusing outright gets
the tool bypassed; silence gets it uninstalled.

**2. Drags write through the API, commits go through `cmds`.**
`cmds.setKeyframe` on a 300-control rig is ~100× slower than
`MFnAnimCurve.setValue` — the difference between a slider that tracks the
mouse and one that lags. But API writes never enter the undo queue. So
`TweenSession.commit()` rewinds the API writes to the snapshot first, *then*
replays the final value through `cmds` inside one undo chunk. The undo queue
ends up holding one honest entry, correct in both directions.

**3. Interpolation always reads from the mouse-down snapshot.**
Never from the current value. Blending from the previous result compounds, and
the slider accelerates away from the cursor. `TestNoCompounding` in
[tests/test_blend.py](tests/test_blend.py) pins this.

**4. Refresh is suspended per mouse-move, not across the drag.**
Holding `refresh(suspend=True)` open for a whole drag — as is often suggested —
freezes the viewport the animator is trying to watch. Scoping it to one
handler collapses N attribute writes into one redraw, which is the win you
actually wanted. `undo.force_resume_refresh()` is the panic button; bind it to
a shelf button while developing.

---

## Blend modes

| Mode | At 0 | At ±1 |
|---|---|---|
| **Between** (default) | holds the current pose | previous / next key |
| **Linear** | midpoint of the neighbours | previous / next key |
| **Average** | holds the current pose | average of both neighbours |
| **Ease** | holds the current pose | previous / next key, eased |

## What the slider acts on

`begin()` picks its own target set:

| Graph Editor | Target |
|---|---|
| keys selected | exactly those keys, wherever they sit on the timeline |
| nothing selected | the current time, inserting keys where a channel has none |

Selected-key mode never changes curve topology and never resolves a layer — the
curve the animator selected keys on *is* the target, so it cannot write to a
layer they were not looking at. Each selected key blends toward its own
immediate neighbours, so a multi-key selection does not collapse toward one
shared value.

Both modes share every path after `begin()`, because an entry is identified by
`(curve, index)` rather than `(plug, time)`. Commits go through
`cmds.keyframe(edit=True, index=…)` — the only form that works for a key
nowhere near the current time, and it avoids a second layer resolution that
could disagree with the first.

`t` is not clamped in `blend()` — drag past the ends to overshoot. Clamping
lives in the UI, and there are deliberately two limits:

| Constant | Value | Applies to |
|---|---|---|
| `OVERSHOOT` | ±200 | dragging the bar |
| `NUMERIC_LIMIT` | ±120 | typed values, wheel nudges, quick buttons |

The drag limit is generous because reaching it means moving a full bar-width
past the edge — always deliberate. Typing `200` is as easy as typing `20`, so
the numeric path needs a guard rail doing the work that physical effort does on
a drag. Both are enforced in `_apply_once`/`set_value`, not in the maths.

Neither number came from Animbot — they are judgement calls. If you want strict
parity, check the real thing and change the two constants.

At the first or last key the slider simply has no travel in that direction,
rather than snapping somewhere arbitrary.

---

## Keyframe operations

Fifteen of them, in [animkit/tools/keys.py](animkit/tools/keys.py):

| Group | Operations |
|---|---|
| **Timing** | `-1`, `+1`, Faster, Slower, Snap |
| **Tangents** | Auto, Spline, Linear, Flat, Step, Hold |
| **Cycle** | Cycle, Cycle+, Hold Ends |
| **Edit** | Delete |

They all act on the same target set as the slider: keys selected in the Graph
Editor, or the current frame on the selected controls.

**`keys.OPERATIONS` is the single source of truth.** The button panel, the
`runTimeCommand` registration and the parametrised test harness all iterate it,
so a new operation appears in all three — including the tests — the moment it is
added. That is deliberate: new operations arrive with coverage rather than with
a TODO.

Every operation goes through `keys._apply`, which hands it one lazily-opened
undo chunk, the shared target set, the layer policy, and a `dgdirty`. An
operation that opens its own chunk or resolves its own targets is an operation
that gets the layer policy subtly wrong six months from now.

### Two bugs the parametrised harness caught immediately

Both were silent, both looked like working code, and neither was findable by
reading it.

**1. `cmds.setInfinity` on an animCurve node does nothing.** No error, no
warning — it returns cleanly, changes nothing, and querying the curve node
returns `None`. The cycle operations reported success on every call while having
no effect at all. Infinity now goes through `setAttr(curve.preInfinity, …)`,
which is undoable and unambiguous about which layer it hit.

Mind the enum while you are in there: `Constant=0, Linear=1, Cycle=3, Cycle with
offset=4, Oscillate=5`. **There is no 2.** Passing 2 is silently rejected and
leaves the previous value in place — the same class of failure again.

**2. Moving a key onto an occupied frame does not merge it.** Maya nudges it a
sliver aside instead: frame `1.0000001700680272`, which reads as "1.0" in the
Graph Editor and plays back wrong. (`option="insert"` and `"segmentOver"` refuse
the move outright, which is no better.) `keys._move_keys` now clears the
destination frame first, then re-resolves indices *after* those deletions,
because deleting a key renumbers every key after it.

### The mirror plane, and when the root does not know it

`xform.mirror_plane` takes the plane from the rig root's rest matrix. The
reasoning is sound -- a rig built at x=500, or rotated, is still symmetric
about *itself* -- and it holds on every rig whose root transform carries the
placement.

It does not hold on a rig that bakes the placement into where its joints and
controls were *built*. Measured on a production character: `Group`,
`MotionSystem` and `FKSystem` all at the origin, the character standing at
x=-520, z=790, rotated about twelve degrees. The plane came out as the world
YZ, every left/right pair landed a thousand units from where it should, and the
mirror refused on a rig that is perfectly symmetric.

`pairing._plane_for_root` handles it now. **The root is tried first and kept
whenever it works** -- it is correct on most rigs, costs one matrix read, and
changing the answer underneath rigs that already mirror correctly would be a
poor trade. Only when the root's plane pairs almost nothing does
`pairing.fit_plane` ask the controls instead: every left/right pair implies
exactly one plane, their perpendicular bisector, and on a symmetric rig they
all imply the same one. On that rig its eight facial controls agreed to three
decimal places.

It **votes rather than averages**, because the outliers are not noise. A
control board's widgets are laid out by spacing and a posed limb is not at
rest; averaging those in would drag the plane off the answer the majority
already agree on. Below four supporting pairs it declines and keeps the root's
plane -- two pairs agreeing could be a pair of props either side of a
character.

### What a derived rest cannot see

`rest_world_matrix` walks to the top of the DAG composing rest *local*
matrices, so an ancestor that is itself a posed control does not contaminate
the answer. A **constrained** ancestor does: its local matrix is written by the
constraint, so its rest is wherever the constraint currently puts it.

Measured on the same rig, whose arm hangs off `FKParentConstraintToScapula_R`
with further point constraints at the elbow and wrist offsets:

| Control | rest x @ f0 | rest x @ f30 | drift | mirror error | tolerance |
|---|---|---|---|---|---|
| FKShoulder_R | -519.183 | -516.451 | 3.028 | 3.833 | 0.785 |
| FKElbow_R | -503.616 | -505.244 | 1.628 | 8.166 | 0.772 |
| FKWrist_R | -486.124 | -491.897 | 5.989 | 11.595 | 0.757 |

The rest pose moves with the animation, which is not something a rest pose is
allowed to do. `capture_rest_pose` is the answer for these rigs and not a
nicety: a captured rest goes into `xform._rest_overrides`, which
`rest_world_matrix` short-circuits on, so the constraint chain is never walked.

### Bake to Ns

Seven operations, `Bake 1` through `Bake 7`, generated from one `BAKE_LABELS`
constant so a step cannot exist without a button and a `runTimeCommand` to
reach it. The parametrised harness covers all seven the moment they appear,
which is the whole reason for generating them rather than listing them.

The invariant is narrow and deliberate: **at every frame a bake keeps, the
animation is worth what it was worth before.** Nothing is promised about the
shape between those frames, because replacing that shape is what a resample
*is*. The range is the selected keys' extent when the animator picked keys in
the Graph Editor, and the curve's own first-to-last key otherwise — the two
target modes genuinely mean different things here and cannot share an answer.

Three things that look like details and are not:

- **The end frame is kept even when it is off the step grid.** Baking 1–11 on
  fours lands on 1, 5, 9 and then 11. The last interval is shorter than the
  step, which reads as an off-by-one in a key count; dropping it instead would
  move the end of the shot.
- **Samples are read before anything is written.** They come off the curve that
  is about to be replaced, so a read interleaved with the writes would be
  asking a curve that is half original and half baked — and it would still
  produce a plausible-looking result.
- **The samples are written first and the strays removed afterwards, not the
  other way round.** Clearing the range first empties the curve, and Maya
  deletes an animCurve node when its last key goes: the next `setKeyframe`
  then failed with *No object matches name* on a node that existed one line
  earlier. Same disappearing-curve trap `targets.dirty()` guards against.

Tangents become `auto`, stated explicitly rather than inherited from Maya's
default tangent preference — that preference is a per-user setting, so a bake
that took it would give two animators different curves from the same input.

### Mirror and flip across a range

`mirror_range` and `flip_range` do what Mirror and Flip do, on every frame the
selection is keyed on rather than on the one the playhead is parked on. They
reuse `pairing.analyse` and the existing mirror maths unchanged; what is new is
which frames get visited and in what order the reading and writing happen.

**Only frames that already carry keys.** An empty frame is a question nothing
in the scene can answer, and inventing an answer for it is house rule 7's
territory — the same ambiguity that let Reset collapse a facial control board.

**It reads the whole range, then writes it.** Two passes, for two reasons that
each produced a wrong answer when it was one:

- The pose read at frame 9 has to be the pose the *animator* made, not the one
  this operation left behind at frame 1. Read lazily, a mirror feeds its own
  output back into its own input.
- The mirror's direction is decided by asking the scene which side is posed,
  and that stops being answerable the moment the other side has been written.
  Frame 1 mirrored correctly; every frame after it found both sides posed,
  reported the pair as ambiguous, and skipped it. The symptom was a range
  mirror that silently only did its first frame — and it looked like it had
  worked, because the frame you were parked on was right.

So the direction is settled once, for the whole range: a control posed on any
frame in it drives its counterpart on all of them. The symmetry verdict is a
property of the rest pose, so it cannot change frame to frame, and it is
checked once, before anything is written — refusing halfway would leave a range
that looks finished and is not.

**The playhead moves, and it has to.** A mirror is computed from world
matrices, so the rig genuinely has to be evaluated at each frame; there is no
single plug to read, so the timed-read trick that makes arc sampling cheap does
not apply. `scripts/arc_bench.py` measures what scrubbing costs — it is
proportional to the whole scene — which makes this the operation in `tools.pose`
most likely to feel slow on a heavy shot.

---

## Settings

JSON at `<userPrefDir>/animkit/settings.json`. The tween panel persists its mode,
quick-button values and both limits; right-click the panel to change them.

**The hard requirement: a corrupt settings file must not raise.** `startup()`
runs from `userSetup.py`, so an exception there does not merely break animkit —
it degrades Maya launch for every animator with the module installed, before
there is any UI to report it in. Missing, empty, truncated by a crash,
hand-edited into invalid JSON, unreadable, or a directory where a file should
be: all of it ends at shipped defaults plus one line in the log.

Values are **type-coerced**, not merely parsed. "It did not raise while loading"
is worth little if it then hands a widget the string `"banana"` where a float
belongs and the traceback lands three seconds later pointing at the UI instead
of at the file. `NaN` and `Infinity` are rejected too — `json.loads` accepts
both, and every clamp downstream then fails open, because
`max(lo, min(hi, NaN))` returns `NaN` without complaining.

Writes are atomic (temp file, then `os.replace`), since the failure this module
exists to survive is most easily caused by crashing halfway through writing the
file it reads.

---

## Performance

Measured, not guessed. [scripts/bench.py](scripts/bench.py):

```powershell
& "C:\Program Files\Autodesk\Maya2024\bin\mayapy.exe" scripts\bench.py --controls 300 --calls
```

```python
# or, in Maya, against the rig you actually animate on
import scripts.bench as bench; bench.run_on_selection()
```

Two numbers matter: `begin()` at mouse-down, and `update()` per mouse-move.
Anything over ~16ms in `update()` feels like lag.

Maya 2024, headless, synthetic rig, full-body selection:

| | before | after |
|---|---|---|
| `begin()`, 300 controls / 2700 plugs | 1519 ms | **458 ms** |
| `begin()`, 40 controls / 520 plugs | — | **64 ms** |
| `update()` per mouse-move, 1800 keys | 24.7 ms | **27 ms** |
| `plugs_from_selection`, 2700 plugs | 278 ms | **98 ms** |
| `target_layer` × 2700, in scope | 408 ms | **2.7 ms** |
| `cmds` calls per `begin()` | 17,701 | **4,502** |

What changed:

- **`selection._survey_node` moved to OpenMaya.** The old path made three
  `getAttr` calls plus one `attributeQuery` *per attribute* — 11,400 command
  round trips at mouse-down. It now makes **zero** `cmds` calls.

  It is deliberately **not** the one-call `cmds.listAttr(keyable, unlocked,
  settable)` collapse that looks obvious: `-settable` does **not** exclude a
  connection-driven channel. It returns a constrained `translateX` happily,
  where both `getAttr(settable=True)` and `MPlug.isFreeToChange()` refuse it.
  `TestSurveyMatchesOracle` pins the API path against the original `cmds` one on
  constrained, directly-driven, locked and layered controls.

- **`animkit/core/cache.py`**, wired into the layer queries. Scoped, *not* a
  long-lived cache keyed on node name — see below.

### The cache is scoped on purpose

`selected_layers()` changes when the animator clicks a different row in the Anim
Layer editor. Lock state changes when they lock a channel. A layer gets muted.
**None of those fire an `MSceneMessage`.** A cache invalidated only on scene
change would serve the previous answer, and the tool would write to the layer
they were looking at a minute ago, silently — the exact bug the layer policy
exists to prevent.

So a cache lives for the duration of *one operation* and no longer. Outside a
`cache.scope()` every wrapped function calls straight through, uncached and
correct, which makes the worst case "slow" rather than "wrong" — the right way
round for a mistake to fail. The scene callback is a backstop, not the
mechanism.

### What is left, and why it was left

`begin()` is still over the 100ms guide at 300 controls. Two honest caveats
before anyone optimises further:

- **`update()` is 90% `cmds.dgdirty`** — 12.3ms of a 13.6ms call on 1800 keys,
  against 1.4ms for all the blend maths and curve writes combined. It is not
  removable: a topology change leaves DG reads stale without it.
  `dgdirty(allPlugs=True)` benches 4× faster on a synthetic scene and would be
  far worse on a real one. Fewer keys per drag is the only real lever.

- **Layers cost more than control count.** `resolve_curve` over 900 plugs on a
  2-layer rig costs 917ms raw, against 200ms over 2700 plugs unlayered — the
  blend-node walk multiplies per layer. The remaining `cmds` calls in `begin()`
  are `_source_plug` and `_is_anim_curve` inside that walk, and moving them to
  `MPlug.source()` would take most of what is left. It has not been done because
  that walk is the safety-critical path the entire layer policy rests on, and it
  deserves its own probe against constrained, driven and deeply layered rigs
  first — not a guess.

Headless numbers are a **floor**, not the real feel. There is no viewport, so
`update()` measures animkit's share of the frame budget and nothing else. Only
`bench.run_on_selection()` on a production rig proves the slider feels right.

---

## Pose copy, paste, mirror and flip

| Operation | What it does |
|---|---|
| **Copy** / **Paste** | the selected controls' pose, onto the same controls |
| **Paste Opp.** | the copied pose, onto the opposite controls |
| **Mirror** | mirror the selection onto its counterparts |
| **Flip** | swap the two sides |
| **Set Rest** | declare the current pose to be this rig's rest — only for a rig that does not zero its controls |

Mirroring is a **transform between capture and apply**, not a separate code
path: `paste` and `paste_mirrored` differ by one function call. That is why
copy/paste was built first.

### No configuration, on any rig

The usual way to build this is a per-rig table — naming convention, plus which
channels negate per control. That table is unnecessary, because **the rig's own
rest pose already contains it.** For a control and its counterpart:

```
Q = W_rest(source) · reflection · W_rest(target)⁻¹        (row-vector)
```

On a symmetric rig `Q` comes out a signed permutation with determinant −1 —
exactly the `{translateX: -1, rotateY: -1, rotateZ: -1}` table people hardcode,
except *derived from that rig, per control*. Mirroring is then a change of basis
on the pose delta:

```
d_target = Q⁻¹ · d_source · Q
```

Flipped joint orients, a different rotation order per side, a non-zero
`rotateAxis`, and a mirror plane away from the world origin all cost nothing,
because nothing ever assumed the two sides' frames agreed.

Conjugation also removes the handedness problem. `det(Q) = −1`, so
`det(Q⁻¹ · d · Q) = det(d)` — the two reflections cancel and the result is a
proper rotation. Reflecting a world matrix directly does not have this property:
`W · reflection` has a negative determinant, so **no rigid transform can ever
equal it.** An early version of the test asserted exactly that and was
unsatisfiable rather than strict.

### Finding the counterpart

**Naming proposes, geometry decides.** A library of the conventions rigs
actually use (`L_`/`R_`, `_l`/`_r`, `Left`/`Right`, `lf`/`rt`, namespaces) is
tried first because it is fast, but a candidate is only accepted if its **rest
position is the reflection of the original's** and its settable transform
channels match.
If naming finds nothing usable, the search falls back to geometry alone and
pairs on reflected rest position — which is what makes this rig-agnostic rather
than merely convention-agnostic.

Because geometry is the gate, the naming patterns can be liberal: a pattern that
fires inside `clavicle` costs nothing, since the candidate fails the position
check and is discarded. A control whose rest position reflects onto itself is a
centre control, and its counterpart is itself.

### What is *not* derivable, and what happens then

**Custom attributes.** No matrix can say whether `ikFkBlend` or `fingerCurl`
should negate — they have no spatial meaning. They are copied across unchanged,
which is right the overwhelming majority of the time.
`pose.MIRROR_NEGATE_ATTRS` is the override for the rare signed one: a short list
for exceptions, not a config the tool needs in order to work.

**Where "rest" is.** Nothing in a scene distinguishes "rig build offset" from
"the animator moved it and did not key it" on a settable, unkeyed channel. So the
rest pose is derived — **attribute defaults for channels that carry animation**,
current values for everything else — and then **verified**. The line is drawn
*per channel*, which is what makes an FK joint work: its rotation zeroes at rest,
while its translate is the bone length and must not be zeroed.

Animation, not lock state, is the signal, and that was a correction. The rule
used to be "keyable, unlocked and settable", which describes what a rigger left
editable rather than what an animator poses — and since riggers do not lock the
internals, 1521 of 1529 transforms on an AdvancedSkeleton character qualified.
The rest pose zeroed almost every node in every parent chain, the whole rest
hierarchy collapsed onto the world origin, and the symmetry check *passed*,
because two controls both reflecting onto the origin pair happily. See
`xform.animated_channels`; it is the same mistake the `posable_channels` rename
below is about, met one layer down.

When the derived rest is not symmetric, the mirror **refuses** and says so:

```
animkit: rig is not symmetric at rest: L_arm_ctrl/R_arm_ctrl off by 5.000. Put
the rig at rest and run capture_rest_pose() if its controls do not zero to
their defaults.
```

One message for both ways a rig can be asymmetric — the pair that verified badly
and the counterpart rejected before it could become a pair. That merge is
described under **Two pieces of debt cleared**.

Refusing is the whole point. A mirror on an asymmetric rest pose does not fail
loudly — it produces a pose whose silhouette reads and whose limb is wrong. **Set
Rest** is the escape hatch: put the rig at rest, press it once, and every frame
relation afterwards comes from that snapshot.

### Three invariants that need no configuration

Which is what makes a rig-agnostic mirror testable at all:

1. **Involution** — mirroring left→right then right→left restores the left.
2. **Fixed point** — mirroring an already-symmetric pose changes nothing.
3. **Geometry** — every paired control's world *position* is the reflection of
   its counterpart's.

Invariant 3 catches the "nearly right" failure, and over a deep hierarchy it
pins rotation too: a wrong rotation mirror puts the children in the wrong place
even when the parent lands correctly.

The test fixture is hostile on purpose — 180° flipped right side, `rotateAxis`,
a joint with `jointOrient`, different `rotateOrder` per side, three levels deep,
**and the character moved away from the origin.** `test_naive_channel_negation_would_have_failed`
asserts the mirrored rotation is *not* any sign-flip of the source, so the
fixture cannot quietly stop being hostile.

### The bug the hostile fixture caught

The pose delta has to be **local (parent space), not world**. A world delta
`W_posed · W_rest⁻¹` absorbs every ancestor transform, so conjugating it mirrors
the character's placement along with the pose. Measured, with the root at
x=100 and only the arms selected:

| delta | root at origin | root at x=100 |
|---|---|---|
| world | correct | arm lands at **−110** instead of **+90** |
| local | correct | correct |

A rig sits at the origin in a test scene and never in a shot, so this is
invisible until it is in front of an animator. Working locally is also *simpler*
— a local matrix does not depend on where its parent ended up, so a hierarchy
needs no parents-first ordering at all.

---

## Selection sets

```python
from animkit.tools import sets
sets.store("left hand")     # saves the current selection, on this rig
sets.recall("left hand")
sets.recall_slot(1)         # what a hotkey binds to
```

Or the **Sets** panel (`animkitSetsShow`): type a name, press Store, and every
set on the current rig gets a row with its hotkey slot, its size, and buttons to
recall, replace and delete it.

### Why an `objectSet` and not a JSON file

Because a selection set is a list of **nodes**, and only Maya can keep a list of
nodes correct. Written to a preferences file it becomes a list of node *names*,
and every one of these then rots silently:

| What happens | What a name-based store does |
|---|---|
| the animator renames a control | the name no longer resolves |
| the rig is referenced under a namespace | every name is wrong by a prefix |
| the same rig is loaded twice | one name matches two nodes |
| a control is deleted | a stale name nobody notices |

An `objectSet` holds real connections. Renames follow, namespaces are irrelevant
because nothing is matched by name, two copies of a rig have two sets, and a
deleted control leaves the set quietly. It is saved in the scene file, so the
sets are there tomorrow with no preferences file to lose, and they travel to
whoever opens the shot. [tests/test_sets_maya.py](tests/test_sets_maya.py) has a
test per row of that table.

### "On the rig" — and where the node actually lives

Each set carries a message connection to its rig root, so a shot with three
characters keeps three sets of sets and a recall picks the right one. The set
*node* is created in whatever file is currently open, which is the only thing
Maya allows and also the right behaviour:

- made by an **animator in a shot** → lives in the shot, travels with the shot
- made by a **rigger in the rig** → arrives with every reference of it, for free

No special case distinguishes the two. Referenced rigs work because the
connection's *destination* plug is the local set's own attribute, so nothing is
written into the referenced file and no reference edit is created.

Recognition is by tag (`animkitSelectionSet`), never by node name — same rule as
everywhere else in this codebase. Rename the node in the Outliner; nothing
notices.

### Slots, and why recall is by number

A hotkey is registered at **startup, before any scene is open**, so it cannot
name a set that does not exist yet. It names a *slot* — a small integer stored
on the set — and the slot resolves against whichever rig the animator is working
on. One keypress means "this character's left hand" for every character in the
shot. `animkitSetRecall1`–`6`.

With nothing selected, a scene holding exactly one rig with sets is still
unambiguous and resolves; a scene holding two says so and does nothing, rather
than guessing.

---

## Reference media

```python
from animkit.tools import reference
reference.drop(["C:/ref/walk.0001.png"])   # what drag-and-drop calls
reference.slip(-1)                          # nudge it a frame earlier
reference.toggle()                          # reference off, look at the rig
```

Or **drag a video or an image sequence straight onto the viewport**. An
`.mp4`, `.mov` or `.avi` is converted to a cached image sequence on the way in,
at the scene's frame rate, because Maya cannot decode the first two on an image
plane at all. It stands up in front of that viewport's camera, at the image's
own proportions, starting on the frame you are sitting on — as an ordinary
object you can move, rotate and scale. The **Ref** tab (`animkitRefShow`) has a drop zone that does the same
thing, a row per reference with a fade slider and a frame offset, and the ten
operations as buttons.

An animator working from reference does four things all day — put it up, line it
up with the animation, fade it back to see the rig through it, and hide it. Each
is one operation, one button and one bindable command, and every one is a single
undo step. Placing and scaling it is a fifth, and that one is just Maya's own
move and scale tools, because a reference is an ordinary object.

### Why an image plane and not a floating video window

Because the reference has to be in the same space as the animation to be worth
anything. A window beside the viewport gets compared by eye across a screen; an
image plane at the back of the camera frustum is behind the character, at the
same scale, in the same frame, and it plays off the same time slider — so
scrubbing scrubs both and there is nothing to keep in sync. It is saved in the
scene, so tomorrow opens with the reference already up.

The cost is that it belongs to a camera. That is why a drop is aimed at the
viewport it landed on rather than at a global default: the filter that catches
the drop carries the panel's name, so the camera is known without any
hit-testing.

### A drop is not a list of files

An animator with a 240-frame reference has 240 PNGs in a folder. They select all
of them in Explorer and drag, because that is what selecting a sequence looks
like. Taken literally that is **240 image planes** stacked on one camera — a
scene that has to be undone before anything else can happen.

So `core.media` collapses a drop. Every path that is a frame of the same
sequence becomes one item, and dropping a *single* frame produces the identical
item, because the sequence is discovered from the directory rather than from how
many files happened to be selected. Dropping the folder means the same thing
again. Three gestures, one result — which is what all three of them meant.

Movies are never collapsed into one another: `take_01.mov` .. `take_09.mov` is
nine takes, and merging them would silently discard eight of the animator's
files.

### The frame number is the *last* digit run

`shot_010.0001.exr` has two digit runs and only the second is the frame. Both
obvious patterns get it wrong — a lazy `.*` in front takes `010`, and a greedy
one backtracks to the single digit `1` and reports a padding of 1. The pattern
is anchored to the extension instead, so the digits can only match a run the
extension follows immediately.

That one mistake is the difference between a reference that plays and one that
shows frame 1 for the whole shot, and it is invisible until somebody scrubs.

Padding is load-bearing in the other direction too. Maya substitutes the frame
number using the padding of the name it was given, so a folder holding both
`ref.0001.png` and `ref.1.png` is one sequence to a person and two to Maya.
`sequence_of` reports the frames **Maya will actually find** and flags the rest,
because a frame count that disagrees with what scrubbing shows is worse than no
frame count at all.

Gaps are reported, not repaired. Maya holds the last good frame across a hole
without saying anything, so a half-rendered sequence reads as a reference that
hitches rather than as missing files.

### "Did it load" has a real answer

`coverageX` on the image plane shape is `-1` until Maya has read pixels, and the
image's true width afterwards. So the loader does not trust the extension and
does not guess: it sets the file, asks, and **refuses** an image Maya could not
read rather than leaving a blank plane on the camera that reads as the tool
having done nothing.

A movie that reaches Maya anyway — conversion switched off, or no ffmpeg on
the machine — is the one place this judgement inverts. It is **kept** rather
than deleted, with a message saying why, because an animator who dropped a
perfectly good `.mp4` and got nothing at all has been told nothing at all. A
visible empty plane plus a warning says exactly what to do next.

### A video is converted, not handed over

Drop an `.mp4`, `.mov` or `.avi` and you get a reference. Not a movie image
plane — a cached image sequence, converted before Maya ever sees the file.

That is not a preference. **Maya cannot decode `.mp4` or `.mov` on an image
plane at all.** Measured on 2024 / Windows:

| dropped file | `type=Movie` | `type=Image File` |
|---|---|---|
| `.mp4` (H.264) | `coverage -1` — draws nothing | fails |
| `.mov` (H.264) | `coverage -1` — draws nothing | fails |
| `.avi` (MJPEG) | `coverage 640` — works | fails |

So the one container that works natively is the one nobody has, and the two an
animator actually owns both produce a plane that loads without complaint and
draws nothing at all. Handing the file to Maya is not a fallback, it is a
silent failure.

A sequence is also simply better reference. Maya scrubs a sequence frame by
frame; a movie image plane *seeks*, and seeking backwards through long-GOP
H.264 is why scrubbing video reference feels like treacle.

### The frame rate is the scene's, not the video's

The conversion resamples to the **scene** rate. This is the decision that makes
the reference animatable: at the scene rate, frame N of the sequence is frame N
of the timeline, so a 3-second clip in a 24fps scene is 72 frames and an action
one second in lands on frame 24. Keeping the source's 30fps instead would leave
every timing in the reference 25% out — consistently, and invisibly until
somebody tries to match a contact.

The rate comes from `MTime`, not from mapping the `currentUnit` string. That
string is an open set — `film`, `ntsc`, `pal`, `ntscf`, `23.976fps` — and a
lookup table is a list that goes stale the next time Autodesk adds a rate.
Asking MTime how many UI frames fit in one second is the same question with an
answer that cannot drift.

### Cached by content

Converting takes real seconds and an animator drops the same file repeatedly —
reopening a shot, undoing, trying it on another camera. Conversions are cached
under the animkit prefs, keyed on the source path, its size and mtime, and
every setting that changes the pixels. Re-dropping the same video is a cache
hit measured at **0.12s**; re-rendering the video and dropping it again
reconverts. A key on the path alone would serve yesterday's render forever.

The frames go to the prefs folder rather than beside the video on purpose:
hundreds of files written into a reference folder nobody asked to have written
to, often on a shared drive, is not a good trade for saving a lookup. The Ref
tab shows what the cache is costing and has a Clear button;
`animkitRefClearCache` is the same thing bindable.

Cancelling a conversion deletes the half-written folder. Served as a cache hit,
a partial sequence would be a silently truncated reference — so a finished
conversion writes a marker, and only a marker makes a hit.

### ffmpeg is bundled, and that has a licence

Maya ships no ffmpeg, so animkit carries one in
[animkit/vendor/ffmpeg/](animkit/vendor/ffmpeg/) — which is what makes a
dropped video work with no setup at all. It is looked up as: the
`reference.ffmpeg` setting, then the bundled binary for this platform, then
`PATH`. **None of it is required**: with no ffmpeg anywhere, animkit falls back
to handing the movie to Maya exactly as before and says so, and images and
image sequences are unaffected.

The bundled Windows build is **LGPL v3** — `n8.1.2-54-gc573a95381`, from
[BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds), configured
`--enable-version3` with no `--enable-gpl`. It replaced a 217 MB GPL v3 build,
and the swap was worth making for one reason: a GPL binary obliges everyone who
passes animkit on to offer ffmpeg's corresponding source to every recipient,
which turns handing an evaluation copy to a studio into a compliance exercise.

It costs nothing, because **animkit only ever decodes**. The GPL parts of
ffmpeg are encoders — `libx264`, `libx265` — and animkit writes MJPEG and PNG,
both native. This build has the GPL-only libraries explicitly disabled, which
is what `make_release.ps1` checks for: it runs `ffmpeg -version` on whatever is
in the slot and **refuses to build a package** if it finds `--enable-gpl`. A
licence problem that leaves the building inside a zip is one nobody notices
until it matters.

Passing the LGPL binary on still requires shipping the licence text and being
able to point at the corresponding source. Both are recorded in
[animkit/vendor/ffmpeg/README.md](animkit/vendor/ffmpeg/README.md), along with
the swap instructions — and `tests/test_transcode.py` runs 32 real conversions
against whatever binary is present, so it says immediately whether a
replacement can do the job.

It is still 126 MB against ~500 KB of Python, and `make_release.ps1 -NoFFmpeg`
leaves it out for a pipeline that would rather supply its own.

### Time is one subtraction

Maya wires `frameExtension` to time the moment `useFrameExtension` is set — by
itself, through a `timeToUnitConversion` node, so there is no expression to
write and writing the one Maya's own UI writes fails outright. It then draws
image `frameExtension + frameOffset`.

So `frameOffset` is the only thing deciding which frame of the reference is on
screen now, and every timing operation is arithmetic on it:

```
offset = first_frame_of_the_sequence - the_frame_it_should_appear_on
```

A drop lands at the **current frame**, not at frame 1, because the animator is
sitting on the frame they want it to start at — that is what made them drop it
there. `Sync` re-aims it there later, and `-1` / `+1` are the nudges that line
reference up against animation. Those two are the ones worth a hotkey.

### Free by default, pinned if you want it

A reference is a **free image plane**: a top-level object in the Outliner with
working move, rotate and scale handles. Select it and the ordinary tools do the
ordinary thing to it.

That is not how it started. The first version attached every reference to a
camera, which is the conventional choice for video reference — it rides the
view and always fills the frame. It is also an object an animator cannot touch,
and the reason is worth writing down because the obvious fix does not work:

- a camera-attached plane is parented under the camera **shape**, in Maya's
  under-world, and the move tool does not reach it;
- `lockedToCamera 0` does **not** free it. The plane stays parented either way;
- so free vs attached is decided when the plane is *created*, not configured
  afterwards.

`Pin` converts one to the other in place, via `imagePlane -e -detach` and
`-e -camera`, keeping the image, the frame offset and the opacity. Pinned is
still the better answer for a long video reference you want locked to the
frame; free is the better answer for a reference board you want to place next
to the character, so free is the default and `reference.attach` remembers your
choice.

### Two numbers that have to be measured, not read

**A free plane is sized by `width`/`height` — not `sizeX`/`sizeY`.** They are
separate, unconnected attributes, and `sizeX` is the camera-attached pair
(aperture inches). Set the wrong one on a free plane and nothing happens at
all: it stays Maya's default **square 10×10**, which letterboxes every 16:9
reference anybody will ever drop. The plane is built from the image's real
proportions instead, read back off `coverageX`/`coverageY` — the same attribute
that already answers "did this file load".

This is only measurable in a real viewport: `exactWorldBoundingBox` on an image
plane is degenerate under `mayapy`, so the headless suite can assert the
attribute ratio and the in-Maya self-test has to assert the rest.

**A new plane is placed at the camera's `centerOfInterest`**, the point it is
aimed at, rather than at a fixed distance. A fixed distance is wrong exactly
where it matters: `front`, `side` and `top` sit 1000 units out, so 40 units in
front of `front` builds a correctly sized reference 960 units from the
character — which reads as the drop having failed. Centre of interest puts it
on the origin for those three and beside the character for a framed `persp`.
`reference.distance` overrides when a shot needs it.

`Frame` is the way back when a free plane ends up off screen, which a
camera-attached one never could.

### Detaching renames the node

An image plane's name encodes where it is parented — `imagePlaneShape1` while
free, `perspShape->imagePlaneShape1` once attached — so `Pin` renames it. Every
name held across that call is stale, and the symptom is a `setAttr` failing
with *"No object matches name"* against a node that plainly exists. `pin()`
carries the **UUID** across the edit instead, which is the same
identify-by-identity rule this codebase applies to rig nodes, turned on
animkit's own node.

### Recognition is by tag, and the camera by connection

An image plane is animkit's if it carries the `animkitReference` attribute — the
same rule as everywhere else here. A shot already has image planes the layout
department put on the shot camera, and a tool that decided ownership from
`imagePlane*` would delete one of those on `Remove`. It also means the node can
be renamed in the Outliner without breaking anything.

The camera is read from the plane's `.message` connection into
`cameraShape.imagePlane[]`, not from its parent path — which for an image plane
is Maya's under-world form, `|persp|perspShape->|imagePlane1`, and is not
something to be parsing.

### A drop must not steal the selection

`cmds.imagePlane` selects what it creates and has no flag to stop it. Two things
go wrong if that is left alone. The animator loses the controls they had
selected, to a gesture that has nothing to do with selection — and because every
reference operation targets the selection first, the plane left selected by the
drop becomes the only thing the next `Fade` or `Slip` touches. Two references
up, press `Fade`, one of them moves, and nothing anywhere says why.

`load()` puts the selection back.
`test_a_drop_does_not_steal_the_selection` is the test that found it.

### Why the viewport filter is per panel

`installEventFilter` only sees events delivered to the object it is installed
on, never to that object's children. Catching a drop over a viewport from the
main window therefore needs a filter on `QApplication`, which then sees every
mouse move Maya makes for the rest of the session — a cost paid all day for an
event that happens twice.

One filter per `modelPanel` costs nothing and carries the panel's name, which is
how the drop knows which camera it landed on. What it does not survive is a
model panel created *after* `install()` ran; Maya makes its four at startup and
reuses them for every layout, so in practice that is a panel the animator
explicitly created, and `install()` is idempotent, so re-running it is the fix.

Two traps paid for on the way:

**The filter has to be kept alive.** `widget.installEventFilter(Filter())` binds
nothing on the Python side. The QObject is collected on the next sweep, Qt
quietly drops the dangling filter, and drops stop working minutes after they
started — with no error anywhere.

**The mime data dies with the event.** Paths are read out into plain strings
*before* the scene work is deferred, because `QMimeData` belongs to the drag and
the OS tears it down as soon as the handler returns; a deferred callback holding
the event reads freed memory, which is a crash and not an exception. The work is
deferred at all because a drop handler runs inside the platform's drag loop, and
building image planes there blocks it for as long as it takes to read the first
frame off a network share — with Explorer's drag cursor stuck to the pointer
while it does.

A drag animkit does not recognise is passed straight through, so dropping a
`.ma` on the viewport still means whatever Maya means by it.

---

## The held-hotkey radial

```python
import animkit.ui.radial as r
r.install_hotkey("c", "pose", alt=True)   # opt in, once
# hold Alt+C, flick toward a wedge, let go
```

Three menus: `pose`, `keys`, and `sets` — the last built from the scene, which is
the reason the radial was worth building at all. A selection set should cost a
flick, not a trip to a panel.

### Maya's own press/release, not a `QObject` event filter

`cmds.hotkey` takes a `releaseName` alongside `name`, so Maya runs one command on
key-down and another on key-up. That is the entire mechanism, and it beats an
event filter on both of the things that actually go wrong:

- **Focus.** A filter only sees keys the focused widget did not consume, so the
  menu works over the viewport and stops working the moment the animator clicks
  the Graph Editor, the Channel Box, or a text field.
- **Viewport recreation.** Maya destroys and rebuilds viewport widgets on layout
  changes, full-screen toggles, and some driver events. A filter installed on a
  widget that no longer exists has silently stopped working, and the symptom is
  "it worked this morning".

There is nothing to reinstall and nothing to keep alive, because the binding
lives in Maya's hotkey set rather than in this process.

### Three properties that are not optional

**It never takes focus.** `WA_ShowWithoutActivating` plus
`WindowDoesNotAcceptFocus`. If the overlay took focus, the key *release* would go
to the overlay instead of to Maya, the release command would never run, and the
menu would stay up forever. Because it never takes focus it also never gets
reliable mouse events, so the highlight is driven by polling `QCursor.pos()` on a
30 ms timer. That is the price of not fighting for focus, not a workaround.

**It always goes away.** A release can be lost — alt-tab mid-flick, a modal
dialog, a keyboard-layout switch. The overlay sits on top of the viewport, so
"stuck visible" is a Maya you have to restart. After `TIMEOUT_MS` it cancels
itself — *cancels*, never fires. A command nobody asked for, because a key event
went missing, would be much worse than a menu that closed.

**The centre is cancel.** A radial fires on release, so "I changed my mind" has
to be expressible, and not moving is the only gesture available. The pose radial
carries Reset; this is not a cosmetic detail.

### The part that can actually be wrong

"Which wedge is the cursor over" is the only part of a radial that can be *wrong*
rather than merely ugly, and it is impossible to test through a frameless
always-on-top overlay driven by a held key. So it does not live there:

| Module | What it is | Tier |
|---|---|---|
| [radial_geom.py](animkit/ui/radial_geom.py) | `wedge_at`, `wedge_span`, layout | fast, no Maya, no Qt |
| `radial.invoke_item` | what happens on release | Maya tier |
| the widget | painting and timers | not tested |

The round-trip property is the one that matters: point at a label, get *that*
item. `test_a_wedge_centre_selects_its_own_wedge` asserts it for every item count
from 1 to 12 — anything else means the drawing and the picking disagree, which
reads as a radial that fires the wrong command about a sixth of the time.

### It binds nothing by itself

`install_hotkey` is opt-in, and it **refuses to overwrite a key that already
carries something else** unless `force=True`. Maya's shipped hotkey set is
read-only, so the first call copies it to a set called `animkit` and says so —
every key the animator already had is still bound.

---

## The panel

```python
import animkit.ui.panel as panel; panel.show()
```

One dockable control, five tabs: **Tween**, **Keys**, **Pose**, **Sets** and
**Ref**. The three separate panels it replaced still work — `tween_ui.show()`, `keys_ui.show()`,
`sets_ui.show()` — because somebody may have one docked into a saved workspace,
and having it vanish on upgrade is a worse first impression than anything the
new panel fixes. This is the front door, not a demolition.

### Icon first, label in the tooltip

A text button has to be as wide as its longest label, so a row of them is as
wide as `Paste Opp.` × 4 whether or not that is useful. An icon button is
square — eight per row instead of four, which is the difference between the
pose group being one band and being three. The tooltip carries the label, the
description *and* the `runTimeCommand` name, so nothing is lost; the label just
stops being the thing that sets the layout.

Groups that read better as words keep them. `-1` and `+1` are already shorter
than any icon could be.

### The icons are drawn, not shipped

37 of them, as `QPainter` paths on a notional 100×100 grid. Three reasons, in
order of how much they matter:

1. **A studio install is a `.mod` file and a folder.** A `resources/` directory
   of PNGs means the install can now be half-done — code copied, icons missed —
   and the symptom is a panel of blank squares that still works, which nobody
   reports as a bug.
2. **DPI.** A 16px PNG is a blurry 16px PNG on a 4K monitor. A path is sharp at
   whatever size it is asked for.
3. **Theme.** The same path draws in the group's accent, dimmed when disabled,
   and white on hover, from one definition. With bitmaps that is three files per
   icon.

They render into a **`QImage`**, not a `QPixmap` — `QPixmap` needs a
`QGuiApplication` and `QImage` does not. That one choice is what makes the icon
set testable under `mayapy` with no UI at all, and it is what lets
`icons.contact_sheet()` render all 27 side by side:

```python
from animkit.ui import icons
icons.contact_sheet(size=64).save("icons.png")
```

**Look at that sheet before adding an icon.** It is the only thing that finds a
collision, and it found four on the first pass: `Cycle`, `Reset` and `Refresh`
were three circular arrows — one picture, three meanings — and the four tangent
icons were four versions of the same diagonal smudge. The cycle family is now
waveforms, `Reset` is a key dropping onto a baseline, and the tangent icons are
distinguished by the **angle of the handle on the middle key**, which is what
actually separates them in the Graph Editor.

`test_the_pairs_that_actually_collided` pins those specific pairs, because a
generic "all icons differ" test passes happily on two shapes that differ by four
pixels.

### Everything scales

Every metric goes through `style.px()`, which multiplies by the screen's device
pixel ratio, read *at call time* rather than cached — an animator drags a
floating panel from a laptop to a 4K monitor mid-session, and a ratio captured
at import is wrong from that moment on.

---

## Hotkeys

No hotkeys are bound by default — binding directly stomps whatever the
animator already had on that key. Everything is registered as a
`runTimeCommand` under **Custom Scripts → animkit** in the Hotkey Editor, so
they bind what they want:

| Command | Suggested |
|---|---|
| `animkitTweenShow` | — |
| `animkitTweenPrev30` / `animkitTweenNext30` | `Ctrl+,` / `Ctrl+.` |
| `animkitTweenPrev60` / `animkitTweenNext60` | `Ctrl+Shift+,` / `Ctrl+Shift+.` |
| `animkitTweenAverage` | — |
| `animkitKeysShow` | — |
| `animkitKeysOffsetBack` / `animkitKeysOffsetForward` | `Alt+,` / `Alt+.` |
| `animkitKeysHold` | — |
| the other eleven `animkitKeys*` | — |
| `animkitPoseCopy` / `animkitPosePaste` | `Alt+C` / `Alt+V` |
| `animkitPosePasteMirrored` | `Alt+Shift+V` |
| `animkitPoseMirror` / `animkitPoseFlip` | — |
| `animkitPoseCaptureRest` | — |
| `animkitSetsShow` | — |
| `animkitSetStore` | `Alt+S` |
| `animkitSetRecall1`…`6` | `Alt+1`…`Alt+6` |
| `animkitRefShow` | — |
| `animkitRefSlipBack` / `animkitRefSlipForward` | `Alt+[` / `Alt+]` |
| `animkitRefToggle` | `Alt+R` |
| `animkitRefPin` | — |
| `animkitRefLoad` / `animkitRefSync` / `animkitRefFadeDown` / `animkitRefFadeUp` / `animkitRefFrame` / `animkitRefRemove` | — |
| `animkitRefDropInstall` / `animkitRefDropUninstall` | — |
| `animkitRadialPose` / `animkitRadialKeys` / `animkitRadialSets` | see below |
| `animkitRadialRelease` | **never on its own** |

72 commands in total. The keyframe, pose, selection-set and reference ones are
generated from `keys.OPERATIONS`, `pose.OPERATIONS`, `sets.OPERATIONS` and
`reference.OPERATIONS`, so they cannot drift out of sync with the panels, and
each button's tooltip names its `runTimeCommand` so an animator can find it in
the Hotkey Editor without guessing.

The two reference slips are the ones worth binding. Lining a video up against
animation is a nudge repeated until it matches, and reaching for a button
between each one is what makes people stop doing it.

The three radial commands are the only ones that need a *held* binding, and the
Hotkey Editor cannot express that. Bind them from Python instead:

```python
import animkit.ui.radial as r
r.install_hotkey("c", "pose", alt=True)
r.install_hotkey("x", "keys", alt=True)
r.install_hotkey("z", "sets", alt=True)
```

`animkitRadialRelease` is the *release* half of those bindings. Bound on its own
it does nothing at all, which is deliberate — see the radial section.

---

## Tests

```powershell
.\scripts\run_tests.ps1              # fast tier, plain CPython, ~0.05s
.\scripts\run_tests.ps1 -Maya        # full tier, boots maya.standalone
.\scripts\run_tests.ps1 -Maya -MayaVersion 2026
```

Run the full tier from a **PowerShell terminal**, not Maya's Script Editor —
`mayapy` is a separate headless interpreter. Windows blocks unsigned scripts by
default, hence the `-ExecutionPolicy Bypass -File` form:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_tests.ps1 -Maya -MayaVersion 2024
```

The fast tier (128 tests) covers the blend maths, the settings layer, the
radial geometry, the dropped-media parsing and the video conversion, and runs
anywhere — none of the five reaches for `maya.cmds` outside a `try`, so all are
testable in plain CPython. The conversion tests run the **bundled ffmpeg for
real**, generating their own `.mp4`, `.mov` and `.avi` and converting them,
which is why that tier now takes seconds rather than milliseconds. It is worth
it: an argument list that looks right and produces a sequence at the wrong rate
is exactly the bug nothing else would catch. The Maya tier adds layer resolution, unit conversion, undo correctness,
drag non-compounding, selected-key mode, target resolution, the attribute-survey
equivalence, cache hygiene, every keyframe operation, the matrix conventions, the
mirror invariants, selection-set storage, reference loading and its frame
arithmetic, and the radial's menu resolution and release path.

**Status: 659 passed on Maya 2024 / Python 3.10.8**, in 90s. pytest is not
bundled with mayapy; the script installs it `--user` on first run, because Maya
lives under Program Files and a plain install needs an elevated shell.

What the headless tier deliberately cannot reach is a widget — `maya.standalone`
leaves only a QGuiApplication. Creating a real QApplication *before* initialising
standalone does work and would make the panels testable, and it was tried and
backed out: two tests in `test_radial_maya.py` exist precisely because this
interpreter has no QApplication, and a QApplication would make both pass for the
wrong reason. `tests/conftest.py` records that. The panels and the viewport drop
are verified by `selftest.run()` inside a real Maya instead.

### The parametrised harness

[tests/test_keys_maya.py](tests/test_keys_maya.py) runs one set of invariants
over every entry in `keys.OPERATIONS`:

```python
@pytest.mark.parametrize("operation", ALL_OPERATIONS, ids=ALL_IDS)
class TestEveryOperation:
    def test_is_one_undo_step(...)                     # full curve state, one Ctrl+Z
    def test_is_one_undo_step_in_selected_key_mode(...)
    def test_redo_reapplies(...)
    def test_returns_a_count(...)
    def test_does_nothing_with_nothing_selected(...)   # and leaves no undo entry
    def test_respects_active_layer(...)                # base layer must not move
    def test_never_touches_locked_layer(...)
    def test_survives_a_single_key_curve(...)
    def test_survives_a_static_channel(...)
```

Written once, it covers each new operation for free. It found both of the bugs
in the Keyframe operations section above on its first run.

The generic harness deliberately asserts **invariants**, not effects. On any one
fixture some operations are legitimately identity — setting auto tangents on
keys that are already auto, clearing a cycle on a curve that was not cycling —
so requiring "it must change something" would only force the fixture to lie.
Whether each operation actually does its job is asserted per operation, where
the fixture can be built for it.

**Assert on curve state, never on `getAttr`.** `cmds.keyframe(q=True,
valueChange=True)` reads the curve; `getAttr` reads the result of DG evaluation,
which is stale after any topology change until the plug is dirtied. A `getAttr`
assertion tests Maya's evaluation timing instead of your correctness.

---

## Maya behaviour worth knowing before you extend this

All of these were found by running the thing, not by reading docs, and every one
are easy to rediscover the hard way.

**1. `animLayer -selected` must be queried per layer.**
The global form `cmds.animLayer(q=True, selected=True)` raises *"No valid query
flags were specified"* — but only once the scene actually has layers, so it
survives every test on an unlayered scene and then breaks everything. Same
per-layer form as `-lock` and `-mute`.

**2. `workspaceControl -uiScript` runs as Python, not MEL.**
The flag is documented as taking a MEL command, so the obvious move is to wrap
your Python in `python("...")`. Do that and you get
`NameError: name 'python' is not defined` — a control created through
`maya.cmds` executes its uiScript as Python. Pass the Python directly.

The failure is quiet in the worst way: the control is created, the uiScript
dies, and you get a panel with a correct title bar and nothing inside it. Worse,
every later `show()` finds the control existing and politely restores the empty
shell. Hence the `animkitBuilt` Qt property — `show()` rebuilds any control a
build function never marked as populated.

**3. Saved workspaceControl state survives `deleteUI`.**
Maya persists a workspaceControl's definition — uiScript included — in the
current workspace, and reuses it when a control of the same name is created
again. So a control born with a broken uiScript comes back broken after
`deleteUI`, no matter what you pass to the new call.

Symptom, and it is genuinely disorienting: a fix that provably changed the
uiScript string still produces the original error, and deleting the panel does
not help. Clear the state with `cmds.workspaceControlState(name, remove=True)`
after `deleteUI` — that ordering matters, because deleting the control writes
its state back out. `mayawin.delete_workspace_control()` does both.

**4. Animation layers are ADDITIVE, so a layer curve holds an offset.**
`cmds.setKeyframe(plug, value=3, animLayer=L)` does not put `3.0` on L's curve.
It puts whatever offset makes the *attribute* read 3.0 — that is, `3.0` minus
the base layer's value at that time.

This is easy to get backwards when writing to a layer, and the symptom is a
pose that is wrong by exactly the base layer's contribution. It is also the
sharpest available test of `resolve_curve`: if it ever returns `3.0` for that
key, it has resolved to the flattened result rather than the layer's own curve,
and the layer policy is broken.

**5. Plug strings use LONG attribute names.**
`cmds.listAttr` returns `translateX`; the Channel Box reports `tx`. Both are
valid plug strings, so nothing breaks visibly — but the same channel then
produces two different plug strings depending on how it was selected, which
breaks the first thing that dedupes or compares them. `selection.py` normalises
Channel Box names to long form at the source.

**6. Topology changes leave DG reads stale until the plug is dirtied.**
Inserting or removing a key — through the API *or* through `cmds.setKeyframe` —
changes the curve while `cmds.getAttr` keeps serving the previous value.
Verified with a probe using no animkit code: `setKeyframe` wrote `key=75.0`
while `getAttr` reported `50.00`.

Interactively this is invisible; Maya evaluates on idle and the viewport is
correct. It bites scripts, batch jobs and tests, which read between refreshes.
Hence `TweenSession._dirty()` — do not delete it as redundant, and do not
assert on `getAttr` in a test when you mean to assert on curve state (use
`cmds.keyframe(q=True, valueChange=True)`).

The API's `addKey`/`remove` are additionally worse: they leave a *later*
`cmds.setKeyframe` on the same curve reading stale too. That is why key
creation goes through `cmds.setKeyframe(insert=True)`, which as a bonus
preserves surrounding curve shape instead of flattening to auto tangents.

**7. `cmds.setInfinity` on an animCurve NODE is silently ignored.**
Given a plug it works. Given the animCurve itself it returns cleanly, changes
nothing, and a follow-up query on the curve node returns `None` rather than a
value. An operation built on it reports success on every call while having no
effect whatsoever — and since the *query* also fails, a test that reads back
through the curve node cannot tell either.

Set `preInfinity` / `postInfinity` with `setAttr` on the resolved curve instead:
undoable, and unambiguous about which layer it landed on.

And mind the enum: `Constant=0, Linear=1, Cycle=3, Cycle with offset=4,
Oscillate=5`. **There is no 2.** `setAttr` on value 2 is silently rejected and
leaves the previous value in place, which is the same failure again from a
different direction.

**8. Moving a key onto an occupied frame does not merge — it nudges.**
`cmds.keyframe -e -timeChange` asked to move a key onto a frame that already has
one puts it at `1.0000001700680272` instead. The Graph Editor renders that as
"1.0", so the curve looks right and plays back wrong. `option="insert"` and
`option="segmentOver"` both refuse the move outright and leave the key where it
was, which is no better, just louder. (`option="scale"` is documented but raises
*Not a recognized option*.)

Clear the destination frame first, then move. And re-resolve indices *after* the
deletions: removing a key renumbers every key after it, so an index sampled
beforehand now points somewhere else.

**9. Qt widgets cannot be constructed under `mayapy`, and the obvious guard
against it does not work.**
`maya.standalone.initialize()` succeeds, `QApplication()` succeeds, and then a
bare `QtWidgets.QWidget()` terminates the interpreter — no traceback, no
exception, just exit code 127. Worth knowing before you spend an afternoon
trying to unit-test a panel headlessly. Import-level checks (the module imports,
the `uiScript` string compiles, the registry maps to buttons) are the most a
headless suite can do; layout has to be eyeballed in Maya.

The guard you would write for it is wrong. `maya.standalone` leaves a
**`QGuiApplication`** in place, so:

```python
>>> QtWidgets.QApplication.instance()
<PySide2.QtGui.QGuiApplication(0x1b8203dbdd0)>      # truthy!
>>> isinstance(_, QtWidgets.QApplication)
False
```

`if QApplication.instance() is None: return` therefore sails straight through
into the constructor that cannot work. Ask whether it is a `QApplication`, not
whether it exists — `radial._can_build_widgets`, and
`test_a_gui_application_is_not_a_widget_application` asserts the trap is still
there, so the guard cannot quietly become untested.

There *is* a way to get widgets under `mayapy`, and it is worth knowing about
mainly so you can decide against it: create the `QApplication` **before**
`maya.standalone.initialize()` and `QWidget()` works from then on. The order is
unforgiving — creating one *after* initialize does not raise, it takes the
interpreter down with a fatal error and writes a crash-recovery `.ma` into the
temp directory.

It was tried in `conftest.py` and backed out, because the two tests quoted above
exist precisely *because* this interpreter has only a `QGuiApplication`. A
`QApplication` in the fixture makes both of them pass for the wrong reason, and
that trap cost a day. Widget coverage was not worth disarming the thing that
guards it.

**10. Row-vector matrices, and where `offsetParentMatrix` sits.**
Maya composes as `worldMatrix = matrix · offsetParentMatrix · parentWorldMatrix`,
row-vector, applying left to right. Reversing that order is the easiest way to
build something that is subtly wrong everywhere and obviously wrong nowhere.

Beware the degenerate test: two pure translations commute, so an
`offsetParentMatrix` holding only a translation cannot distinguish
`L · OPM · P` from `OPM · L · P`. Put a rotation in it or the test proves
nothing — the first version of that check passed both orderings.

The saving grace is `MTransformationMatrix`: it round-trips a node's `.matrix`
exactly, including `rotateAxis` and a joint's `jointOrient`, so a local matrix
can be built from channel values without hand-composing the eleven-matrix
product. Going the other way still needs `[RA][R][JO]` unwound by hand:
`R = RA⁻¹ · total · JO⁻¹`.

**11. A decomposed rotation is not the rotation the animator typed.**
An orientation has infinitely many Euler representations, and Maya's
decomposition returns whichever it likes — usually the one inside ±180. That is
numerically correct and animation-hostile: writing `-180` where the channel held
`+180` is the same pose and the opposite interpolation, so both keys look right
and the limb spins between them. `xform.nearest_euler` picks the representation
closest to what is already on the channel, trying the alternate solution and
whole turns on each axis.

This is also why the analytic-vs-Maya decomposition test compares **matrices**,
not channel values: comparing numbers fails on a difference that does not exist.

**12. `MQtUtil.findControl(name)` matches on objectName and returns the first
hit — which may not be the control.**
Every widget has an objectName, and a tool that sets one for stylesheet
targeting has just created a second candidate. `style.apply_to` used to set
`"animkitPanel"`, which is also `panel.CONTROL_NAME`, so `findControl` returned
the styled widget *inside* the workspaceControl rather than the control itself.

Everything that identifies a control by name then answered about the wrong
widget. `mayawin.is_built()` read the `animkitBuilt` marker off the inner widget,
never found it, and reported a correctly built panel as empty — so every
`show()` logged "its uiScript did not populate it" and then deleted and rebuilt
a panel that was fine, and `show(tab=...)` did `findChildren(AnimkitPanel)` on
what already *was* the `AnimkitPanel`, got nothing, and silently never switched
tab.

Neither module is wrong when read on its own, which is why this survived. The
styled root is `style.ROOT_OBJECT_NAME` now, and
`test_style_object_name_cannot_collide_with_a_control_name` asserts it equals no
`CONTROL_NAME`.

**13. Image planes, five things at once.**
All probed rather than read, because the documentation is thin:

- `setAttr imagePlane.useFrameExtension 1` wires `frameExtension` to time **by
  itself**, through a `timeToUnitConversion` node. Do not also write the
  expression Maya's own UI writes — you get *"Attribute already controlled by
  an expression, keyframe, or other connection"*.
- The image drawn is `frameExtension + frameOffset`, exposed as
  `outputFrameExtension`. So `frameOffset` is the whole of reference timing.
- `coverageX` / `coverageY` are `-1` until Maya has read pixels, and the image's
  real dimensions afterwards. That is a genuine "did this file load" answer, and
  the only one available — `imageName` reads back whatever you set regardless.
- `type` is `Image File : Texture : Movie`, so a movie is `type 2` and not a
  separate node.
- The transform is parented under the camera *shape*, in Maya's under-world
  form: `|persp|perspShape->|imagePlane1`, and `ls -type imagePlane` reports
  `perspShape->imagePlaneShape1`. Do not parse it. The camera is a `.message`
  connection into `cameraShape.imagePlane[]`.

And one that is not about image planes but bit here: **`cmds.imagePlane` selects
what it creates**, with no flag to stop it. Anything that creates one inside a
larger operation has to put the selection back.

---

## Reset, and the facial control board

The bug worth recording, because the reasoning that caused it looked correct.

Reset's rule was: *with a selection, reset animated **and** unkeyed channels* —
on the argument that "a control the animator picked out and can see is not a
twist joint". True, and insufficient. It collapsed a facial control board on a
production rig.

A face board's controls are drawn, clickable, unmistakably controls — and they
sit at **non-zero translate values by design**, because that is their slot on
the board. None is keyed until the animator touches it. So *"unkeyed and off its
default"* did not describe a pose there; it described the rig. Reset zeroed all
of them and the whole board stacked onto one point.

### It was the press with *nothing selected*

From `xform`'s own module docstring: *nothing in a scene separates "rig build
offset" from "the animator moved it and did not key it"* on an unkeyed channel.
Exactly two things can resolve that, and both are a person:

- **a captured rest pose** — the animator said where rest *is*, per control
- **an explicit selection** — the animator picked *these* controls and pressed
  Reset

The bug was treating a third thing as equivalent: **`rig_controls()`**. Pressing
Reset with nothing selected falls back to that sweep — every control-shaped node
on the rig, which on a face board is all of them — and zeroed their unkeyed
channels. `rig_controls()` answers *"is this a control"*. It does not answer
*"where is this control's rest"*, and only the second question was being asked.

Measured on the rig this was found on: nothing selected reached **48 controls**
with unkeyed off-default channels; the animator's own ~200-control selection
reached **exactly one** — the FK shoulder they had just rotated, which is
precisely the control they wanted reset.

### What it does now

| Situation | Reset behaviour |
|---|---|
| animated channel | reset — its rest **is** its default, by doctrine |
| unkeyed, rest **captured** | reset to the captured rest |
| unkeyed, controls **selected** | reset — the animator asked for these |
| unkeyed, **nothing selected** | **left alone and reported** |

Two gates that used to be one. `include_unkeyed` decides whether unkeyed
channels are *considered* — so a scene-wide press still reports them. Whether
one may be *zeroed* is a separate, stricter question. Conflating them is how
"is this a control" came to authorise "and I know where its rest is".

Plus a real bug on its own: **Reset never consulted a captured rest pose at
all.** The one button that exists to say "rest is *here*" had no effect on the
operation that most needs to know. `xform.rest_channels()` is new.

### And Set Rest did not survive a reopen

Which made the advice hollow. `xform._rest_overrides` was a module-level dict,
so a captured rest lasted exactly as long as the Python session — the animator
would do the thing the warning told them to, it would work, and it would be gone
tomorrow. Worse, nothing cleared it on file-open, so a rest captured for `ctrl`
in one shot was still keyed on that name when a different shot with a different
`ctrl` was opened.

[animkit/core/rest_store.py](animkit/core/rest_store.py) persists it in the
scene: one local `network` node per rig, holding message connections to the
controls alongside their rest matrices at matching indices. Controls are
identified by **connection, never by name** — same reason as selection sets.
Adding the attribute to each control instead would be a *reference edit* on
every referenced rig, which pipelines strip and rig updates break; a local node
connected to referenced controls writes nothing into the reference.

One wiring detail worth recording, because the obvious version is wrong: the
clear-on-scene-change invalidator and the load-on-scene-open handler **both fire
on `kAfterOpen`**, in registration order, and nothing declares that order.
Observed on Maya 2024: it loaded correctly in one scene and came back empty in
another, from identical code, because the clear landed after the load. `load()`
clears before it reads, so it is the only handler needed and the ordering
question disappears.

The warning names both removals, because "it did not work" is not actionable:

```
animkit: left 18 unkeyed channel(s) on 9 control(s) alone -- they sit off their
defaults with no keys, so animkit cannot tell a pose from where the rig was
BUILT. On a facial control board, for example, every control sits off zero by
design and zeroing them collapses the board. To reset them anyway: key them
first (an animated channel's rest is its default), or put the rig on its neutral
pose and press Set Rest so animkit knows where rest is. e.g. face_ctrl_0_0, ...
```

`reset_to_default(include_unkeyed=True)` reaches the scene-wide case in one
call, for an animator who knows their rig zeroes. That is a decision somebody can
make; it is not one Reset may make for them.

**The residual risk, stated rather than hidden.** Selecting a face board's
controls and pressing Reset *will* still zero them — that is the animator saying
"put these back to their defaults", and the tool cannot know it is the wrong
thing to want here. The protection is Set Rest, which is now permanent.
`test_selecting_the_board_and_resetting_DOES_zero_it` pins the behaviour so it
stays a documented choice rather than a surprise.

### Two more bugs the same rig turned up

Asking *"does this touch non-keyable channels?"* found two, both of which hit a
face board hardest, because hiding channels is exactly how boards are rigged.

**1. The survey checked the wrong `keyable`.** There are two, with the same name
and different meanings:

| | What it is | On a hidden `rotateZ` |
|---|---|---|
| `MFnAttribute.keyable` | the **attribute's** definition — true for `translateX` on every transform ever made, and nothing a rigger does changes it | `True` |
| `MPlug.isKeyable` | **this plug's** state, which is what `setAttr -keyable false` sets | `False` |

`_survey_node` read the first. So every channel a rigger had taken out of the
animator's reach still came back writable, and `include_unkeyed=True` zeroed
channels that were not even visible in the Channel Box.

The cmds oracle in `_is_tweenable` was right the whole time — it reaches these
through `listAttr -keyable`, which reads the plug. **The spec was correct, the
fast path was wrong, and the equivalence test that exists specifically to catch
that had no non-keyable control in its fixture.** It does now.

It also had a hand-written `parametrize` list, so the case could sit in the
fixture without ever being run. That list is derived from the fixture now, and
`test_every_fixture_control_is_covered` fails if the two drift.

**2. The animation gate could not see a curve on a hidden channel.**
`_node_has_animation` is the one-query gate that rejects ~1500 plumbing nodes so
the expensive per-channel work only runs on the few dozen that matter — which
makes it load-bearing: a node it wrongly rejects has *no animated channels* as
far as the rest of the codebase is concerned, so its rest pose, its mirror and
its reset are all computed from the wrong premise.

Measured on Maya 2024, for a transform whose only key is on a non-keyable
`rotateZ`:

```
cmds.keyframe(node, q=True, keyframeCount=True)                    -> 0   # wrong
cmds.listConnections(node, s=True, d=False, type="animCurve")      -> 1   # right
```

It asks for animCurve connections now. Still one command per node, so the gate
keeps the property it exists for.

`TestFacialControlBoard` is the fixture this needed all along: nine controls in a
3×3 grid, none keyed, all off zero. Seven tests, including that the board
survives a scene-wide reset, that Set Rest makes it resettable, that an explicit
`include_unkeyed=True` still collapses it, and that one Ctrl+Z brings the whole
board back — which is what saved the rig this was found on.

---

## Known gaps

- **The panels have not been opened in interactive Maya.** Everything else here
  is verified headlessly, but constructing a `QWidget` under `mayapy`
  terminates the interpreter, so panel *layout* cannot be checked without the
  real UI. What is verified: both modules import from a cold interpreter, both
  `BUILD_CODE` strings compile, every registry entry maps to a button, and all
  22 `runTimeCommand`s register. Open both panels and confirm they look right.
- `begin()` is ~458ms on a 300-control full-body selection, against a ~100ms
  guide. See the Performance section for the measured reason and the one
  remaining lever.
- `update()` is ~27ms per mouse-move on 1800 simultaneous keys, over the 16ms
  frame budget. 90% of it is a `dgdirty` that cannot be removed. A normal
  selection (40 controls) is ~4ms.
- `retime` and `snap_to_frame` are no-ops at the current frame with no Graph
  Editor selection — the single target key is the pivot, so there is nothing to
  scale or round. Select keys for those two.
- **The mirror has only been tested against synthetic rigs.** They are
  deliberately hostile (flipped orients, `jointOrient`, mixed rotation orders,
  off-origin character) and the invariants are exact to 1e-6, but no production
  character has been through it. If a real rig fails, the failure should be a
  refusal with a reason rather than a wrong pose — that is what the symmetry
  precondition is for, and it is the first thing to check.
- The mirror pairs controls by rest position, so two controls at the *same* rest
  position (a common IK/FK setup) are ambiguous. Naming resolves it when the
  convention is recognised; otherwise the first geometric match wins.
- `pose.apply` keys every channel it was given, including ones whose value did
  not change. Harmless, but a paste onto a fully unkeyed rig creates more keys
  than strictly necessary.
- `TweenBar` relies on PySide6's unscoped-enum forgiveness mode for
  `QtCore.Qt.NoPen` and friends. True in Maya 2025/2026; if it ever breaks, add
  the constants to `vendor/qt.py`.
- The radial's *widget* — painting, the poll timer, the timeout — is not
  covered by tests. A frameless always-on-top overlay driven by a held key does
  not submit to a harness. The two parts that can be wrong rather than ugly were
  pulled out of it and are covered; what is left is drawing.
- `install_hotkey` recognises Maya's read-only hotkey set by its *name*
  (`Maya_Default`), because Maya offers no "is this set locked" query. A studio
  that ships its own locked set would need that name added.
- Fixed since the last pass: `plugs_from_selection` no longer makes ~11,400
  `cmds` calls at mouse-down (it makes none), and `TweenSession.cancel()` no
  longer leaves a no-op undo entry when the drag inserted nothing — the chunk is
  now opened lazily by `targets.resolve_targets(before_write=…)`.
- Also fixed: `posable_channels` is now `settable_transform_channels`, and the
  symmetry check is one call instead of three. Both are described below.

## Two pieces of debt cleared

Both had already cost real work, and both were naming problems rather than logic
ones — which is why neither was visible by reading the code that used them.

### `posable_channels` → `settable_transform_channels`

It answered *"which channels can Maya write"* and read as *"is this node a
control"*. Four call sites came to rely on the second reading. On a rig whose
rigger did not lock the internals those differ by **1521 nodes to 80** —
`AlignIKToWrist_L`, `BendElbow1_L`, twist chains, stabilisers and constraint
targets all pass a lock-state test, and not one of them is a control. Three of
those sites produced confusing diagnostics; the fourth reset the rig's plumbing.

The name now states what is measured — Maya's own word, `getAttr(settable=True)`
— and says nothing about the node. The control question moved to where the rig
actually gets *asked*:

```python
pose.controls_in(nodes)     # controller tags, control shapes, control sets
```

with `pairing.with_settable_channels` as the fallback for a rig that declares
nothing at all, since returning `[]` there would make Set Rest impossible on
exactly the odd rigs most likely to need it. `TestControlFilterIsNotAChannelFilter`
pins the gap between the two, so the rename's premise cannot quietly stop being
true.

### One symmetry verdict instead of three calls

*"A pair that fails `check_symmetry"* and *"controls that fail to pair at all"*
are the same condition seen at two stages. A counterpart that is not where the
reflection says it should be is rejected by `is_geometric_counterpart` **before
it ever becomes a pair** — so it never reaches `check_symmetry`, which then
reports success on a rig it cannot mirror. Three call sites had to remember to
check both, and each of them, at some point, did not.

```python
report = pairing.analyse(nodes)
if not report.ok:
    refuse(report.describe())
```

`report.broken` merges both stages into one list of `(a, b, distance)` — the same
measurement either way, via one `rest_offset` — worst offender first, deduplicated
per pair, so a truncated diagnostic shows the pair worth looking at.
`report.missing` is deliberately **not** part of `ok`: a control with no
counterpart at all is usually a lone control or an unrecognised convention, not a
broken rig, and refusing a whole selection over one would be wrong.

`test_catches_asymmetry_that_check_symmetry_alone_cannot_see` asserts that
`check_symmetry` on its own returns `[]` on that fixture *and* that `analyse` is
not fooled — so the hole cannot close by accident and leave the test passing for
the wrong reason.

---

## Next

Phases 1, 3 and 4 are in, plus reference media.

1. **Phase 5 viewport drawing** — and only after profiling says Python API 2.0
   (`MPxDrawOverride`, `MUIDrawManager`) is not fast enough. Going C++ buys the
   per-Maya-version compile matrix, the single largest cost in the original
   plan. Nothing has been profiled yet, so nothing here is decided.
2. Smaller, if it earns its place: the radial currently draws labels only. Icons
   would help the `pose` menu, where five of eight labels are verbs.
3. macOS and Linux ffmpeg binaries. The slots in
   [animkit/vendor/ffmpeg/](animkit/vendor/ffmpeg/) are there and empty, and
   the lookup already finds whatever is dropped in. The Windows build is an
   LGPL one as of this release, so the licence question is settled; what is
   left is finding builds and running `tests/test_transcode.py` against them
   on those platforms.
4. Reference, if animators ask for it: a corner placement so the video sits in
   the corner of the frame rather than filling it, and a model panel created
   *after* `install()` picking up the drop filter on its own. Neither is a gap
   in what is there — the first is a preference and the second has
   `animkitRefDropInstall` as its answer.

### Verification discipline

Three gates, three different jobs:

| Gate | When | Catches |
|---|---|---|
| `selftest.run()` | every new Maya version, after any core change | environment-specific behaviour — where **all eight** bugs so far came from |
| `run_tests.ps1 -Maya` | per commit | regressions. Every bug gets a test before it gets a fix |
| Manual on a production rig | before release | lag, and interpolation that feels wrong |

The pattern worth keeping: none of the eight bugs found so far were logic errors,
and none were visible by reading code. Two of them — `setInfinity` on a curve
node, and a moved key landing at frame `1.0000001700680272` — were `cmds` calls
that returned success and did nothing useful. That is why the self-test asserts
on observed state after every operation rather than on return values.

---

## Provenance

Clean-room. Written from Maya API documentation and observed animator
workflow. No Animbot code, assets, or branding involved, and nothing was
decompiled. Feature *concepts* and UI conventions are not protectable; the
implementation must stay ours.
