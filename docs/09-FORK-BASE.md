# Fork Base Decision

> **Status: executed 2026-09-21. Build 0 is IL-identical to the validated DLL, and it loads and
> links in the game** — see §9. §6's *behavioural* check (movement, interaction, events under AI
> control) has not yet run: the first attempt had `EnableAIControl` off.
> Every claim below is sourced to the upstream clone at `upstream/rainflayer`, the installed
> assembly, or the recordings. Where the evidence stops, this document says so.

## Recommendation

**Fork `01137e5` (the 1.1.1 source line) and build it against BepInEx 5.** Port the three
hook-emitted events from 2.0.0 by hand, in protocol v2's event shape. Do not take the counterboss
or Mithrix Herald code. Do not move to BepInEx 6.

The first milestone of the fork is not a feature. It is **reproducing the telemetry shape of the
recordings we already have**, so that 10,578 ticks of real play become the fork's regression suite
before any v2 work begins.

---

## §1 The question

Roadmap step 3 says "fork, build from source, pin versions." Phase 0 surfaced two things that make
"fork" underspecified:

- **F6** — upstream source HEAD is `2.0.0`; the Thunderstore package we run is `1.1.1`.
- **F8** — the csproj references BepInEx 6 assembly names; the running mod is a BepInEx 5 plugin.

So there are two independent choices, **which source line** and **which BepInEx major**, giving
three live options:

| | Source | BepInEx | Keeps |
|---|---|---|---|
| **A** | `01137e5` (1.1.1) | 5 | what we validated, 4 hooks |
| **B** | `3d8da8c` (2.0.0) | 6 | everything upstream did, as upstream configured it |
| **C** | `3d8da8c` (2.0.0) | 5 | upstream's latest, retargeted, counterboss stripped |

---

## §2 Evidence

### E1 — No commit is provably the artifact we validated

The installed `Rainflayer.dll` (sha256 `84910CB0…`, 146,432 B) declares plugin version **`1.1.0`**
in its `BepInPlugin` attribute. The package manifest and CHANGELOG say `1.1.1`. Mapping the plugin
version at every commit in upstream history:

| Commit | Date | Declared version |
|---|---|---|
| `974d3d5` … `be55559` | 2026-02-28 → 03-07 | `1.0.0` |
| `01137e5` | 2026-03-09 | **`1.1.1`** |
| `3d8da8c` | 2026-03-18 | `2.0.0` |

**No commit ever declared `1.1.0`**, and `1.0.1` never appears either. The Thunderstore releases
were built from states that were not committed. `01137e5` is the closest commit to what we ran —
its CHANGELOG entry ("added defend_zone command") matches, and `defend_zone` is present in the
installed assembly — but it is *closest*, not *identical*.

This is the fact that most shapes the plan. It is why the fork's first job is to reproduce the
recordings: **we cannot inherit confidence from the validated DLL by commit hash, so we have to
re-earn it by behaviour.**

### E2 — The published DLL was not built from the committed csproj

Reading the installed assembly's metadata (UTF-8 `#Strings` heap):

| Reference | Installed DLL | Every committed csproj |
|---|---|---|
| `BepInEx` (v5) | **referenced** | — |
| `BepInEx.Core` (v6) | — | referenced |
| `BepInEx.Unity.Mono` (v6) | — | referenced |
| `MMHOOK_RoR2` | referenced | referenced, conditionally |
| target framework | `.NETStandard 2.1` | `netstandard2.1` |

`git log -S BepInEx.Unity.Mono` shows the v6 references have been in the csproj **since the initial
commit**. So upstream built its releases with a local, uncommitted project file. **Neither version
builds reproducibly from the repository as committed.** Whichever line we fork, the csproj has to be
reconstructed — so this cost is identical across A and C and does not discriminate between them.

*(An earlier draft of this analysis said MMHOOK was missing from the csproj. It is present, as a
conditional `ItemGroup` at line 88, past where the file was first read. Corrected here.)*

