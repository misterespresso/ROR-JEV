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

#: A task the mod never reports on is released after this long (F25): walks get the
#: contract's NAVIGATE_TARGET deadline, interactions less (jev-01: 30 s cut a long walk short).
TASK_TIMEOUT_S = {"GOTO_ID": 60.0}
TASK_TIMEOUT_DEFAULT_S = 30.0

#: jev-06: JEV preferred another target for 40 s of a 42 s walk and could not say so. A walk is
#: redirected when JEV picks the SAME other walk/interaction this many ticks running, each at
#: least REDIRECT_MIN_P -- enough to follow a real change of mind, not every flicker.
REDIRECT_TICKS = 2
REDIRECT_MIN_P = 0.50

#: jev-09: a walk to a tall shrine never arrived and the mod reported nothing, so the task held
#: for the whole run and 16 JEV picks were discarded. A walk whose target gets no closer than
#: STUCK_PROGRESS_M in STUCK_AFTER_S is abandoned, and that target is set aside for QUARANTINE_S
#: so the next tick is not handed the same unreachable thing.
STUCK_AFTER_S = 12.0
STUCK_PROGRESS_M = 1.0
QUARANTINE_S = 60.0

#: jev-10: JEV chose the Shrine of Chance, the worth gate refused it (0.26-0.53 against 0.75),
#: the tick sent nothing, and the next tick chose it again -- the character stood at the shrine.
#: A refused action is withheld from the options this long, so the next pick is a different one.
#: The refusal stands; JEV simply is not asked the same unanswerable question every 2 s.
REFUSED_SUPPRESS_S = 30.0

#: Back off this long after a rate-limit or overload answer.
BACKOFF_S = {429: 5.0, 529: 5.0}

EVENT_STATE = {"action_started": State.NAVIGATING, "action_complete": State.COMPLETED,
               "action_failed": State.FAILED}


