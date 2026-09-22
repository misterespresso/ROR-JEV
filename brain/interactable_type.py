"""Name the mod's interactables (fork build 6+) by prefab.

The mod reports every purchasable object and barrel with its prefab name (`Chest1`,
`Drone1Broken`, `ShrineChanceSandy`...) and no judgment. This table turns prefab names into
types. Stage variants share a stem, so rules match on patterns, in order, first match wins.

**PROVISIONAL**, like the item and enemy tables: only prefab names whose meaning is well
established are listed. An unrecognised prefab is unclassified, never guessed.

`REGISTRY_TYPE` says which types the action registry has a verb for today. Everything else
is visible (recorded, counted by replay) but not yet offerable to JEV -- the next step is the
verbs, not more telemetry.
"""
from __future__ import annotations

import re

#: (pattern on the prefab name, type). Anchored at the start; order matters.
RULES: list[tuple[str, str]] = [
    (r"Chest1$", "chest_small"),
    (r"Chest2$", "chest_large"),
    (r"GoldChest$", "chest_legendary"),
    # jev-04: one "chest_category" hid which item a chest guarantees, and one-walk-per-kind
    # then offered only one of three. Each variant is its own kind.
    (r"CategoryChest2Damage", "chest_damage_large"),
    (r"CategoryChest2Healing", "chest_healing_large"),
    (r"CategoryChest2Utility", "chest_utility_large"),
    (r"CategoryChestDamage", "chest_damage"),
    (r"CategoryChestHealing", "chest_healing"),
    (r"CategoryChestUtility", "chest_utility"),
    (r"CasinoChest", "chest_adaptive"),
    (r"EquipmentBarrel", "equipment_barrel"),
    (r"Barrel1", "barrel"),                         # free; gives gold and experience
    (r"LunarChest", "lunar_pod"),
    (r"Lockbox", "lockbox"),                        # Rusted Key's lockbox
    (r"ShrineChance", "shrine_chance"),
    (r"ShrineBlood", "shrine_blood"),
    (r"ShrineCombat", "shrine_combat"),
    (r"ShrineBoss", "shrine_mountain"),
    (r"ShrineHealing", "shrine_woods"),
    (r"ShrineRestack", "shrine_order"),
    (r"ShrineGoldshoresAccess", "shrine_gold"),
    (r"ShrineCleanse", "cleansing_pool"),
    (r"(Drone1|Drone2|MissileDrone|EquipmentDrone|FlameDrone|MegaDrone|EmergencyDrone)Broken",
     "drone"),
    (r"Turret1Broken", "turret"),
    (r"Duplicator(Large|Military|Wild)?$", "printer"),
    (r"MultiShop(Large|Equipment)?Terminal", "shop_terminal"),
    (r"Scrapper", "scrapper"),
    (r"NewtStatue", "newt_altar"),                  # lunar coin; opens the Bazaar portal
]

_COMPILED = [(re.compile(p), t) for p, t in RULES]

#: How a type reads in option text (F36). Types not listed read as their name with spaces.
LABELS: dict[str, str] = {
    "chest_small": "small chest", "chest_large": "large chest",
    "chest_legendary": "legendary chest",
    "chest_damage": "damage chest (always a damage item)",
    "chest_healing": "healing chest (always a healing item)",
    "chest_utility": "utility chest (always a utility item)",
    "chest_damage_large": "large damage chest (always an uncommon damage item)",
    "chest_healing_large": "large healing chest (always an uncommon healing item)",
    "chest_utility_large": "large utility chest (always an uncommon utility item)",
    "chest_adaptive": "adaptive chest", "shrine_chance": "Shrine of Chance",
    "shrine_blood": "Shrine of Blood", "barrel": "barrel", "drone": "broken drone",
    "turret": "broken turret", "printer": "printer", "shop_terminal": "shop terminal",
    "lunar_pod": "lunar pod", "newt_altar": "newt altar",
}


def label(kind: str) -> str:
    return LABELS.get(kind, kind.replace("_", " "))


#: Types the registry can act on today, and the registry type each becomes.
REGISTRY_TYPE: dict[str, str] = {
    "chest_small": "chest",
    "chest_large": "chest",
    "chest_legendary": "chest",
    "chest_damage": "chest", "chest_healing": "chest", "chest_utility": "chest",
    "chest_damage_large": "chest", "chest_healing_large": "chest", "chest_utility_large": "chest",
    "shrine_chance": "shrine_chance",
    "shrine_blood": "shrine_blood",
    # jev-03
    "barrel": "barrel",
    "equipment_barrel": "equipment_barrel",
    "shop_terminal": "shop_terminal",
    "drone": "drone",
    "turret": "drone",
}


#: Scene-placed copies carry Unity's " (1)" numbering (fork-10: `NewtStatue (1)`).
_NUMBERED = re.compile(r"\s*\(\d+\)$")


def classify(prefab: str) -> str | None:
    name = _NUMBERED.sub("", prefab or "")
    for rx, t in _COMPILED:
        if rx.match(name):
            return t
    return None


def shop_item_name(raw: str | None) -> str | None:
    """The item table's name for a shop/printer item. `PickupDef.internalName` is prefixed
    (`ItemIndex.BossDamageBonus`, fork-10); the inventory sends the bare name."""
    if not raw:
        return None
    return raw.split(".", 1)[1] if raw.startswith(("ItemIndex.", "EquipmentIndex.")) else raw
