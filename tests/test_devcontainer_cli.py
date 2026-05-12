"""Tests for dcode.devcontainer_cli."""

from __future__ import annotations

import json
import stat
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from dcode import devcontainer_cli
from dcode._progress import StreamedResult


def _completed(rc: int = 0, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=rc, stdout=stdout, stderr=stderr)


def _streamed(
    rc: int = 0,
    stdout: str = "",
    stderr: str = "",
    error: str | None = None,
) -> StreamedResult:
    return StreamedResult(returncode=rc, stdout=stdout, stderr=stderr, error=error)


def _make_executable(path: Path) -> None:
    path.write_text("#!/bin/sh\necho devcontainer 0.86.0\n")
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _ok_download_patch(payload: bytes = b"#!/bin/sh\necho ok\n"):
    """Patch urlopen to return an in-memory file-like context manager."""
    import io

    fake = io.BytesIO(payload)

    class _Ctx:
        def __enter__(self_inner):
            return fake

        def __exit__(self_inner, *args):
            return False

    return patch(
        "dcode.devcontainer_cli.urllib.request.urlopen",
        return_value=_Ctx(),
    )


# ---------------------------------------------------------------------------
# find_cli
# ---------------------------------------------------------------------------


class TestFindCli:
    def test_returns_path_when_on_path(self):
        with patch(
            "dcode.devcontainer_cli.shutil.which",
            return_value="/usr/local/bin/devcontainer",
        ):
            assert devcontainer_cli.find_cli() == Path("/usr/local/bin/devcontainer")

    def test_falls_back_to_default_install_dir(self, tmp_path, monkeypatch):
        fake_home_bin = tmp_path / "bin"
        fake_home_bin.mkdir()
        binary = fake_home_bin / "devcontainer"
        _make_executable(binary)
        monkeypatch.setattr(
            "dcode.devcontainer_cli.DEFAULT_INSTALL_PREFIX", tmp_path
        )
        with patch("dcode.devcontainer_cli.shutil.which", return_value=None):
            assert devcontainer_cli.find_cli() == binary

    def test_returns_none_when_missing_everywhere(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "dcode.devcontainer_cli.DEFAULT_INSTALL_PREFIX", tmp_path
        )
        with patch("dcode.devcontainer_cli.shutil.which", return_value=None):
            assert devcontainer_cli.find_cli() is None

    def test_default_dir_path_must_be_executable(self, tmp_path, monkeypatch):
        # File exists but is not executable: do not consider it a valid CLI.
        fake_home_bin = tmp_path / "bin"
        fake_home_bin.mkdir()
        binary = fake_home_bin / "devcontainer"
        binary.write_text("not really an executable")
        binary.chmod(0o644)
        monkeypatch.setattr(
            "dcode.devcontainer_cli.DEFAULT_INSTALL_PREFIX", tmp_path
        )
        with patch("dcode.devcontainer_cli.shutil.which", return_value=None):
            assert devcontainer_cli.find_cli() is None


# ---------------------------------------------------------------------------
# cli_version
# ---------------------------------------------------------------------------


class TestCliVersion:
    def test_returns_version_string(self):
        with patch(
            "dcode.devcontainer_cli.subprocess.run",
            return_value=_completed(0, "0.86.0\n", ""),
        ):
            assert devcontainer_cli.cli_version(Path("/x/devcontainer")) == "0.86.0"

    def test_non_zero_returns_none(self):
        with patch(
            "dcode.devcontainer_cli.subprocess.run",
            return_value=_completed(1, "", "boom"),
        ):
            assert devcontainer_cli.cli_version(Path("/x/devcontainer")) is None

    def test_oserror_returns_none(self):
        with patch(
            "dcode.devcontainer_cli.subprocess.run",
            side_effect=OSError("nope"),
        ):
            assert devcontainer_cli.cli_version(Path("/x/devcontainer")) is None


# ---------------------------------------------------------------------------
# install_cli
# ---------------------------------------------------------------------------


