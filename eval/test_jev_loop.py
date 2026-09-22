"""The closed loop offline: real fork-11-shaped frames in, a stand-in JEV, commands out.

No game, no network. A fake connection answers the queries `poll` sends; `ask` is replaced
by a function that picks a named option. Checks that the loop turns JEV's pick into the
right fork command, holds the task slot while it runs, and releases it when the target is
used -- and that it asks nothing of a dead character.

Run: python eval/test_jev_loop.py
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from brain.jev_client import JevResult
from brain.stock_bridge import instance_id, to_mod
from brain.policy import Command
from eval import jev_run
from eval.test_chest_trace import FAIL, PASS, check

CHEST = -96072


def frames(chest_available: bool, alive: bool = True) -> dict:
    items = [{"id": -97000, "kind": "barrel", "prefab": "Barrel1", "display": "Barrel",
              "cost": 0, "cost_type": "None", "distance": 9.0, "height": 0.0}]
    if chest_available:
        items.append({"id": CHEST, "kind": "purchase", "prefab": "Chest1", "display": "Chest",
                      "cost": 25, "cost_type": "Money", "distance": 3.0, "height": 0.0})
    obs = {"type": "OBSERVATION", "in_run": True,
           "run": {"survivor": "CommandoBody", "difficulty": "Easy", "elapsed_s": 60.0,
                   "scene": "blackbeach"},
           "self": {"hp": 100 if alive else -5, "hp_max": 110, "shield": 0, "shield_max": 0,
                    "barrier": 0, "alive": alive},
           "hazards": {"taking_environmental_damage": False, "active_dots": [],
                       "hostile_projectiles_nearby": 0, "in_hazard_volume": False,
                       "in_hazard_volume_covers": "void_fog"},
           "threat": {"enemy_count": 0, "elite_count": 0, "boss_count": 0,
                      "targeting_player_count": 0, "listed": 0, "enemies": []},
           "interactables": {"total": len(items), "unavailable": 0, "listed": len(items),
                             "items": items}}
    return {
        "QUERY_OBJECTIVE": {"type": "OBJECTIVE", "objective": "exploring",
                            "scene_name": "blackbeach", "boss_active": False},
        "QUERY_INVENTORY": {"type": "INVENTORY", "money": 100,
                            "items": [{"name": "Crowbar", "count": 4}]},
        "QUERY_COMBAT_STATUS": {"type": "COMBAT_STATUS", "enemy_count": 0,
                                "nearest_enemy_distance": 999.0},
        "QUERY_INTERACTABLES": {"type": "INTERACTABLES", "interactables": []},
        "QUERY_ALLIES": {"type": "ALLIES", "allies": []},
        "QUERY_PILLARS": {"type": "PILLARS", "pillars": []},
        "QUERY_OBSERVATION": obs,
    }


class FakeProbe:
    """Answers `poll`'s queries from a script of per-tick frames; records commands."""

    def __init__(self, script: list[dict], stop: dict):
        self.script, self.stop, self.i = script, stop, -1
        self.events: list[dict] = []
        self.commands: list[tuple[str, str]] = []
        self.lost = False

    def query(self, kind):
        if kind == "QUERY_OBJECTIVE":             # first query of each sweep = next tick
            self.i += 1
            if self.i >= len(self.script):
                self.stop["flag"] = True
                self.i = len(self.script) - 1
        return self.script[self.i].get(kind)

    def command(self, command, args=""):
        self.commands.append((command, args))


class FakeRec:
    def __init__(self):
        self.frames: list[dict] = []

    def record(self, f):
        self.frames.append(f)


def run(script, pick, asked):
    def fake_ask(wire, **_kw):
        asked.append(wire)
        opts = wire["questions"]["next_action"]["criteria"]
        choice = next((k for k in opts if pick in k), "explore")
        return JevResult(answers={"next_action": {"type": "choice", "choice": choice,
                                                  "confidence": 0.9,
                                                  "probabilities": {choice: 0.95}}},
                         usage={"input_tokens": 1000}, latency_ms=100.0, model="stub")

    jev_run.ask = fake_ask
    stop = {"flag": False}
    p, rec = FakeProbe(script, stop), FakeRec()
    a = types.SimpleNamespace(run_id="t", minutes=1.0, decide_every=0.0, dry_run=False)
    from brain.catalog import load_contract
    from eval.fixtures import MILESTONE_1_CAPABILITIES
    jev_run.loop(p, rec, a, load_contract(), frozenset(MILESTONE_1_CAPABILITIES), stop)
    return p, rec


