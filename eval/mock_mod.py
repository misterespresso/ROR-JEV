"""Mock of stock Rainflayer's C# side, for harness self-tests.

Dials ROR_JEV_PORT (default 17777 -- NEVER the game's 7777, so a test can never reach, or be
answered by, a running game). Modes:
  ok          movement, and action_complete after FIND_AND_INTERACT
  noterminal  reproduces fork-02: FIND_AND_INTERACT teleporter -> action_started, the
              objective flips to charging_teleporter, and NO terminal event ever arrives
"""
import json, os, socket, sys, threading, time

MODE = sys.argv[1] if len(sys.argv) > 1 else "ok"
PORT = int(os.environ.get("ROR_JEV_PORT", "17777"))
assert PORT != 7777, "mock refuses to use the game's port"
LOBBY_S = float(os.environ.get("MOCK_LOBBY_S", "0"))
state = {"t_connect": 0.0, "moving": False, "t0": 0.0, "objective": "exploring", "tp_started_at": None,
         "cmds": []}


def dist(base):
    if not state["moving"]:
        return base
    return max(1.0, base - (time.monotonic() - state["t0"]) * 2.0)


def in_lobby():
    return time.monotonic() - state["t_connect"] < LOBBY_S


def handle(msg):
    t = msg.get("type")
    if in_lobby():
        # Reproduces stock's lobby behaviour exactly: OBJECTIVE lies, INVENTORY does not.
        if t == "QUERY_OBJECTIVE":
            return {"type": "OBJECTIVE", "objective": "exploring", "scene_name": "lobby",
                    "teleporter_charged": False, "teleporter_charge": 0.0, "boss_active": False}
        if t == "QUERY_INVENTORY":
            return {"type": "INVENTORY", "money": 0, "equipment": "None", "items": [],
                    "note": "Not in game"}
        if t == "COMMAND":
            state["cmds"].append(f"LOBBY!{msg.get('command')}:{msg.get('args')}")
            return {"type": "COMMAND_RESPONSE", "command": msg.get("command"), "status": "ok"}
    if state["tp_started_at"] and time.monotonic() - state["tp_started_at"] > 4:
        state["objective"] = "charging_teleporter"
    if t == "QUERY_OBJECTIVE":
        return {"type": "OBJECTIVE", "objective": state["objective"], "scene_name": "mocktown",
                "teleporter_charged": False, "teleporter_charge": 0.0, "boss_active": False}
    if t == "QUERY_INVENTORY":
        return {"type": "INVENTORY", "money": 10, "equipment": "None", "items": []}
    if t == "QUERY_COMBAT_STATUS":
        return {"type": "COMBAT_STATUS", "in_combat": False, "enemy_count": 0,
                "nearest_enemy_distance": 999.0}
    if t == "QUERY_INTERACTABLES":
        return {"type": "INTERACTABLES", "interactables": [
            {"type": "teleporter", "name": "Teleporter", "cost": 0, "distance": dist(64.0),
             "locked": False, "charged": False, "chargePercent": 0.0, "bossActive": False}]}
    if t == "QUERY_ALLIES":
        return {"type": "ALLIES", "allies": []}
    if t == "QUERY_PILLARS":
        return {"type": "PILLARS", "scene": "mocktown", "is_moon2": False, "total": 0,
                "charged": 0, "all_charged": False, "pillars": []}
    if t == "COMMAND":
        c, a = msg.get("command"), msg.get("args")
        state["cmds"].append(f"{c}:{a}")
        if c == "MODE" and a == "roam":
            state["moving"], state["t0"] = True, time.monotonic()
        return {"type": "COMMAND_RESPONSE", "command": c, "status": "ok"}
    return {"error": f"Unknown query type: {t}"}


def main():
    s = None
    for _ in range(120):
        try:
            s = socket.create_connection(("127.0.0.1", PORT), timeout=2)
            break
        except OSError:
            time.sleep(0.5)
    if s is None:
        print("mock: could not connect"); return
    s.settimeout(None)
    state["t_connect"] = time.monotonic()
    print(f"mock: connected on {PORT} mode={MODE}", flush=True)
    f = s.makefile("rb")

    def send(obj):
        try:
            s.sendall((json.dumps(obj) + "\n").encode())
        except OSError:
            pass

    while True:
        line = f.readline()
        if not line:
            print("mock: closed; commands received:", state["cmds"], flush=True); return
        try:
            msg = json.loads(line.decode().strip())
        except Exception:
            continue
        send(handle(msg))
        if msg.get("command") == "FIND_AND_INTERACT":
            target = msg.get("args")
            if MODE == "noterminal" and target == "teleporter":
                send({"type": "EVENT", "event_type": "action_started",
                      "command": "FIND_AND_INTERACT:teleporter", "target": "Teleporter",
                      "distance": "64", "cost": "0"})
                state["tp_started_at"] = time.monotonic()      # succeeds silently
            elif MODE == "ok":
                threading.Timer(1.0, send, args=({"type": "EVENT", "event_type": "action_complete",
                                                  "command": f"FIND_AND_INTERACT:{target}",
                                                  "status": "success"},)).start()
            else:
                send({"type": "EVENT", "event_type": "action_failed",
                      "command": f"FIND_AND_INTERACT:{target}", "reason": "not_found"})


main()
