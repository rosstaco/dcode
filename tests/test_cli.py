"""Tests for dcode CLI entrypoint and package metadata."""

from unittest.mock import patch

import pytest

from dcode import cli


class TestVersion:
    def test_resolves_via_importlib_metadata(self):
        from importlib.metadata import version

        import dcode

        assert dcode.__version__ == version("dcode")


class TestDispatch:
    def test_no_subcommand_calls_run_dcode(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["dcode"])
        with (
            patch("dcode.cli.run_dcode") as m_run,
            patch("dcode.cli.run_update") as m_upd,
        ):
            cli.main()
        m_run.assert_called_once_with(".", insiders=False)
        m_upd.assert_not_called()

    def test_path_arg_calls_run_dcode(self, monkeypatch, tmp_path):
        monkeypatch.setattr("sys.argv", ["dcode", str(tmp_path)])
        with patch("dcode.cli.run_dcode") as m_run:
            cli.main()
        m_run.assert_called_once_with(str(tmp_path), insiders=False)

    def test_update_calls_run_update(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["dcode", "update"])
        with (
            patch("dcode.cli.run_update", return_value=0) as m_upd,
            patch("dcode.cli.run_update_check") as m_chk,
            patch("dcode.cli.run_dcode") as m_run,
            pytest.raises(SystemExit) as exc,
        ):
            cli.main()
        assert exc.value.code == 0
        m_upd.assert_called_once_with()
        m_chk.assert_not_called()
        m_run.assert_not_called()

    def test_update_check_calls_run_update_check(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["dcode", "update", "--check"])
        with (
            patch("dcode.cli.run_update_check", return_value=1) as m_chk,
            patch("dcode.cli.run_update") as m_upd,
            pytest.raises(SystemExit) as exc,
        ):
            cli.main()
        assert exc.value.code == 1
        m_chk.assert_called_once_with()
        m_upd.assert_not_called()

    def test_update_exit_code_forwarded(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["dcode", "update"])
        with (
            patch("dcode.cli.run_update", return_value=42),
            pytest.raises(SystemExit) as exc,
        ):
            cli.main()
        assert exc.value.code == 42

    def test_path_named_update_workaround(self, monkeypatch):
        # Documented escape hatch: prefix with ./ to disambiguate.
        monkeypatch.setattr("sys.argv", ["dcode", "./update"])
        with (
            patch("dcode.cli.run_dcode") as m_run,
            patch("dcode.cli.run_update") as m_upd,
        ):
            cli.main()
        m_run.assert_called_once_with("./update", insiders=False)
        m_upd.assert_not_called()

    def test_doctor_subcommand_calls_run_doctor(self, monkeypatch, tmp_path):
        from pathlib import Path

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["dcode", "doctor"])
        with (
            patch("dcode.cli.run_doctor", return_value=0) as m_doc,
            patch("dcode.cli.run_dcode") as m_run,
            pytest.raises(SystemExit) as exc,
        ):
            cli.main()
        assert exc.value.code == 0
        m_doc.assert_called_once_with(Path.cwd())
        m_run.assert_not_called()

    def test_doctor_with_path(self, monkeypatch, tmp_path):
        from pathlib import Path

        monkeypatch.setattr("sys.argv", ["dcode", "doctor", str(tmp_path)])
        with (
            patch("dcode.cli.run_doctor", return_value=0) as m_doc,
            pytest.raises(SystemExit),
        ):
            cli.main()
        m_doc.assert_called_once_with(Path(str(tmp_path)))

    def test_doctor_exit_code_forwarded(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["dcode", "doctor"])
        with (
            patch("dcode.cli.run_doctor", return_value=1),
            pytest.raises(SystemExit) as exc,
        ):
            cli.main()
        assert exc.value.code == 1

    def test_path_named_doctor_workaround(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["dcode", "./doctor"])
        with (
            patch("dcode.cli.run_dcode") as m_run,
            patch("dcode.cli.run_doctor") as m_doc,
        ):
            cli.main()
        m_run.assert_called_once_with("./doctor", insiders=False)
        m_doc.assert_not_called()


