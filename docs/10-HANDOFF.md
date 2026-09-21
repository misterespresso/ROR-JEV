# Handoff — 2026-09-21

Read this first. It is the state of the project at the end of the session that executed Phase 0
and started the fork, written so the next session does not re-derive any of it. Details live in
`08-PHASE0-REPORT.md` (findings F1–F30) and `09-FORK-BASE.md` (the fork, builds 0–2).

## Where things stand

| | State |
|---|---|
| Phase 0 go/no-go | **passed** — stock Rainflayer 1.1.1 runs on the pinned game build (Thunderstore package 1.1.1; its DLL declares plugin version 1.1.0, hence `mod/stock/Rainflayer-1.1.0.dll` — see `contract/pinned-build.yaml`) |
| Recordings | ~3.5 h across 5 survivors, all 3 objective phases, stage 4 reached (`recordings/`, gitignored) |
| Brain | contract implemented; **232 tests passing** (`python -m eval.run_suite`) |
| Brain on real telemetry | 10,578 replayed ticks, registry invariants held on every one — and **zero questions sendable** (F18, F30) |
| Fork | `mod/`, based on upstream `01137e5`, BepInEx 5, builds reproducibly from a clone |
| Deployed | **build 2** `95D1D36E…` (`python tools/deploy.py --status`); build 3 built, not deployed |
| Git | local only, no remote, clean history |

## Builds

| Build | What | Verified in game? |
|---|---|---|
| 0 | IL-identical to stock | yes — loads, 4/4 hooks bind, stock's query schema |
| 1 | roam no longer walks off edges (F28) | **no** |
| 2 | `QUERY_OBSERVATION`: time, difficulty, survivor, HP, 4 hazard facts | **no** |

| 3 | F29: repeat-damage blocking behind `[Gameplay] BlockRepeatDamage`, default **off** | **built, not deployed** |

Build 3 is `mod/` at the commit that adds it (Debug `AA63B6D3…` from this working copy; the
committed LF tree is what to deploy and register). `compare_dll` vs build 2: `LoadConfig`,
`HealthComponent_TakeDamage`, the new property's get/set, and a compiler renumbering of
`AutoChargePillarsCoroutine`'s state machine (`d__42` → `d__46`) caused by the new property —
nothing else. Build 2's environmental-damage note stays **above** the blocking check, so it counts
every hit whether or not blocking is on.

**Recordings before build 3 are on the easier game** (F29). From build 3, `fork_check` and
`record_run` write `block_repeat_damage` into `HARNESS_START`; `null` means the cfg key is absent,
i.e. a pre-build-3 mod that blocks unconditionally — not "off".

## Decisions (made 2026-09-21)

1. **F30 — narrow.** `next_action` declares `threat.enemies_near`, `threat.nearest_enemy_band`,
   `threat.boss_active` (all from stock). `build_request` gates on the full state, then sends
   `strip_unknown(state)`: no `unknown` reaches JEV, and whole-`threat` declarers (`strategy`,
   `situation_is_novel`) are still omitted when any part is unknown. `next_action` still needs
   `hazards` and `run.time_band` — only the adapter work (step 3) supplies them. Loose end: the
   `use_shrine_blood` worth-gate text names `threat` but the worth-gate template does not
   declare it; stripping makes that safe, not right.
2. **F29 — config, default off (vanilla).** Build 3 above.

## Next steps, in order

