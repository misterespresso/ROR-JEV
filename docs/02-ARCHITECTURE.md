# Architecture

> Revision 5. Design of record. Implemented in `brain/` and exercised by `eval/`.
> Supersedes the first draft, which contained three self-contradictions:
> an absolute "JEV authors every command" rule alongside Python-authored fallbacks, a
> retreat branch that commanded the character to hold position under fire, and weighted
> score arithmetic that conflicted with the catalog's own no-arithmetic rule.
> See the revision notes at the bottom.

## The governing constraint

Risk of Rain 2 is a real-time action roguelike. Frame-level survival happens at 50 Hz.
JEV answers in 70-500 ms. **JEV cannot dodge.** Anything that tries to make it dodge produces
a bot that dies on Stage 2 forever.

The split is not "AI vs code." It is **reflex vs judgment**:

- Things with a *correct* answer computable from geometry and numbers -> code, at 50 Hz.
- Things that are a *judgment call* under uncertainty -> JEV, as fast as the network allows.

That division is also what JEV's documented jaggedness demands: bad at counting and arithmetic,
good at bounded judgment over named facts.

---

## Three layers

| Layer | Runs | Rate | Latency | Owns |
|---|---|---|---|---|
| **L0 Reflex** | C# in-process (BepInEx) | 50 Hz | ~1 frame (20 ms) + reaction | Aiming, movement, skill firing, pathfinding, progress tracking, bounded survival reflexes |
| **L1 Judgment** | Python brain -> JEV cloud | 2-4 Hz (closed-loop) | 70-500 ms | **All strategic goal selection and discretionary action** |
| **L2 Counsel** | Local Qwen3-8B | Event-driven | 2-4 s | Advisory text only |

```
                 ┌─────────────────────────────────────────┐
                 │  L2 COUNSEL   Qwen3-8B (Ollama, local)  │
                 └───────────────┬─────────────────────────┘
                                 │  advisor: "<=200 token note"
                                 │  NO AUTHORITY - context only
                                 ▼
  ┌────────────────────────────────────────────────────────────────┐
  │  L1 JUDGMENT   Python brain                                     │
  │                                                                 │
  │  snapshot ──> state_builder ──> ONE fan-out call ──────> JEV     │
  │     (obs_id)                                              │     │
  │                                                  answers  ▼     │
  │                                            policy layer         │
  │                          (categorical hysteresis · per-question  │
  │                           thresholds · commitment gates)         │
  │                                                           │     │
  │                        VALIDATION AUTHORITY ONLY:         ▼     │
  │                        validate · reject · expire · cancel      │
  │                        (may never substitute a new goal)        │
  └───────┬───────────────────────────────────────────────────┬─────┘
          │ snapshots + lifecycle events         commands with │
          │              NDJSON over TCP 127.0.0.1:7777        │
          │              (see 05-PROTOCOL.md)                  │
  ┌───────┴───────────────────────────────────────────────────▼─────┐
  │  L0 REFLEX   BepInEx mod, FixedUpdate @ 50 Hz                    │
  │  SAFETY AUTHORITY: bounded survival reflexes                     │
  │  revalidates every command before it takes effect                │
  └──────────────────────────────────────────────────────────────────┘
```

---

## Authority model

The first draft claimed "every command originates from JEV, no exceptions" and then described
Python emitting `STRATEGY:balanced, MODE:roam` on API failure, Python picking the next-best
candidate on a stuck result, and L0 retreating on its own. Those cannot all be true.

The distinction that actually matters is **strategic authority vs safety authority**.

### The contract

> **JEV is the sole selector of new strategic goals and discretionary actions.**
> Deterministic code may validate, reject, expire, cancel, or safely execute those intentions.
> **L0 may perform bounded survival reflexes.**
> **L2 may not issue or authorize any command.**

