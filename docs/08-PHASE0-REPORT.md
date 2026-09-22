# Phase 0 Report

**Run 2026-09-20.** What was executed, what it measured, what it found, and what is still
blocked. Phase 0's own instruction is *no reassuring defaults*, so this document does not round
a partial execution up to a completed phase.

**Headline: the gate passed. Rainflayer 1.1.1 runs against the current game build.** The risk
register's top entry — "Rainflayer does not run on current game version", likelihood High, impact
High — **did not materialise.** Run `gonogo-01` on `golemplains2`: the mod connected, all six
queries answered, and the character moved **57.9 m in 15 s** under `MODE:roam`.

**Interaction is proven too.** `gonogo-03`: `action_started` on a Basic Chest 71 m away,
navigation across that distance while taking damage, then
`action_complete{"status":"success"}` — the chest was opened. Step 2 is complete.

It took three runs, and the first two failed for benign reasons that were themselves informative:
`gonogo-01` had no chest in the detection cone (`not_found`, see F12) and `gonogo-02` found one it
could not afford (`no_gold`, 19 gold against a 30-cost chest, see F14).

Running the gate also produced ten findings (§3e) that neither `01-RESEARCH.md` nor upstream's own
`PROTOCOL.md` documents. Three had already broken my probe, and one — **F18** — re-shapes what is
left of the project: measured against real stock telemetry, **not one question in the milestone-1
catalog can be sent**, because seven of the sixteen state fields it declares have no stock source.

---

## §1 Step-by-step disposition

| Step | Deliverable | State | Evidence |
|---|---|---|---|
| 1 | Install RoR2, r2modman, BepInEx, Rainflayer | **done** | r2modman 3.2.19, profile `Jev`, Rainflayer 1.1.1 on BepInEx 5.4.2122 (§3d) |
| 2 | Run stock; socket connects and character moves — **go/no-go** | **PASS, complete** | `gonogo-01/02/03`, §3e. Chest opened end to end in `gonogo-03` |
| 3 | Fork, build from source, pin versions | **done; AI behaviour unverified** | `mod/` — IL-identical to the validated DLL, reproducible, loads with 4/4 hooks and stock's query schema; behaviour under AI control not yet exercised (`09-FORK-BASE.md` §9) |
| 4 | Minimum telemetry | **contract only** | `brain/catalog.py`, `eval/fixtures.py` |
| 5 | Raw recorder | **done** | `brain/recorder.py`, 14 traces |
| 6 | Measure serialized catalog size | **done (bytes)** | `contract/catalog-size.json` |
| 7 | Play 3-5 runs, recording throughout | **3 of 3-5 done** | Bandit, Huntress, Engineer — 10,578 replayed ticks, stage 4 reached (§3f) |

Test evidence: `python -m eval.run_suite` → **208 passed, 0 failed** on the Python target (148 of
them added by this phase, including a replay of real recorded frames through the registry). The C# and in-game targets still report NOT IMPLEMENTED, which is
correct and unchanged.

### What "contract only" means for step 4

The field contract is implemented and tested — the banded state shape, the `enabled_when`
grammar, capability gating, and the omit-never-null rule all execute and are covered by traces.
**No C# was written.** The mod-side snapshot pass, the time-sliced reachability schedule, and the
hazard facts remain unimplemented, and the fixtures in `eval/fixtures.py` are hand-written to the
`05-PROTOCOL.md` §4 shape rather than captured from a running game. They are a specification made
executable, not telemetry.

This distinction matters because the phase's own reasoning for pulling telemetry forward was that
"the catalog cannot be tested against recordings of fields never captured." That reason still
stands. The catalog is now tested against *fixtures* of those fields, which is strictly weaker.

---

## §2 The measurement (step 6)

`python -m eval.measure_catalog` builds the real request from `contract/questions.yaml` across the
Stage 1 walk and writes `contract/catalog-size.json`.

**Bytes are measured. Tokens are not, and are not presented as if they were.** JEV's tokenizer is
unpublished and no offline tokenizer for it exists, so every token figure is bracketed between
4.0 and 3.0 characters per token — the optimistic end being ordinary English prose, the
conservative end allowing for JSON punctuation and keys like `use_shrine_chance:entity-51`
fragmenting badly. Ground truth is `usage.input_tokens` on a real response, which Phase 0 cannot
obtain because it holds no API key by design.

> **Owed to Phase 2:** the first paid call must record `usage.input_tokens` alongside the exact
> request bytes and check it against this bracket. If it lands outside 3.0–4.0 chars/token, every
> dollar figure below is wrong and the bracket, not the cost model, is what needs fixing.

| Tick shape | Questions | Catalog | State | Request | Tokens (est) |
|---|---|---|---|---|---|
| Floor — teleporter undiscovered, nothing visible | 4 | 2,460 B | 958 B | 3,462 B | 866–1,154 |
| Typical — chest in range, teleporter known | 4 | 2,665 B | 1,147 B | 3,856 B | 964–1,285 |
| Irreversible — shrine in range, worth gate added | 5 | 3,207 B | 1,253 B | 4,504 B | 1,126–1,501 |
| Ceiling — 12 entity actions, 4 gated | 9 | 6,415 B | 2,147 B | 8,606 B | 2,152–2,869 |

### M1 — The cost model overstated a typical tick by 1.6–2x

The assumed ~2,000 input tokens per call (state ~700 + catalog ~1,300) measures as **964–1,285**
on a typical tick. A 40-minute run at 2 Hz is **$0.19–0.26**, not $0.40. The README's
$0.40–0.70 per run now describes the *ceiling*, not the expected case.

Budget admission control should still be sized against the ceiling, because a loot-dense stage
genuinely looks like the ceiling.

### M2 — The registry, not the fixed catalog, drives cost

Between floor and ceiling the fixed questions barely move; `next_action`'s generated options and
the per-action worth Nouls carry the entire 2.5x spread. `next_action` alone ranges 604–2,819
characters. **Capping the registry is a cost control, not only a prompt-quality one** — which
makes finding F1 below a budget defect as well as a correctness one.

### M3 — `engagement_response` costs twice its estimate

225–300 tokens against the ~150 in `03-QUESTION-SET.md`; its three options carry the longest
prose in the catalog. Not worth trimming at these prices — the options were deliberately
lengthened in r3 to be explicit — but the budget table is now measured rather than guessed.

### M4 — The milestone-1 question count was wrong

`03-QUESTION-SET.md` said 5-8 questions per milestone-1 tick. It is **4-5 plus 0-4 worth gates**.
The r3 table counted `use_equipment_now` and `situation_is_novel`, both of which are gated off in
that configuration — by `equipment` being a Phase 4 capability and by `--jev-only` respectively.
The token budget it quoted was nonetheless about right, for offsetting reasons.

---

## §3 Findings

Thirty, found by trying to execute the plan rather than by reading it — five in our own
contract (§3), four in the upstream teardown (§3b), thirteen from running the game (§3e, §3f, §3g),
and eight from running and building the fork (`09-FORK-BASE.md` §9–§10, §3h and §3i below). Not all are
defects: F14 and F16 are load-bearing good news, and three were bugs in my own probe that only real
telemetry could have exposed.

**If you read one finding, read F18.** It is the measurement that re-shapes the project: seven of
the sixteen state fields the catalog declares have no source in stock telemetry, and they are
enough to omit every question in the milestone-1 catalog. The brain is close to done; the mod is
barely started.

**F1 and F2 both blocked the first milestone and are now fixed.** F3 needs review of text I wrote.
F6, F7 and F8 were settled by installing the stack and reading what r2modman resolved — see §3d.

