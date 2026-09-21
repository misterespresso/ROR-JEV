"""The harness driver's ladder, pinned down.

The driver is not a policy and must never be mistaken for one -- but a driver that beelines
for the teleporter past affordable chests, never fights, and starts a boss at level 1
(`fork-03`) produces short recordings that never reach the phases worth recording. These
traces fix the ladder's priorities so they cannot silently regress.

Run: python eval/test_driver.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.record_run import (
    CHEST_RETRY_S, EXPLORE_BUDGET_S, FIGHT_RADIUS_M, STAGE_CAP_S, RunState, decide, on_event,
)
from eval.test_chest_trace import FAIL, PASS, check

NOW = 10_000.0


def st(**kw) -> RunState:
    """A live, early-stage state with nothing around; override what a case needs."""
    s = RunState(objective="exploring", scene_name="blackbeach", in_game=True, gold=0,
                 stage_started_at=NOW - 60, teleporter_seen=True)
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def rule(s: RunState, now: float = NOW):
    d = decide(s, now)
    return d[2] if d else None


def trace_priorities():
    print("\nDriver ladder: priorities")
    check("charged teleporter -> advance",
          rule(st(objective="teleporter_charged")) == "advance_when_charged")
    check("charging -> hold the zone (the fork-02 death)",
          rule(st(objective="charging_teleporter")) == "hold_zone_while_charging")
    check("charging beats a close enemy",
          rule(st(objective="charging_teleporter", nearest_enemy_m=5.0))
          == "hold_zone_while_charging")
    check("close enemy -> fight",
          rule(st(nearest_enemy_m=FIGHT_RADIUS_M - 1)) == "fight_nearby")
    check("fighting beats looting",
          rule(st(nearest_enemy_m=8.0, gold=500)) == "fight_nearby")
    check("distant enemy does not stop looting",
          rule(st(nearest_enemy_m=FIGHT_RADIUS_M + 10, gold=100)) == "loot_chest")


def trace_chests():
    print("\nDriver ladder: chests (fork-03: 'runs by chests it can afford')")
    # The mod finds the chest; the driver no longer needs one in the view cone first (F12).
    check("affordable, no chest in view list -> still asks the mod to find one",
          rule(st(gold=30, chest_seen=False)) == "loot_chest")
    check("uses the observed price when one is visible",
          rule(st(gold=40, chest_cost=45)) != "loot_chest")
    check("uses the learned price when none is visible",
          rule(st(gold=26, chest_price=25)) == "loot_chest")
    check("cannot afford -> keep exploring, not teleporter",
          rule(st(gold=10)) == "explore_budget")
    s = st(gold=100)
    on_event(s, {"event_type": "action_failed", "command": "FIND_AND_INTERACT:chest",
                 "reason": "not_found"}, NOW)
    check("after not_found -> does not re-ask immediately", rule(s, NOW + 1) != "loot_chest")
    check("after not_found -> asks again once the view has changed",
          rule(s, NOW + CHEST_RETRY_S + 1) == "loot_chest")
    s = st()
    on_event(s, {"event_type": "action_started", "command": "FIND_AND_INTERACT:chest",
                 "target": "Basic Chest", "cost": "30"}, NOW)
    check("learns the real price from the mod (string '30' coerced)", s.chest_price == 30)
    on_event(s, {"event_type": "action_started", "command": "FIND_AND_INTERACT:teleporter",
                 "cost": "0"}, NOW)
    check("a teleporter's cost is not mistaken for a chest price", s.chest_price == 30)


def trace_stage_pacing():
    print("\nDriver ladder: stage pacing (fork-03: 'beelines for the teleporter')")
    check("early in the stage -> explore, not teleporter",
          rule(st(stage_started_at=NOW - 30)) == "explore_budget")
    check("after the explore budget -> start teleporter",
          rule(st(stage_started_at=NOW - EXPLORE_BUDGET_S - 1)) == "start_teleporter")
    check("after the budget, no teleporter in view -> roam to find it",
          rule(st(stage_started_at=NOW - EXPLORE_BUDGET_S - 1, teleporter_seen=False))
          == "explore")
    check("hard cap ends a crowded stage",
          rule(st(stage_started_at=NOW - STAGE_CAP_S - 1, nearest_enemy_m=5.0))
          == "start_teleporter_stage_cap")


def trace_guards():
    print("\nDriver ladder: never act outside a live run, never interrupt an action")
    check("lobby / loading / death screen -> nothing", decide(st(in_game=False), NOW) is None)
    check("action in progress -> nothing", decide(st(action_busy=True, gold=500), NOW) is None)
    check("999.0 enemy sentinel never reads as 'close'",
          rule(st(nearest_enemy_m=None, gold=0)) == "explore_budget")


TRACES = [trace_priorities, trace_chests, trace_stage_pacing, trace_guards]

if __name__ == "__main__":
    for t in TRACES:
        t()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED: " + ", ".join(FAIL))
    sys.exit(1 if FAIL else 0)
