"""Raw recorder: every snapshot and event to JSONL, unbanded.

Phase 0 step 5. The reason this ships before any decision logic: **bands can be recomputed,
lost telemetry cannot.** A recording of banded state can only ever re-test the banding that
produced it, so what goes on disk is the mod's raw output, verbatim, with nothing dropped
and nothing normalised.

Four streams, append-only, one directory per run:

    session.jsonl        HELLO / HELLO_ACK and every reconnect. Carries the PINNED
                         VERSIONS (game, BepInEx, R2API, mod) the run was produced under,
                         so a recording is self-describing.
    observations.jsonl   raw OBSERVATION frames, exactly as received
    events.jsonl         raw EVENT / CANCEL_RESULT / RECONCILE_RESULT frames
    gaps.jsonl           detected sequence discontinuities

The gap stream exists because a silently incomplete recording is worse than a short one:
Phase 5 categorises failures from these files, and "no event here" must mean "nothing
happened", not "we lost it".

Durability: each line is flushed, and `fsync_every` controls how often the OS buffer is
forced to disk. Recording rides alongside a game process, so the failure to plan for is
the machine going down mid-run.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, TextIO

DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "recordings"

#: Frames routed to the event stream. Anything else with a `type` lands in `other.jsonl`
#: rather than being dropped -- an unrecognised frame is still evidence.
EVENT_TYPES = {"EVENT", "CANCEL_RESULT", "RECONCILE_RESULT", "COMMAND_RESPONSE"}
SESSION_TYPES = {"HELLO", "HELLO_ACK", "RECONCILE"}


@dataclass
class Gap:
    stream: str
    expected: int
    got: int
    t_wall_ns: int


@dataclass
class Recorder:
    """Append-only raw recorder for one run.

    Not a journal of decisions -- that is `02-ARCHITECTURE.md` section 9, keyed by
    `command_id`, and belongs to the brain. This records what the *mod* said.
    """

    run_id: str
    root: Path = DEFAULT_ROOT
    fsync_every: int = 25

    _files: dict[str, TextIO] = field(default_factory=dict, repr=False)
    _writes: int = 0
    _last_seq: dict[str, int] = field(default_factory=dict)
    gaps: list[Gap] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.dir = self.root / self.run_id
        self.dir.mkdir(parents=True, exist_ok=True)

    # -- plumbing ------------------------------------------------------------

    def _f(self, stream: str) -> TextIO:
        if stream not in self._files:
            # Line-buffered append. Never truncate: reconnecting to an existing run_id
            # must extend the recording, not replace it.
            self._files[stream] = open(
                self.dir / f"{stream}.jsonl", "a", encoding="utf-8", buffering=1
            )
        return self._files[stream]

    def _write(self, stream: str, obj: dict) -> None:
        f = self._f(stream)
        f.write(json.dumps(obj, separators=(",", ":"), ensure_ascii=False) + "\n")
        f.flush()
        self._writes += 1
        self.counts[stream] = self.counts.get(stream, 0) + 1
        if self.fsync_every and self._writes % self.fsync_every == 0:
            os.fsync(f.fileno())

    def _check_seq(self, stream: str, seq: int | None) -> None:
        if seq is None:
            return
        prev = self._last_seq.get(stream)
        if prev is not None and seq != prev + 1:
            g = Gap(stream, prev + 1, seq, time.time_ns())
            self.gaps.append(g)
            self._write("gaps", {
                "stream": g.stream, "expected": g.expected, "got": g.got,
                "t_wall_ns": g.t_wall_ns,
                # A gap in `event_seq` is the T19 trigger for RECONCILE. Recording it is
                # what lets a replay tell a dropped event from an event that never fired.
                "note": "event_seq gap triggers RECONCILE (T19)"
                        if stream == "events" else "observation seq gap",
            })
        # A regression (seq < prev) is recorded as a gap too, but must not rewind the
        # cursor -- otherwise one out-of-order frame manufactures a gap on every
        # subsequent frame.
        if prev is None or seq > prev:
            self._last_seq[stream] = seq

    # -- public --------------------------------------------------------------

    def record(self, frame: dict) -> str:
        """Record one raw frame verbatim. Returns the stream it landed in.

        The frame is written **unmodified** apart from a receipt timestamp under a
        reserved key, so a replay can reconstruct the exact bytes the brain saw.
        """
        typ = frame.get("type")
        if typ == "OBSERVATION":
            stream = "observations"
        elif typ in EVENT_TYPES:
            stream = "events"
        elif typ in SESSION_TYPES:
            stream = "session"
        else:
            stream = "other"

        if stream == "observations":
            self._check_seq("observations", frame.get("seq"))
        elif stream == "events":
            self._check_seq("events", frame.get("event_seq"))

        self._write(stream, {"_t_recv_wall_ns": time.time_ns(), "frame": frame})
        return stream

    def record_pinned_build(self, hello_ack: dict) -> None:
        """Record the build a run was produced under.

        Roadmap step 3 requires the pinned versions be reported in `HELLO_ACK`; this puts
        them in the recording itself, so a file found later can be trusted or discarded on
        its own evidence rather than on memory.
        """
        self._write("session", {
            "_t_recv_wall_ns": time.time_ns(),
            "pinned_build": {
                k: hello_ack.get(k) for k in
                ("game_version", "mod_version", "bepinex_version", "r2api_version",
                 "protocol_version", "registry_version", "capabilities")
            },
            "frame": hello_ack,
        })

    def close(self) -> None:
        for f in self._files.values():
            f.flush()
            os.fsync(f.fileno())
            f.close()
        self._files.clear()

    def __enter__(self) -> "Recorder":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def summary(self) -> dict:
        return {"run_id": self.run_id, "dir": str(self.dir),
                "counts": dict(self.counts), "gaps": len(self.gaps)}


# ---------------------------------------------------------------------------
# replay
# ---------------------------------------------------------------------------

def replay(run_dir: Path, stream: str = "observations") -> Iterator[dict]:
    """Yield raw frames back in recorded order.

    This is what makes Phase 5's "state-shape changes test offline" claim true: the frames
    are raw, so a new banding or a reworded catalog can be run over them at zero inference
    cost. It cannot establish that a *different action* would have produced a better run
    -- once the action diverges every later frame is counterfactual.
    """
    path = Path(run_dir) / f"{stream}.jsonl"
    if not path.exists():
        return
    with open(path, encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)["frame"]
            except (json.JSONDecodeError, KeyError) as e:
                # A truncated final line is the expected shape of a crash. Surface it;
                # do not silently swallow it, and do not abort the whole replay.
                raise ValueError(f"{path}:{line_no} unreadable: {e}") from e
