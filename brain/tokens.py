"""Opportunity tokens.

Resolves review item 3. A token must identify an *opportunity*, not a snapshot: repeated
observations of the same unchanged interaction opportunity expose the SAME token. Minting
a fresh token per observation would let two observations taken before one shrine use yield
two tokens that each authorize that same use.

Tokens are bound to (run, scene, entity, interaction_kind) -- a teleporter's `start` and
`advance_stage` interactions must not share authorization.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class OppState(str, Enum):
    OPEN = "open"
    CONSUMED = "consumed"
    #: Effect outcome unknown (e.g. connection lost mid-commit). No new token is issued
    #: until reconciled -- we do not guess in the direction of spending again.
    QUARANTINED = "quarantined"


@dataclass
class Opportunity:
    key: tuple[str, int, str, str]
    token: str
    state: OppState = OppState.OPEN
    generation: int = 0


class OpportunityStore:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self._by_key: dict[tuple[str, int, str, str], Opportunity] = {}

    @staticmethod
    def _mint(key: tuple[str, int, str, str], generation: int) -> str:
        _run, scene, entity, kind = key
        return f"opp-{entity}-{kind}-{scene}-{generation}"

    def observe(self, scene_gen: int, entity_id: str, kind: str) -> str | None:
        """Token to expose for this opportunity in an observation.

        Idempotent: the same unchanged opportunity returns the same token every time.
        Returns None when the opportunity is consumed or quarantined, which removes the
        action from the registry entirely.
        """
        key = (self.run_id, scene_gen, entity_id, kind)
        opp = self._by_key.get(key)
        if opp is None:
            opp = Opportunity(key, self._mint(key, 0))
            self._by_key[key] = opp
        return opp.token if opp.state is OppState.OPEN else None

    def validate(self, scene_gen: int, entity_id: str, kind: str, token: str) -> None:
        from .contract import Rejected

        key = (self.run_id, scene_gen, entity_id, kind)
        opp = self._by_key.get(key)
        if opp is None:
            raise Rejected("opportunity_unknown")
        if opp.state is OppState.CONSUMED:
            raise Rejected("opportunity_consumed")
        if opp.state is OppState.QUARANTINED:
            raise Rejected("opportunity_quarantined")
        if token != opp.token:
            raise Rejected("opportunity_token_mismatch")

    def consume(self, scene_gen: int, entity_id: str, kind: str) -> None:
        self._by_key[(self.run_id, scene_gen, entity_id, kind)].state = OppState.CONSUMED

    def quarantine(self, scene_gen: int, entity_id: str, kind: str) -> None:
        self._by_key[(self.run_id, scene_gen, entity_id, kind)].state = OppState.QUARANTINED

    def renew(self, scene_gen: int, entity_id: str, kind: str) -> str:
        """The game exposed a genuinely new opportunity (e.g. a repeatable shrine reset).

        Only the mod calls this, from game state -- never the brain, and never merely
        because a new observation was taken.
        """
        key = (self.run_id, scene_gen, entity_id, kind)
        prev = self._by_key.get(key)
        gen = (prev.generation + 1) if prev else 0
        self._by_key[key] = Opportunity(key, self._mint(key, gen), OppState.OPEN, gen)
        return self._by_key[key].token
