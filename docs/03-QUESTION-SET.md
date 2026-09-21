# The JEV Question Catalog

> **Revision 5.** Implemented in `brain/policy.py`; dispatcher below matches the code.
> Revision 3 removed two Scores (no policy consumer, and a legend cannot resolve a fractional
> score). `primary_objective` removed — it could contradict `next_action` inside the same parallel
> request, which is the r1 answer-chaining defect at a higher altitude. `next_action` is now the
> sole source of executable task selection, over **action IDs** rather than entity IDs.

This is the agent's brain. Everything else is plumbing.

All questions go in **one fan-out call per tick**. Filtering happens in Python afterward.

---

## §0 Authoring rules

1. **Every question must be independently answerable from the submitted state.** No question may
   reference another question's outcome in the same request. Checked statically by C4
   (`07-TEST-PLAN.md`).
2. **Exactly one question selects the executable task.** Two questions that can both imply an
   action can contradict each other in parallel.
3. **Every question must have a policy consumer.** Checked statically by C1. A question the
   dispatcher never reads is pure cost — this is what removed both Scores.
4. **Never ask for arithmetic, counting, or numeric comparison.** Resolved into bands upstream.
5. **Never ask for a measurement code already takes.** Progress, affordability, distance,
   reachability, stalling.
6. **Reference state fields by name, in backticks.** Phrase positively.
7. **Criteria must be mutually exclusive in every reachable state**, and state what each option
   does *and does not* cover.
8. **Mask illegal options.** Solo runs never see `support`. Unavailable capabilities never appear.
9. **Omit, never null.** A question referencing an `unknown` field is dropped. A dropped question
   yields **no authorization**, never a default.

---

## §1 MVP catalog

### Core (sent whenever their capability and telemetry conditions hold)

```jsonc
"next_action": {
  "type": "choice",
  "instructions": "Choose what the character should do next. Each option is a complete committed action including what it costs. Judge each against `threat`, `hazards`, `economy.gold_band`, `run.time_band` and `objective.phase`.",
  "criteria": {
    // --- generated from the action registry; see 06-ACTION-REGISTRY.md ---
    "open_chest:entity-47":       { "what": "Open the cheap chest you are standing at. Spends gold, guaranteed item.",
                                    "not_for": "When gold is better saved or the route is unsafe." },
    "use_shrine_chance:entity-51":{ "what": "Gamble at the Shrine of Chance you are standing at. Spends gold, may give nothing.",
                                    "not_for": "Runs that cannot afford to lose the gold." },
    "start_teleporter:entity-03": { "what": "Begin the boss event at the teleporter you are standing at. Irreversible.",
                                    "not_for": "A character too weak for the boss, or reachable loot with time to take it." },
    "search_teleporter":          { "what": "Explore the stage to find the teleporter. Costs only time.",
                                    "not_for": "Stages where the teleporter is already found." },
    "explore":                    { "what": "Move through unexplored parts of the stage looking for anything useful." },
    "none":                       { "what": "Start nothing new right now.",
                                    "not_for": "When a clearly worthwhile action is available." }
    // "continue_current_action" present ONLY when a valid active action exists
  }
},

"engagement_response": {
  "type": "choice",
  "instructions": "How should the character respond to the current situation? `threat.reposition_feasible` and `threat.full_withdrawal_feasible` state which movements are possible; do not choose one that is not feasible. `hazards` describes damage sources that are not engaged enemies.",
  "criteria": {
    "hold_and_fight": { "what": "Stay and finish the fight, or stay put if nothing is threatening.",
                        "not_for": "When the fight is being lost, or a hazard is doing steady damage." },
    "reposition":     { "what": "Move to better ground while staying engaged — break line of sight, gain height, leave a melee cluster or a hazard volume.",
                        "not_for": "When a full withdrawal is needed." },
    "full_disengage": { "what": "Withdraw from the area entirely and recover. Temporarily abandoning an objective is acceptable if staying would be fatal.",
                        "not_for": "When the threat is mild enough to handle by repositioning." }
  }
},

"strategy": {
  "type": "choice",
  "instructions": "Choose the combat posture that fits `threat`, `self.hp_band` and `build.identity`.",
  "criteria": {
    "aggressive": { "what": "Close distance and maximise damage.", "not_for": "Low health or heavy incoming fire." },
    "defensive":  { "what": "Prioritise survival, keep distance, use mobility.", "not_for": "Fights that must end quickly." },
    "balanced":   { "what": "Trade damage and safety evenly.", "not_for": "Clear emergencies in either direction." }
  }
},

"engage_range": {
  "type": "choice",
  "instructions": "At what distance should the character fight, given `run.survivor`, `build.identity` and `threat.enemy_classes`?",
  "criteria": {
    "melee": { "what": "Directly adjacent. For melee survivors and short-range builds." },
    "close": { "what": "Just outside melee reach, staying mobile." },
    "mid":   { "what": "Comfortable weapon range, kiting between shots." },
    "far":   { "what": "Maximum effective range, avoiding melee contact." }
  }
},

"situation_is_novel": {
  "type": "noul",
  "instructions": "Is the character in a situation that the available actions do not describe well?",
  "criteria": {
    "true":  "Something unusual is happening that the available actions do not cleanly address.",
    "false": "An ordinary situation, well covered by the available actions."
  }
}
```

