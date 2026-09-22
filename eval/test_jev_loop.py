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
          any("small chest (affordable, far)" in t for t in nav.values()), str(nav))
    check("and says nothing is used on arrival",
          all("nothing is used or started" in t for t in nav.values()), str(nav))


def trace_fight_and_primer():
    print("\njev-02: fighting is choosable, and the game primer rides in every request")
    asked: list = []
    fight = frames(True)
    fight["QUERY_COMBAT_STATUS"].update({"enemy_count": 3, "nearest_enemy_distance": 12.0})
    p, rec = run([fight], pick="fight_nearby", asked=asked)
    crit = asked[0]["questions"]["next_action"]["criteria"]
    check("fight_nearby is offered with enemies near", "fight_nearby" in crit, str(sorted(crit)))
    check("picking it sets the mod's combat stance", ("MODE", "combat") in p.commands, str(p.commands))
    check("it holds no task slot (JEV re-decides next tick)",
          not any(f.get("note") == "task_released" for f in rec.frames))
    quiet: list = []
    run([frames(True)], pick="none", asked=quiet)
    check("not offered with no enemies",
          "fight_nearby" not in quiet[0]["questions"]["next_action"]["criteria"])
    game = asked[0]["state"].get("game")
    check("the primer is in the state, first", isinstance(game, list) and game
          and list(asked[0]["state"])[0] == "game", str(list(asked[0]["state"])[:3]))
    check("and the instructions point at it",
          "`game`" in asked[0]["questions"]["next_action"]["instructions"])


def trace_equipment():
    print("\njev-05: equipment is reported, offered when it has a target, and fired")
    from brain.equipment import band
    check("Fruit with full health has no target", band({"name": "Fruit", "ready": True}, "healthy", None)["valid_target_available"] is False)
    check("Fruit when hurt does", band({"name": "Fruit", "ready": True}, "hurt", None)["valid_target_available"] is True)
    check("a missile launcher needs an enemy within 30 m",
          band({"name": "CommandMissile", "ready": True}, "healthy", 12.0)["valid_target_available"]
          and not band({"name": "CommandMissile", "ready": True}, "healthy", 45.0)["valid_target_available"])
    check("the Fuel Array is never activation-compatible",
          band({"name": "QuestVolatileBattery", "ready": True}, "low", 5.0)["activation_compatible"] is False)
    check("unrecognised equipment is not supported", band({"name": "Mystery", "ready": True}, "low", 5.0)["supported_activation"] is False)
    check("an empty slot is all-false, not unknown", band(None, "low", 5.0)["ready"] is False)

    hurt = frames(True)
    hurt["QUERY_OBSERVATION"]["self"].update({"hp": 40, "equipment": {"name": "Fruit", "charges": 1, "ready": True}})
    asked: list = []

    def fake(wire, **_kw):
        asked.append(wire)
        ans = {"next_action": {"type": "choice", "choice": "none", "confidence": 0.9,
                               "probabilities": {"none": 0.95}}}
        if "use_equipment_now" in wire["questions"]:
            ans["use_equipment_now"] = {"type": "noul", "noul": 0.9}
        return JevResult(answers=ans, usage={"input_tokens": 1}, latency_ms=1.0, model="stub")

    jev_run.ask = fake
    stop = {"flag": False}
    p, rec = FakeProbe([hurt], stop), FakeRec()
    a = types.SimpleNamespace(run_id="t", minutes=1.0, decide_every=0.0, dry_run=False)
    from brain.catalog import load_contract
    from eval.fixtures import MILESTONE_1_CAPABILITIES
    jev_run.loop(p, rec, a, load_contract(), frozenset(MILESTONE_1_CAPABILITIES), stop)
    check("use_equipment_now is asked when hurt with a ready Fruit",
          asked and "use_equipment_now" in asked[0]["questions"], str(sorted(asked[0]["questions"]) if asked else []))
    check("a confident yes fires it", ("USE_EQUIPMENT", "") in p.commands, str(p.commands))


def trace_redirect():
    print("\njev-06: a walk is redirected when JEV wants another target two ticks running")
    far = frames(True)
    far["QUERY_OBSERVATION"]["interactables"]["items"][-1]["distance"] = 40.0     # chest: walk
    far["QUERY_OBSERVATION"]["interactables"]["items"][0]["distance"] = 25.0      # barrel: walk

    def scripted(picks):
        answers = iter(picks)

        def fake(wire, **_kw):
            want = next(answers, "none")
            opts = wire["questions"]["next_action"]["criteria"]
            choice = next((k for k in opts if want in k), "none")
            return JevResult(answers={"next_action": {"type": "choice", "choice": choice,
                                                      "confidence": 0.9, "probabilities": {choice: 0.8}}},
                             usage={"input_tokens": 1}, latency_ms=1.0, model="stub")
        return fake

    def go(picks):
        jev_run.ask = scripted(picks)
        stop = {"flag": False}
        p, rec = FakeProbe([far] * len(picks), stop), FakeRec()
        a = types.SimpleNamespace(run_id="t", minutes=1.0, decide_every=0.0, dry_run=False)
        from brain.catalog import load_contract
        from eval.fixtures import MILESTONE_1_CAPABILITIES
        jev_run.loop(p, rec, a, load_contract(), frozenset(MILESTONE_1_CAPABILITIES), stop)
        return p, rec

    p, rec = go(["chest_small", "barrel", "barrel", "barrel"])
    gotos = [c for c in p.commands if c[0] == "GOTO_ID"]
    check("walk to the chest, then redirected to the barrel", gotos == [("GOTO_ID", str(CHEST)), ("GOTO_ID", "-97000")], str(gotos))
    check("the redirect is recorded", any(f.get("note") == "task_redirected" for f in rec.frames))
    p, rec = go(["chest_small", "chest_small", "chest_small", "chest_small"])
    gotos = [c for c in p.commands if c[0] == "GOTO_ID"]
    check("still wanting the same target does not redirect", gotos == [("GOTO_ID", str(CHEST))], str(gotos))
    # jev-11: the alternative need not be the same twice -- JEV's second choice moves around.
    p, rec = go(["chest_small", "barrel", "teleporter", "barrel"])
    gotos = [c for c in p.commands if c[0] == "GOTO_ID"]
    check("two ticks wanting anything else redirects to the latest pick",
          len(gotos) == 2 and gotos[1] != ("GOTO_ID", str(CHEST)), str(gotos))


