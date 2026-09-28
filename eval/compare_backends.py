"""Ask two System One backends the SAME recorded ticks, and compare what they decide.

Switching from JEV to Laya is one URL (`brain/jev_client`), but it is a different model, and
every primer fact, option text and gate threshold in this repo was tuned by watching JEV. So
before trusting a backend in a live run, replay real observations through both and look at
where they disagree -- on the same request, byte for byte.

    laya-serve                                   # in another terminal, GPU if you have one
    python -m eval.compare_backends --run-id jev-full-01 --limit 40

Prints agreement on `next_action`, how often each would clear the gate that authorises the
pick, latency, and the ticks where they differ. Costs one JEV call per tick (~$0.0001).
"""
from __future__ import annotations

import argparse
import pathlib
import statistics as S
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from brain.catalog import BuildContext, build_request, load_contract
from brain.jev_client import DEFAULT_ENDPOINT, JevError, ask
from brain.registry import ENTRY, build_registry
from brain.stock_adapter import adapt
from eval.fixtures import MILESTONE_1_CAPABILITIES
from eval.replay_stock import sessions, ticks

LOCAL = "http://127.0.0.1:8000/v1/systemone"


def top(answer: dict) -> tuple[str, float]:
    probs = answer.get("probabilities") or {}
    return str(answer.get("choice", "-")), float(max(probs.values(), default=0.0))


def main() -> int:
    ap = argparse.ArgumentParser(description="Compare two System One backends on real ticks")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--a", default=DEFAULT_ENDPOINT, help="baseline endpoint (JEV)")
    ap.add_argument("--b", default=LOCAL, help="other endpoint (laya-serve)")
    a = ap.parse_args()

    run = pathlib.Path(__file__).resolve().parents[1] / "recordings" / a.run_id
    if not run.exists():
        print(f"no such recording: {run}")
        return 1
    caps = frozenset(MILESTONE_1_CAPABILITIES)
    contract = load_contract()

    agree = 0
    asked = 0
    lat: dict[str, list[float]] = {"a": [], "b": []}
    models: dict[str, str] = {}
    gated: Counter = Counter()
    diffs: list[str] = []

    for i, t in enumerate(ticks(sessions(run)[-1])):
        if asked >= a.limit:
            break
        if not t.get("OBSERVATION"):
            continue
        ad = adapt(t.get("OBJECTIVE", {}), t.get("INVENTORY", {}), t.get("COMBAT_STATUS", {}),
                   t.get("INTERACTABLES", {}), observation_id=f"cmp-{i}", seq=i,
                   observation=t["OBSERVATION"])
        reg = build_registry(ad.raw, set(caps), False)
        req = build_request(ad.banded, reg, BuildContext(caps, ad.banded, False, False), contract)
        if "next_action" not in req.questions:
            continue
        try:
            ra = ask(req.wire(), endpoint=a.a)
            rb = ask(req.wire(), endpoint=a.b)
        except JevError as e:
            print(f"  tick {i}: {e}")
            if e.status in (None, 401):
                return 1
            continue
        asked += 1
        lat["a"].append(ra.latency_ms)
        lat["b"].append(rb.latency_ms)
        models["a"], models["b"] = ra.model or "?", rb.model or "?"
        ca, pa = top(ra.answers.get("next_action", {}))
        cb, pb = top(rb.answers.get("next_action", {}))
        # Would the pick clear the probability gate its commitment class requires?
        for key, (choice, p) in (("a", (ca, pa)), ("b", (cb, pb))):
            rec = reg.get(choice)
            if rec and p >= ENTRY[rec.commitment]:
                gated[key] += 1
        if ca == cb:
            agree += 1
        else:
            diffs.append(f"  tick {i:4d}  A {ca[:30]:<30} {pa:.2f}   B {cb[:30]:<30} {pb:.2f}")

    if not asked:
        print("no comparable ticks (needs a recording with OBSERVATION frames)")
        return 1
    print("=" * 78)
    print(f"{asked} ticks from {a.run_id}")
    print(f"  A  {models.get('a', '?'):<22} {a.a}")
    print(f"  B  {models.get('b', '?'):<22} {a.b}")
    print("=" * 78)
    print(f"same next_action : {agree}/{asked}  ({100 * agree / asked:.0f}%)")
    print(f"pick clears its gate: A {gated['a']}/{asked}   B {gated['b']}/{asked}")
    for k in ("a", "b"):
        v = lat[k]
        print(f"latency {k.upper()}: median {S.median(v):.0f} ms   max {max(v):.0f} ms")
    if diffs:
        print("\ndisagreements:")
        print("\n".join(diffs[:25]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
