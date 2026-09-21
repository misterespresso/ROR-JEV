# Protocol v2

> **Status: proposed, not frozen.** Freezes when `07-TEST-PLAN.md` exit criteria pass against the
> fake mod, the C# state machine, and the in-game conformance subset.
>
> **Revision 4** removes `ready_to_commit` from the MVP (it deadlocked the dispatcher), adds
> opportunity tokens, a replay watermark, reconnect reconciliation outcomes, an explicit execution
> contract on the wire, a defined point of no return, and backpressure.

---

## §1 What stock Rainflayer lacks

Category-addressed commands; no correlation identity; no session or scene scoping; no
idempotency; no preemption semantics; no execution contract the mod can validate.

---

## §2 Session and scene scoping

Entity IDs are meaningful only within a scene generation.

```jsonc
"session_id": "sess-7f3a",   // one brain<->mod connection
"scene_gen":  14             // increments on every stage load
```

Mismatch rejects with `stale_session` / `stale_scene`.

### Execution identity survives the connection

A connection session is **not** the unit of execution identity. If the socket drops while a task
runs, the mod **preserves the running action under its original `command_id`** and reports it on
reconnect. Killing execution identity on disconnect would make a completed purchase
indistinguishable from one that never started.

Old-session commands are still **rejected** — but the *action they authorized* remains
identifiable.

---

## §3 Handshake and reconnect

```jsonc
-> { "type":"HELLO", "protocol_version":2, "brain_mono_ms":40122,
     "resume_session":"sess-7f3a" }              // null on first connect
<- { "type":"HELLO_ACK", "protocol_version":2, "session_id":"sess-8c02",
     "prior_session_recognized": true,
     "scene_gen":14, "mod_mono_ms":918101, "run_id":"run-221",
     "game_version":"1.3.9", "mod_version":"ror-jev-0.1.0", "registry_version":1,
     "command_watermark": 4310,                  // §5.2
     "capabilities":["observation_v2","navigate_target","interact_target","search",
                     "set_posture","disengage","defend_zone"] }
```

The brain **refuses to start** on protocol mismatch or a missing required capability.
`capabilities` drives capability-gating (`04-ROADMAP.md`): actions whose capability is absent
never enter the registry, so their questions are never asked.

Clock domains: exchange a monotonic offset at handshake. Never compare raw wall clocks.

### §3.1 Reconnect reconciliation

Revision 3's `STATUS` returned one active command plus a bounded terminal list, and treated
absence as proof of non-arrival. Absence is ambiguous between *never arrived*, *completed and aged
out*, and *discarded with the session*. **T18 promised a distinction the protocol could not
make.**

Reconciliation is now per-command and explicit:

```jsonc
-> { "type":"RECONCILE", "command_ids":["cmd-104","cmd-118"] }
<- { "type":"RECONCILE_RESULT", "results": {
       "cmd-104": { "outcome":"terminal", "state":"completed", "effect":"gold_spent:25" },
       "cmd-118": { "outcome":"history_unavailable" } } }
```

| Outcome | Meaning |
|---|---|
| `active` | Still running; `state` given |
| `terminal` | Finished; final state and effect given |
| `received_pending` | Receipted on arrival but not yet enqueued for execution — it *did* arrive |
| `not_received` | **Provable** non-arrival — the replay window is live and no receipt names it |
| `history_unavailable` | Admitted at some point, detail aged out of the window |

Receipt is registered **before** execution is enqueued, which is what makes `received_pending`
distinguishable from `not_received`. Absence from the execution table is not absence from the
wire, and reporting `not_received` for a queued command would invite a duplicate consequential
command.

**`history_unavailable` fails closed.** The brain does **not** automatically reissue a consequential
interaction. It treats the opportunity as spent and lets the next observation tell it the truth —
the chest is either open or it is not.

---

## §4 Observation snapshot (mod -> brain)

One main-thread pass, so health, threat, and objective describe the same instant.