class TestInstallCli:
    def test_success_returns_binary_path(self, tmp_path):
        prefix = tmp_path / "dc"
        bin_path = prefix / "bin" / "devcontainer"

        def fake_run(argv, **kwargs):
            # Simulate the installer creating the binary.
            bin_path.parent.mkdir(parents=True, exist_ok=True)
            _make_executable(bin_path)
            return _streamed(0, "", "")

        console = MagicMock()
        with (
            _ok_download_patch(),
            patch(
                "dcode.devcontainer_cli._progress.run_streaming",
                side_effect=fake_run,
            ) as m,
        ):
            result = devcontainer_cli.install_cli(prefix=prefix, console=console)
        assert result == bin_path
        # Verify the install script was run with --prefix:
        argv = m.call_args.args[0]
        assert argv[0] == "sh"
        assert "--prefix" in argv
        assert argv[argv.index("--prefix") + 1] == str(prefix)
        # The streaming helper was given a label and our console:
        assert m.call_args.kwargs["label"].startswith("Installing Dev Containers CLI")
        assert m.call_args.kwargs["console"] is console

    def test_download_failure_returns_none(self, tmp_path):
        console = MagicMock()
        import urllib.error
        with patch(
            "dcode.devcontainer_cli.urllib.request.urlopen",
            side_effect=urllib.error.URLError("dns"),
        ):
            assert (
                devcontainer_cli.install_cli(prefix=tmp_path / "dc", console=console)
                is None
            )

    def test_install_script_failure_returns_none(self, tmp_path):
        console = MagicMock()
        with (
            _ok_download_patch(),
            patch(
                "dcode.devcontainer_cli._progress.run_streaming",
                return_value=_streamed(2, "", "no permission"),
            ),
        ):
            assert (
                devcontainer_cli.install_cli(prefix=tmp_path / "dc", console=console)
                is None
            )

    def test_install_script_succeeds_but_binary_missing(self, tmp_path):
        # Pathological: installer exits 0 but leaves no binary.
        console = MagicMock()
        with (
            _ok_download_patch(),
            patch(
                "dcode.devcontainer_cli._progress.run_streaming",
                return_value=_streamed(0, "", ""),
            ),
        ):
            assert (
                devcontainer_cli.install_cli(prefix=tmp_path / "dc", console=console)
                is None
            )

    def test_install_script_oserror_returns_none(self, tmp_path):
        # run_streaming reports launcher OSError via the .error field.
        console = MagicMock()
        with (
            _ok_download_patch(),
            patch(
                "dcode.devcontainer_cli._progress.run_streaming",
                return_value=_streamed(-1, "", "", error="missing /bin/sh"),
            ),
        ):
            assert (
                devcontainer_cli.install_cli(prefix=tmp_path / "dc", console=console)
                is None
            )


# ---------------------------------------------------------------------------
# up
# ---------------------------------------------------------------------------


