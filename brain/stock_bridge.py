"""Translate the dispatcher's protocol-v2 commands into what the fork actually accepts.

Scaffolding, like `stock_adapter`: the real design has the mod speak v2. Until then the
dispatcher's commands are mapped onto the fork's command set (stock's plus build 8's
`INTERACT_ID`), and anything with no counterpart returns None so the loop records it as
unsupported rather than approximating it.

`holds_slot` is True only when the mod will run a bounded task that ends in an event or a
visible change (an object used). `MODE` switches are standing behaviours with no end, so they
release the slot at once and JEV re-decides next tick.
"""
from __future__ import annotations

from dataclasses import dataclass

from .policy import Command


@dataclass(frozen=True)
class ModCommand:
    command: str
    args: str
    holds_slot: bool
    #: What the mod's events will name this command as, to match them to the task.
    label: str


def instance_id(target_id: str) -> str | None:
    """`chest_small@-96072` -> `-96072`. Build 6+ entity IDs are `<type>@<instance id>`
    (`stock_adapter`); stock's positional IDs (`chest-0`) have no `@` and name nothing the
    mod can find, so they return None."""
    _typ, sep, rest = target_id.partition("@")
    if not sep:
        return None
    try:
        int(rest)
    except ValueError:
        return None
    return rest


def to_mod(cmd: Command) -> ModCommand | None:
    c, a = cmd.command, cmd.args or {}
    tid = str(a.get("target_id") or "")
    if c == "NAVIGATE_TARGET":
        # F35: walking only. A navigate pick used to reach FIND_AND_INTERACT, which presses the
        # button on arrival -- in jev-01 it started the teleporter JEV had just judged not worth
        # starting (worth gate 0.24-0.26), and the run died.
        if tid.startswith("teleporter"):
            return ModCommand("GOTO_ID", "teleporter", True, "GOTO_ID:teleporter")
        iid = instance_id(tid)
        return ModCommand("GOTO_ID", iid, True, f"GOTO_ID:{iid}") if iid is not None else None
    if c == "INTERACT_TARGET":
        # Reached only through the dispatcher's probability and worth gates.
        if tid.startswith("teleporter"):
            return ModCommand("FIND_AND_INTERACT", "teleporter", True,
                              "FIND_AND_INTERACT:teleporter")
        iid = instance_id(tid)
        if iid is not None:
            return ModCommand("INTERACT_ID", iid, True, f"INTERACT_ID:{iid}")
        # A stock-list target (no build-6 frame) in range: the nearest of its kind is it.
        kind = "chest" if tid.startswith("chest") else "shrine" if tid.startswith("shrine") else None
        return (ModCommand("FIND_AND_INTERACT", kind, True, f"FIND_AND_INTERACT:{kind}")
                if kind else None)
    if c == "SEARCH":
        return ModCommand("MODE", "roam", False, "MODE:roam")
    if c == "DEFEND_ZONE":
        return ModCommand("MODE", "defend_zone", False, "MODE:defend_zone")
    if c == "SET_POSTURE":
        s = a.get("strategy")
        return ModCommand("STRATEGY", s, False, f"STRATEGY:{s}") if s else None
    if c == "CANCEL":
        return ModCommand("GOTO", "cancel", False, "GOTO:cancel")
    return None     # FIGHT_TARGET, DISENGAGE, USE_EQUIPMENT: no fork counterpart yet