### Conditionally sent

```jsonc
// Only when a valid non-terminal active action exists.
"continue_is_worthwhile": {
  "type": "noul",
  "instructions": "Progress on `control.active_action` is `control.progress_band`, with `control.consecutive_failures` recent failures. Is continuing still worth the time it is taking?",
  "criteria": {
    "true":  "The action remains valuable and is worth more attempts.",
    "false": "The time cost now outweighs the reward."
  }
},

// Only when the equipment capability contract is fully satisfied (see §3).
"use_equipment_now": {
  "type": "noul",
  "instructions": "Should the character trigger `self.equipment.name` right now? Judge whether this moment justifies spending a limited-cooldown item of that kind.",
  "criteria": {
    "true":  "This moment justifies spending this particular equipment.",
    "false": "Hold it. A clearly better moment is close, or the situation does not warrant it."
  }
},

// One per irreversible/costly action in the registry that declares requires_worth_gate.
"worth_use_shrine_chance:entity-51": {
  "type": "noul",
  "instructions": "Is gambling at Shrine of Chance `entity-51` worth its gold cost right now, given `economy.gold_band` and how much this run needs items?",
  "criteria": {
    "true":  "The expected item is worth the gold and the risk of nothing.",
    "false": "The gold is better spent on a guaranteed reward."
  }
}
```

### Budget

**Every question is gated** on telemetry, capability, and having a live policy consumer. The
machine-readable `enabled_when` conditions live in `contract/questions.yaml`, which C1/C3
generate from. Revision 3 called five "always sent," which was wrong in three ways at once.

Two different conditions are easy to conflate, so they are named separately:

- **The registry is never empty** — `explore` and `none` need no candidates, so `next_action`
  always has something to offer. This is a property of the *action space*.
- **Telemetry may still be insufficient to ask** — if the fields `next_action`'s instructions
  reference (`threat`, `hazards`, `objective.phase`) are `unknown`, the question is omitted like
  any other, and no task is authorized that tick.

A non-empty registry is a necessary condition for `next_action`, not a sufficient one.

Sizes below are **measured** from the serialized request built in Phase 0
(`python -m eval.measure_catalog`, recorded in `contract/catalog-size.json`). Characters are
exact; token columns are bracketed at 4.0–3.0 chars/token because JEV's tokenizer is
unpublished — see the cost model in `02-ARCHITECTURE.md` for why that bracket is not collapsed
into one number.

| Question | Sent when | Chars | Tokens (est) | r3 est. |
|---|---|---|---|---|
| `next_action` | always (registry is never empty) | 604–2,819 | 151–940 | ~100 + options |
| `engagement_response` | `capability(disengage)` | 899 | 225–300 | ~150 ✗ |
| `strategy` | `capability(set_posture)` | 457 | 114–152 | ~110 ✓ |
| `engage_range` | `capability(set_posture)` | 433 | 108–144 | ~100 ✓ |
| `continue_is_worthwhile` | an active task exists | 326 | 82–109 | ~80 ✓ |
| `use_equipment_now` | `capability(equipment)` **and** all four equipment contract fields | — | — | ~90 |
| `situation_is_novel` | **`counsel_enabled`** | — | — | ~70 |
| `worth_<action_id>` | per irreversible registry record, typically 0-3 | 310–315 | 78–105 each | ~80 ✓ |
| **Milestone-1 tick** (L2 off, no equipment) | **4-5 questions + 0-4 gates** | 2,460–3,207 B catalog | **615–1,069** | ~700-1,000 ✓ |