| Actor | May do | May never do |
|---|---|---|
| **JEV (L1)** | Select goals, targets, postures, discretionary actions | - |
| **Python policy** | Validate a target still exists; reject an unaffordable purchase; expire a stale authorization; cancel a command whose preconditions lapsed; hold the previous *valid* intention | Choose a different target and attribute it to JEV; author a new goal on API failure |
| **L0 reflex** | Bounded, enumerated survival actions (disengage along a feasible path, break line of sight, avoid a telegraphed hit) | Select loot, objectives, purchases, or stage progression |
| **L2 counsel** | Write `state.advisor` | Anything else |

### Provenance, not a narrow signature

The first draft tried to enforce the contract by restricting the dispatcher's inputs to the JEV
answer object alone. That is the wrong mechanism — the dispatcher legitimately needs the
observation, the candidate registry, the action lifecycle, and current validity checks in order
to *validate*.

Enforce provenance instead. Every dispatched command carries an `origin`:

```jsonc
"origin": "jev:interact_target:obs-812"   // strategic - the only kind that introduces a new goal
"origin": "reflex:disengage_melee_lowhp"  // safety - from L0's enumerated reflex set
"origin": "watchdog:ttl_expired"          // validation - cancels, never creates
```

**Invariant, checkable in code and in the journal:** no command with a `watchdog:` origin may
introduce a new goal, and every `reflex:` origin must name a rule from a fixed enumerated list.
A test asserts this over every recorded run.

### What happens on API failure

Not `roam`. Roaming is a discretionary strategic behavior and wandering an unexplored stage
under an unknown threat is not a safe default.

The safe-hold behavior is: **continue the current command while it remains valid; if it becomes
invalid, stop initiating new goals and let L0's reflexes handle threats.** The character may end
up standing still in a cleared area. That is correct — it is the absence of authorization, not
a decision to wait.

---

## L1 in detail

### 1. Observation: one coherent snapshot

The first draft assumed the brain would issue five separate state queries per tick. That can
tear: health captured at one moment combined with threats and objectives from another produces a
state that looks coherent and is false.

**Replace the five endpoints with a single main-thread snapshot** carrying `observation_id`, a
monotonic sequence number, and a capture timestamp. See `05-PROTOCOL.md`.

### 2. The field contract

The first draft's state schema referenced fields the documented Rainflayer endpoints do not
provide - health and health history, barrier/shield, survivor and stage identity, difficulty
coefficient, skill and equipment readiness, enemy classes, navigation progress - while scheduling
the telemetry work for Phase 3. The catalog cannot be tested against recordings of fields that
were never captured.

Every state field is specified as a contract, and **missing data is represented as unknown, never
as a reassuring default**:

| Field | Source | Missing-data behavior |
|---|---|---|
| `self.hp_band` | Player `HealthComponent` | Omit dependent questions |
| `self.hp_trend` | Timestamped health ring buffer | `unknown` until sufficient history |
| `self.barrier_band` | Barrier / shield state | `unknown` |
| `self.skills_ready[]` | Skill cooldown state | Omit skill-discretionary questions |
| `self.equipment` | Equipment slot: identity + charge | **Disallow discretionary activation** |
| `threat.enemy_classes[]` | Enemy body classification | `unknown` |
| `threat.being_focused` | Targeting pressure | `unknown` |
| `control.progress_band` | Navigation progress tracker | `unknown`, explicitly **not** `stuck` |
| `objective.phase` | Objective state adapter | **Reject phase-specific actions** |
| `run.difficulty_band` | Difficulty coefficient + elapsed | `unknown` |

**Rule:** a question whose instructions reference an `unknown` field is **omitted from the
request entirely**, not sent with a null. JEV reads literally, and a null in state invites a
literal misreading. A missing answer is then treated by the policy layer as *no authorization*.

This is why minimum telemetry moves into Phase 0 (see `04-ROADMAP.md`).

### 3. State shape

Numbers become named facts before they cross the wire. JEV performs no arithmetic, ever.

