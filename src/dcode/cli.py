"""dcode — open folders in VS Code devcontainers from the CLI."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import json5


def find_devcontainer(target: Path) -> Path | None:
    """Find devcontainer.json in the target directory."""
    candidates = [
        target / ".devcontainer" / "devcontainer.json",
        target / ".devcontainer.json",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def get_workspace_folder(devcontainer_path: Path, target: Path) -> str:
    """Read workspaceFolder from devcontainer.json, or use the default."""
    with devcontainer_path.open() as f:
        config = json5.load(f)
    return config.get("workspaceFolder", f"/workspaces/{target.name}")


def build_uri(host_path: str, workspace_folder: str) -> str:
    """Build the vscode-remote devcontainer URI."""
    hex_path = host_path.encode().hex()
    return f"vscode-remote://dev-container+{hex_path}{workspace_folder}"


def run_dcode(path: str, *, insiders: bool = False) -> None:
    """Open a folder in VS Code, using devcontainer if available."""
    editor = "code-insiders" if insiders else "code"
    target = Path(path).resolve()

    devcontainer = find_devcontainer(target)
    if devcontainer is None:
        subprocess.run([editor, str(target)])
        return

    workspace_folder = get_workspace_folder(devcontainer, target)
    uri = build_uri(str(target), workspace_folder)
    subprocess.run([editor, "--folder-uri", uri])


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="dcode",
        description="Open a folder in a VS Code devcontainer.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="folder to open (default: current directory)",
    )
    parser.add_argument(
        "-i",
        "--insiders",
        action="store_true",
        help="use VS Code Insiders",
    )
    args = parser.parse_args()
    run_dcode(args.path, insiders=args.insiders)
