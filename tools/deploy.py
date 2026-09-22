"""Deploy the fork's DLL into the r2modman profile, or restore stock Rainflayer.

The fork keeps upstream's plugin GUID (`justindwang.rainflayer`) for now, so it REPLACES stock
rather than loading beside it. Two plugins with one GUID would make BepInEx pick one; two
plugins with different GUIDs would both hook player input and both dial port 7777. Neither is
acceptable, so deployment is a swap.

The stock DLL is backed up once, on first deploy, to `mod/stock/` (gitignored). Restoring is
always possible from there.

Every deploy prints the sha256 of what is now installed and appends it to `mod/deploys.log`,
so a recording can always be matched to the exact binary that produced it -- the gap that
made E1 in docs/09-FORK-BASE.md necessary in the first place.

Close the game first: a loaded plugin DLL is locked on Windows.

Run:
    python tools/deploy.py                 # deploy mod/bin/Debug build
    python tools/deploy.py --config Release
    python tools/deploy.py --restore       # put stock back
    python tools/deploy.py --status        # what is installed right now
"""
from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import shutil
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROFILE = pathlib.Path(os.environ.get("APPDATA", "")) / "r2modmanPlus-local" / "RiskOfRain2" \
    / "profiles" / "Jev"
TARGET = PROFILE / "BepInEx" / "plugins" / "Rainflayer-Rainflayer" / "Rainflayer.dll"
BACKUP = ROOT / "mod" / "stock" / "Rainflayer-1.1.0.dll"
LOG = ROOT / "mod" / "deploys.log"

#: sha256 of the stock DLL the Phase 0 recordings were made with.
STOCK_SHA = "84910CB00D557D50B9A9EDE050C54740D7538C6425BE9B80410D7A3BD88AFD11"


def sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest().upper()


#: Known fork builds, so the log and --status name them. Keep in step with
#: contract/pinned-build.yaml.
KNOWN_BUILDS = {
    "C9E558B8671FA35CC353E08F433CD8DA8ECB47ED0EFC2605E32DE9BE4ABA7FC4": "fork build 0 (IL-identical to stock)",
    "CE424B481B26F5BC745750BCDC19B1BC97DEACB653079DDBBFC7D93AA0176794": "fork build 1 (roam edge fix)",
    "1C9DD2808A6A50F59837B7836227510D0E16FF96FEC9A1686A3CAAA6612B96DA": "fork build 2, CRLF-sourced variant (superseded, never played)",
    "95D1D36EA02E2866804D2524BD99CF160C88EC98686466840E73A8F605E12D1B": "fork build 2 (QUERY_OBSERVATION)",
    "AA63B6D3FCCC334A8EA9046B914D1F590F8DD2EF55DD0B32B6AC71ECF65E9D28": "fork build 3 (BlockRepeatDamage, default off)",
    "48A5B4A4F5C8317F14338C8E4F43B380E9F297FCB5D003FE17C9123D29BED8B5": "fork build 4 (OBSERVATION threat block)",
    "FE06B337CF0DD09287C7C4588304AF557C8EDF55A351BE739AB15AAD8B47629E": "fork build 5 (combat edge safety; roam stops only at cliffs)",
    "F4406EBD4E23C2AE545A547553DC3583B0E23C47CD518CF5BC19178CDD2822BC": "fork build 6 (full-stage interactables)",
    "CF618C83E63BD9DC003466654C1679E2B3DC3D21C6E3F62E5D1AEF4B9E477438": "fork build 7 (8 m drops walked; cornered escape)",
    "3B629A1086C788880ADCE603FAD5E846D67267F33648D558338849F224BB9F26": "fork build 8 (INTERACT_ID)",
    "CCA56F5AAB316036EBCE7B552E6FB548AC9F70E8B01FD5C65E5953F0A1A89AE2": "fork build 9 (GOTO_ID: navigate only walks)",
    "D8E141EF626EEBD57604041D3EBD90750AA9AF0AF223226DF8310649DE513AB9": "fork build 10 (pickup reflex; equipment)",
    "280B09264B508477B46AD4961A12E3090BF86DFB35D40DEFB3FB231506BF85BD": "fork build 11 (hold at GOTO_ID destination)",
    "C36AC0A451995BD5A9F6E4A354B191E5D22EF82DFD7EC33C25658079E6BB803E": "fork build 12 (target lock; clear-shot LOS)",
    "667F6201A69E0795FFEF2BB84D7D7DAB9569EDCDF9EBF0F060251A6E167B8346": "fork build 13 (TARGET_PRIORITY)",
    "953D5162BD7BEF3C7CF5E0903878C717E47FD47C38152637FEDD392A755ACEB8": "fork build 14 (arrival, give-up report, barrel reflex)",
    "C207AC6E286CC90A7BE9A782307A3BB13355BC3D121AEDA5136B6A069D95FE71": "fork build 15 (barrel reflex 6 m)",
}