```jsonc
{
  "observation_id": "obs-812",
  "seq": 4417,
  "run":       { "survivor": "Commando", "stage": "Distant Roost (1)",
                 "difficulty": "Drizzle", "difficulty_band": "gentle",
                 "time_band": "plenty" },
  "self":      { "hp_band": "healthy", "hp_trend": "steady",
                 "barrier_band": "none", "skills_ready": ["primary","secondary"],
                 "equipment": { "name": "Ocular HUD", "category": "buff_self",
                                "ready": true, "requires_target": false } },
  "threat":    { "enemies_near": "few", "nearest_enemy_band": "mid",
                 "elite_present": false, "boss_active": false,
                 "enemy_classes": ["melee_swarm"], "being_focused": false,
                 "reposition_feasible": true, "full_withdrawal_feasible": true },
  "economy":   { "gold_band": "can_afford_one",
                 "candidates": [ { "id": "entity-47", "type": "chest",
                                   "cost_band": "cheap", "distance_band": "near",
                                   "reachable": true } ] },
  "objective": { "phase": "pre_teleporter", "teleporter_found": false,
                 "teleporter_charge_band": "not_started" },
  "build":     { "identity": "undefined", "key_items": [] },
  "control":   { "active_action": "INTERACT_TARGET:entity-47",
                 "progress_band": "advancing",
                 "consecutive_failures": 0,
                 "last_latency_band": "fast" },
  "advisor":   null
}
```

Budget: **600-900 tokens.** Anything no question reads gets cut.

Three rules from the jaggedness doc:

1. **No raw floats requiring comparison.** `hp: 87.3` becomes `hp_band: "healthy"`.
2. **No counting.** `enemy_count: 7` becomes `enemies_near: "several"`.
3. **Candidates are pre-filtered, pre-ranked, and reachability-checked in code**, then given
   stable entity IDs. Never hand JEV 60 interactables and ask it to sort them.

Note `build.identity` is produced by a JEV question or a deterministic feature — **never by L2**,
which would breach the authority contract.

### 4. One fan-out call per tick

Every tick sends the question catalog in a single request. Measured by TypeSafe: 13 questions
batched took 0.27 s vs 2.71 s sequential, 12.2x cheaper, no change in answers.

**Every question must be independently answerable from the submitted state.** Questions may not
reference the outcome of other questions in the same request — they are evaluated in parallel and
no such outcome exists yet. See `03-QUESTION-SET.md` §1 for how the economy questions were
restructured to obey this.

### 5. Policy layer

Raw answers are not decisions. Deterministic Python sits between JEV and the dispatcher.

#### Confidence means concentration, not correctness

Choice `confidence` describes how concentrated the answer distribution is. It is **not** the
probability that a purchase is beneficial or that the character survives. The first draft's
stakes table conflated the two.

- For **"should we do this"**, ask a Noul and threshold its probability.
- For **"which of these"**, use a Choice and read `probabilities`.
- Noul responses carry **no `confidence` field** — only `noul`. Thresholds must be specified
  per proposition and never transferred between primitives.

#### Categorical hysteresis

The first draft tracked the confidence of whichever option won this tick, which does not prevent
flapping. Track **the probability of the currently held option**, available in every Choice
response's `probabilities` map.

Switching requires all three:

1. A challenger exceeds its **entry** threshold, and
2. support for the **currently held** option falls below its **exit** threshold, and
3. the challenger persists for a **minimum dwell period**.

Dwell is measured in **wall-clock milliseconds, not ticks**, because closed-loop pacing means
tick rate varies with network latency. Safety interruptions bypass dwell entirely.

**Legality overrides dwell.** If the held option leaves the legal set — its entity despawned, its
capability was withdrawn, its phase passed — it is dropped immediately regardless of dwell or exit
threshold. Dwell must never keep an illegal action alive.

#### Scores: cut from the MVP

