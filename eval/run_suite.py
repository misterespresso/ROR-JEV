"""Evidence runner: reports results per execution target.

A green Python fake-mod suite is evidence about brain logic and the protocol state
machine as this repo models it. It is NOT evidence that Unity interaction effects behave
correctly. Targets that do not exist yet report as NOT IMPLEMENTED, with a count of the
traces they owe -- never as passing.

Run: python -m eval.run_suite
"""
from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PYTHON_TRACES = ["eval/test_chest_trace.py", "eval/test_lifecycle_traces.py",
                 "eval/test_phase0.py", "eval/test_driver.py"]

#: Traces owed by targets that do not exist yet. Listed so the gap is explicit.
CSHARP_OWED = ["T1-T4", "T6-T14", "T23-T32", "L1-L4"]
INGAME_OWED = ["T2", "T4", "T5", "T6", "T15", "T18", "T22", "T24", "T25", "T27", "T30"]


def run(script: str) -> tuple[int, int, str]:
    r = subprocess.run([sys.executable, script], capture_output=True, text=True, cwd=ROOT)
    tail = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    passed = failed = 0
    if "passed" in tail:
        parts = tail.replace(",", "").split()
        passed, failed = int(parts[0]), int(parts[2])
    return passed, failed, r.stdout


def main() -> int:
    print("=" * 72)
    print("ROR-JEV contract evidence")
    print("=" * 72)
    print(f"python       : {platform.python_version()}")
    print(f"platform     : {platform.platform()}")
    print(f"root         : {ROOT}")
    print(f"command      : python -m eval.run_suite")
    print()

    total_p = total_f = 0
    print("TARGET 1: python fake mod  (brain logic, dispatch, protocol state machine)")
    for script in PYTHON_TRACES:
        p, f, out = run(script)
        total_p += p
        total_f += f
        status = "PASS" if f == 0 else "FAIL"
        print(f"  [{status}] {script:36s} {p:3d} passed, {f} failed")
        if f:
            print("\n".join("      " + l for l in out.splitlines() if "FAIL" in l))
    print(f"  subtotal: {total_p} passed, {total_f} failed, 0 skipped")
    print()

    print("TARGET 2: headless C# protocol state machine")
    print("  [NOT IMPLEMENTED]  0 passed, 0 failed, "
          f"{len(CSHARP_OWED)} trace groups owed")
    print(f"      owed: {', '.join(CSHARP_OWED)}")
    print("      blocker: no C# harness exists yet. The Python fake mod was written by the")
    print("      same author as the brain, so it can share the brain's wrong assumptions.")
    print()

    print("TARGET 3: in-game conformance (real RoR2 + forked Rainflayer)")
    print("  [NOT IMPLEMENTED]  0 passed, 0 failed, "
          f"{len(INGAME_OWED)} traces owed")
    print(f"      owed: {', '.join(INGAME_OWED)}")
    print("      blocker: no forked mod exists. Phase 0's go/no-go PASSED against STOCK")
    print("      1.1.1 (recordings/gonogo-03 opened a chest end to end), which proves the")
    print("      game and mod stack work -- not that protocol v2's typed targets,")
    print("      lifecycle and token rules behave correctly under Unity.")
    print()

    # Contract file freshness
    gen = subprocess.run([sys.executable, "-m", "eval.gen_actions", "--check"],
                         capture_output=True, text=True, cwd=ROOT)
    print("CONTRACT FILES")
    print(f"  [{'PASS' if gen.returncode == 0 else 'FAIL'}] {gen.stdout.strip()}")
    for f in ["contract/actions.yaml", "contract/questions.yaml", "contract/capabilities.yaml"]:
        print(f"        {f}  {'present' if (ROOT / f).exists() else 'MISSING'}")
    print()

    print("=" * 72)
    print(f"DISPOSITION: python target {total_p} passed / {total_f} failed.")
    print("Contract candidate: NOT MET -- C# conformance target does not exist.")
    print("Protocol freeze:    NOT MET -- the full stack IS pinned and the Phase 0 "
          "go/no-go PASSED")
    print("                    (contract/pinned-build.yaml, recordings/gonogo-03), but "
          "that is evidence")
    print("                    about STOCK Rainflayer 1.1.1. No protocol v2 conformance "
          "has run in")
    print("                    game, because no forked mod exists yet.")
    print("=" * 72)
    return 1 if total_f or gen.returncode else 0


if __name__ == "__main__":
    raise SystemExit(main())