def describe(h: str) -> str:
    if h == STOCK_SHA:
        return "STOCK 1.1.0 (what the recordings validated)"
    return KNOWN_BUILDS.get(h, "fork build (unregistered -- add it to KNOWN_BUILDS)")


def source_revision() -> str:
    """The commit the build came from, plus '+dirty' for uncommitted changes under mod/.

    Recorded here rather than stamped into the DLL: the SDK's own stamping made the DLL hash
    change on every commit (see mod/Rainflayer.csproj), which broke 'same code, same hash'.
    """
    import subprocess
    try:
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "mod"], cwd=ROOT,
                               capture_output=True, text=True).stdout.strip()
        return head + ("+dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "no-git"


def log(action: str, h: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')}  {action:<8} {h}  "
                f"src={source_revision():<16} {describe(h)}\n")


def copy(src: pathlib.Path, dst: pathlib.Path) -> None:
    try:
        shutil.copyfile(src, dst)
    except PermissionError:
        raise SystemExit(f"cannot write {dst} -- is the game running? Close it and retry.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Deploy or restore the mod DLL")
    ap.add_argument("--config", default="Debug", choices=["Debug", "Release"],
                    help="Debug matches the stock build and is the default")
    ap.add_argument("--restore", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()

    if not TARGET.exists():
        raise SystemExit(f"no installed Rainflayer at {TARGET} -- is the Jev profile set up?")

    if a.status:
        h = sha(TARGET)
        print(f"installed : {TARGET}")
        print(f"sha256    : {h}")
        print(f"is        : {describe(h)}")
        print(f"backup    : {'present' if BACKUP.exists() else 'none yet'} ({BACKUP})")
        return 0

    if a.restore:
        if not BACKUP.exists():
            raise SystemExit("no backup to restore from -- stock was never replaced")
        copy(BACKUP, TARGET)
        h = sha(TARGET)
        log("restore", h)
        print(f"restored stock -> {TARGET}\nsha256 {h}  ({describe(h)})")
        return 0

    build = ROOT / "mod" / "bin" / a.config / "netstandard2.1" / "Rainflayer.dll"
    if not build.exists():
        raise SystemExit(f"no build at {build} -- run: cd mod && dotnet build -c {a.config}")

    current = sha(TARGET)
    if not BACKUP.exists():
        if current != STOCK_SHA:
            raise SystemExit(
                "refusing to take the first backup: the installed DLL is not the stock one the\n"
                f"recordings used (sha {current[:16]}...). Reinstall Rainflayer in r2modman first.")
        BACKUP.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(TARGET, BACKUP)
        print(f"backed up stock -> {BACKUP}")

    copy(build, TARGET)
    h = sha(TARGET)
    if h != sha(build):
        raise SystemExit("copy verification failed: installed hash does not match the build")
    log("deploy", h)
    print(f"deployed {a.config} build -> {TARGET}")
    print(f"sha256 {h}")
    print()
    print("Note: r2modman's 'update' on Rainflayer would overwrite this with stock.")
    print("Restore any time with: python tools/deploy.py --restore")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
