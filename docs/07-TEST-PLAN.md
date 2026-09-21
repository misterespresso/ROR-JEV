# Test Plan

**The gate on freezing Protocol v2.** No catalog expansion until it passes.

Three review rounds found the same two defect classes recurring at different altitudes:
answer-chaining inside a parallel request, and a phase consuming capabilities from a later phase.
Both are cross-document consistency failures. Prose review does not catch them; generated checks
do.

---

## §1 Three execution targets, shared fixtures

A Python fake mod can faithfully reproduce the brain's own mistaken assumptions. Traces are
therefore defined **once, as data**, and executed against three independent implementations:

| Target | Runs | Covers |
|---|---|---|
| `eval/fake_mod.py` | Every trace | Brain logic, dispatch, policy |
| C# protocol state machine | Every trace, via a headless harness | Mod-side validation, lifecycle, tokens |
| In-game conformance subset | ~8 traces | Reality: timing, Unity threading, real entities |

Traces live in `eval/traces/*.yaml` and name only protocol-level facts, never implementation
details. A trace that passes the fake and fails the C# machine is exactly the bug this structure
exists to find.

The JEV client is stubbed in three modes: `canned`, `recorded` (replay from `recordings/`), and
`live` (small smoke subset only — it costs money and is nondeterministic).

---

## §2 Required tests

### T1 — Missing required gate blocks an action

**Given** registry contains `use_shrine_chance:entity-51`, `requires_worth_gate: true`
**And** answers contain `next_action -> use_shrine_chance:entity-51` but **omit**
`worth_use_shrine_chance:entity-51`
**When** the brain dispatches
**Then** no command is emitted, journaled as `not_authorized: gate_missing`

Revision 3's T1 selected the bare entity ID `entity-51`, which fails registry lookup — it tested
T9's path, not the gate. The selection must be a valid action ID for this test to mean anything.

### T2 — Duplicate command executes once

Same `command_id` + identical content -> existing status returned, gold debited **once**.
Same `command_id` + different content -> `rejected(duplicate_id_conflict)`.

### T3 — Disengagement does not suppress equipment evaluation

`engagement_response -> full_disengage` and `use_equipment_now -> 0.82` must emit **both**
`DISENGAGE` and `USE_EQUIPMENT`. Revision 2's dispatcher returned early.

### T4 — Old-scene and old-session commands are rejected

A command authorized under `scene_gen: 14` executed at `scene_gen: 15`, against a reused entity
ID -> `rejected(stale_scene)`, no interaction. Same for `stale_session`.

### T5 — Safety preemption has a defined outcome *(in-game: phase 3)*

`navigating` + reflex -> `suspended(preempted_by_safety)`; clear+valid -> `resumed`; clear+invalid
-> `failed(preempted_unrecoverable)`; timeout -> `failed(suspend_timeout)`. A started action never
becomes `rejected`. **`committing` is not suspendable.**

### T6 — Navigation and interaction are separately authorized *(rewritten)*

**Given** `entity-47` at distance 31 m, `in_interaction_range: false`
**When** the brain dispatches
**Then** the registry offers `navigate:entity-47`, **not** `open_chest:entity-47`
**And** `NAVIGATE_TARGET` is emitted

**When** the mod reports `completed(arrived)` and a **new observation** has
`in_interaction_range: true` with a fresh `opportunity_token`
**Then** the registry now offers `open_chest:entity-47`
**And** the **real dispatcher** — not the fake mod — emits `INTERACT_TARGET` carrying that token

**And** the task slot is free between the two commands, so the second is never suppressed.

Revision 3 deadlocked here: `task_occupied()` included `ready_to_commit`, so the dispatcher could
never issue the commit, and every such task ended `failed(commit_unauthorized)`. The old T6
asserted only that the fake mod could *emit* `ready_to_commit`, which is why the deadlock survived
review.

### T7 — Searching works with no visible interactables

