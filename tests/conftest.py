"""Shared test helpers.

Houses the `_make_worktree` helper extracted from `tests/test_cli.py`.

It is kept as a plain top-level function (not a pytest fixture) to minimise
churn at the existing call sites, which invoke it as
``_make_worktree(tmp_path)``. Test modules import it explicitly via
``from conftest import _make_worktree`` (pytest puts the ``tests/`` directory
on ``sys.path`` because there is no ``__init__.py``).
"""

from pathlib import Path


def _make_worktree(tmp_path: Path, name: str = "pr-34") -> tuple[Path, Path]:
    """Create a fake main-repo + worktree layout and return (main_repo, worktree)."""
    main_repo = tmp_path / "main-repo"
    main_repo.mkdir()
    (main_repo / ".git").mkdir()
    (main_repo / ".git" / "worktrees" / name).mkdir(parents=True)

    worktree = main_repo / ".worktrees" / name
    worktree.mkdir(parents=True)
    (worktree / ".git").write_text(f"gitdir: ../../.git/worktrees/{name}\n")
    return main_repo, worktree