```jsonc
{
  "type":"OBSERVATION",
  "session_id":"sess-8c02", "scene_gen":14, "run_id":"run-221",
  "observation_id":"obs-812", "seq":4417, "t_capture_mono_ms":918244,
  "registry_version":1,

  "run":   { "survivor":"Commando", "stage_name":"Distant Roost", "stage_index":1,
             "difficulty":"Drizzle", "difficulty_coefficient":1.15, "run_elapsed_s":214.7 },
  "self":  { "hp":104.0, "hp_max":110.0, "hp_history":[[918144,108.0],[918194,104.0]],
             "barrier":0.0, "shield":0.0,
             "skills":[{"slot":"primary","ready":true,"cooldown_remaining":0.0}],
             "equipment":{ "name":"Ocular HUD","ready":true,"supported_activation":true,
                           "valid_target_available":true,"activation_compatible":true },
             "move_speed":7.4, "position":[12.1,3.0,-44.8] },
  "hazards":{ "taking_environmental_damage":false,"active_dots":[],
              "incoming_projectiles":0,"in_hazard_volume":false },
  "threat": { "enemies":[{"entity_id":"e-91","body":"Lemurian","class":"melee_swarm",
                          "is_elite":false,"distance":18.4,"is_targeting_player":false}],
              "boss_active":false,
              "reposition_feasible":true, "full_withdrawal_feasible":true },
  "interactables":[{ "entity_id":"entity-47","type":"chest","display_name":"Chest",
                     "cost":25,"cost_type":"gold","locked":false,"distance":3.1,
                     "in_interaction_range":true,                  // §7
                     "opportunity_token":"opp-47-a91c",            // §5.3
                     "path_reachable":true,"reachability_age_ms":420 }],
  "objective":{ "phase":"pre_teleporter","teleporter_found":false,
                "teleporter_charge":0.0,"pillars":null },
  "inventory":{ "gold":140,"items":[{"name":"Soldier's Syringe","count":2}] },
  "control":{ "active_command_id":"cmd-103","active_state":"navigating",
              "progress":{"distance_delta_5s":22.4,"state":"advancing"} }
}
```

Raw numbers are correct **here**; the brain bands them before they reach JEV.

**Snapshot cost rule:** this pass **copies facts**. Reachability and feasibility are computed on a
**main-thread time-sliced schedule** — a few candidates per frame — cached, and exposed with
`*_age_ms`. "Background cadence" means *scheduled*, not *off-thread*: Unity APIs crash when called
from a worker, which is why the stock mod already marshals everything through a main-thread
dispatch queue. Only genuinely thread-safe computation over copied data may leave the main thread.

---

## §5 Command (brain -> mod)

```jsonc
{
  "type":"COMMAND",
  "session_id":"sess-8c02", "scene_gen":14,
  "command_id":"cmd-104", "command_seq":4412,
  "observation_id":"obs-812",
  "origin":"jev:next_action:obs-812",
  "channel":"task",
  "command":"INTERACT_TARGET",
  "action_id":"use_shrine_chance:entity-51",

  "execution_contract": {                      // §5.4 - what the mod validates
    "verb":"use_shrine_chance",
    "target_id":"entity-51",
    "expected_phase":"pre_teleporter",
    "cost_limit": { "type":"gold", "amount":75 },
    "max_uses": 1,
    "opportunity_token":"opp-51-3de8"
  },
  "accept_deadline_mono_ms": 918420
}
```

### §5.1 Idempotency vs repeated intent

These are different problems and revision 3 conflated them.

- **Idempotency** — the *same* `command_id` arriving twice must not execute twice.
- **Repeated intent** — a *new* `command_id` asking to use the same shrine again is a legitimate
  new decision. Idempotency must not block it, and must not silently permit it either.

Idempotency is handled by the command table and watermark (§5.2). Repeated intent is governed by
opportunity tokens and `max_uses` (§5.3).

### §5.2 Replay watermark

Revision 3 retained 512 commands and evicted terminal entries — after which an identical old
command looks new. Bounded storage cannot give at-most-once on its own.

```
command_seq   monotonic per connection, assigned by the brain
watermark     highest seq whose record has been evicted
window        retained records above the watermark
```

| Incoming | Result |
|---|---|
| `seq` <= watermark | **`rejected(command_history_expired)`** — never executed |
| `seq` in window, known, identical content | Return existing status; no re-execution |
| `seq` in window, known, different content | `rejected(duplicate_id_conflict)` |
| `seq` in window, unknown | Execute |
| `seq` above highest seen | Execute |

An expired or forgotten command is **never** eligible for execution again. The watermark is sent
in `HELLO_ACK` so the brain knows what it can still reconcile.

### §5.3 Opportunity tokens

For consequential and repeatable interactions, the observation issues a **one-shot token** per
currently-interactable target:

- The token is bound to `(run_id, scene_gen, entity_id, interaction opportunity)`.
- A command must carry the token matching the observation it cites.
- The token is **consumed at the moment of effect**.
- A replayed command carrying a consumed token is `rejected(opportunity_consumed)`.
- A new, deliberate second use requires a **new token** from a later observation.