| | Finding | State |
|---|---|---|
| F1 | Registry cap of 12 unenforced | **fixed** |
| F2 | `defend_zone` / `fight_boss` unreachable | **fixed** |
| F3 | Catalog not machine-readable; 5 verbs had no option text | **fixed, text needs review** |
| F4 | Coarse `required_state_fields` couple questions | `next_action` narrowed (F30); `strategy`, `situation_is_novel` still coarse |
| F5 | C7 cannot run (`jevkit` absent) | open, install away |
| F6 | Source is 2.0.0; Thunderstore ships 1.1.1 | **resolved** — runtime 1.1.1, clone 2.0.0 |
| F7 | R2API declared but never used in code | **refined** — install it, it is a manifest dep |
| F8 | BepInEx 5 vs 6 | **resolved** — 1.1.1 is BepInEx 5; 2.0.0 source is 6 |
| F9 | Stock has one `shrine` type; registry expects two | open, telemetry work |
| F10 | `action_failed` is a separate event type with `reason` | **fixed in probe**, informs v2 |
| F11 | Undocumented combat events; `enemy_count` typed as string | open, state builder |
| F12 | Interactables are FOV-filtered and sparse — not a candidate set | **open, affects F1's ranking** |
| F13 | `INVENTORY` includes hidden `DrizzlePlayerHelper` | open, state builder |
| F14 | `no_gold` already carries target, cost and live gold | **good news** — `cost_limit` is plumbing |
| F15 | One outcome, three field names (`result`/`reason`/`status`) | **fixed in probe**, informs v2 |
| F16 | `action_started` + `damage_taken` exist and are useful | **good news** — less C# to write |
| F17 | 999.0 enemy sentinel; interactables have no stable identity | **fixed in probe**; argues for entity IDs |
| F18 | **Every milestone-1 question is omitted against stock telemetry** | **open — this is the remaining work** |
| F19 | Only `chest` maps to a verb; the teleporter does not | **fixed** — classification from real frames |
| F20 | All events come from `AIController`; human play records none | open — the fork should move them to hooks |
| F21 | Bare `"Shrine"` is the mod's fallback, not the rule | **corrected** — Chance/Blood are offerable; fallback fails closed |
| F22 | **Torn read measured**: the two queries disagreed on 1 of 10,578 ticks | open — only the v2 snapshot fixes it |
| F23 | Two upstream NullReferenceExceptions at init (`AIController.Start`, `BaseAI.Awake`) | open — benign in practice, noisy; first fix candidates |
| F24 | **The mod already computes survivor identity** and never sends it | good news — `run.survivor` is plumbing, like HP |
| F25 | **A terminal event is not guaranteed**: a teleporter was activated and no `action_complete` came | open — deadlines in v2 are mandatory, not defensive |
| F26 | `low_health` is level-triggered: ~60 events/s for as long as HP stays low | open — must be edge-triggered in v2 |
| F27 | `OBJECTIVE` answers `exploring` in the character-select lobby | fixed in the harness — `INVENTORY`'s marker decides |
| F28 | **Roam walks off edges in a loop**: detects the cliff, keeps walking, re-picks the same spot | **fixed in fork build 1** — verified in game, fork-06 (`09` §10) |
| F29 | **The mod blocks repeat damage to the player — in every run, human or AI** | **decided 2026-09-21: config `BlockRepeatDamage`, default off (vanilla)** — fork build 3, deployed. Recordings before build 3 are on the easier game |
| F30 | `next_action` needs the *whole* `threat` block, including path feasibility | **decided 2026-09-21: narrowed** to `enemies_near`, `nearest_enemy_band`, `boss_active`; unknowns stripped from the wire state |
| F31 | **`run_suite` reported a crashing test file as PASS** (0 passed, 0 failed) | **fixed** — a crash or a non-zero exit is now a failure |
| F32 | `next_action` is sent on ticks where the character is dead (`hp_band: dead`, fork-06: 3 ticks) | **fixed** — `build_request` asks nothing when `self.hp_band` is `dead` |
| F33 | **Combat movement walks and rolls off cliffs**: backing away and the utility dash have no edge check | **fixed in fork build 5** — not yet verified in game |
| F34 | **Combat target choice is fixed in the mod, not JEV's**: boss +50 wins, then sticks | open — needs a target-priority channel and readable item effects |

### F1 — Registry invariant 9 was not enforced. **FIXED** *(blocked milestone 1; also a cost defect)*

`06-ACTION-REGISTRY.md` invariant 9: "Capped at **12 entity actions** plus sentinels. Ranking is
deterministic and recorded." `brain/registry.py` implements **no cap and no ranking**. Every
reachable interactable becomes an option:

| Interactables | Options offered | Request | Tokens (est) | $/hr @ 2 Hz |
|---|---|---|---|---|
| 12 | 12 | 6,484 B | 1,621–2,161 | $0.49–0.65 |
| 30 | 30 | 11,272 B | 2,818–3,757 | $0.85–1.14 |
| 60 | 60 | 19,252 B | 4,813–6,417 | $1.46–1.94 |

The 60 row is the case `02-ARCHITECTURE.md` §3 rule 3 explicitly forbids — "never hand JEV 60
interactables and ask it to sort them" — and it is currently reachable. It is also the case
`01-RESEARCH.md` warns degrades accuracy through irrelevant state, so the failure is not only
financial.

**Fixed** in `brain/registry.py`: `MAX_ENTITY_ACTIONS = 12`, asserted as an invariant, with a
deterministic ranking — in-range first, then nearest, then entity ID for a stable tie-break — and
a `dropped` list recording every action the cap cut, with its rank and distance. A cap that keeps
no record of what it cut is indistinguishable from telemetry that never saw the target, which is
precisely what Phase 5 needs to tell apart. 7 traces in `eval/test_phase0.py`.

### F2 — `defend_zone` and `fight_boss` were unreachable, so the teleporter event could not be run. **FIXED** *(blocked milestone 1)*

Both verbs exist in `VERBS` with capabilities and commands, but nothing maps to them:
`build_registry` derives verbs only from `TYPE_TO_VERB` over `obs["interactables"]`, and
`TYPE_TO_VERB` contains no entry producing either. Enemies are never iterated, so no
`fight_boss:<enemy>` record can be constructed at all.

Constructed the `charging` row of `06-ACTION-REGISTRY.md` §6 directly — teleporter charging, boss
active, in range — and the registry contains **exactly one option: `explore`.** The agent would
be told to wander off during the boss fight.

Milestone 1 is "clear Stage 1, leave through the teleporter." It cannot pass. This is also why
`defend_zone` was pulled forward into Phase 2 capabilities — the capability manifest was updated,
the registry was not.

**Fixed** in `brain/registry.py`, in two parts:

- The verb is now resolved **before** range gating, and gating is applied per verb from the
  taxonomy rather than to the whole table. The old code read `TYPE_TO_VERB` only when in range, so
  every verb that is *not* range-gated was silently unreachable. Added
  `teleporter_charging -> defend_zone`.
- `fight_boss` records are built from `threat.enemies` filtered by `BOSS_CLASSES`, since an enemy
  never appears in the interactable list. They stay capability-gated out until `fight_target`
  lands in Phase 4 — correct, and it means `06` §6's `charging` row is a Phase 4 picture. For
  milestone 1 the charging phase now offers `defend_zone:entity-03`, `explore`, `none`, which is
  enough to complete charging.

10 traces in `eval/test_phase0.py`.

### F3 — The catalog was not machine-readable, and the registry could offer options the catalog could not describe

The `instructions` and `criteria` text existed only as prose in `docs/03-QUESTION-SET.md`, so the
serialized catalog could not be built — step 6 was not executable as written. Moved into
`contract/questions.yaml` under `payload:`.

Transcribing it surfaced the real problem: **five of the ten verbs in the registry taxonomy had
no option text** (`navigate`, `use_shrine_blood`, `advance_stage`, `defend_zone`, `fight_boss`)
and **three of the four gated verbs had no worth question** (`use_shrine_blood`,
`start_teleporter`, `advance_stage`). `navigate` and `advance_stage` are reachable on any run, so
this was live: JEV would have been offered a blank option, or an irreversible action with no
authorizable gate.

Text is authored in `contract/questions.yaml`, marked `AUTHORED-IN-PHASE-0`, and **needs review**
— it was written to make measurement possible, not by the catalog-authoring judgment that
produced the rest. `brain/catalog.py` now raises rather than rendering a blank option or a gated
action with no gate, so the class of defect cannot recur silently.

### F4 — Coarse `required_state_fields` couple unrelated questions

`next_action` and `strategy` declare the whole `threat` block. One unreadable sub-field therefore
omits both — so a single unmeasurable feasibility flag suspends task selection *and* posture for
that tick, not just `engagement_response`, which is the only question that reads it.

