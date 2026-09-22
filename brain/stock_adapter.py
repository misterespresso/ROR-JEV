"""Adapt stock Rainflayer 1.1.1 telemetry onto the protocol v2 observation shape.

**This is scaffolding, and it is meant to be deleted.** In the real design the *mod* emits
a v2 `OBSERVATION` (`05-PROTOCOL.md` §4) with entity IDs, bands' worth of raw facts, and
hazard/feasibility measurements. None of that exists yet, and writing it needs C#, a .NET
SDK and a fork. Meanwhile there are real recordings sitting on disk that the brain has never
been run against.

So this converts what stock 1.1.1 actually sends into the shape the brain expects, and is
**scrupulous about the difference between converting and inventing**. Every field stock
cannot supply is set to `UNKNOWN`, which makes the questions that read it drop out exactly
as the field contract promises. The point is to find out what the brain does on real
telemetry, not to make the number of sendable questions look better.

Assumptions are declared, not buried -- see `ADAPTER_ASSUMPTIONS`. Two matter most:

* **Entity IDs are synthetic.** Stock sends no identity, and several interactables share one
  display name (F17). IDs here are positional (`chest-0`, `chest-1`, ordered by distance) and
  are **not stable across observations**: the same chest can change ID as the character moves.
  That is a faithful representation of the problem, not a fix for it. Anything that depends
  on identity across ticks -- opportunity tokens, idempotency, `continue_current_action` --
  cannot be validated through this adapter.
* **Interaction range is a guess.** Stock exposes no `in_interaction_range`. A distance
  threshold stands in for it; the real value belongs to the mod.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .build_identity import derive as derive_identity
from .catalog import UNKNOWN
from .enemy_class import band_threat
from .interactable_type import REGISTRY_TYPE, classify as classify_prefab

#: Every place this adapter is making something up rather than reading it. Kept as data so
#: a replay report can print it, because a reader who does not know these is being misled.
ADAPTER_ASSUMPTIONS = [
    "entity IDs are synthetic and positional; stock sends no identity (F17)",
    "in_interaction_range is derived from a distance threshold; stock has no such field",
    "shrine subtype comes from the display name; the mod's bare `Shrine` fallback maps "
    "to nothing rather than being guessed at (F21)",
    "cost fields are trusted as sent, though shrine cost=1 looks like a placeholder",
    "hp, survivor, elapsed time and hazards come from QUERY_OBSERVATION (fork build 2+) "
    "when the recording has it, and are unknown otherwise; feasibility and nav progress "
    "exist nowhere (F18)",
    "enemy classes and being_focused are banded from build 4's threat block by a provisional "
    "table (brain/enemy_class.py); raw threat.enemies stays empty -- the mod sends no "
    "entity IDs, so fight_boss cannot target them (F17)",
    "build 6+: interactables come from the mod's full-stage list with stable instance IDs; "
    "types come from a provisional prefab table (brain/interactable_type.py); `locked` is not "
    "reported there and is assumed false",
    "build.identity is derived from items by a provisional table (brain/build_identity.py)",
    "time_band and hp_band thresholds are provisional (TIME_BANDS_MIN, HP_BANDS); no doc "
    "defines them",
]

#: Metres within which an interaction is assumed possible. RoR2's real interaction range is
#: a few metres; the mod should measure it rather than have the brain assume it.
INTERACTION_RANGE_M = 6.0

#: `COMBAT_STATUS.nearest_enemy_distance` when no enemy exists (F17). Never a distance.
NO_ENEMY_SENTINEL = 999.0

#: `usesRemaining` when the concept does not apply. Another sentinel, integer this time.
USES_NOT_APPLICABLE = -1

#: Hidden items the game inserts that no player picked up (F13). Excluded before anything
#: derives a build identity from the inventory.
HIDDEN_ITEMS = {"DrizzlePlayerHelper", "MonsoonPlayerHelper"}

#: `Run.selectedDifficulty.ToString()` is the DifficultyIndex enum name, not the display name.
DIFFICULTY_NAMES = {"Easy": "Drizzle", "Normal": "Rainstorm", "Hard": "Monsoon"}

#: PROVISIONAL. Run minutes at which `run.time_band` steps up. RoR2's difficulty climbs with
#: run time, so time spent is the pressure `next_action` weighs; the cut points are a first
#: guess, not a measurement. Upper bound exclusive; the last band is open-ended.
TIME_BANDS_MIN = [(10.0, "plenty"), (25.0, "moderate"), (float("inf"), "pressing")]

#: PROVISIONAL. Fraction of combined health (hp + shield over their maxima) at or above which
#: each band applies. `low` starts at 0.30, where the mod's own low-health logic starts
#: (AIController.cs), so the brain and the mod agree on what "low" means.
HP_BANDS = [(0.70, "healthy"), (0.30, "hurt"), (0.0, "low")]


def _time_band(elapsed_s) -> str:
    e = num(elapsed_s)
    if e is None or e < 0:
        return UNKNOWN
    for upper, band in TIME_BANDS_MIN:
        if e / 60.0 < upper:
            return band
    return UNKNOWN


def _hp_band(slf: dict) -> str:
    if slf.get("alive") is False:
        return "dead"
    hp, hp_max = num(slf.get("hp")), num(slf.get("hp_max"))
    if hp is None or not hp_max:
        return UNKNOWN
    # hp goes negative on the killing blow (fork-06: -31.2); clamp so it cannot band oddly.
    frac = (max(hp, 0) + max(num(slf.get("shield"), 0), 0)) / (
        hp_max + max(num(slf.get("shield_max"), 0), 0))
    for floor, band in HP_BANDS:
        if frac >= floor:
            return band
    return UNKNOWN


def _projectiles_band(n) -> str:
    n = num(n)
    if n is None:
        return UNKNOWN
    return "none" if n <= 0 else "few" if n <= 2 else "many"


def from_observation(obs: dict | None) -> dict | None:
    """Band one build-2 `OBSERVATION` frame into `run`, `self` and `hazards` parts.

    Returns None when there is nothing to use (no frame, or `in_run: false`), so the caller
    keeps its `unknown`s. Field names say what the mod measures, not what the protocol
    doc hoped for: the mod reports void fog only, so the fact is `in_void_fog`, not a
    general `in_hazard_volume` that would read `false` while standing in lava; and it counts
    hostile projectiles within a radius, not ones known to be incoming.
    """
    if not obs or obs.get("type") != "OBSERVATION" or not obs.get("in_run"):
        return None
    run, slf, hz = obs.get("run") or {}, obs.get("self") or {}, obs.get("hazards") or {}

    survivor = str(run.get("survivor") or "")
    survivor = survivor[:-4] if survivor.endswith("Body") else survivor
    diff = run.get("difficulty")
    barrier = num(slf.get("barrier"))
    covers = hz.get("in_hazard_volume_covers")

    hazards = {
        "taking_environmental_damage": bool(hz["taking_environmental_damage"])
        if "taking_environmental_damage" in hz else UNKNOWN,
        "active_dots": list(hz["active_dots"]) if isinstance(hz.get("active_dots"), list)
        else UNKNOWN,
        "projectiles_near": _projectiles_band(hz.get("hostile_projectiles_nearby")),
        # Only claim what the mod says it covers.
        "in_void_fog": bool(hz.get("in_hazard_volume")) if covers == "void_fog" else UNKNOWN,
    }
    return {
        "run": {"survivor": survivor or UNKNOWN,
                "difficulty": DIFFICULTY_NAMES.get(str(diff), str(diff)) if diff else UNKNOWN,
                "time_band": _time_band(run.get("elapsed_s"))},
        "self": {"hp_band": _hp_band(slf),
                 "barrier_band": UNKNOWN if barrier is None
                 else ("none" if barrier <= 0 else "some")},
        "hazards": hazards,
        # Build 4+: elite, class and focus facts. None on older frames -> stay unknown.
        "threat": band_threat(obs.get("threat")),
    }


#: The mod's CostTypeIndex names -> the registry's cost types.
COST_TYPES = {"Money": "gold", "LunarCoin": "lunar_coin", "PercentHealth": "health",
              "None": "none"}


#: stock `objective` -> our `objective.phase`
PHASE_MAP = {
    "exploring": "pre_teleporter",
    "charging_teleporter": "charging",
    "teleporter_charged": "charged",
}


def num(v, default=None):
    """Coerce a possibly-string number (F11/F14/F16). Never compare stock numerics raw."""
    if v is None:
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return int(f) if f == int(f) else f


def classify_teleporter(it: dict) -> str:
    """Stock sends one `teleporter` type; the registry needs three (F19).

    Confirmed against `recordings/human-01`, which produced all five combinations of
    (`charged`, `bossActive`, `chargePercent`) including `charged:true, bossActive:true` --
    so `charged` must be tested FIRST and wins outright.
    """
    if it.get("charged"):
        return "teleporter_charged"
    if it.get("bossActive") or (num(it.get("chargePercent"), 0) or 0) > 0:
        return "teleporter_charging"
    return "teleporter_idle"


def classify(it: dict) -> str | None:
    """Map a stock interactable onto our type vocabulary, or None if it cannot be mapped."""
    t = str(it.get("type"))
    if t == "chest":
        return "chest"
    if t == "teleporter":
        return classify_teleporter(it)
    if t == "shrine":
        return classify_shrine(it)
    return None        # shop, drone, printer, portal, misc: deferred or unmapped


#: Display names the mod produces for shrines, mapped onto the two our registry has verbs
#: for. All seven names exist in the installed 1.1.1 assembly, so subtype IS recoverable --
#: see F21, which originally concluded the opposite from a single unclassified sighting.
#:
#: The other five are real shrines with no verb in the MVP registry, so they map to nothing.
#: That is "deferred", not "unknown", and the distinction matters: a Shrine of the Mountain
#: is perfectly identifiable and simply has no action defined for it yet.
SHRINE_NAME_TO_TYPE = {
    "Shrine of Chance": "shrine_chance",
    "Shrine of Blood": "shrine_blood",
}

#: Identified shrines the MVP has no verb for. Listed explicitly so they are distinguishable
#: from the unclassified fallback below.
SHRINE_DEFERRED = {
    "Shrine of Combat", "Shrine of the Mountain", "Shrine of Gold",
    "Shrine of Rest", "Shrine of Order",
}


def classify_shrine(it: dict) -> str | None:
    """Map a shrine by display name, failing closed on the mod's unclassified fallback.

    `EntityDetector.CreateShrineInfo` matches lowercased GameObject names against seven
    substrings and falls back to the bare string `"Shrine"`. That fallback fired for a real
    shrine in `recordings/human-01`, so unclassified shrines genuinely occur -- and a bare
    `"Shrine"` could be any kind, including a Shrine of Blood that costs health.

    Guessing there would authorize an irreversible action against an unknown cost type, so
    the fallback maps to nothing. The mod's substring matching is also order-dependent and
    collision-prone (a name containing an earlier key wins), which is a second reason not to
    trust a name this adapter cannot recognise.
    """
    name = str(it.get("name") or "").strip()
    if name in SHRINE_NAME_TO_TYPE:
        return SHRINE_NAME_TO_TYPE[name]
    return None


@dataclass
class Adapted:
    raw: dict = field(default_factory=dict)      # v2-shaped OBSERVATION, best effort
    banded: dict = field(default_factory=dict)   # what would go to JEV
    unmapped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: Build 6+: classified interactables the registry has no verb for yet (barrel, drone...).
    visible_no_verb: list[str] = field(default_factory=list)


def _gold_band(gold: int | None) -> str:
    # Banding needs a reference price to be meaningful. Without one this is the honest
    # minimum: whether there is any money at all.
    if gold is None:
        return UNKNOWN
    if gold <= 0:
        return "broke"
    return "has_gold"


def _enemies_band(count: int | None) -> str:
    if count is None:
        return UNKNOWN
    return {0: "none", 1: "one"}.get(count, "few" if count <= 3 else "several")


def _nearest_band(d: float | None) -> str:
    if d is None or d == NO_ENEMY_SENTINEL:
        # F17: 999.0 means "no enemy". Banding it as a distance would call it "far", which
        # is a fabricated measurement -- worse than a missing one.
        return "none"
    if d < 10:
        return "close"
    if d < 30:
        return "mid"
    return "far"


def adapt(objective: dict, inventory: dict, combat: dict, interactables: dict,
          *, observation_id: str, seq: int,
          range_m: float = INTERACTION_RANGE_M,
          observation: dict | None = None) -> Adapted:
    """Build one observation from a matched set of stock query responses.

    The caller is responsible for the responses describing roughly the same instant. They
    cannot describe exactly the same instant -- stock answers six separate queries, which is
    the tearing problem `02-ARCHITECTURE.md` §1 replaced with a single snapshot. This adapter
    cannot fix that either; it is another reason the C# work is the critical path.
    """
    out = Adapted()

    stock_phase = str(objective.get("objective", "")) if objective else ""
    phase = PHASE_MAP.get(stock_phase, UNKNOWN)

    items = [i for i in (inventory.get("items") or [])
             if str(i.get("name")) not in HIDDEN_ITEMS] if inventory else []
    gold = num(inventory.get("money")) if inventory else None

    ilist, cands = [], []
    tp_found = False
    for idx, it in enumerate(sorted(interactables.get("interactables") or [],
                                    key=lambda x: num(x.get("distance"), 1e9))):
        mapped = classify(it)
        if mapped is None:
            out.unmapped.append(str(it.get("type")))
            continue
        if mapped.startswith("teleporter"):
            tp_found = True
        dist = num(it.get("distance"), 1e9)
        eid = f"{mapped.split('_')[0]}-{idx}"       # synthetic, positional, NOT stable
        in_range = dist is not None and dist <= range_m
        ilist.append({
            "entity_id": eid, "type": mapped, "display_name": it.get("name"),
            "cost": num(it.get("cost"), 0), "cost_type": "gold",
            "locked": bool(it.get("locked")), "distance": dist,
            "in_interaction_range": in_range,
            # A token for an entity whose ID is not stable is not a real token. Supplied so
            # the registry can build; it proves nothing about at-most-once effects.
            "opportunity_token": f"synthetic-{eid}" if in_range else None,
            "path_reachable": True, "reachability_age_ms": UNKNOWN,
        })
        cands.append({"id": eid, "type": mapped,
                      "cost_band": "cheap" if (num(it.get("cost"), 0) or 0) <= 25
                      else "expensive",
                      "distance_band": _nearest_band(dist), "reachable": True})

    # Build 6+: the mod's full-stage list replaces stock's camera cone (F12) for everything but
    # the teleporter, which it does not include. IDs are the game's instance IDs: stable.
    block = (observation or {}).get("interactables") if (observation or {}).get("in_run") else None
    if isinstance(block, dict) and isinstance(block.get("items"), list):
        keep = [i for i, it in enumerate(ilist) if it["type"].startswith("teleporter")]
        ilist = [ilist[i] for i in keep]
        cands = [cands[i] for i in keep]
        out.unmapped = []
        for it in block["items"]:
            t = classify_prefab(str(it.get("prefab") or ""))
            if t is None:
                out.unmapped.append(f"prefab:{it.get('prefab')}")
                continue
            rtype = REGISTRY_TYPE.get(t)
            if rtype is None:
                out.visible_no_verb.append(t)
                continue
            dist = num(it.get("distance"), 1e9)
            eid = f"{t}-{it.get('id')}"
            in_range = dist is not None and dist <= range_m
            cost = num(it.get("cost"), 0)
            ilist.append({
                "entity_id": eid, "type": rtype, "display_name": it.get("display"),
                "cost": cost, "cost_type": COST_TYPES.get(str(it.get("cost_type")),
                                                        str(it.get("cost_type")).lower()),
                "locked": False, "distance": dist, "in_interaction_range": in_range,
                # Stable ID, so the token names one object -- but the mod still issues none.
                "opportunity_token": f"synthetic-{eid}" if in_range else None,
                "path_reachable": True, "reachability_age_ms": UNKNOWN,
            })
            cands.append({"id": eid, "type": rtype,
                          "cost_band": "cheap" if (cost or 0) <= 25 else "expensive",
                          "distance_band": _nearest_band(dist), "reachable": True})

    out.raw = {
        "type": "OBSERVATION", "observation_id": observation_id, "seq": seq,
        "objective": {"phase": phase,
                      "teleporter_found": tp_found,
                      "teleporter_charge": num(objective.get("teleporter_charge"), 0)
                      if objective else UNKNOWN},
        "interactables": ilist,
        "threat": {"enemies": [], "boss_active": bool(objective.get("boss_active"))
                   if objective else False,
                   # Not measured by stock, and not guessable. The pathfinder owns these.
                   "reposition_feasible": UNKNOWN,
                   "full_withdrawal_feasible": UNKNOWN},
        "inventory": {"gold": gold, "items": items},
    }

    out.banded = {
        "observation_id": observation_id,
        "seq": seq,
        # `run.survivor` and `run.time_band` have no stock source at all (F18).
        "run": {"survivor": UNKNOWN, "stage": objective.get("scene_name", UNKNOWN)
                if objective else UNKNOWN,
                "difficulty": UNKNOWN, "difficulty_band": UNKNOWN,
                "time_band": UNKNOWN},
        "self": {"hp_band": UNKNOWN, "hp_trend": UNKNOWN, "barrier_band": UNKNOWN,
                 "skills_ready": UNKNOWN},
        "threat": {"enemies_near": _enemies_band(num(combat.get("enemy_count"))
                                                if combat else None),
                   "nearest_enemy_band": _nearest_band(
                       num(combat.get("nearest_enemy_distance")) if combat else None),
                   "elite_present": UNKNOWN, "enemy_classes": UNKNOWN,
                   "being_focused": UNKNOWN,
                   "boss_active": bool(objective.get("boss_active")) if objective else False,
                   "reposition_feasible": UNKNOWN,
                   "full_withdrawal_feasible": UNKNOWN},
        "hazards": UNKNOWN,
        "economy": {"gold_band": _gold_band(gold), "candidates": cands},
        "objective": {"phase": phase, "teleporter_found": tp_found,
                      "teleporter_charge_band": (
                          "complete" if stock_phase == "teleporter_charged"
                          else "charging" if stock_phase == "charging_teleporter"
                          else "not_started")},
        "build": {"identity": derive_identity(items) if inventory else UNKNOWN,
                  "key_items": [str(i.get("name")) for i in items]},
        "control": {"active_action": None, "progress_band": UNKNOWN,
                    "consecutive_failures": 0, "last_latency_band": UNKNOWN},
        "advisor": None,
    }

    # Build 2+: QUERY_OBSERVATION fills what stock cannot. Anything it does not measure stays
    # `unknown` (hp_trend needs history; difficulty_band has no defined cut points).
    ob = from_observation(observation)
    if ob:
        out.banded["run"].update(ob["run"])
        out.banded["self"].update(ob["self"])
        out.banded["hazards"] = ob["hazards"]
        if ob["threat"]:
            out.banded["threat"].update(ob["threat"])
        out.raw.update({k: observation[k] for k in ("run", "self", "hazards")
                        if k in observation})
    return out
