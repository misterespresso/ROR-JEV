"""Derive `build.identity` from the inventory, deterministically.

`02-ARCHITECTURE.md` allows exactly two producers for `build.identity`: a JEV question or a
deterministic feature -- never L2. This is the deterministic feature. It exists because
`strategy`, `engage_range` and every worth gate declare the field, and nothing produced it.

**PROVISIONAL.** No doc defines the vocabulary or the cut points. Everything below that is a
judgment is a named constant or a table row, so it can be reviewed line by line:

* `ITEMS` -- internal item name (as stock `INVENTORY` sends it) -> (tier, tag). Only items
  whose internal name and role are well established are listed. An item absent from the table
  is *unclassified*, never guessed.
* `TIER_WEIGHT` -- a red item defines a build more than a white one.
* Identity is the tag with the largest weighted share, if that share is at least
  `DOMINANT_SHARE`; otherwise `mixed`. Below `MIN_WEIGHT` of classified items the build is
  `undefined` (the fixture's word for an early build with nothing that defines it).
* If unclassified items outnumber classified ones, the answer is `unknown`: the table does not
  know enough about this inventory to name it.
"""
from __future__ import annotations

from collections import Counter

from .catalog import UNKNOWN

W, G, R, B, L = "white", "green", "red", "boss", "lunar"

TIER_WEIGHT = {W: 1.0, G: 2.0, R: 4.0, B: 2.0, L: 2.0}

#: Tags that vote on identity, and the identity each one votes for. Attack speed folds into
#: damage and healing into survival: the posture questions do not distinguish them.
VOTES = {"damage": "damage", "attack_speed": "damage", "crit": "crit", "proc": "proc",
         "defense": "survival", "healing": "survival", "mobility": "mobility"}

#: Tags that are real, classified items but say nothing about how the build fights.
NON_VOTING = {"utility", "scrap"}

IDENTITIES = ("undefined", "mixed", "damage", "crit", "proc", "survival", "mobility")

MIN_WEIGHT = 4.0
DOMINANT_SHARE = 0.40