This is what gives at-most-once *effects* across eviction and reconnect, where command-ID
idempotency alone cannot.

### §5.4 Execution contract: the mod validates what JEV authorized

Revision 3 said fresh authorization binds interaction kind, phase, cost exposure and expiry — but
the wire carried only target, action ID and deadline, and stated `action_id` is "a key into the
registry." **The registry lives in the brain's observation. The mod cannot look it up.** It was
validating nothing.

The `execution_contract` block is transmitted explicitly and validated against the mod's own
supported action definitions and live game state.

**Affordability is not a spending authorization.** A price can remain affordable while exceeding
what JEV actually evaluated — the gold total changed, or a multishop terminal costs more than the
chest that was judged. `cost_limit` is a hard numeric ceiling enforced at the moment of effect.

Semantic bands go to JEV for judgment; **numeric limits go on the wire for enforcement.** Both are
needed and they are not substitutes.

### §5.5 Command vocabulary

| Command | Channel | Typed args |
|---|---|---|
| `NAVIGATE_TARGET` | task | `{target_id}` |
| `INTERACT_TARGET` | task | `{target_id}` + `execution_contract` |
| `SEARCH` | task | `{for: "teleporter" \| "interactables"}` |
| `DEFEND_ZONE` | task | `{target_id}` |
| `FIGHT_TARGET` | task | `{target_id}` |
| `DISENGAGE` | disengage | `{from:[ids], style:"reposition"\|"withdraw"}` |
| `SET_POSTURE` | posture | `{strategy, engage_range}` |
| `USE_EQUIPMENT` | equipment | `{target_id?}` + `execution_contract` |
| `CANCEL` | any | `{command_id}` |
| `RECONCILE` | — | `{command_ids:[...]}` |

---

## §6 Authorization freshness

1. **`accept_deadline_mono_ms`** — absolute, in the mod's clock domain. The mod refuses to
   **start** after it passes.
2. **Observation age** — the mod retains each observation's `t_capture_mono_ms` and rejects
   commands citing one older than `max_observation_age_ms`.

Neither bounds the task itself.

---

## §7 MVP lifecycle: navigation and interaction are separate

Revision 3's `ready_to_commit` **deadlocked**: `task_occupied()` included it, so the dispatcher
could never issue the commit that would clear it, and the task always ended
`failed(commit_unauthorized)`.

The MVP takes the simpler lifecycle:

```
NAVIGATE_TARGET  →  accepted → navigating → completed(arrived)
                    ... fresh observation, fresh JEV decision ...
INTERACT_TARGET  →  accepted → committing → completed
```

**Interaction actions are offered only when `in_interaction_range` is true.** An interaction never
initiates a long navigation, so there is no authorization spanning a long journey and no
parent/child bookkeeping.

Range gating does **not** by itself prove the authorization is fresh — a character can stand
beside a chest indefinitely. What splitting buys is that no interaction is ever authorized
*before* long-distance travel. Freshness is established by three separate checks at effect time:
**observation age**, the **execution deadline**, and **live range revalidation**.

`ready_to_commit`, parent-command ownership, and commit children are **deferred** to a later
extension. If reinstated, they require a dispatch branch that authorizes a commit **without
acquiring a second task slot**, plus terminal-state rules for both command IDs.

---

## §8 Pre-effect validation

Immediately before the effect, in the **same main-thread step** as the effect itself:

1. Session and `scene_gen` current
2. Target exists and is still interactable
3. `execution_contract.expected_phase` matches live phase
4. Cost at or below `execution_contract.cost_limit`
5. `opportunity_token` valid and unconsumed
6. Authorization fresh
7. No higher-precedence channel owns movement

Validation and the irreversible game operation occur together, consuming the authorization once.
**The mod never substitutes a different target.**

---

## §9 Preemption

- A **started** action is never retroactively `rejected`.
- Safety preemption moves `accepted`/`navigating` to **`suspended(preempted_by_safety)`**.
- On clear: `resumed` restores **the state that was interrupted** and revalidates freshness.
  An `accepted` interaction must not acquire navigation behaviour on resume. If it is no longer
  valid: `failed(preempted_unrecoverable)`.
- `max_suspend_ms` exceeded: `failed(suspend_timeout)`.
- **`committing` is not suspendable** — see §10.

---

## §10 The point of no return

Revision 3 allowed `any non-terminal → cancelling`, which includes `committing` — so the mod could
report `cancelled` after the gold was already debited.

**`committing` is the point of no return.**