class TestShellDispatch:
    """Dispatch tests for the `dcode shell` subcommand.

    NOTE: `run_shell` is lazy-imported inside `cli.main()` via
    `from dcode.shell import run_shell`, so it MUST be patched at
    `dcode.shell.run_shell` rather than `dcode.cli.run_shell`.
    """

    def test_shell_no_args(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["dcode", "shell"])
        with (
            patch("dcode.shell.run_shell", return_value=0) as m_run,
            pytest.raises(SystemExit) as exc,
        ):
            cli.main()
        assert exc.value.code == 0
        m_run.assert_called_once_with(".", insiders=False, shell_override=None)

    def test_shell_with_path(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["dcode", "shell", "./project"])
        with (
            patch("dcode.shell.run_shell", return_value=0) as m_run,
            pytest.raises(SystemExit),
        ):
            cli.main()
        m_run.assert_called_once_with("./project", insiders=False, shell_override=None)

    def test_shell_with_shell_override(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["dcode", "shell", "--shell", "zsh"])
        with (
            patch("dcode.shell.run_shell", return_value=0) as m_run,
            pytest.raises(SystemExit),
        ):
            cli.main()
        m_run.assert_called_once_with(".", insiders=False, shell_override="zsh")

    def test_shell_with_path_and_shell_override(self, monkeypatch):
        monkeypatch.setattr(
            "sys.argv", ["dcode", "shell", "./path", "--shell", "bash"]
        )
        with (
            patch("dcode.shell.run_shell", return_value=0) as m_run,
            pytest.raises(SystemExit),
        ):
            cli.main()
        m_run.assert_called_once_with("./path", insiders=False, shell_override="bash")

    def test_insiders_flag_before_shell(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["dcode", "-i", "shell"])
        with (
            patch("dcode.shell.run_shell", return_value=0) as m_run,
            pytest.raises(SystemExit),
        ):
            cli.main()
        m_run.assert_called_once_with(".", insiders=True, shell_override=None)

    def test_insiders_flag_after_shell_rejected(self, monkeypatch, capsys):
        # The shell subparser does NOT redeclare -i, so argparse rejects
        # `dcode shell -i` as an unrecognized argument (exit code 2).
        monkeypatch.setattr("sys.argv", ["dcode", "shell", "-i"])
        with (
            patch("dcode.shell.run_shell") as m_run,
            pytest.raises(SystemExit) as exc,
        ):
            cli.main()
        assert exc.value.code == 2
        m_run.assert_not_called()

    def test_shell_override_with_internal_whitespace_rejected(
        self, monkeypatch, capsys
    ):
        monkeypatch.setattr("sys.argv", ["dcode", "shell", "--shell", "bash -l"])
        with (
            patch("dcode.shell.run_shell") as m_run,
            pytest.raises(SystemExit) as exc,
        ):
            cli.main()
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "single executable" in err or "whitespace" in err.lower()
        m_run.assert_not_called()

    def test_shell_override_with_leading_whitespace_rejected(
        self, monkeypatch, capsys
    ):
        monkeypatch.setattr("sys.argv", ["dcode", "shell", "--shell", " zsh"])
        with (
            patch("dcode.shell.run_shell") as m_run,
            pytest.raises(SystemExit) as exc,
        ):
            cli.main()
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "single executable" in err or "whitespace" in err.lower()
        m_run.assert_not_called()

    def test_path_named_shell_workaround(self, monkeypatch):
        # `dcode ./shell` must open a folder literally named 'shell',
        # not dispatch to the shell subcommand.
        monkeypatch.setattr("sys.argv", ["dcode", "./shell"])
        with patch("dcode.cli.run_dcode") as m_run:
            cli.main()
        m_run.assert_called_once_with("./shell", insiders=False)

    def test_looks_like_subcommand_recognizes_shell(self):
        assert cli._looks_like_subcommand(["shell"]) is True
        assert cli._looks_like_subcommand(["./shell"]) is False

    def test_top_level_help_mentions_shell(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["dcode", "--help"])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "shell" in out
        assert "dcode ./shell" in out

    def test_shell_subcommand_help(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["dcode", "shell", "--help"])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "--shell" in out
        assert "devcontainer" in out.lower()

    def test_shell_exit_code_forwarded(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["dcode", "shell"])
        with (
            patch("dcode.shell.run_shell", return_value=127),
            pytest.raises(SystemExit) as exc,
        ):
            cli.main()
        assert exc.value.code == 127
