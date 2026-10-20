"""Action registry: what JEV actually selects.

Range gating is what removes the commit lifecycle -- interaction verbs appear only when
the target is already in interaction range, so no interaction is ever authorized from
across the map. When out of range the registry offers `navigate:<entity>` instead.
"""
from __future__ import annotations

from dataclasses import dataclass, field

SENTINELS = {"none", "continue_current_action"}

#: verb -> (command, commitment, range_gated, worth_gate, capability)
VERBS = {
    "navigate":          ("NAVIGATE_TARGET", "reversible",   False, False, "navigate_target"),
    "open_chest":        ("INTERACT_TARGET", "costly",       True,  False, "interact_target"),
    # jev-10..jev-16: never once used. The worth gate answered 0.26-0.53 against 0.75, every
    # time. A Shrine of Chance is gold for a chance at an item, repeatable, and nothing about
    # the run is one-way afterwards -- that is a chest-shaped gamble, not the class
    # 06-ACTION-REGISTRY.md reserves for decisions that cannot be undone. Reclassified
    # `costly`: the 0.50 probability gate stands, the worth Noul does not apply.
    "use_shrine_chance": ("INTERACT_TARGET", "costly",       True,  False, "interact_target"),
    "use_shrine_blood":  ("INTERACT_TARGET", "irreversible", True,  True,  "interact_target"),
    "start_teleporter":  ("INTERACT_TARGET", "irreversible", True,  True,  "interact_target"),
    "advance_stage":     ("INTERACT_TARGET", "irreversible", True,  True,  "interact_target"),
    "defend_zone":       ("DEFEND_ZONE",     "reversible",   False, False, "defend_zone"),
    "fight_boss":        ("FIGHT_TARGET",    "reversible",   False, False, "fight_target"),
    "search_teleporter": ("SEARCH",          "reversible",   False, False, "search"),
    "explore":           ("SEARCH",          "reversible",   False, False, "search"),
    "fight_nearby":      ("ENGAGE_NEARBY",   "reversible",   False, False, "fight_nearby"),
    # jev-03: visible since build 6, never offerable. INTERACT_ID already opens any of them.
    "open_barrel":            ("INTERACT_TARGET", "reversible", True, False, "interact_target"),
    "open_equipment_barrel":  ("INTERACT_TARGET", "costly",     True, False, "interact_target"),
    "buy_from_terminal":      ("INTERACT_TARGET", "costly",     True, False, "interact_target"),
    "repair_drone":           ("INTERACT_TARGET", "costly",     True, False, "interact_target"),
    # Replacing carried equipment is a real choice. With an empty slot the mod takes it as a
    # reflex, because there is no tradeoff to adjudicate.
    "take_equipment":         ("PICKUP_TARGET",   "costly",     False, False, "equipment"),
}

#: Which verb an interactable type offers. Range gating is applied afterwards, per verb,
#: because not every verb is range-gated: `defend_zone` is a holdout you travel into, so
#: gating the whole table on `in_interaction_range` made it unreachable and left the agent
#: with nothing but `explore` during teleporter charging.
TYPE_TO_VERB = {
    "chest": "open_chest",
    "shrine_chance": "use_shrine_chance",
    "shrine_blood": "use_shrine_blood",
    "teleporter_idle": "start_teleporter",
    "teleporter_charging": "defend_zone",
    "teleporter_charged": "advance_stage",
    "barrel": "open_barrel",
    "equipment_barrel": "open_equipment_barrel",
    "shop_terminal": "buy_from_terminal",
    "drone": "repair_drone",
    "equipment_pickup": "take_equipment",
}

#: A fight is worth offering only when an enemy is this close (jev-03: offered for any enemy
#: within stock's 50 m, fighting lingered on stragglers).
FIGHT_NEARBY_MAX_M = 30.0

#: Enemy classes worth a dedicated `fight_boss` record. Built from `threat.enemies`, not
#: from interactables -- an enemy is not an interactable and never appears in that list.
BOSS_CLASSES = {"boss"}

ENTRY = {"reversible": 0.40, "costly": 0.50, "irreversible": 0.55}
WORTH_THRESHOLD = 0.75

