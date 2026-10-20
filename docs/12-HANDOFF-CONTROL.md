# Handoff 3 — Control fixes, 2026-09-22

Objective: JEV should play well. `jev-18` produced the first Stage 1 clear and entered Wetland
Aspect. The milestone is now to make that repeatable while improving loot execution.

## What jev-18 established

Build 19 ran for 885 JEV decisions with no API errors. It completed Stage 1, entered
`foggyswamp`, and collected ten ordinary items. It exercised all three objective phases:
779 pre-teleporter ticks, 88 charging ticks, and 18 charged ticks. Five task stops were all
acknowledged. This validates the control fixes from F59-F61 in one run, but not repeatability.

Before adjusting boss readiness or progression thresholds, inspection found execution faults:

- Python released stuck/timed-out tasks without stopping navigation in the mod. A subsequent
  `MODE roam` did not clear that navigation. Stops now require a `GOTO cancel` acknowledgement.
- Exploring or fighting could never redirect an active walk. Two consecutive authorized
  JEV choices now can, with cancellation before the replacement command.
- JEV saw `active_action: null` throughout the recording. It now sees the active task's ID
  and description. Progress telemetry remains unknown.
- The teleporter's positional ID changed when another object moved closer. It now has a
  stable identity within the scene. Filters reset between scenes.

The user's end-of-run observations exposed two more concrete faults:

- Nearby enemies cancelled an authorized interaction before the chest opened. Build 20 keeps
  the interaction and fires at enemies concurrently.
- JEV opened an equipment barrel at tick 864, then left its drop because the drop was absent
  from telemetry. Empty-slot equipment is now an automatic pickup. Replacing carried equipment
  is a named JEV choice with both equipment effects visible.

Findings and limits are documented as F59-F63 in `08-PHASE0-REPORT.md`.

## Build and validation

Build 20 is deployed in the Jev profile; `python tools/deploy.py --status` confirms the
installed DLL matches this build. It has not yet been run in game.
SHA-256: `C221E5F8AF1D5EDC3417E17A7E9BAD63AF8AF78C53D556D816783FB5B6BA35A7`.
The Debug build succeeds. Its new methods implement equipment-drop observation and targeted
pickup; the other intended changes are interaction priority, pickup behavior, command dispatch,
and reset/cancellation. Existing nullable/unused-member warnings remain. The build hash is
registered in `tools/deploy.py` and `contract/pinned-build.yaml`.

All 368 offline checks pass (`python -m eval.run_suite`). Trace tests cover cancellation
ordering, missing acknowledgements, replacement
authorization, task visibility, teleporter identity, and explicit equipment replacement.

## Next live evaluation

Start the controller before launching the modded game:

```powershell
python -m eval.jev_run --run-id jev-19 --survivor Commando --difficulty Drizzle
```

Launch the Jev profile through r2modman's **Start modded** and start a new run. Confirm the
recorded `mod_build` is 20. The loop still switches to roaming on exit, as in Handoff 2.

Inspect chest interactions with enemies nearby, ordinary pickup under fire, empty-slot equipment
pickup, and a replacement decision if one appears. Keep JEV as the strategic selector and retain
the teleporter worth gate. Repeatable stage progression, general jump-pad navigation, and
defensive roll timing are still open.
