"""Tests for dcode._progress."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

from dcode._progress import StreamedResult, run_streaming, with_spinner

# ---------------------------------------------------------------------------
# with_spinner
# ---------------------------------------------------------------------------


class TestWithSpinner:
    def test_acts_as_passthrough_context_manager(self):
        sentinel = object()
        console = MagicMock()
        with with_spinner("doing stuff", console=console):
            result = sentinel
        assert result is sentinel
        console.status.assert_called_once_with("doing stuff", spinner="dots")

    def test_propagates_exceptions(self):
        console = MagicMock()
        try:
            with with_spinner("oops", console=console):
                raise RuntimeError("boom")
        except RuntimeError as exc:
            assert "boom" in str(exc)
        else:
            raise AssertionError("expected RuntimeError to propagate")


# ---------------------------------------------------------------------------
# run_streaming — uses real subprocesses for integration coverage
# ---------------------------------------------------------------------------


class TestRunStreaming:
    def _python_argv(self, source: str) -> list[str]:
        return [sys.executable, "-c", source]

    def test_success_captures_stdout_and_streams_stderr(self, capsys):
        argv = self._python_argv(
            "import sys;\n"
            "print('this-is-stdout');\n"
            "print('build line A', file=sys.stderr);\n"
            "print('build line B', file=sys.stderr);\n"
        )
        result = run_streaming(argv, label="Working")
        assert isinstance(result, StreamedResult)
        assert result.returncode == 0
        assert "this-is-stdout" in result.stdout
        # Captured stderr text is preserved verbatim:
        assert "build line A" in result.stderr
        assert "build line B" in result.stderr
        # And was streamed to the console (rich -> stderr by default):
        err = capsys.readouterr().err
        assert "build line A" in err
        assert "build line B" in err

    def test_non_zero_returncode_propagated(self):
        argv = self._python_argv("import sys; sys.exit(7)")
        result = run_streaming(argv, label="X")
        assert result.returncode == 7
        assert result.error is None

    def test_stderr_only_subprocess_still_completes(self, capsys):
        argv = self._python_argv(
            "import sys; print('only-on-stderr', file=sys.stderr); sys.exit(0)"
        )
        result = run_streaming(argv, label="L")
        assert result.returncode == 0
        assert result.stdout == ""
        assert "only-on-stderr" in result.stderr
        assert "only-on-stderr" in capsys.readouterr().err

    def test_no_output_subprocess(self):
        argv = self._python_argv("pass")
        result = run_streaming(argv, label="L")
        assert result.returncode == 0
        assert result.stdout == ""
        assert result.stderr == ""

    def test_oserror_returns_error_field(self):
        result = run_streaming(
            ["/this/binary/does/not/exist/dcode-test"],
            label="L",
        )
        assert result.returncode == -1
        assert result.error is not None
        assert result.stdout == ""
        assert result.stderr == ""

    def test_env_passed_to_subprocess(self):
        argv = self._python_argv(
            "import os, sys; sys.stdout.write(os.environ.get('DCODE_TEST_VAR', ''))"
        )
        result = run_streaming(
            argv,
            label="L",
            env={"DCODE_TEST_VAR": "hello-progress", "PATH": ""},
        )
        assert result.returncode == 0
        assert result.stdout == "hello-progress"

    def test_cwd_passed_to_subprocess(self, tmp_path):
        argv = self._python_argv("import os, sys; sys.stdout.write(os.getcwd())")
        result = run_streaming(argv, label="L", cwd=str(tmp_path))
        assert result.returncode == 0
        assert tmp_path.name in result.stdout

    def test_markup_in_stderr_is_not_interpreted(self, capsys):
        # Ensure that stderr containing rich-like markup ("[red]error[/red]")
        # is printed verbatim, not parsed as rich markup (which would either
        # apply colour or raise on bad markup).
        argv = self._python_argv(
            "import sys; print('[red]should-not-be-styled[/red]', file=sys.stderr)"
        )
        result = run_streaming(argv, label="L")
        assert result.returncode == 0
        err = capsys.readouterr().err
        # The literal brackets should appear in the captured terminal output.
        assert "[red]should-not-be-styled[/red]" in err

    def test_uses_supplied_console(self, capsys):
        # The console argument should be the destination for streamed lines.
        from rich.console import Console

        # Create a console pointing at sys.stderr without forcing a TTY.
        c = Console(stderr=True, force_terminal=False, width=200, highlight=False)
        argv = self._python_argv(
            "import sys; print('via-supplied-console', file=sys.stderr)"
        )
        result = run_streaming(argv, label="L", console=c)
        assert result.returncode == 0
        err = capsys.readouterr().err
        assert "via-supplied-console" in err


# ---------------------------------------------------------------------------
# Mocked Popen — focused checks for thread/Live wiring
# ---------------------------------------------------------------------------


class TestRunStreamingMocked:
    def test_popen_invoked_with_pipes_and_text_mode(self):
        with patch("dcode._progress.subprocess.Popen") as popen:
            mock_proc = MagicMock()
            mock_proc.stdout.read.return_value = ""
            mock_proc.stderr.__iter__.return_value = iter([])
            mock_proc.wait.return_value = 0
            mock_proc.returncode = 0
            popen.return_value = mock_proc

            run_streaming(["fake-bin", "--flag"], label="L")

            args, kwargs = popen.call_args
            assert args[0] == ["fake-bin", "--flag"]
            assert kwargs["stdout"] == -1  # subprocess.PIPE sentinel
            assert kwargs["stderr"] == -1
            assert kwargs["text"] is True
            assert kwargs["bufsize"] == 1
