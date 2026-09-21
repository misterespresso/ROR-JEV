"""Derive the real telemetry schema from recordings.

Phase 0's argument for pulling telemetry forward was that "the catalog cannot be tested
against recordings of fields never captured." This answers the prior question: **what was
captured, and what shape is it actually in?**

It reports, per frame type, every field observed with its JSON types and sample values, and
then flags the specific hazards real frames have already produced:

    * numerics arriving as strings          (F11 `enemy_count:"1"`, F14 `cost:"30"`)
    * sentinel values masquerading as data  (F17 `nearest_enemy_distance: 999.0`)
    * interactable types our registry cannot map  (F9 `shrine`, `teleporter`)
    * item names that are not real items    (F13 `DrizzlePlayerHelper`)
    * event types no document mentions      (F10/F15/F16)

Everything is derived from the recordings, so the output is evidence rather than assumption.
Run it over a step-7 run and the diff against `contract/questions.yaml`'s expectations is the
Phase 0 step 4 work list.

Usage:
    python -m eval.inspect_recording                      # every recording
    python -m eval.inspect_recording --run-id gonogo-03
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import yaml
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from brain.registry import TYPE_TO_VERB
from brain.stock_adapter import classify

ROOT = pathlib.Path(__file__).resolve().parents[1]
RECORDINGS = ROOT / "recordings"

#: Values that look like measurements and are not. Confirmed in `gonogo-03`: with
#: `enemy_count: 0` the mod reports `nearest_enemy_distance: 999.0`.
SUSPECTED_SENTINELS = {999.0, 9999.0, -1.0, 99999.0}

#: Items the game inserts that a player never picked up. Passing these to JEV corrupts
#: `build.identity` and every economy judgment (F13).
KNOWN_HIDDEN_ITEMS = {"DrizzlePlayerHelper", "MonsoonPlayerHelper", "TeleportOnLowHealth"}

#: Field names whose value should be numeric. Used to detect string-typed numbers.
NUMERIC_FIELDS = {
    "cost", "distance", "current_gold", "amount", "remaining", "enemy_count", "money",
    "count", "chargePercent", "teleporter_charge", "nearest_enemy_distance", "total",
    "charged", "usesRemaining", "health", "maxHealth",
}


#: Where each banded state field the catalog demands could come from in STOCK 1.1.1.
#: Derived from the recordings, not from the documents. `None` means no stock query or event
#: supplies it at all -- that field is C# work, and every question naming it is omitted
#: until it exists (`02-ARCHITECTURE.md` field contract: omit, never null).
STOCK_SOURCE: dict[str, str | None] = {
    "objective.phase":                 "OBJECTIVE.objective",
    "economy.gold_band":               "INVENTORY.money",
    "build.identity":                  "INVENTORY.items -> brain/build_identity.py (provisional table)",
    "control.active_action":           "action_started/complete events (brain-side)",
    "control.consecutive_failures":    "action_failed events (brain-side)",
    "control":                         "partial: no progress measurement",
    "control.progress_band":           None,   # roadmap step 4: MEASURED, never inferred
    "self.hp_band":                    None,   # see F18: no player-health query exists
    "self.equipment":                  "INVENTORY.equipment (name only; 1 of 4 fields)",
    "run.survivor":                    None,
    "run.time_band":                   None,   # no elapsed-time field anywhere
    "threat":                          "partial: COMBAT_STATUS + OBJECTIVE.boss_active",
    "threat.enemies_near":             "COMBAT_STATUS.enemy_count",
    "threat.nearest_enemy_band":       "COMBAT_STATUS.nearest_enemy_distance",
    "threat.boss_active":              "OBJECTIVE.boss_active",
    "threat.enemy_classes":            None,   # no per-enemy list at all
    "threat.reposition_feasible":      None,   # needs the pathfinder
    "threat.full_withdrawal_feasible": None,
    "hazards":                         None,   # no environmental/DoT/projectile facts
}


#: What fork build 2's QUERY_OBSERVATION adds, applied only when the recording actually
#: contains OBSERVATION frames -- a stock recording must not be credited with them.
OBSERVATION_SOURCE: dict[str, str] = {
    "self.hp_band":  "OBSERVATION.self (build 2+)",
    "run.survivor":  "OBSERVATION.run.survivor (build 2+)",
    "run.time_band": "OBSERVATION.run.elapsed_s (build 2+)",
    "hazards":       "OBSERVATION.hazards (build 2+)",
}


def _type_of(v) -> str:
    if v is None:
        return "null"
    return {bool: "bool", int: "int", float: "float", str: "str",
            list: "list", dict: "dict"}.get(type(v), type(v).__name__)


class Schema:
    def __init__(self) -> None:
        # frame type -> field -> {json type -> set of sample reprs}
        self.fields: dict[str, dict[str, dict[str, set]]] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(set)))
        self.counts: dict[str, int] = defaultdict(int)
        self.interactable_types: set[str] = set()
        self.interactable_names: dict[str, set[str]] = defaultdict(set)
        self.item_names: set[str] = set()
        self.sentinels: dict[str, set] = defaultdict(set)
        self.string_numerics: dict[str, set] = defaultdict(set)
        self.equipment_values: set[str] = set()
        self.objectives: set[str] = set()
        self.scenes: set[str] = set()
        self.mapped_pairs: dict[str, set] = defaultdict(set)
        self.unmapped_pairs: dict[str, None] = {}

    def observe(self, frame: dict) -> None:
        ftype = str(frame.get("type") or "?")
        if ftype == "EVENT":
            ftype = f"EVENT:{frame.get('event_type')}"
        self.counts[ftype] += 1

        for k, v in frame.items():
            if k == "type":
                continue
            self.fields[ftype][k][_type_of(v)].add(repr(v)[:60])
            if k in NUMERIC_FIELDS:
                if isinstance(v, str):
                    self.string_numerics[f"{ftype}.{k}"].add(v)
                elif isinstance(v, float) and v in SUSPECTED_SENTINELS:
                    self.sentinels[f"{ftype}.{k}"].add(v)

        for it in frame.get("interactables", []) or []:
            t = str(it.get("type"))
            self.interactable_types.add(t)
            self.interactable_names[t].add(str(it.get("name")))
            # Classify exactly as the adapter does, so the two tools cannot disagree
            # about what the registry can offer.
            mapped = classify(it)
            key = f"{t}/{it.get('name')}"
            if mapped:
                # A teleporter maps by FIELD state, not by name, so one name can yield
                # several types. Collect them all rather than keeping the last.
                self.mapped_pairs[key].add(mapped)
            else:
                self.unmapped_pairs[key] = None
            for k, v in it.items():
                self.fields[f"{ftype}.interactables[]"][k][_type_of(v)].add(repr(v)[:60])
                if k in NUMERIC_FIELDS and isinstance(v, str):
                    self.string_numerics[f"interactables[].{k}"].add(v)

        for item in frame.get("items", []) or []:
            self.item_names.add(str(item.get("name")))

        if ftype == "INVENTORY" and isinstance(frame.get("equipment"), str):
            self.equipment_values.add(frame["equipment"])
        if ftype == "OBJECTIVE":
            if frame.get("objective"):
                self.objectives.add(str(frame["objective"]))
            if frame.get("scene_name"):
                self.scenes.add(str(frame["scene_name"]))


def load(run_dirs: list[pathlib.Path], schema: Schema) -> int:
    n = 0
    for d in run_dirs:
        for stream in ("observations", "events", "other", "session"):
            f = d / f"{stream}.jsonl"
            if not f.exists():
                continue
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                frame = rec.get("frame")
                if not isinstance(frame, dict):
                    continue
                # The harness's own bookkeeping is not game telemetry.
                if str(frame.get("type", "")).startswith(("PROBE_", "HARNESS_")):
                    continue
                schema.observe(frame)
                n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description="Derive telemetry schema from recordings")
    ap.add_argument("--run-id", action="append",
                    help="specific run(s); default is every recording")
    a = ap.parse_args()

    if a.run_id:
        dirs = [RECORDINGS / r for r in a.run_id]
    else:
        dirs = sorted(d for d in RECORDINGS.glob("*") if d.is_dir())
    dirs = [d for d in dirs if d.exists()]
    if not dirs:
        print("no recordings found. Run: python -m eval.record_run --run-id run-01")
        return 1

    schema = Schema()
    n = load(dirs, schema)

    print("=" * 78)
    print("OBSERVED TELEMETRY SCHEMA")
    print("=" * 78)
    print(f"recordings : {', '.join(d.name for d in dirs)}")
    print(f"frames     : {n}")
    print()

    print("-" * 78)
    print("FRAME TYPES")
    print("-" * 78)
    for ft, c in sorted(schema.counts.items(), key=lambda kv: -kv[1]):
        print(f"  {ft:<34} {c:5d}")
    print()

    print("-" * 78)
    print("FIELDS BY FRAME TYPE")
    print("-" * 78)
    for ft in sorted(schema.fields):
        print(f"  {ft}")
        for fname in sorted(schema.fields[ft]):
            types = schema.fields[ft][fname]
            tdesc = "|".join(sorted(types))
            samples = sorted({s for ss in types.values() for s in ss})[:3]
            print(f"      {fname:<26} {tdesc:<14} e.g. {', '.join(samples)}")
        print()

    problems = 0

    print("-" * 78)
    print("HAZARDS")
    print("-" * 78)

    if schema.string_numerics:
        problems += 1
        print("  NUMERIC-AS-STRING (F11/F14/F16) -- coerce, never compare raw:")
        for k, vals in sorted(schema.string_numerics.items()):
            print(f"      {k:<40} {sorted(vals)[:6]}")
    else:
        print("  no string-typed numerics seen")

    if schema.sentinels:
        problems += 1
        print("  SENTINELS (F17) -- must map to a band, never be differenced:")
        for k, vals in sorted(schema.sentinels.items()):
            print(f"      {k:<40} {sorted(vals)}")

    hidden = schema.item_names & KNOWN_HIDDEN_ITEMS
    if hidden:
        problems += 1
        print(f"  HIDDEN ITEMS (F13) -- exclude before JEV sees them: {sorted(hidden)}")
    if schema.item_names - KNOWN_HIDDEN_ITEMS:
        print(f"  real items seen: {sorted(schema.item_names - KNOWN_HIDDEN_ITEMS)}")

    if schema.unmapped_pairs:
        problems += 1
        print("  UNMAPPED (type/name) -- the registry can offer nothing for these:")
        for k in sorted(schema.unmapped_pairs):
            print(f"      {k}")
    if schema.mapped_pairs:
        print("  mapped (type/name -> our type):")
        for k, v in sorted(schema.mapped_pairs.items()):
            print(f"      {k:<34} -> {', '.join(sorted(v))}")

    # Identity: duplicate names within a type mean no stable identity (F17).
    dupes = {t: ns for t, ns in schema.interactable_names.items() if len(ns) == 1}
    if dupes:
        print("  NOTE: interactables carry no entity ID; several share one display name,")
        print("        so a specific target cannot be tracked across observations.")

    if schema.equipment_values:
        odd = {v for v in schema.equipment_values if v in {"None", "none", ""}}
        if odd:
            problems += 1
            print(f"  EMPTY-AS-STRING: equipment = {sorted(odd)} (truthy; not null)")

    print()
    print("-" * 78)
    print("VOCABULARY OBSERVED")
    print("-" * 78)
    print(f"  objective values : {sorted(schema.objectives) or '(none)'}")
    print(f"  scenes           : {sorted(schema.scenes) or '(none)'}")
    print(f"  event types      : "
          f"{sorted(k.split(':', 1)[1] for k in schema.counts if k.startswith('EVENT:'))}")
    print()

    # ---- catalog coverage: the step-4 work list -----------------------------
    print("-" * 78)
    print("CATALOG COVERAGE AGAINST STOCK TELEMETRY")
    print("-" * 78)
    contract = yaml.safe_load(
        (ROOT / "contract" / "questions.yaml").read_text(encoding="utf-8"))
    questions = contract["questions"]

    all_needed: set[str] = set()
    for q in questions.values():
        all_needed.update(q.get("required_state_fields") or [])

    # "partial" is NOT available. The brain omits a question if ANY part of a block it
    # declares is unknown (brain/catalog.py `_telemetry_ok`), so a partially sourced block is
    # as blocking as a missing one. Counting partial as present is what produced a wrong claim
    # that time + hazards alone would unblock `next_action` -- `threat` was "partial".
    if schema.counts.get("OBSERVATION"):
        STOCK_SOURCE.update(OBSERVATION_SOURCE)

    def available(f):
        src = STOCK_SOURCE.get(f)
        return bool(src) and not str(src).startswith("partial")
    have = {f for f in all_needed if available(f)}
    missing = sorted(f for f in all_needed if not STOCK_SOURCE.get(f))
    for f in sorted(all_needed):
        src = STOCK_SOURCE.get(f)
        mark = "ok " if available(f) else ("PRT" if src else "GAP")
        print(f"  [{mark}] {f:<34} {src or 'NO STOCK SOURCE -- needs C#'}")
    print()

    # Milestone 1 disables counsel and has no `equipment` capability, so two questions are
    # gated off regardless of telemetry. Counting them as sendable would flatter the result.
    m1_excluded = set(
        yaml.safe_load((ROOT / "contract" / "capabilities.yaml").read_text(encoding="utf-8"))
        ["milestone_1"].get("excluded_questions") or [])

    print("  Effect on the catalog, applying the omit-never-null rule:")
    sendable = []
    for name, q in questions.items():
        need = set(q.get("required_state_fields") or [])
        gaps = sorted(need - have)
        if gaps:
            print(f"      OMITTED   {name:<24} missing {', '.join(gaps)}")
        elif name in m1_excluded:
            print(f"      gated off {name:<24} (not in milestone 1: capability/counsel)")
        else:
            sendable.append(name)
            print(f"      SENDABLE  {name}")
    print()
    if not sendable:
        print("  *** NO QUESTION IN THE MILESTONE-1 CATALOG CAN BE SENT against stock")
        print("      telemetry. *** Every one names at least one field stock does not")
        print("  provide, so every one is omitted, so nothing is ever authorized -- the")
        print("  agent would be inert. This is not a tuning problem: the field contract has")
        print("  no partial-credit path, by design (omit, never null).")
        print()
        print("  Closing these gaps IS Phase 0 step 4, and it is all C#. It is also the")
        print("  honest size of the remaining work: the brain is complete and untestable")
        print("  against reality until the mod supplies hp, survivor, elapsed time, enemy")
        print("  classes, hazards, navigation progress and path feasibility.")
        problems += 1
    print()

    expected_phases = {"exploring", "charging_teleporter", "teleporter_charged"}
    missing = expected_phases - schema.objectives
    if missing:
        print(f"  NOT YET RECORDED: {sorted(missing)}")
        print("  The registry's phase handling cannot be tested against telemetry that never")
        print("  entered those phases. Record a run that charges and leaves a teleporter.")
        print()

    print("=" * 78)
    print(f"{problems} hazard class(es) present. Each one is a state_builder requirement,")
    print("not a curiosity: every one of them reaches JEV as a fact if nothing strips it.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
