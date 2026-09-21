"""The dispatcher: JEV answers -> commands.

Resolves review item 6:
  * Sentinels are journaled and validated BEFORE the occupancy check, because
    `continue_current_action` is offered precisely when a task exists -- revision 4
    processed it inside `if not task_occupied()`, making it unreachable.
  * The slot is reserved on enqueue via `Lifecycle.reserve`.
  * The `continue_current_action` vs `continue_is_worthwhile < 0.35` disagreement is an
    explicit arbitration, recorded as such. Independent questions may disagree; that is
    not answer chaining, it is a policy gap that must be decided rather than hidden.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from .registry import ENTRY, SENTINELS, WORTH_THRESHOLD, ActionRecord

_seq = itertools.count(4400)
_cmd = itertools.count(100)

ABANDON_BELOW = 0.35
EQUIP_ABOVE = 0.65


@dataclass
class Command:
    command_id: str
    command_seq: int
    command: str
    args: dict
    origin: str
    observation_id: str
    action_id: str | None = None
    execution_contract: dict | None = None
    accept_deadline_mono_ms: int | None = None


@dataclass
class Dispatch:
    commands: list[Command] = field(default_factory=list)
    notes: list[dict] = field(default_factory=list)


ACCEPT_WINDOW_MS = 1500


def _gates_satisfied(rec: ActionRecord, answers: dict, sel, notes: list) -> bool:
    p = sel["probabilities"].get(rec.action_id, 0.0)
    if p < ENTRY[rec.commitment]:
        notes.append({"not_authorized": "probability_gate", "action": rec.action_id, "p": p})
        return False
    if rec.requires_worth_gate:
        w = answers.get(rec.worth_question)
        if w is None:
            # Fail closed: the registry declares the requirement, not the answer's presence.
            notes.append({"not_authorized": "gate_missing", "action": rec.action_id})
            return False
        if w["noul"] <= WORTH_THRESHOLD:
            notes.append({"not_authorized": "worth_gate", "action": rec.action_id})
            return False
    return True


def dispatch(obs: dict, answers: dict, registry: dict, lifecycle, capabilities: set) -> Dispatch:
    out = Dispatch()
    obs_id = obs["observation_id"]

    def new_cmd(command, args, origin, **kw):
        c = Command(f"cmd-{next(_cmd)}", next(_seq), command, args, origin, obs_id, **kw)
        return c

    # ---- Sentinel handling: BEFORE the occupancy check (review item 6) ------
    sel = answers.get("next_action")
    sentinel = sel["choice"] if sel and sel["choice"] in SENTINELS else None
    if sentinel:
        lifecycle.journal_intent(sentinel, {"observation_id": obs_id})

    # ---- Arbitration: continue vs abandon ----------------------------------
    cw = answers.get("continue_is_worthwhile")
    wants_abandon = lifecycle.task_occupied() and cw is not None and cw["noul"] < ABANDON_BELOW
    if wants_abandon and sentinel == "continue_current_action":
        # Two independent questions disagree. Cancellation wins -- a task judged not
        # worth continuing is the stronger signal -- and the arbitration is recorded
        # rather than presented as consensus.
        out.notes.append({
            "arbitration": "cancel_overrides_continue",
            "next_action": "continue_current_action",
            "continue_is_worthwhile": cw["noul"],
        })

    if wants_abandon:
        out.commands.append(new_cmd(
            "CANCEL", {"command_id": lifecycle.task.command_id},
            origin=f"jev:continue_is_worthwhile:{obs_id}"))
        lifecycle.mark_cancel_pending()

    # ---- Disengage channel -------------------------------------------------
    movement_owner = None
    er = answers.get("engagement_response")
    if er is not None and "disengage" in capabilities:
        choice = er["choice"]
        feasible = {
            "reposition": obs["threat"].get("reposition_feasible", False),
            "full_disengage": obs["threat"].get("full_withdrawal_feasible", False),
        }
        if choice in feasible and feasible[choice]:
            out.commands.append(new_cmd(
                "DISENGAGE",
                {"from": [e["entity_id"] for e in obs["threat"]["enemies"]],
                 "style": "reposition" if choice == "reposition" else "withdraw"},
                origin=f"jev:engagement_response:{obs_id}"))
            movement_owner = "disengage"

    # ---- Task channel ------------------------------------------------------
    if movement_owner is None and not lifecycle.task_occupied() and sel and not sentinel:
        rec = registry.get(sel["choice"])
        if rec is None:
            raise AssertionError(f"action not in registry: {sel['choice']}")
        if _gates_satisfied(rec, answers, sel, out.notes):
            c = new_cmd(rec.command, dict(rec.args),
                        origin=f"jev:next_action:{obs_id}",
                        action_id=rec.action_id,
                        execution_contract=rec.execution_contract(),
                        accept_deadline_mono_ms=obs["t_capture_mono_ms"] + ACCEPT_WINDOW_MS)
            lifecycle.reserve(c.command_id, rec.action_id, rec.command)
            out.commands.append(c)

    # ---- Posture channel (no movement ownership) ---------------------------
    if "set_posture" in capabilities:
        strat = answers.get("strategy")
        rng = answers.get("engage_range")
        if strat or rng:
            out.commands.append(new_cmd(
                "SET_POSTURE",
                {"strategy": strat and strat["choice"], "engage_range": rng and rng["choice"]},
                origin=f"jev:strategy:{obs_id}"))

    # ---- Equipment: orthogonal, never suppressed by disengage (T3) ---------
    eq = answers.get("use_equipment_now")
    if eq is not None and eq["noul"] > EQUIP_ABOVE and "equipment" in capabilities:
        out.commands.append(new_cmd("USE_EQUIPMENT", {},
                                    origin=f"jev:use_equipment_now:{obs_id}"))

    return out