Zero interactables, `teleporter_found: false` -> `next_action` **is** sent, with
`search_teleporter` and `explore` among its options; `SEARCH` with typed args
`{for: "teleporter"}` can be dispatched.

### T8 — Teleporter completion leads to an explicit stage advance

`pre_teleporter -> charging -> charged`, walked end to end. At `charged` and in range, the registry
offers `advance_stage:entity-03` and not `start_teleporter:entity-03`; with gates satisfied,
`INTERACT_TARGET` is emitted.

---

## §3 Effect-integrity tests

### T23 — Eviction does not resurrect a command

**Given** `cmd-104` executed an interaction and its detailed record was **evicted**
**When** the original `cmd-104` is replayed
**Then** `rejected(command_history_expired)` (seq at or below watermark)
**And** no second effect occurs

Bounded storage alone cannot give at-most-once; this is what the watermark is for.

### T24 — Cancellation immediately *before* the effect

`CANCEL` arriving while `accepted`/`navigating` -> `cancelling` -> `cancelled`, **no gold spent**.

### T25 — Cancellation immediately *after* the effect

`CANCEL` arriving while `committing`, after the debit -> `CANCEL_RESULT{outcome: too_late,
state: completed, effect: gold_spent:25}`. **The run must never report `cancelled` for a purchase
that happened.**

### T26 — Safety preemption at the commit boundary

Reflex firing during `committing` -> the action is **not** suspended; it completes or fails on its
own terms, and the reflex takes movement afterward.

### T27 — Target invalidated between arrival and interaction

Target destroyed after `completed(arrived)` but before `INTERACT_TARGET` -> pre-effect validation
fails -> `rejected(target_gone)`, **no substitution**, no token consumed.

### T28 — Repeated intent requires a new opportunity

**Given** `use_shrine_chance:entity-51` completed, consuming `opp-51-3de8`
**When** a **new** `command_id` replays the **same** token -> `rejected(opportunity_consumed)`
**And when** a later observation issues `opp-51-7b40` and a new command carries it -> executes

Idempotency must not block a legitimate second use, and must not silently permit a replayed one.

### T29 — Reconcile fails closed on unknown history

`RECONCILE` returning `history_unavailable` -> the brain does **not** reissue the consequential
interaction; it waits for the next observation to reveal ground truth.
`not_received` is returned **only** when provable (seq above watermark, inside the window, no
record).

### T30 — Queue saturation applies backpressure, never silent loss

**Inbound** saturated with commands -> `rejected(backpressure)` on new commands; existing tasks
keep running.
**Outbound** saturated with terminal events -> observations stop first; if still saturated, the
connection closes with `protocol_error(outbound_saturated)`, forcing reconnect + reconcile.
**No terminal event is ever dropped.**

---

## §4 Contract tests

| ID | Assertion |
|---|---|
| T9 | A selected `action_id` absent from the registry **fails loudly**; never coerced |
| T10 | Any omitted question yields no authorization, not a `KeyError` |
| T11 | `continue_current_action` offered **iff** a valid non-terminal task exists |
| T12 | After `CANCEL`, the task stays occupied until **any authoritative terminal** arrives; a racing `completed` clears the slot and is not reported as `cancelled` |
| T13 | A JEV-driven cancel carries `origin: jev:continue_is_worthwhile:<obs>`, not `watchdog:` |
| T14 | **Provenance invariant** over a full run: no `watchdog:` origin introduces a goal; every `reflex:` origin names a rule from the enumerated list. Requires `safety_reflex_events` (phase 4) — a reflex that acts without emitting an event is invisible here |
| T15 | Decisions citing an observation older than `max_observation_age_ms` are rejected pre-dispatch |
| T16 | Hysteresis never preserves an option that left the legal set, regardless of dwell |
| T17 | An action whose capability is absent from `HELLO_ACK` never enters the registry; its questions are never sent |
| T18 | Reconnect preserves the **execution identity** of a still-running action under its original `command_id` |
| T19 | `event_seq` gap triggers `RECONCILE`; post-terminal events are discarded |
| T20 | Malformed JSON discarded to the next newline without desyncing; oversized -> `protocol_error` |
| T21 | Budget admission refuses a call that would breach the hard cap; soft threshold drops tick rate first |
| T22 | On sustained API failure, **no new goals** are authorized and no exploration command is emitted |
| T31 | Sentinels `none` and `continue_current_action` never serialize to a command; both are journaled distinctly |
| T32 | `cost_limit` is enforced at the moment of effect: a target still affordable but priced above the limit -> `rejected(cost_limit_exceeded)` |

