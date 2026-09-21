"""Fake mod: the mod side of Protocol v2, in-process.

Implements the full pre-effect validation list from review item 4. Every check runs in the
same step as the effect, and an out-of-range interaction is REJECTED -- never silently
compensated for by starting navigation.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from brain.contract import COMMAND_SPECS, Rejected, State
from brain.identity import Ledger
from brain.registry import VERBS
from brain.tokens import OpportunityStore

MAX_OBSERVATION_AGE_MS = 1200

#: Which verbs each interactable type supports. Guards the verb/target combination.
SUPPORTED = {
    "chest": {"navigate", "open_chest"},
    "shrine_chance": {"navigate", "use_shrine_chance"},
    "shrine_blood": {"navigate", "use_shrine_blood"},
    "teleporter_idle": {"navigate", "start_teleporter"},
    "teleporter_charged": {"navigate", "advance_stage"},
}


#: Types that remain interactable after a successful use. A Shrine of Chance can be used
#: repeatedly in the real game, so marking it spent would let `not_interactable` mask the
#: opportunity-token check that is actually supposed to stop a replay.
REPEATABLE = {"shrine_chance", "shrine_blood"}


@dataclass
class Entity:
    entity_id: str
    type: str
    cost: int
    cost_type: str = "gold"
    distance: float = 30.0
    exists: bool = True
    interactable: bool = True

    @property
    def in_range(self) -> bool:
        return self.distance <= 5.0

    @property
    def repeatable(self) -> bool:
        return self.type in REPEATABLE


@dataclass
class FakeMod:
    run_id: str = "run-221"
    session_id: str = "sess-1"
    scene_gen: int = 14
    phase: str = "pre_teleporter"
    gold: int = 140
    clock: int = 918_000
    capabilities: set = field(default_factory=lambda: {
        "observation_v2", "navigate_target", "interact_target", "search",
        "set_posture", "disengage", "defend_zone"})
    entities: dict = field(default_factory=dict)
    effects: list = field(default_factory=list)
    events: list = field(default_factory=list)
    _obs_n: int = 800
    _event_seq: int = 8800

    def __post_init__(self):
        self.ledger = Ledger(self.run_id)
        self.tokens = OpportunityStore(self.run_id)
        self._observations: dict[str, int] = {}

    # -- world ---------------------------------------------------------------

    def add(self, e: Entity):
        self.entities[e.entity_id] = e

    def tick(self, ms: int = 100):
        self.clock += ms

    def _verb_for(self, e: Entity) -> str | None:
        from brain.registry import TYPE_TO_VERB
        return TYPE_TO_VERB.get(e.type)

    def observe(self) -> dict:
        self._obs_n += 1
        oid = f"obs-{self._obs_n}"
        self._observations[oid] = self.clock
        inter = []
        for e in self.entities.values():
            if not e.exists:
                continue
            row = {
                "entity_id": e.entity_id, "type": e.type, "cost": e.cost,
                "cost_type": e.cost_type, "distance": e.distance,
                "in_interaction_range": e.in_range, "locked": False,
                "path_reachable": True, "reachability_age_ms": 200,
            }
            if e.in_range and e.interactable:
                verb = self._verb_for(e)
                if verb:
                    # Idempotent: same unchanged opportunity -> same token (item 3).
                    row["opportunity_token"] = self.tokens.observe(
                        self.scene_gen, e.entity_id, verb)
            inter.append(row)
        return {
            "type": "OBSERVATION", "session_id": self.session_id,
            "scene_gen": self.scene_gen, "run_id": self.run_id,
            "observation_id": oid, "seq": self._obs_n,
            "t_capture_mono_ms": self.clock,
            "objective": {"phase": self.phase, "teleporter_found": False},
            "inventory": {"gold": self.gold},
            "threat": {"enemies": [], "reposition_feasible": True,
                       "full_withdrawal_feasible": True},
            "hazards": {"taking_environmental_damage": False},
            "interactables": inter,
            "self": {"hp": 104.0, "hp_max": 110.0},
        }

    # -- command intake ------------------------------------------------------

    def send(self, cmd) -> dict:
        payload = {"command": cmd.command, "args": cmd.args,
                   "contract": cmd.execution_contract}
        # Receipt is registered BEFORE execution is enqueued (item 2).
        self.ledger.register_receipt(self.session_id, cmd.command_seq, cmd.command_id)
        verdict, rec = self.ledger.admit(
            self.run_id, cmd.command_id, self.session_id, cmd.command_seq, payload)

        if verdict == "existing":
            return self._ev(cmd.command_id, rec.state, "idempotent_replay")
        if verdict != "execute":
            return self._ev(cmd.command_id, State.REJECTED, verdict)

        try:
            self._validate(cmd)
        except Rejected as r:
            rec.state = State.REJECTED
            return self._ev(cmd.command_id, State.REJECTED, r.reason)

        spec = COMMAND_SPECS[cmd.command]
        if cmd.command == "NAVIGATE_TARGET":
            rec.state = State.NAVIGATING
            return self._ev(cmd.command_id, State.NAVIGATING)
        if cmd.command == "INTERACT_TARGET":
            return self._commit(cmd, rec)
        rec.state = State.COMPLETED
        return self._ev(cmd.command_id, State.COMPLETED)

    def receive_only(self, cmd) -> None:
        """Register receipt without enqueueing execution.

        Models a command that arrived on the wire and is still queued. Receipt must be
        recorded before execution so reconcile can tell this apart from non-arrival.
        """
        self.ledger.register_receipt(self.session_id, cmd.command_seq, cmd.command_id)

    def reconnect(self) -> str:
        """New connection: fresh replay namespace, retained execution identity.

        `watermarks` and `highest_seq` are keyed by session, so the new session starts
        with its own sequence space. `by_execution` is keyed by (run_id, command_id), so
        an action still running in the mod keeps its identity across the drop.
        """
        n = int(self.session_id.split("-")[1]) + 1
        self.session_id = f"sess-{n}"
        return self.session_id

    def suspend(self, command_id: str) -> dict:
        rec = self.ledger.by_execution[(self.run_id, command_id)]
        rec.state = State.SUSPENDED
        return self._ev(command_id, State.SUSPENDED, "preempted_by_safety")

    def resume(self, command_id: str) -> dict:
        rec = self.ledger.by_execution[(self.run_id, command_id)]
        rec.state = State.NAVIGATING
        return self._ev(command_id, State.NAVIGATING, "resumed")

    def arrive(self, command_id: str, entity_id: str) -> dict:
        self.entities[entity_id].distance = 2.0
        rec = self.ledger.by_execution[(self.run_id, command_id)]
        rec.state = State.COMPLETED
        rec.effect = "arrived"
        return self._ev(command_id, State.COMPLETED, "arrived")

    # -- validation + effect, one step (item 4, protocol section 8) ----------

    def _validate(self, cmd):
        c = cmd.execution_contract
        if cmd.command not in ("INTERACT_TARGET", "NAVIGATE_TARGET"):
            return
        tid = cmd.args.get("target_id")
        e = self.entities.get(tid)

        # 6. authorization freshness
        age = self.clock - self._observations.get(cmd.observation_id, 0)
        if age > MAX_OBSERVATION_AGE_MS:
            raise Rejected("authorization_expired")
        if cmd.accept_deadline_mono_ms and self.clock > cmd.accept_deadline_mono_ms:
            raise Rejected("authorization_expired")

        # 2. target exists / interactable
        if e is None or not e.exists:
            raise Rejected("target_gone")
        if cmd.command == "NAVIGATE_TARGET":
            return
        if not e.interactable:
            raise Rejected("not_interactable")

        # 9. args/contract agreement
        if c is None or c.get("target_id") != tid:
            raise Rejected("contract_target_mismatch")
        # 8. verb/target combination supported
        if c["verb"] not in SUPPORTED.get(e.type, set()):
            raise Rejected("contract_unsupported")
        if VERBS[c["verb"]][4] not in self.capabilities:
            raise Rejected("capability_unavailable")
        # 3. phase
        if c["expected_phase"] != self.phase:
            raise Rejected("phase_disallows")
        # 7. CURRENT interaction range -- never compensate by navigating
        if not e.in_range:
            raise Rejected("out_of_range")
        # 4a. cost ceiling authorized by JEV
        if c["cost_limit"] and e.cost > c["cost_limit"]["amount"]:
            raise Rejected("cost_limit_exceeded")
        # 4b. current affordability -- a distinct check from the ceiling
        if e.cost_type == "gold" and e.cost > self.gold:
            raise Rejected("unaffordable")
        # 5. token
        self.tokens.validate(self.scene_gen, tid, c["verb"], c["opportunity_token"])

    def _commit(self, cmd, rec) -> dict:
        rec.state = State.COMMITTING
        c = cmd.execution_contract
        tid = c["target_id"]
        e = self.entities[tid]
        self.gold -= e.cost
        self.tokens.consume(self.scene_gen, tid, c["verb"])
        if not e.repeatable:
            e.interactable = False
        self.effects.append({"verb": c["verb"], "target": tid, "gold_spent": e.cost})
        rec.state = State.COMPLETED
        rec.effect = f"gold_spent:{e.cost}"
        return self._ev(cmd.command_id, State.COMPLETED, f"gold_spent:{e.cost}")

    def _ev(self, command_id, state, reason=None) -> dict:
        self._event_seq += 1
        ev = {"type": "EVENT", "command_id": command_id, "state": state,
              "reason": reason, "event_seq": self._event_seq, "t_mono_ms": self.clock}
        self.events.append(ev)
        return ev