| When | Cancellation |
|---|---|
| `accepted`, `navigating`, `suspended` | Prevents execution -> `cancelled` |
| `committing` | **`too_late`** — the effect is in flight; the real outcome is reported |
| terminal | `too_late` with the recorded terminal state |

`CANCEL` gets an explicit reply:

```jsonc
{ "type":"CANCEL_RESULT", "command_id":"cmd-104", "outcome":"too_late",
  "state":"completed", "effect":"gold_spent:25" }
```

Because §8 performs validation and effect in one main-thread step, `committing` is brief and the
race window is a single frame — but it is defined rather than assumed away. **Terminal finality is
not enough if the wrong terminal state is emitted first.**

---

## §11 State transition table

| From | Event | To | Terminal |
|---|---|---|---|
| — | accepted | `accepted` | no |
| — | validation failed | `rejected(reason)` | **yes** |
| `accepted` | movement begins | `navigating` | no |
| `accepted` | accept deadline passed | `rejected(authorization_expired)` | **yes** |
| `accepted` | in range, interaction | `committing` | no |
| `navigating` | arrived | `completed(arrived)` | **yes** |
| `navigating` | no progress past threshold | `failed(stalled)` | **yes** |
| `navigating` \| `accepted` | safety reflex | `suspended` | no |
| `suspended` | clear, valid | **the interrupted state** (`accepted` or `navigating`) via `resumed` | no |
| `suspended` | clear, invalid | `failed(preempted_unrecoverable)` | **yes** |
| `suspended` | `max_suspend_ms` | `failed(suspend_timeout)` | **yes** |
| `committing` | effect succeeded | `completed` | **yes** |
| `committing` | pre-effect validation failed | `failed(reason)` | **yes** |
| `accepted`/`navigating`/`suspended` | `CANCEL` | `cancelling` | no |
| `cancelling` | acknowledged | `cancelled` | **yes** |
| `committing` | `CANCEL` | stays `committing`, replies `too_late` | no |

**Terminal: `completed`, `rejected`, `cancelled`, `failed`.**

Cancellation is asynchronous — the brain treats a task as active until **any authoritative
terminal outcome** arrives, not specifically `cancelled`. A completion racing a cancellation
clears the slot and is recorded as `completed`; the pending cancel is noted, never reported as
the outcome.

Rejection reasons: `stale_session`, `stale_scene`, `target_gone`, `not_interactable`,
`unaffordable`, `cost_limit_exceeded`, `phase_disallows`, `authorization_expired`,
`duplicate_id_conflict`, `command_history_expired`, `opportunity_consumed`,
`capability_unavailable`, `contract_unsupported`, `out_of_range`.

Failure reasons: `stalled`, `path_unreachable`, `preempted_unrecoverable`, `suspend_timeout`,
`superseded`.

---

## §12 Event ordering

```jsonc
{ "type":"EVENT", "session_id":"sess-8c02", "scene_gen":14,
  "command_id":"cmd-104", "observation_id":"obs-812",
  "event_seq":8813, "state":"rejected", "reason":"target_gone", "t_mono_ms":918402 }
```

- `event_seq` monotonic per session; a gap triggers `RECONCILE`.
- Events after a terminal state for a `command_id` are discarded.
- **Reflex activations are first-class events**, not silent actions — required for the T14
  provenance invariant, which otherwise cannot see reflexes that fire in C# without a journal
  entry.
- Aim for exactly-once **effects**, not exactly-once delivery.

---

## §13 Transport limits and backpressure

Revision 3 said "drop oldest non-command" and "drop oldest non-terminal event" — undefined when
everything queued *is* a command or a terminal event, which is exactly the saturation case.

| Limit | Value | On breach |
|---|---|---|
| Max NDJSON message | 256 KiB | Drop line, `protocol_error` |
| Malformed JSON | — | Discard to next `\n`, log, do not desync |
| Unknown `type` | — | Ignore with log |
| Inbound queue | 64 | See below |
| Outbound queue | 256 | See below |

**Backpressure, not silent loss.** Execution-critical messages are never dropped:

- **Inbound saturated with commands** — the mod replies `rejected(backpressure)` to new commands
  and continues servicing existing tasks. The brain treats this as no authorization.
- **Outbound saturated with terminal events** — the mod **stops producing observations** first
  (they are regenerable; terminal events are not). If still saturated, it **closes the connection**
  with `protocol_error(outbound_saturated)`, forcing a reconnect and reconciliation.

Losing an observation costs one tick. Losing a terminal event costs correctness.
