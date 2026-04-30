"""Tests for dcode.wsl."""

import json
from unittest.mock import patch

from dcode.core import build_uri
from dcode.wsl import build_uri_wsl


class TestBuildUriWsl:
    def test_builds_json_payload_with_windows_path(self):
        with patch("dcode.wsl._wsl_to_windows_path", return_value="\\\\wsl.localhost\\Ubuntu\\home\\ross\\repos\\myapp"):
            uri = build_uri_wsl("/home/ross/repos/myapp", "/workspaces/myapp")
        assert "vscode-remote://dev-container+" in uri
        assert uri.endswith("/workspaces/myapp")
        hex_part = uri.split("dev-container+")[1].split("/workspaces/")[0]
        payload = json.loads(bytes.fromhex(hex_part).decode())
        assert payload == {"hostPath": "\\\\wsl.localhost\\Ubuntu\\home\\ross\\repos\\myapp"}

    def test_wsl_uri_differs_from_plain(self):
        with patch("dcode.wsl._wsl_to_windows_path", return_value="\\\\wsl.localhost\\Ubuntu\\home\\ross\\project"):
            wsl = build_uri_wsl("/home/ross/project", "/workspaces/project")
        plain = build_uri("/home/ross/project", "/workspaces/project")
        assert plain != wsl


class TestEnsureWslDockerSettings:
    def test_patches_settings_when_not_configured(self, tmp_path):
        settings_file = tmp_path / "Code" / "User" / "settings.json"
        settings_file.parent.mkdir(parents=True)
        settings_file.write_text('{"editor.fontSize": 14}')

        with (
            patch("dcode.wsl._get_windows_vscode_settings_path", return_value=settings_file),
            patch("dcode.wsl.get_wsl_distro", return_value="Ubuntu"),
        ):
            from dcode.wsl import _ensure_wsl_docker_settings
            _ensure_wsl_docker_settings()

        result = json.loads(settings_file.read_text())
        assert result["dev.containers.executeInWSL"] is True
        assert result["dev.containers.executeInWSLDistro"] == "Ubuntu"
        assert result["editor.fontSize"] == 14  # preserved

    def test_adds_missing_distro_when_executeInWSL_already_true(self, tmp_path):
        settings_file = tmp_path / "Code" / "User" / "settings.json"
        settings_file.parent.mkdir(parents=True)
        settings_file.write_text('{"dev.containers.executeInWSL": true, "other": 1}')

        with (
            patch("dcode.wsl._get_windows_vscode_settings_path", return_value=settings_file),
            patch("dcode.wsl.get_wsl_distro", return_value="Ubuntu"),
        ):
            from dcode.wsl import _ensure_wsl_docker_settings
            _ensure_wsl_docker_settings()

        result = json.loads(settings_file.read_text())
        assert result["dev.containers.executeInWSL"] is True
        assert result["dev.containers.executeInWSLDistro"] == "Ubuntu"
        assert result["other"] == 1

    def test_does_nothing_when_both_keys_already_correct(self, tmp_path):
        settings_file = tmp_path / "Code" / "User" / "settings.json"
        settings_file.parent.mkdir(parents=True)
        original = (
            '{"dev.containers.executeInWSL": true, '
            '"dev.containers.executeInWSLDistro": "Ubuntu"}'
        )
        settings_file.write_text(original)

        with (
            patch("dcode.wsl._get_windows_vscode_settings_path", return_value=settings_file),
            patch("dcode.wsl.get_wsl_distro", return_value="Ubuntu"),
        ):
            from dcode.wsl import _ensure_wsl_docker_settings
            _ensure_wsl_docker_settings()

        # File content byte-identical — no rewrite happened.
        assert settings_file.read_text() == original

    def test_preserves_jsonc_comments_and_trailing_commas(self, tmp_path):
        settings_file = tmp_path / "Code" / "User" / "settings.json"
        settings_file.parent.mkdir(parents=True)
        original = (
            '// keep me\n'
            '{\n'
            '  "editor.fontSize": 14,\n'
            '}\n'
        )
        settings_file.write_text(original)

        with (
            patch("dcode.wsl._get_windows_vscode_settings_path", return_value=settings_file),
            patch("dcode.wsl.get_wsl_distro", return_value="Ubuntu"),
        ):
            from dcode.wsl import _ensure_wsl_docker_settings
            _ensure_wsl_docker_settings()

        new_text = settings_file.read_text()
        # Original comment + trailing comma preserved.
        assert "// keep me" in new_text
        assert '"editor.fontSize": 14' in new_text
        # New keys present.
        assert '"dev.containers.executeInWSL": true' in new_text
        assert '"dev.containers.executeInWSLDistro": "Ubuntu"' in new_text

    def test_falls_back_to_hint_on_unpatchable_file(self, tmp_path, capsys):
        settings_file = tmp_path / "Code" / "User" / "settings.json"
        settings_file.parent.mkdir(parents=True)
        original = "[]"
        settings_file.write_text(original)

        with (
            patch("dcode.wsl._get_windows_vscode_settings_path", return_value=settings_file),
            patch("dcode.wsl.get_wsl_distro", return_value="Ubuntu"),
        ):
            from dcode.wsl import _ensure_wsl_docker_settings
            _ensure_wsl_docker_settings()

        # File untouched.
        assert settings_file.read_text() == original
        # Hint printed.
        assert "dev.containers.executeInWSL" in capsys.readouterr().err

    def test_falls_back_to_hint_when_path_not_found(self, capsys):
        with patch("dcode.wsl._get_windows_vscode_settings_path", return_value=None):
            from dcode.wsl import _ensure_wsl_docker_settings
            _ensure_wsl_docker_settings()

        assert "dev.containers.executeInWSL" in capsys.readouterr().err
