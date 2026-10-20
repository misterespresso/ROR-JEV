"""What each equipment does and when using it can make sense, for `use_equipment_now`.

jev-05: the character picked up a Foreign Fruit and never used it -- the question needs four
facts (03-QUESTION-SET.md section 3) and nothing produced them. The mod (build 10) reports the
equipment and whether it has a charge; this table supplies the rest.

**PROVISIONAL**, like the item and enemy tables. `when` is not a decision -- JEV makes that --
it is when a use has a valid target at all:

  * `enemies`  -- it hits, pulls or debuffs enemies: needs one within 30 m
  * `hurt`     -- it heals: needs health below full
  * `anytime`  -- a buff, summon or utility that works with no target
  * `never`    -- using it is harmful or pointless here (the Fuel Array explodes)

An equipment not in the table is not offered: `supported_activation` is false.
"""
from __future__ import annotations

from .catalog import UNKNOWN

EQUIPMENT: dict[str, tuple[str, str]] = {
    "Fruit": ("Foreign Fruit: heals a large share of health", "hurt"),
    "PassiveHealing": ("Gnarled Woodsprite: sends a healing spirit", "hurt"),
    "CommandMissile": ("Disposable Missile Launcher: fires a volley of missiles at enemies", "enemies"),
    "BFG": ("Preon Accumulator: fires a huge, slow energy blast", "enemies"),
    "Lightning": ("Royal Capacitor: strikes an enemy with heavy lightning", "enemies"),
    "Blackhole": ("Primordial Cube: pulls nearby enemies into one spot", "enemies"),
    "Saw": ("Sawmerang: throws saw blades that cut and bleed enemies", "enemies"),
    "Molotov": ("Molotov (6-Pack): throws fire bombs at enemies", "enemies"),
    "FireBallDash": ("Volcanic Egg: dashes forward as a fireball, burning enemies", "enemies"),
    "DeathProjectile": ("Forgive Me Please: drops a doll that damages nearby enemies", "enemies"),
    "GoldGat": ("The Crowdfunder: a gun that fires gold at enemies, costing gold", "enemies"),
    "Meteor": ("Glowing Meteorite: meteors strike everything nearby, including the character", "enemies"),
    "CritOnUse": ("Ocular HUD: guaranteed critical hits for a few seconds", "enemies"),
    "TeamWarCry": ("Gorag's Opus: allies attack and move faster for a few seconds", "enemies"),
    "LifestealOnHit": ("Super Massive Leech: heals on every hit for a few seconds", "enemies"),
    "GainArmor": ("Jade Elephant: large armor for a few seconds", "enemies"),
    "Jetpack": ("Milky Chrysalis: flight for a few seconds", "anytime"),
    "DroneBackup": ("The Back-up: calls combat drones for a while", "anytime"),
    "Cleanse": ("Blast Shower: removes debuffs and destroys nearby enemy projectiles", "anytime"),
    "Scanner": ("Radar Scanner: reveals every interactable on the stage", "anytime"),
    "Gateway": ("Eccentric Vase: creates a quantum tunnel for fast travel", "anytime"),
    "Recycle": ("Recycler: turns an item on the ground into another of its tier", "never"),
    "QuestVolatileBattery": ("Fuel Array: explodes when health gets low; do not trigger", "never"),
}


def describe(name: str | None) -> str:
    """Human-readable equipment name and effect for a JEV choice."""
    key = str(name or "")
    what = EQUIPMENT.get(key, ("", ""))[0]
    return what or (key if key else "unknown equipment")

#: An equipment that works on enemies needs one this close to have a target.
ENEMY_RANGE_M = 30.0


def band(equipment: dict | None, hp_band: str, nearest_enemy_m: float | None) -> dict | str:
    """The banded `self.equipment` block, or UNKNOWN when the mod did not report it.

    `None` from the mod means "no equipment" -- reported as an empty slot with every contract
    field false, which keeps `use_equipment_now` omitted without calling it unknown.
    """
    if equipment is None:
        return {"name": "none", "supported_activation": False, "ready": False,
                "valid_target_available": False, "activation_compatible": False}
    if not isinstance(equipment, dict):
        return UNKNOWN
    name = str(equipment.get("name") or "")
    what, when = EQUIPMENT.get(name, ("", ""))
    target = {
        "enemies": nearest_enemy_m is not None and nearest_enemy_m <= ENEMY_RANGE_M,
        "hurt": hp_band in ("hurt", "low"),
        "anytime": True,
    }.get(when, False)
    return {
        "name": name,
        "what_it_does": what or "unrecognised equipment",
        "supported_activation": bool(what),
        "ready": bool(equipment.get("ready")),
        "valid_target_available": bool(target),
        "activation_compatible": when not in ("", "never"),
    }
