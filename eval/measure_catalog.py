"""Phase 0 step 6 / check C8: measure the serialized catalog.

Replaces the estimated token figure in the `02-ARCHITECTURE.md` cost model with a
measurement -- and is explicit about which part is measured and which part is still an
estimate.

    MEASURED   bytes and characters of the exact JSON an HTTP client would send.
               Byte-exact, reproducible, no network, no key, no cost.

    ESTIMATED  tokens. JEV's tokenizer is not published and no offline tokenizer for it
               exists, so tokens are BRACKETED between two documented ratios rather than
               reported as a single fake-precise number.

    GROUND TRUTH  `usage.input_tokens` on a real response. One call, on the order of a
                  hundredth of a cent. It cannot be obtained in Phase 0, which has no API
                  key by design, so the first Phase 2 call must capture it and this
                  bracket must be checked against it.

Reporting an invented token count as measured is the exact failure this repo keeps
correcting in itself, so the bracket is carried through to the dollar figures: the cost
model is stated as a range, and the range is honest.

Run: python -m eval.measure_catalog
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from brain.catalog import BuildContext, build_request, load_contract
from brain.registry import build_registry
from eval.fixtures import MILESTONE_1_CAPABILITIES, scenarios

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "contract" / "catalog-size.json"

#: Per-question token estimates from 03-QUESTION-SET.md section 1 ("Budget"), so the
#: measurement can say whether those estimates held.
DOC_ESTIMATE = {
    "next_action": 100, "engagement_response": 150, "strategy": 110,
    "engage_range": 100, "continue_is_worthwhile": 80, "use_equipment_now": 90,
    "situation_is_novel": 70, "worth_gate_each": 80,
}

#: Bracketing ratios, characters per token.
#:
#: 4.0  Typical for English prose under a modern BPE vocabulary. This is the OPTIMISTIC
#:      end: our payload is mostly English sentences.
#: 3.0  Conservative. JSON structure spends tokens badly -- quotes, braces, colons and
#:      snake_case keys like `use_shrine_chance:entity-51` fragment into several tokens
#:      each, and a catalog is denser in punctuation than prose.
#:
#: Both are assumptions about a tokenizer we have not seen. They are labelled as such
#: everywhere they are used.
CHARS_PER_TOKEN_LOW = 4.0
CHARS_PER_TOKEN_HIGH = 3.0

PRICE_PER_M_INPUT = 0.042      # USD, 01-RESEARCH.md. Output tokens are free.


def tok_bracket(chars: int) -> tuple[int, int]:
    return round(chars / CHARS_PER_TOKEN_LOW), round(chars / CHARS_PER_TOKEN_HIGH)


def measure(obj) -> tuple[int, int]:
    s = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    return len(s), len(s.encode("utf-8"))


def word_punct_split(s: str) -> tuple[int, int]:
    """Diagnostic only: how much of the payload is structure rather than prose."""
    words = re.findall(r"[A-Za-z]+", s)
    punct = re.findall(r"[^A-Za-z0-9\s]", s)
    return len(words), len(punct)


def main() -> int:
    contract = load_contract()
    rows = []

    print("=" * 78)
    print("PHASE 0 STEP 6 - SERIALIZED CATALOG MEASUREMENT")
    print("=" * 78)
    print(f"contract     : contract/questions.yaml (schema {contract['schema_version']})")
    print(f"model        : {contract['model']}")
    print(f"capabilities : milestone 1 -- {', '.join(sorted(MILESTONE_1_CAPABILITIES))}")
    print("counsel      : disabled (--jev-only)")
    print()
    print("Bytes are MEASURED. Tokens are ESTIMATED between "
          f"{CHARS_PER_TOKEN_LOW} and {CHARS_PER_TOKEN_HIGH} chars/token; JEV's tokenizer")
    print("is not published. Ground truth is `usage.input_tokens` on the first paid call.")
    print()

    for sc in scenarios():
        reg = build_registry(sc.raw, set(MILESTONE_1_CAPABILITIES), sc.active_task)
        ctx = BuildContext(
            capabilities=frozenset(MILESTONE_1_CAPABILITIES),
            state=sc.banded,
            active_task_exists=sc.active_task,
            counsel_enabled=False,
        )
        req = build_request(sc.banded, reg, ctx, contract)

        cat_chars, cat_bytes = measure(req.questions)
        st_chars, st_bytes = measure(req.state)
        all_chars, all_bytes = measure(req.wire())

        per_q = {}
        for name, q in req.questions.items():
            qc, _ = measure(q)
            per_q[name] = qc

        n_worth = sum(1 for k in req.questions if k.startswith("worth_"))
        rows.append({
            "scenario": sc.name,
            "note": sc.note,
            "questions": len(req.questions),
            "worth_gates": n_worth,
            "registry_records": len(reg),
            "registry_entity_actions": sum(1 for k in reg if ":" in k),
            "catalog_chars": cat_chars, "catalog_bytes": cat_bytes,
            "state_chars": st_chars, "state_bytes": st_bytes,
            "request_chars": all_chars, "request_bytes": all_bytes,
            "catalog_tokens_est": tok_bracket(cat_chars),
            "state_tokens_est": tok_bracket(st_chars),
            "request_tokens_est": tok_bracket(all_chars),
            "per_question_chars": per_q,
            "omitted": req.omitted,
        })

        lo, hi = tok_bracket(all_chars)
        print(f"-- {sc.name}")
        print(f"   {sc.note}")
        n_ent = sum(1 for k in reg if ":" in k)
        print(f"   questions {len(req.questions):2d} ({n_worth} worth gate"
              f"{'' if n_worth == 1 else 's'}), registry {len(reg)} records "
              f"({n_ent} entity actions)")
        print(f"   catalog {cat_bytes:6d} B | state {st_bytes:5d} B | "
              f"request {all_bytes:6d} B  ->  {lo}-{hi} tokens (est)")
        if req.omitted:
            drops = ", ".join(
                f"{o['question']}({o['reason']})" for o in req.omitted
            )
            print(f"   omitted: {drops}")
        print()

    # ---- per-question comparison against the documented estimates ------------
    print("-" * 78)
    print("PER-QUESTION SIZE vs docs/03 BUDGET TABLE")
    print("-" * 78)
    print(f"{'question':28s} {'chars':>7s} {'est tokens':>12s} {'doc est':>9s} {'':>4s}")
    seen: dict[str, int] = {}
    for r in rows:
        for name, chars in r["per_question_chars"].items():
            key = "worth_<action_id>" if name.startswith("worth_") else name
            seen[key] = max(seen.get(key, 0), chars)
    for name, chars in sorted(seen.items(), key=lambda kv: -kv[1]):
        lo, hi = tok_bracket(chars)
        doc = DOC_ESTIMATE.get(
            "worth_gate_each" if name == "worth_<action_id>" else name
        )
        if doc is None:
            verdict = ""
        elif lo > doc * 1.25:
            verdict = "OVER"
        elif hi < doc * 0.75:
            verdict = "under"
        else:
            verdict = "ok"
        shown = f"{doc}" if doc else "-"
        print(f"{name:28s} {chars:7d} {f'{lo}-{hi}':>12s} {shown:>9s} {verdict:>4s}")
    print()
    print("`next_action` is measured WITH its generated options, which is why it exceeds a")
    print("doc estimate written as \"~100 + generated options\". The others are directly")
    print("comparable.")
    print()

    # ---- cost model ---------------------------------------------------------
    m1 = {r["scenario"]: r for r in rows}
    typical = m1["chest_in_range"]
    ceiling = m1["ceiling_twelve"]
    floor = m1["floor_undiscovered"]

    print("-" * 78)
    print("COST MODEL, RECOMPUTED FROM MEASURED BYTES")
    print("-" * 78)
    print(f"{'tick shape':22s} {'req bytes':>10s} {'est tokens':>12s} "
          f"{'$/hr @2Hz':>11s} {'$/40min @2Hz':>13s}")
    for label, r in (("floor (idle)", floor), ("typical", typical), ("ceiling (12)", ceiling)):
        lo, hi = r["request_tokens_est"]
        per_hr_lo = lo * 7200 * PRICE_PER_M_INPUT / 1e6
        per_hr_hi = hi * 7200 * PRICE_PER_M_INPUT / 1e6
        print(f"{label:22s} {r['request_bytes']:10d} {f'{lo}-{hi}':>12s} "
              f"{f'${per_hr_lo:.2f}-{per_hr_hi:.2f}':>11s} "
              f"{f'${per_hr_lo*2/3:.2f}-{per_hr_hi*2/3:.2f}':>13s}")
    print()
    print("Rates from 02-ARCHITECTURE.md: 1 Hz idle throttle, 2 Hz nominal, 4 Hz ceiling.")
    print("A 40-minute run is two thirds of an hour.")
    print()

    tlo, thi = typical["request_tokens_est"]
    print(f"The cost model assumed ~2,000 input tokens per call (state ~700 + catalog")
    print(f"~1,300). Measured typical tick: {typical['request_bytes']} bytes, estimated "
          f"{tlo}-{thi} tokens")
    print(f"(state {typical['state_bytes']} B, catalog {typical['catalog_bytes']} B).")
    print()

    record = {
        "measured_at_phase": 0,
        "contract_schema_version": contract["schema_version"],
        "model": contract["model"],
        "capabilities": sorted(MILESTONE_1_CAPABILITIES),
        "counsel_enabled": False,
        "method": {
            "bytes": "exact, json.dumps compact separators, UTF-8",
            "tokens": "ESTIMATED, bracketed",
            "chars_per_token_low": CHARS_PER_TOKEN_LOW,
            "chars_per_token_high": CHARS_PER_TOKEN_HIGH,
            "ground_truth": "usage.input_tokens on the first Phase 2 call -- NOT YET TAKEN",
        },
        "price_per_m_input_usd": PRICE_PER_M_INPUT,
        "scenarios": rows,
    }
    OUT.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
    print()
    print("=" * 78)
    print("DISPOSITION: catalog size MEASURED in bytes. Token figure remains an ESTIMATE")
    print("until a real response supplies usage.input_tokens. C8 is satisfied for bytes")
    print("and explicitly NOT satisfied for tokens.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