Two corrections fall out of the measurement:

- The question **count** was wrong, not the budget: a milestone-1 tick sends **4-5**, not 5-8,
  because `use_equipment_now` and `situation_is_novel` are both gated off in that configuration
  and the r3 table counted them.
- `engagement_response` is **twice** its estimate. Its three options carry the longest prose in
  the catalog. Recorded rather than trimmed — at these prices clarity is worth more than 150
  tokens, and the options were rewritten in r3 precisely to be explicit.

`use_equipment_now` and `situation_is_novel` are unmeasured because no configuration in the
measured set enables them; their text exists in `contract/questions.yaml` and they will be
measured when `equipment` (Phase 4) and `counsel` (Phase 6) land.

`situation_is_novel`'s only consumer is L2 escalation, and **L2 is off by default** under
`--jev-only`. Revision 3 sent it unconditionally, so in the default configuration it was a paid
question nothing read — a C1 violation hiding in the flagship config.

---

## §2 Why both Scores were removed

`threat_level` and `time_pressure` appeared in the r2 catalog and in **no branch of the r2
dispatcher** — a direct violation of rule 3.

They also had an unresolved interpretation problem. The r2 fix proposed:

```python
sev = score_label(ans["threat_level"])   # "immune to indexing conventions"
```

That does not work. `legend` maps **integer indices** to descriptions. A returned score of `2.6`
has no index, and the criteria were full sentences, not stable labels like `"Critical"`. Silently
rounding is exactly the kind of arithmetic the catalog forbids.

**If a Score returns later, it needs a documented interpretation rule.** The cleanest is to
consume the categorical distribution directly, never the scalar:

```python
p = ans["threat_level"].probabilities          # per-level, named
if p["Critical"] + p["Lethal"] > 0.50: ...     # explicit, no rounding
```

This requires short stable labels in the criteria, with the descriptive sentence moved into the
level's structured `summary`. Until a consumer and a rule both exist, the questions stay out.

The underlying facts are not lost — `threat.*`, `hazards.*` and `run.time_band` remain in state,
where `next_action` and `engagement_response` read them.

---

## §3 Equipment capability contract

r2 asked "should the character use their equipment" given only a name and readiness. Equipment
activation is not universally compatible with every movement or skill state.

`use_equipment_now` is sent **only when all four hold**:

| Field | Meaning |
|---|---|
| `supported_activation` | This mod build can actually activate this equipment |
| `ready` | Off cooldown, charges available |
| `valid_target_available` | For targeted equipment, a legal target exists |
| `activation_compatible` | Current animation/skill/movement state permits activation |

Any false -> question omitted -> no authorization. **In the MVP, unsupported equipment is gated
out entirely** rather than attempted and failed.

---

## §4 Hazards are not enemies

r2's `threat_level` defined `Safe` as "No engaged enemies. Health may be low; nothing is currently
threatening it." That is false: environmental damage, damage-over-time, and incoming projectiles
threaten health with no engaged enemy.

The observation now carries explicit `hazards` facts — `taking_environmental_damage`,
`active_dots`, `incoming_projectiles`, `in_hazard_volume` — and `engagement_response` reads them
directly.

Relatedly, r2's `full_disengage` was `not_for: "Objectives that must be completed under fire, such
as an active holdout zone."` **An active holdout does not justify dying in it.** The criterion now
permits temporary objective abandonment explicitly.

---

## §5 Feasibility is two facts, not one

r2 used a single `threat.escape_feasible` to gate both `reposition` and `full_disengage`. Those
are different questions — local repositioning can be possible when full withdrawal is not, and
occasionally the reverse.

Split into `threat.reposition_feasible` and `threat.full_withdrawal_feasible`, both computed by
the mod's pathfinder.

---

## §6 Policy layer

### Confidence is concentration, not correctness

Choice `confidence` describes distribution concentration, not whether an action is beneficial.
Noul responses carry **no confidence field** — only `noul`. Thresholds are per question and never
transferred between primitives.

