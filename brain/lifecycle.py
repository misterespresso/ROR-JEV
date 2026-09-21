"""Brain-side view of the task slot.

Resolves review items 5 and 6:
  * `submitted` reserves the slot at enqueue time, so a second decision arriving before
    `accepted` cannot submit a second task.
  * Suspension restores the state it interrupted rather than a generic `navigating`.
  * The slot is released only on an authoritative terminal outcome; a racing `completed`
    clears a locally pending cancellation.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .contract import COMMAND_SPECS, OCCUPYING, RESUMABLE_FROM, TERMINAL, State


@dataclass
class Task:
    command_id: str
    action_id: str
    command: str
    state: State = State.SUBMITTED
    prior_state: State | None = None      # preserved across suspension
    cancel_pending: bool = False


@dataclass
class Lifecycle:
    task: Task | None = None
    journal: list[dict] = field(default_factory=list)

    # -- queries -------------------------------------------------------------

    def task_occupied(self) -> bool:
        return self.task is not None and self.task.state in OCCUPYING

    def has_active_task(self) -> bool:
        return self.task_occupied()

    # -- brain side ----------------------------------------------------------

    def reserve(self, command_id: str, action_id: str, command: str) -> None:
        """Reserve the slot at enqueue time (review item 6)."""
        if not COMMAND_SPECS[command].occupies_task_slot:
            return
        assert not self.task_occupied(), "slot already held: double submission"
        self.task = Task(command_id, action_id, command, State.SUBMITTED)

    def journal_intent(self, kind: str, detail: dict | None = None) -> None:
        self.journal.append({"intent": kind, **(detail or {})})

    def mark_cancel_pending(self) -> None:
        if self.task is not None:
            self.task.cancel_pending = True

    # -- mod events ----------------------------------------------------------

    def on_event(self, command_id: str, state: State, reason: str | None = None) -> None:
        t = self.task
        if t is None or t.command_id != command_id:
            return  # event for a superseded command; ignore (T19)

        if state is State.SUSPENDED:
            if t.state in RESUMABLE_FROM:
                t.prior_state = t.state
            t.state = State.SUSPENDED
            return

        if state is State.NAVIGATING and t.state is State.SUSPENDED:
            # `resumed` restores what was interrupted, not a generic navigating.
            t.state = t.prior_state or State.NAVIGATING
            t.prior_state = None
            return

        t.state = state

        if state in TERMINAL:
            # A racing `completed` clears a locally pending cancellation: the slot is
            # released by the authoritative outcome, not by the cancel we asked for.
            self.journal.append(
                {"terminal": state.value, "command_id": command_id, "reason": reason,
                 "cancel_was_pending": t.cancel_pending})
            self.task = None