### E3 — 2.0.0 uses no BepInEx-6-only API

The only BepInEx type 2.0.0 touches is `BaseUnityPlugin`, which exists in both majors. There is no
`BasePlugin`, no IL2CPP, no `BepInEx.Unity.*` namespace in code. **The BepInEx 6 dependency exists
only in the csproj's HintPaths.** Retargeting either version to BepInEx 5 is a project-file edit,
not a port.

### E4 — The 1.1.1 → 2.0.0 diff is almost purely additive

`git diff --stat 01137e5 3d8da8c -- mod/`: **3,352 insertions, 25 deletions.**

| File | Change | What it is |
|---|---|---|
| `AdversaryAI.cs` | +1,253 | new — counterboss |
| `CounterbossController.cs` | +659 | new — counterboss |
| `MithrixHeraldController.cs` | +587 | new — final-boss rework |
| `AdversaryItemDisplay.cs` | +285 | new — counterboss |
| `SocketBridge.cs` | +360 | counterboss handlers, `QUERY_CONFIG`, three new events |
| `RainflayerPlugin.cs` | +177 | counterboss/herald wiring, three new events |
| `AIController.cs` | 32 | **spelling: "Mythrix" → "Mithrix"** |
| `EntityDetector.cs` | 8 | **spelling** |
| `NavigationController.cs` | 4 | **spelling** |

Every change 2.0.0 makes to the three files whose behaviour our recordings exercised is a rename.
The only wire-visible consequence is three moon2-only reason codes
(`in_mythrix_arena` → `in_mithrix_arena`, etc.), which are outside MVP scope.

**Nothing 2.0.0 changes would alter what the 10,578 replayed ticks measured.** Its substance is a
large, self-contained feature we do not want.

### E5 — 2.0.0 adds three events we *do* want, and they fire without AI control

| Event | Raised from | Gated on `EnableAIControl`? |
|---|---|---|
| `run_started` | `Run_Start` hook | no |
| `stage_started` | `RainflayerPlugin`, same region | no |
| `item_picked_up` | `OnLocalPlayerInventoryChanged` | no |

The only `EnableAIControl` checks in `RainflayerPlugin.cs` are at lines 171 and 825, neither
enclosing these. This matters because of **F20**: every 1.1.1 event is raised from `AIController`
and falls silent when a human plays. 2.0.0 already moves some events into game hooks, which is the
correct direction. `stage_started` in particular is the natural source for protocol v2's
`scene_gen` (`05-PROTOCOL.md` §2).

2.0.0 also adds `QUERY_CONFIG`, which would have let the harness detect the `EnableAIControl`
misconfiguration directly instead of parsing the config file.

### E6 — 2.0.0 ships a default-on gameplay modification

| Setting | Default | Effect |
|---|---|---|
| `EnableCounterboss` | `false` | off unless enabled |
| `EnableMithrixHerald` | **`true`** | **replaces Phase 1 of the final boss** with a survivor adversary while Mithrix stands frozen and immune |

A 2.0.0 fork run with default settings plays a **different game** on Commencement. That is out of
MVP scope today, but any recording from a 2.0.0 base would carry a silent asterisk.

### E7 — Hook surface more than doubles

Hooks subscribed (`On.RoR2.* +=`): **4 in 1.1.1, 8 in 2.0.0.**

*(Corrected. This originally read "8 in 1.1.1, 19 in 2.0.0", which counted every textual
`On.RoR2.` occurrence — each hook appears once where it is subscribed and again in its handler's
`orig` parameter type. The runtime log settled it: BepInEx reports exactly four hooks applied by
`Rainflayer.dll`, matching four subscriptions. The ratio, and so the argument, is unchanged.)*

Hooks are exactly what game patches break, and a plugin that throws during initialisation does not
load at all. The risk register's top entry — "Rainflayer does not run on current game version" — was
retired on the evidence of *1.1.1's* 4 hooks. Four additional hooks, all serving features we will
not use, re-open that risk for no return.

