# ROADMAP.md

What is not built yet, what it would cost, and what we could build that the
tool we are being compared against does not have.

Read [AGENTS.md](AGENTS.md) first for the rules, and [README.md](README.md) for
the reasoning behind what already exists. This file is only about what comes
next.

**Scope of this document.** It is a gap analysis against the public Animbot
command index (`animbot.ca/tooltips/en/`, ~198 entries across nine colour
groups) plus a list of original features neither tool has. It is a map, not a
commitment: nothing here is scheduled, and several entries argue for *not*
building the thing.

**On naming.** Animbot is a commercial product and that index is its
documentation. Equivalent *functionality* is fair; its tooltip wording, command
names and colour taxonomy are not ours to copy. Animbot's names are used below
purely as identifiers so the mapping is checkable. Anything we ship gets our own
name and our own copy. This is the same reasoning that already made the package
`animkit` rather than `animbot` — see the stack table in AGENTS.md.

---

## Where we stand

55 operations across five registries — [keys.py](animkit/tools/keys.py) 22,
[pose.py](animkit/tools/pose.py) 10, [sets.py](animkit/tools/sets.py) 7,
[reference.py](animkit/tools/reference.py) 11,
[audio.py](animkit/tools/audio.py) 5 — surfaced as 74 `runTimeCommand`s.

Against the ~198-entry index:

| Group | Entries | Have | Partial | Missing |
|---|---|---|---|---|
| Green — keyframe manipulation | 40 | 11 | 4 | 25 |
| Yellow — blending and tweening | 12 | 3 | 2 | 7 |
| Orange — tangents | 16 | 6 | 0 | 10 |
| Misc — key tinting | 4 | 0 | 0 | 4 |
| Purple — selection and transfer | 9 | 3 | 1 | 5 |
| Pink — mirroring and alignment | 18 | 4 | 0 | 14 |
| Turquoise — advanced manipulation | 15 | 0 | 0 | 15 |
| Red — motion analysis and playback | 22 | 0 | 0 | 22 |
| Blue — extended utilities | ~55 | 0 | 1 | ~54 |
| White — system and settings | 7 | 1 | 1 | 5 |
| **Total** | **~198** | **28** | **9** | **~161** |

That count is the least useful number in this document, and the rest of it
explains why: the ~161 remaining entries are not 161 independent features. They
collapse into **eight infrastructure investments**, and one of those eight
accounts for a quarter of the entries while costing more than the other seven
combined.

---

## What is remaining, by group

### Green — keyframe manipulation (25 missing, 4 partial)

**Have.** Nudge left/right (`animkitKeysOffsetBack` / `Forward`), Bake on ones
through sevens and custom interval (generated from `BAKE_LABELS`), Reset Pose
(`reset_to_default`), Snapshot Preferred Default Pose (`capture_rest_pose` plus
[rest_store.py](animkit/core/rest_store.py)).

**Partial.**

| Entry | What is missing |
|---|---|
| Nudge All Keys | `offset` works on resolved targets; needs a wider scope than "selected keys or current time" |
| Reset Translation Rotation Scale | `reset_to_default` resets everything; needs a channel filter |
| Ease In Out | exists only as `MODE_EASE` inside the tween drag, not as a standalone recalculation |
| Simplify Bake Keys | the bake half is in; simplify / remove-redundant is not |

**Missing.**

- *Scope variants* — Nudge Time Offsetter, Nudge Scene, Insert/Remove
  Inbetween, Insert/Remove Inbetween Scene, Set Inbetween. All blocked on
  investment 1.
- *Key-time transfer* — Share Key Times, Share Key Times From Last Selected,
  Copy Key Times, Copy Color Key Times, Paste Merge / Insert / Replace / Tint
  Key Times. Blocked on investment 5.
- *Value operators* — Scale From Average / Default / Frame / Neighbor Left /
  Neighbor Right, Smooth Rough, Noise Wave, Pull Push (amp/deamp), Gap
  Stitcher, Connect to Neighbors. Blocked on investment 2.
- *Other* — Increase/Decrease Precise Transform, Bake From Last Selected, Bake
  Infinity, Time Offsetter, Time Offsetter Stagger.

