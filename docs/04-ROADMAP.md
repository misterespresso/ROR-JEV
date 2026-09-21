# Roadmap

> **Revision 5.** Splits the freeze gate in two, pulls basic preemption into Phase 3 to match
> where T5 runs, adds minimal `defend_zone` to Phase 2, and points capability scheduling at
> `contract/capabilities.yaml` as the single source of truth.

Seven phases. Each ends in something observable.

**Standing rules:**
- Cost accounting, journaling, and validity gates ship with the **first paid call**.
- Static consistency checks C1-C8 (`07-TEST-PLAN.md`) run on **every commit** from Phase 1 on.

---

## Phase 0 — Compatibility and observability

**Deliverable: pinned build, movement smoke test, coherent snapshots, raw recorder.**

> **Status: partially executed 2026-09-20. The go/no-go gate PASSED.** Full account in
> [`docs/08-PHASE0-REPORT.md`](08-PHASE0-REPORT.md).
>
> | Step | State |
> |---|---|
> | 1. Install RoR2, r2modman, BepInEx, Rainflayer | **done** — r2modman 3.2.19, profile `Jev`, Rainflayer 1.1.1 on BepInEx 5.4.2122 |
> | 2. Run stock, confirm socket + movement — **go/no-go** | **PASS, complete** — mod connects, 6/6 queries, character moves, and a chest was opened end to end (`gonogo-03`) |
> | 3. Fork, build from source, pin versions | **done** — `mod/` builds from `01137e5` on BepInEx 5, **IL-identical to the validated DLL**, byte-reproducible, deployed ([`09` §9](09-FORK-BASE.md)). In-game confirmation pending |
> | 4. Minimum telemetry | **contract only** — field shape and gating implemented and tested in Python; no C# |
> | 5. Raw recorder | **done** — `brain/recorder.py`, 14 traces |
> | 6. Measure serialized catalog size | **done** — bytes measured; tokens still an estimate |
> | 7. Play 3-5 runs, recording throughout | **3 of 3-5 done** — Bandit, Huntress, Engineer; ~3 h of play, stage 4 reached, **10,578 ticks** replayed through the real brain with invariants holding on every one |
> | — | **22 findings.** F1/F2 blocked milestone 1, fixed; F6/F7/F8 settled by the install; F10/F15/F17 were probe bugs only real telemetry could expose; **F18 is the headline: no milestone-1 question is sendable against stock telemetry, because 7 of 16 declared state fields have no stock source.** The remaining work is C#, not Python |
>
> The fork exists, builds reproducibly, and is IL-identical to the DLL the recordings validated.
> What remains is step 4's telemetry pass — C# work, now unblocked — and its first item is F20.

The dominant unknown is whether Rainflayer still builds and runs against current RoR2. Its
Thunderstore page claims no version compatibility, it has 552 downloads, the author calls it an
early proof of concept, and Seekers of the Storm broke large swathes of legacy mods. **Assume
repair, not setup.**

1. Install RoR2, r2modman, BepInEx, Rainflayer — **done**, and it settled three findings
   (`08-PHASE0-REPORT.md` §3d). Let r2modman resolve the graph: Rainflayer 1.1.1 declares
   **BepInEx 5** (`bbepis-BepInExPack`) and **R2API_Core 5.3.0**, so R2API is installed even
   though no commit in upstream ever references it. Note the runtime is **1.1.1** while the
   source clone is **2.0.0** — a deliberate split, not drift.
2. Run stock. Confirm socket connection and that the character moves. **Go/no-go gate.**
3. Fork (MIT). Build from source. **Pin game and BepInEx versions** in
   `contract/pinned-build.yaml` and report them in `HELLO_ACK`. **Fork base decided in
   [`09-FORK-BASE.md`](09-FORK-BASE.md): `01137e5` (1.1.1) on BepInEx 5.** Neither upstream line
   builds reproducibly from its committed csproj, and no commit is provably the DLL we validated,
   so the fork's first milestone is reproducing the Phase 0 recordings before any v2 work.