### E8 — The ecosystem you run is BepInEx 5, end to end

Profile `Jev` holds `bbepis-BepInExPack 5.4.2122`, `RoR2BepInExPack 1.43.0`, `HookGenPatcher 1.2.9`,
`FixPluginTypesSerialization 1.0.4`, `MiscFixes 1.5.9` and `SeekersPatcher 1.4.1` — all BepInEx 5
packages. `MMHOOK_RoR2.dll`, which the mod needs for its hooks, is itself **generated by
HookGenPatcher under BepInEx 5**.

### E9 — Every build input is already on this machine

All fifteen assemblies the csproj names, mapped to their BepInEx 5 equivalents, exist locally:
BepInEx and Harmony from the profile's `BepInEx/core/`, `MMHOOK_RoR2` from `BepInEx/plugins/MMHOOK/`,
and the eleven game and Unity assemblies from `Risk of Rain 2_Data/Managed/`. **The .NET SDK is the
only missing build input.**

---

## §3 Why not B

B takes on BepInEx 6 and gains nothing for it. E3 shows 2.0.0 uses no BepInEx-6-only API; E8 shows
the entire installed ecosystem, including the generated `MMHOOK_RoR2` the mod's hooks depend on, is
BepInEx 5. Choosing 6 would mean a second r2modman profile, re-resolving every dependency against a
major version the RoR2 community does not target, and running something nothing in this project has
ever run. **All cost, no benefit.** Ruled out.

## §4 A versus C

Both use BepInEx 5; both need the csproj reconstructed (E2) — so that cost cancels. The difference
is what else each carries.

| | **A — 1.1.1** | **C — 2.0.0 stripped** |
|---|---|---|
| Distance from the validated DLL | one version string (E1) | +3,352 lines (E4) |
| Hooks subscribed (breakage surface) | **4** | 8, or fewer if stripping is thorough (E7) |
| Default-on gameplay change | none | Mithrix Herald, must be removed (E6) |
| Events fired without AI control | none (F20) | three (E5) |
| `QUERY_CONFIG` | no | yes |
| Work to reach the fork's first milestone | retarget csproj | retarget csproj **and** excise ~2,800 lines + wiring cleanly |
| Moon2 reason-code spelling | "mythrix" | "mithrix" |

C's advantages are real but **small and temporary**: three events and one query, perhaps 60 lines in
total. And they would be **rewritten anyway** — protocol v2 events carry `command_id`, `event_seq`,
`session_id` and `scene_gen` (`05-PROTOCOL.md` §12), which none of 2.0.0's events have. Taking them
from 2.0.0 saves the idea, not the code.

A's advantages are **structural and permanent**: the smallest hook surface on the project's
historically largest risk, the least distance from what was validated, and nothing to excise. Excising
2,800 lines of interleaved counterboss wiring from `SocketBridge` and `RainflayerPlugin` is itself a
source of bugs, and it would have to be done *before* the fork could be trusted to reproduce the
recordings.

**A wins, with 2.0.0 used as a reference for the three hook-based events** — read, understood, and
reimplemented in v2 shape rather than merged.

---

## §5 What this does not establish

- **Nothing has been compiled.** No SDK is installed (E9). "Should build" is the claim; "builds" is
  not.
- **`01137e5` is not provably the running DLL** (E1). It declares `1.1.1`; the DLL declares `1.1.0`.
  Some uncommitted difference may exist, of unknown size. This is the single largest uncertainty
  here, and §6's exit criterion exists to measure it rather than assume it away.