Bake From Last Selected is the cheapest real win in this group: `keyed_frames`
in pose.py already answers the only hard question it asks.

### Yellow — blending and tweening (7 missing, 2 partial)

**Have.** Tweener with four modes ([blend.py](animkit/tools/blend.py)) —
`MODE_BETWEEN`, `MODE_LINEAR`, `MODE_AVERAGE`, `MODE_EASE`. `MODE_LINEAR` *is*
Blend to Neighbors; `MODE_BETWEEN` is the everyday case.

**Partial.** Blend to Default (we reset hard; the gradual version needs the
drag), Blend to Ease (`MODE_EASE`, but not as its own slider).

**Missing.** Tweener World Space, Blend to Buffer, Blend to Frame (+WS), Blend
to Neighbors WS, Blend to Infinity (+WS), Blend to Undo.

Every one of these is blocked on investment 3, and every one is small once it
lands. Blend to Buffer additionally needs buffer-curve storage; Blend to Undo
needs a pre-operation pose snapshot that survives one step, which is a store,
not an algorithm.

### Orange — tangents (10 missing)

**Have.** Auto, Spline, Linear, Flat, Stepped, Hold — the native set, which is
what Animbot's "Native Tangents" entry also is.

**Missing.** Cycle Match Tangent, Best Guess, Polished, Flow, Bounce (five
solvers — investment 6), and the ten `Blend to <tangent>` variants
(investment 3 on top of investment 6).

The five solvers are the whole cost here. Once they exist as functions over
neighbouring key relationships, the ten blend variants are the same twenty-line
target provider ten times.

### Misc — key tinting (4 missing)

Tint Key Red / Yellow / Green, Smart Tint Keys.

**This one has a trap worth recording before anyone starts.** Maya's native
`keyframe -tickDrawSpecial` gives you *one* special colour, not three. Genuine
multi-colour key tinting means drawing our own timeline overlay, which puts the
whole group behind investment 8. The cheap alternative is a single tint colour
done natively, which is a different feature honestly described rather than the
same feature done badly — and per rule 7, that is the choice we would make.

Colour-key navigation (Smart Go To Previous/Next Color Key, Play Color Keys,
Copy Color Key Times) all inherit whichever answer this gets.

### Purple — selection and animation transfer (5 missing, 1 partial)

**Have.** Select Sets ([sets.py](animkit/tools/sets.py), stored as tagged
`objectSet`s on the rig), Copy Pose, Paste Pose.

**Partial.** Snapshot Opposite Controls — we do not snapshot.
`pairing.analyse()` derives the pairing live, every time. That is a better
answer, not a missing one, and it is the single most important idea in the repo
(AGENTS.md rule 1). What is genuinely missing is *showing* the animator what was
derived — see **Rig report card** below.

**Missing.** Selection Display, Select Opposite, Copy Animation, Paste Insert
Animation, Paste Replace Animation.

Select Opposite is roughly fifteen lines on `pairing.counterpart` and should
probably be in the next thing anyone builds.

### Pink — mirroring and alignment (14 missing)

**Have.** Mirror Pose, Mirror All Keys (`mirror_range`), Flip, and the rest
capture that makes them safe on a rig that does not zero to its defaults.

**Missing.**

- *Directional* — Mirror to Right, Mirror to Left. Thin variants of what exists.
- *Alignment* — Align Objects, Align Objects All Keys.
- *Xform transfer* — Copy/Paste Xform Relationship, Copy/Paste Xform World
  Space, Selection Attribute Space Switcher, Asset Attribute Space Switcher.
- *Gradual* — Blend to Mirror, Mirror to Frame, Blend to Align, Blend to Xform
  Relationship, Blend to Xform World Space.

This group looks large and is not. [xform.py](animkit/core/xform.py) already
carries `world_matrix`, `channels_from_world`, `channels_via_proxy`,
`frame_relation` and `pose_delta` — the alignment and xform-transfer entries are
applications of code that is written and tested. The five gradual ones need
investment 3 and nothing else.

### Turquoise — advanced manipulation (15 missing)

Master Spline; Temp Controls (plus Grab/Release Space and Anim Controls); Temp
Pivot (plus To Last Object, Centered, World Space, Toggle Edit, Reset); Micro
Manipulator; Global Offset (plus Set Falloff, Pre, Post).

