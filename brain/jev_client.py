"""The one network call: a built request to TypeSafe's System One endpoint, answers back.

Standard library only (`urllib`), so the live loop needs nothing installed. The key comes
from `JEV_KEY` (this project's name for it) or else `TYPESAFE_API_KEY` (the name TypeSafe's
own SDK reads), and is never logged, recorded, or included in an error message.

API facts are from `docs/01-RESEARCH.md` section 1: `POST /v1/systemone`, bearer auth,
70-500 ms, errors 401 / 422 / 429 / 529.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
KEY_ENVS = ("JEV_KEY", "TYPESAFE_API_KEY")

#: A tick's answer is worthless once the world has moved on; the loop skips the tick instead.
TIMEOUT_S = 3.0


class JevError(RuntimeError):
    """A failed call. `status` is the HTTP status, or None for a network/timeout failure."""

    def __init__(self, status: int | None, message: str):
        super().__init__(f"{status or 'network'}: {message}")
        self.status = status


@dataclass
class JevResult:
    answers: dict = field(default_factory=dict)
    usage: dict = field(default_factory=dict)
    latency_ms: float = 0.0
    model: str = ""


def _key() -> str:
    for name in KEY_ENVS:
        v = os.environ.get(name, "").strip()
        if v:
            return v
    # Windows: a user variable set after this process's parent started is in the registry
    # but not yet in the inherited environment. Read it there rather than demand a restart.
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                for name in KEY_ENVS:
                    try:
                        v = str(winreg.QueryValueEx(k, name)[0]).strip()
                    except OSError:
                        continue
                    if v:
                        return v
        except OSError:
            pass
    return ""


def have_key() -> bool:
    return bool(_key())


def ask(wire: dict, *, timeout_s: float = TIMEOUT_S, endpoint: str = ENDPOINT) -> JevResult:
    """POST one fan-out request (`BuiltRequest.wire()`) and return the parsed answers."""
    key = _key()
    if not key:
        raise JevError(None, f"none of {', '.join(KEY_ENVS)} is set")
    body = json.dumps(wire, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(endpoint, data=body, method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            payload = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # The response body can echo the request; keep only a short, key-free detail.
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:                                  # noqa: BLE001
            pass
        raise JevError(e.code, detail.replace(key, "<redacted>")) from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise JevError(None, type(e).__name__) from None
    return JevResult(answers=payload.get("answers") or {}, usage=payload.get("usage") or {},
                     latency_ms=(time.perf_counter() - t0) * 1000.0,
                     model=str(payload.get("model") or ""))
