"""Protocol v2 contract: states, completion rules, slot occupancy.

Resolves review item 5 (command-specific completion rules) as data rather than prose.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class State(str, Enum):
    # Local-only: the brain has enqueued a task command but the mod has not yet accepted it.
    # Without this, a second decision can arrive before `accepted` and submit a second task.
    SUBMITTED = "submitted"

    ACCEPTED = "accepted"
    NAVIGATING = "navigating"
    COMMITTING = "committing"
    SUSPENDED = "suspended"
    CANCELLING = "cancelling"

    COMPLETED = "completed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    FAILED = "failed"


TERMINAL = {State.COMPLETED, State.REJECTED, State.CANCELLED, State.FAILED}

#: States in which the single task slot is held. The slot is released only on an
#: authoritative terminal outcome -- not merely on `cancelled`, because a racing
#: `completed` must be able to clear a locally pending cancellation.
OCCUPYING = {
    State.SUBMITTED,
    State.ACCEPTED,
    State.NAVIGATING,
    State.COMMITTING,
    State.SUSPENDED,
    State.CANCELLING,
}

#: `committing` is the point of no return: the effect is in flight.
NON_CANCELLABLE = {State.COMMITTING}


@dataclass(frozen=True)
class CommandSpec:
    """Per-command completion semantics."""

    name: str
    occupies_task_slot: bool
    completion: str
    bounded_deadline_ms: int | None = None


COMMAND_SPECS: dict[str, CommandSpec] = {
    "NAVIGATE_TARGET": CommandSpec(
        "NAVIGATE_TARGET", True, "arrived within interaction range, or stalled", 60_000
    ),
    "INTERACT_TARGET": CommandSpec(
        "INTERACT_TARGET", True, "effect applied, or pre-effect validation failed", 5_000
    ),
    "PICKUP_TARGET": CommandSpec(
        "PICKUP_TARGET", True, "named equipment collected, gone, or deadline", 12_000
    ),
    "SEARCH": CommandSpec(
        "SEARCH", True, "target discovered, search segment exhausted, or deadline", 45_000
    ),
    "DEFEND_ZONE": CommandSpec(
        "DEFEND_ZONE", True, "holdout complete, phase changed, cancelled, or failed", None
    ),
    "FIGHT_TARGET": CommandSpec(
        "FIGHT_TARGET", True, "target defeated, target invalidated, cancelled, or failed", None
    ),
    # Standing combat stance (jev-02): no end of its own, so it never holds the task slot and
    # JEV re-decides it every tick, like SEARCH under the fork's MODE:roam.
    "ENGAGE_NEARBY": CommandSpec(
        "ENGAGE_NEARBY", False, "stance applied; fighting continues until another choice", 1_000
    ),
    "DISENGAGE": CommandSpec(
        "DISENGAGE", False, "withdrawal condition reached, or bounded deadline", 8_000
    ),
    "SET_TARGET_PRIORITY": CommandSpec(
        "SET_TARGET_PRIORITY", False, "target priority applied", 1_000
    ),
    # Posture and equipment never occupy the task slot: they are orthogonal channels.
    "SET_POSTURE": CommandSpec("SET_POSTURE", False, "posture update applied", 1_000),
    "USE_EQUIPMENT": CommandSpec(
        "USE_EQUIPMENT", False, "activation accepted by the game, or explicit failure", 2_000
    ),
}


#: Suspension must restore the state it interrupted. A generic
#: `suspended -> navigating` edge would resume an accepted interaction into navigation.
RESUMABLE_FROM = {State.ACCEPTED, State.NAVIGATING}


class Rejected(Exception):
    """Mod-side refusal. Carries a protocol reason code."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason
