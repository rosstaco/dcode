"""Tests for ``dcode.update``."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from dcode import update
from dcode.update import detect_install_method, run_update, run_update_check
from dcode.version_check import NetworkError

# ---------- detect_install_method ----------


def test_detect_uv_missing():
    with patch.object(update.shutil, "which", return_value=None):
        assert detect_install_method() == "uv-missing"


def test_detect_returncode_nonzero_is_unknown():
    with (
        patch.object(update.shutil, "which", return_value="/usr/local/bin/uv"),
        patch.object(
            update.subprocess,
            "run",
            return_value=MagicMock(returncode=1, stdout=""),
        ),
    ):
        assert detect_install_method() == "unknown"


def test_detect_timeout_is_unknown():
    with (
        patch.object(update.shutil, "which", return_value="/usr/local/bin/uv"),
        patch.object(
            update.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(cmd="uv", timeout=10),
        ),
    ):
        assert detect_install_method() == "unknown"


def test_detect_oserror_is_unknown():
    with (
        patch.object(update.shutil, "which", return_value="/usr/local/bin/uv"),
        patch.object(update.subprocess, "run", side_effect=OSError("boom")),
    ):
        assert detect_install_method() == "unknown"


def test_detect_uv_tool_when_dcode_listed():
    stdout = "dcode v0.4.2\n- dcode\nfs2 v0.1.0\n- fs2\n"
    with (
        patch.object(update.shutil, "which", return_value="/usr/local/bin/uv"),
        patch.object(
            update.subprocess,
            "run",
            return_value=MagicMock(returncode=0, stdout=stdout),
        ),
    ):
        assert detect_install_method() == "uv-tool"


def test_detect_not_uv_tool_when_dcode_missing():
    stdout = "fs2 v0.1.0\n- fs2\nghostcfg v0.1.3\n- gcfg\n"
    with (
        patch.object(update.shutil, "which", return_value="/usr/local/bin/uv"),
        patch.object(
            update.subprocess,
            "run",
            return_value=MagicMock(returncode=0, stdout=stdout),
        ),
    ):
        assert detect_install_method() == "not-uv-tool"


# ---------- run_update ----------


def test_run_update_uv_missing(capsys):
    with patch.object(update, "detect_install_method", return_value="uv-missing"):
        rc = run_update()
    err = capsys.readouterr().err
    assert rc == 1
    assert "uv" in err
    assert "https://docs.astral.sh/uv" in err


def test_run_update_not_uv_tool(capsys):
    with patch.object(update, "detect_install_method", return_value="not-uv-tool"):
        rc = run_update()
    err = capsys.readouterr().err
    assert rc == 1
    assert "uv tool install" in err


def test_run_update_uv_tool_happy_path():
    with (
        patch.object(update, "detect_install_method", return_value="uv-tool"),
        patch.object(
            update.subprocess,
            "run",
            return_value=MagicMock(returncode=0),
        ) as mock_run,
    ):
        rc = run_update()
    assert rc == 0
    mock_run.assert_called_once_with(["uv", "tool", "upgrade", "dcode"], check=False)


def test_run_update_uv_tool_forwards_nonzero():
    with (
        patch.object(update, "detect_install_method", return_value="uv-tool"),
        patch.object(
            update.subprocess,
            "run",
            return_value=MagicMock(returncode=42),
        ),
    ):
        rc = run_update()
    assert rc == 42


def test_run_update_unknown_falls_through(capsys):
    with (
        patch.object(update, "detect_install_method", return_value="unknown"),
        patch.object(
            update.subprocess,
            "run",
            return_value=MagicMock(returncode=0),
        ) as mock_run,
    ):
        rc = run_update()
    assert rc == 0
    assert "could not detect" in capsys.readouterr().err
    mock_run.assert_called_once()


# ---------- run_update_check ----------


_RELEASE = {"tag_name": "v0.4.2", "html_url": "https://github.com/rosstaco/dcode/releases/tag/v0.4.2"}


def test_run_update_check_up_to_date(capsys):
    with (
        patch.object(update, "dcode") as mock_dcode,
        patch.object(update.version_check, "get_latest_release", return_value=_RELEASE),
    ):
        mock_dcode.__version__ = "0.4.2"
        rc = run_update_check()
    err = capsys.readouterr().err
    assert rc == 0
    assert "up to date" in err
    assert "local:" in err and "0.4.2" in err
    assert "release:" in err


def test_run_update_check_behind(capsys):
    with (
        patch.object(update, "dcode") as mock_dcode,
        patch.object(update.version_check, "get_latest_release", return_value=_RELEASE),
    ):
        mock_dcode.__version__ = "0.4.0"
        rc = run_update_check()
    err = capsys.readouterr().err
    assert rc == 1
    assert "update available" in err


def test_run_update_check_ahead_dev(capsys):
    with (
        patch.object(update, "dcode") as mock_dcode,
        patch.object(update.version_check, "get_latest_release", return_value=_RELEASE),
    ):
        mock_dcode.__version__ = "0.4.2.dev0+g1234"
        rc = run_update_check()
    err = capsys.readouterr().err
    assert rc == 0
    assert "ahead" in err


def test_run_update_check_strictly_ahead(capsys):
    with (
        patch.object(update, "dcode") as mock_dcode,
        patch.object(update.version_check, "get_latest_release", return_value=_RELEASE),
    ):
        mock_dcode.__version__ = "0.5.0"
        rc = run_update_check()
    err = capsys.readouterr().err
    assert rc == 0
    assert "ahead" in err


def test_run_update_check_network_error(capsys):
    with (
        patch.object(update, "dcode") as mock_dcode,
        patch.object(
            update.version_check,
            "get_latest_release",
            side_effect=NetworkError("offline"),
        ),
    ):
        mock_dcode.__version__ = "0.4.2"
        rc = run_update_check()
    err = capsys.readouterr().err
    assert rc == 2
    assert "could not reach" in err


# Reference pytest so isort/F401 stays quiet if it ever drops.
_ = pytest
