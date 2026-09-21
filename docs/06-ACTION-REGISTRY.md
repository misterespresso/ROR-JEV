# Action Registry

**Schema version: 1.** The dispatcher, the mod, and every recorded run must agree on what an
action *is*. A registry change is a breaking change.

Machine-readable form: `contract/actions.yaml`, **generated** from `brain/registry.py`
(`python -m eval.gen_actions`) so the taxonomy and the registry builder cannot drift.
This document explains it.

---

## §1 Why entity IDs are not enough

An entity does not uniquely identify an action. A teleporter supports navigate / start / defend /
advance; a printer supports navigate / use. Those differ in phase, cost, and reversibility.

`action_id` carries **verb + entity** and resolves to an immutable record. Nothing is remapped —
the selection is more specific, not less.

---

## §2 Action record

```jsonc
{
  "action_id":        "use_shrine_chance:entity-51",   // Choice option key
  "verb":             "use_shrine_chance",             // MUST equal the action_id prefix
  "command":          "INTERACT_TARGET",
  "args":             { "target_id": "entity-51" },    // typed, complete
  "expected_phase":   "pre_teleporter",
  "commitment":       "irreversible",                  // reversible | costly | irreversible
  "cost_limit":       { "type": "gold", "amount": 75 },
  "max_uses":         1,
  "opportunity_token":"opp-51-3de8",
  "requires_worth_gate": true,
  "worth_question":   "worth_use_shrine_chance:entity-51",
  "capability":       "interact_target",
  "registry_version": 1
}
```

### Field rules

- **`verb` must equal the `action_id` prefix.** Revision 3 had `action_id: "start_teleporter:..."`
  with `verb: "start_event"`, two vocabularies for one concept. Asserted by C5.
- **`args` is complete and typed.** The dispatcher emits the whole `args` object, not just
  `target_id` — `SEARCH` carries `{for: "teleporter"}` and has no target at all.
- **`cost_limit` / `max_uses` / `opportunity_token`** become the wire `execution_contract`
  (`05-PROTOCOL.md` §5.4). The mod validates against these; JEV never sees the numbers, only bands.
- **`requires_worth_gate` is declared by the registry**, never inferred from whether an answer
  arrived. Missing required gate = **not authorized** (fail closed).
- **`capability`** must be in the session's `HELLO_ACK` capability set, or the record is excluded.

---

## §3 Verb taxonomy

| Verb | Entity | Range-gated | Commitment | Worth gate | Capability | Command |
|---|---|---|---|---|---|---|
| `navigate` | yes | no | reversible | no | `navigate_target` | `NAVIGATE_TARGET` |
| `open_chest` | yes | **yes** | costly | **no** | `interact_target` | `INTERACT_TARGET` |
| `use_shrine_chance` | yes | **yes** | irreversible | **yes** | `interact_target` | `INTERACT_TARGET` |
| `use_shrine_blood` | yes | **yes** | irreversible | **yes** | `interact_target` | `INTERACT_TARGET` |
| `buy_shop_item` | yes | **yes** | irreversible | **yes** | `interact_target` | `INTERACT_TARGET` |
| `use_printer` | yes | **yes** | irreversible | **yes** | `interact_target` | `INTERACT_TARGET` |
| `start_teleporter` | yes | **yes** | irreversible | **yes** | `interact_target` | `INTERACT_TARGET` |
| `advance_stage` | yes | **yes** | irreversible | **yes** | `interact_target` | `INTERACT_TARGET` |
| `defend_zone` | yes | no | reversible | no | `defend_zone` | `DEFEND_ZONE` |
| `fight_boss` | yes | no | reversible | no | `fight_target` | `FIGHT_TARGET` |
| `search_teleporter` | no | no | reversible | no | **`search`** | `SEARCH` |
| `explore` | no | no | reversible | no | **`search`** | `SEARCH` |
| `continue_current_action` | no | — | — | no | **sentinel** | *none* |
| `none` | no | — | — | no | **sentinel** | *none* |

### Worth gates apply to `irreversible` only

Revision 3's registry marked `open_chest` as needing no worth gate while the catalog's threshold
table required one for everything `costly`. Reconciled in favour of the registry:

- **`reversible`** — probability gate only, low threshold.
- **`costly`** — probability gate only, higher threshold. A chest's value is legible from its own
  option description, and a Noul per chest would bloat every tick.