def trace_bridge():
    print("\nstock_bridge: v2 commands -> fork commands")
    check("instance ID parsed from type@id", instance_id("chest_small@-96072") == "-96072")
    check("stock positional ID is not an instance ID", instance_id("chest-0") is None)

    def cmd(c, **args):
        return Command("cmd-1", 1, c, args, "jev:test", "obs")

    m = to_mod(cmd("INTERACT_TARGET", target_id="barrel@-97000"))
    check("interact by ID", m and (m.command, m.args, m.holds_slot) == ("INTERACT_ID", "-97000", True))
    m = to_mod(cmd("NAVIGATE_TARGET", target_id="teleporter-0"))
    check("F35: navigating to the teleporter only walks there",
          m and (m.command, m.args) == ("GOTO_ID", "teleporter"), str(m))
    m = to_mod(cmd("NAVIGATE_TARGET", target_id="chest_small@-96072"))
    check("F35: navigating to a chest only walks there",
          m and (m.command, m.args) == ("GOTO_ID", "-96072"), str(m))
    check("F35: navigate never becomes an interaction, for any target",
          all((to_mod(cmd("NAVIGATE_TARGET", target_id=t)) or m).command == "GOTO_ID"
              for t in ("teleporter-3", "barrel@-1", "chest_small@-5")))
    check("F35: a stock positional ID cannot be walked to, so it is not approximated",
          to_mod(cmd("NAVIGATE_TARGET", target_id="chest-0")) is None)
    m = to_mod(cmd("INTERACT_TARGET", target_id="teleporter-0"))
    check("starting the teleporter (gated) still uses the mod's finder",
          m and (m.command, m.args) == ("FIND_AND_INTERACT", "teleporter"))
    m = to_mod(cmd("SEARCH", **{"for": "interactables"}))
    check("explore is a standing MODE that frees the slot",
          m and (m.command, m.args, m.holds_slot) == ("MODE", "roam", False))
    check("no fork counterpart -> None, not an approximation",
          to_mod(cmd("FIGHT_TARGET", target_id="x")) is None)


def trace_loop_executes_the_pick():
    print("\njev_run.loop: JEV's pick becomes INTERACT_ID; the slot holds until the chest is used")
    asked: list = []
    script = [frames(True), frames(True), frames(False), frames(False)]
    p, rec = run(script, pick=str(CHEST), asked=asked)
    ids = [c for c in p.commands if c[0] == "INTERACT_ID"]
    check("the chosen chest is sent by its instance ID", ids and ids[0] == ("INTERACT_ID", str(CHEST)),
          str(p.commands))
    check("only once while the task runs", len(ids) == 1, str(p.commands))
    check("released when the chest leaves the available list",
          any(f.get("note") == "task_released" for f in rec.frames),
          str([f for f in rec.frames if f.get("type") == "HARNESS_NOTE"]))
    check("every exchange recorded", sum(f["type"] == "JEV_EXCHANGE" for f in rec.frames) == len(asked))
    check("provenance is jev:", all(f["origin"].startswith("jev:") for f in rec.frames
                                    if f["type"] == "HARNESS_DECISION"))


def trace_navigate_options_name_their_target():
    print("\nF36: navigate options say what they point at")
    asked: list = []
    far = frames(True)
    far["QUERY_OBSERVATION"]["interactables"]["items"][-1]["distance"] = 40.0   # out of range
    run([far], pick="none", asked=asked)
    crit = asked[0]["questions"]["next_action"]["criteria"]
    nav = {k: v["what"] for k, v in crit.items() if k.startswith("navigate:")}
    check("a navigate option exists for the far chest", nav, str(sorted(crit)))
    check("its text names the chest and its bands",
          any("small chest (cheap, far)" in t for t in nav.values()), str(nav))
    check("and says nothing is used on arrival",
          all("nothing is used or started" in t for t in nav.values()), str(nav))


def trace_dead_asks_nothing():
    print("\njev_run.loop: no call to JEV while dead")
    asked: list = []
    p, _ = run([frames(True, alive=False)] * 3, pick="explore", asked=asked)
    check("JEV is not called", asked == [], f"{len(asked)} calls")
    check("nothing is sent", p.commands == [], str(p.commands))


if __name__ == "__main__":
    for t in (trace_bridge, trace_loop_executes_the_pick,
              trace_navigate_options_name_their_target, trace_dead_asks_nothing):
        t()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
