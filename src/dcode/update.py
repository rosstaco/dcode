"""Implementation of the ``dcode update`` subcommand."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys

import dcode

from . import version_check

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


def run_update_check() -> int:
    """Driver for ``dcode update --check``."""
    local = dcode.__version__
    try:
        info = version_check.get_latest_release()
    except version_check.NetworkError as exc:
        print(f"dcode update: could not reach github.com ({exc})", file=sys.stderr)
        return 2

    latest_tag = info["tag_name"]
    url = info["html_url"]
    cmp = version_check.compare_versions(local, latest_tag.lstrip("v"))

    print(f"local:   {local}", file=sys.stderr)
    print(f"latest:  {latest_tag}", file=sys.stderr)
    print(f"release: {url}", file=sys.stderr)

    if cmp < 0:
        print("update available — run `dcode update`", file=sys.stderr)
        return 1
    # Equal-or-ahead: distinguish dev builds (same or higher numeric prefix
    # but with a dev/post suffix) from a clean released build.
    _, local_is_dev = version_check.parse_version(local)
    if cmp > 0 or local_is_dev:
        print("dcode is ahead of the latest release", file=sys.stderr)
        return 0
    print("dcode is up to date", file=sys.stderr)
    return 0
