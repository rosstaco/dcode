"""Internal rich rendering helpers shared by ``doctor`` and ``update``.

Centralizes the :class:`rich.console.Console` setup so both subcommands route
their output through one place. ``Console`` already respects ``NO_COLOR`` and
auto-detects TTY, so callers do not need to do anything special.
"""

from __future__ import annotations

import sys

from rich.console import Console

# status -> (glyph, rich style)
STATUS_STYLES: dict[str, tuple[str, str]] = {
    "ok": ("✓", "green"),
    "warn": ("⚠", "yellow"),
    "fail": ("✗", "bold red"),
    "skip": ("-", "dim"),
    "info": ("•", "cyan"),
}


def get_console() -> Console:
    """Return a stderr-targeted :class:`Console`.

    On a TTY, rich auto-detects width and color support (and respects
    ``NO_COLOR``). When piped to a non-TTY (tests, ``| cat``, etc.) we pin
    the width wide enough that long URIs and paths do not soft-wrap and
    break substring assertions.
    """
    if sys.stderr.isatty():
        return Console(stderr=True, highlight=False)
    return Console(stderr=True, highlight=False, width=200, force_terminal=False)


def status_markup(status: str, message: str) -> str:
    """Return a rich-markup string for a single check line."""
    glyph, style = STATUS_STYLES.get(status, STATUS_STYLES["info"])
    return f"[{style}]{glyph}[/] {message}"
