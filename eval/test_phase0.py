"""Phase 0 traces: the omit-never-null rule, the raw recorder, and the built request.

Phase 0 ships two things that can be wrong silently -- a question sent when the telemetry
behind it was unreadable, and a recording with a hole in it. Both of those failures look
like success. These traces make them look like failures.

Run: python eval/test_phase0.py
"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from brain.catalog import (
    UNKNOWN, BuildContext, ConditionError, build_request, evaluate_condition,
    load_contract,
)
from brain.recorder import Recorder, replay
from brain.stock_adapter import adapt, classify, num
from brain.registry import MAX_ENTITY_ACTIONS, build_registry
from eval.fixtures import MILESTONE_1_CAPABILITIES, RAW_BASE, scenarios
from eval.test_chest_trace import FAIL, PASS, check

CONTRACT = load_contract()
CAPS = frozenset(MILESTONE_1_CAPABILITIES)


def _sc(name):
    return next(s for s in scenarios() if s.name == name)


def _build(sc, caps=CAPS, counsel=False):
    reg = build_registry(sc.raw, set(caps), sc.active_task)
    ctx = BuildContext(frozenset(caps), sc.banded, sc.active_task, counsel)
    return build_request(sc.banded, reg, ctx, CONTRACT), reg


# ---------------------------------------------------------------------------
# the field contract
# ---------------------------------------------------------------------------

def trace_omit_never_null():
    print("\n02 field contract: an `unknown` field OMITS its question, never nulls it")
    sc = _sc("chest_in_range")
    req, _ = _build(sc)
    check("baseline sends engagement_response", "engagement_response" in req.questions)

    # `engagement_response` names threat.reposition_feasible. Make it unreadable.
    blind = copy.deepcopy(sc.banded)
    blind["threat"]["reposition_feasible"] = "unknown"
    reg = build_registry(sc.raw, set(CAPS), False)
    req2 = build_request(blind, reg, BuildContext(CAPS, blind, False, False), CONTRACT)

    check("unknown feasibility -> engagement_response omitted",
          "engagement_response" not in req2.questions, str(sorted(req2.questions)))
    check("omission is not a null on the wire",
          all(v is not None for v in req2.questions.values()))
    check("omission is recorded with its reason",
          any(o["question"] == "engagement_response"
              and o["reason"] == "telemetry_unknown" for o in req2.omitted),
          str(req2.omitted))

    # Granularity of `required_state_fields` decides the blast radius (F4). `next_action` was
    # narrowed to the three threat parts it uses (F30), so an unreadable feasibility flag no
    # longer suspends task selection. `strategy` still declares the whole block, and is
    # still omitted: gating runs on the unstripped state.
    check("narrowed next_action survives unknown feasibility (F30)",
          "next_action" in req2.questions, str(req2.omitted))
    check("coarse `threat` declaration still omits strategy",
          "strategy" not in req2.questions, str(sorted(req2.questions)))
    check("no `unknown` anywhere in the wire state",
          "unknown" not in json.dumps(req2.state), json.dumps(req2.state)[:200])
    check("the unknown sub-field is stripped, its measured siblings are kept",
          "reposition_feasible" not in req2.state["threat"]
          and "enemies_near" in req2.state["threat"], str(req2.state["threat"]))

    # A part next_action DOES declare still omits it.
    blind2 = copy.deepcopy(sc.banded)
    blind2["threat"]["enemies_near"] = "unknown"
    req3 = build_request(blind2, reg, BuildContext(CAPS, blind2, False, False), CONTRACT)
    check("unknown threat.enemies_near -> next_action omitted",
          "next_action" not in req3.questions, str(sorted(req3.questions)))
    check("a question declaring a PRECISE path survives (threat.enemy_classes)",
          "engage_range" in req2.questions, str(req2.omitted))
    check("no `unknown` ever reaches a question that reads the field",
          all("unknown" not in json.dumps(q) for q in req2.questions.values()))


def trace_empty_is_not_unknown():
    print("\n02 field contract: a measured EMPTY container is a fact, not missing telemetry")
    sc = _sc("chest_in_range")
    # hazards.active_dots == [] means "measured, nothing burning" -- a real observation.
    check("baseline hazards are empty lists", sc.banded["hazards"]["active_dots"] == [])
    req, _ = _build(sc)
    check("empty hazards still permit engagement_response",
          "engagement_response" in req.questions, str(req.omitted))


def trace_capability_gating():
    print("\nT17 / C3: an absent capability removes the question and the registry record")
    sc = _sc("chest_in_range")
    req, reg = _build(sc)
    check("milestone 1 has no `equipment` capability", "equipment" not in CAPS)
    check("use_equipment_now never sent", "use_equipment_now" not in req.questions)
    check("situation_is_novel never sent with counsel off",
          "situation_is_novel" not in req.questions)

    # Withdraw set_posture: both posture questions must vanish together.
    caps = CAPS - {"set_posture"}
    req2, _ = _build(sc, caps)
    check("no set_posture -> strategy and engage_range both omitted",
          "strategy" not in req2.questions and "engage_range" not in req2.questions,
          str(sorted(req2.questions)))
    check("no set_posture -> next_action still sent (registry is never empty)",
          "next_action" in req2.questions)

    # Withdraw interact_target: chest options must leave the catalog entirely.
    caps = CAPS - {"interact_target"}
    req3, reg3 = _build(sc, caps)
    check("no interact_target -> no open_chest record",
          not any(k.startswith("open_chest") for k in reg3), str(sorted(reg3)))
    check("no interact_target -> no open_chest option on the wire",
          not any(k.startswith("open_chest")
                  for k in req3.questions["next_action"]["criteria"]),
          str(sorted(req3.questions["next_action"]["criteria"])))


def trace_counsel_flag():
    print("\nC1: situation_is_novel is sent only when its consumer exists")
    sc = _sc("chest_in_range")
    off, _ = _build(sc, counsel=False)
    on, _ = _build(sc, counsel=True)
    check("counsel off -> omitted", "situation_is_novel" not in off.questions)
    check("counsel on  -> sent", "situation_is_novel" in on.questions)


def trace_worth_gate_pairing():
    print("\nT1 / invariant 8: every irreversible option ships its worth question")
    for name in ("shrine_in_range", "teleporter_in_range", "charged_advance",
                 "ceiling_twelve"):
        sc = _sc(name)
        req, reg = _build(sc)
        opts = set(req.questions["next_action"]["criteria"])
        irrev = [r for r in reg.values() if r.requires_worth_gate]
        missing = [r.action_id for r in irrev if r.worth_question not in req.questions]
        check(f"{name}: every gated action has its Noul ({len(irrev)} gated)",
              not missing, str(missing))
        orphan = [q for q in req.questions
                  if q.startswith("worth_") and q[len("worth_"):] not in opts]
        check(f"{name}: no worth question without a selectable action",
              not orphan, str(orphan))


def trace_registry_option_parity():
    print("\nInvariant 3: every non-sentinel option key is a registry key")
    for sc in scenarios():
        req, reg = _build(sc)
        opts = set(req.questions["next_action"]["criteria"])
        sentinels = {"none", "continue_current_action"}
        check(f"{sc.name}: options - sentinels == registry keys",
              opts - sentinels == set(reg), str((opts - sentinels) ^ set(reg)))
        check(f"{sc.name}: `none` always offered", "none" in opts)
        check(f"{sc.name}: continue_current_action iff active task",
              ("continue_current_action" in opts) == sc.active_task)


def trace_no_blank_options():
    print("\nEvery option JEV can pick carries text describing it")
    for sc in scenarios():
        req, _ = _build(sc)
        blank = [k for k, v in req.questions["next_action"]["criteria"].items()
                 if not v.get("what", "").strip()]
        check(f"{sc.name}: no option with empty `what`", not blank, str(blank))
        unsub = [k for k, v in req.questions["next_action"]["criteria"].items()
                 if any("{" in s for s in v.values())]
        check(f"{sc.name}: no unsubstituted template placeholder", not unsub, str(unsub))


def trace_condition_grammar():
    print("\nenabled_when: unrecognised forms fail loudly rather than defaulting")
    ctx = BuildContext(CAPS, {}, False, False)
    check("always -> true", evaluate_condition("always", ctx))
    check("capability(present) -> true", evaluate_condition("capability(search)", ctx))
    check("capability(absent) -> false", not evaluate_condition("capability(equipment)", ctx))
    check("conjunction requires every term",
          not evaluate_condition("capability(search) and counsel_enabled", ctx))
    for bad in ("capability(search) or counsel_enabled", "not counsel_enabled"):
        try:
            evaluate_condition(bad, ctx)
            check(f"rejects unsupported form {bad!r}", False, "no error raised")
        except ConditionError:
            check(f"rejects unsupported form {bad!r}", True)


def trace_no_arithmetic_on_the_wire():
    print("\n01 jaggedness: no raw number crosses into the catalog or state")
    sc = _sc("shrine_in_range")
    req, _ = _build(sc)
    # The registry holds numeric cost_limits for the MOD to enforce; they must not appear
    # in what JEV reads. Semantic bands go to JEV, numeric limits go on the wire.
    wire = json.dumps(req.wire())
    check("no `cost_limit` in the JEV request", "cost_limit" not in wire)
    check("no `opportunity_token` in the JEV request", "opportunity_token" not in wire)
    check("state carries a gold BAND, not a gold amount",
          req.state["economy"]["gold_band"] == "can_afford_one"
          and "gold" not in req.state["economy"])
    check("raw observation does carry the numbers (they are the mod's job)",
          RAW_BASE["inventory"]["gold"] == 140)
    floats = [k for k, v in req.state["self"].items() if isinstance(v, float)]
    check("no float in `self`", not floats, str(floats))


# ---------------------------------------------------------------------------
# the raw recorder
# ---------------------------------------------------------------------------

def trace_recorder_roundtrip():
    print("\nStep 5: raw frames recorded verbatim and replayed byte-identical")
    with tempfile.TemporaryDirectory() as tmp:
        with Recorder("run-221", root=Path(tmp)) as rec:
            frames = []
            for i in range(3):
                f = copy.deepcopy(RAW_BASE)
                f["seq"] = 4417 + i
                f["observation_id"] = f"obs-{812 + i}"
                frames.append(f)
                rec.record(f)
            rec.record({"type": "EVENT", "event_seq": 1, "command_id": "cmd-104",
                        "state": "accepted"})
            summary = rec.summary()

        back = list(replay(Path(tmp) / "run-221"))
        check("all observations replayed", len(back) == 3, str(len(back)))
        check("replay is byte-identical to what was recorded",
              back == frames)
        check("events land in their own stream", summary["counts"]["events"] == 1)
        check("no gaps on a contiguous sequence", summary["gaps"] == 0)
        check("recording is unbanded -- raw hp survives",
              back[0]["self"]["hp"] == 104.0)


def trace_recorder_detects_gaps():
    print("\nStep 5: a hole in the recording is recorded as a hole (T19 trigger)")
    with tempfile.TemporaryDirectory() as tmp:
        with Recorder("run-gap", root=Path(tmp)) as rec:
            for seq in (10, 11, 14):
                f = copy.deepcopy(RAW_BASE)
                f["seq"] = seq
                rec.record(f)
            rec.record({"type": "EVENT", "event_seq": 5, "state": "accepted"})
            rec.record({"type": "EVENT", "event_seq": 9, "state": "completed"})
            gaps = list(rec.gaps)

        check("observation gap detected", any(g.stream == "observations" for g in gaps))
        check("observation gap names the missing range",
              any(g.expected == 12 and g.got == 14 for g in gaps), str(gaps))
        check("event_seq gap detected (triggers RECONCILE)",
              any(g.stream == "events" and g.expected == 6 for g in gaps), str(gaps))
        text = (Path(tmp) / "run-gap" / "gaps.jsonl").read_text(encoding="utf-8")
        check("gaps persisted to disk, not just held in memory",
              text.count("\n") == 2, repr(text))


def trace_recorder_out_of_order():
    print("\nStep 5: one late frame does not manufacture a gap on every later frame")
    with tempfile.TemporaryDirectory() as tmp:
        with Recorder("run-ooo", root=Path(tmp)) as rec:
            for seq in (10, 11, 12, 9, 13, 14):
                f = copy.deepcopy(RAW_BASE)
                f["seq"] = seq
                rec.record(f)
            gaps = rec.gaps
        # seq 9 arriving after 12 is itself one discontinuity; 13 and 14 follow 12 and
        # must not each be reported as a gap against a rewound cursor.
        check("exactly one discontinuity reported", len(gaps) == 1, str(gaps))
        check("the late frame is still recorded",
              len(list(replay(Path(tmp) / "run-ooo"))) == 6)


def trace_recorder_append_and_pin():
    print("\nStep 3 + 5: reconnect extends the recording and pins the build")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ack = {"type": "HELLO_ACK", "protocol_version": 2, "session_id": "sess-8c02",
               "game_version": "1.3.9", "mod_version": "ror-jev-0.1.0",
               "bepinex_version": "5.4.23.2", "r2api_version": "5.1.5",
               "registry_version": 1, "capabilities": sorted(MILESTONE_1_CAPABILITIES)}
        with Recorder("run-221", root=root) as rec:
            rec.record_pinned_build(ack)
            rec.record(copy.deepcopy(RAW_BASE))
        with Recorder("run-221", root=root) as rec:          # reconnect, same run
            rec.record(copy.deepcopy(RAW_BASE))

        obs = list(replay(root / "run-221"))
        check("reconnect appends rather than truncating", len(obs) == 2, str(len(obs)))
        session = [json.loads(l) for l in
                   (root / "run-221" / "session.jsonl").read_text(encoding="utf-8").splitlines()]
        check("pinned build recorded alongside the frame",
              session[0]["pinned_build"]["game_version"] == "1.3.9")
        check("recording is self-describing about the mod build",
              session[0]["pinned_build"]["mod_version"] == "ror-jev-0.1.0")




# ---------------------------------------------------------------------------
# F1 / F2 regressions (found in Phase 0, fixed in brain/registry.py)
# ---------------------------------------------------------------------------

def _charging_obs(boss=True):
    raw = copy.deepcopy(RAW_BASE)
    raw["objective"] = {"phase": "charging", "teleporter_found": True,
                        "teleporter_charge": 0.4, "pillars": None}
    raw["threat"]["boss_active"] = boss
    raw["threat"]["enemies"] = [
        {"entity_id": "e-91", "body": "Beetle Queen", "class": "boss", "is_elite": False,
         "distance": 12.0, "is_targeting_player": True}]
    raw["interactables"] = [
        {"entity_id": "entity-03", "type": "teleporter_charging",
         "display_name": "Teleporter", "cost": 0, "cost_type": "gold", "locked": False,
         "distance": 40.0, "in_interaction_range": False, "opportunity_token": None,
         "path_reachable": True, "reachability_age_ms": 100}]
    return raw


def trace_f2_charging_offers_defend_zone():
    print("\nF2 / invariant 9: the teleporter event is executable at all")
    raw = _charging_obs()
    reg = build_registry(raw, set(CAPS), False)
    # Before the fix this was exactly ['explore'] -- the agent would wander off mid-boss.
    check("charging phase offers defend_zone", "defend_zone:entity-03" in reg, str(sorted(reg)))
    check("defend_zone is offered OUT of interaction range (it is a zone you travel into)",
          "defend_zone:entity-03" in reg and not raw["interactables"][0]["in_interaction_range"])
    check("charging phase is not explore-only", sorted(reg) != ["explore"], str(sorted(reg)))
    check("defend_zone needs no worth gate", not reg["defend_zone:entity-03"].requires_worth_gate)
    check("defend_zone carries the zone as its target",
          reg["defend_zone:entity-03"].args == {"target_id": "entity-03"})
    check("defend_zone maps to the DEFEND_ZONE command",
          reg["defend_zone:entity-03"].command == "DEFEND_ZONE")


def trace_f2_fight_boss_from_threat_list():
    print("\nF2: fight_boss is built from threat.enemies, not from interactables")
    raw = _charging_obs()
    check("absent while `fight_target` is (phase 4)",
          not any(k.startswith("fight_boss") for k in build_registry(raw, set(CAPS), False)))
    reg = build_registry(raw, set(CAPS) | {"fight_target"}, False)
    check("present once the capability exists", "fight_boss:e-91" in reg, str(sorted(reg)))
    check("targets the boss entity", reg["fight_boss:e-91"].args == {"target_id": "e-91"})
    # A Lemurian is not a boss; only BOSS_CLASSES get a dedicated record.
    raw2 = _charging_obs()
    raw2["threat"]["enemies"][0]["class"] = "melee_swarm"
    check("ordinary enemies get no fight_boss record",
          not any(k.startswith("fight_boss")
                  for k in build_registry(raw2, set(CAPS) | {"fight_target"}, False)))


def _many(n, in_range_at=3):
    raw = copy.deepcopy(RAW_BASE)
    raw["interactables"] = [
        {"entity_id": f"e{i:02d}", "type": "chest", "display_name": "Chest", "cost": 25,
         "cost_type": "gold", "locked": False, "distance": float(60 - i),
         "in_interaction_range": (i == in_range_at),
         "opportunity_token": "opp-x" if i == in_range_at else None,
         "path_reachable": True, "reachability_age_ms": 100} for i in range(n)]
    return raw


def trace_f1_cap_and_ranking():
    print("\nF1 / invariant 9: entity actions are capped, ranked, and drops recorded")
    raw = _many(30)
    dropped = []
    reg = build_registry(raw, set(CAPS), False, dropped)
    ent = [k for k in reg if ":" in k]
    check(f"30 interactables -> capped at {MAX_ENTITY_ACTIONS}",
          len(ent) == MAX_ENTITY_ACTIONS, f"got {len(ent)}")
    check("the in-range action survives the cap",
          any(k.startswith("open_chest") for k in reg), str(sorted(reg)))
    check("everything cut is recorded for Phase 5",
          len(dropped) == 30 - MAX_ENTITY_ACTIONS, f"{len(dropped)} recorded")
    check("drop records name the action and why it ranked low",
          all({"action_id", "rank", "distance"} <= set(d) for d in dropped))
    check("ranking is stable across identical builds",
          sorted(build_registry(raw, set(CAPS), False)) == sorted(reg))

    # Nearest-first among out-of-range candidates: e29 is closest (distance 31).
    out_of_range = [(k, reg[k]) for k in reg if k.startswith("navigate:")]
    kept = {k.split(":")[1] for k, _ in out_of_range}
    check("nearest out-of-range targets are the ones kept",
          "e29" in kept and "e00" not in kept, str(sorted(kept)))
    check("under the cap, nothing is dropped",
          build_registry(_many(5), set(CAPS), False, (d5 := [])) is not None and not d5)




# ---------------------------------------------------------------------------
# Stock adapter (F19/F21) -- cases taken from recordings/human-01, not invented
# ---------------------------------------------------------------------------

def trace_teleporter_classification():
    print("\nF19: teleporter tri-state, from the 5 combos real frames produced")
    tp = lambda ch, boss, pct: {"type": "teleporter", "name": "Teleporter", "cost": 0,
                                "locked": False, "distance": 5.0, "charged": ch,
                                "bossActive": boss, "chargePercent": pct}
    check("idle: not charged, no boss, 0%",
          classify(tp(False, False, 0.0)) == "teleporter_idle")
    check("charging: boss active at 0%",
          classify(tp(False, True, 0.0)) == "teleporter_charging")
    check("charging: boss active mid-charge",
          classify(tp(False, True, 47.3)) == "teleporter_charging")
    # This combo is why `charged` must be tested FIRST -- it really occurs.
    check("charged wins over bossActive (real combo: charged+boss+100%)",
          classify(tp(True, True, 100.0)) == "teleporter_charged")
    check("charged: boss gone, 100%",
          classify(tp(True, False, 100.0)) == "teleporter_charged")
    # Charge progress without an explicit boss flag still means charging, not idle.
    check("charging: progress without bossActive",
          classify(tp(False, False, 12.0)) == "teleporter_charging")


def trace_shrine_classification():
    print("\nF21 (corrected): shrines classify by display name; the fallback fails closed")
    def sh(name):
        return {"type": "shrine", "name": name, "cost": 25, "locked": False,
                "distance": 5.0, "usesRemaining": -1}
    check("Shrine of Chance -> shrine_chance",
          classify(sh("Shrine of Chance")) == "shrine_chance")
    check("Shrine of Blood -> shrine_blood",
          classify(sh("Shrine of Blood")) == "shrine_blood")
    # Identified but no MVP verb: deferred, which is different from unknown.
    for n in ("Shrine of Combat", "Shrine of the Mountain", "Shrine of Order",
              "Shrine of Gold"):
        check(f"{n} identified but has no MVP verb", classify(sh(n)) is None)
    # The mod's own fallback when its substring match finds nothing. Seen for real in
    # recordings/human-01, so it is not hypothetical -- and it could be ANY shrine,
    # including one that costs health.
    check("bare `Shrine` fallback maps to nothing (fails closed)",
          classify(sh("Shrine")) is None)

    obs_int = {"interactables": [sh("Shrine")]}
    ad = adapt({"objective": "exploring", "boss_active": False},
               {"money": 200, "items": []},
               {"enemy_count": 0, "nearest_enemy_distance": 999.0},
               obs_int, observation_id="obs-1", seq=1)
    check("unclassified shrine recorded as unmapped, not dropped silently",
          "shrine" in ad.unmapped, str(ad.unmapped))
    reg = build_registry(ad.raw, set(CAPS), False)
    check("no shrine verb enters the registry from the fallback",
          not any("shrine" in k for k in reg), str(sorted(reg)))
    check("registry is still non-empty (invariant 1)", bool(reg), str(sorted(reg)))

    # A named Shrine of Chance in range DOES become an offerable irreversible action.
    named = sh("Shrine of Chance")
    named["distance"] = 2.0
    ad2 = adapt({"objective": "exploring", "boss_active": False},
                {"money": 200, "items": []},
                {"enemy_count": 0, "nearest_enemy_distance": 999.0},
                {"interactables": [named]}, observation_id="obs-2", seq=2)
    reg2 = build_registry(ad2.raw, set(CAPS), False)
    check("named Shrine of Chance in range is offerable",
          any(k.startswith("use_shrine_chance") for k in reg2), str(sorted(reg2)))
    rec = next(v for k, v in reg2.items() if k.startswith("use_shrine_chance"))
    check("and it is irreversible with a worth gate required",
          rec.commitment == "irreversible" and rec.requires_worth_gate)


def trace_adapter_refuses_to_invent():
    print("\nF18: fields stock cannot supply stay `unknown`, and questions drop out")
    ad = adapt({"objective": "exploring", "boss_active": False, "scene_name": "blackbeach"},
               {"money": 140, "items": [{"name": "Syringe", "count": 2},
                                        {"name": "DrizzlePlayerHelper", "count": 1}]},
               {"enemy_count": 0, "nearest_enemy_distance": 999.0},
               {"interactables": []}, observation_id="obs-1", seq=1)
    for f in ("hazards",):
        check(f"{f} is unknown, not fabricated", ad.banded[f] == UNKNOWN)
    check("self.hp_band is unknown", ad.banded["self"]["hp_band"] == UNKNOWN)
    check("run.survivor is unknown", ad.banded["run"]["survivor"] == UNKNOWN)
    check("run.time_band is unknown", ad.banded["run"]["time_band"] == UNKNOWN)
    check("feasibility is unknown, never assumed true",
          ad.banded["threat"]["reposition_feasible"] == UNKNOWN
          and ad.banded["threat"]["full_withdrawal_feasible"] == UNKNOWN)

    # F17: the 999.0 sentinel must not become a distance band.
    check("999.0 becomes `none`, not `far`",
          ad.banded["threat"]["nearest_enemy_band"] == "none")
    # F13: hidden items are stripped before anything reads the inventory.
    check("DrizzlePlayerHelper is filtered out",
          "DrizzlePlayerHelper" not in ad.banded["build"]["key_items"],
          str(ad.banded["build"]["key_items"]))
    check("real items survive", "Syringe" in ad.banded["build"]["key_items"])

    reg = build_registry(ad.raw, set(CAPS), False)
    req = build_request(ad.banded, reg, BuildContext(CAPS, ad.banded, False, False), CONTRACT)
    check("no question is sendable on stock telemetry (F18)",
          not req.questions, str(sorted(req.questions)))
    check("every omission is recorded with a reason",
          all("reason" in o for o in req.omitted))


def trace_string_coercion():
    print("\nF11/F14/F16: stock numerics arrive as strings and must be coerced")
    check("'30' coerces to 30", num("30") == 30)
    check("'0.9' coerces to 0.9", num("0.9") == 0.9)
    check("None gives the default", num(None, 7) == 7)
    check("garbage gives the default, not a crash", num("n/a", -1) == -1)
    # A string compared raw would order wrongly: "9" > "30" lexicographically.
    check("coercion fixes ordering that raw strings get wrong",
          num("9") < num("30") and "9" > "30")


def _obs(**over):
    o = {"type": "OBSERVATION", "v": 1, "in_run": True,
         "run": {"survivor": "CommandoBody", "difficulty": "Easy",
                 "difficulty_coefficient": 1.1, "elapsed_s": 125.4,
                 "stage_clear_count": 0, "scene": "blackbeach2"},
         "self": {"hp": 143, "hp_max": 143, "shield": 0, "shield_max": 0,
                  "barrier": 0, "alive": True},
         "hazards": {"taking_environmental_damage": False,
                     "environmental_damage_age_s": None, "active_dots": [],
                     "hostile_projectiles_nearby": 0, "hostile_projectiles_radius_m": 15,
                     "in_hazard_volume": False, "in_hazard_volume_covers": "void_fog"}}
    for path, v in over.items():
        blk, key = path.split("__")
        o[blk][key] = v
    return o


def trace_adapter_reads_observation():
    print("\nBuild 2: QUERY_OBSERVATION fills run, self and hazards; nothing is invented")
    stock = ({"objective": "exploring", "boss_active": False, "scene_name": "blackbeach2"},
             {"money": 15, "items": []}, {"enemy_count": 0, "nearest_enemy_distance": 999.0},
             {"interactables": []})

    def banded(obs):
        return adapt(*stock, observation_id="o", seq=1, observation=obs).banded

    b = banded(_obs())
    check("survivor loses the Body suffix", b["run"]["survivor"] == "Commando")
    check("DifficultyIndex `Easy` becomes Drizzle", b["run"]["difficulty"] == "Drizzle")
    check("2 min in is `plenty`", b["run"]["time_band"] == "plenty")
    check("full hp is `healthy`", b["self"]["hp_band"] == "healthy")
    check("hp_trend stays unknown (needs history)", b["self"]["hp_trend"] == UNKNOWN)
    check("difficulty_band stays unknown (no cut points)",
          b["run"]["difficulty_band"] == UNKNOWN)
    check("hazards become a measured block",
          b["hazards"] == {"taking_environmental_damage": False, "active_dots": [],
                           "projectiles_near": "none", "in_void_fog": False}, str(b["hazards"]))

    check("25 min in is `pressing`",
          banded(_obs(run__elapsed_s=1500))["run"]["time_band"] == "pressing")
    check("29% combined health is `low` (the mod's 0.30)",
          banded(_obs(self__hp=41.4))["self"]["hp_band"] == "low")
    check("shield counts toward combined health",
          banded(_obs(self__hp=50, self__shield=100, self__shield_max=100))
          ["self"]["hp_band"] == "hurt")
    check("overkill hp with alive:false is `dead`, not a negative fraction (fork-06)",
          banded(_obs(self__hp=-31.2, self__alive=False))["self"]["hp_band"] == "dead")
    check("3 projectiles within the radius is `many`",
          banded(_obs(hazards__hostile_projectiles_nearby=3))["hazards"]["projectiles_near"]
          == "many")

    # Honesty: `in_void_fog` is only a fact if the mod says that is what it covers.
    b2 = banded(_obs(hazards__in_hazard_volume_covers="something_else"))
    check("unrecognised hazard-volume coverage stays unknown",
          b2["hazards"]["in_void_fog"] == UNKNOWN)

    for label, obs in [("no frame", None), ("in_run false", {"type": "OBSERVATION",
                                                               "in_run": False})]:
        b3 = banded(obs)
        check(f"{label}: hp, time and hazards stay unknown",
              b3["hazards"] == UNKNOWN and b3["self"]["hp_band"] == UNKNOWN
              and b3["run"]["time_band"] == UNKNOWN)

    ad = adapt(*stock, observation_id="o", seq=1, observation=_obs())
    reg = build_registry(ad.raw, set(CAPS), False)
    req = build_request(ad.banded, reg, BuildContext(CAPS, ad.banded, False, False), CONTRACT)
    check("with an observation, next_action is sendable on stock + build 2 (F30)",
          "next_action" in req.questions, str(req.omitted))
    check("and no `unknown` is on the wire", "unknown" not in json.dumps(req.wire()))


def trace_replay_pairs_observations():
    print("\nReplay: OBSERVATION frames from their own stream are paired with ticks")
    from eval.replay_stock import OBS_MAX_AGE_NS, sessions, ticks
    sweep = [{"type": "OBJECTIVE", "objective": "exploring"}, {"type": "INVENTORY", "money": 0},
             {"type": "COMBAT_STATUS", "enemy_count": 0}, {"type": "INTERACTABLES",
                                                           "interactables": []}]
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        other = [(0, {"type": "HARNESS_START"})]
        other += [(10 + i, f) for i, f in enumerate(sweep)]                  # tick 1: fresh
        late = 10 + OBS_MAX_AGE_NS + 100
        other += [(late + i, f) for i, f in enumerate(sweep)]                # tick 2: stale
        obs = [(5, {"type": "OBSERVATION", "seq": 1, "in_run": True})]
        for name, rows in (("other.jsonl", other), ("observations.jsonl", obs)):
            (d / name).write_text("".join(json.dumps({"_t_recv_wall_ns": t, "frame": f}) + "\n"
                                          for t, f in rows), encoding="utf-8")
        tk = list(ticks(sessions(d)[-1]))
    check("two ticks assembled", len(tk) == 2, str(len(tk)))
    check("a fresh observation rides with its tick", tk and "OBSERVATION" in tk[0])
    check("a stale one does not", len(tk) == 2 and "OBSERVATION" not in tk[1])


def trace_dead_asks_nothing():
    print("\nF32: nothing is asked of a dead character")
    ad = adapt({"objective": "exploring", "boss_active": False, "scene_name": "blackbeach2"},
               {"money": 15, "items": []}, {"enemy_count": 0, "nearest_enemy_distance": 999.0},
               {"interactables": []}, observation_id="o", seq=1,
               observation=_obs(self__hp=-31.2, self__alive=False))
    reg = build_registry(ad.raw, set(CAPS), False)
    req = build_request(ad.banded, reg, BuildContext(CAPS, ad.banded, False, False), CONTRACT)
    check("dead -> no question at all", req.questions == {}, str(sorted(req.questions)))
    check("every question recorded as omitted for `dead`",
          {o["question"] for o in req.omitted} == set(CONTRACT["questions"])
          and all(o["reason"] == "dead" for o in req.omitted), str(req.omitted)[:200])


def trace_build_identity():
    print("\nbuild.identity: deterministic, from items; unknown when the table cannot tell")
    from brain.build_identity import ITEMS, TIER_WEIGHT, VOTES, NON_VOTING, derive

    def inv(**kw):
        return [{"name": k, "count": v} for k, v in kw.items()]

    check("empty inventory is `undefined`", derive([]) == "undefined")
    check("one white item is `undefined` (below MIN_WEIGHT)", derive(inv(Crowbar=1)) == "undefined")
    check("utility and scrap never vote",
          derive(inv(Bandolier=3, ScrapWhite=5)) == "undefined")
    check("four damage whites are `damage`", derive(inv(Crowbar=2, Syringe=2)) == "damage")
    check("one red outweighs three whites of another tag",
          derive(inv(Behemoth=1, ArmorPlate=3)) == "proc")
    check("an even split is `mixed`, never list order",
          derive(inv(Crowbar=2, ArmorPlate=2)) == "mixed"
          and derive(inv(ArmorPlate=2, Crowbar=2)) == "mixed")
    check("healing and defense both count as `survival`",
          derive(inv(Tooth=2, Bear=2)) == "survival")
    check("mostly unrecognised items -> unknown, not a guess",
          derive(inv(Crowbar=1, NotARealItem=2)) == UNKNOWN)
    check("string counts are coerced (F11)",
          derive([{"name": "Crowbar", "count": "4"}]) == "damage")
    check("every table row uses a known tier and tag",
          all(t in TIER_WEIGHT and (g in VOTES or g in NON_VOTING) for t, g in ITEMS.values()))


def trace_threat_block():
    print("\nBuild 4: the mod's threat block fills elite, classes and focus; strategy stays gated")
    from brain.enemy_class import BODIES, CLASSES, FOCUS_MIN

    def enemy(body, **kw):
        e = {"body": body, "team": "Monster", "is_elite": False, "is_boss": False,
             "is_flying": False, "distance": 12.0, "targeting_player": True}
        e.update(kw)
        return e

    def threat(enemies, elites=0, targeting=1):
        return {"radius_m": 50, "enemy_count": len(enemies), "elite_count": elites,
                "boss_count": 0, "targeting_player_count": targeting,
                "listed": len(enemies), "enemies": enemies}

    stock = ({"objective": "exploring", "boss_active": False, "scene_name": "blackbeach2"},
             {"money": 15, "items": [{"name": "Crowbar", "count": 4}]},
             {"enemy_count": 2, "nearest_enemy_distance": 12.0}, {"interactables": []})

    def run(th):
        o = _obs()
        if th is not None:
            o["threat"] = th
        ad = adapt(*stock, observation_id="o", seq=1, observation=o)
        reg = build_registry(ad.raw, set(CAPS), False)
        return ad.banded["threat"], build_request(
            ad.banded, reg, BuildContext(CAPS, ad.banded, False, False), CONTRACT)

    t, req = run(threat([enemy("BeetleBody"), enemy("WispBody", is_flying=True)]))
    check("classes are named, sorted and de-duplicated",
          t["enemy_classes"] == ["flying_ranged", "melee_swarm"], str(t["enemy_classes"]))
    check("no elites -> elite_present false", t["elite_present"] is False)
    check("one enemy targeting is not `being_focused`", t["being_focused"] is False)
    check("engage_range is now sendable (survivor + identity + classes)",
          "engage_range" in req.questions, str(req.omitted))
    check("strategy still omitted: feasibility is still unknown",
          "strategy" not in req.questions)

    t, _ = run(threat([enemy("GolemBody", is_boss=True)], elites=1, targeting=FOCUS_MIN))
    check("the boss flag wins over the body's own class", t["enemy_classes"] == ["boss"])
    check("an elite count sets elite_present", t["elite_present"] is True)
    check(f"{FOCUS_MIN} targeting is `being_focused`", t["being_focused"] is True)

    t, req = run(threat([enemy("BeetleBody"), enemy("NoSuchBody"), enemy("OtherBody")]))
    check("mostly unrecognised bodies -> classes unknown, not a partial list",
          t["enemy_classes"] == UNKNOWN)
    check("so engage_range is omitted", "engage_range" not in req.questions)

    t, _ = run(threat([]))
    check("no enemies is a measured empty list", t["enemy_classes"] == [])

    t, _ = run(None)
    check("a build-2 frame (no threat block) leaves the three unknown",
          all(t[k] == UNKNOWN for k in ("elite_present", "enemy_classes", "being_focused")))
    check("every table row uses a known class", set(BODIES.values()) <= set(CLASSES))


def trace_full_interactables():
    print("\nBuild 6: the mod's full-stage interactables replace stock's cone; IDs are stable")
    from brain.interactable_type import REGISTRY_TYPE, RULES, classify as ctype

    check("stage variants match their stem", ctype("ShrineChanceSandy") == "shrine_chance"
          and ctype("ShrineBloodSnowy") == "shrine_blood")
    check("a money barrel is a barrel, an equipment barrel is not",
          ctype("Barrel1") == "barrel" and ctype("EquipmentBarrel") == "equipment_barrel")
    check("printers and terminals by prefab", ctype("DuplicatorLarge") == "printer"
          and ctype("MultiShopTerminal") == "shop_terminal")
    check("Chest1 is not Chest2", ctype("Chest1") == "chest_small" and ctype("Chest2") == "chest_large")
    check("unknown prefab stays unclassified", ctype("SomethingNew") is None)
    check("Unity's numbered copies match (fork-10)", ctype("NewtStatue (1)") == "newt_altar")
    from brain.interactable_type import shop_item_name
    from brain.build_identity import ITEMS as ITEM_TABLE
    check("shop item prefix is stripped to the item table's name (fork-10)",
          shop_item_name("ItemIndex.BossDamageBonus") == "BossDamageBonus"
          and "BossDamageBonus" in ITEM_TABLE)
    check("every registry type is a table type",
          set(REGISTRY_TYPE) <= {t for _, t in RULES})

    def item(i, prefab, dist, cost=25, ct="Money", **kw):
        d = {"id": i, "kind": "purchase", "prefab": prefab, "display": prefab, "cost": cost,
             "cost_type": ct, "distance": dist, "height": 0.0}
        d.update(kw)
        return d

    o = _obs()
    o["interactables"] = {"total": 5, "unavailable": 2, "listed": 5, "items": [
        item(111, "Chest1", 3.0),
        item(222, "Barrel1", 8.0, cost=0, ct="None", kind="barrel"),
        item(333, "Drone1Broken", 20.0, cost=40),
        item(444, "MultiShopTerminal", 30.0, shop_hidden=False, shop_item="Crowbar"),
        item(555, "MysteryThing", 40.0),
    ]}
    stock_ix = {"interactables": [
        {"type": "chest", "name": "Basic Chest", "cost": 25, "distance": 90.0},
        {"type": "teleporter", "name": "Teleporter", "distance": 120.0, "charged": False}]}
    stock = ({"objective": "exploring", "boss_active": False, "scene_name": "golemplains"},
             {"money": 100, "items": []}, {"enemy_count": 0, "nearest_enemy_distance": 999.0},
             stock_ix)
    ad = adapt(*stock, observation_id="o", seq=1, observation=o)
    ids = [i["entity_id"] for i in ad.raw["interactables"]]
    check("stock's cone chest is replaced; its teleporter is kept",
          any(i.startswith("teleporter") for i in ids) and "chest-0" not in ids, str(ids))
    check("the chest carries the game's instance ID", "chest_small@111" in ids, str(ids))
    check("barrel, drone and terminal are visible but not offered",
          sorted(ad.visible_no_verb) == ["barrel", "drone", "shop_terminal"],
          str(ad.visible_no_verb))
    check("the unknown prefab is reported, not guessed", ad.unmapped == ["prefab:MysteryThing"])

    reg = build_registry(ad.raw, set(CAPS), False)
    check("the in-range chest is offerable under its stable ID",
          any(k.startswith("open_chest") and "111" in k for k in reg), str(sorted(reg)))

    ad2 = adapt(*stock, observation_id="o", seq=2, observation=_obs())
    check("without the block, stock's list is used unchanged",
          [i["entity_id"] for i in ad2.raw["interactables"]] == ["chest-0", "teleporter-1"],
          str([i["entity_id"] for i in ad2.raw["interactables"]]))


def trace_adapter_on_real_recording():
    print("\nReplay: real recorded frames through the real registry")
    rec = Path(__file__).resolve().parents[1] / "recordings" / "human-01"
    if not rec.exists():
        check("recording present (skipped: no recordings/human-01)", True)
        return
    from eval.replay_stock import sessions, ticks
    sess = sessions(rec)
    frames = sess[-1]
    n = ok = 0
    phases = set()
    verbs = set()
    for tick in ticks(frames):
        n += 1
        ad = adapt(tick.get("OBJECTIVE", {}), tick.get("INVENTORY", {}),
                   tick.get("COMBAT_STATUS", {}), tick.get("INTERACTABLES", {}),
                   observation_id=f"obs-{n}", seq=n)
        phases.add(ad.banded["objective"]["phase"])
        try:
            reg = build_registry(ad.raw, set(CAPS), False)
            verbs.update(k.split(":")[0] for k in reg)
            ok += 1
        except AssertionError:
            pass
    check(f"replayed real ticks ({n})", n > 500, f"only {n}")
    check("registry invariants held on every real tick", ok == n, f"{ok}/{n}")
    check("all three objective phases exercised",
          {"pre_teleporter", "charging", "charged"} <= phases, str(sorted(phases)))
    check("defend_zone reachable on real charging telemetry (F2 + F19)",
          "defend_zone" in verbs, str(sorted(verbs)))
    check("advance_stage reachable on real charged telemetry",
          "advance_stage" in verbs, str(sorted(verbs)))
    check("entity cap never exceeded on real data", ok == n)


TRACES = [
    trace_omit_never_null, trace_empty_is_not_unknown, trace_capability_gating,
    trace_counsel_flag, trace_worth_gate_pairing, trace_registry_option_parity,
    trace_no_blank_options, trace_condition_grammar, trace_no_arithmetic_on_the_wire,
    trace_recorder_roundtrip, trace_recorder_detects_gaps, trace_recorder_out_of_order,
    trace_recorder_append_and_pin,
    trace_f2_charging_offers_defend_zone, trace_f2_fight_boss_from_threat_list,
    trace_f1_cap_and_ranking,
    trace_teleporter_classification, trace_shrine_classification,
    trace_adapter_refuses_to_invent, trace_string_coercion,
    trace_adapter_reads_observation, trace_replay_pairs_observations,
    trace_dead_asks_nothing, trace_build_identity, trace_threat_block,
    trace_full_interactables,
    trace_adapter_on_real_recording,
]

if __name__ == "__main__":
    for t in TRACES:
        t()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED: " + ", ".join(FAIL))
    sys.exit(1 if FAIL else 0)
