"""Compare two builds of the mod: which methods changed, and nothing else.

Two uses:

1. **Provenance.** Establish that a fork build is the artifact the recordings validated. Commit
   hashes could not do that -- the installed DLL declares a version no commit ever carried
   (docs/09-FORK-BASE.md, E1) -- so it is established on what the assemblies contain.
2. **Change control.** After editing the fork, confirm that exactly the intended methods changed.
   A one-line fix that silently alters a navigation routine shows up here before it shows up as a
   stalled character.

Comparison levels, via `tools/dllmeta.cs`:

    metadata   assembly references, types, method signatures, external member references,
               every string literal (for this mod, the entire wire protocol), plugin attribute
    IL         each method body, normalised so compiler encoding choices (short vs long
               branches, ldc.i4 forms, local-slot encodings) do not count, and compared as a
               MULTISET per method name so same-arity overloads cannot pair up wrongly

Normalisation cannot bridge an optimisation difference: Debug and Release bodies differ
everywhere. Compare builds made in the same configuration -- the stock DLL is a Debug build.

Not compared: exception-handling regions, local-variable signatures, static data blobs, type
layout. A difference confined to those would pass this check.

Run:
    python -m eval.compare_dll <before.dll> <after.dll>
    python -m eval.compare_dll --stock mod/bin/Debug/netstandard2.1/Rainflayer.dll
"""
from __future__ import annotations

import argparse
import collections
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "dllmeta.cs"
INSTALLED = (pathlib.Path(os.environ.get("APPDATA", "")) / "r2modmanPlus-local" / "RiskOfRain2"
             / "profiles" / "Jev" / "BepInEx" / "plugins" / "Rainflayer-Rainflayer"
             / "Rainflayer.dll")
#: The verified stock backup taken by tools/deploy.py. Once the fork is deployed, the installed
#: DLL is no longer stock, so `--stock` must not read the profile -- it did, briefly, and
#: reported a fork-vs-fork comparison under the name "stock".
STOCK_BACKUP = ROOT / "mod" / "stock" / "Rainflayer-1.1.0.dll"


def dump(dll: pathlib.Path, il: bool) -> list[str]:
    args = ["dotnet", "run", str(TOOL), "--"] + (["--il"] if il else []) + [str(dll)]
    r = subprocess.run(args, capture_output=True, text=True, cwd=ROOT)
    if r.returncode != 0:
        raise SystemExit(f"dllmeta failed on {dll}:\n{r.stderr or r.stdout}")
    return [l for l in r.stdout.splitlines() if l.strip()]


def sections(lines: list[str]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = collections.defaultdict(set)
    for l in lines:
        kind, _, rest = l.partition(" ")
        out[kind].add(rest)
    return out


def il_sets(lines: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = collections.defaultdict(list)
    for l in lines:
        _, key, h = l.split(" ", 2)
        out[key].append(h)
    return {k: sorted(v) for k, v in out.items()}


def main() -> int:
    ap = argparse.ArgumentParser(description="Compare two builds of the mod")
    ap.add_argument("dlls", nargs="*", type=pathlib.Path)
    ap.add_argument("--stock", type=pathlib.Path, metavar="DLL",
                    help="compare DLL against stock Rainflayer 1.1.0 (the verified backup)")
    ap.add_argument("--installed", type=pathlib.Path, metavar="DLL",
                    help="compare DLL against whatever is deployed in the profile right now")
    a = ap.parse_args()

    if a.stock:
        if not STOCK_BACKUP.exists():
            raise SystemExit(f"no stock backup at {STOCK_BACKUP} -- run tools/deploy.py once")
        before, after = STOCK_BACKUP, a.stock
    elif a.installed:
        before, after = INSTALLED, a.installed
    elif len(a.dlls) == 2:
        before, after = a.dlls
    else:
        ap.error("give two DLLs, or --stock DLL")
    for p in (before, after):
        if not p.exists():
            raise SystemExit(f"not found: {p}")

    print("=" * 78)
    print("BUILD COMPARISON")
    print("=" * 78)
    print(f"before : {before}")
    print(f"after  : {after}")
    print()

    ma, mb = sections(dump(before, False)), sections(dump(after, False))
    meta_diffs = 0
    print(f"{'section':<12} {'before':>7} {'after':>7}   differences")
    for kind in ("ASSEMBLYREF", "TYPE", "METHOD", "MEMBERREF", "USERSTRING", "ATTR"):
        a_, b_ = ma.get(kind, set()), mb.get(kind, set())
        gone, new = sorted(a_ - b_), sorted(b_ - a_)
        meta_diffs += len(gone) + len(new)
        print(f"{kind:<12} {len(a_):>7} {len(b_):>7}   "
              f"{'identical' if not (gone or new) else f'-{len(gone)} +{len(new)}'}")
        for g in gone[:8]:
            print(f"      - {g}")
        for n in new[:8]:
            print(f"      + {n}")
    print()

    ia, ib = il_sets(dump(before, True)), il_sets(dump(after, True))
    changed = sorted(k for k in set(ia) & set(ib) if ia[k] != ib[k])
    only_a = sorted(set(ia) - set(ib))
    only_b = sorted(set(ib) - set(ia))
    n_a = sum(len(v) for v in ia.values())
    print(f"method bodies: {n_a} before, {sum(len(v) for v in ib.values())} after")
    print(f"  changed : {len(changed)}")
    for k in changed:
        print(f"      ~ {k}")
    print(f"  removed : {len(only_a)}")
    for k in only_a:
        print(f"      - {k}")
    print(f"  added   : {len(only_b)}")
    for k in only_b:
        print(f"      + {k}")
    print()

    total = len(changed) + len(only_a) + len(only_b)
    print("=" * 78)
    if total == 0:
        print("IL-IDENTICAL: every method body matches after normalisation.")
        if meta_diffs:
            print(f"Metadata differs in {meta_diffs} entr{'y' if meta_diffs == 1 else 'ies'} "
                  "listed above -- check they are expected (e.g. the plugin version string).")
    else:
        print(f"{total} method(s) differ. For a deliberate change, that list should be exactly")
        print("the methods you meant to touch. Anything else is a change you did not intend.")
        if n_a and total > n_a * 0.5:
            print("More than half differ: most likely an optimisation mismatch (Debug vs")
            print("Release) rather than a code change. The stock DLL is a Debug build.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
