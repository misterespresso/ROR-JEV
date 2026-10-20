# Repository Guidelines

## Project Structure & Module Organization

- `brain/`: Python decision loop components, JEV client, policy, action registry, and protocol adapters.
- `mod/`: C# Rainflayer fork for BepInEx; builds to `mod/bin/Debug/netstandard2.1/`.
- `contract/`: questions, capabilities, generated actions, and pinned build metadata.
- `eval/`: executable tests, fake mods, recording, replay, and game probes.
- `tools/`: deployment and DLL inspection utilities. `docs/`: architecture, protocol proposals, findings, and handoffs.
- `recordings/` and `upstream/` are ignored local data. Game assemblies and assets remain outside the repository.

Start with `docs/11-HANDOFF-2.md`, then `docs/10-HANDOFF.md`; parts of the README describe earlier plans.

## Build, Test, and Development Commands

Use Python 3.10+; the Python tools use the standard library. Run from the repository root unless specified.

- `python -m eval.run_suite`: run offline trace tests and generated-contract checks.
- `python eval/test_jev_loop.py`: run the focused offline decision-loop tests.
- `python -m eval.gen_actions`: regenerate `contract/actions.yaml` after registry changes.
- From `mod/`, run `dotnet build -c Debug`: build using the SDK pinned in `mod/global.json`. Override `RoR2Managed` and `R2Profile` with `-p:Name=path` for alternate installations.
- `python -m eval.jev_run --run-id jev-local --survivor Commando --difficulty Drizzle`: start the live controller before launching the configured game.

## Coding Style & Naming Conventions

Use four-space indentation. Follow Python's existing type hints, `snake_case` functions/modules, `PascalCase` classes, and `UPPER_SNAKE_CASE` constants. Match C#'s existing `PascalCase` types/methods, camelCase fields, and braces on separate lines. Preserve LF endings required by `.gitattributes`. No formatter or linter is configured.

## Testing Guidelines

Tests are custom Python trace scripts using shared `check()` assertions, not pytest. Add regressions in `eval/test_*.py`; register new scripts in `eval/run_suite.py`. Run the suite before submitting behavior changes. No percentage coverage threshold is configured. Python passes do not establish Unity behavior or protocol-v2 conformance. Mock socket tests must use `ROR_JEV_PORT=17777`, never the live port 7777.

## Commit & Pull Request Guidelines

History uses descriptive subjects, often prefixed with finding IDs (`F55:`) or build numbers (`Build 18 (F56-F58):`). Follow that style where applicable. PRs should explain behavior changes, link relevant findings/issues, report validation, and include recording IDs or screenshots for game-visible changes.

## Architecture & Configuration

JEV selects strategic actions; Python validates them, and C# executes them with bounded reflexes. Keep credentials in `JEV_KEY` or `TYPESAFE_API_KEY`, never committed. Deploy with the game closed and restart afterward; record build hashes in `tools/deploy.py` and `contract/pinned-build.yaml`.
