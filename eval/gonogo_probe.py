"""Phase 0 step 2: the go/no-go probe.

The gate is "run stock, confirm the socket connects and the character moves." Doing that
through the stock Python brain means getting an LLM backend, a key and a 4-second prompt
loop working first, none of which the gate is about. This speaks stock Rainflayer's
protocol directly instead, so the gate needs no API key and no model.

**Python is the server.** The mod is a `TcpClient` that dials `127.0.0.1:7777` and retries
every 2 s (`upstream/rainflayer/mod/SocketBridge.cs`), so this listens and waits.

What it checks, in the order a failure would matter:

    1. the mod connects at all                  -- is the plugin loaded
    2. queries answer, and not "Not in game"    -- is the AI controller live
    3. all six state queries return             -- is the protocol intact
    4. the character MOVES under MODE:roam      -- is navigation alive
    5. FIND_AND_INTERACT completes with an EVENT -- is the full loop alive

Step 4 has no direct telemetry to read: stock exposes no player position. It is measured
instead from interactable and enemy distances changing over time, which is a proxy, and one
that reads "did not move" if the character is boxed in with nothing nearby. The probe says
so rather than reporting a clean failure.

Everything received is written through `brain.recorder`, so the gate run also produces the
first real recording and the actual field shapes Phase 0 step 4 has to build against.

Usage, with the game already running and a run started:

    python -m eval.gonogo_probe --run-id gonogo-01

Exit code 0 is GO, 1 is NO-GO, 2 means it could not tell.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, Queue

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from brain.recorder import Recorder
from eval.profile_config import ai_control_enabled

HOST = "127.0.0.1"
#: The mod dials 7777 and nothing else. Overriding this is how self-tests stay unreachable by
#: a running game: on 2026-09-21 a self-test on 7777 was answered by the live game instead of
#: the mock it was meant for, and drove the player's character for ~40 s. Tests now run on a
#: different port via ROR_JEV_PORT, where the real mod can never connect.
GAME_PORT = 7777
PORT = int(os.environ.get("ROR_JEV_PORT", GAME_PORT))

#: Every state query stock Rainflayer answers. `docs/01-RESEARCH.md` says "five state
#: endpoints"; there are six. PILLARS is moon2-only and expected to be empty on Stage 1.
QUERIES = [
    "QUERY_OBJECTIVE", "QUERY_INVENTORY", "QUERY_COMBAT_STATUS",
    "QUERY_INTERACTABLES", "QUERY_ALLIES", "QUERY_PILLARS",
]

def listen_socket() -> socket.socket:
    """A server socket that REFUSES to share its port.

    `SO_REUSEADDR` means different things on different platforms. On Linux it only permits
    rebinding a port in TIME_WAIT. On Windows it lets a second process bind a port that is
    already being listened on, silently -- so running the probe and the recorder at the same
    time produced two servers on 7777, and which one the mod reached was undefined. It worked
    once by luck (the recorder picked up the moment the probe closed).

    `SO_EXCLUSIVEADDRUSE` makes the second bind fail, which turns that into a clear error.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    excl = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
    if excl is not None:                       # Windows
        s.setsockopt(socket.SOL_SOCKET, excl, 1)
    else:                                      # POSIX: REUSEADDR is safe here
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    return s


#: Metres of change in any tracked distance that counts as the character having moved.
#: Comfortably above jitter, well below a real traversal.
MOVE_THRESHOLD_M = 3.0


@dataclass
class Check:
    name: str
    ok: bool | None          # None == could not determine, which is not a pass
    detail: str = ""
    #: An `ok is None` that the gate anticipates -- PILLARS off-moon, for instance. Noted
    #: but not held against the verdict, so a clean Stage 1 run can read GO.
    expected: bool = False

    @property
    def mark(self) -> str:
        if self.ok is None:
            return "n/a " if self.expected else "??  "
        return {True: "PASS", False: "FAIL"}[self.ok]


