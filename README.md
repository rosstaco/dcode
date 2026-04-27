# dcode 🚀

Open folders in VS Code devcontainers directly from the CLI.

Replace the two-step `code .` → "Reopen in Container" workflow with a single command. ✨

## 📦 Install

```bash
uv tool install git+https://github.com/rosstaco/dcode
```

## 🔧 Usage

```bash
# Open current folder in devcontainer
dcode .

# Open a specific path
dcode /path/to/project

# Use VS Code Insiders
dcode --insiders .
dcode -i .
```

If the folder has no `.devcontainer/devcontainer.json`, falls back to plain `code .`.

## 🌳 Git worktrees

When you run `dcode .` inside a git worktree, it automatically detects the main repo, finds the devcontainer config there, and opens the worktree folder inside the same container. This means all worktrees share a single devcontainer instance — same extensions, same Copilot context, multiple VS Code windows. 🪟🪟🪟

```bash
cd ~/repos/my-project
git worktree add .worktrees/pr-42 pr-42

# Opens pr-42 in the devcontainer defined in my-project
dcode .worktrees/pr-42

# Opens pr-99 in the SAME container, different window
git worktree add .worktrees/pr-99 pr-99
dcode .worktrees/pr-99
```

> ⚠️ The worktree must live inside the main repo directory tree (e.g. `.worktrees/`) so it's accessible from the container's mounted volume.

## 🧠 How it works

Constructs a `vscode-remote://dev-container+<hex-path>/workspaces/<name>` URI and launches VS Code with `--folder-uri`. VS Code handles the container lifecycle automatically.

For worktrees, the hex-encoded path points to the main repo (so all worktrees resolve to the same container), while the workspace folder is adjusted to open the worktree subfolder inside the container.

## 🐧 WSL behavior

When `dcode` runs inside WSL, it:

1. Builds the URI using a Windows UNC path (`\\wsl.localhost\<distro>\…`) so VS Code on Windows can resolve the folder.
2. Auto-edits your **Windows** VS Code `settings.json` (under `%APPDATA%\Code\User\` or `Code - Insiders`) to set:
   - `"dev.containers.executeInWSL": true`
   - `"dev.containers.executeInWSLDistro": "<your-distro>"`

   This is required so the Dev Containers extension talks to Docker inside WSL instead of `docker.exe` on Windows. Comments and trailing commas in your `settings.json` are preserved (in-place patching, not a rewrite).

To opt out, pre-set those keys to whatever values you want — `dcode` only writes them when they're missing or differ from the desired values.

## 🤝 Contributing

This project uses [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `chore:`, `docs:`, etc.). Releases are automated by [release-please](https://github.com/googleapis/release-please) — merging a `feat:` or `fix:` commit to `main` opens/updates a release PR, and merging that PR creates the tag + GitHub Release.