4. **Minimum telemetry** — here, not Phase 3, because the catalog cannot be tested against
   recordings of fields never captured:
   - Single main-thread `OBSERVATION` with `observation_id`, `seq`, `session_id`, `scene_gen`
   - Health + timestamped history, barrier/shield
   - Survivor, stage, difficulty coefficient, elapsed time
   - Skill cooldowns; equipment identity + the four capability fields
   - **Hazard facts**: environmental damage, DoTs, incoming projectiles, hazard volumes
   - Enemy entity IDs, body, class, elite flag, targeting pressure
   - Interactables with entity ID, cost, type, lock state, reachability
   - Navigation progress (`advancing`/`slow`/`stalled`) — **measured, not inferred**
   - `reposition_feasible` and `full_withdrawal_feasible` as **separate** facts

   **Snapshot cost discipline:** the main-thread pass **copies facts; it does not run unbounded
   path searches.** Reachability and feasibility are computed on a **main-thread time-sliced
   schedule** — a few candidates per frame — cached, and exposed with `*_age_ms`. A 50 Hz snapshot
   that pathfinds to every interactable will tank the frame rate and corrupt the very timing the
   agent depends on.

   **"Background cadence" means scheduled, not off-thread.** Unity APIs crash when called from a
   worker — which is why the stock mod already marshals everything through a main-thread dispatch
   queue (`01-RESEARCH.md` §5). Only genuinely thread-safe computation over copied data may leave
   the main thread. Revision 3's wording invited exactly the bug the stock mod already avoids.
5. **Raw recorder** — every snapshot and event to JSONL, unbanded. Bands can be recomputed; lost
   telemetry cannot.
6. **Measure serialized catalog size** to replace the estimated token figure in the cost model.
7. Play 3-5 runs across survivors, recording throughout.

No reassuring defaults. An unreadable field is `unknown`, and `unknown` omits its questions.

---

## Phase 1 — Contract

**Deliverable: versioned action registry, protocol state-transition table, fake-mod test suite.**

Mostly parallel with Phase 0 — it needs no game and no paid calls.

1. `docs/06-ACTION-REGISTRY.md` schema implemented as typed records with an assertion for each
   registry invariant.
2. `docs/05-PROTOCOL.md` v2 state machine implemented on both sides.
3. `eval/fake_mod.py` and the T1-T32 / L1-L4 / C1-C8 suite from `07-TEST-PLAN.md`.

### Two gates, not one

Revision 4's freeze condition was circular: Phase 1 said the protocol freezes when the exit
criteria pass, but those criteria include in-game tests needing capabilities from Phases 3-4.

- **Contract candidate** — headless Python **and** C# conformance passes (T1-T4, T6-T14, T23-T32,
  L1-L4, C1-C8). The protocol is approved for integration and implementation proceeds.
- **Protocol freeze** — the in-game conformance subset passes, *after* the capabilities it
  exercises exist (T5 preemption and T18 reconnect land in Phase 3).

This permits implementation without falsely declaring the protocol proven.

### The phase capability manifest

Capability scheduling now lives in `contract/capabilities.yaml`, not prose. C3 generates from it:
for each phase, every enabled question's `required_capabilities` and every reachable registry
record's `capability` must be in that phase's cumulative set. **A test exercising a capability
absent from its phase is a scheduling bug and CI says so.**

That closes the two cases revision 3 left open: T5 (preemption) now runs in-game in Phase 3 where
`preemption` lands rather than a phase early, and `defend_zone` is pulled into Phase 2 because
milestone 1 requires completing teleporter charging. Stock Rainflayer already has holdout
behavior — if it verifies, it is **exposed through the new contract rather than rewritten.**

### Resolving the command/phase inversion, explicitly

r2 asked Phase 1 to consume `SET_POSTURE` and `DISENGAGE` while scheduling them for Phase 3.
**Both mechanisms are adopted, for different purposes:**

- **Capability-gating is the general mechanism.** Actions whose `capability` is absent from
  `HELLO_ACK` never enter the registry, so their questions are never asked. The catalog can never
  reference a command the mod cannot execute. Enforced by C3 and T17.
- **`SET_POSTURE` and `DISENGAGE` are pulled forward into Phase 2** as required capabilities,
  because the MVP catalog genuinely needs them. `DISENGAGE` may be minimal in Phase 2 (retreat
  along a feasible path) and gain survivor-specific mobility in Phase 4.

