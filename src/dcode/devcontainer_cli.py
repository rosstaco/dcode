"""Helpers for the official ``@devcontainers/cli`` (the ``devcontainer`` CLI).

Used by ``dcode shell`` to build & start a devcontainer on demand when the
project has no running container yet. The CLI is the same Node.js app VS
Code's Dev Containers extension drives under the hood, so containers it
creates carry the same ``devcontainer.local_folder`` /
``devcontainer.config_file`` / ``devcontainer.metadata`` labels VS Code
expects.

Three entry points:

* :func:`find_cli` — locate the ``devcontainer`` binary (``$PATH`` or the
  default install location ``~/.devcontainers/bin/devcontainer``).
* :func:`install_cli` — download the upstream ``install.sh`` and run it to
  drop a self-contained CLI (bundled Node runtime) into ``~/.devcontainers``.
* :func:`up` — run ``devcontainer up`` against a workspace and parse the
  JSON result for the new container id.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from rich.console import Console

from dcode import _progress
from dcode._rich import get_console

INSTALL_SCRIPT_URL = (
    "https://raw.githubusercontent.com/devcontainers/cli/main/scripts/install.sh"
)
DEFAULT_INSTALL_PREFIX = Path.home() / ".devcontainers"


def find_cli() -> Path | None:
    """Locate a usable ``devcontainer`` binary.

    Checks ``$PATH`` first, then the default install location used by the
    upstream ``install.sh`` (``~/.devcontainers/bin/devcontainer``). Returns
    ``None`` when neither is present or executable.
    """
    on_path = shutil.which("devcontainer")
    if on_path:
        return Path(on_path)
    fallback = DEFAULT_INSTALL_PREFIX / "bin" / "devcontainer"
    if fallback.is_file() and os.access(fallback, os.X_OK):
        return fallback
    return None


def cli_version(cli_path: Path) -> str | None:
    """Return the ``devcontainer --version`` string, or ``None`` on failure."""
    try:
        proc = subprocess.run(
            [str(cli_path), "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    out = proc.stdout.strip()
    return out or None


def install_cli(
    *,
    prefix: Path | None = None,
    console: Console | None = None,
) -> Path | None:
    """Download and run the official Dev Containers CLI install script.

    The upstream script bundles its own Node.js runtime, so no host Node is
    required. Defaults to ``~/.devcontainers``; pass ``prefix`` to override.
    Prints status to *console* (defaults to the dcode stderr console). On
    success returns the absolute path to the installed binary; on any
    failure returns ``None`` after printing a hint.
    """
    cons = console or get_console()
    install_prefix = prefix or DEFAULT_INSTALL_PREFIX

    cons.print(
        f"dcode: downloading Dev Containers CLI installer from {INSTALL_SCRIPT_URL}",
        highlight=False,
    )

    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            suffix=".sh",
            delete=False,
        ) as tmp:
            tmp_path = Path(tmp.name)
            try:
                with urllib.request.urlopen(INSTALL_SCRIPT_URL, timeout=30) as resp:
                    shutil.copyfileobj(resp, tmp)
            except (urllib.error.URLError, OSError) as exc:
                cons.print(
                    f"[red]dcode: failed to download installer ({exc})[/]",
                    highlight=False,
                )
                return None

        cmd = ["sh", str(tmp_path), "--prefix", str(install_prefix)]
        result = _progress.run_streaming(
            cmd,
            label=f"Installing Dev Containers CLI into {install_prefix}...",
            console=cons,
        )
        if result.error is not None:
            cons.print(
                f"[red]dcode: failed to run installer: {result.error}[/]",
                highlight=False,
            )
            return None
        if result.returncode != 0:
            cons.print(
                f"[red]dcode: Dev Containers CLI install failed "
                f"(exit {result.returncode}) — see output above[/]",
                highlight=False,
            )
            return None

    finally:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink()

    binary = install_prefix / "bin" / "devcontainer"
    if not (binary.is_file() and os.access(binary, os.X_OK)):
        cons.print(
            f"[red]dcode: installer reported success but {binary} is not "
            "an executable file[/]",
            highlight=False,
        )
        return None

    cons.print(
        f"[green]dcode: installed Dev Containers CLI at {binary}[/]",
        highlight=False,
    )
    return binary


def up(
    cli_path: Path,
    workspace_folder: Path,
    config_path: Path,
    *,
    console: Console | None = None,
) -> tuple[str | None, str]:
    """Build & start a devcontainer with ``devcontainer up``.

    Runs ``<cli> up --workspace-folder <workspace> --config <config>`` while
    streaming the build's stderr live above a pinned spinner (via
    :func:`dcode._progress.run_streaming`). devcontainers/cli writes one
    final ``JSON.stringify(result)`` line to stdout when finished; we parse
    that for ``containerId``.

    Returns ``(container_id, "")`` on success or ``(None, error_summary)``
    on failure. ``error_summary`` is just the JSON ``description``/``message``
    from the CLI when present — the full stderr was already shown live so
    we don't dump it again. If no diagnostic is available, falls back to a
    one-line "exit code N" message.
    """
    cons = console or get_console()
    argv = [
        str(cli_path),
        "up",
        "--workspace-folder",
        str(workspace_folder),
        "--config",
        str(config_path),
    ]

    result = _progress.run_streaming(
        argv,
        label="Building devcontainer (this may take several minutes)...",
        console=cons,
    )
    if result.error is not None:
        return (None, f"failed to launch devcontainer CLI: {result.error}")

    parsed = _parse_up_result(result.stdout)

    if result.returncode == 0 and parsed is not None:
        outcome = parsed.get("outcome")
        container_id = parsed.get("containerId")
        if outcome == "success" and isinstance(container_id, str) and container_id:
            return (container_id, "")
        if isinstance(container_id, str) and container_id and outcome != "error":
            return (container_id, "")

    # Failure path — surface the most useful concise diagnostic. The full
    # stderr stream was already printed live above the spinner, so we keep
    # this short.
    if parsed is not None:
        for key in ("description", "message"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                return (None, value.strip())

    return (
        None,
        f"devcontainer up exited with code {result.returncode} "
        "(see output above for details)",
    )


def _parse_up_result(stdout: str) -> dict | None:
    """Parse the JSON ``devcontainer up`` result from *stdout*.

    devcontainers/cli writes one JSON object as the final stdout line.
    Defensively try the whole stripped stdout first (covers the common case
    of clean stdout), then fall back to the last non-empty line.
    """
    text = (stdout or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        for line in reversed(text.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                break
            except ValueError:
                continue
        else:
            return None
    return data if isinstance(data, dict) else None


def install_hint() -> str:
    """Return a multi-line hint string with install instructions for the CLI."""
    return (
        "install Dev Containers CLI:\n"
        f"    curl -fsSL {INSTALL_SCRIPT_URL} | sh\n"
        "  or, if you have Node.js:\n"
        "    npm install -g @devcontainers/cli\n"
        "  then ensure ~/.devcontainers/bin (or your npm bin) is on PATH"
    )


__all__ = [
    "DEFAULT_INSTALL_PREFIX",
    "INSTALL_SCRIPT_URL",
    "cli_version",
    "find_cli",
    "install_cli",
    "install_hint",
    "up",
]
