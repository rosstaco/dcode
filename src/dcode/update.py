"""Implementation of the ``dcode update`` subcommand."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys

from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text

import dcode

from . import version_check
from ._rich import get_console

_DCODE_LINE = re.compile(r"^dcode\s+v\d")


def detect_install_method() -> str:
    """Return one of ``"uv-tool"``, ``"not-uv-tool"``, ``"uv-missing"``, ``"unknown"``."""
    if shutil.which("uv") is None:
        return "uv-missing"
    try:
        result = subprocess.run(
            ["uv", "tool", "list"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    if result.returncode != 0:
        return "unknown"
    for line in result.stdout.splitlines():
        if _DCODE_LINE.match(line):
            return "uv-tool"
    return "not-uv-tool"


def run_update() -> int:
    """Driver for ``dcode update`` (no flags)."""
    method = detect_install_method()
    if method == "uv-missing":
        print('dcode update: "uv" is not installed or not on PATH', file=sys.stderr)
        print(
            "  install uv: https://docs.astral.sh/uv/getting-started/installation/",
            file=sys.stderr,
        )
        return 1
    if method == "not-uv-tool":
        print("dcode update: dcode is not installed via 'uv tool'", file=sys.stderr)
        print(
            "  re-install with: uv tool install git+https://github.com/rosstaco/dcode",
            file=sys.stderr,
        )
        print("  or upgrade via the original install method", file=sys.stderr)
        return 1
    if method == "unknown":
        print(
            "dcode update: could not detect install method; attempting upgrade anyway",
            file=sys.stderr,
        )
    # Stream subprocess output to user's terminal — no capture.
    result = subprocess.run(["uv", "tool", "upgrade", "dcode"], check=False)
    return result.returncode


def run_update_check(console: Console | None = None) -> int:
    """Driver for ``dcode update --check``."""
    cons = console or get_console()
    local = dcode.__version__
    try:
        info = version_check.get_latest_release()
    except version_check.NetworkError as exc:
        cons.print(f"[bold red]dcode update: could not reach github.com ({exc})[/]")
        return 2

    latest_tag = info["tag_name"]
    url = info["html_url"]
    cmp = version_check.compare_versions(local, latest_tag.lstrip("v"))
    _, local_is_dev = version_check.parse_version(local)

    if cmp < 0:
        local_style = "yellow"
        status_line = Text.from_markup(
            "[yellow]update available — run `dcode update`[/]"
        )
        rc = 1
    elif cmp > 0 or local_is_dev:
        local_style = "cyan"
        status_line = Text.from_markup("[cyan]ahead of the latest release[/]")
        rc = 0
    else:
        local_style = "green"
        status_line = Text.from_markup("[green]up to date[/]")
        rc = 0

    body = Group(
        Text.from_markup(f"[bold]local:[/]   [{local_style}]{local}[/]"),
        Text.from_markup(f"[bold]latest:[/]  [dim]{latest_tag}[/]"),
        Text.from_markup(f"[bold]release:[/] [dim][link={url}]{url}[/link][/]"),
        Text(""),
        status_line,
    )
    cons.print(
        Panel(
            body,
            title="dcode update",
            title_align="left",
            border_style="cyan",
            padding=(0, 1),
        )
    )
    return rc
