# Handoff — 2026-09-21

Read this first. It is the state of the project at the end of the session that executed Phase 0
and started the fork, written so the next session does not re-derive any of it. Details live in
`08-PHASE0-REPORT.md` (findings F1–F30) and `09-FORK-BASE.md` (the fork, builds 0–2).

## Where things stand

| | State |
|---|---|
| Phase 0 go/no-go | **passed** — stock Rainflayer 1.1.1 runs on the pinned game build (Thunderstore package 1.1.1; its DLL declares plugin version 1.1.0, hence `mod/stock/Rainflayer-1.1.0.dll` — see `contract/pinned-build.yaml`) |
| Recordings | ~3.5 h across 5 survivors, all 3 objective phases, stage 4 reached (`recordings/`, gitignored) |
| Brain | contract implemented; **277 tests passing** (`python -m eval.run_suite`) |
| Brain on real telemetry | stock recordings: **zero questions sendable** (F18). fork-06 (build 2): **`next_action` sendable on 444/448 ticks** (the other 4: 3 dead, 1 no observation) and `worth_start_teleporter` on 6; the rest blocked on `threat` sub-fields |
| Fork | `mod/`, based on upstream `01137e5`, BepInEx 5, builds reproducibly from a clone |
| Deployed | **build 4** `48A5B4A4…` (`python tools/deploy.py --status`) |
| Git | local only, no remote, clean history |

## Builds

| Build | What | Verified in game? |
|---|---|---|
| 0 | IL-identical to stock | yes — loads, 4/4 hooks bind, stock's query schema |
| 1 | roam no longer walks off edges (F28) | **yes** — fork-06, stayed on the ground in combat too (user observed) |
| 2 | `QUERY_OBSERVATION`: time, difficulty, survivor, HP, 4 hazard facts | **yes** — fork-06, 516 frames, every check below passes |

| 3 | F29: repeat-damage blocking behind `[Gameplay] BlockRepeatDamage`, default **off** | loads (cfg key written); superseded by build 4 before a run was recorded |
| 4 | `OBSERVATION` gains a `threat` block: enemy/elite/boss/targeting counts within 50 m, 16 nearest with body, team, elite, boss, flying, distance, targeting | **deployed** `48A5B4A4…` from `b0e03a2`; **not yet run** |

Build 3 is `36faf23` (Debug `AA63B6D3…`, reproduced from the committed tree). `compare_dll` vs build 2: `LoadConfig`,
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

1. **Build 3 launched: done.** The cfg gained `BlockRepeatDamage = false` under `[Gameplay]`.
   The first build-4 run (step 5) also confirms F29 on the wire.
   The OBSERVATION checks below apply to every later run.

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

   fork-06 (build 2, blackbeach2, died at 525 s) passed all of these. It also saw environmental
   damage once, at 414 s, with no projectile nearby — source unknown (a fall is the likely
   candidate; ask the person who watched).

   **If a build misbehaves in game:** `deploy.py --restore` goes back to *stock*, not to the
   previous fork build. Build 1's source is commit `e802351` (`deploys.log` predates source
   logging, so it does not say). Rebuilding it from a clean LF checkout gives an IL-identical DLL
   with a **different hash** from `CE424B48…`, because build 1 was compiled from CRLF sources
   (`contract/pinned-build.yaml`, `builds:`). Confirm with `compare_dll` rather than the hash,
   register the new hash as "build 1, LF rebuild", deploy, then return `mod/` to `master`.
2. **Adapter: done.** `brain/stock_adapter.py` `from_observation` bands build 2's frame into
   `run` (survivor, difficulty, `time_band`), `self` (`hp_band`, `barrier_band`) and `hazards`;
   `replay_stock` merges `observations.jsonl` back in by receipt time (≤ 3 s stale).
   `python -m eval.replay_stock --run-id fork-06`: **`next_action` sendable on 447/448 ticks**,
   no `unknown` on the wire. The band cut points are **provisional** — `TIME_BANDS_MIN`
   (10 / 25 min) and `HP_BANDS` (0.70 / 0.30, the latter matching the mod's low-health logic);
   no doc defines them, so review them. Hazard fields are named for what the mod measures:
   `in_void_fog`, not `in_hazard_volume`, and `projectiles_near`, not `incoming_projectiles`.
3. **F32: done.** Nothing is asked of a dead character (`brain/catalog.py`).
4. **`build.identity`: done, provisional.** `brain/build_identity.py` derives it from items: a
   table of internal item name → (tier, tag), tier-weighted, dominant tag at ≥ 40 % share, else
   `mixed`; `undefined` below weight 4; `unknown` if unrecognised items outnumber recognised ones.
   Vocabulary: `undefined`, `mixed`, `damage`, `crit`, `proc`, `survival`, `mobility`. All 38
   item names in the recordings are in the table. **Review the table and the cut points** — no
   doc defines them. Effect on fork-06: `worth_start_teleporter` sendable on the 6 ticks the
   teleporter is offered, the first worth gate ever sendable. `strategy` and `engage_range` are
   still blocked, now only on `threat` sub-fields (elite, enemy classes, focus, feasibility).
5. **Threat telemetry: built, deployed as build 4, not yet run.** The mod sends raw facts
   (`mod/Observation.cs` `Threat`); `brain/enemy_class.py` bands them: `elite_present` from the
   elite count, `enemy_classes` from a provisional body-name table (`is_boss` wins; `unknown` if
   unrecognised bodies outnumber recognised ones), `being_focused` at ≥ 3 AIs targeting the
   player. **Review the body table and `FOCUS_MIN`.** Expected once recorded: `engage_range`
   sendable. `strategy` and `engagement_response` still need path feasibility.

   **Verify with a run** (game closed → AI control on → main menu):
   `python -m eval.fork_check --run-id fork-07 --survivor Commando --difficulty Drizzle`, then
   `python -m eval.replay_stock --run-id fork-07` (expect `engage_range` sent) and
   `python -m eval.inspect_recording --run-id fork-07`. Check: `HARNESS_START` has
   `"block_repeat_damage": false`; `threat.enemy_count` roughly matches stock `enemy_count`
   (stock also counts dead bodies, so it can be higher); `targeting_player_count` rises in a
   fight; every body name in the run is in `BODIES` (unlisted ones show as unclassified).
   Raw `threat.enemies` in the adapter stays empty: no entity IDs, so `fight_boss` still cannot
   target them (F17).
6. **Path feasibility** — the expensive telemetry; unblocks `strategy` and `engagement_response`.
7. Fork work the runs already justify: exactly one terminal event per command (F25), `low_health`
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