Nothing here is cheap. Temp Controls and Master Spline need custom nodes and
constraint plumbing; Micro Manipulator needs a manipulator context override;
Temp Pivot needs both, plus viewport feedback to be usable. Global Offset is the
one exception — it is a value operator with a falloff window, so it could ride
on investment 2 without any of the rest.

### Red — motion analysis and playback (22 missing)

Motion Trail and its five options; Suspend Viewport; Time Bookmarks and its
seven commands; Play Keys, Play Color Keys, Play on Ones/Twos/Threes/Fours.

Split this group in two before costing it. **Suspend Viewport and the six
playback entries are cheap** — they are playback-range and refresh manipulation,
no drawing involved, and they belong in the wave-1 utility sweep. **Motion
trails and time bookmarks are investment 8** and carry its full price.

Maya 2022+ ships native time bookmarks, which is worth checking before building
ours; the entry may reduce to a panel over something that already exists.

### Blue — extended utilities (~54 missing)

The largest group and the cheapest per entry. Roughly 35 of them are 10–30 line
wrappers over `cmds` with no new infrastructure at all:

Clear Animation, Crop Animation, Reverse Animation, Remove Redundant Keys,
Remove Static Anim Curves, Apply Smart Euler Filter, Copy/Cut/Paste/Delete Keys,
Paste Keys Relative, Set Smart Key, Set Smart Key All Channels, Smart Go To
Frame/Keyframe, Smart Snap Keys, Grow/Shrink Keys Selection (and their
directional variants), Select Objects From Keys, Deselect Static Objects, Select
All Anim Curves, Select Hierarchy, Select Hierarchy Animated, Select Rig
Controls, Select Animated Rig Controls, Frame Current Second, Frame Next Second,
Hide/Auto-Hide Static Anim Curves, Tumble Around Selection, Sync Isolate Select,
Toggle Geometry Visibility, Toggle Display Controllers / Particle Instancers /
Hold Outs, the two Axis Orientation entries.

`pose.rig_controls()` already answers the hardest question in that list — which
nodes are controls — so the four Select Rig Controls variants are nearly free.

The remainder need a surface, not an algorithm: Channel Box Multi Selection
Helper, Auto Clear / Clear Channel Box Selection, Channel Box Selection Timeline
Highlight, Graph Editor Toolbar, Display Top Waveform, Toggle Timeline Height,
Auto Load Timeline Sound, Timeline Boost, the seven visibility-mode entries, and
the seven "Extra Tools" sub-menus. These are Qt work against Maya's own panels.

### White — system and settings (5 missing, 1 partial)

**Have.** Sliders behaviour and prefs infrastructure
([settings.py](animkit/core/settings.py), JSON, cannot break Maya launch).

**Partial.** Bundle Snapshot — `pairing` plus `rest_store` is our equivalent,
derived rather than snapshotted.

**Missing.** Smart Euler Filter, Sliders Overshoot Mode, Sliders Pop Up, Anim
Recovery, Auto Bots, Search.

Search is worth more than its position in this list suggests. At 55 operations
the panel is browsable; at 150 it is not, and a command palette becomes the
primary way anyone finds anything. It reads the registries, so it is one widget
and no new data — see rule 5.

---

## The eight investments

| # | Investment | Unlocks | Cost | New core? |
|---|---|---|---|---|
| 1 | Target scope enum | ~8 | Small | No — one change to `targets.py` |
| 2 | Curve value operators | ~12 | Small | `tools/curveops.py`, Maya-free |
| 3 | Generalised drag session | ~25 | Medium | Refactor of `tween.py` |
| 4 | World-space pose layer | ~10 | Medium | Mostly applications of `xform.py` |
| 5 | Key-time clipboard + tinting | ~12 | Medium | Store, plus possibly investment 8 |
| 6 | Tangent solvers | ~14 | Medium | `tools/tangents.py`, Maya-free |
| 7 | Plain command wrappers | ~40 | Small × 40 | `tools/utils.py` |
| 8 | Viewport and timeline overlay | ~40 | **Large** | Phase 5 |

### 1. A target scope enum

