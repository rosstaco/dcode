# Research Findings — `dcode doctor` and `dcode update`

## 1. Problem statement

Add two subcommands to `dcode`:

- `dcode doctor` — diagnose the local environment and report anything that would prevent `dcode <path>` from successfully opening a folder in a devcontainer. Per-check pass/warn/fail with actionable hints.
- `dcode update` — upgrade the installed `dcode` tool. Always shells out to `uv tool upgrade dcode` (user confirmed install method is `uv tool install git+https://github.com/rosstaco/dcode`).

## 2. Install-method facts

User confirmed: dcode is distributed via `uv tool install git+https://github.com/rosstaco/dcode`. Implications:

- **Not on PyPI** in any meaningful sense. PyPI's latest is `0.3.0` (stale, abandoned), while GitHub's latest tag is `v0.4.2`. **Do not use PyPI as the freshness signal.**
- `uv tool upgrade dcode` works for git-installed tools — uv re-resolves the git ref and reinstalls. Verified via `uv tool upgrade --help`.
- `uv tool list` shows `dcode v0.4.2` with `- dcode` script entry — gives us a way to detect the install method.

## 3. Version-check API (GitHub Releases)

**Endpoint:** `GET https://api.github.com/repos/rosstaco/dcode/releases/latest`

**Real response (truncated to relevant fields):**

```json
{
  "tag_name": "v0.4.2",
  "name": "v0.4.2",
  "html_url": "https://github.com/rosstaco/dcode/releases/tag/v0.4.2"
}
```

| Field path | Type | Example | Notes |
|---|---|---|---|
| `tag_name` | string | `"v0.4.2"` | Use this; strip leading `v` for comparison |
| `name` | string | `"v0.4.2"` | Same as tag here |
| `html_url` | string | `"https://github.com/rosstaco/dcode/releases/tag/v0.4.2"` | Show in "update available" hint |

**Constraints:**

- Anonymous GitHub API rate limit: **60 req/hr per IP**. Doctor is interactive and infrequent — fine.
- Set `User-Agent: dcode-doctor` (GitHub requires a UA header; missing UA returns 403).
- Use `Accept: application/vnd.github+json`.
- Returns 404 if no published GitHub *Release* exists yet — fall back to `GET /repos/rosstaco/dcode/tags?per_page=1`. Verified both endpoints return `v0.4.2` today.
- Network may be offline / behind proxy / blocked — must time out gracefully (suggest 3s) and report as a *warning*, not a failure.

**Local version source:** `dcode.__version__` (already wired via `importlib.metadata` → hatch-vcs). For uv-tool installs this resolves to the wheel's hatch-vcs version, e.g. `0.4.2`.

**Comparison:** simple `packaging.version.Version` parse. `packaging` is already an indirect dep of most Python tooling but **not** currently in `dcode`'s deps — recommend a hand-rolled tuple compare on `int(part)` of dot-split numeric prefix to avoid adding a dependency. Tag form is `vX.Y.Z`; PEP 440 dev/local segments from hatch-vcs (e.g. `0.4.2.dev3+g1234`) need to be handled — easiest: split on first non-`[0-9.]` character and compare the numeric prefix; if local is a dev build ahead of latest tag, report "ahead of latest release".

## 4. `uv tool upgrade` behavior

```
$ uv tool upgrade --help
Upgrade installed tools
Usage: uv tool upgrade [OPTIONS] <NAME>...
```

- Exits non-zero if the tool isn't installed via `uv tool` — `dcode update` should detect that and print a useful message instead of just forwarding the error.
- Detection: run `uv tool list` and look for a line starting with `dcode `. If `uv` is missing entirely (`shutil.which("uv") is None`), tell the user how to install uv or upgrade manually.
- Implementation is just `subprocess.run(["uv", "tool", "upgrade", "dcode"], check=False)` then `sys.exit(result.returncode)`. No need to capture output — let it stream to the user's terminal.

## 5. Doctor checks (derived from `src/dcode/cli.py` + user selections)

For each check: status ∈ {ok, warn, fail, skip}, message, optional hint.

