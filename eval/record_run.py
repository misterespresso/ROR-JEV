"""Phase 0 step 7: record full runs.

Step 7 is "play 3-5 runs across survivors, recording throughout." Two things make that
non-obvious, and both are handled here:

1. **Stock Rainflayer only moves when commanded.** Attaching a passive recorder and letting
   it sit records a stationary character. So this ships a deliberately crude scripted
   driver: enough to progress a run, nowhere near enough to be a policy.
2. **The recording is the deliverable, not the run.** Bands can be recomputed; lost
   telemetry cannot. Everything is written raw via `brain.recorder`.

Two modes:

    --drive     (default) the scripted driver plays. Progresses stages, so telemetry covers
                exploring / charging / charged, which is what the registry needs (F2) and
                what fixtures currently only guess at.
    --observe   send nothing at all; you play. This is Phase 5's "casual human run"
                baseline, and it is the only way to see what the telemetry looks like under
                competent play.

**The driver is not a JEV stand-in and must never be mistaken for one.** It is an
if-else ladder over three fields. It exists so the game moves while we watch the wire.
Every command it sends is journaled with a `script:<rule>` origin, which keeps the
`02-ARCHITECTURE.md` provenance model honest in the recordings: a `script:` origin is
visibly not a `jev:` origin, and the T14 provenance test can assert that no `script:`
origin appears in a run that claims to be JEV-driven.

Usage:

    python -m eval.record_run --run-id run-01                 # scripted, until Ctrl+C
    python -m eval.record_run --run-id run-02 --minutes 20
    python -m eval.record_run --run-id human-01 --observe      # you play

Ctrl+C is the normal way to stop; the summary prints either way.
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from brain.recorder import Recorder
from eval.gonogo_probe import HOST, NON_RUN_SCENES, PORT, QUERIES, Probe, _outcome

from eval.profile_config import ai_control_enabled, block_repeat_damage  # noqa: E402

#: Terminal action events. Same set the probe uses -- see F10/F15: there are two terminal
#: types and three possible outcome field names.
TERMINAL = {"action_complete", "action_failed"}

#: Events that say an action is now running, so the driver must not start another.
STARTED = {"action_started"}

#: Give up waiting for a terminal event after this long. A terminal event is not guaranteed:
#: in `fork-02` a teleporter interaction succeeded mid-run and no `action_complete` ever came
#: (F25). That run's chest command also never reported back, but it was issued from the lobby
#: before the character existed, so it is confounded and proves nothing about stuck detection.
BUSY_TIMEOUT_S = 45.0

#: Explore, fight and loot for this long before starting the teleporter. The first ladder
#: had no budget: chests were rarely in the mod's view cone, so "go to the teleporter" was
#: the only rule that ever fired, and the character started a boss fight at level 1 with no
#: items (fork-03). Stage difficulty rises with time, so this is a trade, not "longer is safer".
EXPLORE_BUDGET_S = 300.0

#: Hard cap: start the teleporter after this long even if enemies keep coming, so a stage
#: full of enemies cannot hold the run on one stage forever.
STAGE_CAP_S = 600.0

#: After a chest search comes back `not_found`, wait this long before asking again.
CHEST_RETRY_S = 25.0

#: Stand and fight when an enemy is this close. Stock's `in_combat` means "anything within
#: 50 m", which in a crowded stage is true almost constantly -- keying on it would fight
#: forever and never loot. Roam already auto-engages whatever it runs into.
FIGHT_RADIUS_M = 25.0


@dataclass
class RunState:
    """What the driver is allowed to look at. Deliberately tiny."""

    objective: str = "unknown"
    scene_name: str | None = None
    gold: int = 0
    chest_cost: int | None = None      # cheapest chest cost actually observed
    chest_seen: bool = False
    teleporter_seen: bool = False
    in_combat: bool = False
    #: Metres to the nearest enemy, or None when there is none. Stock reports 999.0 for "no
    #: enemy" (F17); that is mapped to None here, never treated as a distance.
    nearest_enemy_m: float | None = None
    #: Whether the player is actually in a run. From INVENTORY's "Not in game" marker, never
    #: from OBJECTIVE, which answers "exploring" even in the character-select lobby.
    in_game: bool = False
    action_busy: bool = False
    #: When the running action started, and the objective phase at that moment. Needed
    #: because stock 1.1.1 does not reliably send a terminal event (F25): in `fork-02` a
    #: teleporter interaction SUCCEEDED -- the objective flipped to charging -- and no
    #: `action_complete` ever arrived, so the driver waited forever and never held the zone.
    busy_since: float | None = None
    busy_objective: str | None = None
    #: When the current stage began, for the exploration budget.
    stage_started_at: float | None = None
    #: Best known chest price. Starts at stage 1's base cost and is corrected from what the
    #: mod reports -- `action_started`/`no_gold` carry the real `cost` (F14, F16).
    chest_price: int = 25
    #: Do not ask for a chest again before this time. Set after `not_found`: the mod's
    #: detection is a view cone (F12), so the answer changes once the character has moved.
    chest_retry_at: float = 0.0
    #: Increments on every scene change. This prototypes protocol v2's `scene_gen`
    #: (05-PROTOCOL.md section 2) from a field stock already provides (F16's neighbour:
    #: OBJECTIVE.scene_name), so entity-ID scoping can be tested offline later.
    scene_gen: int = 0
    scenes: list[str] = field(default_factory=list)


#: Fork-only queries. Polled every sweep, recorded when answered, and silently skipped when
#: the installed mod is stock (which answers with an "Unknown query type" error).
OPTIONAL_QUERIES = ["QUERY_OBSERVATION"]


def poll(p: Probe, st: RunState) -> dict[str, dict]:
    """One full sweep of every query. Recorded by the Probe's reader automatically."""
    got: dict[str, dict] = {}
    for q in QUERIES + OPTIONAL_QUERIES:
        r = p.query(q)
        if r is None or "error" in r:
            continue
        got[q] = r

    if o := got.get("QUERY_OBJECTIVE"):
        st.objective = str(o.get("objective", "unknown"))
        scene = o.get("scene_name")
        if scene and scene != st.scene_name:
            st.scene_gen += 1
            st.stage_started_at = time.monotonic()
            st.scene_name = scene
            st.scenes.append(str(scene))
            print(f"  [scene {st.scene_gen}] {scene}")
    if inv := got.get("QUERY_INVENTORY"):
        st.gold = int(inv.get("money", 0) or 0)
        st.in_game = not inv.get("note") and (st.scene_name or "") not in NON_RUN_SCENES
    if c := got.get("QUERY_COMBAT_STATUS"):
        st.in_combat = bool(c.get("in_combat"))
        d = c.get("nearest_enemy_distance")
        try:
            d = float(d)
        except (TypeError, ValueError):
            d = None
        st.nearest_enemy_m = None if d is None or d >= 999.0 else d
    if it := got.get("QUERY_INTERACTABLES"):
        types = [str(x.get("type")) for x in it.get("interactables", [])]
        st.chest_seen = "chest" in types
        st.teleporter_seen = "teleporter" in types
        costs = [int(x["cost"]) for x in it.get("interactables", [])
                 if str(x.get("type")) == "chest" and x.get("cost")]
        st.chest_cost = min(costs) if costs else None
    return got