ITEMS: dict[str, tuple[str, str]] = {
    # --- white ------------------------------------------------------------------
    "Syringe": (W, "attack_speed"),            # Soldier's Syringe
    "Crowbar": (W, "damage"),
    "NearbyDamageBonus": (W, "damage"),        # Focus Crystal
    "BossDamageBonus": (W, "damage"),          # Armor-Piercing Rounds
    "FragileDamageBonus": (W, "damage"),       # Delicate Watch
    "CritGlasses": (W, "crit"),                # Lens-Maker's Glasses
    "BleedOnHit": (W, "proc"),                 # Tri-Tip Dagger
    "StickyBomb": (W, "proc"),
    "IgniteOnKill": (W, "proc"),               # Gasoline
    "Firework": (W, "proc"),                   # Bundle of Fireworks
    "Tooth": (W, "healing"),                   # Monster Tooth
    "Mushroom": (W, "healing"),                # Bustling Fungus
    "HealWhileSafe": (W, "healing"),           # Cautious Slug
    "Medkit": (W, "healing"),
    "Bear": (W, "defense"),                    # Tougher Times
    "PersonalShield": (W, "defense"),          # Personal Shield Generator
    "FlatHealth": (W, "defense"),              # Bison Steak
    "ArmorPlate": (W, "defense"),              # Repulsion Armor Plate
    "BarrierOnKill": (W, "defense"),           # Topaz Brooch
    "OutOfCombatArmor": (W, "defense"),        # Oddly-shaped Opal
    "Hoof": (W, "mobility"),                   # Paul's Goat Hoof
    "SprintBonus": (W, "mobility"),            # Energy Drink
    "SprintOutOfCombat": (W, "mobility"),      # Red Whip
    "StunChanceOnHit": (W, "utility"),         # Stun Grenade
    "TreasureCache": (W, "utility"),           # Rusted Key
    "SecondarySkillMagazine": (W, "utility"),  # Backup Magazine
    "WardOnLevel": (W, "utility"),             # Warbanner
    "GoldOnHurt": (W, "utility"),              # Roll of Pennies
    "ScrapWhite": (W, "scrap"),
    # --- green ------------------------------------------------------------------
    "ChainLightning": (G, "proc"),             # Ukulele
    "Missile": (G, "proc"),                    # AtG Missile Mk. 1
    "Thorns": (G, "proc"),                     # Razorwire
    "ExplodeOnDeath": (G, "proc"),             # Will-o'-the-wisp
    "IceRing": (G, "proc"),                    # Runald's Band
    "FireRing": (G, "proc"),                   # Kjaro's Band
    "DeathMark": (G, "damage"),
    "ExecuteLowHealthElite": (G, "damage"),    # Old Guillotine
    "AttackSpeedOnCrit": (G, "crit"),          # Predatory Instincts
    "HealOnCrit": (G, "crit"),                 # Harvester's Scythe
    "WarCryOnMultiKill": (G, "attack_speed"),  # Berzerker's Pauldron
    "Seed": (G, "healing"),                    # Leeching Seed
    "TPHealingNova": (G, "healing"),           # Lepton Daisy
    "Infusion": (G, "defense"),
    "Phasing": (G, "defense"),                 # Old War Stealthkit
    "SprintArmor": (G, "defense"),             # Rose Buckler
    "Feather": (G, "mobility"),                # Hopoo Feather
    "JumpBoost": (G, "mobility"),              # Wax Quail
    "Bandolier": (G, "utility"),
    "BonusGoldPackOnKill": (G, "utility"),     # Ghor's Tome
    "EquipmentMagazine": (G, "utility"),       # Fuel Cell
    "SlowOnHit": (G, "utility"),               # Chronobauble
    "Squid": (G, "utility"),                   # Squid Polyp
    "EnergizedOnEquipmentUse": (G, "utility"), # War Horn
    "ScrapGreen": (G, "scrap"),
    # --- red --------------------------------------------------------------------
    "Behemoth": (R, "proc"),                   # Brilliant Behemoth
    "Dagger": (R, "proc"),                     # Ceremonial Dagger
    "Icicle": (R, "proc"),                     # Frost Relic
    "GhostOnKill": (R, "proc"),                # Happiest Mask
    "ShockNearby": (R, "proc"),                # Unstable Tesla Coil
    "Clover": (R, "proc"),                     # 57 Leaf Clover
    "BounceNearby": (R, "proc"),               # Sentient Meat Hook
    "LaserTurbine": (R, "proc"),               # Resonance Disc
    "MoreMissile": (R, "proc"),                # Pocket I.C.B.M.
    "HeadHunter": (R, "damage"),               # Wake of Vultures
    "ArmorReductionOnHit": (R, "damage"),      # Shattering Justice
    "CritDamage": (R, "crit"),                 # Laser Scope
    "NovaOnHeal": (R, "healing"),              # N'kuhana's Opinion
    "IncreaseHealing": (R, "healing"),         # Rejuvenation Rack
    "Plant": (R, "healing"),                   # Interstellar Desk Plant
    "ExtraLife": (R, "defense"),               # Dio's Best Friend
    "BarrierOnOverHeal": (R, "defense"),       # Aegis
    "ImmuneToDebuff": (R, "defense"),          # Ben's Raincoat
    "FallBoots": (R, "mobility"),              # H3AD-5T v2
    "UtilitySkillMagazine": (R, "mobility"),   # Hardlight Afterburner
    "AlienHead": (R, "utility"),
    "Talisman": (R, "utility"),                # Soulbound Catalyst
    "KillEliteFrenzy": (R, "utility"),         # Brainstalks
    "ScrapRed": (R, "scrap"),
    # --- boss -------------------------------------------------------------------
    "Knurl": (B, "defense"),                   # Titanic Knurl
    "Pearl": (B, "defense"),
    "NovaOnLowHealth": (B, "defense"),         # Genesis Loop
    "ParentEgg": (B, "healing"),               # Planula
    "SiphonOnLowHealth": (B, "healing"),       # Mired Urn
    "BleedOnHitAndExplode": (B, "proc"),       # Shatterspleen
    "FireballsOnHit": (B, "proc"),             # Molten Perforator
    "LightningStrikeOnHit": (B, "proc"),       # Charged Perforator
    "SprintWisp": (B, "proc"),                 # Little Disciple
    "BeetleGland": (B, "utility"),             # Queen's Gland
    "TitanGoldDuringTP": (B, "utility"),       # Halcyon Seed
    "ScrapYellow": (B, "scrap"),
    # --- lunar ------------------------------------------------------------------
    "LunarDagger": (L, "damage"),              # Shaped Glass
    "ShieldOnly": (L, "defense"),              # Transcendence
    "FocusConvergence": (L, "utility"),        # Focused Convergence
    "MonstersOnShrineUse": (L, "utility"),     # Defiant Gouge
}


def derive(items: list[dict]) -> str:
    """`build.identity` for one inventory (hidden items already removed by the caller)."""
    weight: Counter[str] = Counter()
    classified = unclassified = 0
    for it in items or []:
        name = str(it.get("name") or "")
        try:
            count = max(int(float(it.get("count", 1))), 0)
        except (TypeError, ValueError):
            count = 1
        entry = ITEMS.get(name)
        if entry is None:
            unclassified += count
            continue
        classified += count
        tier, tag = entry
        if tag in VOTES:
            weight[VOTES[tag]] += TIER_WEIGHT[tier] * count

    if unclassified > classified:
        return UNKNOWN
    total = sum(weight.values())
    if total < MIN_WEIGHT:
        return "undefined"
    ranked = weight.most_common()
    top, w = ranked[0]
    if len(ranked) > 1 and ranked[1][1] == w:
        return "mixed"                  # a tie has no dominant tag; never pick by list order
    return top if w / total >= DOMINANT_SHARE else "mixed"
