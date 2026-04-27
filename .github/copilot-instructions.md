# Copilot Instructions for dcode

## Build & Test

```bash
# Run all tests
uv run pytest

# Run a single test by name
uv run pytest -k test_resolves_worktree_with_relative_gitdir

# Run a test class
uv run pytest -k TestResolveWorktree
```

Linting is via `ruff` (`uv run ruff check`). Versioning is automated: `hatch-vcs` derives the version from the latest git tag, and [release-please](https://github.com/googleapis/release-please) manages tags + the changelog from [Conventional Commits](https://www.conventionalcommits.org/). **Do not** edit a version field in `pyproject.toml` or `src/dcode/__init__.py` — both are derived from the git tag at install/build time via `importlib.metadata`.

## Architecture

dcode is a single-module CLI (`src/dcode/cli.py`) that constructs `vscode-remote://dev-container+<hex-encoded-host-path><workspace-folder>` URIs and launches VS Code with `--folder-uri`. The entrypoint is `dcode.cli:main`.

The core flow in `run_dcode()`:

1. **Worktree detection** — `resolve_worktree()` checks if `.git` is a file (not directory), parses the `gitdir:` pointer, and validates the path structure to distinguish worktrees from submodules. When the gitdir contains an absolute path from a different environment (e.g. a container), it falls back to walking ancestor directories for the real `.git` dir.
2. **Config lookup** — `find_devcontainer()` searches for `.devcontainer/devcontainer.json` or `.devcontainer.json` in the target (or main repo for worktrees).
3. **URI construction** — Two codepaths: `build_uri()` for native systems (hex-encodes the host path directly) and `build_uri_wsl()` for WSL (wraps a Windows UNC path in a JSON payload). For worktrees, the host path is always the main repo root so all worktrees share one container.

## Conventions

- `json5` is used to parse `devcontainer.json` (supports JSONC comments and trailing commas).
- All user-facing messages go to `sys.stderr`; stdout is reserved for machine output.
- Tests use `tmp_path` fixtures with mock filesystem layouts (fake `.git` files/dirs) — no real git repos needed. `subprocess.run` is always patched in integration tests.
- The helper `_make_worktree(tmp_path, name)` in the test file creates a complete fake main-repo + worktree layout for test reuse.