This is the check that would have caught the recurrence, so it is now automated rather than
remembered.

---

## Phase 2 — Minimal JEV vertical slice

**Deliverable: Commando, Drizzle, solo. Explore, loot, teleporter, stage advance. Typed actions.**

1. TypeSafe API key. Install the skill:
   ```bash
   claude plugin marketplace add typesafe-ai/skills
   claude plugin install typesafe@typesafe-ai
   ```
2. `brain/state_builder.py` — raw snapshot to banded facts. Unit-tested against recordings.
3. `brain/questions.py` — the catalog from `contract/questions.yaml`. `jevkit` lint in CI.
4. `brain/jev_engine.py` — one fan-out call. Pin `model="jev-1.13.0"`.
5. `brain/policy.py` — the dispatcher in `03-QUESTION-SET.md` §7 (implemented: `brain/policy.py`).
6. Mod capabilities required: `observation_v2`, `navigate_target`, `interact_target`, `search`,
   `set_posture`, `disengage` (minimal), **`defend_zone` (minimal)** — milestone 1 requires
   completing teleporter charging. Canonical list: `contract/capabilities.yaml`.
7. **Ships with the first paid call** — moved from r2's Phase 2:
   - Token accounting from `usage.input_tokens`
   - **Budget admission control**: refuse to issue a call that would breach the hard cap
   - The three journal streams
   - Registry-membership assertion at dispatch
8. Run at 0.25 Hz. Speed is Phase 3.

**Milestone check:** compare against stock Rainflayer on the same survivor.

---

## Phase 3 — Reliable execution at rate

**Deliverable: lifecycle enforcement, stale rejection, hysteresis, fault tests in-game.**

1. Live-mod hardening of the split navigate/interact lifecycle from Phase 2. **There is no
   two-stage commit** — range gating removed the need for one (`05-PROTOCOL.md` §7).
2. Latency ledger, monotonic clocks, handshake offset, pre-dispatch staleness rejection.
3. Categorical hysteresis with wall-clock dwell and **legality override**.
4. Closed-loop pacing: one in-flight request, 250 ms floor, 2 s deadline, idle throttle to 1 Hz.
5. **Soft throttle** at a lower budget threshold than the Phase 2 hard admission cap.
6. Bounded retries, backoff with jitter, safe-hold on sustained failure — **not roam**.
7. **Basic preemption**: `suspended` / `resumed` transitions preserving the interrupted state.
   Moved here from Phase 4 so that T5 is implemented in the phase that reruns it.
8. Fault injection **in-game**: T2, T4, T5, T6, T15, T18, T22, T24, T25, T27, T30.

**Deliverable: 2-4 Hz, no flapping, a measured dollar cost per run.**

---

## Phase 4 — Survival and capability expansion

| Work | Layer |
|---|---|
| `DISENGAGE` with survivor-specific mobility | C# |
| Enumerated safety reflexes with named rule IDs (provenance invariant T14) | C# |
| `DEFEND_ZONE` **enhancement** beyond the minimal Phase 2 version (choppy movement, edge cases) | C# |
| `SET_POSTURE` honoring `engage_range` (kiting) | C# |
| `USE_EQUIPMENT` with the four-field capability contract | C# |
| Speed guard for the >2 Goat Hooves navigation defect | C# |
| `DEFEND_ZONE`, `FIGHT_TARGET` | C# |

A generic "HP low, press utility" reflex is **not acceptable** — utility skills differ wildly
across survivors and loadouts.

---

## Phase 5 — Evaluation

**Deliverable: baselines, repeated matched runs, failure categorization.**

| Metric | Why |
|---|---|
| Stages cleared per run | Headline |
| Deaths per run and where | Where the agent is weak |
| Wall-clock per stage | Objective churn, navigation stalls |
| Items per stage; gold spent / earned | Whether action selection works |
| Invalid-action rate (rejections / commands) | Protocol and validity health |
| Switch frequency per hysteretic output | Flapping, measured |
| Gate acceptance rate per question | Whether the catalog fits reality |
| Latency p50/p95; stale-rejection rate | Network reality |
| USD per run | Cost discipline |

**Baselines:** scripted policy; stock Rainflayer at 0.25 Hz; a casual human run.