@dataclass
class Probe:
    recorder: Recorder
    timeout: float = 10.0
    conn: socket.socket | None = None
    events: list[dict] = field(default_factory=list)
    _responses: "Queue[dict]" = field(default_factory=Queue)
    _buf: bytes = b""
    _stop: threading.Event = field(default_factory=threading.Event)
    checks: list[Check] = field(default_factory=list)
    #: Set to a reason string once the mod goes away. Every later step short-circuits.
    lost: str | None = None

    def add(self, name: str, ok: bool | None, detail: str = "",
            expected: bool = False) -> Check:
        c = Check(name, ok, detail, expected)
        self.checks.append(c)
        print(f"  {c.mark}  {name}" + (f"  <- {detail}" if detail else ""))
        return c

    # -- transport -----------------------------------------------------------

    def accept(self, wait_s: float) -> bool:
        """Listen and wait for the mod to dial in."""
        if PORT != GAME_PORT:
            print(f"  ...  TEST PORT {PORT}: the game cannot connect here (it only dials "
                  f"{GAME_PORT})")
        srv = listen_socket()
        try:
            srv.bind((HOST, PORT))
        except OSError as e:
            srv.close()
            self.add(f"port {PORT} is free", False,
                     f"{e}. Another listener already holds it -- is a probe or recorder "
                     f"still running in another window? Run them one after the other.")
            return False
        srv.listen(1)
        srv.settimeout(wait_s)
        print(f"  ...  listening on {HOST}:{PORT}, waiting up to {wait_s:.0f}s "
              f"for the mod to connect")
        try:
            self.conn, addr = srv.accept()
        except socket.timeout:
            self.add("mod connects to the brain socket", False,
                     "no connection. Plugin not loaded, or EnableAIControl is false. "
                     "Check BepInEx/LogOutput.log for [Rainflayer].")
            return False
        finally:
            srv.close()
        self.conn.settimeout(0.5)
        self.add("mod connects to the brain socket", True, f"from {addr[0]}:{addr[1]}")
        threading.Thread(target=self._read_loop, daemon=True,
                         name="probe-reader").start()
        return True

    def _read_loop(self) -> None:
        """One reader. EVENTs arrive unsolicited on the same connection as responses, so
        they must be separated here rather than by whoever happens to read next."""
        while not self._stop.is_set() and self.conn is not None:
            try:
                chunk = self.conn.recv(65536)
            except socket.timeout:
                continue
            except OSError as e:
                # A shutdown we initiated is not a lost connection. Without this guard the
                # reader races `close()` and reports a false NO-GO after a clean run.
                if not self._stop.is_set():
                    self._mark_lost(f"connection error while reading: {e}")
                return
            if not chunk:
                if not self._stop.is_set():
                    self._mark_lost("mod closed the connection")
                return
            self._buf += chunk
            while b"\n" in self._buf:
                line, self._buf = self._buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    frame = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    # Never desync on a bad line (protocol v2 T20 behaviour, applied here).
                    self.recorder.record({"type": "PROBE_MALFORMED",
                                          "raw": line.decode("utf-8", "replace")})
                    continue
                self.recorder.record(frame)
                if frame.get("type") == "EVENT":
                    self.events.append(frame)
                    # Only `action_*` events carry a command and an outcome. Combat events
                    # carry neither, and printing "None -> None" for them read as a bug.
                    bits = [str(frame.get("event_type"))]
                    if frame.get("command"):
                        bits.append(str(frame["command"]))
                    outcome = frame.get("result") or frame.get("reason")
                    if outcome:
                        bits.append(f"-> {outcome}")
                    extra = {k: v for k, v in frame.items()
                             if k not in {"type", "event_type", "command", "result", "reason"}}
                    if extra:
                        bits.append(json.dumps(extra))
                    print("       EVENT " + " ".join(bits))
                else:
                    self._responses.put(frame)

    def send(self, obj: dict) -> bool:
        if self.conn is None or self.lost:
            return False
        self.recorder.record({"type": "PROBE_SENT", "sent": obj})
        try:
            self.conn.sendall((json.dumps(obj) + "\n").encode("utf-8"))
            return True
        except OSError as e:
            # The game crashing mid-probe is a likely outcome, not an exceptional one, and
            # it must read as a NO-GO with a reason rather than a Python traceback.
            self._mark_lost(f"connection lost while sending: {e}")
            return False

    def _mark_lost(self, why: str) -> None:
        if not self.lost:
            self.lost = why
            self.add("connection stays up for the whole probe", False, why)

    def request(self, obj: dict) -> dict | None:
        """Send and wait for the next non-EVENT frame."""
        while True:                       # drain anything stale first
            try:
                self._responses.get_nowait()
            except Empty:
                break
        if not self.send(obj):
            return None
        try:
            return self._responses.get(timeout=self.timeout)
        except Empty:
            if self._stop.is_set() or self.lost:
                return None
            return None

    def query(self, kind: str) -> dict | None:
        return self.request({"type": kind})

    def command(self, command: str, args: str = "") -> dict | None:
        return self.request({"type": "COMMAND", "command": command, "args": args})

    def close(self) -> None:
        self._stop.set()
        if self.conn:
            try:
                self.conn.close()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------

