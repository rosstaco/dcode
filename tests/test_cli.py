"""Tests for dcode CLI."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from dcode.cli import build_uri, build_uri_wsl, find_devcontainer, get_workspace_folder


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
    def test_builds_json_payload_uri(self):
        uri = build_uri_wsl("/home/ross/repos/myapp", "/workspaces/myapp")
        assert "vscode-remote://dev-container+" in uri
        assert uri.endswith("/workspaces/myapp")
        # Decode the hex portion and verify it's JSON with hostPath
        hex_part = uri.split("dev-container+")[1].split("/workspaces/")[0]
        payload = json.loads(bytes.fromhex(hex_part).decode())
        assert payload == {"hostPath": "/home/ross/repos/myapp"}

    def test_wsl_uri_differs_from_plain(self):
        plain = build_uri("/home/ross/project", "/workspaces/project")
        wsl = build_uri_wsl("/home/ross/project", "/workspaces/project")
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

        with (
            patch("dcode.cli.subprocess.run") as mock_run,
            patch("dcode.cli.is_wsl", return_value=True),
            patch("dcode.cli._ensure_wsl_docker_settings"),
        ):
            from dcode.cli import run_dcode
            run_dcode(str(tmp_path), insiders=False)

        args = mock_run.call_args[0][0]
        assert args[0] == "code"
        assert args[1] == "--folder-uri"
        # WSL URI should contain a JSON payload with hostPath
        hex_part = args[2].split("dev-container+")[1].split("/workspaces/")[0]
        payload = json.loads(bytes.fromhex(hex_part).decode())
        assert "hostPath" in payload


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