This is the field contract working exactly as written ("a question whose instructions reference an
`unknown` field is omitted entirely"), and both questions' instructions do reference `threat` as a
whole, so the behaviour is fail-safe and correct. But the blast radius is larger than the prose
suggests and it is a live in-game risk: if any threat sub-field turns out to be flaky, the agent
goes inert rather than degrading. Asserted explicitly in `eval/test_phase0.py` so it is a known
property rather than a surprise. Recommend measuring per-field read reliability during step 7 and
narrowing the declarations if any field proves unreliable.

### F5 — C7 cannot run

`07-TEST-PLAN.md` C7 requires `jevkit` lint over the serialized catalog. `jevkit` is a Rust CLI
and is not installed, so C7 is unsatisfied. The serialized catalog it needs now exists, so this
is an install away rather than blocked on design.

---

## §3b Upstream teardown findings

Rainflayer is now cloned to `upstream/rainflayer` (MIT, HEAD `3d8da8c`). Reading it against
`01-RESEARCH.md` §5 found four more discrepancies, all of which change what "install the
prerequisites" means.

### F6 — Upstream is 2.0.0, not the 1.1.1 the roadmap targets

`[BepInPlugin("justindwang.rainflayer", "Rainflayer", "2.0.0")]`. HEAD adds a counterboss feature
plus `AdversaryAI.cs`, `AICameraController.cs`, `MithrixHeraldController.cs`,
`AdversaryItemDisplay.cs` and `QueryHandlers.cs` — five files the documented module map does not
mention. There are no git tags, so 1.1.1 is not checkoutable by tag.

**The fork base is now a decision, not a default.** The teardown in `01` describes neither version
exactly. 2.0.0 is what the source gives you; 1.1.1 is what the Thunderstore package (and the
roadmap) names.

### F7 — R2API is declared but never used in code *(refined; see §3d)*

`grep -rn R2API` across upstream's `.cs`, `.csproj` and `.md` returns **nothing**, and
`git log --all -S R2API` over the entire history returns nothing either — it has never been
referenced in code, in any commit.

**But it is a declared package dependency.** Rainflayer's Thunderstore manifest lists
`RiskofThunder-R2API_Core-5.3.0`, so r2modman installs it. The manifest is maintained outside the
git repository, which is how the two facts diverged.

The correct instruction is therefore **let r2modman install it** — it is harmless, and overriding
a package manager's resolved graph buys nothing. What the finding actually rules out is *needing
R2API to build a fork*: the fork's csproj will not reference it.

### F8 — BepInEx 5 vs 6 is a version divergence, not a contradiction *(resolved; see §3d)*

`docs/SETUP.md` says BepInEx 5.x. `mod/Rainflayer.csproj` references `libs/BepInEx.Core.dll` and
`libs/BepInEx.Unity.Mono.dll` — **BepInEx 6** assembly names; 5.4.x ships a single `BepInEx.dll`
and has no `BepInEx.Unity.Mono`. So the published build is presumably 5.x while the source in the
repo is written against 6.

`git log -S BepInEx.Unity.Mono` shows those references have been in the csproj **since the initial
commit**, so this is not drift. The resolution (§3d): the *published 1.1.1 package* is a BepInEx
**5** mod, and the csproj describes the **2.0.0** source line. Two version lines, two frameworks.

**Consequence for the fork:** forking 2.0.0 means BepInEx 6 and a *separate r2modman profile*,
because the profile now installed is BepInEx 5. Forking 1.1.1 keeps the current profile but means
forking a version the git history cannot check out by tag.

### F9 — `shrine` is one interactable type upstream; our registry expects two *(open)*

Stock exposes types `chest, shrine, teleporter, shop, drone, printer, portal, misc`. Our
`TYPE_TO_VERB` keys on `shrine_chance` and `shrine_blood`, **neither of which stock produces** —
the shrine kind is only in the human-readable `name` ("Shrine of Combat"). The same applies to
`teleporter_idle` / `teleporter_charging` / `teleporter_charged`: stock has one `teleporter` type
with `charged` / `chargePercent` / `bossActive` fields.

So the Phase 0 telemetry work owes a **type classification layer** — shrine subtype from the body
or name, teleporter state from its fields — and our registry's type vocabulary is a *target*
shape, not something stock will hand us. Worth noting the fixtures in `eval/fixtures.py` use the
target vocabulary, so they are consistent with the contract and not yet with reality.

Two smaller corrections, recorded so the teardown stops being wrong:

- `01` §5 says "query the same **five** state endpoints." There are **six**:
  `QUERY_OBJECTIVE`, `QUERY_INVENTORY`, `QUERY_COMBAT_STATUS`, `QUERY_INTERACTABLES`,
  `QUERY_ALLIES`, `QUERY_PILLARS`. Requests carry a `QUERY_` prefix the teardown omits.
- Stock's Python side **deduplicates interactables to the closest of each type** before the brain
  ever sees them, so the stock agent cannot choose between two chests at all. This confirms that
  per-entity addressing is real new work rather than exposure of something already present.
- Stock errors are bare `{"error": "..."}` with **no `type` field**, which protocol v2's typed
  framing assumes.

Confirmed correct in `01` §5, for the record: port 7777, NDJSON, mod-dials-Python with 2 s retry,
main-thread dispatch queue, the command vocabulary, and `MODE:defend_zone` already existing — the
last of which is what `capabilities.yaml` bet on when it pulled `defend_zone` into Phase 2.

---

## §3c The go/no-go probe (step 2 instrument)

`eval/gonogo_probe.py` runs the gate without an API key or an LLM. Python is the server; the mod
dials in. It checks, in the order a failure would matter: the mod connects → queries answer and
not `Not in game` → all six queries return → **the character moves** → `FIND_AND_INTERACT`
produces an `action_complete` event. Everything received goes through `brain/recorder.py`, so the
gate run also yields the first real recording and the actual field shapes step 4 must target.

Movement has no direct telemetry — stock exposes no player position — so it is inferred from
interactable and enemy distances changing over a sampling window. That proxy reads "did not move"
when the character is boxed in with nothing nearby, so the probe reports **inconclusive** in that
case rather than a clean failure.

Self-tested against a mock of `SocketBridge.cs` across four paths: happy (GO), plugin absent
(NO-GO), connects-but-stuck (NO-GO), and not-in-game-then-spawns (GO). Exit code 0 / 1 / 2 for
GO / NO-GO / inconclusive.

---

## §3d What installing the stack settled

r2modman 3.2.19, profile `Jev`. Installing Rainflayer and reading what the resolver pulled
answered three findings with evidence rather than inference. Full record in
`contract/pinned-build.yaml`.

| Package | Version |
|---|---|
| `Rainflayer-Rainflayer` | **1.1.1** |
| `bbepis-BepInExPack` | **5.4.2122** (BepInEx **5**) |
| `RiskofThunder-R2API_Core` | 5.3.0 |
| `RiskofThunder-HookGenPatcher` | 1.2.9 |
| `RiskofThunder-RoR2BepInExPack` | 1.43.0 |
| `RiskofThunder-BepInEx_GUI` | 3.0.3 |
| `RiskofThunder-FixPluginTypesSerialization` | 1.0.4 |
| `pseudopulse-SeekersPatcher` | 1.4.1 |
| `score-MiscFixes` | 1.5.9 |

**F6 → resolved as a deliberate split.** Thunderstore ships **1.1.1**, which is what the roadmap
named and what the gate will test. The source clone stays at **2.0.0** for reading. Runtime and
source are different versions on purpose, and `pinned-build.yaml` records both.

**F7 → refined, and my advice was wrong.** I told you not to install R2API. The underlying grep was
right — R2API appears in no commit, ever — but Rainflayer's Thunderstore manifest *declares*
`R2API_Core-5.3.0`, and that manifest lives outside the git repo. Let r2modman install it.

**F8 → resolved.** Declared dependency is `bbepis-BepInExPack-5.4.2121`, resolved to 5.4.2122. The
published mod is BepInEx 5. The csproj's BepInEx 6 references belong to the 2.0.0 line and have
been there since the first commit.

### The probe is compatible with 1.1.1

`eval/gonogo_probe.py` was written against **2.0.0's** `docs/PROTOCOL.md`, so 1.1.1 could have
lacked queries it sends and produced false NO-GOs. String-extracting the installed assembly
(`Rainflayer.dll`, 146,432 B, sha256 `84910CB0…`) shows otherwise — all six `QUERY_*` handlers,
all five commands, `defend_zone`, and every `STRATEGY` argument are present. The 1.1.1 CHANGELOG
confirms `defend_zone` landed in that release, which is the bet `capabilities.yaml` made when it
pulled `defend_zone` into Phase 2.

One packaging trap: the release zip carries macOS AppleDouble junk — `._Rainflayer.dll`,
`._manifest.json` and friends. The `._Rainflayer.dll` stub is **163 bytes and not an assembly**;
it caught my first extraction attempt. Any tooling that globs `*.dll` in the plugin directory must
exclude `._*`.

---

## §3e What the gate run found (`recordings/gonogo-01`)

Scene `golemplains2`, Commando, Drizzle, solo, money 15. The decisive result is in the headline;
these are the protocol facts the run exposed. All four are **undocumented in both** `01-RESEARCH.md`
§5 and upstream's `docs/PROTOCOL.md` — which is the argument for Phase 0 recording raw from the
first minute.

### F10 — `action_failed` is its own event type, carrying `reason`, not `result`

```jsonc
{"type":"EVENT","event_type":"action_failed",
 "command":"FIND_AND_INTERACT:chest","reason":"not_found"}
```

Upstream's PROTOCOL.md documents **only** `action_complete` with a `result` field enumerating
`failed (stuck)`, `failed (no gold)` and so on. The installed 1.1.1 instead emits a *separate
event type* and names the field `reason`.

**This broke the probe**, in two compounding ways: it waited on `action_complete`, which never
arrives on a failure path, and it snapshotted `len(events)` *before* the wait loop, so the
`action_failed` that had already arrived was skipped. Both fixed; the fix is verified by replaying
the recorded frames rather than the mock.

The lesson transfers directly to protocol v2: **any terminal-event matcher must key on a set of
terminal types, never a single expected one.** T19's "events after a terminal state are discarded"
presumes you can recognise the terminal state at all.

### F11 — Undocumented combat events, with string-typed numbers

```jsonc
{"type":"EVENT","event_type":"combat_cleared"}
{"type":"EVENT","event_type":"combat_entered","enemy_count":"1"}
```

Note `enemy_count` is the **string** `"1"`, while `COMBAT_STATUS.enemy_count` is the **integer**
`2`. The same quantity is typed differently depending on which frame carries it, so the state
builder must coerce rather than trust — and a band computed from `"1"` by naive comparison would
be silently wrong.

### F12 — The interactable list is sparse and FOV-filtered, so it is not a candidate set

The only interactable returned was the teleporter, at **269.3 m** — well beyond the 100 m radius
`01-RESEARCH.md` §5 attributes to `EntityDetector`, so that figure is wrong for 1.1.1 or the
teleporter is special-cased. **No chests were listed at all**, which is what produced `not_found`.

Combined with the documented 180° FOV filter, this means the stock list is *what the character
happens to be looking at*, not what is nearby. Our registry treats `interactables` as a
pre-filtered, pre-ranked candidate set (`02-ARCHITECTURE.md` §3 rule 3), and the invariant-9
ranking added for F1 sorts by distance — **both assume a full-radius enumeration that the mod does
not currently provide.** Phase 0's telemetry work owes full-radius enumeration with the FOV filter
removed; ranking a cone is ranking an artefact of camera angle.

**Filters confirmed from source (`fork-05`, 2026-09-21).** The two interactable searches differ:
`QUERY_INTERACTABLES` uses a **180° cone plus line of sight** within 100 m (`SocketBridge.cs`
~line 592), so the cone claim above holds for the list. `FIND_AND_INTERACT` searches **360° with
line of sight** (`AIController.cs` line 573); the cone applies only to enemies. The line-of-sight
test is lenient by design — several ray heights, and any one passing counts.

In `fork-05`, 7 of 11 chest searches returned `not_found` with 150–170 gold, each coinciding with an
empty list. **The cause is not established.** A first reading blamed line of sight, and the player
watching the run contradicted it directly: two chests *side by side*, one skipped; and a chest later
targeted *through a wall from below*. Neither fits a line-of-sight explanation. Stock sends no
positions, so the recording cannot settle it — which is itself the finding: chest selection is
unobservable until the fork reports entity IDs and positions.

### F13 — `INVENTORY` includes hidden internal items

```jsonc
"items":[{"name":"DrizzlePlayerHelper","count":1}]
```

That is the hidden Drizzle difficulty assist, not a pickup. Passed through to JEV it reads as a
real item and would corrupt `build.identity` and every economy judgment. The state builder needs
an explicit exclusion list for internal/hidden items, and the safe default is to exclude anything
not on a known-good list rather than to filter a blocklist.

### F14 — `action_failed(no_gold)` already carries the numbers `cost_limit` enforcement needs

`gonogo-02`, standing at a chest with 19 gold:

```jsonc
{"type":"EVENT","event_type":"action_failed","command":"FIND_AND_INTERACT:chest",
 "reason":"no_gold","target":"Basic Chest","cost":"30","current_gold":"19"}
```

This is the most encouraging finding of the two runs. `05-PROTOCOL.md` §5.4 insists that
"affordability is not a spending authorization" and that `cost_limit` must be **enforced at the
moment of effect** with a hard numeric ceiling. The mod already resolves a *specific named target*,
knows its *live price*, and knows *current gold* at refusal time — so the three inputs
`cost_limit` enforcement requires already exist in 1.1.1's interaction path. Wiring
`cost_limit_exceeded` (T32) is plumbing an existing comparison into a new rejection, not building a
pricing system.

Two caveats. **`cost` and `current_gold` are strings** (`"30"`, `"19"`), same as F11's
`enemy_count` — coerce, never trust. And the event names the target by **display name**
(`"Basic Chest"`), not by an entity ID, which is exactly the gap protocol v2's typed targets exist
to close: two Basic Chests are indistinguishable in this event.

The probe now credits this separately — "mod resolves a specific target and prices it" passes on a
`no_gold` refusal — because the detection-and-costing path working is a real result, distinct from
the interaction effect that a refusal by definition did not perform.

### F15 — One outcome, three field names, in one mod version

The same concept is spelled three ways, and nothing documents any of it:

| Event | Field | Example |
|---|---|---|
| (2.0.0's `PROTOCOL.md`) | `result` | `"failed (stuck)"` |
| `action_failed` (1.1.1) | `reason` | `"no_gold"`, `"not_found"` |
| `action_complete` (1.1.1) | `status` | `"success"` |

**This scored a successful chest opening as a failure.** The probe read `result` then `reason`,
found neither on `action_complete`, and fell through to its failure branch — reporting NO-GO on the
run that actually proved interaction works. Fixed by reading all three.

Twice now the same class of bug: F10 was assuming one terminal event *type*, F15 was assuming one
outcome *field*. For protocol v2 the lesson is the same both times, and it is worth stating as a
rule rather than a fix: **the brain must never infer an outcome from the absence of an expected
key.** T10's "any omitted question yields no authorization, not a `KeyError`" is exactly this
principle applied to answers; it needs applying to events too.

### F16 — `action_started` and `damage_taken` exist, and both are useful

```jsonc
{"event_type":"action_started","command":"FIND_AND_INTERACT:chest",
 "target":"Basic Chest","distance":"71","cost":"25"}
{"event_type":"damage_taken","amount":"7","remaining":"102"}
```

Neither is documented anywhere. Both reduce what the Phase 0 C# telemetry work has to add:

- **`action_started`** is an acceptance receipt carrying the target, its distance and its cost. That
  is most of what `05-PROTOCOL.md` §11's `accepted` transition needs, and it means the
  submitted-to-accepted edge can be observed rather than inferred from a timeout.
- **`damage_taken`** carries `amount` and `remaining`, which is a health delta stream. Roadmap step
  4 asks for "health + timestamped history" for `self.hp_trend`; this supplies the deltas, and
  timestamping them brain-side is nearly free. It does not replace polled HP — an event stream can
  be missed — but it makes `hp_trend` cheap to compute between polls.

Strings again (`"71"`, `"25"`, `"7"`, `"102"`), consistent with F11 and F14.

### F17 — Two ways the stock telemetry defeats naive measurement

Both broke the probe's movement check, and both are direct arguments for protocol v2's entity IDs.

**`nearest_enemy_distance: 999.0` is a sentinel, not a distance.** With `enemy_count: 0` the mod
reports 999.0. The probe differenced it against a real 30.8 m once combat started and reported
**968.2 m of movement in 15 seconds** — for a Commando, about 64 m/s. The check passed for entirely
the wrong reason.

For the state builder this is sharper than it looks: 999.0 must map to
`nearest_enemy_band: "none"`, never through a distance banding that would call it `"far"`. A
fabricated measurement is worse than a missing one, because the field contract has no `unknown`
path for a value that arrives looking valid.

**Interactables have no stable identity.** `gonogo-03` returned three entries all named
`"Shop Terminal"`, so keying on `type:name` collapsed them to whichever came last, and the retained
value jumped between samples for no positional reason. Without entity IDs you cannot track a
specific target across two observations — precisely the defect `06-ACTION-REGISTRY.md` §1 exists to
fix, demonstrated here on real frames rather than argued from first principles.

The proxy now compares **sorted distances grouped by type**, skipping any type whose count changed
(an interactable entering the cone is not the character moving). Re-scored against all three
recordings: 57.9 m, 4.5 m, and 18.9 m across 4 comparable targets — the last replacing the bogus
968.2 m.

### F18 — Against stock telemetry, **every** milestone-1 question is omitted

This is the most consequential finding of the phase, and it is mechanical rather than
arguable. `python -m eval.inspect_recording` maps each field the catalog declares in
`required_state_fields` onto a stock source, using the recordings as evidence:

| Field | Stock source |
|---|---|
| `objective.phase` | `OBJECTIVE.objective` |
| `economy.gold_band` | `INVENTORY.money` |
| `build.identity` | `INVENTORY.items` (derivable) |
| `control.active_action`, `.consecutive_failures` | action events, brain-side |
| `self.equipment` | `INVENTORY.equipment` — **name only, 1 of the 4 contract fields** |
| `threat` | partial: `COMBAT_STATUS` + `OBJECTIVE.boss_active` |
| **`self.hp_band`** | **none** |
| **`run.survivor`** | **none** |
| **`run.time_band`** | **none** |
| **`threat.enemy_classes`** | **none** |
| **`threat.reposition_feasible` / `.full_withdrawal_feasible`** | **none** |
| **`hazards`** | **none** |
| **`control.progress_band`** | **none** |

Applying the field contract's omit-never-null rule to that table:

```
OMITTED   next_action              missing hazards, run.time_band
OMITTED   engagement_response      missing hazards, reposition_feasible, full_withdrawal_feasible
OMITTED   strategy                 missing self.hp_band
OMITTED   engage_range             missing run.survivor, threat.enemy_classes
OMITTED   continue_is_worthwhile   missing control.progress_band
gated off use_equipment_now        (equipment is a phase-4 capability)
gated off situation_is_novel       (counsel disabled under --jev-only)
```

**Nothing is sendable. The agent would be inert** — it would build a valid request containing
no questions, receive no answers, authorize nothing, and stand still. Correctly, by design:
the field contract has no partial-credit path, and that is the behaviour it promises.

Two things are worth separating here.

**Stock exposes no player health at all.** Six queries, and not one returns the player's HP.
`ALLIES` carries `health`/`maxHealth` — for *allies*, which a solo run never has. The only
player-health signal in the entire protocol is `damage_taken.remaining` (F16), an event
stream that **misses healing entirely**, so it cannot even be used as a substitute. Since
`self.hp_band` is the most-referenced field in the catalog, this single gap is load-bearing.

**This is not bad news about the design; it is the measurement the design asked for.** The
roadmap's step 4 exists precisely because "the catalog cannot be tested against recordings of
fields never captured." That reasoning is now quantified: seven of sixteen declared fields
have no stock source, and they are enough to silence the entire catalog. What changes is the
*shape* of the remaining work — the brain side is essentially complete and the mod side is
essentially unstarted, so the project's centre of gravity is C#, not Python.

It also sharpens what Phase 2 cannot do. `04-ROADMAP.md` Phase 2 lists `state_builder.py` as
"unit-tested against recordings"; recordings of stock telemetry can only test the seven fields
that exist. The other nine need the forked mod before a single unit test over them means
anything.

### F19 — Only `chest` maps; the teleporter itself is unmapped

F9 said stock has one `shrine` type where the registry expects two. It is worse: of the
interactable types observed, **only `chest` maps to a verb at all.**

`TYPE_TO_VERB` keys on `teleporter_idle`, `teleporter_charging` and `teleporter_charged`.
Stock emits a single `teleporter` type carrying `charged`, `chargePercent` and `bossActive` as
fields. So the teleporter — the one interactable milestone 1 cannot be completed without —
produces no registry record at all.

This does not invalidate the F2 fix, which corrected the range-gating *logic* and was right to.
It means the fix was written against a type vocabulary the game does not use. The classification
layer is small and unambiguous:

| Stock | Condition | Our type |
|---|---|---|
| `teleporter` | `charged: false`, `bossActive: false` | `teleporter_idle` |
| `teleporter` | `bossActive: true` or `0 < chargePercent < 100` | `teleporter_charging` |
| `teleporter` | `charged: true` | `teleporter_charged` |
| `shrine` | by display name (`Shrine of Chance` / `of Blood` / `of Combat`) | `shrine_chance` / `shrine_blood` / unmapped |
| `shop` | — | deferred (multishop is out of MVP scope) |

Writing that is cheap. Knowing it was needed required running the game.

### Smaller observations

- **`OBJECTIVE` carries `scene_name`** (`"golemplains2"`), which is undocumented and directly
  useful: protocol v2 needs scene identity for `scene_gen` and for stage naming, and this supplies
  it without new C#.
- **`QUERY_PILLARS` answers off-moon** with a structured empty result
  (`{"is_moon2":false,"total":0,"charged":0,"all_charged":false,"pillars":[]}`) rather than
  erroring, so the probe's defensive off-moon special case was unnecessary.
- **`equipment` is the string `"None"`**, not `null`, when empty — another coercion the field
  contract must handle, since `"None"` is truthy.

---

## §3f Step 7: the first real run, and the brain replayed against it

`recordings/human-01`, session 2: a **25-minute human-played run**, `EnableAIControl = false`,
three stages (`blackbeach` → `foggyswamp` → `wispgraveyard`), gold 0 → 1387, 20 distinct items,
17,857 frames. All three objective phases captured for the first time: `exploring`,
`charging_teleporter`, `teleporter_charged`.

`python -m eval.replay_stock --run-id human-01` runs those frames through the **real** adapter,
registry and request builder — no stubs on the brain side. 1,488 ticks:

| Phase | Ticks |
|---|---|
| `pre_teleporter` | 1,274 |
| `charging` | 185 |
| `charged` | 29 |

**Registry invariants held on 1,488/1,488 ticks**, and the registry offered, in real conditions:

```
navigate           1605      defend_zone         185
explore            1488      advance_stage        14
open_chest           23      start_teleporter      7
search_teleporter     5
```

Two results matter here.

**The F1/F2/F19 fixes work on real telemetry.** `defend_zone` appears on 185 charging ticks and
`advance_stage` on 14 charged ticks — the teleporter lifecycle the registry could not express at
all this morning (F2), now driven by the classification derived from real frames (F19). The
`06-ACTION-REGISTRY.md` §6 worked example is reproduced from a recording rather than asserted.

**And zero questions were sendable, on all 1,488 ticks.** F18 is no longer a table-based argument:

```
omitted  next_action:telemetry_unknown            1488
omitted  engagement_response:telemetry_unknown    1488
omitted  strategy:telemetry_unknown               1488
omitted  engage_range:telemetry_unknown           1488
```

The brain ran clean and authorized nothing, which is the field contract behaving exactly as
specified. This is the sharpest statement of where the project is: **the decision layer is
complete and correct, and it is starved.**

### The adapter is scaffolding, and says so

`brain/stock_adapter.py` converts stock frames into the v2 observation shape. It exists to get
the brain in front of real data before the fork exists, and it declares every assumption it
makes rather than smoothing them over — notably that **entity IDs are synthetic and positional**,
so nothing depending on identity across ticks (opportunity tokens, idempotency,
`continue_current_action`) can be validated through it. It should be deleted once the mod emits
v2 observations.

### F20 — Every event is emitted from `AIController`, so human play records no events

The 25-minute human run produced **zero events**. Not a harness bug: `grep` shows
`damage_taken`, `low_health`, `combat_entered` and `combat_cleared` are all raised from
`mod/AIController.cs`, which does not run when `EnableAIControl = false`.

Consequences worth stating:

- **The Phase 5 "casual human run" baseline cannot collect event-derived telemetry.** Queries
  work; the event stream is silent. Any metric defined over events is AI-only.
- Queries survive because the `AIController` *object* still exists with AI control off — only
  its update loop is gated. `QUERY_ALLIES` reaches through it to `EntityDetector` and returned
  data 1,144 times during the human run, so the gating is on behaviour, not on the query path.
- **The fork must move these emissions out of the AI layer into game hooks** if health deltas,
  combat transitions and low-health warnings are to exist independently of who is driving. That
  is a small change and worth doing early, because it is the difference between a human baseline
  that is comparable to an agent run and one that is not.
- It also means `low_health` — one of only two HP-adjacent signals — has still never been
  observed, despite 25 minutes of play.

### F21 — The bare `"Shrine"` name is the mod's *fallback*, not the rule *(corrected)*

**This finding was wrong when first written, and the correction matters more than the
original.** It was drawn from a single shrine sighting.

The one shrine in the run reported:

```jsonc
{"type":"shrine","name":"Shrine","cost":1,"locked":false,"usesRemaining":-1}
```

From that I concluded shrine subtype was unrecoverable and that `use_shrine_chance` and
`use_shrine_blood` could never be offered. Upstream's own `PROTOCOL.md` example shows
`"name": "Shrine of Combat"`, which should have been treated as evidence against the
conclusion rather than as a documentation error.

Checking the source settles it. `EntityDetector.CreateShrineInfo` matches the lowercased
GameObject name against seven substrings and **falls back to the bare string `"Shrine"`**:

```csharp
string displayName = "Shrine";
if (objName.Contains("combat"))      displayName = "Shrine of Combat";
else if (objName.Contains("blood"))  displayName = "Shrine of Blood";
else if (objName.Contains("mountain")) displayName = "Shrine of the Mountain";
else if (objName.Contains("chance")) displayName = "Shrine of Chance";
...
```

All seven display names are present in the **installed 1.1.1 assembly**, verified by string
extraction at both byte alignments. So subtype **is** recoverable; the run simply met a shrine
whose GameObject name matched none of the seven keys.

Corrected handling, implemented in `brain/stock_adapter.py`:

| Display name | Maps to |
|---|---|
| `Shrine of Chance` | `shrine_chance` — offerable, irreversible, worth-gated |
| `Shrine of Blood` | `shrine_blood` — offerable, irreversible, worth-gated |
| `Shrine of Combat` / `of the Mountain` / `of Gold` / `of Rest` / `of Order` | identified, **deferred** — no MVP verb |
| bare `Shrine` | **unmapped, fails closed** |

The fallback fails closed because a bare `"Shrine"` could be any kind, **including a Shrine of
Blood that costs health rather than gold**. Offering an irreversible action against an unknown
cost type is precisely what the commitment model exists to prevent, so the adapter declines.
"Identified but deferred" and "unidentified" are tracked separately, because they call for
different work: the first needs a verb, the second needs better classification in the mod.

Two residual concerns worth carrying into the fork:

- **The substring matching is order-dependent and collision-prone.** The first matching key
  wins, so any GameObject name containing an earlier key is captured by it regardless of what
  the shrine actually is. A rename upstream, or a shrine whose internal name happens to contain
  another key, silently mislabels an irreversible action. The fork should match on the
  interactable's type rather than on a substring of its GameObject name.
- **`cost: 1` is not believable** for any shrine, and `usesRemaining: -1` is a third sentinel
  (F17's pattern, integer this time). The adapter passes cost through as sent, which is flagged
  in `ADAPTER_ASSUMPTIONS`; the real cost and cost *type* (gold vs health) must come from the
  mod, since `execution_contract.cost_limit` is enforced numerically at the moment of effect
  (`05-PROTOCOL.md` §5.4) and a wrong unit there is a wrong authorization.

The methodological lesson is the one this repo keeps relearning: **a single observation is
enough to find a behaviour and never enough to bound it.** Twelve shrine sightings in the run
were twelve polls of one shrine.

### Teleporter classification, confirmed exhaustively

All five combinations of (`charged`, `bossActive`, `chargePercent`) appeared, which settles the
rule ordering — `charged` must be tested first, because `charged:true, bossActive:true` occurs:

| `charged` | `bossActive` | `chargePercent` | Our type |
|---|---|---|---|
| false | false | 0 | `teleporter_idle` |
| false | true | 0 or mid | `teleporter_charging` |
| true | true | 100 | `teleporter_charged` |
| true | false | 100 | `teleporter_charged` |

---

## §3g Runs 2 and 3, and what they settled

`recordings/run-02`, two labelled sessions, both `--observe` with AI control off:

| Session | Survivor | Difficulty | Length | Stages reached |
|---|---|---|---|---|
| 0 | Huntress | Rainstorm | 111 min | `golemplains` → `goolake` → `wispgraveyard` → `shipgraveyard` |
| 1 | Engineer | Rainstorm | 43 min | `blackbeach2` → `foggyswamp` → `frozenwall` → `dampcavesimple` |

Both labelled at record time, so unlike `human-01` they did not need a sidecar. Gold reached
4,316 and 7,667; 36 distinct real items across all recordings.

Replayed through the real brain: **6,569 and 2,527 ticks, registry invariants holding on every
one.** With `human-01` that is **10,578 ticks** of real telemetry through the real registry and
request builder with zero invariant failures and zero crashes.

### The F21 correction is validated end to end

This is what runs 2 and 3 were for. Both shrine cases appeared in the same dataset:

- **`Shrine of Blood`** — 50 sightings in the Huntress session, 7 in the Engineer session. It
  classified to `shrine_blood`, and **`use_shrine_blood` was offered on one tick in each
  session**: the first time the registry has proposed a worth-gated irreversible action on real
  telemetry. Being offered required both classification *and* proximity, which is why one tick
  and not fifty.
- **Bare `"Shrine"`** — 74 sightings in the Engineer session, all correctly dropped as unmapped.

So the same recording set demonstrates the named path working and the fallback failing closed.
Had F21 stood as originally written, `use_shrine_blood` would have been permanently unreachable
and the error would have looked like correct conservative behaviour.

### F18 is now measured over four survivors and ten thousand ticks

Across all 10,578 ticks, in every objective phase, on four survivors: **no question was ever
sendable.** The omission reasons are identical every time — `next_action`,
`engagement_response`, `strategy` and `engage_range` each blocked by
`telemetry_unknown`. This is no longer an inference from a coverage table; it is a measured
property of the current system, and it is the field contract working as designed.

### Phase coverage is now good enough to test against

| Phase | Ticks (all runs) |
|---|---|
| `pre_teleporter` | 9,656 |
| `charging` | 825 |
| `charged` | 97 |

`defend_zone` was offered on **824 of 825** charging ticks and `advance_stage` on **59 of 97**
charged ticks. Both shortfalls are explained, and one of them is a finding:

- The 38 charged ticks without `advance_stage` are the **range gate working**. `advance_stage`
  is range-gated, so when the character was further than the interaction threshold the registry
  correctly offered `navigate:<teleporter>` instead. That is registry invariant 7 holding on real
  data.
- The single charging tick without `defend_zone` is **F22** below.

The teleporter lifecycle that the registry could not express at all before F2/F19 is now
exercised by real recordings across four survivors and seven distinct stages.

### F22 — A torn read, measured: the two queries disagreed on exactly one tick

`02-ARCHITECTURE.md` §1 replaced stock's separate state queries with a single snapshot because
loosely synchronised queries "can tear into a false-but-coherent state." That was an argument.
Here it is as a measurement.

Cross-checking `OBJECTIVE.objective` against the teleporter's own flags in the same assembled
tick, over all 10,578 ticks:

| `OBJECTIVE.objective` | teleporter classifies as | Ticks |
|---|---|---|
| `exploring` | `teleporter_idle` | 9,656 |
| `charging_teleporter` | `teleporter_charging` | 824 |
| `teleporter_charged` | `teleporter_charged` | 97 |
| `charging_teleporter` | **`teleporter_idle`** | **1** |

One tick, in `human-01`, where `OBJECTIVE` reported `charging_teleporter` while `INTERACTABLES`
still reported `charged: false, bossActive: false, chargePercent: 0.0`. The two responses
describe different instants either side of the activation, and the assembled tick is a state
that never existed.

**Measured tearing rate: 1 in 10,578 ticks, at a phase boundary.** Low, and precisely where it
hurts most — the consequence in our registry is that this tick offers `start_teleporter` for a
teleporter that has *already* been started. A command built from it would be wrong in the way
that is hardest to notice: syntactically valid, addressed to a real entity, and semantically
describing a world one frame stale.

Three things follow:

- **The single-snapshot decision is vindicated with data**, not just reasoning. The rate is low
  enough that testing would rarely catch it and high enough that a 2-4 Hz agent over a 40-minute
  run would meet it.
- **Torn reads concentrate at transitions**, which is also where irreversible actions live
  (`start_teleporter`, `advance_stage`). A defect that appears only at phase boundaries is a
  defect in exactly the commands that cannot be walked back.
- The adapter cannot fix this and does not pretend to; `eval/replay_stock.py` already notes that
  a tick assembled from six separate queries is torn by construction. Only the mod emitting one
  main-thread snapshot removes it.

### Smaller observations from these runs

- **Item names are internal codenames**, not display names: `Bear`, `Hoof`, `Seed`,
  `ScrapWhite`, `Tooth`, `Syringe`. Anything deriving `build.identity` must map codenames or
  reason over them directly; a catalog written against display names would silently match
  nothing.
- **`MonstersOnShrineUse` appears in the item list** and is worth verifying before it reaches
  JEV. It may be an internal marker rather than a pickup, in which case it belongs with
  `DrizzlePlayerHelper` in the hidden-item filter. Flagged rather than assumed — F21 is a recent
  reminder of what a single unverified inference costs.
- **No events in either session**, independently confirming F20: with AI control off, the entire
  event stream is silent, so `low_health` remains unobserved after roughly three hours of play.
- **`shop` remains unmapped** (658 sightings across all runs) — deferred by design, since
  multishop terminals are out of MVP scope.

### Tooling defect found by having two tools

`eval/inspect_recording.py` was reporting `shrine` and `teleporter` as unmapped while
`eval/replay_stock.py` was mapping both, because the inspector tested the raw `TYPE_TO_VERB`
keys and the replay used the adapter's `classify()`. Two tools disagreeing about the same
question is worse than either being wrong alone, since whichever a reader consults first becomes
the answer. The inspector now calls `classify()` directly, so they cannot diverge.

---

### F24 — The mod already knows the survivor; it just never says

Found while tracing F23. `CombatController.DetectCharacterClass()` reads
`BodyCatalog.GetBodyName(body.bodyIndex)` and sets `currentSurvivor` — it needs to, for the
per-survivor combat logic (Huntress sprint-shooting, MUL-T weapon modes, Engineer turrets). So
**survivor identity is computed inside the mod on every run and discarded at the socket.**

That changes the price of F18. Of the seven fields with no stock source, two are now known to be
one field read away:

| Missing field | Already inside the mod as |
|---|---|
| `self.hp_band` | `healthComponent.health` / `fullHealth` — `GetAllies()` already serializes both for allies |
| `run.survivor` | `CombatController.currentSurvivor`, via `BodyCatalog.GetBodyName` |

Neither needs new measurement, only exposure. The expensive gaps are unchanged: `hazards`,
`control.progress_band`, and the two feasibility facts, which need the mod to *measure* things it
does not measure today.

It also retires the workaround for the recording problem: once the fork sends `run.survivor`,
`--survivor` labels and `labels.json` sidecars become a cross-check rather than the only record.

---

## §3h First runs with the fork under AI control

`fork-gonogo-02`, `fork-02`, and `fc-selftest` — the last an accident, described below. These are
the first recordings in which the AI actually drove, and the event stream behaved very differently
from the human runs.

### F25 — A terminal event is not guaranteed, even when the action succeeds

In `fork-02` the driver sent `FIND_AND_INTERACT teleporter` at 92 s. `action_started` arrived, the
character walked 376 m, and at 140.7 s the objective flipped to `charging_teleporter` — the
interaction **succeeded**. No `action_complete` ever arrived.

This is the clean case, and it matters well beyond the harness:

- **The harness driver waited for it and never got it.** Its action lock never released, so it
  never sent `defend_zone`, and the character died in the boss event it had just started. Fixed:
  the lock now releases when the objective changes or after 45 s, and each release is recorded.
- **Protocol v2 holds the task slot until a terminal event arrives** (`lifecycle.task_occupied`).
  Against a mod that can skip the terminal event, that is the L2 deadlock class exactly — a slot
  held with nothing in flight. `brain/contract.py`'s per-command `bounded_deadline_ms` is therefore
  **mandatory, not defensive**, and the fork's first protocol work should make every command end in
  exactly one terminal event, including success.

`fork-02`'s *chest* command also never reported back, but it is **not evidence of the same thing**:
it was sent from the lobby before the character existed (F27), so it is confounded. An earlier draft
of this analysis read it as oscillation defeating a speed-based stuck detector. That may be true;
this recording cannot show it.

### F26 — `low_health` fires every physics tick for as long as HP stays low

It finally fired, after roughly 3.5 hours of recordings. It is level-triggered, not edge-triggered:

| Recording | `low_health` events | Rate while low |
|---|---|---|
| `fork-02` | 581, in three bursts | ~60/s |
| `fork-gonogo-02` | 261 | ~60/s |

The payload is `{"threshold":"30","current":"25"}` — strings again (F11). `current` is absolute HP,
making it a third HP signal after `damage_taken.remaining`. It kept firing at `current` 31–33 with a
threshold of 30, which is consistent with a **30% of max-HP** threshold on a ~110 HP Commando, but
that is inference from one survivor, not established.

At 2–4 Hz an agent would receive 15–30 identical `low_health` events per tick. That is noise for the
brain and exactly the outbound saturation case T30 exists for. Protocol v2 must emit it on the
**crossing**, once.

The same run shows healing only indirectly: `damage_taken.remaining` read 25 → 31 → 53 → 94 across
successive hits with no event in between. Healing is visible solely as the gap between two damage
reports — which is why `self.hp_band` still needs a polled value rather than an event stream.

### F27 — `OBJECTIVE` does not know you are in the lobby

In the character-select lobby, `QUERY_OBJECTIVE` answers `{"objective":"exploring",
"scene_name":"lobby"}` with no "Not in game" marker. `QUERY_INVENTORY` at the same instant correctly
says `Not in game`. The probe trusted `OBJECTIVE`, declared the run live from the lobby, and sent
`MODE roam` and `FIND_AND_INTERACT chest` at 0.1 s — which is what confounded `fork-02`'s chest
command.

This only surfaced because the combined `eval/fork_check.py` flow asks you to start the command
*before* the run; the old flow started the probe mid-run, where `OBJECTIVE` is truthful. Fixed:
a run counts as live only when `INVENTORY` has no marker and the scene is not a menu scene, on two
consecutive polls, followed by a 6 s wait for the drop pod. The driver applies the same test and
sends nothing outside a live run. The marker also appears mid-stage in real scenes (`blackbeach2`,
`dampcavesimple`) during loading and after death, so scene name alone would not have been enough.

### An accident, recorded as one

`recordings/fc-selftest` is not a planned run. A self-test listening on 7777 was answered by the
live game instead of the mock it was written for, and drove the character for about 40 seconds —
`MODE roam`, `FIND_AND_INTERACT` chest and teleporter, then `MODE wait` — without the player
expecting it. It is labelled as such in its sidecar and kept, because it is genuine fork telemetry
with AI control on.

It cannot recur: `ROR_JEV_PORT` now moves the harness off 7777, every self-test uses 17777, and the
mock refuses to run on 7777 at all. The mod only ever dials 7777, so a test on another port cannot
be reached by a running game.

---

## §3i Found while adding telemetry (fork build 2)

### F29 — The mod cancels repeat damage to the player, always

`RainflayerPlugin.HealthComponent_TakeDamage` is registered unconditionally at startup. Its comment
says it tracks what is damaging the player. It also **cancels** damage: a hit from an attacker that
already hit the player within `DAMAGE_COOLDOWN = 0.5 s` returns without calling the original method,
so the damage never happens.

- It is **not** gated on `EnableAIControl`. The human runs — `human-01`, `run-02` — had it too.
- Anything that hits more often than twice a second is largely blocked: beams, rapid melee, and
  probably damage-over-time ticks, which carry the attacker who applied them.
- It does not touch damage with no attacker (falls, environmental), so it is not total.

**Every recording in this project — about 3.5 hours — was made on an easier game than vanilla**, and
every JEV run would be too. Agent-versus-human comparisons are still like-for-like, since both sides
had it; comparisons with vanilla play, or claims about difficulty, are not. This is the same category
of problem `09-FORK-BASE.md` E6 raised against 2.0.0's Mithrix Herald, and it was missed in 1.1.1.

Whether to keep it is a decision, not a fix: it makes a crude agent survive longer, and it makes
every result less meaningful. Options are to keep it, to make it a config setting defaulting to off
(vanilla), or to make it a setting defaulting to on (today's behaviour, now visible).

**Decision (2026-09-21): config setting, default off.** `[Gameplay] BlockRepeatDamage` in
`justindwang.rainflayer.cfg`, fork build 3. Build 2's environmental-damage note stays above the
check, so it sees every hit whether or not blocking is on. Runs recorded before build 3 was
deployed are on the easier game; runs after it are vanilla unless the cfg says otherwise.

### F30 — `next_action` cannot be sent until the most expensive telemetry exists

An earlier plan said elapsed time plus hazards would unblock `next_action`. Tested against the real
request builder with every build-2 field filled in, **nothing becomes sendable**:

```
next_action            blocked by ['threat']
strategy               blocked by ['threat', 'build.identity']
engage_range           blocked by ['build.identity', 'threat.enemy_classes']
engagement_response    blocked by ['threat.reposition_feasible', 'threat.full_withdrawal_feasible']
```

`next_action` declares the whole `threat` block, and the field contract omits a question if any part
of a declared block is unknown (F4). Five parts are: `elite_present`, `enemy_classes`,
`being_focused`, and the two path-feasibility facts, the most expensive measurements in the plan. The
wrong claim came from the inspector, which counted a "partial" source as available while the brain
never does; the inspector now matches the brain.

Two ways through:

- **Measure all of `threat`**, feasibility included, before JEV is asked anything.
- **Narrow the declarations** to the parts each question's instructions actually use, and have the
  state builder drop unknown sub-fields rather than send them. F4 already recommended narrowing.
  `next_action` would then need `threat.enemies_near`, `threat.nearest_enemy_band` and
  `threat.boss_active` — stock already provides all three — plus hazards and time from build 2.

`build.identity` is separate: nothing derives it yet, and it blocks `strategy` and `engage_range`.
`02-ARCHITECTURE.md` permits a deterministic derivation from items.

**Decision (2026-09-21): narrow.** `next_action` now declares `threat.enemies_near`,
`threat.nearest_enemy_band` and `threat.boss_active` (`contract/questions.yaml`).
`build_request` gates every question on the full state, then sends `strip_unknown(state)`, so
no `unknown` value reaches JEV, and `strategy` (still declaring all of `threat`) is still omitted
when any part is unknown. `next_action` still needs `hazards` and `run.time_band`, which only
build 2's `QUERY_OBSERVATION` supplies. Until the adapter reads it, replays of stock recordings
still send nothing.


### F31 — The suite could report a crash as a pass

`eval/run_suite.py` read each test script's last stdout line for its counts. A script that dies
before printing one — a syntax error, an import error — produced no counts and was reported as
`[PASS] 0 passed, 0 failed`. Found when an edit to `eval/test_phase0.py` broke its syntax and the
suite total quietly fell from 232 to 81 while the disposition still read green. Now a missing
summary line or a non-zero exit with no counted failure is one failure, with stderr's last line
printed. Verified with a deliberately crashing script.

### F32 — JEV would be asked what a dead character should do next

With build 2's observation, `self.hp_band` can be `dead`, and `next_action` does not declare
`self.hp_band` — so on fork-06's three post-death ticks `next_action` was sendable. Nothing is
executed on a dead character, but the call costs tokens and records a decision about nothing.
The fix is a gate, not a declaration: `enabled_when` on `alive`, or the lifecycle suspending the
tick loop at death.

**Fixed.** One rule in `build_request`, ahead of every question: `hp_band: dead` omits all of them
with reason `dead`. A global rule rather than an `enabled_when` on each question, because no
question is meaningful for a dead character and a per-question gate can be forgotten on the next
one added. fork-06: 3 ticks, all now empty.


### F33 — Combat movement and the utility dash go off cliffs

fork-07 (build 4, Commando, Drizzle): the person watching saw the character, in combat, roll
backwards off a cliff three times. The observations agree exactly: environmental damage at 26 s
(−46 HP), 30 s (−51 HP) and 33 s (−19 HP, leaving 1 HP), each the fall-and-teleport penalty, and
death at 36 s. The falls ended the run.

Two paths, neither with an edge check (build 1 fixed roaming only):

- `ApplyMovementControl` backs straight away from an enemy inside the strategy's optimal range.
- `UseSkillIfReady` fires the utility whenever the target is within its range (20 m balanced).
  Commando's utility is a dive in the direction of travel -- backwards, while backing away.

**Fixed in build 5.** Combat movement probes for ground every 2 m out to 3 m ahead (build 1's ray)
and, if the heading is unsafe, takes the first safe detour at ±45°, ±90°, ±135°, 180°, or stands
still. The utility skill is skipped unless ground runs the whole way to 12 m in the direction it
will go (move vector, or aim if standing). Both log `[EDGE]` lines (≤ 1/s) to the BepInEx log.

**Build 1 was too strict, and build 5 corrects it.** In fork-08 (still build 4) the person
watching saw roaming stall on stairs and in sunken areas. Two causes in build 1's roam check:
the eye-level obstacle ray hits stair steps and rims, and build 1 made that case stop dead like a
cliff; and the ground ray started only 2 m above the probe point, so ground rising more than 2 m
within 3 m put the ray's origin inside the terrain and read as a cliff. Build 5 stops only for
cliffs (an obstacle re-picks the waypoint and keeps moving through `ApplyJumpDetection`), and
`GroundBelow` starts the ray 4 m up with the same 3 m reach below, shared by roam, combat
movement and the dash check.

The same run is the first real evidence for builds 2 and 4: every fall registered as
environmental damage, and the threat block classified every body it saw (Beetle, Lemurian,
Golem). `engage_range` was sendable on 13 of 18 ticks.


### F34 — Who to shoot is the mod's decision, and JEV cannot change it

fork-09: the person watching saw the character, holding Monster Tooth (kills drop healing orbs),
fire only at the Stone Titan while 17-18 smaller enemies wore it down; it died. The target is
chosen by `EntityDetector.FindBestTarget`'s fixed score -- boss +50, already in the crosshair
+40, elite +30, closeness up to +30, low health up to +20 -- so a boss wins and then holds. No
question in the catalog reaches it: `strategy` and `engage_range` set distance and aggression,
not the target. JEV also cannot see *why* Monster Tooth matters: `build.key_items` sends
internal names only, and identity counts it toward `survival`.

Making it JEV's call needs (1) a target-priority setting the mod obeys (e.g. boss / clear adds /
nearest), asked as a question, with today's score as the default; and (2) item effects in
state that JEV can read -- a one-line effect per item from the identity table would do.
After build 6 (visibility).

---

## §4 Pinned build (step 3)

Full record in `contract/pinned-build.yaml`.

| Component | State | Value |
|---|---|---|
| Risk of Rain 2 | **verified** | Steam build `21587608`, depot manifest `5715419509320521739`, Unity **2021.3.33f1**, installed 2026-08-19, default branch |
| Python | **verified** | 3.13.13, PyYAML 6.0.3 |
| Ollama | **verified** | present (Phase 6 only) |
| BepInEx | **unpinned** | no `BepInEx/` directory in the game install |
| R2API | **unpinned** | not installed |
| Rainflayer | **unpinned** | not cloned, not forked |
| r2modman | **not installed** | no profile directory in `%APPDATA%` |
| .NET SDK | **missing** | `dotnet --list-sdks` returns nothing |
| TypeSafe API key | **absent** | by design — Phase 0 makes no paid calls |

The game is pinned by build id, depot manifest, and SHA-256 of `Risk of Rain 2.exe`,
`UnityPlayer.dll`, `RoR2.dll` and `Assembly-CSharp.dll`. The human-readable version string is not
stored as a literal in `RoR2.dll` and could not be read off disk; the mod should report
`RoR2Application.GetBuildId()` in `HELLO_ACK` once it builds.

`brain/recorder.py` writes these fields into every recording's `session.jsonl`, so a recording
found later is self-describing rather than trusted from memory.

---

## §5 What is blocked, and on what

| Blocked | Blocked on | Who |
|---|---|---|
| ~~Step 1 install~~ | **DONE** — r2modman profile `Jev` (§3d) | done |
| ~~Step 2 go/no-go, including interaction~~ | **PASSED** — `gonogo-01/02/03` (§3e) | done |
| Step 7 recorded runs | Playing 3-5 runs with the recorder attached | **human** |
| ~~Building the forked mod~~ | **DONE** — IL-identical to stock, reproducible, deployed (`09-FORK-BASE.md` §9) | done |
| C# telemetry pass, hazard facts, time-sliced reachability | Step 7, so the real field shapes are known first | either, after step 7 |
| Ground-truth token count | A TypeSafe API key and one paid call | human (key) |
| C7 | Installing `jevkit` | either |
| F19 type classification; F11/F13/F17 coercion and filtering | Nothing — Python-side and actionable now | either |
| F3 text review | Nothing | either |
| **F18: the nine missing state fields** | **The forked mod. This is the critical path.** | either, after the fork |

**Nothing downstream is unblocked by more planning.** The next action that changes what is known
is step 7: play runs with the recorder attached, so the catalog is tested against real telemetry
instead of the fixtures in `eval/fixtures.py`.

Two things worth saying plainly now that the gate has passed:

**The risk register's top line was wrong, in the good direction.** "Rainflayer does not run on
current game version" was rated High/High and drove the whole structure of Phase 0. It runs. The
effort estimate withdrawn at the bottom of `04-ROADMAP.md` can be revisited on that basis — the
unbounded "compatibility repair" item is now bounded at roughly zero.

**F12 is the finding that matters most, and it was invisible from the documents.** Our registry
assumes `interactables` is a full-radius, rankable candidate set; the mod returns a 180°-cone
sample that listed one teleporter 269 m away and no chests at all. Every ranking decision in F1's
fix, and the "pre-filtered, pre-ranked" premise in `02-ARCHITECTURE.md` §3 rule 3, rest on
telemetry that does not exist yet. That is now the largest gap between the design and the game,
and it is C# work.
