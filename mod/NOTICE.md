# Provenance

This directory is a fork of **Rainflayer** by justindwang.

| | |
|---|---|
| Upstream | https://github.com/justindwang/rainflayer |
| Base commit | `01137e5` (2026-03-09), declares plugin version `1.1.1` |
| Decision record | `../docs/09-FORK-BASE.md` |

## License

Upstream declares **MIT** in its README (`## License` / `MIT`). The repository ships **no LICENSE
file and no copyright notice**, so there is no notice text to carry forward verbatim, which is what
the MIT license ordinarily asks of a copy. This fork keeps attribution here instead.

If this fork is ever published, ask upstream to add a LICENSE file first. The grant currently rests
on one word in a README.

## What differs from upstream

- `Rainflayer.csproj` — build 0 retargets references to BepInEx 5 and real install paths. Sources
  unchanged. See the header comment in the project file.
- `global.json` — pins the .NET SDK, because upstream's releases cannot be rebuilt partly for want
  of any record of how they were built.