#: Registry invariant 9: at most this many entity actions, plus sentinels. A cap is not
#: only a prompt-quality rule -- measured in Phase 0, 60 interactables cost ~3x a capped
#: tick (docs/08-PHASE0-REPORT.md F1) and `02-ARCHITECTURE.md` section 3 forbids handing
#: JEV an unsorted pile of candidates outright.
MAX_ENTITY_ACTIONS = 12


@dataclass(frozen=True)
class ActionRecord:
    action_id: str
    verb: str
    command: str
    args: dict
    expected_phase: str
    commitment: str
    capability: str
    cost_limit: dict | None = None
    max_uses: int | None = None
    opportunity_token: str | None = None
    requires_worth_gate: bool = False

    @property
    def worth_question(self) -> str:
        return f"worth_{self.action_id}"

    def execution_contract(self) -> dict:
        return {
            "verb": self.verb,
            "target_id": self.args.get("target_id"),
            "expected_phase": self.expected_phase,
            "cost_limit": self.cost_limit,
            "max_uses": self.max_uses,
            "opportunity_token": self.opportunity_token,
        }


def _rank(entry: tuple[str, ActionRecord, dict]) -> tuple:
    """Deterministic ranking for the invariant-9 cap.

    Invariant 9 requires the ranking be deterministic and recorded, not that it be clever.
    In-range actions first because they are actionable this instant, then nearest, then
    entity ID so two equidistant chests always order the same way across ticks -- an
    unstable order would make the recorded registry unusable for replay.
    """
    _aid, rec, it = entry
    return (
        0 if it.get("in_interaction_range") else 1,
        float(it.get("distance", float("inf"))),
        str(it.get("entity_id", "")),
    )


