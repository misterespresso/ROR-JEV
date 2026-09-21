"""End-to-end trace: distant chest -> navigate -> arrive -> open -> one effect.

This is the trace the review asked for first, run through the REAL dispatcher and the
headless fake mod. Plus the regression traces for the contract decisions it raised.

Run: python eval/test_chest_trace.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from brain.contract import State
from brain.lifecycle import Lifecycle
from brain.policy import dispatch
from brain.registry import build_registry, selectable
from eval.fake_mod import Entity, FakeMod

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'  <- ' + detail if detail and not cond else ''}")


def jev_choice(options, pick, p=0.9):
    """Stub a JEV Choice response with a calibrated-looking distribution."""
    rest = (1.0 - p) / max(len(options) - 1, 1)
    probs = {o: (p if o == pick else rest) for o in options}
    return {"type": "choice", "choice": pick, "confidence": p, "probabilities": probs}


def noul(v):
    return {"type": "noul", "noul": v}


CAPS = {"observation_v2", "navigate_target", "interact_target", "search",
        "set_posture", "disengage", "defend_zone"}


def trace_chest():
    print("\nT6 / primary trace: navigate -> arrive -> open -> one effect")
    mod = FakeMod()
    mod.add(Entity("entity-47", "chest", cost=25, distance=31.0))
    life = Lifecycle()

    # --- observe distant chest -------------------------------------------
    obs = mod.observe()
    reg = build_registry(obs, CAPS, life.has_active_task())
    opts = selectable(reg, life.has_active_task())
    check("out of range -> navigate offered, open NOT offered",
          "navigate:entity-47" in reg and "open_chest:entity-47" not in reg, str(sorted(reg)))
    check("continue_current_action absent with no active task",
          "continue_current_action" not in opts)

    ans = {"next_action": jev_choice(opts, "navigate:entity-47")}
    out = dispatch(obs, ans, reg, life, CAPS)
    nav = out.commands[0]
    check("NAVIGATE_TARGET emitted", nav.command == "NAVIGATE_TARGET")
    check("slot reserved at enqueue (submitted)",
          life.task is not None and life.task.state is State.SUBMITTED)

    ev = mod.send(nav)
    life.on_event(nav.command_id, ev["state"])
    check("mod accepted -> navigating", life.task.state is State.NAVIGATING)

    # --- a second decision arrives mid-navigation ------------------------
    mod.tick(200)
    obs2 = mod.observe()
    reg2 = build_registry(obs2, CAPS, life.has_active_task())
    opts2 = selectable(reg2, life.has_active_task())
    check("continue_current_action offered while task active",
          "continue_current_action" in opts2)
    ans2 = {"next_action": jev_choice(opts2, "continue_current_action")}
    out2 = dispatch(obs2, ans2, reg2, life, CAPS)
    check("sentinel emits no command", not any(c.command != "SET_POSTURE" for c in out2.commands))
    check("sentinel journaled during active task (item 6)",
          any(j.get("intent") == "continue_current_action" for j in life.journal))

    # --- arrive ----------------------------------------------------------
    mod.tick(3000)
    ev = mod.arrive(nav.command_id, "entity-47")
    life.on_event(nav.command_id, ev["state"], ev["reason"])
    check("navigation terminal -> slot released", not life.task_occupied())

    # --- fresh observation, now in range ---------------------------------
    obs3 = mod.observe()
    reg3 = build_registry(obs3, CAPS, life.has_active_task())
    check("in range -> open_chest offered", "open_chest:entity-47" in reg3)
    check("in range -> navigate no longer offered", "navigate:entity-47" not in reg3)
    rec = reg3["open_chest:entity-47"]
    check("execution contract carries cost_limit + token",
          rec.cost_limit == {"type": "gold", "amount": 25}
          and rec.opportunity_token is not None)

    opts3 = selectable(reg3, life.has_active_task())
    ans3 = {"next_action": jev_choice(opts3, "open_chest:entity-47")}
    out3 = dispatch(obs3, ans3, reg3, life, CAPS)
    interact = [c for c in out3.commands if c.command == "INTERACT_TARGET"][0]
    gold_before = mod.gold
    ev = mod.send(interact)
    life.on_event(interact.command_id, ev["state"], ev["reason"])
    check("interaction completed", ev["state"] is State.COMPLETED)
    check("exactly one effect", len(mod.effects) == 1, str(mod.effects))
    check("gold debited once", mod.gold == gold_before - 25)

    # --- replay produces no second effect --------------------------------
    ev_replay = mod.send(interact)
    check("replay is idempotent, no second effect",
          len(mod.effects) == 1 and ev_replay["reason"] == "idempotent_replay")
    check("token consumed -> action no longer offered",
          "open_chest:entity-47" not in build_registry(mod.observe(), CAPS, False))


def trace_two_observations_one_token():
    print("\nItem 3: two observations before one use -> exactly one effect")
    mod = FakeMod()
    mod.add(Entity("entity-51", "shrine_chance", cost=30, distance=2.0))
    life = Lifecycle()

    a = mod.observe()
    mod.tick(150)
    b = mod.observe()
    ta = a["interactables"][0]["opportunity_token"]
    tb = b["interactables"][0]["opportunity_token"]
    check("same unchanged opportunity -> same token", ta == tb, f"{ta} vs {tb}")

    cmds = []
    for o in (a, b):
        reg = build_registry(o, CAPS, False)
        opts = selectable(reg, False)
        ans = {"next_action": jev_choice(opts, "use_shrine_chance:entity-51"),
               "worth_use_shrine_chance:entity-51": noul(0.9)}
        cmds.append(dispatch(o, ans, reg, Lifecycle(), CAPS).commands[0])

    mod.send(cmds[0])
    ev = mod.send(cmds[1])
    # The shrine is repeatable, so `not_interactable` cannot mask this: the token is the
    # only thing standing between two distinct command_ids and two effects.
    check("second command rejected BY THE TOKEN, not incidentally",
          ev["reason"] == "opportunity_consumed", str(ev))
    check("exactly one effect from two observations", len(mod.effects) == 1, str(mod.effects))

    # A genuinely new opportunity (game-side reset) issues a new token and is usable.
    new_token = mod.tokens.renew(mod.scene_gen, "entity-51", "use_shrine_chance")
    check("renewed opportunity mints a different token", new_token != ta)
    c = mod.observe()
    reg = build_registry(c, CAPS, False)
    ans = {"next_action": jev_choice(selectable(reg, False), "use_shrine_chance:entity-51"),
           "worth_use_shrine_chance:entity-51": noul(0.9)}
    mod.send(dispatch(c, ans, reg, Lifecycle(), CAPS).commands[0])
    check("deliberate second use succeeds with a new token", len(mod.effects) == 2,
          str(mod.effects))


def trace_gates_and_range():
    print("\nT1 / item 4: fail-closed gate, range and cost enforcement")
    mod = FakeMod()
    mod.add(Entity("entity-51", "shrine_chance", cost=30, distance=2.0))
    obs = mod.observe()
    reg = build_registry(obs, CAPS, False)
    opts = selectable(reg, False)

    # T1: missing required worth gate
    life = Lifecycle()
    ans = {"next_action": jev_choice(opts, "use_shrine_chance:entity-51")}
    out = dispatch(obs, ans, reg, life, CAPS)
    check("T1 missing worth gate -> no command",
          not any(c.command == "INTERACT_TARGET" for c in out.commands))
    check("T1 journaled as gate_missing",
          any(n.get("not_authorized") == "gate_missing" for n in out.notes), str(out.notes))

    # T32: cost above the authorized ceiling, still affordable
    life2 = Lifecycle()
    ans2 = {"next_action": jev_choice(opts, "use_shrine_chance:entity-51"),
            "worth_use_shrine_chance:entity-51": noul(0.9)}
    cmd = dispatch(obs, ans2, reg, life2, CAPS).commands[0]
    mod.entities["entity-51"].cost = 60          # price rose; 140 gold still covers it
    ev = mod.send(cmd)
    check("T32 cost_limit_exceeded while still affordable",
          ev["reason"] == "cost_limit_exceeded", str(ev))
    check("T32 no effect", len(mod.effects) == 0)

    # item 4: out-of-range interaction is rejected, never compensated by navigating
    mod2 = FakeMod()
    mod2.add(Entity("entity-60", "chest", cost=25, distance=2.0))
    o = mod2.observe()
    r = build_registry(o, CAPS, False)
    c = dispatch(o, {"next_action": jev_choice(selectable(r, False), "open_chest:entity-60")},
                 r, Lifecycle(), CAPS).commands[0]
    mod2.entities["entity-60"].distance = 40.0    # walked away before the command landed
    ev = mod2.send(c)
    check("out-of-range interaction rejected", ev["reason"] == "out_of_range", str(ev))
    check("no navigation substituted", len(mod2.effects) == 0)


def trace_arbitration():
    print("\nItem 6: continue vs abandon arbitration is recorded")
    mod = FakeMod()
    mod.add(Entity("entity-47", "chest", cost=25, distance=31.0))
    life = Lifecycle()
    obs = mod.observe()
    reg = build_registry(obs, CAPS, False)
    nav = dispatch(obs, {"next_action": jev_choice(selectable(reg, False), "navigate:entity-47")},
                   reg, life, CAPS).commands[0]
    life.on_event(nav.command_id, mod.send(nav)["state"])

    obs2 = mod.observe()
    reg2 = build_registry(obs2, CAPS, life.has_active_task())
    opts2 = selectable(reg2, life.has_active_task())
    ans = {"next_action": jev_choice(opts2, "continue_current_action"),
           "continue_is_worthwhile": noul(0.10)}
    out = dispatch(obs2, ans, reg2, life, CAPS)
    check("disagreement recorded as explicit arbitration",
          any(n.get("arbitration") == "cancel_overrides_continue" for n in out.notes),
          str(out.notes))
    check("cancel emitted with jev: origin, not watchdog:",
          any(c.command == "CANCEL" and c.origin.startswith("jev:continue_is_worthwhile")
              for c in out.commands))
    check("slot still occupied until terminal arrives (T12)", life.task_occupied())

    # A racing completion clears the locally pending cancellation.
    ev = mod.arrive(nav.command_id, "entity-47")
    life.on_event(nav.command_id, ev["state"], ev["reason"])
    check("racing completed clears pending cancel and frees slot",
          not life.task_occupied()
          and life.journal[-1]["terminal"] == "completed"
          and life.journal[-1]["cancel_was_pending"] is True)


def trace_identity():
    print("\nItem 2: active command survives watermark advance")
    from brain.identity import Ledger
    led = Ledger("run-1", window=3)
    led.register_receipt("s1", 10, "cmd-a")
    _, active = led.admit("run-1", "cmd-a", "s1", 10, {"x": 1})   # stays active
    for i, seq in enumerate(range(11, 18)):
        cid = f"cmd-{seq}"
        led.register_receipt("s1", seq, cid)
        v, rec = led.admit("run-1", cid, "s1", seq, {"x": seq})
        rec.state = State.COMPLETED
        led._evict("s1")
    check("watermark never passes the live command",
          led.watermarks.get("s1", -1) < 10, f"wm={led.watermarks.get('s1')}")
    check("old active command still reconciles as active",
          led.reconcile("run-1", "cmd-a", "s1")["outcome"] == "active",
          str(led.reconcile("run-1", "cmd-a", "s1")))
    check("unknown command in a live window is provably not_received",
          led.reconcile("run-1", "cmd-ghost", "s1")["outcome"] == "not_received")


if __name__ == "__main__":
    trace_chest()
    trace_two_observations_one_token()
    trace_gates_and_range()
    trace_arbitration()
    trace_identity()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED: " + ", ".join(FAIL))
    sys.exit(1 if FAIL else 0)
