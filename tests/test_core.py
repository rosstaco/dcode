"""Tests for dcode.core."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from conftest import _make_worktree

from dcode.core import (
    build_uri,
    find_devcontainer,
    get_workspace_folder,
    resolve_worktree,
)


def _ok():
    """CompletedProcess representing successful editor invocation."""
    return subprocess.CompletedProcess(args=[], returncode=0)


class TestBuildUri:
    def test_builds_correct_uri(self):
        uri = build_uri("/home/ross/repos/myapp", "/workspaces/myapp")
        assert uri == (
            "vscode-remote://dev-container+"
            "2f686f6d652f726f73732f7265706f732f6d79617070"
            "/workspaces/myapp"
        )

    def test_hex_encodes_path(self):
        uri = build_uri("/tmp/worktree-test/main", "/workspaces/main")
        assert "2f746d702f776f726b747265652d746573742f6d61696e" in uri

    def test_custom_workspace_folder(self):
        uri = build_uri("/some/path", "/workspace")
        assert uri.endswith("/workspace")


class TestFindDevcontainer:
    def test_finds_in_devcontainer_dir(self, tmp_path):
        dc_dir = tmp_path / ".devcontainer"
        dc_dir.mkdir()
        dc_file = dc_dir / "devcontainer.json"
        dc_file.write_text('{"name": "test"}')

        assert find_devcontainer(tmp_path) == dc_file

    def test_finds_root_level(self, tmp_path):
        dc_file = tmp_path / ".devcontainer.json"
        dc_file.write_text('{"name": "test"}')

        assert find_devcontainer(tmp_path) == dc_file

    def test_prefers_devcontainer_dir_over_root(self, tmp_path):
        # Both exist — .devcontainer/ should win
        dc_dir = tmp_path / ".devcontainer"
        dc_dir.mkdir()
        dir_file = dc_dir / "devcontainer.json"
        dir_file.write_text('{"name": "dir"}')

        root_file = tmp_path / ".devcontainer.json"
        root_file.write_text('{"name": "root"}')

        assert find_devcontainer(tmp_path) == dir_file

    def test_returns_none_when_missing(self, tmp_path):
        assert find_devcontainer(tmp_path) is None


class TestGetWorkspaceFolder:
    def test_reads_custom_workspace_folder(self, tmp_path):
        dc_file = tmp_path / "devcontainer.json"
        dc_file.write_text('{"workspaceFolder": "/workspace"}')

        result = get_workspace_folder(dc_file, Path("/home/ross/project"))
        assert result == "/workspace"

    def test_defaults_when_not_set(self, tmp_path):
        dc_file = tmp_path / "devcontainer.json"
        dc_file.write_text('{"name": "test"}')

        result = get_workspace_folder(dc_file, Path("/home/ross/project"))
        assert result == "/workspaces/project"

    def test_handles_jsonc_comments_and_trailing_commas(self, tmp_path):
        dc_file = tmp_path / "devcontainer.json"
        dc_file.write_text(
            '// This is a comment\n'
            '{\n'
            '  "name": "test",\n'
            '  "workspaceFolder": "/custom",\n'
            '}\n'
        )

        result = get_workspace_folder(dc_file, Path("/any"))
        assert result == "/custom"

    def test_falls_back_on_malformed_devcontainer_json(self, tmp_path, capsys):
        dc_file = tmp_path / "devcontainer.json"
        dc_file.write_text("{ this is not json")

        result = get_workspace_folder(dc_file, Path("/x/myapp"))
        assert result == "/workspaces/myapp"
        assert "failed to parse" in capsys.readouterr().err


class TestMain:
    def test_launches_with_devcontainer_uri(self, tmp_path):
        dc_dir = tmp_path / ".devcontainer"
        dc_dir.mkdir()
        (dc_dir / "devcontainer.json").write_text('{"name": "test"}')

        with patch("dcode.core.subprocess.run", return_value=_ok()) as mock_run:
            from dcode.core import run_dcode
            run_dcode(str(tmp_path), insiders=False)

        args = mock_run.call_args[0][0]
        assert args[0] == "code"
        assert args[1] == "--folder-uri"
        assert "vscode-remote://dev-container+" in args[2]

    def test_launches_insiders(self, tmp_path):
        dc_dir = tmp_path / ".devcontainer"
        dc_dir.mkdir()
        (dc_dir / "devcontainer.json").write_text('{"name": "test"}')

        with patch("dcode.core.subprocess.run", return_value=_ok()) as mock_run:
            from dcode.core import run_dcode
            run_dcode(str(tmp_path), insiders=True)

        args = mock_run.call_args[0][0]
        assert args[0] == "code-insiders"

    def test_fallback_without_devcontainer(self, tmp_path):
        with patch("dcode.core.subprocess.run", return_value=_ok()) as mock_run:
            from dcode.core import run_dcode
            run_dcode(str(tmp_path), insiders=False)

        args = mock_run.call_args[0][0]
        assert args == ["code", str(tmp_path)]

    def test_propagates_nonzero_exit_from_code_launcher(self, tmp_path):
        failed = subprocess.CompletedProcess(args=[], returncode=2)
        with patch("dcode.core.subprocess.run", return_value=failed):
            from dcode.core import run_dcode
            with pytest.raises(SystemExit) as exc:
                run_dcode(str(tmp_path), insiders=False)
        assert exc.value.code == 2

    def test_propagates_nonzero_exit_with_devcontainer(self, tmp_path):
        dc_dir = tmp_path / ".devcontainer"
        dc_dir.mkdir()
        (dc_dir / "devcontainer.json").write_text('{"name": "test"}')

        failed = subprocess.CompletedProcess(args=[], returncode=3)
        with patch("dcode.core.subprocess.run", return_value=failed):
            from dcode.core import run_dcode
            with pytest.raises(SystemExit) as exc:
                run_dcode(str(tmp_path), insiders=False)
        assert exc.value.code == 3

    def test_uses_wsl_uri_on_wsl(self, tmp_path):
        dc_dir = tmp_path / ".devcontainer"
        dc_dir.mkdir()
        (dc_dir / "devcontainer.json").write_text('{"name": "test"}')

        unc = f"\\\\wsl.localhost\\Ubuntu{tmp_path}"
        with (
            patch("dcode.core.subprocess.run", return_value=_ok()) as mock_run,
            patch("dcode.core.is_wsl", return_value=True),
            patch("dcode.core._ensure_wsl_docker_settings"),
            patch("dcode.wsl._wsl_to_windows_path", return_value=unc),
        ):
            from dcode.core import run_dcode
            run_dcode(str(tmp_path), insiders=False)

        args = mock_run.call_args[0][0]
        assert args[0] == "code"
        assert args[1] == "--folder-uri"
        # WSL URI should contain a JSON payload with Windows UNC hostPath
        hex_part = args[2].split("dev-container+")[1].split("/workspaces/")[0]
        payload = json.loads(bytes.fromhex(hex_part).decode())
        assert payload["hostPath"] == unc


class TestResolveWorktree:
    def test_returns_none_for_normal_repo(self, tmp_path):
        (tmp_path / ".git").mkdir()
        assert resolve_worktree(tmp_path) is None

    def test_returns_none_when_no_git(self, tmp_path):
        assert resolve_worktree(tmp_path) is None

    def test_resolves_worktree_with_relative_gitdir(self, tmp_path):
        main_repo, worktree = _make_worktree(tmp_path)

        result = resolve_worktree(worktree)
        assert result is not None
        main, rel = result
        assert main == main_repo
        assert rel == Path(".worktrees/pr-34")

    def test_resolves_worktree_with_absolute_gitdir(self, tmp_path):
        main_repo = tmp_path / "main-repo"
        main_repo.mkdir()
        git_dir = main_repo / ".git"
        git_dir.mkdir()
        wt_meta = git_dir / "worktrees" / "feature"
        wt_meta.mkdir(parents=True)

        worktree = main_repo / ".worktrees" / "feature"
        worktree.mkdir(parents=True)
        (worktree / ".git").write_text(f"gitdir: {wt_meta}\n")

        result = resolve_worktree(worktree)
        assert result is not None
        main, rel = result
        assert main == main_repo
        assert rel == Path(".worktrees/feature")

    def test_returns_none_for_submodule(self, tmp_path):
        main_repo = tmp_path / "main-repo"
        main_repo.mkdir()
        (main_repo / ".git").mkdir()
        (main_repo / ".git" / "modules" / "sub").mkdir(parents=True)

        submodule = main_repo / "sub"
        submodule.mkdir()
        (submodule / ".git").write_text("gitdir: ../.git/modules/sub\n")

        assert resolve_worktree(submodule) is None

    def test_returns_none_for_external_worktree(self, tmp_path):
        main_repo = tmp_path / "main-repo"
        main_repo.mkdir()
        (main_repo / ".git").mkdir()
        (main_repo / ".git" / "worktrees" / "ext").mkdir(parents=True)

        external = tmp_path / "elsewhere" / "ext"
        external.mkdir(parents=True)
        (external / ".git").write_text(f"gitdir: {main_repo / '.git' / 'worktrees' / 'ext'}\n")

        assert resolve_worktree(external) is None

    def test_returns_none_for_malformed_git_file(self, tmp_path):
        (tmp_path / ".git").write_text("not a valid gitdir line\n")
        assert resolve_worktree(tmp_path) is None

    def test_resolves_worktree_with_foreign_absolute_gitdir(self, tmp_path):
        """Worktree created inside a devcontainer has a container-absolute path."""
        main_repo, worktree = _make_worktree(tmp_path)
        # Overwrite with a path that doesn't exist on the host (container path)
        (worktree / ".git").write_text(
            "gitdir: /workspaces/myapp/.git/worktrees/pr-34\n"
        )

        result = resolve_worktree(worktree)
        assert result is not None
        main, rel = result
        assert main == main_repo
        assert rel == Path(".worktrees/pr-34")


class TestRunDcodeWorktree:
    def test_worktree_uses_main_repo_host_path(self, tmp_path):
        main_repo, worktree = _make_worktree(tmp_path)
        dc_dir = main_repo / ".devcontainer"
        dc_dir.mkdir()
        (dc_dir / "devcontainer.json").write_text('{"name": "test"}')

        with patch("dcode.core.subprocess.run", return_value=_ok()) as mock_run:
            from dcode.core import run_dcode
            run_dcode(str(worktree))

        args = mock_run.call_args[0][0]
        assert args[1] == "--folder-uri"
        uri = args[2]
        hex_part = uri.split("dev-container+")[1].split("/workspaces/")[0]
        decoded_path = bytes.fromhex(hex_part).decode()
        assert decoded_path == str(main_repo)

    def test_worktree_workspace_folder_includes_relative_path(self, tmp_path):
        main_repo, worktree = _make_worktree(tmp_path)
        dc_dir = main_repo / ".devcontainer"
        dc_dir.mkdir()
        (dc_dir / "devcontainer.json").write_text('{"name": "test"}')

        with patch("dcode.core.subprocess.run", return_value=_ok()) as mock_run:
            from dcode.core import run_dcode
            run_dcode(str(worktree))

        uri = mock_run.call_args[0][0][2]
        assert uri.endswith("/workspaces/main-repo/.worktrees/pr-34")

    def test_multiple_worktrees_share_same_container(self, tmp_path):
        main_repo = tmp_path / "main-repo"
        main_repo.mkdir()
        (main_repo / ".git").mkdir()
        dc_dir = main_repo / ".devcontainer"
        dc_dir.mkdir()
        (dc_dir / "devcontainer.json").write_text('{"name": "test"}')

        uris = []
        for name in ["pr-1", "pr-2"]:
            (main_repo / ".git" / "worktrees" / name).mkdir(parents=True)
            wt = main_repo / ".worktrees" / name
            wt.mkdir(parents=True)
            (wt / ".git").write_text(f"gitdir: ../../.git/worktrees/{name}\n")

            with patch("dcode.core.subprocess.run", return_value=_ok()) as mock_run:
                from dcode.core import run_dcode
                run_dcode(str(wt))
            uris.append(mock_run.call_args[0][0][2])

        # Same hex prefix = same container
        hex1 = uris[0].split("dev-container+")[1].split("/workspaces/")[0]
        hex2 = uris[1].split("dev-container+")[1].split("/workspaces/")[0]
        assert hex1 == hex2

        # Different workspace folders
        assert uris[0].endswith("/.worktrees/pr-1")
        assert uris[1].endswith("/.worktrees/pr-2")

    def test_worktree_falls_back_when_no_devcontainer_in_main_repo(self, tmp_path):
        main_repo, worktree = _make_worktree(tmp_path)

        with patch("dcode.core.subprocess.run", return_value=_ok()) as mock_run:
            from dcode.core import run_dcode
            run_dcode(str(worktree))

        args = mock_run.call_args[0][0]
        assert args == ["code", str(worktree)]

    def test_worktree_with_custom_workspace_folder(self, tmp_path):
        main_repo, worktree = _make_worktree(tmp_path)
        dc_dir = main_repo / ".devcontainer"
        dc_dir.mkdir()
        (dc_dir / "devcontainer.json").write_text('{"workspaceFolder": "/workspace"}')

        with patch("dcode.core.subprocess.run", return_value=_ok()) as mock_run:
            from dcode.core import run_dcode
            run_dcode(str(worktree))

        uri = mock_run.call_args[0][0][2]
        assert uri.endswith("/workspace/.worktrees/pr-34")