def on_event(st: RunState, ev: dict, now: float) -> None:
    """Learn from action outcomes. Stock sends numbers as strings (F11), so coerce."""
    def num(v):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None

    cmd = str(ev.get("command") or "")
    if not cmd.endswith(":chest"):
        return
    cost = num(ev.get("cost"))
    if cost:
        st.chest_price = cost                   # the mod's own price for the chest it chose
    if ev.get("event_type") == "action_failed":
        reason = str(ev.get("reason") or "")
        if reason == "not_found":
            st.chest_retry_at = now + CHEST_RETRY_S
        elif reason in ("no_gold", "no gold"):
            st.chest_retry_at = now + CHEST_RETRY_S


def decide(st: RunState, now: float | None = None) -> tuple[str, str, str] | None:
    """The crude ladder. Returns (command, args, rule) or None.

    Ordered by how much a run needs it, not by expected value -- there is no expected value
    here, which is the entire reason JEV exists in this project.
    """
    if not st.in_game:
        # Lobby, stage loading, or the death screen. A command sent now lands on no body, or
        # waits and fires later against a world that has moved on (fork-02).
        return None
    if st.action_busy:
        return None
    if st.objective == "teleporter_charged":
        return ("FIND_AND_INTERACT", "teleporter", "advance_when_charged")
    if st.objective == "charging_teleporter":
        # Holding the zone is what F2 found the registry could not even express. Worth
        # recording heavily: this is the phase the milestone depends on.
        return ("MODE", "defend_zone", "hold_zone_while_charging")
    now = time.monotonic() if now is None else now
    stage_age = now - st.stage_started_at if st.stage_started_at is not None else 0.0

    # Past the hard cap, the stage has to end even if it is crowded.
    if stage_age >= STAGE_CAP_S and st.teleporter_seen:
        return ("FIND_AND_INTERACT", "teleporter", "start_teleporter_stage_cap")

    # Kills are the only income. The first ladder never sent a fight order, so the character
    # walked past enemies, earned nothing, and could never afford a chest.
    if st.nearest_enemy_m is not None and st.nearest_enemy_m <= FIGHT_RADIUS_M:
        return ("MODE", "combat", "fight_nearby")

    # Let the MOD find the chest. Its own search reaches chests the interactables list never
    # shows (that list is a view cone, F12), so requiring `chest_seen` first -- as the first
    # ladder did -- meant chests it could afford were never even attempted.
    price = st.chest_cost if st.chest_cost is not None else st.chest_price
    if st.gold >= price and now >= st.chest_retry_at:
        return ("FIND_AND_INTERACT", "chest", "loot_chest")

    if stage_age < EXPLORE_BUDGET_S:
        return ("MODE", "roam", "explore_budget")
    if st.teleporter_seen:
        return ("FIND_AND_INTERACT", "teleporter", "start_teleporter")
    return ("MODE", "roam", "explore")


