"""Replay a stock recording through the real brain.

This is the test `04-ROADMAP.md` Phase 2 wants ("unit-tested against recordings") and that
F18 said was blocked, run as far as it can go today: real frames from a real run, through the
real adapter, registry, and request builder. No stubs on the brain side.

It answers three questions the fixtures cannot:

    1. What does the registry actually offer, tick by tick, on real telemetry?
    2. Which questions survive the omit-never-null rule in each objective phase?
    3. Does anything crash, assert, or fail an invariant on inputs nobody hand-wrote?

Recordings append across sessions (`brain/recorder.py` never truncates), so `HARNESS_START`
frames are used to split them and the newest session is replayed by default.

Run:
    python -m eval.replay_stock --run-id human-01
    python -m eval.replay_stock --run-id human-01 --session 1
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from brain.catalog import UNKNOWN, BuildContext, build_request, load_contract
from brain.registry import build_registry
from brain.stock_adapter import ADAPTER_ASSUMPTIONS, adapt
from eval.fixtures import MILESTONE_1_CAPABILITIES
from eval.label_run import session_label

ROOT = pathlib.Path(__file__).resolve().parents[1]


#: An OBSERVATION older than this when a tick completes is not paired with it. The recorder
#: polls it about once a second; a stale snapshot would describe a different moment.
OBS_MAX_AGE_NS = 3_000_000_000


def _load(path: pathlib.Path) -> list[tuple[int, dict]]:
    out = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        fr = rec.get("frame")
        if isinstance(fr, dict):
            out.append((int(rec.get("_t_recv_wall_ns") or 0), fr))
    return out


def sessions(run_dir: pathlib.Path) -> list[list[tuple[int, dict]]]:
    """Split an appended recording into sessions on HARNESS_START.

    The recorder writes OBSERVATION frames to their own stream, so they are merged back in by
    receipt time -- otherwise a build-2+ recording replays as if it had none.
    """
    frames = _load(run_dir / "other.jsonl") + _load(run_dir / "observations.jsonl")
    frames.sort(key=lambda tf: tf[0])          # stable: equal stamps keep file order
    out: list[list[tuple[int, dict]]] = []
    for t, fr in frames:
        if fr.get("type") == "HARNESS_START":
            out.append([])
        if out:
            out[-1].append((t, fr))
    return out or [[]]


def ticks(frames: list[tuple[int, dict]]):
    """Group stock query responses into ticks.

    A tick is completed by the arrival of the LAST query of a sweep. Stock answers six
    separate queries, so a tick assembled this way is already slightly torn -- the exact
    defect the single v2 snapshot exists to remove. Noted rather than hidden.

    Each tick carries the newest OBSERVATION under the key "OBSERVATION", if one arrived
    within OBS_MAX_AGE_NS of the tick completing.
    """
    cur: dict[str, dict] = {}
    obs: tuple[int, dict] | None = None
    for t, fr in frames:
        typ = fr.get("type")
        if typ == "OBSERVATION":
            obs = (t, fr)
            continue
        if typ in {"OBJECTIVE", "INVENTORY", "COMBAT_STATUS", "INTERACTABLES"}:
            # Stock marks a degraded response with `note` ("Not in game"). Replaying those
            # as real observations counts menu and loading time as gameplay.
            if fr.get("note"):
                cur.clear()
                continue
            cur[typ] = fr
            if len(cur) == 4:
                tick = dict(cur)
                if obs and t - obs[0] <= OBS_MAX_AGE_NS:
                    tick["OBSERVATION"] = obs[1]
                yield tick
                cur = {}


def main() -> int:
    ap = argparse.ArgumentParser(description="Replay a stock recording through the brain")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--session", type=int, default=-1,
                    help="which session (0-based); default -1 = newest")
    ap.add_argument("--limit", type=int, default=0, help="max ticks (0 = all)")
    a = ap.parse_args()

    run_dir = ROOT / "recordings" / a.run_id
    if not run_dir.exists():
        print(f"no such recording: {run_dir}")
        return 1

    sess = sessions(run_dir)
    frames = sess[a.session]
    contract = load_contract()
    caps = frozenset(MILESTONE_1_CAPABILITIES)

    print("=" * 78)
    print("REPLAY: STOCK RECORDING -> REAL BRAIN")
    print("=" * 78)
    print(f"recording : {a.run_id}  (session {a.session} of {len(sess)})")
    print(f"frames    : {len(frames)}")
    lbl = session_label(run_dir, a.session, len(sess))
    if lbl:
        bits = [f"{k}={v}" for k, v in lbl.items() if k not in {"labelled_at", "source"}]
        print(f"labelled  : {'  '.join(bits)}")
        print(f"            ({lbl.get('source', 'unknown provenance')})")
    else:
        # Not cosmetic: without a label there is no way to know which survivor this was,
        # so cross-survivor comparison silently compares unknowns.
        print("labelled  : NO LABEL -- telemetry carries no survivor identity (F18).")
        print("            python -m eval.label_run --run-id "
              f"{a.run_id} --session {a.session} --survivor <name>")
    print()
    print("Adapter assumptions -- every one is something stock does NOT tell us:")
    for s in ADAPTER_ASSUMPTIONS:
        print(f"  * {s}")
    print()

    n = 0
    phases = Counter()
    reg_shapes = Counter()
    sendable = Counter()
    omitted_reasons = Counter()
    unmapped = Counter()
    errors: list[str] = []
    verbs_offered = Counter()
    with_obs = 0

    for tick in ticks(frames):
        n += 1
        if a.limit and n > a.limit:
            break
        ad = adapt(tick.get("OBJECTIVE", {}), tick.get("INVENTORY", {}),
                   tick.get("COMBAT_STATUS", {}), tick.get("INTERACTABLES", {}),
                   observation_id=f"obs-{n}", seq=n,
                   observation=tick.get("OBSERVATION"))
        if "OBSERVATION" in tick:
            with_obs += 1
        for u in ad.unmapped:
            unmapped[u] += 1
        phases[ad.banded["objective"]["phase"]] += 1

        try:
            reg = build_registry(ad.raw, set(caps), False)
        except AssertionError as e:
            errors.append(f"tick {n}: registry invariant: {e}")
            continue

        for k in reg:
            verbs_offered[k.split(":")[0]] += 1
        reg_shapes[tuple(sorted({k.split(":")[0] for k in reg}))] += 1

        try:
            req = build_request(ad.banded, reg,
                                BuildContext(caps, ad.banded, False, False), contract)
        except Exception as e:                       # noqa: BLE001 - want any failure named
            errors.append(f"tick {n}: request build: {type(e).__name__}: {e}")
            continue

        for q in req.questions:
            sendable[q] += 1
        for o in req.omitted:
            omitted_reasons[f"{o['question']}:{o['reason']}"] += 1

    print("-" * 78)
    print(f"TICKS REPLAYED: {n}")
    print(f"  with a fresh OBSERVATION (build 2+): {with_obs}")
    print("-" * 78)
    print("objective phases seen:")
    for p, c in phases.most_common():
        print(f"    {p:<20} {c:5d} ticks")
    print()
    print("registry verb shapes (distinct sets of verbs offered):")
    for shape, c in reg_shapes.most_common(8):
        print(f"    {c:5d} x  {', '.join(shape) or '(empty)'}")
    print()
    print("verbs offered, by tick count:")
    for v, c in verbs_offered.most_common():
        print(f"    {v:<20} {c:5d}")
    print()
    if unmapped:
        print("unmapped stock interactable types (no verb, so never offered):")
        for u, c in unmapped.most_common():
            print(f"    {u:<20} {c:5d} sightings")
        print()

    print("-" * 78)
    print("QUESTIONS SENT, ON REAL TELEMETRY")
    print("-" * 78)
    if sendable:
        for q, c in sendable.most_common():
            print(f"    SENT     {q:<26} {c:5d}/{n} ticks")
    else:
        print("    (none)")
    print()
    top = omitted_reasons.most_common(10)
    for k, c in top:
        print(f"    omitted  {k:<44} {c:5d}")
    print()

    if errors:
        print("-" * 78)
        print(f"FAILURES: {len(errors)}")
        for e in errors[:10]:
            print(f"    {e}")
        print()

    print("=" * 78)
    if not sendable:
        print("RESULT: the brain ran clean over real telemetry and authorized NOTHING.")
        print("Every question was omitted for want of a field stock does not provide. That")
        print("is the field contract working, and it is F18 reproduced end to end on real")
        print("frames rather than argued from a table.")
    else:
        print(f"RESULT: {len(sendable)} question(s) sendable on real telemetry.")
    print(f"Registry invariants held on {n - len(errors)}/{n} ticks.")
    print("=" * 78)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