[targets.py](animkit/core/targets.py) already resolves "the keys selected in the
Graph Editor, or the current time". Animbot's `... All Keys` and `... Scene`
variants are the same operations with the scope widened. Add
`SCOPE_KEYS / SCOPE_TIME / SCOPE_OBJECT / SCOPE_SCENE` to `resolve_targets`, and
the `kwargs` mechanism in [registry.py](animkit/tools/registry.py) turns every
existing keys operation into three more, declaratively, with the parametrised
harness covering them the moment they exist.

Do this first. It is the smallest change with the widest blast radius, and it
gets more expensive the longer it waits.

### 2. Curve value operators

Scale From *, Smooth Rough, Noise Wave, Pull Push, Ease In Out and Global Offset
are one shape:

```
resolve targets -> compute a per-curve reference value -> new = ref + (old - ref) * f(t)
```

One module plus five reference-value functions. Model it on
[blend.py](animkit/tools/blend.py): import nothing from Maya, and the behaviour
animators will actually argue about gets tested in the fast tier in
milliseconds.

### 3. Generalising `TweenSession` — the highest-leverage change here

[tween.py](animkit/tools/tween.py) already has the hard part right, and its
docstring records why each piece is the way it is: snapshot at mouse-down,
recompute from the snapshot every frame (never from the current value, or the
drag compounds), rewind-then-write on commit, one undo chunk spanning the whole
drag, key insertion through `cmds` because API topology edits leave DG reads
stale.

All of that is target-agnostic. It is only hardcoded to "blend between
neighbours" at the point where it asks what the destination value is. Factor
that into a strategy and **every** Yellow entry, all ten Orange `Blend to`
variants, and the five Pink gradual entries become small target providers on
machinery that is already correct and already tested.

Do not attempt this before investments 2 and 6 exist, because they are what the
providers will be made of. Do not attempt it without the existing tween tests
green as the regression net.

### 4. World-space pose layer

`world_matrix`, `channels_from_world`, `channels_via_proxy`, `frame_relation`
and `pose_delta` are written. What is missing is the operations on top: every
"World Space Mode" variant, Align Objects, Copy/Paste Xform Relationship,
Copy/Paste Xform World Space, and both Space Switchers.

Rule 7 applies hard here. A space switch that silently produces a pose which
reads but is wrong is exactly the failure `pairing.analyse` exists to prevent,
and the same standard should gate this.

### 5. Key-time clipboard and tinting

Two stores: a module-level key-time clipboard, and per-key colour. The clipboard
half is straightforward and unlocks six Green entries. The colour half is
blocked by Maya's single-special-colour limit — see the Misc group above.

### 6. Tangent solvers

Five analysis functions over neighbouring key relationships — Best Guess,
Polished, Flow, Bounce, Cycle Match. Maya-free, fast-tier testable, and the
gateway to fourteen entries once investment 3 lands.

These have real ambiguities. Rule 7: where the neighbouring keys do not
determine an answer, refuse and say which question could not be answered, rather
than guessing a tangent that looks deliberate.

### 7. Plain command wrappers

~35–40 Blue entries plus the cheap half of Red. Mechanical, low-risk, and the
fastest way to move the headline number. One new module, one registry, no core
changes.

### 8. Viewport and timeline overlay — the wall

Motion Trails (6), Temp Controls (4), Temp Pivot (6), Micro Manipulator, Master
Spline, Time Bookmarks (8), timeline tinting (4), Graph Editor Toolbar, and the
waveform and timeline-height entries. Roughly forty entries, and more work than
the other seven investments combined.

AGENTS.md already gates this: Phase 5 is deliberately not started, and it is
gated on profiling with `scripts/bench.py` that has not been done. **Do not
start it on the assumption that Python API 2.0 is too slow.** Going C++ buys the
per-Maya-version compile matrix, which was the largest single cost in the
original plan and is the reason one build currently serves every Maya version.

---

## Suggested sequence

