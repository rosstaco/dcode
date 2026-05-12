"""Reusable progress UX: a pinned spinner with optional live log streaming.

Built on rich's :class:`~rich.console.Console.status` (a thin wrapper around
:class:`~rich.live.Live`). Any call to ``console.print()`` while a status is
active is rendered **above** the live region, so stderr lines from a child
process can scroll past while the spinner stays pinned at the bottom of the
terminal.

Two entry points:

* :func:`with_spinner` — bare context manager for short, non-streaming work.
* :func:`run_streaming` — runs a subprocess and forwards its stderr lines
  to the console live, capturing both stdout (for downstream parsing) and
  the full stderr text (for failure-mode reporting).

Both gracefully degrade on non-TTY consoles: the spinner becomes a one-shot
status line and stderr forwarding still works as plain output.
"""

from __future__ import annotations

import contextlib
import subprocess
import threading
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import IO

from rich.console import Console

from dcode._rich import get_console


@dataclass(frozen=True, slots=True)
class StreamedResult:
    """Outcome of :func:`run_streaming`.

    ``stderr`` is the same text that was streamed live to the console,
    captured in full so callers can inspect it without re-running the
    subprocess.
    """

    returncode: int
    stdout: str
    stderr: str
    error: str | None = None  # populated when the subprocess failed to launch


@contextlib.contextmanager
def with_spinner(
    label: str,
    *,
    console: Console | None = None,
    spinner: str = "dots",
) -> Iterator[None]:
    """Show a rich spinner with *label* for the duration of the block.

    On a TTY the spinner animates at the bottom of the terminal and any
    ``console.print()`` calls during the block scroll above it. On non-TTY
    it degrades to a one-shot status line.
    """
    cons = console or get_console()
    with cons.status(label, spinner=spinner):
        yield


def run_streaming(
    argv: list[str],
    *,
    label: str,
    console: Console | None = None,
    env: Mapping[str, str] | None = None,
    cwd: str | None = None,
) -> StreamedResult:
    """Run *argv* and stream its stderr above a pinned spinner.

    Both streams are captured: stdout in full (for downstream parsing) and
    stderr both live-streamed to the console and captured in full. The
    spinner is pinned to the bottom of the terminal via rich's Live display
    while stderr lines scroll above it.

    On any pre-launch failure (``OSError`` / missing executable) returns a
    :class:`StreamedResult` with ``returncode=-1`` and ``error`` set.
    """
    cons = console or get_console()

    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # line-buffered
            env=dict(env) if env is not None else None,
            cwd=cwd,
        )
    except (FileNotFoundError, OSError) as exc:
        return StreamedResult(
            returncode=-1,
            stdout="",
            stderr="",
            error=str(exc),
        )

    captured_stderr: list[str] = []

    def _pump_stderr(stream: IO[str]) -> None:
        # While the rich Status (Live) is active, console.print routes the
        # output above the spinner so it scrolls naturally.
        for raw_line in stream:
            captured_stderr.append(raw_line)
            line = raw_line.rstrip("\r\n")
            # markup=False prevents user text like "[ERROR]" from being
            # interpreted as rich markup; highlight=False keeps the colour
            # palette quiet.
            cons.print(line, markup=False, highlight=False, emoji=False)

    pump_thread: threading.Thread | None = None
    try:
        with cons.status(label, spinner="dots"):
            assert proc.stderr is not None  # noqa: S101 - PIPE configured above
            assert proc.stdout is not None  # noqa: S101
            pump_thread = threading.Thread(
                target=_pump_stderr,
                args=(proc.stderr,),
                daemon=True,
            )
            pump_thread.start()
            # Read stdout to completion; both pipes will be drained by the
            # time the process exits, then we wait for the OS-level reap.
            stdout_text = proc.stdout.read()
            proc.wait()
            pump_thread.join(timeout=5)
    except KeyboardInterrupt:
        # Best-effort terminate so we don't leave the child hanging.
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        if pump_thread is not None:
            pump_thread.join(timeout=2)
        raise

    return StreamedResult(
        returncode=proc.returncode,
        stdout=stdout_text or "",
        stderr="".join(captured_stderr),
    )


__all__ = ["StreamedResult", "run_streaming", "with_spinner"]