**Repeated matched runs**, with failures categorized as navigation stall / bad judgment / stale
decision / protocol rejection / death under reflex. Aggregate cause categories tell you where the
next hour goes — and separate navigation quality from decision quality, since navigation may cap
the ceiling regardless.

### What replay can and cannot establish

**Can:** which commands change under new thresholds; switch frequency; gate acceptance; invalid
action rates; policy self-consistency. Threshold replay is **zero inference cost**; re-running a
reworded catalog over recordings costs cents.

**Cannot:** that an alternative action produces a *better run*. Once the action diverges, every
later recorded state is counterfactual. Replay evaluates **policy behavior**, not outcomes.

State-shape changes test offline **if raw telemetry was recorded** — hence Phase 0 records raw.

---

## Phase 6 — Optional counsel

1. Ollama with **Qwen3-8B Q4_K_M**.
2. **Benchmark it with RoR2 running.** The ~112 tok/s figure is standalone; the game and the model
   share VRAM and GPU time, and a 16 GB card running a 9.9 GB model alongside RoR2 is tight.
   Measure actual throughput and frame-time impact under load before committing to the design —
   if it costs frames, it costs survival.
3. `brain/counsel.py` — async, four triggers, **duration-based** (6 s) rather than tick-count,
   one request in flight, 20 s cooldown, results bound to `scene_gen`, <=200 tokens into
   `state.advisor`, 45 s expiry.
4. Assert the authority invariant: no command may carry an L2-derived origin.
5. A/B with Phase 5's apparatus.

**Deliverable: a number saying whether the local LLM helps.** If not, leave it off.

Qwen3.8-27B does not fit (Q4_K_M ~17.1 GB against 16 GB with KV cache and buffers). Qwen3-14B Q4
is the upgrade path — but re-measure under load first.

---

## Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| ~~**Rainflayer does not run on current game version**~~ | ~~High~~ | ~~High~~ | **RETIRED 2026-09-20** — 1.1.1 runs on build 21587608; gate passed |
| **Protocol v2 + registry work exceeds estimate** | High | High | Phase 1 is now explicit and testable rather than folded into the slice |
| **Snapshot pathfinding tanks frame rate** | Medium | High | Copy-facts-only rule; cached reachability with exposed age |
| **Mode flapping** | High if unmitigated | High | Hysteresis + legality override, Phase 3 |
| **Stale or double-executed commands** | Medium | High | Idempotency, session/scene scoping, staleness rejection |
| **JEV cannot react fast enough** | Medium | High | L0 reflexes, Phase 4 |
| **Navigation caps the ceiling** | High | Medium | Phase 5 categorization separates it from judgment |
| **Local LLM steals GPU from the game** | Medium | Medium | Phase 6 measures under load before committing |
| **JEV API churn** | Medium | Medium | Pin `jev-1.13.0` |
| **Commencement pathfinding** | High | Low | Scope to Stages 1-5 |

---

## Open questions

1. **Prompt caching?** Undocumented. The catalog is most of the payload and static. Ask support.
2. **Score interpretation** — deferred with both Scores (`03-QUESTION-SET.md` §2). If they return,
   consume `probabilities` by named level, never the scalar.
3. **Multiplayer?** No. Solo.
4. **Success bar** — "reliably clears Stage 3 on Drizzle" for Phase 3.

---

## First milestone

> **Commando, Drizzle, solo, L2 disabled: autonomously clear Stage 1, leave through the
> teleporter, and correctly reject stale or invalid commands during an injected API outage.**

Exercises the whole boundary — observation, judgment, execution, progression, failure — not the
happy path. **Before it holds, faster inference mostly makes integration defects arrive sooner.**

---

## On the effort estimate

The original 55-80 hours is **withdrawn as a schedule**. Three items are unbounded until measured:

- **Compatibility repair** — unknown until the Phase 0 gate
- **Protocol v2, the action registry, and the test suite** — now a phase of its own, and the real
  engineering content of this project
- **Navigation work** — a long known-defect list that may cap the ceiling regardless

Re-estimate after Phase 0 and Phase 1. The honest statement today: Phase 0 is a bounded spike of
roughly a working week of evenings; everything after it is contingent on what that spike finds.
