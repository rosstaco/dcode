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

## Commit 3 of 4 — `version_check` + `update`

### Files

- **Created** `src/dcode/version_check.py` — `NetworkError`, `get_latest_release`,
  `parse_version`, `compare_versions`. Stdlib-only (`urllib.request`, `json`,
  `re`). Headers `User-Agent: dcode-doctor` + `Accept: application/vnd.github+json`.
  Falls back from `/releases/latest` (404) to `/tags?per_page=1` and synthesizes
  `html_url`. Wraps `(URLError, HTTPError, TimeoutError, OSError, ValueError,
  json.JSONDecodeError)` as `NetworkError`.
- **Created** `src/dcode/update.py` — `detect_install_method`, `run_update`,
  `run_update_check`. All user-facing output → stderr.
- **Modified** `src/dcode/cli.py` — added `update` subcommand wiring (see
  argparse note below).
- **Created** `tests/test_version_check.py` — 30 tests (parametrized).
- **Created** `tests/test_update.py` — 13 tests.
- **Modified** `tests/test_cli.py` — added 6 dispatch tests.

### Argparse: subparser + top-level positional collision

The natural form (`add_subparsers` + top-level positional `path`) **does**
misbehave on Python 3.13: `dcode ./somepath` fails with
`argument command: invalid choice: './somepath'` because argparse tries to
match the positional against the subparser choices.

**Workaround taken (the `sys.argv` peek the plan §8 documented):**
peek `sys.argv[1:]` for the first non-flag token. If it's in
`_SUBCOMMANDS = ("update",)` (or it's `-h/--help`), use the full
subparser-aware parser. Otherwise use a stripped-down legacy parser that
only knows about `path` and `-i`. This keeps `dcode ./update` working as
a folder-open and `dcode update` as a subcommand. Commit 4 should add
`"doctor"` to `_SUBCOMMANDS` and add the `doctor` subparser.

### Plan deviations

- **`compare_versions` semantics:** task spec required `compare_versions("0.4.2.dev0+g…", "0.4.2") == 0` (load-bearing rule). Plan §11 #12 had this case as `+1`. I followed the task spec — comparison is now purely on the numeric prefix, ignoring dev/post suffixes. The "ahead" message in `run_update_check` is driven instead by `parse_version`'s `is_dev` flag.
- **`parse_version` signature:** task spec was 2-tuple `((nums...), is_dev)`; plan §7 had a 3-tuple including raw suffix. Followed task spec.
- **`parse_version` invalid input:** task spec required raising `ValueError`; plan §7 implementation silently returned `((0,), False, s)`. Followed task spec — strict.
- **No `name` field in `get_latest_release` return:** plan said `{tag_name, name, html_url}`; task said `{tag_name, html_url}`. Followed task.

### Commit 3 validation (run on this machine, 2026-04-29)

```
$ uv tool list
dcode v0.4.2
- dcode
fs2 v0.1.0
- fs2
ghostcfg v0.1.3
- gcfg
- ghostcfg
…
```
Confirms `^dcode\s+v\d` regex matches today's output.

```
$ curl -sSL -H 'User-Agent: dcode-doctor' -H 'Accept: application/vnd.github+json' \
    https://api.github.com/repos/rosstaco/dcode/releases/latest
{ … "tag_name":"v0.4.2", "html_url":"https://github.com/rosstaco/dcode/releases/tag/v0.4.2", … }
```
Confirms today's release schema unchanged from research.

```
$ uv run python -m dcode update --check
local:   0.4.2.dev0+g5f9826397.d20260427
latest:  v0.4.2
release: https://github.com/rosstaco/dcode/releases/tag/v0.4.2
dcode is ahead of the latest release
exit=0
```
Confirms the dev-build "ahead" branch fires correctly end-to-end. Did **not** run `dcode update` (no flags) — would attempt a real upgrade.

### Test count

Before commit 3: 39 passed. After: 91 passed (+52). Ruff clean.

## Commit 4 of 4 — `doctor`

### Files

- **Created** `src/dcode/doctor.py` — per-check functions (`check_editor`,
  `check_extension`, `check_docker`, `check_git`, `check_wsl`,
  `check_wsl_distro`, `check_wsl_settings_paths`,
  `check_wsl_executeInWSL_settings`, `check_devcontainer`,
  `check_devcontainer_parses`, `check_worktree`, `check_version`,
  `check_install_method`), `render_plan`, `_render_wsl_settings_preview`,
  `run_doctor`. All output to stderr, no file writes.
- **Created** `tests/test_doctor.py` — 53 tests covering every check branch,
  the plan-summary cases (incl. WSL preview), and driver exit-code logic.
- **Modified** `src/dcode/cli.py` — added `"doctor"` to `_SUBCOMMANDS`,
  added `doctor` subparser. Used `dest="doctor_path"` (with
  `metavar="path"`) to avoid colliding with the top-level `path` positional —
  argparse otherwise lets the top-level default `"."` overwrite the
  subparser's value (verified: `dcode doctor /tmp` was being parsed as
  `path="."`, `doctor_path="/tmp"` collapsed to just `path="."`).