#: Scenes that are never a run, whatever OBJECTIVE claims.
NON_RUN_SCENES = {"lobby", "title", "splash", "intro", "loadingbasic", "logbook", "crystalworld",
                  "eclipseworld", "infinitetowerworld"}

#: Seconds to wait after the run is first seen live before issuing anything. The character
#: spawns inside a drop pod, and a command sent before it is out lands on a body that cannot
#: act on it yet.
SPAWN_SETTLE_S = 6.0


def run_is_live(p: Probe) -> tuple[bool, str]:
    """True only when the player is actually in a run.

    **OBJECTIVE cannot be trusted for this.** In the character-select lobby it still answers
    `objective: "exploring"` with no "Not in game" marker, so a probe that trusted it declared a
    run live from the lobby and sent `FIND_AND_INTERACT` before the character existed (`fork-02`:
    commands at 0.1 s, run not started until later; the stale command then executed into a
    back-and-forth that never reported back). INVENTORY does carry the marker, so it decides.
    """
    inv = p.query("QUERY_INVENTORY")
    obj = p.query("QUERY_OBJECTIVE")
    if inv is None or obj is None:
        return False, "no response"
    if "error" in inv:
        return False, str(inv["error"])
    if inv.get("note"):
        return False, f"INVENTORY: {inv['note']}"
    scene = str(obj.get("scene_name") or "")
    if scene in NON_RUN_SCENES:
        return False, f"scene is {scene!r}"
    return True, f"scene={scene} objective={obj.get('objective')}"


def wait_for_run(p: Probe, wait_s: float) -> bool:
    """Wait until a run is live on two consecutive polls, then let the drop pod open."""
    deadline = time.monotonic() + wait_s
    last, streak = None, 0
    while time.monotonic() < deadline:
        live, why = run_is_live(p)
        last = why
        streak = streak + 1 if live else 0
        if streak >= 2:
            print(f"  ...  run is live ({why}); letting the drop pod open "
                  f"for {SPAWN_SETTLE_S:g}s")
            time.sleep(SPAWN_SETTLE_S)
            p.add("queries answer once a run is live", True, why)
            return True
        if not live:
            print(f"  ...  waiting for a run to start ({why})")
        time.sleep(1.5 if live else 3.0)
    p.add("queries answer once a run is live", False,
          f"last: {last}. Start a run (Commando, Drizzle, solo) and rerun.")
    return False


def check_state_queries(p: Probe) -> dict[str, dict]:
    """All six queries must answer. Shapes are recorded for Phase 0 step 4."""
    got: dict[str, dict] = {}
    for q in QUERIES:
        r = p.query(q)
        if r is None:
            p.add(f"{q} answers", False, "timed out")
        elif "error" in r:
            # PILLARS legitimately errors off-moon; that is not a protocol failure.
            moon_only = q == "QUERY_PILLARS"
            p.add(f"{q} answers", None if moon_only else False,
                  f"{r['error']}" + (" (moon2-only, expected off-moon)" if moon_only else ""),
                  expected=moon_only)
        else:
            got[q] = r
            keys = ",".join(sorted(k for k in r if k != "type"))
            p.add(f"{q} answers", True, keys)
    return got


