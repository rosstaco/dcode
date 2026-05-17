"""Core dcode logic: locate devcontainers and launch VS Code."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import json5

from dcode._progress import with_spinner
from dcode.wsl import _ensure_wsl_docker_settings, build_uri_wsl, is_wsl


def _find_repo_root(start: Path) -> Path | None:
    """Walk up from *start* looking for a parent that contains a ``.git`` directory."""
    current = start.parent
    while current != current.parent:
        if (current / ".git").is_dir():
            return current
        current = current.parent
    return None


def _find_project_root(target: Path) -> Path | None:
    """Walk *target* and its ancestors for a ``.git`` (file or dir).

    Returns the directory containing the ``.git`` entry, or ``None`` if no
    such ancestor exists (target is outside any git repository / worktree).
    Used so ``dcode shell`` and ``dcode <path>`` work from a subdirectory
    of a repo or worktree, not only from the root.
    """
    current = target
    while True:
        git = current / ".git"
        if git.is_file() or git.is_dir():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


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


def resolve_target(target: Path) -> tuple[Path, Path]:
    """Resolve *target* to ``(project_root, container_subdir)``.

    ``project_root`` is the host directory that owns the devcontainer —
    for a plain repo it's the repo root; for a worktree it's the **main
    repo** so all worktrees share one container.

    ``container_subdir`` is the path **relative to** the devcontainer's
    ``workspaceFolder`` that ``target`` corresponds to inside the
    container, so callers can open / set the working directory there.
    Empty :class:`Path` (``Path('.')``) means *target* maps directly to
    the workspace folder root.

    When *target* is outside any git repo / worktree, returns
    ``(target, Path('.'))`` so the existing "no devcontainer" code path
    keeps working unchanged.
    """
    project_root = _find_project_root(target)
    if project_root is None:
        return (target, Path("."))

    # Worktree: project_root is the worktree dir, with a `.git` *file*
    # pointing back at <main_repo>/.git/worktrees/<name>. Anchor the
    # container at the main repo so worktrees share it.
    if (project_root / ".git").is_file():
        worktree = resolve_worktree(project_root)
        if worktree is not None:
            main_repo, wt_rel = worktree
            try:
                inside_wt = target.relative_to(project_root)
            except ValueError:  # pragma: no cover - defensive
                inside_wt = Path(".")
            container_subdir = (
                wt_rel if inside_wt == Path(".") else wt_rel / inside_wt
            )
            return (main_repo, container_subdir)
        # `.git` file that isn't a real worktree (submodule, malformed,
        # external worktree): fall back to treating project_root as the
        # anchor directly.

    # Plain repo (`.git` is a directory) or unresolvable worktree pointer.
    try:
        container_subdir = target.relative_to(project_root)
    except ValueError:  # pragma: no cover - defensive
        container_subdir = Path(".")
    return (project_root, container_subdir)


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


def _launch_editor(argv: list[str], *, label: str) -> int:
    """Run *argv* (the VS Code launch) under a spinner.

    Output is captured so it doesn't trample the spinner; on a non-zero exit
    we surface whatever the editor printed so the user sees the actual error.
    """
    with with_spinner(label):
        try:
            result = subprocess.run(
                argv,
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            print(f"dcode: failed to launch {argv[0]}: {exc}", file=sys.stderr)
            return 127

    if result.returncode:
        out = (result.stderr or "").strip() or (result.stdout or "").strip()
        if out:
            print(out, file=sys.stderr)
    return result.returncode


def run_dcode(path: str, *, insiders: bool = False) -> None:
    """Open a folder in VS Code, using devcontainer if available."""
    editor = "code-insiders" if insiders else "code"
    target = Path(path).resolve()

    project_root, container_subdir = resolve_target(target)
    devcontainer = find_devcontainer(project_root)

    if devcontainer is None:
        rc = _launch_editor([editor, str(target)], label=f"Launching {editor}...")
        if rc:
            sys.exit(rc)
        return

    host_path = str(project_root)
    base_folder = get_workspace_folder(devcontainer, project_root)
    if container_subdir == Path("."):
        workspace_folder = base_folder
    else:
        workspace_folder = f"{base_folder}/{container_subdir.as_posix()}"

    if is_wsl():
        uri = build_uri_wsl(host_path, workspace_folder)
        _ensure_wsl_docker_settings(insiders=insiders)
    else:
        uri = build_uri(host_path, workspace_folder)

    rc = _launch_editor(
        [editor, "--folder-uri", uri],
        label=f"Launching {editor} in devcontainer...",
    )
    if rc:
        sys.exit(rc)
