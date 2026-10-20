"""Build the JEV request from `contract/questions.yaml`.

Phase 0 needs this to satisfy roadmap step 6 and check C8: the serialized catalog size
cannot be measured without serializing the catalog, and the catalog cannot be serialized
while its text lives only in prose. The payload text now lives in the contract file and
this module renders it into wire shape.

Scope note: Phase 2 owns the production catalog (`brain/questions.py` in the roadmap).
This module is the *measurement instrument* and the gating logic that measurement requires
-- request building, `enabled_when` evaluation, and the omit-never-null rule. It makes no
network call and has no notion of a client.

Two rules from `02-ARCHITECTURE.md` are enforced here rather than trusted:

  * **Omit, never null.** A question referencing an `unknown` field is dropped from the
    request entirely. A dropped question yields no authorization, never a default.
  * **Mask illegal options.** Questions whose capability is absent never appear, and
    `next_action` options come only from the capability-gated registry.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .interactable_type import label
from .registry import ActionRecord, SENTINELS

CONTRACT = Path(__file__).resolve().parents[1] / "contract" / "questions.yaml"

#: Sentinel for a telemetry field the mod could not read. Never a reassuring default.
UNKNOWN = "unknown"


def load_contract(path: Path | None = None) -> dict:
    return yaml.safe_load((path or CONTRACT).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# enabled_when
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BuildContext:
    """Everything `enabled_when` is allowed to read.

    Deliberately not the whole world: a condition that could read another question's
    answer would reintroduce the answer-chaining defect that C4 exists to prevent.
    """

    capabilities: frozenset[str]
    state: dict                      # the BANDED state, as sent to JEV
    active_task_exists: bool = False
    counsel_enabled: bool = False


class ConditionError(ValueError):
    """An `enabled_when` form the evaluator does not recognise.

    Raised rather than defaulted in either direction. Defaulting true would send a
    question the contract may not permit; defaulting false would silently drop one the
    contract requires. Both are worse than a loud failure at build time.
    """


def _atom(tok: str, ctx: BuildContext) -> bool:
    tok = tok.strip()
    if tok == "always":
        return True
    if tok == "active_task_exists":
        return ctx.active_task_exists
    if tok == "counsel_enabled":
        return ctx.counsel_enabled
    if tok.startswith("capability(") and tok.endswith(")"):
        return tok[len("capability("):-1].strip() in ctx.capabilities
    # A bare dotted path is a truthiness test on a banded state field. Used by the
    # equipment contract's four fields (03-QUESTION-SET.md section 3).
    if "." in tok or tok.isidentifier():
        val = resolve(ctx.state, tok)
        if val is _MISSING or val == UNKNOWN:
            return False
        return bool(val)
    raise ConditionError(tok)


def evaluate_condition(expr: str, ctx: BuildContext) -> bool:
    """Evaluate the small `enabled_when` grammar: atoms joined by `and`.

    No `or`, no negation, no parentheses -- the contract does not use them, and an
    expression language nobody audits is how a question ends up sent in a configuration
    where nothing reads it.
    """
    expr = " ".join(expr.split())
    if " or " in expr or "not " in expr or "(" in expr.replace("capability(", ""):
        raise ConditionError(expr)
    return all(_atom(t, ctx) for t in expr.split(" and "))


# ---------------------------------------------------------------------------
# state field resolution
# ---------------------------------------------------------------------------

_MISSING = object()


def resolve(state: dict, dotted: str) -> Any:
    cur: Any = state
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return _MISSING
        cur = cur[part]
    return cur


def strip_unknown(v: Any) -> Any:
    """Drop every `unknown` value, at any depth, from the state sent to JEV (F30).

    Gating runs on the UNSTRIPPED state first, so a question declaring a whole block is still
    omitted when any part of it is unknown. Stripping only stops a narrowly-declared question
    from being shown fields it never declared and cannot use.
    """
    if isinstance(v, dict):
        return {k: strip_unknown(x) for k, x in v.items() if x != UNKNOWN}
    if isinstance(v, list):
        return [strip_unknown(x) for x in v if x != UNKNOWN]
    return v


def _telemetry_ok(fields: list[str], state: dict) -> tuple[bool, list[str]]:
    """A question is sendable only if every field it names is readable.

    `unknown` and absent are treated identically: the question is omitted. Note that an
    EMPTY container is fine -- `hazards: {active_dots: []}` means "measured, nothing
    there", which is a fact, not missing telemetry.
    """
    bad = []
    for f in fields or []:
        v = resolve(state, f)
        if v is _MISSING or v == UNKNOWN or (isinstance(v, dict) and UNKNOWN in v.values()):
            bad.append(f)
    return (not bad), bad


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def _render_next_action(
    payload: dict, registry: dict[str, ActionRecord], state: dict, active_task: bool
) -> dict:
    """Render `next_action`, whose options are generated from the registry.

    Registry invariant 3 holds by construction: every option key other than a sentinel is
    a registry key, because that is where the keys come from.
    """
    templates = payload["option_templates"]
    criteria: dict[str, dict] = {}

    for action_id, rec in sorted(registry.items()):
        tpl = templates.get(rec.verb)
        if tpl is None:
            raise ConditionError(
                f"no option text for verb {rec.verb!r}: the registry offers an action the "
                f"catalog cannot describe, so JEV would be asked to pick a blank option"
            )
        criteria[action_id] = {
            k: v.format(**_option_vars(rec, state)) for k, v in tpl.items()
        }

    sentinels = payload["sentinels"]
    criteria["none"] = dict(sentinels["none"])
    if active_task:
        # Invariant 2: offered iff a valid non-terminal task exists.
        criteria["continue_current_action"] = dict(sentinels["continue_current_action"])

    return {"type": "choice", "instructions": payload["instructions"], "criteria": criteria}


def _option_vars(rec: ActionRecord, state: dict) -> dict:
    """Substitution variables available to option text. Bands only, never numbers.

    `target` names what an option points at (F36): the candidate's type with its cost and
    distance bands, e.g. "the small chest (cheap, close)" -- so options differ by more than
    their keys.
    """
    band, target, item = UNKNOWN, "this target", "the item on offer"
    tid = rec.args.get("target_id")
    for cand in state.get("economy", {}).get("candidates", []):
        if cand.get("id") == tid:
            band = cand.get("cost_band", UNKNOWN)
            kind = label(str(cand.get("kind") or cand.get("type", "target")))
            dist = cand.get("distance_band", UNKNOWN)
            if cand.get("item"):
                item = cand["item"]
            if kind.startswith("teleporter"):
                target = f"the teleporter ({dist})"
            elif cand.get("item"):
                target = f"the {kind} selling {item} ({band}, {dist})"
            else:
                target = f"the {kind} ({band}, {dist})"
    carried = state.get("self", {}).get("equipment") or {}
    carried = carried.get("what_it_does") or carried.get("name") or "an empty equipment slot"
    return {"cost_band": band, "entity_id": rec.args.get("target_id", ""), "target": target,
            "item": item, "carried_equipment": carried}


def _render_worth_gates(payload: dict, registry: dict[str, ActionRecord]) -> dict:
    out = {}
    for action_id, rec in sorted(registry.items()):
        if not rec.requires_worth_gate:
            continue
        tpl = payload["option_templates"].get(rec.verb)
        if tpl is None:
            raise ConditionError(
                f"{action_id} requires a worth gate but no worth-question text exists for "
                f"verb {rec.verb!r}. Fail closed: the action would never be authorizable."
            )
        eid = rec.args.get("target_id", "")
        out[rec.worth_question] = {
            "type": "noul",
            "instructions": tpl["instructions"].format(entity_id=eid),
            "criteria": dict(tpl["criteria"]),
        }
    return out


@dataclass
class BuiltRequest:
    """A JEV request plus the record of what was left out and why."""

    questions: dict = field(default_factory=dict)
    state: dict = field(default_factory=dict)
    model: str = ""
    omitted: list[dict] = field(default_factory=list)

    def wire(self) -> dict:
        return {"model": self.model, "state": self.state, "questions": self.questions}

    def serialized(self) -> str:
        """Compact JSON, as an HTTP client would send it."""
        return json.dumps(self.wire(), separators=(",", ":"), ensure_ascii=False)


def build_request(
    state: dict,
    registry: dict[str, ActionRecord],
    ctx: BuildContext,
    contract: dict | None = None,
) -> BuiltRequest:
    """Build one fan-out request. Every question is gated; nothing is sent by default."""
    c = contract or load_contract()
    payload = c["payload"]
    out = BuiltRequest(state=strip_unknown(state), model=c["model"])
    if c.get("game_primer"):
        # Static facts about the game, the same every tick (jev-02). Not telemetry: never
        # unknown, never gating anything, and first in the state so it reads as context.
        out.state = {"game": list(c["game_primer"]), **out.state}

    # F32: nothing is asked of a dead character. Every question would be about a body that
    # cannot act, and `next_action` does not declare `self.hp_band`, so the field contract
    # alone would let it through.
    if resolve(state, "self.hp_band") == "dead":
        out.omitted = [{"question": name, "reason": "dead"} for name in c["questions"]]
        return out

    for name, spec in c["questions"].items():
        cond = str(spec.get("enabled_when", "always"))
        if not evaluate_condition(cond, ctx):
            out.omitted.append({"question": name, "reason": "enabled_when", "condition": cond})
            continue

        ok, bad = _telemetry_ok(spec.get("required_state_fields", []), state)
        if not ok:
            # Omit, never null: no authorization from this question this tick.
            out.omitted.append({"question": name, "reason": "telemetry_unknown", "fields": bad})
            continue

        p = payload[name]
        if spec.get("dynamic"):
            out.questions[name] = _render_next_action(
                p, registry, state, ctx.active_task_exists
            )
        else:
            out.questions[name] = {
                "type": spec["type"],
                "instructions": p["instructions"],
                "criteria": dict(p["criteria"]),
            }

    # Worth gates ride along with next_action: a gate for an action nobody can select is
    # cost with no consumer, and an action selectable with no gate cannot be authorized.
    if "next_action" in out.questions:
        gate_state_fields = (
            c["dynamic_question_templates"]["worth_gate"].get("required_state_fields", [])
        )
        ok, bad = _telemetry_ok(gate_state_fields, state)
        if ok:
            out.questions.update(_render_worth_gates(payload["worth_gate"], registry))
        else:
            # Fail closed. Irreversible actions stay selectable but unauthorizable, which
            # the dispatcher already records as `not_authorized: gate_missing` (T1).
            out.omitted.append(
                {"question": "worth_*", "reason": "telemetry_unknown", "fields": bad}
            )

    return out