#: `COMBAT_STATUS.nearest_enemy_distance` reports this when there is no enemy. It is a
#: sentinel, not a measurement, and differencing it against a real distance manufactured a
#: 968 m "movement" in `gonogo-03`. Enemy distance is excluded from the proxy entirely --
#: enemies move and spawn independently of the character, so it was never a positional
#: reference in the first place.
NO_ENEMY_SENTINEL = 999.0


def _distances(p: Probe) -> dict[str, list[float]]:
    """Positional fingerprint: sorted interactable distances, grouped by type.

    Stock interactables have **no stable identity** -- three entries all named
    "Shop Terminal" are indistinguishable, so keying on `type:name` silently collapsed them
    to whichever came last and produced meaningless deltas. Grouping and sorting per type
    is the most that can be done without the entity IDs protocol v2 adds.
    """
    out: dict[str, list[float]] = {}
    r = p.query("QUERY_INTERACTABLES")
    if r and "interactables" in r:
        for it in r["interactables"]:
            if it.get("distance") is not None:
                out.setdefault(str(it.get("type")), []).append(float(it["distance"]))
    return {k: sorted(v) for k, v in out.items()}


def _movement_delta(before: dict[str, list[float]],
                    after: dict[str, list[float]]) -> tuple[float, int]:
    """Largest per-target distance change, comparing only comparable groups.

    A type whose count changed between samples is skipped: an interactable appearing or
    disappearing (a chest entering the detection cone, F12) is not the character moving.
    """
    biggest, compared = 0.0, 0
    for typ, b in before.items():
        a = after.get(typ)
        if a is None or len(a) != len(b):
            continue
        for x, y in zip(b, a):
            biggest = max(biggest, abs(y - x))
            compared += 1
    return biggest, compared


def check_movement(p: Probe, seconds: float) -> None:
    """The gate's real question: does the character move when told to?"""
    before = _distances(p)
    if not before:
        p.add("character moves under MODE:roam", None,
              "nothing measurable nearby -- no interactables and no enemies, so movement "
              "cannot be inferred. Watch the screen and judge by eye.")
        p.command("MODE", "roam")
        return

    r = p.command("MODE", "roam")
    if r is None:
        p.add("MODE:roam accepted", False, "no COMMAND_RESPONSE")
        return
    p.add("MODE:roam accepted", "error" not in r, json.dumps(r))

    n_refs = sum(len(v) for v in before.values())
    print(f"  ...  sampling movement for {seconds:.0f}s ({n_refs} reference distances "
          f"across {len(before)} interactable types)")
    time.sleep(seconds)
    after = _distances(p)

    biggest, compared = _movement_delta(before, after)
    if not compared:
        p.add("character moves under MODE:roam", None,
              "no comparable reference points between samples -- every interactable type "
              "changed count, so movement cannot be separated from the detection cone "
              "shifting. Watch the screen and judge by eye.")
        return
    p.add("character moves under MODE:roam", biggest >= MOVE_THRESHOLD_M,
          f"largest distance change {biggest:.1f} m over {seconds:.0f}s "
          f"across {compared} comparable targets (threshold {MOVE_THRESHOLD_M} m)"
          + ("" if biggest >= MOVE_THRESHOLD_M
             else ". Stuck, boxed in, or navigation is broken -- check LogOutput.log"))


#: Event types that terminate an action. Rainflayer 1.1.1 emits `action_failed` as its own
#: event_type carrying `reason`, which neither `docs/01-RESEARCH.md` nor upstream's own
#: PROTOCOL.md documents -- the latter describes only `action_complete` with `result`.
#: Waiting for `action_complete` alone therefore hangs on every failure path.
TERMINAL_EVENTS = {"action_complete", "action_failed"}

#: Outcomes that mean the command path worked and the world simply did not cooperate. These
#: are not gate failures: `not_found` is the mod correctly reporting that no chest was
#: within its detection cone.
BENIGN_REASONS = {"not_found", "no_active_zone", "no gold", "no_gold"}


