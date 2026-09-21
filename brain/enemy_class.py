"""Band the mod's raw threat facts (fork build 4+) into `threat.*`.

The mod reports bodies, distances and targeting; judging what they mean happens here, where
the tables can be reviewed without a rebuild.

**PROVISIONAL.** No doc defines the class vocabulary; the fixture's `melee_swarm` is the only
example. The vocabulary is chosen for the one question that reads it, `engage_range`: what
matters is whether an enemy closes to contact, shoots, flies, or is big.

* `BODIES` -- internal body name (as `BodyCatalog.GetBodyName` returns it) -> class. Only
  bodies whose internal name and behaviour are well established are listed.
* Any enemy the mod flags `is_boss` is `boss`, whatever its body -- a teleporter boss can be a
  normal body with a boss flag.
* An enemy absent from the table is unclassified. If unclassified enemies outnumber
  classified ones, `enemy_classes` is `unknown` rather than a partial list that reads as
  complete.
* `being_focused` is true when at least `FOCUS_MIN` enemies' AIs target the player. In a
  single-player run nearly every enemy in a fight targets the player, so 1 is not pressure.
"""
from __future__ import annotations

from .catalog import UNKNOWN

CLASSES = ("melee_swarm", "ranged", "flying_melee", "flying_ranged", "heavy_melee",
           "heavy_ranged", "boss")

FOCUS_MIN = 3

BODIES: dict[str, str] = {
    "BeetleBody": "melee_swarm",
    "ImpBody": "melee_swarm",
    "LunarExploderBody": "melee_swarm",       # Lunar Chimera (Exploder)
    "AcidLarvaBody": "melee_swarm",
    "VerminBody": "melee_swarm",              # Blind Vermin
    "LemurianBody": "ranged",
    "HermitCrabBody": "ranged",
    "MiniMushroomBody": "ranged",             # Mini Mushrum
    "MinorConstructBody": "ranged",           # Alpha Construct
    "JellyfishBody": "flying_melee",
    "WispBody": "flying_ranged",
    "GreaterWispBody": "flying_ranged",
    "BellBody": "flying_ranged",              # Brass Contraption
    "VultureBody": "flying_ranged",           # Alloy Vulture
    "RoboBallMiniBody": "flying_ranged",      # Solus Probe
    "LunarWispBody": "flying_ranged",
    "FlyingVerminBody": "flying_ranged",      # Blind Pest
    "BeetleGuardBody": "heavy_melee",
    "BisonBody": "heavy_melee",
    "ParentBody": "heavy_melee",
    "GupBody": "heavy_melee",
    "GolemBody": "heavy_ranged",              # Stone Golem
    "LemurianBruiserBody": "heavy_ranged",    # Elder Lemurian
    "ClayBruiserBody": "heavy_ranged",        # Clay Templar
    "LunarGolemBody": "heavy_ranged",
    "NullifierBody": "heavy_ranged",          # Void Reaver
    # Bosses that appear without the boss flag in some encounters.
    "BeetleQueen2Body": "boss",
    "TitanBody": "boss",
    "VagrantBody": "boss",
    "ClayBossBody": "boss",                   # Clay Dunestrider
    "GravekeeperBody": "boss",
    "MagmaWormBody": "boss",
    "ElectricWormBody": "boss",
    "RoboBallBossBody": "boss",
    "SuperRoboBallBossBody": "boss",
    "ImpBossBody": "boss",
    "GrandParentBody": "boss",
    "ScavBody": "boss",
}


def classify(enemy: dict) -> str | None:
    if enemy.get("is_boss"):
        return "boss"
    return BODIES.get(str(enemy.get("body") or ""))


def band_threat(threat: dict | None) -> dict | None:
    """`elite_present`, `enemy_classes`, `being_focused` from the mod's threat block.

    Returns None when the frame has no usable block (pre-build-4 recordings), so the caller
    keeps its `unknown`s. `enemies_near` and `nearest_enemy_band` still come from stock
    COMBAT_STATUS and are left alone.
    """
    if not isinstance(threat, dict) or not isinstance(threat.get("enemies"), list):
        return None
    try:
        elites = int(threat["elite_count"])
        targeting = int(threat["targeting_player_count"])
    except (KeyError, TypeError, ValueError):
        return None

    classes: list[str] = []
    unclassified = 0
    for e in threat["enemies"]:
        c = classify(e)
        if c is None:
            unclassified += 1
        elif c not in classes:
            classes.append(c)

    classified = len(threat["enemies"]) - unclassified
    return {
        "elite_present": elites > 0,
        "enemy_classes": UNKNOWN if unclassified > classified else sorted(classes),
        "being_focused": targeting >= FOCUS_MIN,
    }
