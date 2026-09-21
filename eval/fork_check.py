"""One command for the in-game half of docs/09-FORK-BASE.md section 6: probe, then record.

Run it BEFORE you are in a run. It waits for the game to connect and for a run to start, then
does everything else without a keypress:

    1. the go/no-go probe checks: queries, movement under MODE:roam, one FIND_AND_INTERACT
    2. straight into the scripted driver for `--minutes`, recording throughout

Why one command instead of the probe and the recorder separately: running them separately
meant alt-tabbing out of a live run between them. The probe finishes by parking the character
(`MODE:wait`), so it stood still and died while you switched windows -- and running both at
once was worse, because on Windows two servers could share port 7777 (fixed in
`gonogo_probe.listen_socket`). Here there is one connection, no gap, and no parking between
the two phases.

Both phases land in one recording, as two sessions split by `HARNESS_START`, so
`eval.replay_stock --session` and `eval.inspect_recording` read them as they read any other
recording.

Usage (start this first, THEN launch the game and start a run):

    python -m eval.fork_check --run-id fork-02 --survivor Commando --difficulty Drizzle
"""
from __future__ import annotations

import argparse
import contextlib
import io
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from brain.recorder import Recorder
from eval import gonogo_probe as gp
from eval.profile_config import ai_control_enabled, block_repeat_damage
from eval.record_run import RunState, drive_loop


def main() -> int:
    ap = argparse.ArgumentParser(description="Probe, then record, on one connection")
    ap.add_argument("--run-id", default=time.strftime("fork-%Y%m%d-%H%M%S"))
    ap.add_argument("--minutes", type=float, default=10.0, help="length of the driving phase")
    ap.add_argument("--survivor", default=None)
    ap.add_argument("--difficulty", default=None)
    # Generous, because the point is to start this before the game is even open.
    ap.add_argument("--connect-wait", type=float, default=600.0)
    ap.add_argument("--run-wait", type=float, default=600.0)
    a = ap.parse_args()

    print("=" * 78)
    print("FORK CHECK - probe, then record, one connection, no alt-tab")
    print("=" * 78)
    print(f"run id   : {a.run_id}")
    print(f"then     : {a.minutes:g} min of scripted driving")
    ai_on, cfg = ai_control_enabled()
    if ai_on is False:
        print()
        print("  !! EnableAIControl = false. Nothing will move and no events will fire, so")
        print("     this run would prove nothing about the AI. Close the game, set it true in")
        print(f"     {cfg}")
        print("     and start again.")
        return 2
    print(f"AI ctrl  : {'on' if ai_on else 'UNKNOWN - could not read the config'}")
    print()
    print(">>> Now launch the game (r2modman, Start modded) and start a run.")
    print(">>> You can leave this window alone from here on.")
    print()

    with Recorder(a.run_id) as rec:
        p = gp.Probe(rec)
        try:
            # ------------------------------------------------------------ probe phase
            if not p.accept(a.connect_wait):
                return 1
            rec.record({"type": "HARNESS_START", "harness": "fork_check/probe",
                        "ai_control": ai_on,
                        "block_repeat_damage": block_repeat_damage(), "survivor": a.survivor,
                        "difficulty": a.difficulty})
            try:
                if not gp.wait_for_run(p, a.run_wait):
                    return 1
                gp.check_state_queries(p)
                if not p.lost:
                    gp.check_movement(p, 15.0)
                if not p.lost:
                    gp.check_interaction(p, 90.0)
            except KeyboardInterrupt:
                print("\n  interrupted during the probe")
                return 1

            # Classify with the probe's own rules, but print only the verdict block: its
            # closing advice is written for a standalone probe and would mislead here.
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                probe_code = gp.verdict(p, rec)
            lines = buf.getvalue().splitlines()
            start = next((i for i, l in enumerate(lines) if l.startswith("VERDICT")), None)
            print()
            print("-" * 78)
            print("PROBE PHASE")
            for l in (lines[start:start + 6] if start is not None else []):
                if l.startswith(("VERDICT", "  ")):
                    print(l)
            print("-" * 78)

            if p.lost:
                print("  connection lost -- stopping")
                return 1

            # ------------------------------------------------------------ drive phase
            # No MODE:wait between the phases: the driver's first decision lands within a
            # couple of seconds, so the character is never left standing.
            print()
            print(f"DRIVE PHASE - {a.minutes:g} min (Ctrl+C to stop early)")
            rec.record({"type": "HARNESS_START", "harness": "fork_check/drive",
                        "run_id": a.run_id, "mode": "drive", "poll_hz": 1.0,
                        "decide_every_s": 6.0, "survivor": a.survivor,
                        "difficulty": a.difficulty, "ai_control": ai_on,
                        "block_repeat_damage": block_repeat_damage(),
                        "note": "fork_check: continues the probe's connection"})

            stop = {"flag": False}

            def _sigint(_sig, _frm):
                stop["flag"] = True
                print("\n  ...  stopping (Ctrl+C)")

            signal.signal(signal.SIGINT, _sigint)
            st = RunState()
            rules: dict[str, int] = {}
            events: dict[str, int] = {}
            drive_loop(p, rec, st, observe=False, minutes=a.minutes, poll_hz=1.0,
                       decide_every=6.0, stop=stop, rules_used=rules, event_counts=events)
            if not p.lost:
                p.command("MODE", "wait")
        finally:
            p.close()
            summary = rec.summary()

    print()
    print("=" * 78)
    print(f"recording : {summary['dir']}")
    print(f"frames    : {summary['counts']}   gaps: {summary['gaps']}")
    print(f"scenes    : {', '.join(st.scenes) or '(none)'}")
    print("events    : " + (", ".join(f"{k}={v}" for k, v in sorted(events.items()))
                            or "NONE -- no event fired while driving"))
    print("rules     : " + (", ".join(f"{k}={v}" for k, v in sorted(rules.items())) or "-"))
    print(f"probe     : exit {probe_code} (0 GO, 1 NO-GO, 2 blocked/inconclusive)")
    print("=" * 78)
    return probe_code


if __name__ == "__main__":
    raise SystemExit(main())
