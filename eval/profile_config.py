"""Read Rainflayer's BepInEx config from the r2modman profile.

Shared by the probe and the recorder, because both give misleading results when
`EnableAIControl` is wrong for what they are doing -- and this exact misconfiguration has now
silently spoiled two runs:

  * `human-01`, session 0: meant to be human play, AI control was ON, so the mod played.
  * `fork-gonogo-01` / `fork-01`: meant to exercise the fork under AI control, it was OFF, so every
    command was accepted and ignored, no events fired (F20), and the in-game half of
    docs/09-FORK-BASE.md section 6 did not actually run.

Neither failure looks like a configuration problem from the outside. One looks like the AI
fighting the player; the other looks like a mod that does not move.

The config file is named for the plugin GUID, `justindwang.rainflayer.cfg` -- not the
`com.rainflayer.cfg` upstream's SETUP.md names. Stock 1.1.1 has no QUERY_CONFIG (that is a 2.0.0
addition), so the file is the only way to know before connecting.
"""
from __future__ import annotations

import os
from pathlib import Path

CFG_GLOB = "r2modmanPlus-local/RiskOfRain2/profiles/*/BepInEx/config/justindwang.rainflayer.cfg"


def ai_control_enabled() -> tuple[bool | None, Path | None]:
    """Return (EnableAIControl, config path). None when it cannot be determined."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None, None
    for cfg in Path(appdata).glob(CFG_GLOB):
        if cfg.name.startswith("._"):      # macOS AppleDouble stub shipped in the package
            continue
        for line in cfg.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("EnableAIControl"):
                return line.split("=", 1)[1].strip().lower() == "true", cfg
    return None, None
