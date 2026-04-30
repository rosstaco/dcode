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