| # | Check | How | Status semantics |
|---|---|---|---|
| 1 | `code` / `code-insiders` on PATH | `shutil.which("code")` and `shutil.which("code-insiders")` | fail if **both** missing; warn if only one missing |
| 2 | VS Code Dev Containers extension | `code --list-extensions` (and/or `code-insiders`); look for **`ms-vscode-remote.remote-containers`** | fail if missing on the editor that exists; skip if no editor |
| 3 | Docker (or compatible) runtime running | `docker info --format '{{.ServerVersion}}'` with timeout; non-zero exit ⇒ engine not reachable | fail if `docker` CLI present but daemon unreachable; warn if CLI missing (Podman/Rancher possible) |
| 4 | Git on PATH | `shutil.which("git")` | warn if missing (worktree handling degrades but `dcode` still works on plain dirs) |
| 5 | WSL — running inside WSL? | reuse `is_wsl()` | informational |
| 5a | WSL distro detected | reuse `get_wsl_distro()` (env `WSL_DISTRO_NAME`) | warn if WSL but no distro env (means `executeInWSLDistro` won't be auto-set) |
| 5b | Windows-side VS Code `settings.json` reachable | reuse `_get_windows_vscode_settings_path()` for both stable + insiders | warn if not reachable |
| 5c | `dev.containers.executeInWSL` is `true` and `dev.containers.executeInWSLDistro` matches current distro | parse settings.json with `json5` | warn if missing/wrong; mention `dcode <path>` will auto-fix on next launch |
| 6 | Current dir devcontainer | `find_devcontainer(Path.cwd())` (also resolve worktree first via `resolve_worktree`) | informational: report path + parsed `workspaceFolder` (or "no devcontainer found") |
| 6a | `devcontainer.json` parses cleanly | `json5.loads` of the discovered file | fail if present-but-unparseable |
| 7 | Worktree sanity | call `resolve_worktree(Path.cwd())`; if `target/.git` is a file but `resolve_worktree` returned `None`, it's either a submodule or an external worktree | informational + warn for the "external worktree" case |
| 8 | dcode version vs latest GitHub release | section 3 above | warn if behind; ok if equal; info if ahead (dev build) |
| 9 | Install method | `uv tool list` parsing | informational — needed so `update` can give a useful message |

### 5a. "What would dcode do here?" plan section

Doctor must end with a **plan summary** describing what `dcode <cwd>` *would* do for the current directory, without launching anything. This mirrors `run_dcode()` exactly so the user can sanity-check before running it. Compute by replaying the same control flow against `Path.cwd()`:

1. Resolve `target = Path.cwd().resolve()`.
2. `worktree = resolve_worktree(target)`.
   - If `worktree` is not None → main repo + relative path; `devcontainer = find_devcontainer(main_repo)`.
   - Else → `devcontainer = find_devcontainer(target)`.
3. Branch on `devcontainer is None`:
   - **None:** plan = "open `<target>` in `<editor>` directly (no devcontainer found)". Note: for a worktree this means dcode would *not* fall back to the main repo's devcontainer because `find_devcontainer` was already called against the main repo — so the message is just "no devcontainer in main repo `<main_repo>` either".
   - **Found:** compute `host_path` and `workspace_folder` exactly like `run_dcode` (worktree → main repo + suffixed `workspaceFolder`; otherwise → target). Build the URI via the right builder (`build_uri_wsl` if `is_wsl()` else `build_uri`). Print:
     - editor (`code` or `code-insiders`; doctor reports both — see below)
     - host path (and Windows UNC equivalent on WSL)
     - devcontainer config path
     - effective `workspaceFolder` value
     - the `vscode-remote://` URI that would be passed to `--folder-uri`
     - on WSL: whether `_ensure_wsl_docker_settings()` would write to `settings.json` on this run (compute desired vs current; report "would patch" / "no changes")
4. **Editor selection in the plan:** doctor doesn't get `-i`. Show the plan for the **default `code`** path, but include a one-line note for `-i/--insiders` if `code-insiders` is also installed.

Output goes under a final `Plan for <cwd>:` section after the checks summary. Plan output is informational only — it never changes doctor's exit code.

**Worktree-specific messaging:** when `resolve_worktree` returns a value, explicitly say:
> Detected git worktree. dcode will open the **main repo** at `<main_repo>` so all worktrees share one container. The container's `workspaceFolder` will be `<base>/<rel_path>` (= `<full_workspace_folder>`).

When `target/.git` is a file but `resolve_worktree` returned `None` (submodule or external worktree), say:
> `<target>` looks like a worktree or submodule but cannot be resolved (external worktree or submodule). dcode will open it directly without shared-container support.

**Output format:** one line per check, leading status glyph (`ok`/`warn`/`fail` text — no emoji per user's general style). Final summary line: `dcode doctor: N ok, M warn, K fail`. Exit code: `0` if no `fail`, `1` otherwise. Warnings do not fail.

## 6. Existing architecture / conventions to follow

From `src/dcode/cli.py` and [.github/copilot-instructions.md](.github/copilot-instructions.md):

- **Single-module CLI.** Add `doctor` and `update` as new subcommands inside `cli.py`. Do **not** split into a package unless it grows past ~600 lines.
- All user-facing output → `sys.stderr`. (Doctor report is user-facing → stderr. Keep stdout clean.)
- `subprocess.run(..., check=False, timeout=...)` with explicit timeouts; catch `(OSError, subprocess.TimeoutExpired)`.
- Reuse existing helpers: `is_wsl`, `get_wsl_distro`, `_get_windows_vscode_settings_path`, `find_devcontainer`, `resolve_worktree`, `get_workspace_folder`. Several are currently `_`-prefixed; promote on use or call them as-is — they're internal to the same module.
- Tests use `tmp_path` and patch `subprocess.run`. New tests must mock all network + subprocess; no real GitHub calls in the suite.
- ruff line-length 100, target py311; rules E/F/I/UP/B/SIM (from `ruff.toml`).
- Conventional Commits required. New work: `feat: add doctor and update subcommands`.

## 7. argparse migration

Current parser is a flat `argparse.ArgumentParser` with positional `path` and `-i/--insiders`. Adding subcommands while keeping `dcode <path>` working is a small refactor:

- Use `add_subparsers(dest="command", required=False)`.
- Default behavior (no subcommand) = current "open" behavior, including bare `dcode` and `dcode <path>`.
- Subcommands: `doctor`, `update`. Reserve names so `dcode doctor` / `dcode update` aren't ambiguous with paths named `doctor`/`update`. Document that ambiguity in `--help` (workaround: `dcode ./doctor`).

## 8. Constraints / gotchas

- **No new runtime deps.** Use stdlib `urllib.request` for the GitHub call (timeout=3s, set UA + Accept headers). Adding `requests` or `httpx` is overkill.
- **Network-off case** must be a warning, not a crash. Catch `urllib.error.URLError`, `TimeoutError`, `OSError`.
- **JSON parsing of GitHub response** uses stdlib `json`, not `json5`.
- **macOS specifically**: `code` CLI is only present if user ran "Shell Command: Install 'code' command in PATH" from VS Code. Doctor's "missing `code`" hint should mention this.
- **Docker on macOS**: `docker info` against a stopped Docker Desktop returns non-zero with `Cannot connect to the Docker daemon` — that's the signal.
- **`uv tool list` output format** is stable enough to grep for `^dcode `. Verified above. If absent, fall back to "dcode does not appear to be installed via `uv tool`; run `uv tool install git+https://github.com/rosstaco/dcode` or upgrade however you originally installed it."
- **Self-update under uv-tool venv**: `uv tool upgrade dcode` rebuilds the tool venv. The currently-running `dcode update` process keeps running from the *old* venv until it exits — this is fine because we just `sys.exit(rc)` after the upgrade. No need for re-exec gymnastics.
- **Subprocess to `uv` from inside a `uv tool`-installed dcode**: `uv` is on PATH for the user's shell but is not a dependency of dcode's venv. `shutil.which("uv")` against the user's PATH is what we want — that's the default for `subprocess.run`.

## 9. Open questions

None. All answered by user's questionnaire responses + confirmation that install is via `git+`. Ready for the Plan phase.

## 10. Module split (decided)

Current `src/dcode/cli.py` is ~370 lines and `tests/test_cli.py` holds 39 tests. Adding `doctor` (9 checks + a plan-summary that replays `run_dcode`) and `update` will roughly double both. Split now — minimum viable, no subpackages.

### Source layout

```
src/dcode/
├── __init__.py    (unchanged — exports __version__)
├── __main__.py    (unchanged — `python -m dcode`)
├── cli.py         (argparse + subcommand dispatch only)
├── core.py        (resolve_worktree, _find_repo_root, find_devcontainer,
│                   get_workspace_folder, build_uri, run_dcode)
├── wsl.py         (is_wsl, get_wsl_distro, _wsl_to_windows_path,
│                   build_uri_wsl, _get_windows_vscode_settings_path,
│                   _TOP_LEVEL_OBJECT_RE, _format_jsonc_value,
│                   _patch_jsonc_settings, _ensure_wsl_docker_settings,
│                   _print_wsl_hint)
├── doctor.py      (new — checks + plan-summary replay of run_dcode)
└── update.py      (new — `uv tool upgrade dcode` + detect_install_method)
```

### Test layout (mirror)

```
tests/
├── conftest.py        (new — shared `_make_worktree(tmp_path, name)` helper
│                       currently embedded in test_cli.py)
├── test_core.py       (worktree resolution, devcontainer discovery,
│                       workspace-folder parsing, build_uri, run_dcode integration)
├── test_wsl.py        (is_wsl/get_wsl_distro, build_uri_wsl,
│                       _patch_jsonc_settings, _ensure_wsl_docker_settings)
├── test_cli.py        (argparse dispatch only — small)
├── test_doctor.py     (per-check + plan-summary tests)
└── test_update.py     (uv-tool detection + upgrade shell-out)
```

### Rules and non-goals

- Cross-module imports of `_`-prefixed helpers are fine — still a single distribution.
- Do not rename helpers just to make them "public."
- No `checks/` subpackage with one file per doctor check — keep all 9 in `doctor.py`.
- `doctor` imports `detect_install_method()` from `update` (single source of truth).
- `cli.py` stays small enough to read in one screen after the split (~80 lines).

### Sequencing (one commit per step)

1. `refactor: split cli into core and wsl modules` — pure move, all 39 existing tests pass with at most import-line edits.
2. `refactor: split test_cli into test_core, test_wsl, conftest` — pure move, no new assertions.
3. `feat: add update subcommand` — adds `update.py` + `test_update.py`, wires dispatch into `cli.py`.
4. `feat: add doctor subcommand` — adds `doctor.py` + `test_doctor.py`, wires dispatch into `cli.py`. (Last because it imports from `update`.)
