"""Command identity, replay rejection, and reconnect reconciliation.

Resolves review item 2. Revision 4 mixed per-connection sequence numbers, a watermark
returned on a NEW connection, and execution identity that survives reconnects. Those are
three different scopes; conflating them let an old active command fall below a watermark
advanced by later completions and be wrongly declared history-expired.

Identity model:
    execution key = (run_id, command_id)          -- survives reconnects
    replay key    = (origin_session_id, command_seq) -- per connection, own sequence space
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from .contract import TERMINAL, State


def payload_digest(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


@dataclass
class CommandRecord:
    run_id: str
    command_id: str
    origin_session_id: str
    command_seq: int
    digest: str
    state: State
    effect: str | None = None

    @property
    def execution_key(self) -> tuple[str, str]:
        return (self.run_id, self.command_id)

    @property
    def replay_key(self) -> tuple[str, int]:
        return (self.origin_session_id, self.command_seq)

    @property
    def is_active(self) -> bool:
        return self.state not in TERMINAL


class Ledger:
    """Mod-side command table with a bounded replay window per origin session.

    Two ordering rules matter, and both were missing in revision 4:

    1. A retained ACTIVE command is resolved before any watermark rule. An old active
       command must never be declared `command_history_expired` because later commands
       completed and advanced the watermark past it.
    2. Receipt is registered before execution is enqueued, so `not_received` can be
       proven. Absence from the execution table alone is not proof of non-arrival --
       the command may simply still be queued.
    """

    def __init__(self, run_id: str, window: int = 8):
        self.run_id = run_id
        self.window = window
        self.by_execution: dict[tuple[str, str], CommandRecord] = {}
        #: receipt ledger: replay_key -> command_id, written on arrival, before execution
        self.receipts: dict[tuple[str, int], str] = {}
        #: command_ids that actually entered execution. Distinguishes "received but still
        #: queued" from "executed then aged out of the window" -- without this, a queued
        #: command and an evicted one are indistinguishable at reconcile time.
        self.admitted: set[str] = set()
        #: per origin session: highest evicted seq. Replay namespaces are per session;
        #: execution identity (run_id, command_id) is not, so it survives reconnects.
        self.watermarks: dict[str, int] = {}
        self.highest_seq: dict[str, int] = {}

    # -- arrival -------------------------------------------------------------

    def register_receipt(self, session_id: str, seq: int, command_id: str) -> None:
        self.receipts[(session_id, seq)] = command_id
        self.highest_seq[session_id] = max(self.highest_seq.get(session_id, -1), seq)

    def admit(
        self, run_id: str, command_id: str, session_id: str, seq: int, payload: dict
    ) -> tuple[str, CommandRecord | None]:
        """Return (verdict, record). Verdict is 'execute', 'existing', or a reject reason."""
        digest = payload_digest(payload)
        exec_key = (run_id, command_id)
        existing = self.by_execution.get(exec_key)

        # Rule 1: an active command is found before eviction rules apply.
        if existing is not None and existing.is_active:
            if existing.digest != digest or existing.command_seq != seq:
                return "duplicate_id_conflict", existing
            return "existing", existing

        if existing is not None:  # terminal, still retained
            if existing.digest != digest or existing.command_seq != seq:
                return "duplicate_id_conflict", existing
            return "existing", existing

        # Known command_id reused under a different sequence or payload is a conflict
        # even if the original record was evicted, provided we still hold its receipt.
        receipted = self.receipts.get((session_id, seq))
        if receipted is not None and receipted != command_id:
            return "duplicate_id_conflict", None

        # Rule 2: only now do watermark rules apply, and only to evicted history.
        wm = self.watermarks.get(session_id, -1)
        if seq <= wm:
            return "command_history_expired", None

        record = CommandRecord(run_id, command_id, session_id, seq, digest, State.SUBMITTED)
        self.by_execution[exec_key] = record
        self.admitted.add(command_id)
        self._evict(session_id)
        return "execute", record

    def _evict(self, session_id: str) -> None:
        """Evict terminal records outside the window, advancing the watermark.

        Active records are never evicted, so the watermark can never pass one.
        """
        live = sorted(
            (r for r in self.by_execution.values() if r.origin_session_id == session_id),
            key=lambda r: r.command_seq,
        )
        terminal = [r for r in live if not r.is_active]
        oldest_active = min((r.command_seq for r in live if r.is_active), default=None)

        while len(live) > self.window and terminal:
            victim = terminal.pop(0)
            if oldest_active is not None and victim.command_seq > oldest_active:
                break  # never advance the watermark past a live command
            del self.by_execution[victim.execution_key]
            self.watermarks[session_id] = max(
                self.watermarks.get(session_id, -1), victim.command_seq
            )
            live.remove(victim)

    # -- reconnect -----------------------------------------------------------

    def reconcile(self, run_id: str, command_id: str, session_id: str) -> dict:
        """Per-command reconciliation with explicit, honest outcomes."""
        rec = self.by_execution.get((run_id, command_id))
        if rec is not None:
            if rec.is_active:
                return {"outcome": "active", "state": rec.state.value}
            return {"outcome": "terminal", "state": rec.state.value, "effect": rec.effect}

        # Admitted but no longer in the window: executed at some point, detail aged out.
        if command_id in self.admitted:
            return {"outcome": "history_unavailable"}

        # Receipted but never admitted: it arrived and is still queued. Reporting
        # `not_received` here would be a lie that invites a duplicate consequential
        # command -- absence from the execution table is not absence from the wire.
        if command_id in {cid for (s, _), cid in self.receipts.items() if s == session_id}:
            return {"outcome": "received_pending"}

        # Provable non-arrival: the replay window for this session is live (nothing has
        # been evicted past the highest seq we have seen) and no receipt names it.
        wm = self.watermarks.get(session_id, -1)
        if self.highest_seq.get(session_id, -1) > wm:
            return {"outcome": "not_received"}
        return {"outcome": "history_unavailable"}
