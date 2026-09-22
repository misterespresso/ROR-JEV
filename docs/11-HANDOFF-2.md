# Handoff 2 — 2026-09-22

Read this first, then `10-HANDOFF.md` for the Phase 0 half. This session took the project from
"the brain has never made a decision" to **JEV playing the game end to end**: it reads the game
every second, picks from options it can tell apart, and the fork carries out the pick.
Findings F31–F58 are in `08-PHASE0-REPORT.md`; builds 3–18 are in `contract/pinned-build.yaml`.

## Where things stand

| | State |
|---|---|
| Loop | `python -m eval.jev_run` — poll → adapter → registry → request → **JEV** → dispatcher → fork commands. 17 runs (`recordings/jev-01..17`) |
| Tests | **350 passing** (`python -m eval.run_suite`), including an offline loop test with a stand-in JEV |
| Deployed | **build 18** `1F925615…` (`python tools/deploy.py --status`); source has one unbuilt line (below) |
| Cost / speed | ~2,900 input tokens per call, 505 ms median (720 p99), 1 decision/s, **~$0.43/hour** |
| Key | `JEV_KEY` (user-level env var). `brain/jev_client.py` also reads it from the registry on Windows |
| Git | local only, clean history |

**What JEV can do now:** choose a task each tick (walk to / open / buy / repair / fight / explore /
start the teleporter), choose who to shoot (`target_priority`), choose engagement range, and fire
equipment. What it cannot: `strategy` and `engagement_response` have never been sendable (they
need path feasibility, which nothing measures).

## The one thing that has never happened

**No run has ever finished a stage.** 17 runs, all on stage 1. The teleporter is the only exit and
JEV will not start it:

| run | `worth_start_teleporter` (needs > 0.75) | ticks it was even offered |
|---|---|---|
| jev-08..11 | 0.12–0.31 | few |
| jev-15 | 0.29–0.64 | 27 of 537 |
| jev-17 (build 18) | 0.13–0.53 | **6 of 605** |

Two separate problems, and the second is the bigger one:

1. **It is rarely asked.** `start_teleporter` only enters the registry when the character is
   *standing at* the teleporter (`in_interaction_range`, 6 m). In jev-17 that was 6 ticks out of
   605. Everything else is `navigate`/`explore`. Offline, with the teleporter in range and the
   stage picked over, JEV *does* authorise: worth **0.78–0.80**, pick 0.69–0.76 (§F55). The live
   loop almost never reaches that state.
2. **It never runs out of things to do.** `objective.unopened_left_here` was `plenty` on 464 of
   605 ticks — a stage always has more chests than gold, so the "stage is picked clean" condition
   the primer leans on never arrives.

Ideas, in the order I would try them: make `start_teleporter` offerable from further away (walk
and start as one action, like `FIND_AND_INTERACT teleporter` did before F35 split them — but
gated, which is the part that must not regress); or give JEV a stage budget in state (something
like "this stage has taken 3× the median") rather than leaving `overdue` to carry all the weight;
or ask the worth question about *staying* instead of starting. Do not simply lower
`WORTH_THRESHOLD` (0.75) — that gate is what stopped jev-01 dying at 80 s to a boss JEV had
already judged not worth waking.

## The other open behaviour

**It re-walks ground it has already covered** (jev-17: `navigate` 285, `explore` 223 of 605).
Nothing in state says where the character has been. The mod's roam keeps `visitedPositions`
internally, but JEV cannot see it and `explore` means "roam somewhere". A coverage fact in state —
even a crude "fraction of the stage's seen interactables already used" or a visited-region band —
is the missing input.

**Stuck on terrain** (jev-16): the character ran at a wall it could not climb and walked past a
jump pad that would have carried it there. The mod only understands jump pads on the moon stage
(`Moon2JumpPadChain`). General `JumpVolume` support, reported as an interactable JEV can walk to,
is unbuilt.

**F38, the roll, is still open.** The utility skill fires whenever it is off cooldown, so it is
rarely available for a Titan fist or Golem laser. The plan: reflex in the mod (dodge when a nearby
enemy is winding up — entity states are readable), policy from JEV (save / spend freely).

## Build the next session must deploy

`mod/Observation.cs` has one line that is committed but **never built into a deployed DLL**:
`P("unseen", …)` in the interactables block. Build it, deploy, restart the game, and the snapshot
will finally report how many interactables were withheld as unseen.