def _outcome(ev: dict) -> str:
    """One concept, three field names across one mod version.

    `result` is what 2.0.0's PROTOCOL.md documents; the installed 1.1.1 uses `reason` on
    `action_failed` and `status` on `action_complete`. Reading only the first two scored a
    successful chest opening as a failure.
    """
    return str(ev.get("result") or ev.get("reason") or ev.get("status") or "")


#: Cheapest small chest in RoR2 is 25 gold at difficulty coefficient 1.0 and scales up from
#: there; the Basic Chest observed in `gonogo-02` cost 30. Below this there is no point
#: spending 90 s waiting for a `no_gold` we can predict.
MIN_GOLD_FOR_CHEST = 35


def check_interaction(p: Probe, wait_s: float) -> None:
    """FIND_AND_INTERACT exercises navigation, interaction and the event path at once."""
    inv = p.query("QUERY_INVENTORY")
    gold = inv.get("money") if inv and "error" not in inv else None
    if gold is not None:
        print(f"  ...  gold: {gold}")
        if int(gold) < MIN_GOLD_FOR_CHEST:
            # Say this BEFORE issuing the command, so the outcome is not a surprise.
            print(f"  ...  below {MIN_GOLD_FOR_CHEST}, so a chest will likely report "
                  f"no_gold. Kill a few enemies and rerun to prove the interaction.")

    r = p.command("FIND_AND_INTERACT", "chest")
    if r is None:
        p.add("FIND_AND_INTERACT accepted", False, "no COMMAND_RESPONSE")
        return
    p.add("FIND_AND_INTERACT accepted", "error" not in r, json.dumps(r))

    print(f"  ...  waiting up to {wait_s:.0f}s for a terminal action event")
    deadline = time.monotonic() + wait_s
    seen = 0
    while time.monotonic() < deadline:
        for ev in p.events[seen:]:
            if ev.get("event_type") not in TERMINAL_EVENTS:
                continue
            kind, why = ev["event_type"], _outcome(ev)
            p.add("a terminal action event arrives", True, f"{kind} reason={why or '-'}")

            # `no_gold` carries `target`, `cost` and `current_gold`, which proves the mod
            # resolved a SPECIFIC interactable and priced it against live gold. That is the
            # whole detection-and-costing path, and it is worth crediting separately from
            # the interaction effect -- which a refusal by definition did not perform.
            if {"target", "cost"} <= set(ev):
                p.add("mod resolves a specific target and prices it", True,
                      f"target={ev['target']!r} cost={ev.get('cost')} "
                      f"gold={ev.get('current_gold')}")

            if kind == "action_complete" and why.startswith(
                    ("success", "interrupted", "pending")):
                p.add("interaction loop completes an interaction", True, f"result={why}")
            elif why in BENIGN_REASONS:
                # The loop reported a correct outcome for the world it was in. That proves
                # the command and event path; it does not prove navigate-and-open works.
                fix = {
                    "no_gold": "earn gold until you can afford a chest "
                               f"(>= {MIN_GOLD_FOR_CHEST}), then rerun",
                    "no gold": "earn gold until you can afford a chest, then rerun",
                    "not_found": "no chest was in the mod's detection cone -- rerun with a "
                                 "chest in front of the character (see F12)",
                    "no_active_zone": "rerun during teleporter charging",
                }.get(why, "rerun under conditions that permit the interaction")
                p.add("interaction loop completes an interaction", None,
                      f"{kind}({why}) -- the command path worked and reported honestly, but "
                      f"no interaction happened. To prove it: {fix}.")
            else:
                p.add("interaction loop completes an interaction", False,
                      f"{kind}({why}) -- check LogOutput.log")
            return
        seen = len(p.events)
        time.sleep(1.0)
    p.add("a terminal action event arrives", False,
          "no terminal event within the window. Navigation may be stalled; check "
          "LogOutput.log.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase 0 go/no-go probe for stock Rainflayer")
    ap.add_argument("--run-id", default=time.strftime("gonogo-%Y%m%d-%H%M%S"))
    ap.add_argument("--connect-wait", type=float, default=120.0,
                    help="seconds to wait for the mod to connect")
    ap.add_argument("--run-wait", type=float, default=180.0,
                    help="seconds to wait for a run to start")
    ap.add_argument("--move-seconds", type=float, default=15.0)
    ap.add_argument("--interact-wait", type=float, default=90.0)
    ap.add_argument("--skip-interact", action="store_true",
                    help="stop after the movement check")
    a = ap.parse_args()

    print("=" * 78)
    print("PHASE 0 STEP 2 - GO/NO-GO PROBE (stock Rainflayer, no API key required)")
    print("=" * 78)
    print("Before running: game launched with BepInEx + Rainflayer, EnableAIControl true,")
    print("and ideally a run already started (Commando, Drizzle, solo).")
    print()

    ai_on, cfg = ai_control_enabled()
    if ai_on is False:
        print("  !!  EnableAIControl = false in")
        print(f"       {cfg}")
        print("      The mod will ACCEPT every command and IGNORE it. The movement and")
        print("      interaction checks below will fail, and that failure will be about")
        print("      this setting, not about the mod. Close the game, set it to true,")
        print("      restart, and rerun. (Continuing so the query checks still run.)")
        print()
    elif ai_on is None:
        print("  !   could not read EnableAIControl; if nothing moves, check it first.")
        print()

    with Recorder(a.run_id) as rec:
        p = Probe(rec)
        rec.record({"type": "HARNESS_START", "harness": "gonogo_probe",
                    "ai_control": ai_on, "config": str(cfg) if cfg else None})
        try:
            if not p.accept(a.connect_wait):
                return verdict(p, rec)
            if not wait_for_run(p, a.run_wait):
                return verdict(p, rec)
            check_state_queries(p)
            if not p.lost:
                check_movement(p, a.move_seconds)
            if not p.lost and not a.skip_interact:
                check_interaction(p, a.interact_wait)
            # Leave the character not wandering off while the human reads the output.
            p.command("MODE", "wait")
        except KeyboardInterrupt:
            print("\n  interrupted by user")
        finally:
            p.close()
        return verdict(p, rec)


