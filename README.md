# dcode

Open folders in VS Code devcontainers directly from the CLI.

Replace the two-step `code .` → "Reopen in Container" workflow with a single command.

## Install

```bash
uv tool install git+https://github.com/rosstaco/dcode
```

## Usage

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

## How it works

Constructs a `vscode-remote://dev-container+<hex-path>/workspaces/<name>` URI and launches VS Code with `--folder-uri`. VS Code handles the container lifecycle automatically.
