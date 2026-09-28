"""The one network call: a built request to a System One endpoint, answers back.

Two backends, same wire format:

  * **JEV** (TypeSafe, hosted) -- the default, needs a key.
  * **Laya** (convaiinnovations, open weights, Apache 2.0) -- a 421M decision model that
    `laya-serve` exposes on the SAME `POST /v1/systemone`, so only the URL changes. Local,
    free, no key. `pip install "laya[serve]"` then `LAYA_DEVICE=cuda laya-serve`.

Point the loop at Laya by setting the endpoint; nothing else in the brain changes:

    JEV_ENDPOINT=http://127.0.0.1:8000/v1/systemone python -m eval.jev_run --run-id laya-01 ...

The answers come from a different model, so treat a backend switch as a change of behaviour,
not a change of plumbing: the primer, option text and gate thresholds were all tuned against
JEV. `JevResult.model` records which model answered, and the recorder keeps it per tick.

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

#: TypeSafe's hosted endpoint, overridable for a local Laya server.
DEFAULT_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
ENDPOINT_ENV = "JEV_ENDPOINT"
KEY_ENVS = ("JEV_KEY", "TYPESAFE_API_KEY")


def endpoint() -> str:
    return os.environ.get(ENDPOINT_ENV, "").strip() or DEFAULT_ENDPOINT


def is_local(url: str) -> bool:
    """A server on this machine needs no API key (laya-serve takes none)."""
    return any(h in url for h in ("localhost", "127.0.0.1", "[::1]", "0.0.0.0"))


def backend(url: str | None = None) -> str:
    """What to print and record: which model is about to be asked."""
    url = url or endpoint()
    return "laya (local)" if is_local(url) else "jev (typesafe)"

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
    """A key is required for the hosted endpoint only."""
    return bool(_key()) or is_local(endpoint())


def ask(wire: dict, *, timeout_s: float = TIMEOUT_S, endpoint: str | None = None) -> JevResult:
    """POST one fan-out request (`BuiltRequest.wire()`) and return the parsed answers."""
    url = endpoint or globals()["endpoint"]()
    key = _key()
    if not key and not is_local(url):
        raise JevError(None, f"none of {', '.join(KEY_ENVS)} is set")
    body = json.dumps(wire, separators=(",", ":")).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
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
        raise JevError(e.code, detail.replace(key, "<redacted>") if key else detail) from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise JevError(None, type(e).__name__) from None
    return JevResult(answers=payload.get("answers") or {}, usage=payload.get("usage") or {},
                     latency_ms=(time.perf_counter() - t0) * 1000.0,
                     model=str(payload.get("model") or ""))
