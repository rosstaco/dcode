"""Tests for dcode CLI."""

from pathlib import Path
from unittest.mock import patch

import pytest

from dcode.cli import build_uri, find_devcontainer, get_workspace_folder


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