Revision 2 proposed resolving score thresholds "by label through the legend." **That does not
work.** `legend` maps *integer indices* to descriptions; a returned score of `2.6` has no index,
and the criteria were full sentences rather than stable labels. Silently rounding would be exactly
the arithmetic this design forbids.

Both Scores were also read by **no branch of the dispatcher**, violating the rule that every
question must have a policy consumer. They are cut from the MVP.

If a Score returns, it needs a documented interpretation rule. The sound one is to consume the
categorical distribution directly and never the scalar:

```python
p = ans["threat_level"].probabilities
if p["Critical"] + p["Lethal"] > 0.50: ...    # explicit, no rounding
```

That requires short stable labels in the criteria, with prose moved into each level's structured
`summary`. Weighted arithmetic over raw scores remains **removed** from the design entirely.

#### Channel arbitration

Posture, task execution, and equipment activation are **separate channels** that can otherwise
issue contradictory movement orders. Strict precedence:

| Precedence | Channel | Owns movement? |
|---|---|---|
| 1 | Safety reflex (L0) | **Yes, exclusively while active** |
| 2 | Disengage intention | **Yes** |
| 3 | Active task execution (navigate / interact) | **Yes** |
| 4 | Routine combat & posture | Yes, only when 1-3 are idle |

Equipment activation is orthogonal and may fire alongside any of the above.

**Thresholds in this repo are unvalidated starting points.** None may be described as tuned until
measured against real game decisions.

### 6. Closed-loop pacing

Keep **one request in flight**; dispatch on arrival and immediately issue the next. The loop
self-tunes to network conditions.

- `min_interval` floor (default 250 ms = 4 Hz ceiling) for cost control
- `request_deadline` (default 2 s) is a **watchdog on the in-flight request** — it bounds how long
  we wait, and does not guarantee another decision arrives
- **Idle throttle** to 1 Hz when no enemies are near and no action is active

### 7. Latency ledger and staleness rejection

`observation_age_ms` in state is useful but insufficient: an input field cannot tell JEV how much
*additional* latency the current request will incur.

Track a ledger with **monotonic** timestamps, accounting for the C#/Python clock domains
(exchange an offset at handshake; never compare raw wall clocks):

| Stamp | Where |
|---|---|
| `t_capture` | L0, at snapshot |
| `t_recv` | Brain, on receipt |
| `t_infer_start` / `t_infer_end` | Brain, around the JEV call |
| `t_dispatch` | Brain, before emit |
| `t_apply` | L0, when the command takes effect |

**Before dispatch, deterministically reject decisions whose observation is already too old.**
That is a validation action, not a new goal. The *previous* request's measured latency is passed
into state as a semantic band (`control.last_latency_band`), which is the honest thing a state
field can convey.

### 8. Budget and fault handling

- **Admission control**: stop admitting calls before the hard budget is exhausted.
- **Soft throttle** at a separate, lower threshold — drop to 1 Hz first.
- Track `usage.input_tokens` from every response.
- Bounded retries with **exponential backoff plus jitter** on 429 and 529.
- On sustained failure: safe-hold (above), not `roam`.

### 9. Journaling

Three append-only streams, linked by `command_id`. A tick record must never contain an outcome that
has not happened yet:

```
observations.jsonl   { observation_id, seq, t_capture, state }
decisions.jsonl      { observation_id, command_id, answers, policy_trace, origin, t_* }
events.jsonl         { command_id, event, result, t_apply }
```

---

## Cost model

> **Revised in Phase 0 from measurement.** `python -m eval.measure_catalog` serializes the real
> request from `contract/questions.yaml` across the Stage 1 walk and writes
> `contract/catalog-size.json`. The r2 figure of ~2,000 input tokens per call (state ~700 +
> catalog ~1,300) **overstated a typical tick by roughly 1.6-2x**.
>
> **Bytes are measured. Tokens are not.** JEV's tokenizer is unpublished and no offline
> tokenizer for it exists, so tokens are bracketed between 4.0 and 3.0 characters per token —
> the optimistic end being ordinary English prose, the conservative end allowing for JSON
> punctuation and `snake_case:entity-id` keys fragmenting badly. Ground truth is
> `usage.input_tokens` on a real response, which Phase 0 cannot obtain because it holds no API
> key by design. **The first Phase 2 call must capture it and this bracket must be checked
> against it.**

