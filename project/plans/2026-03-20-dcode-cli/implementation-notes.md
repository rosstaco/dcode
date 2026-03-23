# Implementation Notes

## Summary

Plan followed with WSL enhancement added post-initial implementation.

## Files Created

1. `pyproject.toml` — package config with hatchling build, json5 dep, pytest dev dep
2. `src/dcode/__init__.py` — version string
3. `src/dcode/cli.py` — all logic (~110 lines)
4. `tests/test_cli.py` — 19 tests
5. `README.md` — install and usage docs
6. `LICENSE` — MIT

## WSL Support (post-plan addition)

The original plan didn't account for WSL with native Docker (no Docker Desktop). When `code` runs from WSL, it launches Windows-side VS Code which defaults to `docker.exe`. Two fixes applied:

1. **JSON URI format**: On WSL, hex-encodes `{"hostPath": "<path>"}` instead of just the path, giving VS Code proper host context.
2. **Auto-patch VS Code settings**: On first WSL run, `dcode` finds the Windows-side `settings.json` (via `cmd.exe` + `wslpath`) and adds:
   - `"dev.containers.executeInWSL": true`
   - `"dev.containers.executeInWSLDistro": "<distro>"`
   
   This is idempotent — skips if already configured. Falls back to printing manual instructions if the settings file can't be found.

## Discoveries

- Hatchling requires README.md to exist at build time
- `uv tool install .` works cleanly, installs `dcode` to `~/.local/bin/`
- URI formula verified against research hex values — exact match
- json5 handles JSONC (comments + trailing commas) correctly
- `--folder-uri` is not in `code --help` but works fine
- VS Code settings.json on Windows is JSONC too — used json5 to parse, json to write (clean output)

## Things for Verify to Check

- Test `dcode .` on macOS with a devcontainer folder
- Test `dcode .` on WSL with native Docker — verify settings patch works
- Test fallback behavior (no devcontainer)
- Verify `uv tool install git+https://github.com/rosstaco/dcode` after push