def build_registry(
    obs: dict,
    capabilities: set[str],
    has_active_task: bool,
    dropped: list | None = None,
) -> dict[str, ActionRecord]:
    """Build the per-observation registry. Invariants asserted here, not assumed.

    `dropped` collects the entity actions the invariant-9 cap excluded, so Phase 5 can ask
    what was never offered. A cap with no record of what it cut is indistinguishable from
    telemetry that never saw the target.
    """
    reg: dict[str, ActionRecord] = {}
    phase = obs["objective"]["phase"]
    candidates: list[tuple[str, ActionRecord, dict]] = []

    for it in obs["interactables"]:
        eid = it["entity_id"]
        in_range = it.get("in_interaction_range", False)

        # Resolve the verb FIRST, then decide whether range gating applies to it. Doing it
        # the other way round silently drops every verb that is not range-gated.
        verb = TYPE_TO_VERB.get(it["type"])
        if verb is None:
            continue
        if VERBS[verb][2] and not in_range:     # range-gated but out of range
            # jev-15: walking to something unaffordable ends in declining it on arrival, and
            # those options split the vote away from the ones that can actually be taken.
            if str(it.get("cost_type")) == "gold" and (it.get("cost") or 0) > 0                     and not it.get("affordable", True):
                continue
            verb = "navigate"

        command, commitment, range_gated, worth, cap = VERBS[verb]
        if cap not in capabilities:
            continue  # capability-gated out entirely (T17)
        token = it.get("opportunity_token") if range_gated else None
        if range_gated and token is None:
            continue  # consumed or quarantined opportunity: not offerable
        aid = f"{verb}:{eid}"
        candidates.append((aid, ActionRecord(
            action_id=aid,
            verb=verb,
            command=command,
            args={"target_id": eid},
            expected_phase=phase,
            commitment=commitment,
            capability=cap,
            cost_limit=({"type": it["cost_type"], "amount": it["cost"]} if range_gated else None),
            max_uses=1 if range_gated else None,
            opportunity_token=token,
            requires_worth_gate=worth,
        ), it))

    # Bosses come from the threat list, not the interactable list.
    if "fight_target" in capabilities:
        for en in obs.get("threat", {}).get("enemies", []):
            if en.get("class") not in BOSS_CLASSES:
                continue
            eid = en["entity_id"]
            command, commitment, _rg, worth, cap = VERBS["fight_boss"]
            aid = f"fight_boss:{eid}"
            candidates.append((aid, ActionRecord(
                action_id=aid, verb="fight_boss", command=command,
                args={"target_id": eid}, expected_phase=phase, commitment=commitment,
                capability=cap, requires_worth_gate=worth,
            ), {"entity_id": eid, "distance": en.get("distance"),
                "in_interaction_range": False}))

    candidates.sort(key=_rank)
    # jev-03: four "walk to the small chest (cheap, far)" options split JEV's vote -- summed
    # chest-walk probability reached 0.62-0.95 while no single option cleared the 0.40 gate,
    # 30 times. Walking is offered to the NEAREST of each kind only; options JEV cannot tell
    # apart must not divide its choice. In-range actions are never merged.
    seen_walk: set[str] = set()
    kept = []
    for c in candidates:
        _aid, rec, it = c
        if rec.verb == "navigate":
            kind = str(it.get("kind") or it.get("type"))
            if kind in seen_walk:
                if dropped is not None:
                    dropped.append({"action_id": _aid, "rank": None, "merged_into_kind": kind,
                                    "distance": it.get("distance")})
                continue
            seen_walk.add(kind)
        kept.append(c)
    candidates = kept
    for aid, rec, it in candidates[:MAX_ENTITY_ACTIONS]:
        reg[aid] = rec
    if dropped is not None:
        dropped.extend(
            {"action_id": aid, "rank": i, "distance": it.get("distance"),
             "in_interaction_range": it.get("in_interaction_range", False)}
            for i, (aid, _rec, it) in enumerate(candidates)
            if i >= MAX_ENTITY_ACTIONS
        )

    # Non-entity actions keep the registry non-empty (invariant 1).
    if "search" in capabilities:
        if not obs["objective"].get("teleporter_found", False):
            reg["search_teleporter"] = ActionRecord(
                "search_teleporter", "search_teleporter", "SEARCH",
                {"for": "teleporter"}, phase, "reversible", "search")
        reg["explore"] = ActionRecord(
            "explore", "explore", "SEARCH", {"for": "interactables"},
            phase, "reversible", "search")
    # jev-02: with enemies close and the teleporter judged not worth starting, JEV's only
    # options were walking somewhere -- it chose the teleporter 84 times in 91 while the mod's
    # own combat dragged the character back and forth. Fighting has to be choosable.
    nearest = obs.get("threat", {}).get("nearest_enemy_m")
    if ("fight_nearby" in capabilities and (obs.get("threat", {}).get("enemy_count") or 0) > 0
            and nearest is not None and nearest <= FIGHT_NEARBY_MAX_M):
        reg["fight_nearby"] = ActionRecord(
            "fight_nearby", "fight_nearby", "ENGAGE_NEARBY", {}, phase, "reversible",
            "fight_nearby")

    _assert_invariants(reg, capabilities, has_active_task)
    return reg


def _assert_invariants(reg, capabilities, has_active_task) -> None:
    assert reg, "invariant 1: registry is never empty"
    entity_actions = [a for a in reg if ":" in a]
    assert len(entity_actions) <= MAX_ENTITY_ACTIONS, (
        f"invariant 9: {len(entity_actions)} entity actions exceeds "
        f"{MAX_ENTITY_ACTIONS}")
    for aid, r in reg.items():
        assert r.verb == aid.split(":")[0], f"invariant 5: verb/action_id mismatch on {aid}"
        assert r.capability in capabilities, f"invariant 4: {aid} capability not in session"
        if r.commitment == "irreversible":
            assert r.cost_limit is not None and r.opportunity_token is not None, (
                f"invariant 8: {aid} missing cost_limit/token")


def selectable(reg: dict[str, ActionRecord], has_active_task: bool) -> list[str]:
    """Option keys offered to JEV, including sentinels.

    `continue_current_action` appears iff a valid active task exists (invariant 2);
    sentinels are exempt from the capability invariant (invariant 4).
    """
    keys = sorted(reg.keys())
    keys.append("none")
    if has_active_task:
        keys.append("continue_current_action")
    return keys
