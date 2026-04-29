# Implementation Notes — doctor/update feature

Plan source: `~/.copilot/session-state/0a9db18a-cc0d-47ec-9251-b9eb4537bc3f/plan.md`

## Commit 1 of 4 — `refactor-split-core-wsl`

Pure code-move refactor of `src/dcode/cli.py`. No behavior changes.

### Files

- **Created** `src/dcode/core.py` — `_find_repo_root`, `resolve_worktree`,
  `find_devcontainer`, `get_workspace_folder`, `build_uri`, `run_dcode`.
  Imports `is_wsl`, `build_uri_wsl`, `_ensure_wsl_docker_settings` from `dcode.wsl`.
- **Created** `src/dcode/wsl.py` — `is_wsl`, `get_wsl_distro`,
  `_wsl_to_windows_path`, `build_uri_wsl`, `_get_windows_vscode_settings_path`,
  `_TOP_LEVEL_OBJECT_RE`, `_format_jsonc_value`, `_patch_jsonc_settings`,
  `_ensure_wsl_docker_settings`, `_print_wsl_hint`. Self-contained.
- **Modified** `src/dcode/cli.py` — slimmed to docstring + `argparse` + `main()`,
  imports `run_dcode` from `dcode.core`.
- **Modified** `tests/test_cli.py` — updated import sites and `patch()` targets
  to follow symbols to their new modules. No test logic changes; no new tests.

### Patch-target mapping (used as truth-table for the test rewrite)

| Old target                                  | New target                                   |
|---------------------------------------------|----------------------------------------------|
| `dcode.cli.subprocess.run`                  | `dcode.core.subprocess.run`                  |
| `dcode.cli.is_wsl`                          | `dcode.core.is_wsl`                          |
| `dcode.cli._ensure_wsl_docker_settings`     | `dcode.core._ensure_wsl_docker_settings`     |
| `dcode.cli._wsl_to_windows_path`            | `dcode.wsl._wsl_to_windows_path`             |
| `dcode.cli._get_windows_vscode_settings_path` | `dcode.wsl._get_windows_vscode_settings_path` |
| `dcode.cli.get_wsl_distro`                  | `dcode.wsl.get_wsl_distro`                   |

Reasoning: mock at the import site (where the function is *looked up*).
`is_wsl` / `_ensure_wsl_docker_settings` are imported into `core`, so they
must be patched there even though they live in `wsl`.

### Validation

```
$ uv run ruff check
All checks passed!

$ uv run pytest -q
39 passed in 0.08s
```

Same 39-test baseline as before the split. No deviations from the plan.