Where a Choice gate and a Noul gate are both required, that is a **policy choice to be measured**,
not a multiplied error bound: the two are evaluated separately but are not statistically
independent, since they read the same state.

### Categorical hysteresis

Track the probability of the **currently held** option from `probabilities`. Switching requires
all three:

| Output | Challenger entry | Held exit | Min dwell |
|---|---|---|---|
| `strategy` | 0.55 | 0.35 | 800 ms |
| `engage_range` | 0.50 | 0.30 | 600 ms |
| `engagement_response` | 0.60 | 0.35 | 400 ms |

Dwell is **wall-clock milliseconds**, since closed-loop pacing means tick rate varies.

**Legality overrides dwell.** If the held option leaves the legal set — its entity vanished, its
capability was withdrawn, its phase passed — it is dropped **immediately**, regardless of dwell or
exit threshold. Dwell must never preserve an illegal action (T16).

Safety preemption bypasses dwell entirely.

### Per-question thresholds

| Question | Primitive | Gate |
|---|---|---|
| `next_action` -> `reversible` | Choice | `p(selected) > 0.40` |
| `next_action` -> `costly` | Choice | `p(selected) > 0.50`, **no worth gate** |
| `next_action` -> `irreversible` | Choice | `p(selected) > 0.55` **+ worth gate** |
| `worth_<action_id>` | Noul | `noul > 0.75` |
| `use_equipment_now` | Noul | `noul > 0.65` |
| `continue_is_worthwhile` | Noul | `noul < 0.35` to abandon |
| `situation_is_novel` | Noul | `noul > 0.70` (escalation only) |

Worth gates apply to **`irreversible` only**. Revision 3's registry and threshold table disagreed
about `costly`; reconciled in favour of the registry, because a chest's value is legible from its
own option description and a Noul per chest would bloat every tick. `06-ACTION-REGISTRY.md` §3 is
the source of truth, mirrored in `contract/questions.yaml` under `commitment_gates`.

**Every number here is an unvalidated starting point**, to be measured in Phase 5.

### Channel arbitration

| Precedence | Channel | Owns movement? |
|---|---|---|
| 1 | Safety reflex (L0) | Yes, exclusively while active |
| 2 | Disengage | Yes |
| 3 | Task execution | Yes |
| 4 | Posture | No — modifies how, not where |
| — | Equipment | No — orthogonal, always evaluated |

---

## §7 Dispatcher

Every r2 defect is corrected here. Channels are processed independently; **nothing returns early.**

