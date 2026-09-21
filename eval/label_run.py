"""Attach human-supplied labels to a recording, without touching the recording.

Stock telemetry carries no survivor identity, no difficulty and no run intent (F18): a 3.3 MB
recording of a 25-minute run does not reveal which character was played. Those facts have to
come from the person who played it, and `eval/record_run.py` takes `--survivor` /
`--difficulty` / `--note` for exactly that. This tool covers the other case -- a run that was
already recorded before anyone thought to label it.

**Labels go in a sidecar, never into the streams.** `brain/recorder.py` promises the JSONL is
raw and append-only, and every replay and schema claim rests on that. Rewriting recorded frames
to add a field learned afterwards would make the recording a thing we edit, which is exactly
what it must not be. So labels live in `labels.json` beside the streams, keyed by session, and
readers merge them at load time.

Usage:
    python -m eval.label_run --run-id human-01 --session 1 --survivor Bandit
    python -m eval.label_run --run-id human-01 --show
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

ROOT = pathlib.Path(__file__).resolve().parents[1]
RECORDINGS = ROOT / "recordings"
SIDECAR = "labels.json"


def load_labels(run_dir: pathlib.Path) -> dict:
    p = run_dir / SIDECAR
    if not p.exists():
        return {"run_id": run_dir.name, "sessions": {}}
    return json.loads(p.read_text(encoding="utf-8"))


def session_label(run_dir: pathlib.Path, index: int, n_sessions: int) -> dict:
    """Labels for one session, resolving a negative index the way replay does."""
    data = load_labels(run_dir)
    if index < 0:
        index = n_sessions + index
    return data.get("sessions", {}).get(str(index), {})


def main() -> int:
    ap = argparse.ArgumentParser(description="Label a recording out of band")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--session", type=int, default=-1,
                    help="which session (0-based, or negative from the end)")
    ap.add_argument("--survivor")
    ap.add_argument("--difficulty")
    ap.add_argument("--note")
    ap.add_argument("--show", action="store_true", help="print existing labels and exit")
    a = ap.parse_args()

    run_dir = RECORDINGS / a.run_id
    if not run_dir.exists():
        print(f"no such recording: {run_dir}")
        return 1

    # Resolve the session count the same way the replay tool splits them, so an index means
    # the same thing in both places.
    from eval.replay_stock import sessions
    n = len(sessions(run_dir))

    data = load_labels(run_dir)
    if a.show:
        print(json.dumps(data, indent=2))
        return 0

    idx = a.session if a.session >= 0 else n + a.session
    if not 0 <= idx < n:
        print(f"session {a.session} out of range: recording has {n} session(s)")
        return 1

    entry = data.setdefault("sessions", {}).setdefault(str(idx), {})
    for k, v in (("survivor", a.survivor), ("difficulty", a.difficulty), ("note", a.note)):
        if v is not None:
            entry[k] = v
    entry["labelled_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    # Provenance: these are recollections, not measurements, and a later reader must be able
    # to tell the difference.
    entry["source"] = "human, supplied after the fact"

    (run_dir / SIDECAR).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"labelled {a.run_id} session {idx} of {n}:")
    print(json.dumps(entry, indent=2))
    print()
    print(f"written to {(run_dir / SIDECAR).relative_to(ROOT)} -- the JSONL streams are")
    print("untouched, so the recording stays raw.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
