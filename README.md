# ROR-JEV

**JEV plays Risk of Rain 2.**

A Risk of Rain 2 agent where [TypeSafe AI's JEV](https://typesafe.ai/) — a System One model that
returns typed, calibrated decisions in 70-500 ms — selects every strategic goal and discretionary
action. A local LLM may advise. It may not act.

Status: **Phase 0 go/no-go PASSED; Phase 1 in progress.** Rainflayer 1.1.1 runs against current
RoR2 (Steam build 21587608) — the mod connects, all six state queries answer, the character moves
under command, and a chest was located, navigated to and opened end to end. The brain-side contract is implemented in `brain/` with 169 passing traces in
`eval/`. **No C# has been written yet**, and the largest gap between design and game is that the
stock interactable list is a 180° cone sample rather than the rankable candidate set the registry
assumes. Protocol v2 is **proposed, not frozen**. See
[`docs/08-PHASE0-REPORT.md`](docs/08-PHASE0-REPORT.md).

---

## The idea in one paragraph

JEV is not an LLM. It does not generate text. You hand it state plus typed questions and it
returns typed answers with calibrated probabilities, fast enough to sit inside a control loop —
TypeSafe's own launch demos include a Doom bot at ~10 queries per second. Risk of Rain 2's
interesting decisions are exactly the kind JEV is built for: *fight or reposition, loot or
advance, spend or save, which target, when to leave.* The frame-level work — aiming, pathing,
dodging — is deterministic and belongs in code at 50 Hz. An existing MIT-licensed mod,
[Rainflayer](https://github.com/justindwang/rainflayer), already provides that 50 Hz layer and a
TCP protocol to drive it. We fork it, replace its 4-second LLM brain with JEV, and extend the
protocol so commands address **specific entities** rather than categories.

## The authority contract

> **JEV is the sole selector of new strategic goals and discretionary actions.**
> Deterministic code may validate, reject, expire, cancel, or safely execute those intentions.
> **L0 may perform bounded survival reflexes.**
> **L2 may not issue or authorize any command.**

This replaces revision 1's "every command originates from JEV, no exceptions," which was
contradicted by its own fallback paths. The distinction that matters is **strategic authority vs
safety authority** — a reflex that dodges a rocket is not choosing a goal, and Python rejecting a
vanished chest is not choosing a different one.

Enforcement is by **provenance**: every command carries an `origin`, and a test asserts over every
recorded run that only `jev:` origins ever introduce a new goal. `--jev-only` disables the counsel
layer and is the default.

---

## Documents

| | |
|---|---|
| [`docs/01-RESEARCH.md`](docs/01-RESEARCH.md) | What JEV is, its exact API contract, its documented weaknesses, prior art in real-time control loops, and a teardown of Rainflayer's architecture and protocol |
| [`docs/02-ARCHITECTURE.md`](docs/02-ARCHITECTURE.md) | Three-layer design, authority model, field contract, policy layer, latency ledger, cost model, failure modes |
| [`docs/03-QUESTION-SET.md`](docs/03-QUESTION-SET.md) | The catalog — 5 always-on questions plus conditionals — hysteresis, thresholds, and the channel-arbitrated dispatcher |
| [`docs/04-ROADMAP.md`](docs/04-ROADMAP.md) | Seven phases, metrics, baselines, risk register, first milestone |
| [`docs/05-PROTOCOL.md`](docs/05-PROTOCOL.md) | **Proposed** protocol v2: sequenced snapshots, session/scene scoping, idempotency, two-stage commit, preemption, state-transition table |
| [`docs/06-ACTION-REGISTRY.md`](docs/06-ACTION-REGISTRY.md) | Versioned action-record schema — what JEV actually selects, and how gates are declared |
| [`docs/07-TEST-PLAN.md`](docs/07-TEST-PLAN.md) | The fake-mod suite that gates freezing the protocol, plus the CI consistency checks |
| [`docs/08-PHASE0-REPORT.md`](docs/08-PHASE0-REPORT.md) | **Phase 0 execution record** — what ran, the measured catalog size and cost model, 22 findings, and what is still blocked |
| [`docs/09-FORK-BASE.md`](docs/09-FORK-BASE.md) | **Fork-base decision** — why 1.1.1 on BepInEx 5, and the recordings as the fork's regression suite |

Read in order. `01` is evidence, `02` is design, `03` is the brain, `04` is the plan, `05`+`06`
are the contract, `07` is how we know the contract holds.

---

## Architecture at a glance

| Layer | Runs | Rate | Authority |
|---|---|---|---|
| **L0 Reflex** | C# / BepInEx, in-process | 50 Hz | **Safety** — bounded, enumerated survival reflexes |
| **L1 Judgment** | Python -> JEV cloud | 2-4 Hz | **Strategic** — all goal and action selection |
| **L2 Counsel** | Qwen3-8B local, off by default | event-driven | **None** — writes `state.advisor`, nothing else |

Python between L1 and L0 holds **validation** authority only: validate, reject, expire, cancel.
It may never substitute a target and attribute it to JEV.

Key numbers: JEV costs **$0.042 per million input tokens** with free output, answers in
**70-500 ms**, and batching questions into one call is roughly **10x faster and 12x cheaper** than
asking them serially. **Measured in Phase 0**: a typical tick serializes to 3,856 bytes, putting a
40-minute run at 2 Hz at **$0.19-0.26** and a worst-case loot-dense run at **$0.43-0.58** — under
the original $0.40-0.70 estimate. Bytes are measured; the token count behind those dollars is
still bracketed, because JEV's tokenizer is unpublished. See
[`docs/08-PHASE0-REPORT.md`](docs/08-PHASE0-REPORT.md) §2.

---

## Four constraints that shape everything

1. **JEV cannot do arithmetic.** Its own jaggedness docs say counting and numeric comparison are
   unreliable. So the mod converts numbers into named bands before they cross the wire, and JEV
   only ever exercises judgment. `hp: 87.3` becomes `hp_band: "healthy"`.
2. **Questions in one request cannot see each other's answers.** They are evaluated in parallel.
   Revision 1 broke this inside the economy group; revision 2 reintroduced it between
   `primary_objective` and `next_action`. **Exactly one question now selects the executable task**,
   and a CI check rejects any question referencing another's outcome.
3. **JEV cannot dodge.** At 70-500 ms it is not a reflex. L0 owns survival outright.
4. **An authorized intention must survive timing, cancellation, scene changes, and retries
   without becoming a different action.** This is the actual engineering content of the project —
   action IDs, session and scene scoping, idempotency, two-stage commit, defined preemption.

---

## Planned layout

```
ROR-JEV/
├── docs/                     # the plan (written)
├── mod/                      # C# BepInEx plugin, forked from Rainflayer
│                             #   + protocol v2: snapshots, typed targets, lifecycle
├── brain/
│   ├── state_builder.py      # raw snapshot -> banded facts (field contract)
│   ├── questions.py          # the JEV question catalog
│   ├── jev_engine.py         # the fan-out call
│   ├── policy.py             # hysteresis, thresholds, channel arbitration
│   ├── counsel.py            # optional local LLM advisor
│   └── journal.py            # observations / decisions / events streams
├── recordings/               # raw captured runs, for offline iteration
└── eval/                     # replay tooling, baselines, fault injection
```

## Prerequisites

- Risk of Rain 2 (Steam), r2modman, BepInEx 5.x, R2API — **versions pinned in Phase 0**
- Python 3.10+
- A TypeSafe API key from `console.typesafe.ai` (`TYPESAFE_API_KEY`)
- Optional: Ollama with Qwen3-8B Q4_K_M for the counsel layer

Recommended while building — puts the JEV API in Claude Code's context:

```bash
claude plugin marketplace add typesafe-ai/skills
claude plugin install typesafe@typesafe-ai
```

---

## First milestone

> Commando, Drizzle, solo, L2 disabled: autonomously clear Stage 1, leave through the teleporter,
> and correctly reject stale or invalid commands during an injected API outage.

That exercises the whole boundary — observation, judgment, execution, progression, failure —
instead of the happy path. Before it holds, faster inference mostly makes integration defects
arrive sooner.

---

## Credits

Built on [Rainflayer](https://github.com/justindwang/rainflayer) by justindwang (MIT), which
solved the hard part: driving a Risk of Rain 2 character by writing directly to the game's
internal input state instead of simulating a keyboard.
