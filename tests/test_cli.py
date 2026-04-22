"""Tests for dcode CLI."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from dcode.cli import build_uri, build_uri_wsl, find_devcontainer, get_workspace_folder, resolve_worktree


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


class TestBuildUriWsl:
    def test_builds_json_payload_with_windows_path(self):
        with patch("dcode.cli._wsl_to_windows_path", return_value="\\\\wsl.localhost\\Ubuntu\\home\\ross\\repos\\myapp"):
            uri = build_uri_wsl("/home/ross/repos/myapp", "/workspaces/myapp")
        assert "vscode-remote://dev-container+" in uri
        assert uri.endswith("/workspaces/myapp")
        hex_part = uri.split("dev-container+")[1].split("/workspaces/")[0]
        payload = json.loads(bytes.fromhex(hex_part).decode())
        assert payload == {"hostPath": "\\\\wsl.localhost\\Ubuntu\\home\\ross\\repos\\myapp"}

    def test_wsl_uri_differs_from_plain(self):
        with patch("dcode.cli._wsl_to_windows_path", return_value="\\\\wsl.localhost\\Ubuntu\\home\\ross\\project"):
            wsl = build_uri_wsl("/home/ross/project", "/workspaces/project")
        plain = build_uri("/home/ross/project", "/workspaces/project")
        assert plain != wsl


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


class TestMain:
    def test_launches_with_devcontainer_uri(self, tmp_path):
        dc_dir = tmp_path / ".devcontainer"
        dc_dir.mkdir()
        (dc_dir / "devcontainer.json").write_text('{"name": "test"}')

        with patch("dcode.cli.subprocess.run") as mock_run:
            from dcode.cli import run_dcode
            run_dcode(str(tmp_path), insiders=False)

        args = mock_run.call_args[0][0]
        assert args[0] == "code"
        assert args[1] == "--folder-uri"
        assert "vscode-remote://dev-container+" in args[2]

    def test_launches_insiders(self, tmp_path):
        dc_dir = tmp_path / ".devcontainer"
        dc_dir.mkdir()
        (dc_dir / "devcontainer.json").write_text('{"name": "test"}')

        with patch("dcode.cli.subprocess.run") as mock_run:
            from dcode.cli import run_dcode
            run_dcode(str(tmp_path), insiders=True)

        args = mock_run.call_args[0][0]
        assert args[0] == "code-insiders"

    def test_fallback_without_devcontainer(self, tmp_path):
        with patch("dcode.cli.subprocess.run") as mock_run:
            from dcode.cli import run_dcode
            run_dcode(str(tmp_path), insiders=False)

        args = mock_run.call_args[0][0]
        assert args == ["code", str(tmp_path)]

    def test_uses_wsl_uri_on_wsl(self, tmp_path):
        dc_dir = tmp_path / ".devcontainer"
        dc_dir.mkdir()
        (dc_dir / "devcontainer.json").write_text('{"name": "test"}')

        unc = f"\\\\wsl.localhost\\Ubuntu{tmp_path}"
        with (
            patch("dcode.cli.subprocess.run") as mock_run,
            patch("dcode.cli.is_wsl", return_value=True),
            patch("dcode.cli._ensure_wsl_docker_settings"),
            patch("dcode.cli._wsl_to_windows_path", return_value=unc),
        ):
            from dcode.cli import run_dcode
            run_dcode(str(tmp_path), insiders=False)

        args = mock_run.call_args[0][0]
        assert args[0] == "code"
        assert args[1] == "--folder-uri"
        # WSL URI should contain a JSON payload with Windows UNC hostPath
        hex_part = args[2].split("dev-container+")[1].split("/workspaces/")[0]
        payload = json.loads(bytes.fromhex(hex_part).decode())
        assert payload["hostPath"] == unc


class TestEnsureWslDockerSettings:
    def test_patches_settings_when_not_configured(self, tmp_path):
        settings_file = tmp_path / "Code" / "User" / "settings.json"
        settings_file.parent.mkdir(parents=True)
        settings_file.write_text('{"editor.fontSize": 14}')

        with (
            patch("dcode.cli._get_windows_vscode_settings_path", return_value=settings_file),
            patch("dcode.cli.get_wsl_distro", return_value="Ubuntu"),
        ):
            from dcode.cli import _ensure_wsl_docker_settings
            _ensure_wsl_docker_settings()

        result = json.loads(settings_file.read_text())
        assert result["dev.containers.executeInWSL"] is True
        assert result["dev.containers.executeInWSLDistro"] == "Ubuntu"
        assert result["editor.fontSize"] == 14  # preserved

    def test_skips_when_already_configured(self, tmp_path):
        settings_file = tmp_path / "Code" / "User" / "settings.json"
        settings_file.parent.mkdir(parents=True)
        original = '{"dev.containers.executeInWSL": true, "other": 1}'
        settings_file.write_text(original)

        with (
            patch("dcode.cli._get_windows_vscode_settings_path", return_value=settings_file),
            patch("dcode.cli.get_wsl_distro", return_value="Ubuntu"),
        ):
            from dcode.cli import _ensure_wsl_docker_settings
            _ensure_wsl_docker_settings()

        # File should not be rewritten (no distro added since executeInWSL already set)
        result = json.loads(settings_file.read_text())
        assert result["dev.containers.executeInWSL"] is True

    def test_falls_back_to_hint_when_path_not_found(self, capsys):
        with patch("dcode.cli._get_windows_vscode_settings_path", return_value=None):
            from dcode.cli import _ensure_wsl_docker_settings
            _ensure_wsl_docker_settings()

        assert "dev.containers.executeInWSL" in capsys.readouterr().err


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


class TestRunDcodeWorktree:
    def test_worktree_uses_main_repo_host_path(self, tmp_path):
        main_repo, worktree = _make_worktree(tmp_path)
        dc_dir = main_repo / ".devcontainer"
        dc_dir.mkdir()
        (dc_dir / "devcontainer.json").write_text('{"name": "test"}')

        with patch("dcode.cli.subprocess.run") as mock_run:
            from dcode.cli import run_dcode
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

        with patch("dcode.cli.subprocess.run") as mock_run:
            from dcode.cli import run_dcode
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

            with patch("dcode.cli.subprocess.run") as mock_run:
                from dcode.cli import run_dcode
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

        with patch("dcode.cli.subprocess.run") as mock_run:
            from dcode.cli import run_dcode
            run_dcode(str(worktree))

        args = mock_run.call_args[0][0]
        assert args == ["code", str(worktree)]

    def test_worktree_with_custom_workspace_folder(self, tmp_path):
        main_repo, worktree = _make_worktree(tmp_path)
        dc_dir = main_repo / ".devcontainer"
        dc_dir.mkdir()
        (dc_dir / "devcontainer.json").write_text('{"workspaceFolder": "/workspace"}')

        with patch("dcode.cli.subprocess.run") as mock_run:
            from dcode.cli import run_dcode
            run_dcode(str(worktree))

        uri = mock_run.call_args[0][0][2]
        assert uri.endswith("/workspace/.worktrees/pr-34")