1. **Verify builds 1 and 2 in game.** From the main menu, AI control on:
   `python -m eval.fork_check --run-id fork-06 --survivor Commando --difficulty Drizzle`.
   Then confirm `OBSERVATION` frames appear and look sane:
   `python -m eval.inspect_recording --run-id fork-06`. Watch for the character stepping off edges
   *during combat* — combat movement has no edge check (build 1 fixed roam only).

   "Sane" means, for frames with `in_run: true`:
   - `run.elapsed_s` never decreases within a stage, and stops during pauses
   - `run.difficulty` is `Easy` (the enum name for Drizzle, not the display name) and
     `run.survivor` is `CommandoBody`
   - `self.hp ≤ self.hp_max`, and `self.alive` goes false exactly once, at death. `hp` goes
     **negative** on the killing blow (overkill; fork-06 recorded −31.2) — only with `alive: false`.
     The adapter must clamp before banding.
   - on a quiet stage 1, `hazards.taking_environmental_damage` is false, `active_dots` is empty,
     and `hostile_projectiles_nearby` is 0 when nothing is shooting
   - in the lobby: `in_run: false` with `note: "Not in game"`, not a stale snapshot

   **If a build misbehaves in game:** `deploy.py --restore` goes back to *stock*, not to the
   previous fork build. Build 1's source is commit `e802351` (`deploys.log` predates source
   logging, so it does not say). Rebuilding it from a clean LF checkout gives an IL-identical DLL
   with a **different hash** from `CE424B48…`, because build 1 was compiled from CRLF sources
   (`contract/pinned-build.yaml`, `builds:`). Confirm with `compare_dll` rather than the hash,
   register the new hash as "build 1, LF rebuild", deploy, then return `mod/` to `master`.
2. **Deploy build 3** with the game closed (`python tools/deploy.py`), register its hash in
   `KNOWN_BUILDS` and `contract/pinned-build.yaml`, and check the cfg gained
   `BlockRepeatDamage = false` after one launch.
3. **Adapter: consume `QUERY_OBSERVATION`** in `brain/stock_adapter.py` — band `elapsed_s` →
   `run.time_band`, `hp/hp_max` → `self.hp_band`, fill `run.survivor` and `hazards`. Not done yet;
   the recorder already polls and records the query. With it, `next_action` becomes
   sendable on real telemetry for the first time — replay a recording to prove it.
4. `build.identity`: nothing derives it; it blocks `strategy`, `engage_range` and every worth
   gate. `02-ARCHITECTURE.md` permits a deterministic derivation from items.
5. Fork work the runs already justify: exactly one terminal event per command (F25), `low_health`
   on the crossing only (F26), events emitted from game hooks so human runs have them (F20), one
   snapshot replacing the separate queries (F22), entity IDs and positions (F17, F12).

## How to work on the fork

- Build **Debug** (the stock DLL is Debug): `cd mod && dotnet build -c Debug`.
- After every change: `python -m eval.compare_dll --installed mod/bin/Debug/netstandard2.1/Rainflayer.dll`
  — the changed-method list must be exactly what you meant to touch.
- Deploy with the game **closed**: `python tools/deploy.py`. Then register the new hash in
  `KNOWN_BUILDS` (`tools/deploy.py`) and `contract/pinned-build.yaml`. `--restore` puts stock back.
- Commit before or right after deploying; `mod/deploys.log` records the source commit and marks
  `+dirty` builds.

## Traps that already cost time

- **`EnableAIControl`** in `%APPDATA%\r2modmanPlus-local\RiskOfRain2\profiles\Jev\BepInEx\config\justindwang.rainflayer.cfg`
  must be `true` for anything the AI drives and `false` for human play. Edit it with the game
  closed. Getting it wrong spoiled two runs in opposite directions; the tools now check it.
- **Never test on port 7777.** A self-test on 7777 was answered by the live game and drove the
  player's character. Use `ROR_JEV_PORT=17777` with `eval/mock_mod.py`, which refuses 7777.
- **Run the probe and the recorder one after the other, or use `fork_check`**, which does both on
  one connection. Two listeners on one port was silently allowed on Windows until fixed.
- **`OBJECTIVE` says `exploring` in the lobby.** `INVENTORY`'s "Not in game" marker is the truth (F27).
- **Terminal events are not guaranteed** (F25), and `low_health` fires ~60 times a second (F26).
- **Build inputs are LF.** CRLF sources compile to IL-identical DLLs with different hashes.
- **Stock sends no positions.** Explanations of movement or targeting from recordings alone have
  been wrong twice; the person watching the game has been right. Ask them.

## Working with this user

- The goal is JEV deciding. The scripted driver in `eval/record_run.py` exists only to keep runs
  alive — do not tune how it plays unless it blocks a run.
- Commands should run without alt-tabbing mid-run; the character dies while you switch windows.
