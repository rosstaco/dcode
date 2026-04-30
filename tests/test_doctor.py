"""Tests for ``dcode doctor`` checks, plan summary, and driver."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest
from conftest import _make_worktree

from dcode import doctor

# ---------------------------------------------------------------------------
# check_editor
# ---------------------------------------------------------------------------


class TestCheckEditor:
    def test_both_present(self):
        with patch("dcode.doctor.shutil.which", side_effect=lambda x: f"/u/{x}"):
            status, msg, hint = doctor.check_editor()
        assert status == "ok"
        assert "code" in msg and "code-insiders" in msg
        assert hint is None

    def test_only_code(self):
        with patch(
            "dcode.doctor.shutil.which",
            side_effect=lambda x: "/u/code" if x == "code" else None,
        ):
            status, msg, hint = doctor.check_editor()
        assert status == "warn"
        assert "code-insiders not on PATH" in msg
        assert hint and "code-insiders" in hint

    def test_only_insiders(self):
        with patch(
            "dcode.doctor.shutil.which",
            side_effect=lambda x: "/u/code-insiders" if x == "code-insiders" else None,
        ):
            status, msg, hint = doctor.check_editor()
        assert status == "warn"
        assert "code not on PATH" in msg

    def test_neither(self):
        with patch("dcode.doctor.shutil.which", return_value=None):
            status, msg, hint = doctor.check_editor()
        assert status == "fail"
        assert "neither" in msg
        assert hint and "Command Palette" in hint


# ---------------------------------------------------------------------------
# check_extension
# ---------------------------------------------------------------------------


class TestCheckExtension:
    def test_present(self):
        cp = CompletedProcess([], 0, "ms-vscode-remote.remote-containers\nfoo.bar\n", "")
        with (
            patch("dcode.doctor.shutil.which", side_effect=lambda x: "/u/code" if x == "code" else None),
            patch("dcode.doctor.subprocess.run", return_value=cp),
        ):
            status, msg, hint = doctor.check_extension()
        assert status == "ok"
        assert "ms-vscode-remote.remote-containers" in msg
        assert hint is None

    def test_missing(self):
        cp = CompletedProcess([], 0, "foo.bar\n", "")
        with (
            patch("dcode.doctor.shutil.which", side_effect=lambda x: "/u/code" if x == "code" else None),
            patch("dcode.doctor.subprocess.run", return_value=cp),
        ):
            status, msg, hint = doctor.check_extension()
        assert status == "fail"
        assert "missing" in msg
        assert hint and "install-extension" in hint

    def test_subprocess_oserror_warns(self):
        with (
            patch("dcode.doctor.shutil.which", side_effect=lambda x: "/u/code" if x == "code" else None),
            patch("dcode.doctor.subprocess.run", side_effect=OSError("boom")),
        ):
            status, msg, hint = doctor.check_extension()
        assert status == "warn"
        assert "could not list" in msg

    def test_returncode_nonzero_warns(self):
        cp = CompletedProcess([], 1, "", "boom")
        with (
            patch("dcode.doctor.shutil.which", side_effect=lambda x: "/u/code" if x == "code" else None),
            patch("dcode.doctor.subprocess.run", return_value=cp),
        ):
            status, _, _ = doctor.check_extension()
        assert status == "warn"

    def test_no_editor_skips(self):
        with patch("dcode.doctor.shutil.which", return_value=None):
            status, msg, _ = doctor.check_extension()
        assert status == "skip"
        assert "no editor" in msg


# ---------------------------------------------------------------------------
# check_docker
# ---------------------------------------------------------------------------


class TestCheckDocker:
    def test_ok(self):
        cp = CompletedProcess([], 0, "29.4.0\n", "")
        with (
            patch("dcode.doctor.shutil.which", return_value="/u/docker"),
            patch("dcode.doctor.subprocess.run", return_value=cp),
        ):
            status, msg, _ = doctor.check_docker()
        assert status == "ok"
        assert "29.4.0" in msg

    def test_daemon_down_fails(self):
        cp = CompletedProcess([], 1, "", "Cannot connect")
        with (
            patch("dcode.doctor.shutil.which", return_value="/u/docker"),
            patch("dcode.doctor.subprocess.run", return_value=cp),
        ):
            status, msg, hint = doctor.check_docker()
        assert status == "fail"
        assert "daemon is not reachable" in msg
        assert hint

    def test_no_cli_warns(self):
        with patch("dcode.doctor.shutil.which", return_value=None):
            status, msg, hint = doctor.check_docker()
        assert status == "warn"
        assert "not on PATH" in msg
        assert hint

    def test_timeout_fails(self):
        with (
            patch("dcode.doctor.shutil.which", return_value="/u/docker"),
            patch(
                "dcode.doctor.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=5),
            ),
        ):
            status, msg, _ = doctor.check_docker()
        assert status == "fail"
        assert "failed" in msg


# ---------------------------------------------------------------------------
# check_git
# ---------------------------------------------------------------------------


class TestCheckGit:
    def test_present(self):
        with patch("dcode.doctor.shutil.which", return_value="/usr/bin/git"):
            status, msg, _ = doctor.check_git()
        assert status == "ok"
        assert "/usr/bin/git" in msg

    def test_missing_warns(self):
        with patch("dcode.doctor.shutil.which", return_value=None):
            status, _, hint = doctor.check_git()
        assert status == "warn"
        assert hint


# ---------------------------------------------------------------------------
# check_wsl + sub-checks
# ---------------------------------------------------------------------------


class TestCheckWsl:
    def test_not_in_wsl(self):
        with patch("dcode.doctor.is_wsl", return_value=False):
            status, msg, _ = doctor.check_wsl()
        assert status == "ok"
        assert "not running in WSL" in msg

    def test_in_wsl(self):
        with patch("dcode.doctor.is_wsl", return_value=True):
            status, msg, _ = doctor.check_wsl()
        assert status == "ok"
        assert "detected" in msg


class TestCheckWslDistro:
    def test_present(self):
        with patch("dcode.doctor.get_wsl_distro", return_value="Ubuntu"):
            status, msg, _ = doctor.check_wsl_distro()
        assert status == "ok"
        assert "Ubuntu" in msg

    def test_missing(self):
        with patch("dcode.doctor.get_wsl_distro", return_value=None):
            status, _, hint = doctor.check_wsl_distro()
        assert status == "warn"
        assert hint


class TestCheckWslSettingsPaths:
    def test_resolves(self, tmp_path):
        with patch(
            "dcode.doctor._get_windows_vscode_settings_path",
            side_effect=lambda insiders: tmp_path / ("ins" if insiders else "stable"),
        ):
            results = doctor.check_wsl_settings_paths()
        assert all(r[0] == "ok" for r in results)
        assert len(results) == 2

    def test_unresolvable_warns(self):
        with patch("dcode.doctor._get_windows_vscode_settings_path", return_value=None):
            results = doctor.check_wsl_settings_paths()
        assert all(r[0] == "warn" for r in results)


class TestCheckWslExecuteInWslSettings:
    def test_correct(self, tmp_path):
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({
            "dev.containers.executeInWSL": True,
            "dev.containers.executeInWSLDistro": "Ubuntu",
        }))
        with (
            patch("dcode.doctor._get_windows_vscode_settings_path",
                  side_effect=lambda insiders: settings if not insiders else None),
            patch("dcode.doctor.get_wsl_distro", return_value="Ubuntu"),
        ):
            results = doctor.check_wsl_executeInWSL_settings()
        assert results
        assert results[0][0] == "ok"

    def test_missing_warns(self, tmp_path):
        settings = tmp_path / "settings.json"
        settings.write_text("{}")
        with (
            patch("dcode.doctor._get_windows_vscode_settings_path",
                  side_effect=lambda insiders: settings if not insiders else None),
            patch("dcode.doctor.get_wsl_distro", return_value="Ubuntu"),
        ):
            results = doctor.check_wsl_executeInWSL_settings()
        assert results[0][0] == "warn"

    def test_parse_error_warns(self, tmp_path):
        settings = tmp_path / "settings.json"
        settings.write_text("{ not json")
        with (
            patch("dcode.doctor._get_windows_vscode_settings_path",
                  side_effect=lambda insiders: settings if not insiders else None),
            patch("dcode.doctor.get_wsl_distro", return_value="Ubuntu"),
        ):
            results = doctor.check_wsl_executeInWSL_settings()
        assert results[0][0] == "warn"
        assert "parse" in results[0][1]


# ---------------------------------------------------------------------------
# check_devcontainer / check_devcontainer_parses
# ---------------------------------------------------------------------------


class TestCheckDevcontainer:
    def test_found(self, tmp_path):
        dc = tmp_path / ".devcontainer"
        dc.mkdir()
        (dc / "devcontainer.json").write_text("{}")
        status, msg, _ = doctor.check_devcontainer(tmp_path)
        assert status == "ok"
        assert "devcontainer.json" in msg

    def test_missing_warns(self, tmp_path):
        status, msg, hint = doctor.check_devcontainer(tmp_path)
        assert status == "warn"
        assert "none found" in msg
        assert hint

    def test_via_worktree(self, tmp_path):
        main_repo, worktree = _make_worktree(tmp_path)
        dc = main_repo / ".devcontainer"
        dc.mkdir()
        (dc / "devcontainer.json").write_text("{}")
        status, msg, _ = doctor.check_devcontainer(worktree)
        assert status == "ok"
        assert "devcontainer.json" in msg

    def test_worktree_no_devcontainer(self, tmp_path):
        _, worktree = _make_worktree(tmp_path)
        status, msg, _ = doctor.check_devcontainer(worktree)
        assert status == "warn"
        assert "main repo" in msg


class TestCheckDevcontainerParses:
    def test_ok(self, tmp_path):
        dc = tmp_path / ".devcontainer"
        dc.mkdir()
        (dc / "devcontainer.json").write_text("{}")
        status, _, _ = doctor.check_devcontainer_parses(tmp_path)
        assert status == "ok"

    def test_parse_error_fails(self, tmp_path):
        dc = tmp_path / ".devcontainer"
        dc.mkdir()
        (dc / "devcontainer.json").write_text("{ not json")
        status, msg, hint = doctor.check_devcontainer_parses(tmp_path)
        assert status == "fail"
        assert "parse error" in msg
        assert hint

    def test_top_level_not_object_warns(self, tmp_path):
        dc = tmp_path / ".devcontainer"
        dc.mkdir()
        (dc / "devcontainer.json").write_text("[]")
        status, msg, _ = doctor.check_devcontainer_parses(tmp_path)
        assert status == "warn"
        assert "not an object" in msg

    def test_skip_when_no_file(self, tmp_path):
        status, _, _ = doctor.check_devcontainer_parses(tmp_path)
        assert status == "skip"


# ---------------------------------------------------------------------------
# check_worktree
# ---------------------------------------------------------------------------


class TestCheckWorktree:
    def test_regular_repo(self, tmp_path):
        (tmp_path / ".git").mkdir()
        status, msg, _ = doctor.check_worktree(tmp_path)
        assert status == "ok"
        assert "regular" in msg

    def test_no_git(self, tmp_path):
        status, msg, _ = doctor.check_worktree(tmp_path)
        assert status == "ok"
        assert "not a git repo" in msg

    def test_detected(self, tmp_path):
        _, worktree = _make_worktree(tmp_path)
        status, msg, _ = doctor.check_worktree(worktree)
        assert status == "ok"
        assert "main repo" in msg

    def test_external_warns(self, tmp_path):
        # .git is a file pointing to nonexistent gitdir
        (tmp_path / ".git").write_text("gitdir: /elsewhere/that/does/not/exist\n")
        status, msg, hint = doctor.check_worktree(tmp_path)
        assert status == "warn"
        assert "external" in msg or "submodule" in msg
        assert hint


# ---------------------------------------------------------------------------
# check_version
# ---------------------------------------------------------------------------


class TestCheckVersion:
    def test_up_to_date(self):
        with (
            patch("dcode.doctor.dcode.__version__", "0.4.2"),
            patch(
                "dcode.doctor.version_check.get_latest_release",
                return_value={"tag_name": "v0.4.2", "html_url": "https://x"},
            ),
        ):
            status, msg, _ = doctor.check_version()
        assert status == "ok"
        assert "latest" in msg

    def test_behind(self):
        with (
            patch("dcode.doctor.dcode.__version__", "0.4.1"),
            patch(
                "dcode.doctor.version_check.get_latest_release",
                return_value={"tag_name": "v0.4.2", "html_url": "https://x"},
            ),
        ):
            status, msg, hint = doctor.check_version()
        assert status == "warn"
        assert "https://x" in msg
        assert hint and "dcode update" in hint

    def test_ahead_dev(self):
        with (
            patch("dcode.doctor.dcode.__version__", "0.4.2.dev1+g123"),
            patch(
                "dcode.doctor.version_check.get_latest_release",
                return_value={"tag_name": "v0.4.2", "html_url": "https://x"},
            ),
        ):
            status, msg, _ = doctor.check_version()
        assert status == "ok"
        assert "ahead" in msg

    def test_network_error_warns(self):
        from dcode.version_check import NetworkError

        with patch(
            "dcode.doctor.version_check.get_latest_release",
            side_effect=NetworkError("offline"),
        ):
            status, msg, hint = doctor.check_version()
        assert status == "warn"
        assert "GitHub" in msg
        assert hint


# ---------------------------------------------------------------------------
# check_install_method
# ---------------------------------------------------------------------------


class TestCheckInstallMethod:
    def test_uv_tool(self):
        with patch("dcode.doctor.update.detect_install_method", return_value="uv-tool"):
            status, msg, _ = doctor.check_install_method()
        assert status == "ok"
        assert "uv tool" in msg

    def test_not_uv_tool(self):
        with patch("dcode.doctor.update.detect_install_method", return_value="not-uv-tool"):
            status, _, hint = doctor.check_install_method()
        assert status == "warn"
        assert hint and "uv tool install" in hint

    def test_uv_missing(self):
        with patch("dcode.doctor.update.detect_install_method", return_value="uv-missing"):
            status, msg, hint = doctor.check_install_method()
        assert status == "warn"
        assert "uv" in msg
        assert hint

    def test_unknown(self):
        with patch("dcode.doctor.update.detect_install_method", return_value="unknown"):
            status, _, _ = doctor.check_install_method()
        assert status == "warn"


# ---------------------------------------------------------------------------
# render_plan
# ---------------------------------------------------------------------------


class TestRenderPlan:
    def test_no_editor(self, tmp_path, capsys):
        doctor.render_plan(tmp_path, code_present=False, insiders_present=False)
        err = capsys.readouterr().err
        assert "no editor available" in err

    def test_no_devcontainer_no_worktree(self, tmp_path, capsys):
        with patch("dcode.doctor.is_wsl", return_value=False):
            doctor.render_plan(tmp_path, code_present=True, insiders_present=False)
        err = capsys.readouterr().err
        assert "directly" in err
        assert "code" in err

    def test_with_devcontainer_no_worktree(self, tmp_path, capsys):
        dc = tmp_path / ".devcontainer"
        dc.mkdir()
        (dc / "devcontainer.json").write_text('{"workspaceFolder": "/work"}')
        with patch("dcode.doctor.is_wsl", return_value=False):
            doctor.render_plan(tmp_path, code_present=True, insiders_present=False)
        err = capsys.readouterr().err
        assert "devcontainer.json" in err
        assert "/work" in err
        assert "URI:" in err
        assert "vscode-remote" in err

    def test_with_worktree_and_devcontainer(self, tmp_path, capsys):
        main_repo, worktree = _make_worktree(tmp_path)
        dc = main_repo / ".devcontainer"
        dc.mkdir()
        (dc / "devcontainer.json").write_text('{"workspaceFolder": "/work"}')
        with patch("dcode.doctor.is_wsl", return_value=False):
            doctor.render_plan(worktree, code_present=True, insiders_present=False)
        err = capsys.readouterr().err
        assert "MAIN repo" in err
        assert "/work/.worktrees/pr-34" in err

    def test_external_worktree(self, tmp_path, capsys):
        (tmp_path / ".git").write_text("gitdir: /elsewhere/that/does/not/exist\n")
        with patch("dcode.doctor.is_wsl", return_value=False):
            doctor.render_plan(tmp_path, code_present=True, insiders_present=False)
        err = capsys.readouterr().err
        assert "cannot be resolved" in err

    def test_wsl_shows_uri_and_settings(self, tmp_path, capsys):
        dc = tmp_path / ".devcontainer"
        dc.mkdir()
        (dc / "devcontainer.json").write_text('{"workspaceFolder": "/work"}')
        settings = tmp_path / "settings.json"
        settings.write_text("{}")
        with (
            patch("dcode.doctor.is_wsl", return_value=True),
            patch("dcode.doctor._wsl_to_windows_path", return_value="\\\\wsl.localhost\\Ubuntu\\x"),
            patch("dcode.doctor._get_windows_vscode_settings_path", return_value=settings),
            patch("dcode.doctor.get_wsl_distro", return_value="Ubuntu"),
        ):
            doctor.render_plan(tmp_path, code_present=True, insiders_present=False)
        err = capsys.readouterr().err
        assert "Windows UNC path" in err
        assert "would patch" in err

    def test_wsl_settings_no_change(self, tmp_path, capsys):
        dc = tmp_path / ".devcontainer"
        dc.mkdir()
        (dc / "devcontainer.json").write_text('{"workspaceFolder": "/w"}')
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({
            "dev.containers.executeInWSL": True,
            "dev.containers.executeInWSLDistro": "Ubuntu",
        }))
        with (
            patch("dcode.doctor.is_wsl", return_value=True),
            patch("dcode.doctor._wsl_to_windows_path", return_value="\\\\wsl\\Ubuntu\\x"),
            patch("dcode.doctor._get_windows_vscode_settings_path", return_value=settings),
            patch("dcode.doctor.get_wsl_distro", return_value="Ubuntu"),
        ):
            doctor.render_plan(tmp_path, code_present=True, insiders_present=False)
        err = capsys.readouterr().err
        assert "already correct" in err

    def test_editor_only_code_no_note(self, tmp_path, capsys):
        with patch("dcode.doctor.is_wsl", return_value=False):
            doctor.render_plan(tmp_path, code_present=True, insiders_present=False)
        err = capsys.readouterr().err
        assert "also available" not in err

    def test_editor_both_shows_note(self, tmp_path, capsys):
        with patch("dcode.doctor.is_wsl", return_value=False):
            doctor.render_plan(tmp_path, code_present=True, insiders_present=True)
        err = capsys.readouterr().err
        assert "also available" in err
        assert "code-insiders" in err

    def test_editor_only_insiders(self, tmp_path, capsys):
        with patch("dcode.doctor.is_wsl", return_value=False):
            doctor.render_plan(tmp_path, code_present=False, insiders_present=True)
        err = capsys.readouterr().err
        assert "code-insiders" in err
        assert "code\" not on PATH" in err


# ---------------------------------------------------------------------------
# run_doctor (driver)
# ---------------------------------------------------------------------------


def _all_ok(*_args, **_kw):
    return ("ok", "ok msg", None)


class TestRunDoctor:
    def test_no_failures_exits_0(self, tmp_path, capsys):
        with (
            patch("dcode.doctor.shutil.which", return_value="/u/x"),
            patch("dcode.doctor.is_wsl", return_value=False),
            patch.multiple(
                "dcode.doctor",
                check_editor=lambda: ("ok", "e", None),
                check_extension=lambda: ("ok", "x", None),
                check_docker=lambda: ("ok", "d", None),
                check_git=lambda: ("ok", "g", None),
                check_devcontainer=lambda *a: ("warn", "w", "h"),
                check_devcontainer_parses=lambda *a: ("skip", "s", None),
                check_worktree=lambda *a: ("ok", "wt", None),
                check_version=lambda: ("ok", "v", None),
                check_install_method=lambda: ("ok", "i", None),
            ),
        ):
            rc = doctor.run_doctor(tmp_path)
        assert rc == 0
        err = capsys.readouterr().err
        assert "dcode doctor:" in err
        assert " 0 fail" in err

    def test_with_failure_exits_1(self, tmp_path, capsys):
        with (
            patch("dcode.doctor.shutil.which", return_value="/u/x"),
            patch("dcode.doctor.is_wsl", return_value=False),
            patch.multiple(
                "dcode.doctor",
                check_editor=lambda: ("ok", "e", None),
                check_extension=lambda: ("ok", "x", None),
                check_docker=lambda: ("fail", "d", "h"),
                check_git=lambda: ("ok", "g", None),
                check_devcontainer=lambda *a: ("ok", "dc", None),
                check_devcontainer_parses=lambda *a: ("ok", "p", None),
                check_worktree=lambda *a: ("ok", "wt", None),
                check_version=lambda: ("ok", "v", None),
                check_install_method=lambda: ("ok", "i", None),
            ),
        ):
            rc = doctor.run_doctor(tmp_path)
        assert rc == 1
        err = capsys.readouterr().err
        assert " 1 fail" in err
        assert "hint:" in err

    def test_summary_line_format(self, tmp_path, capsys):
        with (
            patch("dcode.doctor.shutil.which", return_value=None),
            patch("dcode.doctor.is_wsl", return_value=False),
            patch.multiple(
                "dcode.doctor",
                check_editor=lambda: ("ok", "e", None),
                check_extension=lambda: ("ok", "x", None),
                check_docker=lambda: ("warn", "d", "h"),
                check_git=lambda: ("warn", "g", "h"),
                check_devcontainer=lambda *a: ("ok", "dc", None),
                check_devcontainer_parses=lambda *a: ("ok", "p", None),
                check_worktree=lambda *a: ("ok", "wt", None),
                check_version=lambda: ("ok", "v", None),
                check_install_method=lambda: ("ok", "i", None),
            ),
        ):
            doctor.run_doctor(tmp_path)
        err = capsys.readouterr().err
        assert "dcode doctor: 8 ok, 2 warn, 0 fail" in err

    def test_plan_failure_does_not_change_exit_code(self, tmp_path, capsys):
        with (
            patch("dcode.doctor.shutil.which", return_value="/u/x"),
            patch("dcode.doctor.is_wsl", return_value=False),
            patch.multiple(
                "dcode.doctor",
                check_editor=lambda: ("ok", "e", None),
                check_extension=lambda: ("ok", "x", None),
                check_docker=lambda: ("ok", "d", None),
                check_git=lambda: ("ok", "g", None),
                check_devcontainer=lambda *a: ("ok", "dc", None),
                check_devcontainer_parses=lambda *a: ("ok", "p", None),
                check_worktree=lambda *a: ("ok", "wt", None),
                check_version=lambda: ("ok", "v", None),
                check_install_method=lambda: ("ok", "i", None),
                render_plan=lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("kaboom")),
            ),
        ):
            rc = doctor.run_doctor(tmp_path)
        assert rc == 0
        err = capsys.readouterr().err
        assert "kaboom" in err

    def test_path_passed_to_devcontainer_checks(self, tmp_path):
        captured: dict = {}

        def cap(p):
            captured["path"] = p
            return ("ok", "x", None)

        with (
            patch("dcode.doctor.shutil.which", return_value="/u/x"),
            patch("dcode.doctor.is_wsl", return_value=False),
            patch.multiple(
                "dcode.doctor",
                check_editor=lambda: ("ok", "e", None),
                check_extension=lambda: ("ok", "x", None),
                check_docker=lambda: ("ok", "d", None),
                check_git=lambda: ("ok", "g", None),
                check_devcontainer=cap,
                check_devcontainer_parses=lambda *a: ("skip", "p", None),
                check_worktree=lambda *a: ("ok", "wt", None),
                check_version=lambda: ("ok", "v", None),
                check_install_method=lambda: ("ok", "i", None),
                render_plan=lambda *a, **kw: None,
            ),
        ):
            doctor.run_doctor(tmp_path)
        assert captured["path"] == tmp_path.resolve()




# ---------------------------------------------------------------------------
# No-ANSI rendering when NO_COLOR is set
# ---------------------------------------------------------------------------


_ANSI_RE = re.compile(r"\x1b\[")


def test_run_doctor_no_color_emits_no_ansi(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    with (
        patch("dcode.doctor.shutil.which", return_value="/u/x"),
        patch("dcode.doctor.is_wsl", return_value=False),
        patch.multiple(
            "dcode.doctor",
            check_editor=lambda: ("ok", "e", None),
            check_extension=lambda: ("ok", "x", None),
            check_docker=lambda: ("warn", "d", "h"),
            check_git=lambda: ("ok", "g", None),
            check_devcontainer=lambda *a: ("ok", "dc", None),
            check_devcontainer_parses=lambda *a: ("ok", "p", None),
            check_worktree=lambda *a: ("ok", "wt", None),
            check_version=lambda: ("ok", "v", None),
            check_install_method=lambda: ("ok", "i", None),
        ),
    ):
        doctor.run_doctor(tmp_path)
    err = capsys.readouterr().err
    assert "dcode doctor:" in err
    assert _ANSI_RE.search(err) is None, f"ANSI escapes leaked: {err!r}"


# Need re for the ANSI regex

# Suppress unused import warning
_ = pytest
_ = Path