```python
def dispatch(obs, answers, lifecycle):
    obs_id = obs.observation_id
    cmds   = []

    # ---- Channel 2: DISENGAGE ------------------------------------------
    movement_owner = None
    resp = choice_with_hysteresis("engagement_response", answers, obs)   # None if omitted
    if resp == "reposition" and obs.threat.reposition_feasible:
        cmds.append(Cmd(DISENGAGE, style="reposition",
                        origin=f"jev:engagement_response:{obs_id}"))
        movement_owner = "disengage"
    elif resp == "full_disengage" and obs.threat.full_withdrawal_feasible:
        cmds.append(Cmd(DISENGAGE, style="withdraw",
                        origin=f"jev:engagement_response:{obs_id}"))
        movement_owner = "disengage"

    # ---- Sentinels: handled BEFORE the occupancy check --------------------
    # `continue_current_action` is offered precisely WHEN a task is active, so
    # processing it inside `if not task_occupied()` made it unreachable.
    sel = answers.get("next_action")
    sentinel = sel["choice"] if sel and sel["choice"] in SENTINELS else None
    if sentinel:
        lifecycle.journal_intent(sentinel, {"observation_id": obs_id})

    # ---- Channel 3: TASK -----------------------------------------------
    cw = answers.get("continue_is_worthwhile")
    wants_abandon = lifecycle.task_occupied() and cw is not None and cw["noul"] < 0.35
    if wants_abandon and sentinel == "continue_current_action":
        # Two independent questions disagree. Cancellation wins, and the
        # arbitration is RECORDED rather than presented as consensus.
        out.notes.append({"arbitration": "cancel_overrides_continue", ...})
    if wants_abandon:
        cmds.append(Cmd(CANCEL, command_id=lifecycle.task.command_id,
                        origin=f"jev:continue_is_worthwhile:{obs_id}"))
        lifecycle.mark_cancel_pending()      # async: active until ANY terminal (T12)

    if movement_owner is None and not lifecycle.task_occupied() and sel and not sentinel:
        record = obs.registry.get(sel["choice"])
        if record is None:
            fail_loudly("action not in registry", sel["choice"])        # (T9)
        elif gates_satisfied(record, answers, sel):                     # fail-closed (T1)
            c = Cmd(record.command,
                    args=record.args,                   # complete typed args
                    action_id=record.action_id,
                    execution_contract=record.execution_contract(),
                    origin=f"jev:next_action:{obs_id}",
                    accept_deadline=obs.t_capture + ACCEPT_WINDOW_MS)
            lifecycle.reserve(c.command_id, record.action_id, record.command)
            cmds.append(c)                              # slot reserved at ENQUEUE
            movement_owner = "task"
        # else: emit nothing. Python never substitutes a target.

    # ---- Channel 4: POSTURE (no movement ownership) ---------------------
    strat = choice_with_hysteresis("strategy", answers, obs)
    rng   = choice_with_hysteresis("engage_range", answers, obs)
    if strat is not None or rng is not None:
        cmds.append(Cmd(SET_POSTURE, strategy=strat, engage_range=rng,
                        origin=f"jev:strategy:{obs_id}"))

    # ---- Equipment: orthogonal, evaluated regardless of disengage (T3) --
    eq = answers.get("use_equipment_now")
    if eq is not None and eq.noul > 0.65:
        cmds.append(Cmd(USE_EQUIPMENT, origin=f"jev:use_equipment_now:{obs_id}"))

    return cmds
```

`lifecycle.task_occupied()` returns true while a task is `accepted`, `navigating`, `committing`,
`suspended`, **or `cancelling`** — cancellation is not complete until the mod acknowledges it.

**`ready_to_commit` is gone.** Revision 3 included it in `task_occupied()` while claiming the
commit would be issued "on a later tick" — but the occupancy check suppressed exactly that
command, so every travel-then-purchase task ended `failed(commit_unauthorized)`. A deadlock, in
code, under prose that said otherwise.

The MVP splits it instead (`05-PROTOCOL.md` §7): `NAVIGATE_TARGET` terminates on arrival, freeing
the slot; the next observation reports `in_interaction_range: true` with a fresh
`opportunity_token`, and the registry then offers `open_chest:entity-47` where it previously
offered `navigate:entity-47`. Authorization freshness is **structural** — to be in range you must
have just arrived — so no commit bookkeeping is needed at all.

### r2 dispatcher defects, corrected

| Defect | Correction |
|---|---|
| `return` after disengage skipped equipment | No early return; channels independent (T3) |
| Omitted questions indexed directly | `.get()` throughout; absence = no authorization (T10) |
| `continue_current_action` always offered | Registry invariant 2 (T11) |
| `task_active()` checked right after `CANCEL` | Cancellation pending until acknowledged (T12) |
| JEV-driven cancel attributed to `watchdog:` | Now `jev:continue_is_worthwhile:` (T13) |
| Missing `worth_` implied no gate | Registry declares the requirement; fail-closed (T1) |
| One escape check for two movements | Split feasibility facts (§5) |

---

## §8 L2 escalation

Invoked when any of:

- `situation_is_novel` > 0.70
- `next_action` selects `none` while legal non-`none` actions existed, **continuously for 6 s**
  (duration, not tick count — pacing is variable)
- `control.consecutive_failures` >= 3
- A stage transition occurs

With: **one counsel request in flight at a time**, a **20 s cooldown** between requests, and
**results bound to the `scene_gen` they were requested under** — advice from the previous stage is
discarded, not applied.

Returns at most 200 tokens into `state.advisor`, expiring after 45 s. It writes no other field —
notably not `build.identity` — and never emits a command.

---

## §9 Deferred beyond MVP

Kept out until they have a consumer and executable commands:

- `build_identity`, `printer_worth_using`, `shop_item_pick` (Group E)
- `pillar_target`, `hold_the_zone` (Commencement)
- `threat_level`, `time_pressure` (Scores — need an interpretation rule per §2)
