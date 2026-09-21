"""Traces for the carried-forward P0/P1 findings.

Each test names the finding it supplies acceptance evidence for.
Run: python eval/test_lifecycle_traces.py
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
from eval.test_chest_trace import CAPS, check, jev_choice, noul


def _nav(mod, life):
    obs = mod.observe()
    reg = build_registry(obs, CAPS, life.has_active_task())
    ans = {"next_action": jev_choice(selectable(reg, life.has_active_task()),
                                     "navigate:entity-47")}
    return dispatch(obs, ans, reg, life, CAPS).commands[0]


def trace_reconnect():
    print("\nP0 protocol 5.2 + 3.1: reconnect namespaces and receipt registration")
    mod = FakeMod()
    mod.add(Entity("entity-47", "chest", cost=25, distance=31.0))
    life = Lifecycle()

    nav = _nav(mod, life)
    life.on_event(nav.command_id, mod.send(nav)["state"])
    check("task active before disconnect", life.task_occupied())

    old_session = mod.session_id
    new_session = mod.reconnect()
    check("reconnect yields a new session id", new_session != old_session)
    check("replay namespaces are separate",
          mod.ledger.watermarks.get(new_session, -1) == -1
          and mod.ledger.highest_seq.get(new_session, -1) == -1,
          f"wm={mod.ledger.watermarks}, hi={mod.ledger.highest_seq}")

    r = mod.ledger.reconcile(mod.run_id, nav.command_id, old_session)
    check("execution identity retained across reconnect -> active",
          r["outcome"] == "active" and r["state"] == "navigating", str(r))

    # A command that arrived but has not been enqueued for execution.
    mod2 = FakeMod()
    mod2.add(Entity("entity-47", "chest", cost=25, distance=31.0))
    queued = _nav(mod2, Lifecycle())
    mod2.receive_only(queued)
    r2 = mod2.ledger.reconcile(mod2.run_id, queued.command_id, mod2.session_id)
    check("received-but-queued does NOT report not_received",
          r2["outcome"] != "not_received", str(r2))
    check("received-but-queued reports received_pending",
          r2["outcome"] == "received_pending", str(r2))

    r3 = mod2.ledger.reconcile(mod2.run_id, "cmd-never-sent", mod2.session_id)
    check("genuinely absent command is provably not_received",
          r3["outcome"] == "not_received", str(r3))


def trace_pre_effect_checks():
    print("\nP0 protocol 8: live range, affordability, target agreement -> zero effects")

    # Insufficient funds, distinct from the authorized cost ceiling.
    mod = FakeMod(gold=10)
    mod.add(Entity("entity-47", "chest", cost=25, distance=2.0))
    obs = mod.observe()
    reg = build_registry(obs, CAPS, False)
    cmd = dispatch(obs, {"next_action": jev_choice(selectable(reg, False),
                                                   "open_chest:entity-47")},
                   reg, Lifecycle(), CAPS).commands[0]
    ev = mod.send(cmd)
    check("insufficient funds -> unaffordable", ev["reason"] == "unaffordable", str(ev))
    check("insufficient funds -> zero effects", len(mod.effects) == 0)

    # args.target_id disagreeing with execution_contract.target_id.
    mod2 = FakeMod()
    mod2.add(Entity("entity-47", "chest", cost=25, distance=2.0))
    mod2.add(Entity("entity-99", "chest", cost=25, distance=2.0))
    obs2 = mod2.observe()
    reg2 = build_registry(obs2, CAPS, False)
    c = dispatch(obs2, {"next_action": jev_choice(selectable(reg2, False),
                                                  "open_chest:entity-47")},
                 reg2, Lifecycle(), CAPS).commands[0]
    c.args["target_id"] = "entity-99"          # tamper: args now disagree with contract
    ev2 = mod2.send(c)
    check("target mismatch -> contract_target_mismatch",
          ev2["reason"] == "contract_target_mismatch", str(ev2))
    check("target mismatch -> zero effects", len(mod2.effects) == 0)

    # Live range revalidated at effect time, not remembered from the observation.
    mod3 = FakeMod()
    mod3.add(Entity("entity-47", "chest", cost=25, distance=2.0))
    obs3 = mod3.observe()
    reg3 = build_registry(obs3, CAPS, False)
    c3 = dispatch(obs3, {"next_action": jev_choice(selectable(reg3, False),
                                                   "open_chest:entity-47")},
                  reg3, Lifecycle(), CAPS).commands[0]
    mod3.entities["entity-47"].distance = 40.0
    ev3 = mod3.send(c3)
    check("live range checked at effect -> out_of_range",
          ev3["reason"] == "out_of_range", str(ev3))
    check("out of range -> zero effects", len(mod3.effects) == 0)


def trace_single_submission():
    print("\nP0 catalog 7: delayed accept across decisions -> exactly one task submitted")
    mod = FakeMod()
    mod.add(Entity("entity-47", "chest", cost=25, distance=31.0))
    life = Lifecycle()

    nav = _nav(mod, life)
    check("first decision reserves the slot as submitted",
          life.task is not None and life.task.state is State.SUBMITTED)

    # Deliberately withhold the mod's `accepted` event across two further decisions.
    submitted = []
    for _ in range(2):
        mod.tick(120)
        obs = mod.observe()
        reg = build_registry(obs, CAPS, life.has_active_task())
        opts = selectable(reg, life.has_active_task())
        ans = {"next_action": jev_choice(opts, "navigate:entity-47")}
        out = dispatch(obs, ans, reg, life, CAPS)
        submitted += [c for c in out.commands if c.command == "NAVIGATE_TARGET"]

    check("no second task submitted while unacknowledged", submitted == [], str(submitted))
    check("slot still held by the original command",
          life.task is not None and life.task.command_id == nav.command_id)


def trace_sentinel_during_active_task():
    print("\nP1 catalog 7: sentinel endorsement recorded while a task is active")
    mod = FakeMod()
    mod.add(Entity("entity-47", "chest", cost=25, distance=31.0))
    life = Lifecycle()
    nav = _nav(mod, life)
    life.on_event(nav.command_id, mod.send(nav)["state"])

    mod.tick(150)
    obs = mod.observe()
    reg = build_registry(obs, CAPS, life.has_active_task())
    opts = selectable(reg, life.has_active_task())
    check("continue_current_action offered while navigating",
          "continue_current_action" in opts)
    dispatch(obs, {"next_action": jev_choice(opts, "continue_current_action")},
             reg, life, CAPS)
    check("endorsement journaled despite occupied slot",
          any(j.get("intent") == "continue_current_action" for j in life.journal),
          str(life.journal))


def trace_suspension_restores_state():
    print("\nLifecycle: suspension restores the interrupted state, not a generic navigating")
    mod = FakeMod()
    mod.add(Entity("entity-47", "chest", cost=25, distance=2.0))
    life = Lifecycle()

    obs = mod.observe()
    reg = build_registry(obs, CAPS, False)
    cmd = dispatch(obs, {"next_action": jev_choice(selectable(reg, False),
                                                   "open_chest:entity-47")},
                   reg, Lifecycle(), CAPS).commands[0]
    # Drive the brain-side lifecycle directly: an ACCEPTED interaction, not navigation.
    life.reserve(cmd.command_id, cmd.action_id, cmd.command)
    life.on_event(cmd.command_id, State.ACCEPTED)
    check("interaction is accepted, not navigating", life.task.state is State.ACCEPTED)

    life.on_event(cmd.command_id, State.SUSPENDED)
    check("suspended records prior state",
          life.task.state is State.SUSPENDED and life.task.prior_state is State.ACCEPTED)

    life.on_event(cmd.command_id, State.NAVIGATING)      # the mod's `resumed` event
    check("resume restores ACCEPTED, does not become NAVIGATING",
          life.task.state is State.ACCEPTED, f"got {life.task.state}")


def trace_cancel_completion_race():
    print("\nP1 protocol 11 / T12: completion racing cancellation clears the slot")
    mod = FakeMod()
    mod.add(Entity("entity-47", "chest", cost=25, distance=31.0))
    life = Lifecycle()
    nav = _nav(mod, life)
    life.on_event(nav.command_id, mod.send(nav)["state"])

    mod.tick(150)
    obs = mod.observe()
    reg = build_registry(obs, CAPS, life.has_active_task())
    opts = selectable(reg, life.has_active_task())
    out = dispatch(obs, {"next_action": jev_choice(opts, "continue_current_action"),
                         "continue_is_worthwhile": noul(0.05)}, reg, life, CAPS)
    check("cancel emitted", any(c.command == "CANCEL" for c in out.commands))
    check("slot held while cancellation is pending", life.task_occupied())

    # The mod completes the navigation before the cancel takes effect.
    ev = mod.arrive(nav.command_id, "entity-47")
    life.on_event(nav.command_id, ev["state"], ev["reason"])
    check("slot released by ANY authoritative terminal, not only `cancelled`",
          not life.task_occupied())
    check("terminal recorded as completed, not falsely cancelled",
          life.journal[-1]["terminal"] == "completed"
          and life.journal[-1]["cancel_was_pending"] is True,
          str(life.journal[-1]))


TRACES = [trace_reconnect, trace_pre_effect_checks, trace_single_submission,
          trace_sentinel_during_active_task, trace_suspension_restores_state,
          trace_cancel_completion_race]

if __name__ == "__main__":
    from eval.test_chest_trace import FAIL, PASS
    for t in TRACES:
        t()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED: " + ", ".join(FAIL))
    sys.exit(1 if FAIL else 0)