def drive_loop(p: Probe, rec: Recorder, st: RunState, *, observe: bool, minutes: float,
               poll_hz: float, decide_every: float, stop: dict,
               rules_used: dict[str, int], event_counts: dict[str, int]) -> None:
    """Poll, track actions, and (unless observing) drive, until time, Ctrl+C, or a lost link.

    Factored out so `eval/fork_check.py` can run it on the SAME connection the probe just
    used. Handing over between two processes meant alt-tabbing out mid-run while the character
    stood parked -- and on Windows it briefly meant two servers on one port.
    """
    t_end = time.monotonic() + minutes * 60 if minutes else None
    t_next_poll = 0.0
    t_next_decide = time.monotonic() + 2.0     # let the first poll land first
    # Start after anything already received: when called after the probe, its events are
    # already in `p.events` and must not be counted, or replayed into action-busy state.
    seen_events = len(p.events)
    last_line = 0.0

    while not stop["flag"] and not p.lost:
        now = time.monotonic()
        if t_end and now >= t_end:
            print("  ...  time limit reached")
            break

        if now >= t_next_poll:
            poll(p, st)
            t_next_poll = now + (1.0 / max(poll_hz, 0.05))

        # Drain events for action-busy tracking and counting.
        for ev in p.events[seen_events:]:
            et = str(ev.get("event_type"))
            event_counts[et] = event_counts.get(et, 0) + 1
            on_event(st, ev, now)
            if et in STARTED:
                st.action_busy = True
                st.busy_since = now
                st.busy_objective = st.objective
            elif et in TERMINAL:
                st.action_busy = False
        seen_events = len(p.events)

        # A terminal event is not guaranteed (F25), so busy must also end when the world has
        # visibly moved on, or after a timeout. Each release is recorded, so the recording says
        # why the driver acted rather than leaving a silent gap.
        if st.action_busy:
            why = None
            if st.busy_objective is not None and st.objective != st.busy_objective:
                why = f"objective changed {st.busy_objective} -> {st.objective}"
            elif st.busy_since is not None and now - st.busy_since > BUSY_TIMEOUT_S:
                why = f"no terminal event within {BUSY_TIMEOUT_S:g}s"
            if why:
                st.action_busy = False
                rec.record({"type": "HARNESS_NOTE", "note": "busy_released", "why": why})
                print(f"  ...  released action lock: {why}")
                t_next_decide = now      # act on the new situation immediately

        if not observe and now >= t_next_decide:
            d = decide(st, now)
            if d is not None:
                cmd, args, rule = d
                # Provenance: journaled as `script:`, never `jev:`.
                rec.record({"type": "HARNESS_DECISION", "origin": f"script:{rule}",
                            "command": cmd, "args": args,
                            "objective": st.objective, "gold": st.gold,
                            "scene_gen": st.scene_gen})
                p.command(cmd, args)
                rules_used[rule] = rules_used.get(rule, 0) + 1
            t_next_decide = now + decide_every

        if now - last_line >= 5.0:
            print(f"  {st.scene_name or '?':<16} {st.objective:<20} "
                  f"gold={st.gold:<5} {'combat' if st.in_combat else '      '} "
                  f"{'busy' if st.action_busy else '    '} "
                  f"frames={sum(rec.counts.values())}")
            last_line = now

        time.sleep(0.05)


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase 0 step 7 run recorder")
    ap.add_argument("--run-id", default=time.strftime("run-%Y%m%d-%H%M%S"))
    ap.add_argument("--minutes", type=float, default=0.0,
                    help="stop after this long; 0 means run until Ctrl+C")
    ap.add_argument("--poll-hz", type=float, default=1.0,
                    help="full query sweeps per second")
    ap.add_argument("--decide-every", type=float, default=6.0,
                    help="seconds between driver decisions")
    ap.add_argument("--observe", action="store_true",
                    help="send no commands at all; you play (Phase 5 human baseline)")
    ap.add_argument("--connect-wait", type=float, default=180.0)
    # Stock telemetry contains NO survivor identity -- a 3.3 MB recording of a 25-minute
    # run does not reveal which character was played (F18). Cross-survivor comparison in
    # Phase 5 is therefore impossible unless the human labels the run at record time.
    ap.add_argument("--survivor", default=None,
                    help="which survivor you are playing; telemetry cannot report it")
    ap.add_argument("--difficulty", default=None,
                    help="Drizzle/Rainstorm/Monsoon; telemetry cannot report it either")
    ap.add_argument("--note", default=None, help="free-text label for this run")
    a = ap.parse_args()

    mode = "OBSERVE (you play, harness sends nothing)" if a.observe \
        else "DRIVE (scripted ladder plays)"
    print("=" * 78)
    print("PHASE 0 STEP 7 - RUN RECORDER")
    print("=" * 78)
    print(f"run id     : {a.run_id}")
    print(f"mode       : {mode}")
    print(f"poll       : {a.poll_hz:g} Hz full sweep ({len(QUERIES)} queries)")
    if not a.observe:
        print(f"decide     : every {a.decide_every:g}s")
    print(f"stop       : {'Ctrl+C' if not a.minutes else f'{a.minutes:g} min or Ctrl+C'}")
    if a.survivor or a.difficulty:
        print(f"labelled   : survivor={a.survivor or '?'} difficulty={a.difficulty or '?'}")
    else:
        print("  !  no --survivor given. Telemetry carries no survivor identity, so this")
        print("     recording will be indistinguishable from any other character's.")
    print()
    print("Launch the game through r2modman and start a run. Recording is raw and unbanded;")
    print("the recording is the deliverable, not the run.")
    print()

    # The two modes want OPPOSITE settings of the same config flag, and getting it wrong is
    # confusing rather than obviously broken: in observe mode the AI silently plays for you.
    ai_on, cfg = ai_control_enabled()
    if ai_on is None:
        print("  !  could not read EnableAIControl from the profile config.")
        print("     --observe needs it false; --drive needs it true.")
    elif a.observe and ai_on:
        print("  !! EnableAIControl = true, so THE MOD WILL PLAY, not you.")
        print(f"     Close the game, set EnableAIControl = false in:")
        print(f"       {cfg}")
        print("     then restart the game (the value is read at plugin init) and rerun.")
        print("     Queries keep working with it off -- SocketBridge is attached")
        print("     unconditionally, so telemetry is unaffected.")
        print()
        print("     Continuing anyway; the recording will be of the mod playing.")
    elif not a.observe and not ai_on:
        print("  !! EnableAIControl = false, so commands will be accepted and ignored.")
        print(f"     Set it true in {cfg} and restart the game, or use --observe.")
    else:
        print(f"  ok EnableAIControl = {str(ai_on).lower()}, correct for this mode.")
    print()

    stop = {"flag": False}

    def _sigint(_sig, _frm):
        stop["flag"] = True
        print("\n  ...  stopping (Ctrl+C)")

    signal.signal(signal.SIGINT, _sigint)

    st = RunState()
    rules_used: dict[str, int] = {}
    event_counts: dict[str, int] = {}

    with Recorder(a.run_id) as rec:
        p = Probe(rec)
        try:
            if not p.accept(a.connect_wait):
                print("  no connection -- is the game running with the Jev profile?")
                return 1

            rec.record({"type": "HARNESS_START", "run_id": a.run_id,
                        "mode": "observe" if a.observe else "drive",
                        "poll_hz": a.poll_hz, "decide_every_s": a.decide_every,
                        # Human-supplied, because the wire cannot supply it.
                        "survivor": a.survivor, "difficulty": a.difficulty,
                        "note": a.note, "ai_control": ai_on,
                        "block_repeat_damage": block_repeat_damage()})

            drive_loop(p, rec, st, observe=a.observe, minutes=a.minutes,
                       poll_hz=a.poll_hz, decide_every=a.decide_every, stop=stop,
                       rules_used=rules_used, event_counts=event_counts)

            if not a.observe:
                p.command("MODE", "wait")      # leave the character parked
        finally:
            p.close()
            summary = rec.summary()

    print()
    print("-" * 78)
    print(f"recording : {summary['dir']}")
    print(f"frames    : {summary['counts']}")
    print(f"gaps      : {summary['gaps']}")
    print(f"scenes    : {st.scene_gen} -> {', '.join(st.scenes) or '(none)'}")
    if event_counts:
        print("events    : " + ", ".join(f"{k}={v}" for k, v in sorted(event_counts.items())))
    if rules_used:
        print("rules     : " + ", ".join(f"{k}={v}" for k, v in sorted(rules_used.items())))
    print("-" * 78)
    if st.scene_gen >= 2:
        print("Stage transition captured -- that is the telemetry the registry's phase")
        print("handling (F2) and scene scoping (05-PROTOCOL.md section 2) need.")
    else:
        print("No stage transition captured. A recording that never leaves stage 1 cannot")
        print("exercise scene_gen or the charged/advance phases; consider a longer run.")
    print()
    print("Next: python -m eval.inspect_recording --run-id " + a.run_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
