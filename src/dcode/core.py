"""Core dcode logic: locate devcontainers and launch VS Code."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import json5

from dcode.wsl import _ensure_wsl_docker_settings, build_uri_wsl, is_wsl


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
    default = f"/workspaces/{target.name}"
    try:
        with devcontainer_path.open() as f:
            config = json5.load(f)
    except (OSError, ValueError) as exc:
        print(
            f"dcode: failed to parse {devcontainer_path} ({exc}); "
            f"using default workspace folder {default}",
            file=sys.stderr,
        )
        return default
    if not isinstance(config, dict):
        return default
    return config.get("workspaceFolder", default)


def build_uri(host_path: str, workspace_folder: str) -> str:
    """Build the vscode-remote devcontainer URI (plain path, hex-encoded)."""
    hex_path = host_path.encode().hex()
    return f"vscode-remote://dev-container+{hex_path}{workspace_folder}"


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
        result = subprocess.run([editor, str(target)], check=False)
        if result.returncode:
            sys.exit(result.returncode)
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

    result = subprocess.run([editor, "--folder-uri", uri], check=False)
    if result.returncode:
        sys.exit(result.returncode)
