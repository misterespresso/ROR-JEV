"""Generate contract/actions.yaml from brain/registry.py.

The verb taxonomy lives in code, so the registry builder and the published contract
cannot drift. Run: python -m eval.gen_actions
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from brain.registry import (
    ENTRY, MAX_ENTITY_ACTIONS, TYPE_TO_VERB, VERBS, WORTH_THRESHOLD,
)

HEADER = (
    "# GENERATED from brain/registry.py by `python -m eval.gen_actions`. "
    "Do not edit by hand.\n"
    "# The verb taxonomy lives in code so the registry builder and this file "
    "cannot drift.\n"
)


def render() -> str:
    out = [HEADER + "schema_version: 1", "", "commitment_gates:"]
    for k, v in ENTRY.items():
        req = str(k == "irreversible").lower()
        out.append(f"  {k}: {{ entry: {v}, worth_gate_required: {req} }}")
    out += [
        "",
        f"worth_threshold: {WORTH_THRESHOLD}",
        "",
        "# Registry invariant 9. Ranking is in-range first, then nearest, then entity ID;",
        "# everything the cap cuts is recorded so Phase 5 can ask what was never offered.",
        f"max_entity_actions: {MAX_ENTITY_ACTIONS}",
        "",
        "verbs:",
    ]
    for verb, (cmd, commit, rng, worth, cap) in VERBS.items():
        out += [
            f"  {verb}:",
            f"    command: {cmd}",
            f"    commitment: {commit}",
            f"    range_gated: {str(rng).lower()}",
            f"    requires_worth_gate: {str(worth).lower()}",
            f"    capability: {cap}",
        ]
    out += [
        "",
        "sentinels:",
        "  none: { serializes: false, capability_exempt: true }",
        "  continue_current_action: { serializes: false, capability_exempt: true,",
        "                             offered_when: active_task_exists }",
        "",
        "type_to_verb:",
    ]
    for t, v in TYPE_TO_VERB.items():
        out.append(f"  {t}: {v}")
    return "\n".join(out) + "\n"


def main() -> int:
    path = pathlib.Path(__file__).resolve().parents[1] / "contract" / "actions.yaml"
    new = render()
    if "--check" in sys.argv:
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        if current != new:
            print("contract/actions.yaml is stale; run: python -m eval.gen_actions")
            return 1
        print("contract/actions.yaml up to date")
        return 0
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(new)
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