- **`irreversible`** — probability gate **and** a worth Noul. These are the decisions that cannot
  be walked back.

`03-QUESTION-SET.md` §6 is updated to match.

### Range gating replaces the commit lifecycle

**Range-gated verbs are offered only when `in_interaction_range` is true.** Otherwise the registry
offers `navigate:<entity>` for that target instead. This is what makes `ready_to_commit`
unnecessary in the MVP (`05-PROTOCOL.md` §7): no interaction is authorized *before* long-distance
travel.

Range gating alone does not establish freshness — a character can stand beside an interactable
indefinitely. Observation age, the execution deadline, and live range revalidation do that, at
effect time.

### Sentinels

`none` and `continue_current_action` are **local-only**. They never serialize to a command and are
exempt from the capability invariant.

| Sentinel | Meaning |
|---|---|
| `none` | Authorize no new action. Any existing valid execution is **unaffected** and continues. |
| `continue_current_action` | Positively endorse continuing the active task. Also authorizes no new command. |

They differ in recorded intent, not in emitted commands. `continue_current_action` is available
only when a valid active task exists (invariant 2); `none` is always available. Journaling both
distinguishes "JEV had nothing better to do" from "JEV actively wanted this to continue" — which
is exactly the signal Phase 5 needs to tell a stalled agent from a patient one.

---

## §4 Registry invariants

Asserted in code, checked against every recorded run.

1. **Never empty.** `explore`, `none`, and (when undiscovered) `search_teleporter` require no
   candidates. The action question is always sendable — exploration matters most when nothing is
   visible.
2. **`continue_current_action` present iff** a valid non-terminal active task exists.
3. **Every Choice option key has a record** in the same observation's registry.
4. **Every non-sentinel record's `capability` is in the session capability set.** `none` and
   `continue_current_action` are explicitly exempt.
5. **`verb` equals the `action_id` prefix.**
6. **`expected_phase` matches `objective.phase`** at build time; the mod revalidates at effect.
7. **Range-gated verbs appear only when `in_interaction_range`**; otherwise `navigate` appears.
8. **Irreversible records carry `cost_limit`, `max_uses`, `opportunity_token`, and a
   `worth_question`.**
9. Capped at **12 entity actions** plus sentinels. Ranking is deterministic and recorded.

---

## §5 Dispatcher contract

```python
sel = answers.get("next_action")
if sel is None:
    return []                                        # no authorization

if sel.choice in SENTINELS:                          # none | continue_current_action
    journal_intent(sel.choice)
    return []                                        # never serializes to a command

record = obs.registry.get(sel.choice)
if record is None:
    fail_loudly("action not in registry", sel.choice)
    return []

if not gates_satisfied(record, answers, sel):
    return []                                        # emit nothing; never substitute

return [Cmd(record.command,
            args=record.args,                        # complete typed args
            action_id=record.action_id,
            execution_contract=record.execution_contract(),
            origin=f"jev:next_action:{obs.observation_id}",
            accept_deadline=obs.t_capture + ACCEPT_WINDOW_MS)]
```

```python
def gates_satisfied(record, answers, sel):
    if sel.probabilities[record.action_id] < ENTRY[record.commitment]:
        return False
    if record.requires_worth_gate:
        w = answers.get(record.worth_question)
        if w is None or w.noul <= WORTH_THRESHOLD:   # missing -> NOT authorized
            return False
    return True
```

### Combining a Choice gate with a Noul gate

Requiring both is a reasonable belt-and-braces **policy**, to be measured in Phase 5. It is **not**
a quantified error bound: the two questions are evaluated separately but are not statistically
independent — they read the same state, often the same fields.

---

## §6 Worked example: the teleporter across a stage

| Phase | In range? | Registry entries |
|---|---|---|
| `teleporter_undiscovered` | — | `search_teleporter`, `explore`, `none` |
| `pre_teleporter` | no | `navigate:entity-03`, `explore`, chests, `none` |
| `pre_teleporter` | **yes** | `start_teleporter:entity-03` (irreversible, gated, tokened) |
| `charging` | — | `defend_zone:entity-03`, `fight_boss:e-91`, `none` |
| `charged` | no | `navigate:entity-03` |
| `charged` | **yes** | `advance_stage:entity-03` (irreversible, gated) |

The whole Stage 1 lifecycle is executable from one question, and no interaction is ever authorized
from across the map.
