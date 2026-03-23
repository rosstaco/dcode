# Research Findings: dcode CLI

## Problem Statement

Build a CLI tool (UV script) that opens a folder directly in a VS Code devcontainer from the terminal — replacing the `code .` → "Reopen in Container" two-step workflow. Must support VS Code Stable and Insiders, and work on macOS, Windows, and WSL.

## Core Mechanism: `vscode-remote://` URI Scheme

VS Code accepts a `--folder-uri` flag (undocumented but confirmed working on VS Code 1.112.0) that can open remote contexts directly.

### URI Format

```
vscode-remote://dev-container+<hex-encoded-host-path>/workspaces/<folder-name>
```

- **`<hex-encoded-host-path>`**: The full absolute path to the folder on the host, hex-encoded (no newline)
- **`/workspaces/<folder-name>`**: The workspace folder path inside the container

### Verified Working Command

```bash
code --folder-uri "vscode-remote://dev-container+2f746d702f776f726b747265652d746573742f6d61696e/workspaces/main"
```

This was tested on macOS, VS Code 1.112.0 — launches VS Code and opens the devcontainer directly.

### Hex Encoding

The host path must be hex-encoded. Example:

| Host Path | Hex |
|-----------|-----|
| `/tmp/worktree-test/main` | `2f746d702f776f726b747265652d746573742f6d61696e` |

Python equivalent: `path.encode().hex()`

### VS Code CLI Binaries

| Edition | CLI Command | `--folder-uri` |
|---------|-------------|-----------------|
| Stable | `code` | ✅ Works (tested) |
| Insiders | `code-insiders` | ✅ Expected (same codebase) |

### For Insiders

Same command but `code-insiders` instead of `code`:
```bash
code-insiders --folder-uri "vscode-remote://..."
```

## workspaceFolder Resolution

The URI needs to know the workspace path **inside the container**.

### Default Behavior

When `workspaceFolder` is NOT set in `devcontainer.json`:
- Default is `/workspaces/<folder-name>` where `<folder-name>` is `basename` of the opened folder

### Custom Behavior

When `workspaceFolder` IS set in `devcontainer.json`:
```json
{ "workspaceFolder": "/workspace" }
```
The URI should use that path instead of `/workspaces/<folder-name>`.

### Parsing `devcontainer.json`

`devcontainer.json` is **JSONC** (JSON with Comments) — supports `//` and `/* */` comments plus trailing commas.

**Cannot use `json.loads()` directly.** Options:

| Library | Comments | Trailing Commas | Suitability |
|---------|----------|-----------------|-------------|
| `json5` | ✅ `//` and `/* */` | ✅ | Best — handles all JSONC features |
| `jsonc-parser` | ✅ | ✅ | Purpose-built for JSONC |
| `commentjson` | ✅ `//` only | ❌ | Partial |

**Recommendation:** Use `json5` — widely used, handles all VS Code JSONC patterns.

### devcontainer.json Locations

The file can be in two places:
1. `.devcontainer/devcontainer.json` (most common)
2. `.devcontainer.json` (root-level, less common)

## Cross-Platform Considerations

### macOS
- Paths are standard POSIX: `/Users/ross/repos/project`
- `code` CLI installed via VS Code shell command
- Hex encoding is straightforward

### WSL (Priority)
- When running inside WSL, paths are Linux-native: `/home/ross/repos/project`
- `code` CLI from within WSL already knows how to talk to the Windows-side VS Code
- The hex-encoded path should be the **WSL path** (not Windows path)
- Performance note: projects should live in WSL filesystem (`/home/...`), NOT `/mnt/c/...`

### Windows (native)
- Paths use backslashes: `C:\Users\ross\repos\project`
- `code` CLI available in PATH after VS Code install
- Hex encoding works the same — just encode the Windows path

### Path Detection

The script should detect the current platform:
```python
import platform
platform.system()  # 'Darwin', 'Linux', 'Windows'
```

For WSL detection (Linux but running under WSL):
```python
import os
is_wsl = os.path.exists('/proc/version') and 'microsoft' in open('/proc/version').read().lower()
```

## Implementation Approach: UV Inline Script

UV supports inline script metadata for dependencies:

```python
# /// script
# requires-python = ">=3.11"
# dependencies = ["json5"]
# ///
```

This allows a single `.py` file that can be run with `uv run dcode.py` — UV handles dependency installation automatically.

### Aliasing

User can alias in `.zshrc` / `.bashrc`:
```bash
alias dcode='uv run /path/to/dcode.py'
alias dcode-insiders='uv run /path/to/dcode.py --insiders'
```

Or install as a UV tool for global access.

## Constraints

1. **`--folder-uri` is undocumented** — it works but isn't in `code --help`. It's been stable for years and is used by the devcontainer extension itself.
2. **devcontainer.json is JSONC** — must use a JSONC-aware parser (json5).
3. **Folder must contain a devcontainer config** — script should error clearly if none found.
4. **VS Code must be installed** — `code` or `code-insiders` must be in PATH.
5. **No container needs to be running** — VS Code handles container lifecycle when opened via URI.

## Architecture Notes (from dcode repo)

- Repo is at `/Users/rossmiles/repos/rosstaco/dcode`
- Currently empty (just `.git`)
- User prefers UV for Python tooling
- No existing patterns to match — greenfield

## Open Questions

None — all resolved during research.
