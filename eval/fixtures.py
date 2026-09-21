"""Representative observations and banded states, as data.

Phase 0 records raw telemetry precisely so bands can be recomputed later, so these
fixtures come in pairs: a raw observation in `05-PROTOCOL.md` section 4 shape, and the
banded state in `02-ARCHITECTURE.md` section 3 shape that a tick would send to JEV.

The scenarios walk the Stage 1 lifecycle from `06-ACTION-REGISTRY.md` section 6, because
the catalog's size is dominated by the registry and the registry changes shape at every
step of that walk. Measuring one tick would report one point on a curve.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass

#: Milestone 1: Commando, Drizzle, solo, L2 disabled, no equipment capability.
MILESTONE_1_CAPABILITIES = frozenset({
    "observation_v2", "navigate_target", "interact_target", "search",
    "set_posture", "disengage", "defend_zone",
})

RAW_BASE: dict = {
    "type": "OBSERVATION",
    "session_id": "sess-8c02", "scene_gen": 14, "run_id": "run-221",
    "observation_id": "obs-812", "seq": 4417, "t_capture_mono_ms": 918244,
    "registry_version": 1,
    "run": {"survivor": "Commando", "stage_name": "Distant Roost", "stage_index": 1,
            "difficulty": "Drizzle", "difficulty_coefficient": 1.15, "run_elapsed_s": 214.7},
    "self": {"hp": 104.0, "hp_max": 110.0,
             "hp_history": [[918144, 108.0], [918194, 104.0]],
             "barrier": 0.0, "shield": 0.0,
             "skills": [{"slot": "primary", "ready": True, "cooldown_remaining": 0.0},
                        {"slot": "secondary", "ready": True, "cooldown_remaining": 0.0},
                        {"slot": "utility", "ready": False, "cooldown_remaining": 2.4},
                        {"slot": "special", "ready": True, "cooldown_remaining": 0.0}],
             "equipment": None,
             "move_speed": 7.4, "position": [12.1, 3.0, -44.8]},
    "hazards": {"taking_environmental_damage": False, "active_dots": [],
                "incoming_projectiles": 0, "in_hazard_volume": False},
    "threat": {"enemies": [{"entity_id": "e-91", "body": "Lemurian", "class": "melee_swarm",
                            "is_elite": False, "distance": 18.4,
                            "is_targeting_player": False}],
               "boss_active": False,
               "reposition_feasible": True, "full_withdrawal_feasible": True},
    "interactables": [],
    "objective": {"phase": "pre_teleporter", "teleporter_found": True,
                  "teleporter_charge": 0.0, "pillars": None},
    "inventory": {"gold": 140, "items": [{"name": "Soldier's Syringe", "count": 2}]},
    "control": {"active_command_id": None, "active_state": None,
                "progress": {"distance_delta_5s": 0.0, "state": "advancing"}},
}

BANDED_BASE: dict = {
    "observation_id": "obs-812",
    "seq": 4417,
    "run": {"survivor": "Commando", "stage": "Distant Roost (1)", "difficulty": "Drizzle",
            "difficulty_band": "gentle", "time_band": "plenty"},
    "self": {"hp_band": "healthy", "hp_trend": "steady", "barrier_band": "none",
             "skills_ready": ["primary", "secondary", "special"]},
    "threat": {"enemies_near": "few", "nearest_enemy_band": "mid", "elite_present": False,
               "boss_active": False, "enemy_classes": ["melee_swarm"],
               "being_focused": False, "reposition_feasible": True,
               "full_withdrawal_feasible": True},
    "hazards": {"taking_environmental_damage": False, "active_dots": [],
                "incoming_projectiles": "none", "in_hazard_volume": False},
    "economy": {"gold_band": "can_afford_one", "candidates": []},
    "objective": {"phase": "pre_teleporter", "teleporter_found": True,
                  "teleporter_charge_band": "not_started"},
    "build": {"identity": "undefined", "key_items": []},
    "control": {"active_action": None, "progress_band": "advancing",
                "consecutive_failures": 0, "last_latency_band": "fast"},
    "advisor": None,
}


def _interactable(eid: str, typ: str, cost: int, dist: float, in_range: bool) -> dict:
    return {
        "entity_id": eid, "type": typ, "display_name": typ.replace("_", " ").title(),
        "cost": cost, "cost_type": "gold", "locked": False, "distance": dist,
        "in_interaction_range": in_range,
        "opportunity_token": f"opp-{eid}-{typ}-14-0" if in_range else None,
        "path_reachable": True, "reachability_age_ms": 420,
    }


def _candidate(eid: str, typ: str, cost_band: str, dist_band: str) -> dict:
    return {"id": eid, "type": typ, "cost_band": cost_band,
            "distance_band": dist_band, "reachable": True}


@dataclass(frozen=True)
class Scenario:
    name: str
    note: str
    raw: dict
    banded: dict
    active_task: bool = False


def _mk(name, note, interactables, candidates, *, phase="pre_teleporter",
        tp_found=True, active_task=False, charge_band="not_started") -> Scenario:
    raw = copy.deepcopy(RAW_BASE)
    raw["interactables"] = interactables
    raw["objective"]["phase"] = phase
    raw["objective"]["teleporter_found"] = tp_found
    banded = copy.deepcopy(BANDED_BASE)
    banded["economy"]["candidates"] = candidates
    banded["objective"]["phase"] = phase
    banded["objective"]["teleporter_found"] = tp_found
    banded["objective"]["teleporter_charge_band"] = charge_band
    if active_task:
        raw["control"] = {"active_command_id": "cmd-103", "active_state": "navigating",
                          "progress": {"distance_delta_5s": 22.4, "state": "advancing"}}
        banded["control"] = {"active_action": "NAVIGATE_TARGET:entity-47",
                             "progress_band": "advancing", "consecutive_failures": 0,
                             "last_latency_band": "fast"}
    return Scenario(name, note, raw, banded, active_task)


def scenarios() -> list[Scenario]:
    """The Stage 1 walk, plus the registry floor and ceiling.

    Floor and ceiling matter more than the median: the floor is what an idle tick costs
    and the ceiling is what the cost model must survive.
    """
    chest_far = _interactable("entity-47", "chest", 25, 31.0, False)
    chest_near = _interactable("entity-47", "chest", 25, 3.1, True)
    shrine_near = _interactable("entity-51", "shrine_chance", 75, 2.8, True)
    tp_far = _interactable("entity-03", "teleporter_idle", 0, 64.0, False)
    tp_near = _interactable("entity-03", "teleporter_idle", 0, 4.2, True)
    tp_charged = _interactable("entity-03", "teleporter_charged", 0, 4.2, True)

    c_chest = _candidate("entity-47", "chest", "cheap", "near")
    c_shrine = _candidate("entity-51", "shrine_chance", "expensive", "near")
    c_tp = _candidate("entity-03", "teleporter", "free", "far")

    out = [
        _mk("floor_undiscovered",
            "Teleporter not found, nothing visible. The smallest catalog the agent ever "
            "sends, and the one an idle-throttled tick pays for.",
            [], [], tp_found=False),
        _mk("navigate_only",
            "Three targets, none in range. Every entity action is `navigate`.",
            [chest_far, tp_far], [c_chest, c_tp]),
        _mk("chest_in_range",
            "Standing at a chest: `open_chest` is costly, so a probability gate only.",
            [chest_near, tp_far], [c_chest, c_tp]),
        _mk("shrine_in_range",
            "Standing at a Shrine of Chance: irreversible, so it adds a worth-gate Noul.",
            [shrine_near, chest_far, tp_far], [c_shrine, c_chest, c_tp]),
        _mk("teleporter_in_range",
            "Standing at the idle teleporter: `start_teleporter`, irreversible, gated.",
            [tp_near, chest_far], [c_tp, c_chest]),
        _mk("charged_advance",
            "Teleporter charged and in range: `advance_stage`, irreversible, gated.",
            [tp_charged], [c_tp], phase="charged", charge_band="complete"),
        _mk("navigating_active",
            "A task is running, so `continue_current_action` and "
            "`continue_is_worthwhile` both appear.",
            [chest_far, tp_far], [c_chest, c_tp], active_task=True),
    ]

    # Ceiling: registry invariant 9 caps entity actions at 12. Four irreversible targets
    # in range is the expensive shape -- each one adds its own worth-gate question.
    many_i, many_c = [], []
    for n in range(12):
        eid = f"entity-{60 + n}"
        if n < 4:
            many_i.append(_interactable(eid, "shrine_chance", 75, 2.0 + n, True))
            many_c.append(_candidate(eid, "shrine_chance", "expensive", "near"))
        else:
            many_i.append(_interactable(eid, "chest", 25, 20.0 + n, False))
            many_c.append(_candidate(eid, "chest", "cheap", "mid"))
    out.append(_mk("ceiling_twelve",
                   "Registry at its invariant-9 cap of 12 entity actions, four of them "
                   "irreversible and in range. The worst tick the cost model must survive.",
                   many_i, many_c, active_task=True))
    return out
