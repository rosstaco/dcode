"""WSL-specific helpers for dcode."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import json5


def is_wsl() -> bool:
    """Detect if running inside WSL."""
    proc_version = Path("/proc/version")
    if not proc_version.exists():
        return False
    try:
        return "microsoft" in proc_version.read_text().lower()
    except OSError:
        return False


def get_wsl_distro() -> str | None:
    """Get the current WSL distro name."""
    return os.environ.get("WSL_DISTRO_NAME")


def _wsl_to_windows_path(linux_path: str) -> str:
    """Convert a WSL Linux path to a Windows UNC path."""
    try:
        result = subprocess.run(
            ["wslpath", "-w", linux_path],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if result.returncode == 0:
            win_path = result.stdout.strip()
            if win_path:
                return win_path
    except (OSError, subprocess.TimeoutExpired):
        pass
    # Fallback: construct UNC path manually
    distro = get_wsl_distro() or "Ubuntu"
    return f"\\\\wsl.localhost\\{distro}{linux_path}"


def build_uri_wsl(host_path: str, workspace_folder: str) -> str:
    """Build the URI with Windows UNC path so VS Code finds the WSL folder."""
    win_path = _wsl_to_windows_path(host_path)
    payload = json.dumps({"hostPath": win_path}, separators=(",", ":"))
    hex_payload = payload.encode().hex()
    return f"vscode-remote://dev-container+{hex_payload}{workspace_folder}"


def _get_windows_vscode_settings_path(insiders: bool = False) -> Path | None:
    """Find the Windows-side VS Code settings.json from WSL."""
    try:
        result = subprocess.run(
            ["cmd.exe", "/C", "echo", "%APPDATA%"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        appdata_win = result.stdout.strip()
        if not appdata_win or "%" in appdata_win:
            return None
        # Convert Windows path to WSL path
        result = subprocess.run(
            ["wslpath", "-u", appdata_win],
            capture_output=True, text=True, timeout=5, check=False,
        )
        appdata_wsl = result.stdout.strip()
        if not appdata_wsl:
            return None
        code_dir = "Code - Insiders" if insiders else "Code"
        return Path(appdata_wsl) / code_dir / "User" / "settings.json"
    except (OSError, subprocess.TimeoutExpired):
        return None


# Matches a top-level JSONC object: optional leading whitespace/comments,
# then '{', then arbitrary content, then a final '}' (greedy to last brace).
_TOP_LEVEL_OBJECT_RE = re.compile(r"^(\s*(?://[^\n]*\n|/\*.*?\*/|\s)*)\{(.*)\}(\s*)\Z", re.DOTALL)


def _format_jsonc_value(value: object) -> str:
    """Format a Python value as a JSON literal suitable for embedding in JSONC."""
    return json.dumps(value)


def _patch_jsonc_settings(text: str, patches: dict) -> str | None:
    """Apply ``patches`` to a JSONC ``settings.json`` text in-place.

    For each key in ``patches``: replace the value of an existing top-level
    ``"key":`` entry, or insert a new entry just before the closing ``}``.
    Preserves comments, trailing commas, and indentation.

    Returns the patched text, or ``None`` if the input doesn't look like a
    single top-level JSON object (in which case the caller should fall back
    rather than risk corrupting the file).
    """
    match = _TOP_LEVEL_OBJECT_RE.match(text)
    if match is None:
        return None

    result = text
    for key, value in patches.items():
        literal = _format_jsonc_value(value)
        # Replace existing top-level key. We require the key to start a line
        # (after optional whitespace) to avoid matching keys inside nested
        # objects or strings. Value match handles strings, bools, numbers,
        # null — not nested objects/arrays (we only patch scalar settings).
        key_pattern = re.compile(
            r'(?m)^(?P<indent>[ \t]*)"' + re.escape(key)
            + r'"(?P<sep>\s*:\s*)(?P<value>"(?:\\.|[^"\\])*"|true|false|null|-?\d+(?:\.\d+)?)'
        )
        new_result, n = key_pattern.subn(
            lambda m, lit=literal, k=key: f'{m.group("indent")}"{k}"{m.group("sep")}{lit}',
            result,
            count=1,
        )
        if n:
            result = new_result
            continue

        # Key not present — insert before the final closing '}'.
        insert_match = re.search(r"(?P<lastline>[^\n]*)\}(?P<trailing>\s*)\Z", result, re.DOTALL)
        if insert_match is None:
            return None
        # Detect indent from the line before the closing brace, or default to 4 spaces.
        last_line = insert_match.group("lastline")
        indent_match = re.match(r"[ \t]*", last_line)
        indent = indent_match.group(0) if indent_match else "    "
        if not indent:
            indent = "    "
        # Find the position of the final '}' to insert before it.
        close_idx = result.rfind("}")
        before = result[:close_idx].rstrip()
        after = result[close_idx:]
        # Add a trailing comma to the previous entry if it doesn't already have one.
        sep = "" if before.rstrip().endswith(("{", ",")) else ","
        new_entry = f'{sep}\n{indent}"{key}": {literal}\n'
        result = before + new_entry + after

    return result


def _ensure_wsl_docker_settings(insiders: bool = False) -> None:
    """Auto-configure VS Code to use Docker from WSL if not already set."""
    settings_path = _get_windows_vscode_settings_path(insiders)
    if settings_path is None:
        _print_wsl_hint()
        return

    distro = get_wsl_distro()

    desired: dict = {"dev.containers.executeInWSL": True}
    if distro:
        desired["dev.containers.executeInWSLDistro"] = distro

    # Read existing settings text + parsed form (for comparison only).
    existing_text = ""
    settings: dict = {}
    if settings_path.is_file():
        try:
            existing_text = settings_path.read_text()
            parsed = json5.loads(existing_text) if existing_text.strip() else {}
            if isinstance(parsed, dict):
                settings = parsed
        except (OSError, ValueError):
            _print_wsl_hint()
            return

    # Determine which patches actually need to be written.
    patches = {k: v for k, v in desired.items() if settings.get(k) != v}
    if not patches:
        return

    if existing_text.strip():
        new_text = _patch_jsonc_settings(existing_text, patches)
        if new_text is None:
            _print_wsl_hint()
            return
    else:
        # No file (or empty): write a fresh JSON object.
        new_text = json.dumps(desired, indent=4) + "\n"

    try:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(new_text)
        suffix = f" ({distro})" if distro else ""
        print(
            f"dcode: configured VS Code to use Docker from WSL{suffix}",
            file=sys.stderr,
        )
    except OSError:
        _print_wsl_hint()


def _print_wsl_hint() -> None:
    """Print manual instructions as fallback."""
    print(
        "hint: if VS Code uses docker.exe instead of WSL docker, enable these VS Code settings:\n"
        '  "dev.containers.executeInWSL": true\n'
        '  "dev.containers.executeInWSLDistro": "<your-distro>"',
        file=sys.stderr,
    )