Measured, milestone-1 configuration (Commando, Drizzle, solo, L2 off, no equipment):

| Tick shape | Questions | Catalog | State | Request | Tokens (est) |
|---|---|---|---|---|---|
| Floor — teleporter undiscovered, nothing visible | 4 | 2,460 B | 958 B | 3,462 B | 866–1,154 |
| Typical — a chest in range, teleporter known | 4 | 2,665 B | 1,147 B | 3,856 B | 964–1,285 |
| Irreversible — shrine in range, worth gate added | 5 | 3,207 B | 1,253 B | 4,504 B | 1,126–1,501 |
| Ceiling — registry at its 12-action cap, 4 gated | 9 | 6,415 B | 2,147 B | 8,606 B | 2,152–2,869 |

At $0.042/M input with output free:

| Tick rate | Calls/hr | Typical $/hr | Typical /40-min | Ceiling $/hr | Ceiling /40-min |
|---|---|---|---|---|---|
| 1 Hz (idle throttle) | 3,600 | $0.15–0.19 | $0.10–0.13 | $0.33–0.43 | $0.22–0.29 |
| 2 Hz | 7,200 | $0.29–0.39 | $0.19–0.26 | $0.65–0.87 | $0.43–0.58 |
| 4 Hz (planned ceiling) | 14,400 | $0.58–0.78 | $0.39–0.52 | $1.30–1.74 | $0.87–1.16 |

Three things this measurement settled:

1. **A run is cheaper than planned.** The README's $0.40–0.70 per 40-minute run now sits at the
   pessimistic end of the *ceiling*, not the typical case. At 2 Hz on typical ticks it is
   $0.19–0.26. Budget admission control should still be sized against the ceiling, because the
   ceiling is what a loot-dense stage actually looks like.
2. **The registry dominates cost, not the fixed catalog.** Between floor and ceiling the fixed
   questions barely move; `next_action`'s generated options and the per-action worth Nouls carry
   the whole 2.5x spread. Capping entity actions is a cost control, not only a prompt-quality
   one — and **registry invariant 9's cap of 12 is not implemented** (see `04-ROADMAP.md`).
3. **`engagement_response` costs about twice its estimate** — 225–300 tokens against the ~150 in
   `03-QUESTION-SET.md`. Its three options carry the longest `what`/`not_for` prose in the
   catalog. Not a problem at these prices; recorded so the budget table stops being fiction.

Rate-limit headroom is ample even at the ceiling: 4 Hz is 4 of 20 permitted req/s, and ~2,900
tokens at 4 Hz is ~11.5k of 250k permitted tok/s.

Rate-limit headroom is ample: 4 Hz is 4 of 20 permitted req/s and 8k of 250k permitted tok/s.

---

## Failure modes

| Failure | Mitigation |
|---|---|
| **Mode flapping** | Categorical hysteresis on held-option probability + dwell |
| **Stale decision applied** | Latency ledger + deterministic pre-dispatch rejection |
| **Late event misattributed** | All events carry `command_id` |
| **Target changed between decision and act** | L0 revalidates before interaction (`05-PROTOCOL.md`) |
| **Torn observation** | Single main-thread snapshot with sequence number. **Measured**: stock's separate queries disagreed on 1 of 10,578 recorded ticks, at a teleporter phase boundary — `08-PHASE0-REPORT.md` F22 |
| **JEV slow / 429 / offline** | One in-flight request, deadline, backoff+jitter, safe-hold |
| **JEV cannot dodge** | L0 owns enumerated survival reflexes |
| **Missing telemetry read as safe** | Field contract: unknown omits the question |
| **Cost runaway** | Admission control + soft throttle |
| **Rainflayer broken by a patch** | Phase 0 go/no-go gate |