def trace_stuck_walk():
    print("\njev-09: a walk that gets no closer is abandoned, and that target is set aside")
    far = frames(True)
    far["QUERY_OBSERVATION"]["interactables"]["items"][-1]["distance"] = 40.0   # never changes
    old = (jev_run.STUCK_AFTER_S, jev_run.QUARANTINE_S)
    jev_run.STUCK_AFTER_S, jev_run.QUARANTINE_S = -1.0, 300.0
    try:
        asked: list = []
        p, rec = run([far] * 4, pick=str(CHEST), asked=asked)
    finally:
        jev_run.STUCK_AFTER_S, jev_run.QUARANTINE_S = old
    rel = [f for f in rec.frames if f.get("note") == "task_released"]
    check("the stuck walk is released", rel and "no closer" in rel[0]["why"], str(rel))
    later = [w for w in asked[2:]]
    check("the target is not offered again while set aside",
          all(not any(str(CHEST) in k for k in w["questions"]["next_action"]["criteria"])
              for w in later), str([sorted(w["questions"]["next_action"]["criteria"]) for w in later][:1]))
    check("only one walk was ever sent", len([c for c in p.commands if c[0] == "GOTO_ID"]) == 1, str(p.commands))


def trace_refused_action_is_withheld():
    print("\njev-10: a gate-refused action is withheld, so the next tick moves on")
    fr = frames(True)
    fr["QUERY_OBSERVATION"]["interactables"]["items"].append(
        {"id": -55555, "kind": "purchase", "prefab": "ShrineChance", "display": "Shrine of Chance",
         "cost": 25, "cost_type": "Money", "distance": 3.0, "height": 0.0})
    asked: list = []

    def fake(wire, **_kw):
        asked.append(wire)
        opts = wire["questions"]["next_action"]["criteria"]
        choice = next((k for k in opts if k.startswith("use_shrine_chance")), "explore")
        ans = {"next_action": {"type": "choice", "choice": choice, "confidence": 0.9,
                               "probabilities": {choice: 0.9}}}
        for q in wire["questions"]:
            if q.startswith("worth_"):
                ans[q] = {"type": "noul", "noul": 0.3}      # JEV: not worth it
        return JevResult(answers=ans, usage={"input_tokens": 1}, latency_ms=1.0, model="stub")

    jev_run.ask = fake
    stop = {"flag": False}
    p, rec = FakeProbe([fr] * 3, stop), FakeRec()
    a = types.SimpleNamespace(run_id="t", minutes=1.0, decide_every=0.0, dry_run=False)
    from brain.catalog import load_contract
    from eval.fixtures import MILESTONE_1_CAPABILITIES
    jev_run.loop(p, rec, a, load_contract(), frozenset(MILESTONE_1_CAPABILITIES), stop)
    check("the shrine is offered on the first tick",
          any(k.startswith("use_shrine_chance") for k in asked[0]["questions"]["next_action"]["criteria"]))
    check("the worth gate refuses it",
          any(f.get("not_authorized") == "worth_gate" for f in rec.frames), str(rec.frames[:3]))
    check("and it is withheld afterwards",
          all(not any(k.startswith("use_shrine_chance") for k in w["questions"]["next_action"]["criteria"])
              for w in asked[1:]), str([sorted(w["questions"]["next_action"]["criteria"]) for w in asked[1:]][:1]))
    check("so something else is sent instead", ("MODE", "roam") in p.commands, str(p.commands))
    check("and walking back to the same shrine is withheld too",
          all(not any(k.startswith("navigate:shrine_chance") for k in w["questions"]["next_action"]["criteria"])
              for w in asked[1:]),
          str([sorted(w["questions"]["next_action"]["criteria"]) for w in asked[1:]][:1]))


def trace_dead_asks_nothing():
    print("\njev_run.loop: no call to JEV while dead")
    asked: list = []
    p, _ = run([frames(True, alive=False)] * 3, pick="explore", asked=asked)
    check("JEV is not called", asked == [], f"{len(asked)} calls")
    check("nothing is sent", p.commands == [], str(p.commands))


if __name__ == "__main__":
    for t in (trace_bridge, trace_loop_executes_the_pick,
              trace_navigate_options_name_their_target, trace_fight_and_primer,
              trace_equipment, trace_redirect, trace_stuck_walk,
              trace_refused_action_is_withheld, trace_dead_asks_nothing):
        t()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)
