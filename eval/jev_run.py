"""JEV drives: poll the game, ask JEV, carry out its picks. The first closed loop.

Every `--decide-every` seconds (default 2) while the character is alive in a run:

    stock queries + QUERY_OBSERVATION -> adapter -> registry -> built request
      -> JEV (brain/jev_client.py) -> dispatcher (brain/policy.py)
      -> fork commands (brain/stock_bridge.py) -> the game

Nothing scripted decides. When JEV is not asked (dead, lobby, loading) or a call fails, the
tick sends nothing and the mod keeps doing what it was doing. Every request, answer, and
command is recorded, provenance `jev:`, so a run can be replayed and audited.

Needs fork build 8+ (INTERACT_ID) and a key in JEV_KEY or TYPESAFE_API_KEY.

Usage (start this first, then launch the game and start a run):

    python -m eval.jev_run --run-id jev-01 --survivor Commando --difficulty Drizzle
    python -m eval.jev_run --run-id jev-02 --dry-run      # ask JEV, send nothing
"""
from __future__ import annotations

import argparse
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from brain.catalog import BuildContext, build_request, load_contract
from brain.contract import State
from brain.jev_client import JevError, ask, have_key
from brain.lifecycle import Lifecycle
from brain.policy import dispatch
from brain.recorder import Recorder
from brain.registry import build_registry
from brain.stock_adapter import adapt
from brain.stock_bridge import to_mod
from eval import gonogo_probe as gp
from eval.fixtures import MILESTONE_1_CAPABILITIES
from eval.profile_config import ai_control_enabled, block_repeat_damage
from eval.record_run import RunState, poll

#: Documented price (docs/01-RESEARCH.md): $0.042 per million input tokens, output free.
USD_PER_INPUT_TOKEN = 0.042 / 1_000_000

#: A task the mod never reports on is released after this long (F25).
TASK_TIMEOUT_S = 30.0

#: Back off this long after a rate-limit or overload answer.
BACKOFF_S = {429: 5.0, 529: 5.0}

EVENT_STATE = {"action_started": State.NAVIGATING, "action_complete": State.COMPLETED,
               "action_failed": State.FAILED}


class Task:
    """What the loop knows about the one running task, beyond the Lifecycle slot."""

    def __init__(self, command_id: str, label: str, target_iid: str | None, now: float):
        self.command_id, self.label, self.target_iid, self.since = (
            command_id, label, target_iid, now)


def available_ids(observation: dict | None) -> set[str] | None:
    """Instance IDs of available interactables in a build-6 frame, or None if absent."""
    block = (observation or {}).get("interactables")
    if not isinstance(block, dict):
        return None
    return {str(it.get("id")) for it in block.get("items") or []}


def main() -> int:
    ap = argparse.ArgumentParser(description="JEV drives the character")
    ap.add_argument("--run-id", default=time.strftime("jev-%Y%m%d-%H%M%S"))
    ap.add_argument("--minutes", type=float, default=15.0)
    ap.add_argument("--decide-every", type=float, default=2.0)
    ap.add_argument("--survivor", default=None)
    ap.add_argument("--difficulty", default=None)
    ap.add_argument("--dry-run", action="store_true", help="ask JEV but send no commands")
    ap.add_argument("--connect-wait", type=float, default=600.0)
    ap.add_argument("--run-wait", type=float, default=600.0)
    a = ap.parse_args()

    print("=" * 78)
    print("JEV RUN - JEV decides, the mod executes")
    print("=" * 78)
    if not have_key():
        print("  !! No key: set JEV_KEY (or TYPESAFE_API_KEY) and start again.")
        return 2
    ai_on, cfg = ai_control_enabled()
    if ai_on is False:
        print(f"  !! EnableAIControl = false in {cfg}. Close the game, set it true, retry.")
        return 2
    print(f"run id   : {a.run_id}   decide every {a.decide_every:g}s   "
          f"{'DRY RUN (nothing sent)' if a.dry_run else 'LIVE'}")
    print(">>> Now launch the game and start a run. Leave this window alone.")
    print()

    contract = load_contract()
    caps = frozenset(MILESTONE_1_CAPABILITIES)
    stop = {"flag": False}

    def _sigint(_s, _f):
        stop["flag"] = True
        print("\n  ...  stopping (Ctrl+C)")

    signal.signal(signal.SIGINT, _sigint)

    with Recorder(a.run_id) as rec:
        p = gp.Probe(rec)
        try:
            if not p.accept(a.connect_wait) or not gp.wait_for_run(p, a.run_wait):
                return 1
            rec.record({"type": "HARNESS_START", "harness": "jev_run", "run_id": a.run_id,
                        "decide_every_s": a.decide_every, "dry_run": a.dry_run,
                        "survivor": a.survivor, "difficulty": a.difficulty,
                        "ai_control": ai_on, "block_repeat_damage": block_repeat_damage()})
            return loop(p, rec, a, contract, caps, stop)
        finally:
            if not p.lost:
                p.command("MODE", "roam")
            p.close()