---

## What stays deterministic on purpose

Aim vectors and lead prediction; pathfinding and raycasting; cooldown tracking; gold arithmetic
and affordability; item count aggregation; distance and threshold comparisons; **progress and
stall detection**.

JEV's job is *which target, whether to fight, when to leave* — not *where is the chest* and not
*am I making progress*.

---

## Revision notes (r1 -> r2)

| Change | Reason |
|---|---|
| "JEV authors every command" replaced with strategic/safety/validation authority split | r1 asserted an absolute rule and then described three code-authored fallbacks |
| Provenance (`origin`) replaces the narrow dispatcher signature | The dispatcher legitimately needs observation and lifecycle state to validate |
| Five state queries replaced by one sequenced snapshot | Loosely synchronized queries can tear into a false-but-coherent state |
| Field contract added; telemetry moved to Phase 0 | r1's catalog depended on fields no documented endpoint supplied |
| Confidence reinterpreted as concentration; Noul thresholds separated | r1 applied one "0.85 confidence" gate across primitives; Noul has no confidence field |
| Hysteresis now tracks held-option probability + dwell | Tracking the winner's confidence does not prevent flapping |
| Weighted score arithmetic removed; legend-resolved labels added | r1's §5 contradicted the catalog's no-arithmetic rule |
| Channel arbitration made explicit | r1 could issue posture and navigation orders with no defined movement owner |
| Safe-hold replaces `roam` on API failure | Roaming is discretionary and unsafe under unknown threat |
| Journal split into append-only streams keyed by `command_id` | r1's tick record contained an outcome that had not occurred |
| Replay claims narrowed (see `04-ROADMAP.md`) | r1 implied replay could establish better outcomes |

## Revision notes (r2 -> r3)

| Change | Reason |
|---|---|
| Choice keys are **action IDs**, not entity IDs (`06-ACTION-REGISTRY.md`) | An entity does not uniquely identify an action: a teleporter supports navigate, start, defend and advance, with different authorization requirements |
| `primary_objective` removed; `next_action` is the sole task selector | r2 could have `primary_objective: heal` and `next_action: open_chest` contradict each other **in the same parallel request** — the r1 answer-chaining defect one level up |
| Action question is **always sent**, even with zero interactables | r2 omitted it when the candidate list was empty, making the agent inert exactly when exploration mattered most |
| Both Scores cut | No policy consumer, and a legend cannot resolve a fractional score |
| Worth gates declared by the **registry**, fail-closed | r2's "absent Noul means no gate" authorized irreversible actions on missing data |
| Two-stage commit (`05-PROTOCOL.md` §7) | A 20-second navigation ending in a purchase was committing under a 20-second-old strategic authorization |
| Session + scene scoping, idempotency, preemption, reconnect, event ordering, transport limits | Correlation IDs alone do not prevent double purchases, cross-stage entity-ID reuse, or ambiguous reconnects |
| Hysteresis gains a legality override | Dwell could preserve an action whose entity had despawned |
| Hazard facts separated from enemy engagement | r2 defined "Safe" as no engaged enemies, ignoring environmental damage, DoTs and projectiles |
| `reposition_feasible` and `full_withdrawal_feasible` split | r2 gated two different movements on one boolean |
| Equipment four-field capability contract | Activation is not compatible with every movement or skill state |
| Dispatcher rewritten: no early return, `.get()` throughout, async cancellation, corrected origin attribution | Seven concrete defects; see `03-QUESTION-SET.md` §7 |
| Static consistency checks C1-C8 in CI | Two review rounds found the same two defect classes recurring at different altitudes. Prose review does not catch these reliably |