That line matters for a second reason. **F56 is partly wrong and the report says so**: I concluded
jev-14..16 "ran stale" because their frames lacked `unseen` — but no build ever emitted `unseen`,
so that inference proved nothing. The restart rule is still real (BepInEx loads the DLL at
startup) and `mod_build` now makes it checkable — jev-17 confirmed `running: 18, expected: 18`.
Treat the F56 *conclusion* about those three runs as unproven.

## How the loop works (`eval/jev_run.py`)

Every second, while alive and in a run:

1. `poll` — six stock queries plus `QUERY_OBSERVATION`.
2. `brain/stock_adapter.adapt` — bands everything; anything unmeasured stays `unknown`.
3. `brain/registry.build_registry` — the options, capability- and range-gated.
4. `brain/catalog.build_request` — omits a question if any field it declares is unknown, then
   strips `unknown` from the state sent.
5. `brain/jev_client.ask` — the one network call.
6. `brain/policy.dispatch` — probability gate, worth gate, task slot.
7. `brain/stock_bridge.to_mod` — v2 command → fork command.

Guards the runs forced, all in the loop and all recorded:

- **redirect**: a walk is abandoned when JEV wants something else two ticks running (≥ 0.5).
- **stuck**: a walk whose target gets no closer than 1 m in 12 s is abandoned; that target is
  hidden for 180 s (`UNREACHABLE_QUARANTINE_S`).
- **refused**: an action a gate refuses is withheld 30 s — and so is walking back to it.
- **declined**: an action passed over 3 ticks while in range hides that object for 60 s.
- **dead**: nothing is asked of a dead character.

## Tables to review (all provisional, all mine, none from a doc)

| file | what | why it matters |
|---|---|---|
| `brain/build_identity.py` | item → (tier, tag); `EFFECTS` one-liners | drives `build.identity` and what JEV knows its items do |
| `brain/enemy_class.py` | body → class; `FOCUS_MIN` | `enemy_classes`, `being_focused` |
| `brain/interactable_type.py` | prefab → kind, labels | every option's wording; chest and drone variants |
| `brain/equipment.py` | equipment → what it does, when it applies | whether `use_equipment_now` is asked |
| `brain/stock_adapter.py` | `TIME_BANDS_MIN`, `HP_BANDS`, `STAGE_TIME_BANDS_MIN`, `DIFFICULTY_BANDS` | every time/health/difficulty judgement |
| `contract/questions.yaml` | `game_primer` (14 facts), option text | the biggest lever on JEV's behaviour, by far |

**The primer and option text are the main tuning surface.** Every behaviour complaint this session
that was not a bug was fixed by telling JEV something true it could not see: what a drone does,
that a stage ends only through the teleporter, that difficulty climbs with time, that unaffordable
chests are not a reason to stay. When JEV does something odd, read the exact request it was sent
(`JEV_EXCHANGE` frames) before changing code.

## Method that worked

- **Reproduce from the recording before changing anything.** Every `jev-*` run records the full
  request, the answers, and the commands. Most complaints resolved to "JEV was never offered it"
  or "the option text argued against itself", not "JEV chose badly".
- **Re-ask the model on the recorded tick** after a change (`ask(req.wire())` on a replayed
  observation). That is how F34, F49, F54 and F55 were confirmed before ever launching the game —
  costs a fraction of a cent.
- **Separate decisions from reflexes.** Picking up an item, opening a free barrel, dodging, and
  target-locking are not decisions; they belong in the mod. What to do next, who to shoot, whether
  to spend gold — those are JEV's.

## Traps (in addition to 10-HANDOFF's)

- **Deploying is not enough: restart the game.** `tools/deploy.py` says so now, and `jev_run`
  prints the running `mod_build` on the first frame.
- **`jev_run` writes `MODE roam` on exit**, so the character keeps moving after Ctrl+C.
- **`[EDGE]`, `[PICKUP]`, `[BARREL]` logs are `LogDebug`** and the profile has `DebugMode = false`.
  Set it true (game closed) to see the reflexes in the BepInEx log.
- **Build 6+ entity IDs are `kind@instanceID`.** `stock_bridge.instance_id` splits on `@`; stock's
  positional IDs (`chest-0`) have no `@` and cannot be targeted by `INTERACT_ID`.
- **The suite reports a crashed test file as a failure now** (F31). If a target's count drops to
  zero, that is a crash, not a pass.

## Working with this user

- They watch the runs and report what they see. **Their observation has been right every time**,
  including when the data first looked like it disagreed (the fire-enemy walk-in, the drone, the
  chest across the map). Start from "what did JEV actually get offered at that moment".
- They care that the agent plays *fairly*: seeing only what a player could see (F52) was their
  call, not a design requirement.
- Deliver a run they can start in one command, and do not make them alt-tab mid-run.