- **Modified** `tests/test_cli.py` — added 4 dispatch tests
  (doctor default, doctor with path, exit-code forwarding,
  `./doctor` escape hatch).

### Plan deviations

- The plan §4 wording template was followed as written for status mapping
  and most messages. Where the plan had a single placeholder (e.g.
  "5b warn for one editor"), the implementation emits one result per editor
  (stable + insiders) for clarity; both still tagged `warn` if unresolvable.
- `check_install_method` for the `"unknown"` branch drops the
  `({err})` interpolation since `detect_install_method` doesn't expose
  the underlying exception.
- `render_plan` field labels use lowercase ("editor", "host path",
  "devcontainer config path", "effective workspaceFolder", "URI") — the
  plan was inconsistent (some camelcase, some lowercase). Recorded here
  for the verifier.
- The "extra_note" line for the both-editors case is rendered as
  `also available: \`dcode -i <path>\` would use code-insiders`
  (plan said "(note: -i / --insiders would use code-insiders instead)").
  Followed the task brief's wording, which was more concrete.

### argparse — top-level vs subparser positional collision

When a top-level positional `path` (default=".") coexists with a subparser
that also defines a `path`, argparse stores both under `args.path` — and
the top-level default overwrites the subparser's value. Fix: the doctor
subparser uses `dest="doctor_path"` (with `metavar="path"` so the help
text still reads "path"). The dispatcher reads `args.doctor_path`.

### Commit 4 validation (run on this machine, 2026-04-29)

```
$ uv run python -m dcode doctor
warn  VS Code editor: code (code-insiders not on PATH)
      hint: install VS Code Insiders or run "Shell Command: Install 'code-insiders' command" from the Command Palette
ok    Dev Containers extension: ms-vscode-remote.remote-containers (code)
ok    Container runtime: docker daemon reachable (29.4.0)
ok    git: /opt/homebrew/bin/git
ok    WSL: not running in WSL (skipping WSL-specific checks)
warn  devcontainer: none found in /Users/rossmiles/repos/rosstaco/dcode — dcode will open the folder directly without a container
      hint: add .devcontainer/devcontainer.json to enable container support
skip  devcontainer.json: no file to parse
ok    worktree: target is a regular git repo (or non-repo)
ok    dcode version: 0.4.2.dev11+g9434082fc.d20260429 (ahead of latest release v0.4.2)
ok    install method: uv tool (upgradable via "dcode update")

dcode doctor: 7 ok, 2 warn, 0 fail

Plan for /Users/rossmiles/repos/rosstaco/dcode:
  No devcontainer found — would open /Users/rossmiles/repos/rosstaco/dcode in code directly.
exit=0
```

```
$ uv run python -m dcode doctor /tmp
... same prefix ...
warn  devcontainer: none found in /private/tmp — dcode will open the folder directly without a container
ok    worktree: not a git repo
...
Plan for /private/tmp:
  No devcontainer found — would open /private/tmp in code directly.
exit=0
```

`code-insiders` is not installed on this machine (warn) and Docker Desktop
was running at validation time (`docker daemon reachable (29.4.0)`).

### Test count

Before commit 4: 91 passed. After: 154 passed (+63). Ruff clean.
