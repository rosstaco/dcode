"""dcode — open folders in VS Code devcontainers from the CLI."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import json5


def is_wsl() -> bool:
    """Detect if running inside WSL."""
    try:
        return os.path.exists("/proc/version") and "microsoft" in Path(
            "/proc/version"
        ).read_text().lower()
    except OSError:
        return False


def get_wsl_distro() -> str | None:
    """Get the current WSL distro name."""
    return os.environ.get("WSL_DISTRO_NAME")


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
    """Build the vscode-remote devcontainer URI (plain path, hex-encoded)."""
    hex_path = host_path.encode().hex()
    return f"vscode-remote://dev-container+{hex_path}{workspace_folder}"


def build_uri_wsl(host_path: str, workspace_folder: str) -> str:
    """Build the URI with a JSON object so VS Code knows about the host context."""
    payload = json.dumps({"hostPath": host_path}, separators=(",", ":"))
    hex_payload = payload.encode().hex()
    return f"vscode-remote://dev-container+{hex_payload}{workspace_folder}"


def _get_windows_vscode_settings_path(insiders: bool = False) -> Path | None:
    """Find the Windows-side VS Code settings.json from WSL."""
    try:
        result = subprocess.run(
            ["cmd.exe", "/C", "echo", "%APPDATA%"],
            capture_output=True, text=True, timeout=5,
        )
        appdata_win = result.stdout.strip()
        if not appdata_win or "%" in appdata_win:
            return None
        # Convert Windows path to WSL path
        result = subprocess.run(
            ["wslpath", "-u", appdata_win],
            capture_output=True, text=True, timeout=5,
        )
        appdata_wsl = result.stdout.strip()
        if not appdata_wsl:
            return None
        code_dir = "Code - Insiders" if insiders else "Code"
        return Path(appdata_wsl) / code_dir / "User" / "settings.json"
    except (OSError, subprocess.TimeoutExpired):
        return None


def _ensure_wsl_docker_settings(insiders: bool = False) -> None:
    """Auto-configure VS Code to use Docker from WSL if not already set."""
    settings_path = _get_windows_vscode_settings_path(insiders)
    if settings_path is None:
        _print_wsl_hint()
        return

    distro = get_wsl_distro()

    # Read existing settings (or start fresh)
    settings: dict = {}
    if settings_path.is_file():
        try:
            settings = json5.loads(settings_path.read_text())
        except Exception:
            _print_wsl_hint()
            return

    # Check if already configured
    if settings.get("dev.containers.executeInWSL") is True:
        return

    # Patch the settings
    settings["dev.containers.executeInWSL"] = True
    if distro:
        settings["dev.containers.executeInWSLDistro"] = distro

    try:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps(settings, indent=4) + "\n")
        print(
            f"dcode: configured VS Code to use Docker from WSL"
            + (f" ({distro})" if distro else ""),
            file=sys.stderr,
        )
    except OSError:
        _print_wsl_hint()


def _print_wsl_hint() -> None:
    """Print manual instructions as fallback."""
    print(
        "hint: if VS Code uses docker.exe instead of WSL docker, enable these VS Code settings:\n"
        '  "dev.containers.executeInWSL": true\n'
        '  "dev.containers.executeInWSLDistro": "<your-distro>"',
        file=sys.stderr,
    )


def run_dcode(path: str, *, insiders: bool = False) -> None:
    """Open a folder in VS Code, using devcontainer if available."""
    editor = "code-insiders" if insiders else "code"
    target = Path(path).resolve()

    devcontainer = find_devcontainer(target)
    if devcontainer is None:
        subprocess.run([editor, str(target)])
        return

    workspace_folder = get_workspace_folder(devcontainer, target)

    if is_wsl():
        uri = build_uri_wsl(str(target), workspace_folder)
        _ensure_wsl_docker_settings(insiders=insiders)
    else:
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