def verdict(p: Probe, rec: Recorder) -> int:
    failed = [c for c in p.checks if c.ok is False]
    unknown = [c for c in p.checks if c.ok is None and not c.expected]
    noted = [c for c in p.checks if c.ok is None and c.expected]
    print()
    print("-" * 78)
    s = rec.summary()
    print(f"recording : {s['dir']}")
    print(f"frames    : {s['counts']}  gaps: {s['gaps']}")
    print(f"checks    : {len(p.checks) - len(failed) - len(unknown) - len(noted)} pass, "
          f"{len(failed)} fail, {len(unknown)} inconclusive, {len(noted)} n/a")
    print("-" * 78)
    ai_on, _ = ai_control_enabled()
    behaviour = {"character moves under MODE:roam", "FIND_AND_INTERACT accepted",
                 "a terminal action event arrives", "interaction loop completes an interaction"}
    if failed and ai_on is False and all(c.name in behaviour for c in failed):
        print("VERDICT: BLOCKED BY CONFIGURATION, not NO-GO")
        print("  Every failure is a behaviour check, and EnableAIControl is false, so the")
        print("  mod was never allowed to act. Nothing here says the mod is broken.")
        print("  Set EnableAIControl = true (game closed), restart, and rerun.")
        return 2
    if failed:
        print("VERDICT: NO-GO")
        for c in failed:
            print(f"  - {c.name}: {c.detail}")
        print()
        print("Phase 0's premise is 'assume repair, not setup'. A NO-GO here is the")
        print("expected outcome, not a surprise -- the next work is making the mod build")
        print("and run against the pinned game, before any of protocol v2 matters.")
        return 1
    if unknown:
        print("VERDICT: INCONCLUSIVE -- some checks could not be determined")
        for c in unknown:
            print(f"  - {c.name}: {c.detail}")
        return 2
    print("VERDICT: GO")
    print("Stock Rainflayer runs against the pinned build. Record the versions into")
    print("contract/pinned-build.yaml, then Phase 0 step 7: play 3-5 runs recording")
    print("throughout, which gives the catalog real telemetry to be tested against.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
