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
