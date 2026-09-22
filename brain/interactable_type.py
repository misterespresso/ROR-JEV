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
    (r"CategoryChest2?(Damage|Healing|Utility)", "chest_category"),
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
]

_COMPILED = [(re.compile(p), t) for p, t in RULES]

#: Types the registry can act on today, and the registry type each becomes.
REGISTRY_TYPE: dict[str, str] = {
    "chest_small": "chest",
    "chest_large": "chest",
    "chest_legendary": "chest",
    "chest_category": "chest",
    "shrine_chance": "shrine_chance",
    "shrine_blood": "shrine_blood",
}


def classify(prefab: str) -> str | None:
    for rx, t in _COMPILED:
        if rx.match(prefab or ""):
            return t
    return None