class TestUp:
    def test_success_returns_container_id(self, tmp_path):
        success = json.dumps(
            {
                "outcome": "success",
                "containerId": "abcdef0123456789",
                "remoteUser": "node",
                "remoteWorkspaceFolder": "/workspaces/proj",
            }
        )
        console = MagicMock()
        with patch(
            "dcode.devcontainer_cli._progress.run_streaming",
            return_value=_streamed(0, success + "\n", "log noise"),
        ) as m:
            cid, err = devcontainer_cli.up(
                Path("/x/devcontainer"),
                tmp_path / "proj",
                tmp_path / "proj/.devcontainer/devcontainer.json",
                console=console,
            )
        assert cid == "abcdef0123456789"
        assert err == ""
        # Verify the invocation shape:
        argv = m.call_args.args[0]
        assert argv[0] == "/x/devcontainer"
        assert argv[1] == "up"
        assert "--workspace-folder" in argv
        assert argv[argv.index("--workspace-folder") + 1] == str(tmp_path / "proj")
        assert "--config" in argv
        # Streamed via the progress helper with our console:
        assert m.call_args.kwargs["label"].startswith("Building devcontainer")
        assert m.call_args.kwargs["console"] is console

    def test_success_with_log_lines_before_json(self, tmp_path):
        # devcontainers/cli normally writes only JSON to stdout, but cope
        # defensively with prefix log lines (e.g. progress chatter).
        success = json.dumps({"outcome": "success", "containerId": "cid_abc"})
        stdout = "preparing build...\nfetching layers...\n" + success + "\n"
        console = MagicMock()
        with patch(
            "dcode.devcontainer_cli._progress.run_streaming",
            return_value=_streamed(0, stdout, ""),
        ):
            cid, err = devcontainer_cli.up(
                Path("/x/devcontainer"),
                tmp_path / "proj",
                tmp_path / "proj/dc.json",
                console=console,
            )
        assert cid == "cid_abc"
        assert err == ""

    def test_error_outcome_returns_description(self, tmp_path):
        err_payload = json.dumps(
            {
                "outcome": "error",
                "message": "Build failed",
                "description": "Dockerfile RUN apt-get install foo failed",
            }
        )
        console = MagicMock()
        with patch(
            "dcode.devcontainer_cli._progress.run_streaming",
            return_value=_streamed(1, err_payload + "\n", "stderr noise"),
        ):
            cid, err = devcontainer_cli.up(
                Path("/x/devcontainer"),
                tmp_path / "proj",
                tmp_path / "proj/dc.json",
                console=console,
            )
        assert cid is None
        assert "Dockerfile RUN apt-get install foo failed" in err
        # Stderr was streamed live by run_streaming, so we don't repeat it
        # in the returned summary.
        assert "stderr noise" not in err

    def test_non_zero_with_no_json_returns_exit_summary(self, tmp_path):
        # When the CLI bails before writing its JSON envelope we don't have
        # a structured description, just an exit code. The full stderr was
        # already streamed live, so the summary just points back at it.
        console = MagicMock()
        with patch(
            "dcode.devcontainer_cli._progress.run_streaming",
            return_value=_streamed(2, "", "Cannot connect to docker daemon\n"),
        ):
            cid, err = devcontainer_cli.up(
                Path("/x/devcontainer"),
                tmp_path / "proj",
                tmp_path / "proj/dc.json",
                console=console,
            )
        assert cid is None
        assert "exited with code 2" in err
        assert "see output above" in err

    def test_launch_oserror_returns_helpful_message(self, tmp_path):
        # run_streaming surfaces pre-launch OSError via .error.
        console = MagicMock()
        with patch(
            "dcode.devcontainer_cli._progress.run_streaming",
            return_value=_streamed(-1, "", "", error="not found"),
        ):
            cid, err = devcontainer_cli.up(
                Path("/missing/devcontainer"),
                tmp_path / "proj",
                tmp_path / "proj/dc.json",
                console=console,
            )
        assert cid is None
        assert "failed to launch" in err
        assert "not found" in err

    def test_zero_exit_with_no_container_id_treated_as_failure(self, tmp_path):
        # Defensive: if outcome=success but no containerId, we shouldn't
        # silently return success.
        weird = json.dumps({"outcome": "success"})
        console = MagicMock()
        with patch(
            "dcode.devcontainer_cli._progress.run_streaming",
            return_value=_streamed(0, weird + "\n", ""),
        ):
            cid, err = devcontainer_cli.up(
                Path("/x/devcontainer"),
                tmp_path / "proj",
                tmp_path / "proj/dc.json",
                console=console,
            )
        assert cid is None
        assert err  # some explanation

    def test_zero_exit_with_unparseable_output_treated_as_failure(self, tmp_path):
        console = MagicMock()
        with patch(
            "dcode.devcontainer_cli._progress.run_streaming",
            return_value=_streamed(0, "totally not json", "logs"),
        ):
            cid, err = devcontainer_cli.up(
                Path("/x/devcontainer"),
                tmp_path / "proj",
                tmp_path / "proj/dc.json",
                console=console,
            )
        assert cid is None
        assert "exited with code 0" in err


# ---------------------------------------------------------------------------
# install_hint
# ---------------------------------------------------------------------------


class TestInstallHint:
    def test_includes_curl_and_npm_paths(self):
        hint = devcontainer_cli.install_hint()
        assert "curl" in hint
        assert "install.sh" in hint
        assert "npm install -g @devcontainers/cli" in hint