def loop(p, rec, a, contract, caps, stop) -> int:
    st = RunState()
    life = Lifecycle()
    task: Task | None = None
    seen_events = len(p.events)
    t_end = time.monotonic() + a.minutes * 60
    t_next = 0.0
    backoff_until = 0.0
    last_posture = None
    n = asked = errors = 0
    tokens = 0
    last_line = 0.0
    last_choice = "-"

    while not stop["flag"] and not p.lost and time.monotonic() < t_end:
        now = time.monotonic()
        if now < t_next:
            time.sleep(0.05)
            continue
        t_next = now + a.decide_every
        got = poll(p, st)
        obs = got.get("QUERY_OBSERVATION")

        # --- task bookkeeping: events, then the fallbacks F25 makes necessary -----------
        for ev in p.events[seen_events:]:
            state = EVENT_STATE.get(str(ev.get("event_type")))
            if task and state and str(ev.get("command")) == task.label:
                life.on_event(task.command_id, state, str(ev.get("reason") or "") or None)
                if state in (State.COMPLETED, State.FAILED):
                    rec.record({"type": "HARNESS_NOTE", "note": "task_ended",
                                "label": task.label, "how": ev.get("event_type"),
                                "reason": ev.get("reason")})
                    task = None
        seen_events = len(p.events)
        if task:
            ids = available_ids(obs)
            why = None
            if task.target_iid and ids is not None and task.target_iid not in ids:
                why, state = "target no longer available (used)", State.COMPLETED
            elif now - task.since > TASK_TIMEOUT_S:
                why, state = f"no outcome within {TASK_TIMEOUT_S:g}s", State.FAILED
            if why:
                life.on_event(task.command_id, state, why)
                rec.record({"type": "HARNESS_NOTE", "note": "task_released",
                            "label": task.label, "why": why})
                task = None

        # --- ask JEV ---------------------------------------------------------------------
        if not st.in_game or now < backoff_until or not all(
                q in got for q in ("QUERY_OBJECTIVE", "QUERY_INVENTORY",
                                   "QUERY_COMBAT_STATUS", "QUERY_INTERACTABLES")):
            continue
        n += 1
        ad = adapt(got["QUERY_OBJECTIVE"], got["QUERY_INVENTORY"], got["QUERY_COMBAT_STATUS"],
                   got["QUERY_INTERACTABLES"], observation_id=f"{a.run_id}-{n}", seq=n,
                   observation=obs)
        ad.raw["t_capture_mono_ms"] = int(now * 1000)
        active = life.has_active_task()
        reg = build_registry(ad.raw, set(caps), active)
        req = build_request(ad.banded, reg, BuildContext(caps, ad.banded, active, False),
                            contract)
        if not req.questions:
            continue            # dead, or nothing answerable: ask nothing, send nothing
        try:
            res = ask(req.wire())
        except JevError as e:
            errors += 1
            rec.record({"type": "JEV_ERROR", "tick": n, "status": e.status, "error": str(e)})
            print(f"  !! JEV error {e}")
            if e.status == 401:
                print("  !! key rejected -- stopping")
                return 1
            backoff_until = now + BACKOFF_S.get(e.status or 0, 1.0)
            continue
        asked += 1
        tokens += int(res.usage.get("input_tokens") or 0)
        rec.record({"type": "JEV_EXCHANGE", "tick": n, "request": req.wire(),
                    "omitted": req.omitted, "answers": res.answers, "usage": res.usage,
                    "latency_ms": round(res.latency_ms, 1)})

        # --- dispatch and execute --------------------------------------------------------
        d = dispatch(ad.raw, res.answers, reg, life, set(caps))
        for note in d.notes:
            rec.record({"type": "HARNESS_NOTE", "note": "dispatch", **note})
        sel = res.answers.get("next_action") or {}
        last_choice = f"{sel.get('choice', '-')} p={max((sel.get('probabilities') or {0: 0}).values()):.2f}" if sel else "-"
        for cmd in d.commands:
            mc = to_mod(cmd)
            if mc is None:
                rec.record({"type": "HARNESS_NOTE", "note": "unsupported_command",
                            "command": cmd.command, "args": cmd.args})
                if life.task and life.task.command_id == cmd.command_id:
                    life.on_event(cmd.command_id, State.REJECTED, "unsupported")
                continue
            if mc.command == "STRATEGY":
                if mc.args == last_posture:
                    continue
                last_posture = mc.args
            rec.record({"type": "HARNESS_DECISION", "origin": cmd.origin,
                        "action_id": cmd.action_id, "command": mc.command, "args": mc.args,
                        "dry_run": a.dry_run})
            if not a.dry_run:
                p.command(mc.command, mc.args)
            if life.task and life.task.command_id == cmd.command_id:
                if mc.holds_slot and not a.dry_run:
                    life.on_event(cmd.command_id, State.ACCEPTED)
                    iid = mc.args if mc.command == "INTERACT_ID" else None
                    task = Task(cmd.command_id, mc.label, iid, now)
                else:
                    # A standing MODE, or a dry run: nothing will end it, so free the slot now
                    # and let JEV re-decide next tick.
                    life.on_event(cmd.command_id, State.COMPLETED, "standing behaviour")

        if now - last_line >= 5.0:
            print(f"  {st.scene_name or '?':<14} gold={st.gold:<5} asked={asked:<4} "
                  f"err={errors:<3} task={'busy' if task else 'free'}  "
                  f"next_action={last_choice}   ~${tokens * USD_PER_INPUT_TOKEN:.4f}")
            last_line = now

    print(f"  done: asked JEV {asked} times, {errors} errors, {tokens} input tokens "
          f"(~${tokens * USD_PER_INPUT_TOKEN:.4f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