T32 is the test for "affordability is not a spending authorization."

---

## §5 Liveness

C6 checks that states are reachable and have outbound edges. That proves the graph is
well-formed, **not that the system progresses.**

| ID | Assertion |
|---|---|
| L1 | Every **accepted** bounded action reaches a terminal state within the trace's stated timing assumptions |
| L2 | No trace ends with a task occupying the slot and no in-flight command — the T6 deadlock class |
| L3 | Under sustained safe-hold (API down), the brain remains responsive and resumes authorizing within one tick of recovery |
| L4 | `cancelling` always reaches `cancelled` or a terminal state; it is never absorbing |

L2 is the check that would have caught revision 3's deadlock directly.

---

## §6 Generated consistency checks

Generated from `contract/questions.yaml` and `contract/capabilities.yaml`, run on every commit.

| ID | Check | Generated from |
|---|---|---|
| C1 | Every question has >=1 `policy_consumers` entry, and every named consumer exists in the dispatcher | `questions.yaml` |
| C2 | Every command the dispatcher can emit is in the protocol vocabulary | dispatcher + `05-PROTOCOL.md` §5.5 |
| C3 | Every enabled question's `required_capabilities` is a subset of the target phase's cumulative set | both files |
| C4 | `references_answers` is empty for every question | `questions.yaml` |
| C5 | Every registry `verb` equals its `action_id` prefix, and has a command + capability | `contract/actions.yaml` |
| C6 | Every protocol state is reachable; every non-terminal state has an outbound edge | transition table |
| C7 | `jevkit` lint passes over the serialized catalog | built request |
| C8 | Serialized catalog size recorded, replacing the estimated token figure | built request |

**C4 is metadata-driven, not lint.** Revision 3 proposed pattern-matching instructions for phrases
like "the currently chosen" — that cannot be reliable over arbitrary natural language. Every
question declares `references_answers` explicitly; the check asserts it is empty. Prose lint
remains as a **secondary guardrail**, not the mechanism.

C3 is what prevents a fourth recurrence of the phase-dependency defect.

---

## §7 Exit criteria

Protocol v2 freezes when:

1. T1–T8 pass on the fake mod **and** the C# state machine.
2. T23–T32 pass on both.
3. T9–T22 pass.
4. L1–L4 pass.
5. C1–C8 pass in CI.
6. The in-game conformance subset passes (T2, T4, T5, T6, T24, T25, T27, T30).
7. A recorded-observation replay produces a dispatch trace with zero `fail_loudly` events.

Only then is catalog tuning meaningful. Before that, tuning optimizes a contract that is still
moving.

---

## §8 Next implementation slice

Build exactly this, in order. **Do not add model questions.**

1. **Split navigation and interaction lifecycle** — removes the T6 deadlock (`05-PROTOCOL.md` §7).
2. **Typed action arguments and cost limits** — the `execution_contract` the mod can actually
   validate (§5.4).
3. **Replay watermark and reconnect reconciliation** — T23, T29.
4. **Cancellation-versus-effect race tests** — T24, T25, T26, T27.
5. **Corrected T1–T8 through the real dispatcher.**

The remaining work is to prove that one selected action produces **one correctly attributed
effect, or a clear refusal**, under realistic timing and failure conditions.
