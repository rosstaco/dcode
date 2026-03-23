# Implementation Notes

## Summary

Plan followed as-is. No deviations.

## Files Created

1. `pyproject.toml` — package config with hatchling build, json5 dep, pytest dev dep
2. `src/dcode/__init__.py` — version string
3. `src/dcode/cli.py` — all logic (68 lines)
4. `tests/test_cli.py` — 13 tests covering all plan test cases
5. `README.md` — install and usage docs
6. `LICENSE` — MIT

## Discoveries

- Hatchling requires README.md to exist at build time (even if listed in pyproject.toml) — created it early
- `uv tool install .` works cleanly, installs `dcode` to `~/.local/bin/`
- URI formula verified against research hex values — exact match
- json5 handles JSONC (comments + trailing commas) correctly as expected

## Things for Verify to Check

- Actually launch `dcode .` in a folder with a devcontainer and confirm VS Code opens in container mode
- Test on a folder without devcontainer to confirm plain `code` fallback
- Test `dcode --insiders` if code-insiders is available
