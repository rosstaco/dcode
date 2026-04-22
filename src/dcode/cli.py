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


def _find_repo_root(start: Path) -> Path | None:
    """Walk up from *start* looking for a parent that contains a ``.git`` directory."""
    current = start.parent
    while current != current.parent:
        if (current / ".git").is_dir():
            return current
        current = current.parent
    return None


def resolve_worktree(target: Path) -> tuple[Path, Path] | None:
    """If *target* is a git worktree root, return ``(main_repo, rel_path)``.

    *main_repo* is the root of the repository that owns the worktree, and
    *rel_path* is the worktree's location relative to *main_repo*.

    Returns ``None`` when *target* is not a worktree, is a submodule, or
    the worktree lives outside the main repository tree.
    """
    git_file = target / ".git"
    if not git_file.is_file():
        return None

    try:
        content = git_file.read_text().strip()
    except OSError:
        return None

    if not content.startswith("gitdir:"):
        return None

    gitdir_ref = content.split("gitdir:", 1)[1].strip()
    gitdir = (target / gitdir_ref).resolve()

    # Worktrees point to <repo>/.git/worktrees/<name>.
    # Submodules point to <repo>/.git/modules/<name> — reject those.
    if gitdir.parent.name != "worktrees" or gitdir.parent.parent.name != ".git":
        return None

    main_repo = gitdir.parent.parent.parent  # <repo>/.git/worktrees/<n> → <repo>

    # The gitdir may contain an absolute path from a different environment
    # (e.g. a container path when running on the host).  Fall back to
    # walking the filesystem when the derived main_repo doesn't exist.
    if not main_repo.is_dir():
        main_repo = _find_repo_root(target)
        if main_repo is None:
            return None

    try:
        rel_path = target.relative_to(main_repo)
    except ValueError:
        print(
            "dcode: worktree is outside the main repo tree — "
            "shared-container mode is not supported for external worktrees",
            file=sys.stderr,
        )
        return None

    return (main_repo, rel_path)


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


def _wsl_to_windows_path(linux_path: str) -> str:
    """Convert a WSL Linux path to a Windows UNC path."""
    try:
        result = subprocess.run(
            ["wslpath", "-w", linux_path],
            capture_output=True, text=True, timeout=5,
        )
        win_path = result.stdout.strip()
        if win_path:
            return win_path
    except (OSError, subprocess.TimeoutExpired):
        pass
    # Fallback: construct UNC path manually
    distro = get_wsl_distro() or "Ubuntu"
    return f"\\\\wsl.localhost\\{distro}{linux_path}"


def build_uri_wsl(host_path: str, workspace_folder: str) -> str:
    """Build the URI with Windows UNC path so VS Code finds the WSL folder."""
    win_path = _wsl_to_windows_path(host_path)
    payload = json.dumps({"hostPath": win_path}, separators=(",", ":"))
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

    # For worktrees, resolve the main repo so all worktrees share one container.
    worktree = resolve_worktree(target)
    if worktree is not None:
        main_repo, rel_path = worktree
        devcontainer = find_devcontainer(main_repo)
    else:
        main_repo = None
        rel_path = None
        devcontainer = find_devcontainer(target)

    if devcontainer is None:
        subprocess.run([editor, str(target)])
        return

    if main_repo is not None:
        host_path = str(main_repo)
        base_folder = get_workspace_folder(devcontainer, main_repo)
        workspace_folder = f"{base_folder}/{rel_path.as_posix()}"
    else:
        host_path = str(target)
        workspace_folder = get_workspace_folder(devcontainer, target)

    if is_wsl():
        uri = build_uri_wsl(host_path, workspace_folder)
        _ensure_wsl_docker_settings(insiders=insiders)
    else:
        uri = build_uri(host_path, workspace_folder)

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