| Wave | Investments | Entries | Shape |
|---|---|---|---|
| 1 | 7, plus thin variants of what exists | ~40 | Registry additions only. No core changes. |
| 2 | 1 + 2 + 6 | ~35 | Two Maya-free modules, one `targets.py` change. Fast tier. |
| 3 | 3 + 4 | ~35 | The `TweenSession` refactor. Highest leverage, highest care. |
| 4 | 5, plus the animation clipboard, Anim Recovery, Search | ~20 | Stores and persistence on `settings.py`. |
| 5 | 8 | ~40 | Phase 5. Re-decide whether it is worth it when we get here. |

Waves 1–4 reach roughly **85% of the entry count for well under half the work**,
because the expensive fraction is concentrated almost entirely in wave 5.

**The first four things to build, concretely:**

1. The scope enum in `resolve_targets` (investment 1) — everything in Green
   depends on it, and it is cheaper now than later.
2. `animkit/tools/curveops.py` (investment 2), structured like `blend.py`.
3. `animkit/tools/utils.py` (investment 7) — the wrapper sweep.
4. *Then* the `TweenSession` generalisation.

Thin variants worth folding into wave 1 because they are nearly free on code
that exists: Select Opposite, Mirror to Left / Right, Reset Translation Rotation
Scale, Bake From Last Selected, the four Select Rig Controls entries.

---

## Ideas Animbot does not have

The gap analysis above is a catch-up list, and a tool that only catches up has
no reason to exist. This section is the other direction: things worth building
because *nobody* has them, several of which are cheap specifically because of
decisions already made here.

### What we already have that Animbot does not

Worth stating, because it is the foundation the ideas below build on.

- **Reference media.** Eleven operations, drag-and-drop onto a viewport or the
  time slider, video and image sequences via bundled ffmpeg, tagged so no other
  image plane in the shot is touched. Animbot has no video reference tooling at
  all.
- **Audio with automatic transcoding.** Maya reads `wav` and `aiff` and nothing
  else; we convert. Animbot's audio entries are waveform *display* toggles.
- **No per-rig configuration.** Animbot needs Snapshot Mirror Settings, Snapshot
  Opposite Controls and Bundle Snapshot — three configuration steps per rig. We
  ask the rig instead (rule 1), so mirroring works on first contact.
- **Refusal instead of a plausible wrong answer** (rule 7).
- **Headless-testable core.** `blend`, `media`, `transcode`, `usage`,
  `catalogue` and `radial_geom` import neither Maya nor Qt.

### Proposed

**1. Key from reference.** Scrub a dropped video, mark the contacts and extremes
on it, transfer those marks to the rig as key times. Nothing connects reference
footage to key timing in any tool on the market, and we are the only ones who
already have the footage in the scene as a first-class object. *Builds on:*
`reference.py` (frame timing is already one subtraction on `frameOffset`),
investment 5's clipboard. *Cost:* small-to-medium, and probably the single
highest-value idea in this document.

**2. Audio-driven key times.** ffmpeg is already bundled and
`transcode.convert_audio` already produces a wav. Peak and onset detection over
those samples is arithmetic — no third-party dependency, no Maya, fast tier —
and it gives suggested key times for footsteps, impacts and dialogue beats.
*Builds on:* `transcode.py`, `audio.py`. *Cost:* small. Present the result as a
suggestion the animator accepts, never as keys written on their behalf.

**3. Rig report card.** `scripts/rig_probe.py` is already a read-only diagnosis
of why a mirror is not working. Give it a panel: which controls paired and how,
which did not and why, what the derived rest pose is, what is locked, what is
asymmetric. Animbot's Bundle Snapshot is opaque and produces a config file; a
transparent report is strictly better, and it turns rule 7's refusals from a
dead end into a diagnosis. *Builds on:* `pairing.analyse`, `describe_pairs`,
`describe_failures` — all written. *Cost:* small; it is mostly UI over existing
returns.

**4. Animation lint.** One panel that sweeps the scene for static curves,
redundant keys, sub-frame keys, unkeyed-but-posed controls
(`xform.unkeyed_but_posed` exists), curves outside the frame range, and
gimbal-risk rotations — each with a one-click fix. Animbot has the individual
removal commands; nobody has the overview. *Builds on:* investment 7's wrappers,
which do the fixing. *Cost:* small once wave 1 lands.