- **Upstream merges are not preserved.** The fork will diverge heavily once protocol v2 replaces
  `SocketBridge`'s query model, so neither option keeps a cheap path to upstream fixes. This does
  not discriminate between A and C, but it does mean "upstream's latest" (C's intuitive appeal) buys
  less than it sounds.

## §6 Exit criterion for the fork's first milestone

Before any v2 work, the built fork must **reproduce the recordings' telemetry shape**:

1. `eval/gonogo_probe.py` passes against the fork, including an `action_complete(success)` chest.
2. A fresh `eval/record_run.py` session, replayed through `eval/replay_stock.py`, produces the
   same field schema as `eval/inspect_recording.py` reports for `human-01` and `run-02`: same frame
   types, same fields, same types, same sentinels.
3. Registry invariants hold on every replayed tick, as they did on 10,578 ticks of stock telemetry.

This turns E1 from an unknowable into a measurement: if the fork reproduces the recordings, the
missing `1.1.0` difference did not matter to anything we depend on. If it does not, the diff between
recordings pinpoints what `01137e5` lacks.

It also means the recordings pay for themselves twice — first as the evidence behind F18–F22, then
as the fork's regression suite.

## §7 First steps

1. **Install the .NET SDK** — the only missing build input (E9).
2. Branch from `01137e5`. Rewrite the csproj's BepInEx HintPaths to BepInEx 5's `BepInEx.dll`;
   point the others at the local paths in E9. Keep `netstandard2.1`.
3. Build. Record the resulting DLL's sha256 and plugin version in `contract/pinned-build.yaml`.
4. Set the plugin version to something unambiguous (`ror-jev-0.1.0`), matching the
   `mod_version` field already in `05-PROTOCOL.md` §3's `HELLO_ACK`, so recordings from the fork can
   never be mistaken for stock.
5. Run §6. Only then begin Phase 0 step 4's C# work — and start it with **F20**: move event
   emission out of `AIController` into game hooks, using 2.0.0's `Run_Start` and
   `OnLocalPlayerInventoryChanged` as the pattern.

## §9 Result: build 0

Executed on .NET SDK 10.0.401, pinned in `mod/global.json`.

### It compiles, first time

`mod/` holds `01137e5`'s sources **unmodified**. The only edit is `Rainflayer.csproj`: BepInEx 5
instead of 6, real install paths instead of `libs\`, conditional references made mandatory, and
deterministic output. 0 errors; 237 warnings, all nullability warnings in upstream code except one
worth tracking — `GenericPickupController.pickupIndex` is obsolete in favour of `pickupState`, a
game API RoR2 is retiring and therefore a future breakage point.

### It is the validated artifact, as far as the assembly can show

`python -m eval.compare_dll --stock mod/bin/Debug/netstandard2.1/Rainflayer.dll`:

| Section | Stock (validated) | Fork build 0 | |
|---|---|---|---|
| Assembly references | 7 | 7 | identical |
| Types | 27 | 27 | identical |
| Method signatures | 315 | 315 | identical |
| External member references | 385 | 385 | identical |
| String literals — the wire protocol | 646 | 646 | identical |
| Plugin attribute | `1.1.0` | `1.1.1` | the version string, nothing else |
| **Method bodies (normalised IL)** | **317** | **317** | **0 differ** |

**§5's "single largest uncertainty" is resolved: the uncommitted difference between `01137e5` and
the DLL behind 10,578 ticks of recordings is zero at the code level.** What upstream never committed
was a version string and a BepInEx 5 project file. Both are now committed here.

The comparison normalises compiler encoding choices (short vs long branches, `ldc.i4` forms,
local-slot encodings) and compares bodies as a multiset per method name, so same-arity overloads
cannot pair up wrongly. It does **not** compare exception-handling regions, local-variable
signatures, static data blobs or type layout; a difference confined to those would pass. Given
identical strings, types, member references and bodies, that residual risk is small, and §6's
in-game run covers it.

### Upstream shipped a Debug build

A Release build of the fork differed in **203 of 317** methods. Every difference was a `nop`: the
published DLL is unoptimised, almost certainly produced by a bare `dotnet build`, whose default
configuration is Debug. Built in Debug, the fork matches exactly — **same 146,432-byte size** — so
build 0 ships in Debug. Moving to Release is a legitimate later change, but it is a *change*, and
it gets its own validation run rather than riding along with anything else.

### It is reproducible, which upstream's builds were not

A clean rebuild (`rm -rf bin obj`) produces a byte-identical DLL:

| Configuration | sha256 |
|---|---|
| Debug (deployed) | `C9E558B8671FA35CC353E08F433CD8DA8ECB47ED0EFC2605E32DE9BE4ABA7FC4` |
| Release | `0BA2D809D50EFACFCC7C41CFAB0802FA19005DCA3BC1B69CB1FBD801732416C6` |

Deterministic compilation plus a mapped source path means the hash depends on the inputs, not on
the machine. That is the concrete fix for E2: the thing that made upstream's releases
un-rebuildable was never the code, it was the absence of any record of how they were built.

### Deployed

`python tools/deploy.py` swapped the fork into profile `Jev`, keeping upstream's GUID so it
*replaces* stock rather than loading beside it. The stock DLL is backed up to `mod/stock/` and
verified against the recorded stock hash; `python tools/deploy.py --restore` puts it back. Every
deploy and restore is appended to `mod/deploys.log` with its sha256, so any future recording can be
matched to the exact binary that produced it — the gap that made E1 necessary.

### In game: it loads and links, and its telemetry has the same shape as stock

Runs `fork-gonogo-01` and `fork-01`, 2026-09-21.

| Check | Result | Evidence |
|---|---|---|
| The fork, not stock, is what loaded | **yes** | BepInEx logs `Loading [Rainflayer 1.1.1]`; stock declares `1.1.0` |
| Every hook binds against the current `MMHOOK_RoR2` | **4 of 4** | `OnHook … added by assembly: Rainflayer.dll` for `PlayerCharacterMasterController.Update`, `Stage.Start`, `Run.Start`, `HealthComponent.TakeDamage` |
| Socket connects; every query answers | **yes** | 165 full query sweeps in `fork-01` |
| Query schema matches stock | **identical** | 0 fields stock never produced, 0 JSON-type changes, same `999.0` sentinel |
| Behaviour under AI control | **partly exercised** | `fork-gonogo-02`, `fork-02`: combat, damage, `low_health` and `action_*` events all fire — and those are raised only from `AIController`, so the AI is live on the fork. No clean GO yet: the movement proxy had nothing measurable, and `fork-02`'s probe phase was confounded (report F27) |

The last row is why §6 is not closed. With AI control off, the mod accepts every command and
ignores it, and no event fires (F20) — so the recordings contain no `action_*`, `combat_*` or
`damage_taken` frames and nothing moved under the fork's control. Nothing in them says the fork
is broken; nothing in them says its AI works either. `fork-01`'s `HARNESS_START` recorded
`ai_control: false` at the time, which is how this was caught rather than misread.

Two pre-existing exceptions surfaced in the log. Both are in upstream code and, since build 0 is
IL-identical to stock, stock throws them too:

- `AIController.Start()` — calls `combatController.DetectCharacterClass()` before
  `combatController` is assigned. **Benign:** detection has a lazy retry and two other call sites,
  so it recovers on a later frame. Noise, not a defect in behaviour.
- `BaseAI.Awake()` via `InitializePlayerAI` → `AddComponent<PlayerAI>()` — the game's own `Awake`
  expects state a player master does not have. The mod drives input directly rather than through
  `BaseAI`, which is presumably why every recorded run has worked regardless.

Neither is fixed in build 0, deliberately: build 0's value is being identical to what was
validated. They are the first candidates once a change is allowed.

### Tooling hardened by that miss

`EnableAIControl` has now silently spoiled two runs in opposite directions — `human-01` session 0
had it on when a human meant to play, and the fork runs had it off when the AI was meant to. Both
failures looked like something else. So:

- `eval/profile_config.py` — one shared reader of the config, used by the probe and the recorder.
- `eval/gonogo_probe.py` warns before it listens, records the setting in the recording, and when
  every failure is a behaviour check **and** AI control is off, reports **BLOCKED BY
  CONFIGURATION** (exit 2) instead of NO-GO. A query failure is still NO-GO, because configuration
  cannot explain it.

### New tooling

- `tools/dllmeta.cs` — metadata and normalised-IL dump of an assembly, runnable with
  `dotnet run tools/dllmeta.cs`.
- `eval/compare_dll.py` — the comparison above, as one command. Its second use matters more than
  its first: **after every change to the fork, the list of changed methods should be exactly the
  methods you meant to touch.** Anything else is a change nobody intended.
- `tools/deploy.py` — deploy, restore, status.

---

## §10 Build 1: the first deliberate change — the roam edge loop

**Build 1 ends build 0's IL-identity with stock, on purpose.** It changes exactly one method.

### What was wrong

`fork-04` was stopped by hand: under `MODE roam` the character walked off the same edge five or more
times in one area. Reading `CombatController.ApplyIdleBehavior` found three compounding defects,
all upstream, all present in stock:

1. **It detects the cliff and keeps walking.** A 3 m look-ahead raycast finds the edge, and the
   branch that handles it clears the waypoint — but sets no movement. `RainflayerPlugin`'s input
   override saves and restores `inputBank.moveVector` every frame, so last frame's "walk toward the
   edge" stays in force.
2. **It picks destinations across gaps.** A candidate waypoint 25–45 m away is accepted if a
   downward ray finds ground *at the destination*. Nothing checks the ground *between* here and
   there.
3. **It keeps choosing the unreachable spot.** Waypoints are scored by distance from previously
   visited positions, so the most attractive target is the one furthest from anywhere it has been.
   A spot beyond an edge can never be visited, so it stays the most attractive target forever: fall,
   respawn nearby, choose it again. That is the observed loop.

It surfaced now because the harness driver was changed to roam for the first five minutes of each
stage; before that the driver mostly issued `FIND_AND_INTERACT`, which pathfinds on the NodeGraph and
never enters this method.

### The change

In the edge branch: set `moveVector` to zero, and add the abandoned waypoint to `visitedPositions` so
it becomes the *least* attractive choice. Defect 2 is left alone — with 1 fixed the character stops at
the edge instead of crossing it, and with 3 fixed it stops choosing it.

### Verification

`python -m eval.compare_dll --stock mod/bin/Debug/netstandard2.1/Rainflayer.dll` against the
verified stock backup:

| | |
|---|---|
| Metadata | identical except the plugin version string (`1.1.0` → `1.1.1`, as build 0) |
| Method bodies changed | **1 — `CombatController::ApplyIdleBehavior`** |
| Methods added or removed | 0 |

| Build | sha256 (Debug) | Contents |
|---|---|---|
| 0 | `C9E558B8…` | IL-identical to stock |
| 1 | `CE424B48…` | build 0 + roam edge fix |

**Not yet verified in game.** The change is small and its logic is direct, but "stops at the edge"
is a claim about physics and movement that only a run can confirm.

### Known gaps

- **Combat movement is untouched.** Strafing and kiting in `CombatController` (the `moveVector`
  assignments around lines 392–452) steer without the look-ahead ground check that roam has. A
  character fighting near an edge may still step off it. Unverified; worth watching for.
- `compare_dll` also had a naming bug this change exposed: `--stock` read whatever was installed in
  the profile, which after build 0's deployment was no longer stock. It now reads the verified
  backup in `mod/stock/`, and `--installed` compares against what is deployed.

---

## §8 What would change this recommendation

- **If `01137e5` fails §6 badly** — the fork does not reproduce the recordings and the gap is large —
  then the missing `1.1.0` state matters, and C (with its own §6 run) becomes worth comparing.
- **If a future RoR2 patch breaks 1.1.1 and upstream fixes it only on the 2.0.0 line**, that fix is
  the thing to port, and the hook-surface argument weakens.
- **If Commencement enters scope**, C's correctly spelled moon2 reason codes and the knowledge in
  `MithrixHeraldController` become relevant — though the Herald itself would still be removed.