class Task:
    """What the loop knows about the one running task, beyond the Lifecycle slot."""

    def __init__(self, command_id: str, label: str, target_iid: str | None, now: float,
                 objective: str, timeout_s: float):
        self.command_id, self.label, self.target_iid, self.since = (
            command_id, label, target_iid, now)
        self.objective, self.timeout_s = objective, timeout_s
        self.best_m: float | None = None     # closest the target has been
        self.best_at = now


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
    last_setting: dict[str, str] = {}
    n = asked = errors = 0
    tokens = 0
    last_line = 0.0
    last_choice = "-"
    redirect = {"to": None, "n": 0}
    quarantined: dict[str, float] = {}
    refused: dict[str, float] = {}

    while not stop["flag"] and not p.lost and time.monotonic() < t_end:
        now = time.monotonic()
        if now < t_next:
            # jev-06: a task ending mid-wait used to be noticed only at the next tick, after the
            # mod had carried the character away from the chest it just reached. Decide now.
            if task and any(str(ev.get("command")) == task.label
                            and str(ev.get("event_type")) in ("action_complete", "action_failed")
                            for ev in p.events[seen_events:]):
                t_next = now
                continue
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
                if state is State.FAILED and str(ev.get("reason")) == "unreachable" and task.target_iid:
                    quarantined[str(task.target_iid)] = now + QUARANTINE_S
                if state in (State.COMPLETED, State.FAILED):
                    rec.record({"type": "HARNESS_NOTE", "note": "task_ended",
                                "label": task.label, "how": ev.get("event_type"),
                                "reason": ev.get("reason")})
                    task = None
        seen_events = len(p.events)
        if task:
            ids = available_ids(obs)
            why = None
            # Is the walk actually getting closer? (jev-09)
            d = None
            for it in ((obs or {}).get("interactables") or {}).get("items") or []:
                if str(it.get("id")) == task.target_iid:
                    d = it.get("distance")
            if d is not None and (task.best_m is None or d < task.best_m - STUCK_PROGRESS_M):
                task.best_m, task.best_at = d, now
            if task.target_iid and ids is not None and task.target_iid not in ids:
                why, state = "target no longer available (used)", State.COMPLETED
            elif st.objective != task.objective:
                # F37: starting the teleporter sends no terminal event, so jev-01 held the slot
                # 13 s into the charge before defend_zone could be chosen.
                why, state = f"objective changed {task.objective} -> {st.objective}", State.COMPLETED
            elif (task.best_m is not None and now - task.best_at > STUCK_AFTER_S
                  and task.label.startswith("GOTO_ID:")):
                quarantined[str(task.target_iid)] = now + QUARANTINE_S
                why, state = (f"no closer than {task.best_m:.0f} m in {STUCK_AFTER_S:g}s "
                              f"-- set aside for {QUARANTINE_S:g}s"), State.FAILED
            elif now - task.since > task.timeout_s:
                why, state = f"no outcome within {task.timeout_s:g}s", State.FAILED
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
        # Targets that could not be reached are hidden until their quarantine expires, so JEV is
        # not offered them again straight away (jev-09).
        quarantined = {k: v for k, v in quarantined.items() if v > now}
        if quarantined and isinstance((obs or {}).get("interactables"), dict):
            obs = dict(obs)
            block = dict(obs["interactables"])
            block["items"] = [it for it in block.get("items") or []
                              if str(it.get("id")) not in quarantined]
            obs["interactables"] = block
        ad = adapt(got["QUERY_OBJECTIVE"], got["QUERY_INVENTORY"], got["QUERY_COMBAT_STATUS"],
                   got["QUERY_INTERACTABLES"], observation_id=f"{a.run_id}-{n}", seq=n,
                   observation=obs)
        ad.raw["t_capture_mono_ms"] = int(now * 1000)
        active = life.has_active_task()
        reg = build_registry(ad.raw, set(caps), active)
        refused = {k: v for k, v in refused.items() if v > now}
        for aid in list(reg):
            if aid in refused:
                reg.pop(aid)        # gate-refused recently: offering it again just stalls
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

        # --- redirect a walk JEV no longer wants (jev-06) ---------------------------------
        sel0 = res.answers.get("next_action") or {}
        pick = str(sel0.get("choice") or "")
        p_pick = float((sel0.get("probabilities") or {}).get(pick, 0.0))
        other = (task is not None and task.label.startswith("GOTO_ID:") and life.task is not None
                 and pick in reg and pick != life.task.action_id
                 and pick.split(":")[0] not in ("fight_nearby", "explore")
                 and p_pick >= REDIRECT_MIN_P)
        redirect = ({"to": pick, "n": redirect["n"] + 1 if redirect["to"] == pick else 1}
                    if other else {"to": None, "n": 0})
        if redirect["n"] >= REDIRECT_TICKS:
            rec.record({"type": "HARNESS_NOTE", "note": "task_redirected", "from": task.label,
                        "to": pick, "p": round(p_pick, 2)})
            life.on_event(task.command_id, State.CANCELLED, f"redirected to {pick}")
            task = None
            redirect = {"to": None, "n": 0}

        # --- dispatch and execute --------------------------------------------------------
        d = dispatch(ad.raw, res.answers, reg, life, set(caps))
        for note in d.notes:
            rec.record({"type": "HARNESS_NOTE", "note": "dispatch", **note})
            if note.get("not_authorized") and note.get("action"):
                aid = str(note["action"])
                refused[aid] = now + REFUSED_SUPPRESS_S
                # ...and walking back to it: jev-10 refused at the shrine, roamed off, walked
                # back, was refused again. While the interaction is withheld, so is the trip.
                if ":" in aid:
                    refused[f"navigate:{aid.split(':', 1)[1]}"] = now + REFUSED_SUPPRESS_S
        sel = res.answers.get("next_action") or {}
        last_choice = f"{sel.get('choice', '-')} p={max((sel.get('probabilities') or {0: 0}).values()):.2f}" if sel else "-"
        for cmd in d.commands:
            mc = to_mod(cmd)
            if mc is None:
                if cmd.command == "SET_POSTURE":
                    continue            # engage_range alone: the fork has no command for it
                rec.record({"type": "HARNESS_NOTE", "note": "unsupported_command",
                            "command": cmd.command, "args": cmd.args})
                if life.task and life.task.command_id == cmd.command_id:
                    life.on_event(cmd.command_id, State.REJECTED, "unsupported")
                continue
            # Settings the mod keeps until told otherwise: send only on a change.
            if mc.command in ("STRATEGY", "TARGET_PRIORITY"):
                if last_setting.get(mc.command) == mc.args:
                    continue
                last_setting[mc.command] = mc.args
            rec.record({"type": "HARNESS_DECISION", "origin": cmd.origin,
                        "action_id": cmd.action_id, "command": mc.command, "args": mc.args,
                        "dry_run": a.dry_run})
            if not a.dry_run:
                p.command(mc.command, mc.args)
            if life.task and life.task.command_id == cmd.command_id:
                if mc.holds_slot and not a.dry_run:
                    life.on_event(cmd.command_id, State.ACCEPTED)
                    # The object this task is about, when it names one: used to notice it
                    # was used (gone from the list) and whether a walk is making progress.
                    iid = (mc.args if mc.command in ("INTERACT_ID", "GOTO_ID")
                           and mc.args != "teleporter" else None)
                    task = Task(cmd.command_id, mc.label, iid, now, st.objective,
                                TASK_TIMEOUT_S.get(mc.command, TASK_TIMEOUT_DEFAULT_S))
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