**5. Pose diff.** Select two frames, or two controls, and get the channels that
actually differ, numerically. The everyday question "why does this pose look
wrong" currently has no instrument. *Builds on:* `pose.capture`,
`xform.pose_delta`. *Cost:* small.

**6. Timing library.** Save a *rhythm* — relative key spacing with no values —
under a name, and stamp it onto any control on any rig. Animbot's key-time
copy/paste is an absolute, session-scoped clipboard. A named, on-disk, reusable
timing library treats timing as the reusable asset it actually is. *Builds on:*
investment 5, `settings.py`. *Cost:* medium. Genuinely novel.

**7. Cross-rig animation transfer.** `pairing` already solves the hard half of
this — deriving control correspondence without a naming convention. Pointed at
two different rigs instead of two sides of one, the same machinery moves
animation between them. Export to JSON, import onto anything that pairs.
*Builds on:* `pairing.py`, investment 4. *Cost:* medium-to-large, and the most
defensible thing on this list, because the component that makes it possible is
the one we already built and nobody else has.

**8. Pose and animation library on disk.** Thumbnailed, versioned, shareable
between animators. Both tools' pose copy/paste is a session clipboard that dies
with Maya. *Builds on:* `pose.capture` / `apply`, `settings.py`. *Cost:* medium.

**9. Non-uniform retime curve.** A curve you draw that remaps the whole shot's
timing, rather than uniform Faster/Slower. *Builds on:* investments 1 and 2.
*Cost:* medium.

**10. Headless batch operations.** Run bake, cleanup, lint or transfer over a
directory of scenes from `mayapy`, on a farm. Animbot is GUI-locked. Our core is
`cmds` and API only, with UI strictly separated — so this is close to free, and
it is a pipeline-TD-facing feature that would put the tool in studios rather
than only on desks. *Builds on:* the existing separation. *Cost:* small.

**11. Context-aware radial.** [radial.py](animkit/ui/radial.py) exists. Make its
contents depend on what is selected and what the animator is doing — keys
selected in the Graph Editor versus controls selected in the viewport are two
different menus. Every marking menu in this space is static. *Builds on:*
`radial.py`, `catalogue.py`. *Cost:* small.

**12. Shot notes pinned to frames.** Annotations stored in the scene at a frame,
for dailies feedback, surviving save and reopen. *Builds on:* `rest_store.py`'s
pattern for scene-resident data. *Cost:* small.

**13. Per-scene workspace state.** Remember panel, tab, reference layout and
bookmarks per scene file rather than globally. *Builds on:* `settings.py`,
`scene.py` callbacks. *Cost:* small.

Ranked by value-to-cost, the order to take them in is roughly **3, 5, 11, 1, 4,
10, 2, 6, 7**.

---

## Constraints that apply to all of it

Not style preferences. Each is in AGENTS.md because breaking it produced a real
bug.

- **No third-party runtime Python dependency.** A studio install is a `.mod`
  file and a folder. This rules out numpy for the value operators and any audio
  library for idea 2 — both are stdlib-implementable, and that is the
  requirement, not a suggestion.
- **Ask the rig, never match a naming convention** (rule 1).
- **Never assert on `getAttr`; assert on curve state** (rule 2). This will bite
  in every new test in waves 2 and 3.
- **One user-visible operation is exactly one undo step** (rule 3). Use
  `LazyChunk` for anything that may decide there is nothing to do.
- **Refuse rather than produce something plausibly wrong** (rule 7). Applies
  hardest to the tangent solvers, Connect to Neighbors, and the space switchers.
- **Say something when nothing happened** (rule 8).
- **The registry is the single source of truth** (rule 5) — which is what makes
  wave 1 cheap. Prefer extending a registry over adding a bespoke entry point.
- **Colours live in `style.py`; icons are drawn, not shipped** (rule 9). Around
  160 new operations is a lot of new icons; run `icons.contact_sheet()` before
  adding any, because four tangent icons that were the same smudge is how that
  habit started.
- **Import Qt only through `animkit/vendor/qt.py`** (rule 10).

Every wave has to leave the three gates green — fast tier, `-Maya` tier, and
`selftest.run()` twice with identical results. Current baseline: **149 fast, 903
under `-Maya`, 34/34 self-test.** If a change moves those down, that is the
finding; report it rather than papering over it.
