"""Implementation of the ``dcode doctor`` subcommand.

Diagnoses the local environment (editor, container runtime, git, WSL setup,
devcontainer in target, dcode version, install method) and prints a
"what would `dcode <path>` do" plan summary. Read-only — never patches
settings.json or spawns the editor.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import json5

import dcode
from dcode import update, version_check
from dcode.core import (
    build_uri,
    find_devcontainer,
    get_workspace_folder,
    resolve_worktree,
)
from dcode.wsl import (
    _get_windows_vscode_settings_path,
    _wsl_to_windows_path,
    build_uri_wsl,
    get_wsl_distro,
    is_wsl,
)

# (status, message, hint)
CheckResult = tuple[str, str, str | None]

_DEV_CONTAINERS_EXT = "ms-vscode-remote.remote-containers"


# ---------------------------------------------------------------------------
# Per-check functions
# ---------------------------------------------------------------------------


def check_editor() -> CheckResult:
    code = shutil.which("code")
    insiders = shutil.which("code-insiders")
    if code and insiders:
        return ("ok", "VS Code editor: code, code-insiders", None)
    if code and not insiders:
        return (
            "warn",
            "VS Code editor: code (code-insiders not on PATH)",
            'install VS Code Insiders or run "Shell Command: Install \'code-insiders\' '
            'command" from the Command Palette',
        )
    if insiders and not code:
        return (
            "warn",
            "VS Code editor: code-insiders (code not on PATH)",
            'install VS Code or run "Shell Command: Install \'code\' command in PATH" '
            "from the Command Palette (macOS)",
        )
    return (
        "fail",
        "VS Code editor: neither code nor code-insiders on PATH",
        "install VS Code (https://code.visualstudio.com/) and run "
        "\"Shell Command: Install 'code' command in PATH\" from the Command Palette (macOS)",
    )


def _editors_present() -> list[str]:
    return [name for name in ("code", "code-insiders") if shutil.which(name)]


def check_extension() -> CheckResult:
    editors = _editors_present()
    if not editors:
        return ("skip", "Dev Containers extension: no editor available", None)

    missing: list[str] = []
    failed: list[str] = []
    ok_in: list[str] = []
    for editor in editors:
        try:
            result = subprocess.run(
                [editor, "--list-extensions"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            failed.append(editor)
            continue
        if result.returncode != 0:
            failed.append(editor)
            continue
        exts = {line.strip() for line in result.stdout.splitlines() if line.strip()}
        if _DEV_CONTAINERS_EXT in exts:
            ok_in.append(editor)
        else:
            missing.append(editor)

    if missing:
        return (
            "fail",
            f"Dev Containers extension: {_DEV_CONTAINERS_EXT} missing in {', '.join(missing)}",
            f'install via "{missing[0]} --install-extension {_DEV_CONTAINERS_EXT}"',
        )
    if failed and not ok_in:
        return (
            "warn",
            f"Dev Containers extension: could not list extensions for {', '.join(failed)}",
            f'try "{failed[0]} --list-extensions" manually to see why it failed',
        )
    if failed:
        return (
            "warn",
            f"Dev Containers extension: present in {', '.join(ok_in)}; "
            f"could not list for {', '.join(failed)}",
            f'try "{failed[0]} --list-extensions" manually to see why it failed',
        )
    return (
        "ok",
        f"Dev Containers extension: {_DEV_CONTAINERS_EXT} ({', '.join(ok_in)})",
        None,
    )


def check_docker() -> CheckResult:
    if shutil.which("docker") is None:
        return (
            "warn",
            "Container runtime: docker CLI not on PATH "
            "(Podman or Rancher Desktop may still work)",
            "install Docker Desktop, OrbStack, Podman, or Rancher Desktop",
        )
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return (
            "fail",
            f"Container runtime: docker info failed ({exc})",
            "start Docker Desktop, OrbStack, or your container engine and re-run",
        )
    if result.returncode != 0:
        return (
            "fail",
            "Container runtime: docker CLI present but daemon is not reachable",
            "start Docker Desktop, OrbStack, or your container engine and re-run",
        )
    version = result.stdout.strip() or "unknown"
    return ("ok", f"Container runtime: docker daemon reachable ({version})", None)


def check_git() -> CheckResult:
    git = shutil.which("git")
    if git is None:
        return (
            "warn",
            "git: not on PATH (worktree detection will be skipped)",
            'install git (e.g. "brew install git" or "apt install git")',
        )
    return ("ok", f"git: {git}", None)


def check_wsl() -> CheckResult:
    if not is_wsl():
        return ("ok", "WSL: not running in WSL (skipping WSL-specific checks)", None)
    return ("ok", "WSL: detected", None)


def check_wsl_distro() -> CheckResult:
    distro = get_wsl_distro()
    if distro:
        return ("ok", f"WSL distro: {distro}", None)
    return (
        "warn",
        "WSL distro: WSL_DISTRO_NAME not set",
        "dcode cannot auto-set dev.containers.executeInWSLDistro without "
        "WSL_DISTRO_NAME — set it in your shell rc",
    )


def check_wsl_settings_paths() -> list[CheckResult]:
    results: list[CheckResult] = []
    for insiders, label in ((False, "code"), (True, "code-insiders")):
        path = _get_windows_vscode_settings_path(insiders=insiders)
        if path is None:
            results.append(
                (
                    "warn",
                    f"WSL settings: cannot resolve Windows-side settings.json for {label}",
                    "ensure cmd.exe and wslpath are available; "
                    '"echo %APPDATA%" must return a Windows path under WSL',
                )
            )
        else:
            results.append(("ok", f"WSL settings ({label}): {path}", None))
    return results


def check_wsl_executeInWSL_settings() -> list[CheckResult]:
    distro = get_wsl_distro()
    results: list[CheckResult] = []
    for insiders, label in ((False, "code"), (True, "code-insiders")):
        path = _get_windows_vscode_settings_path(insiders=insiders)
        if path is None or not path.is_file():
            continue
        try:
            text = path.read_text()
            parsed = json5.loads(text) if text.strip() else {}
        except (OSError, ValueError) as exc:
            results.append(
                (
                    "warn",
                    f"WSL devcontainer settings ({label}): failed to parse {path} ({exc})",
                    "dcode auto-patches these on launch; or set them manually in settings.json",
                )
            )
            continue
        if not isinstance(parsed, dict):
            parsed = {}
        exec_in_wsl = parsed.get("dev.containers.executeInWSL")
        exec_distro = parsed.get("dev.containers.executeInWSLDistro")
        problems = []
        if exec_in_wsl is not True:
            problems.append(f"executeInWSL is {exec_in_wsl!r}, expected true")
        if distro and exec_distro != distro:
            problems.append(
                f"executeInWSLDistro is {exec_distro!r}, expected {distro!r}"
            )
        if problems:
            results.append(
                (
                    "warn",
                    f"WSL devcontainer settings ({label}): {'; '.join(problems)} "
                    '(will auto-fix on next "dcode <path>")',
                    "dcode auto-patches these on launch; or set them manually in settings.json",
                )
            )
        else:
            distro_part = f", executeInWSLDistro={distro}" if distro else ""
            results.append(
                (
                    "ok",
                    f"WSL devcontainer settings ({label}): executeInWSL=true{distro_part}",
                    None,
                )
            )
    return results


def check_devcontainer(target: Path) -> CheckResult:
    worktree = resolve_worktree(target)
    if worktree is not None:
        main_repo, _ = worktree
        devcontainer = find_devcontainer(main_repo)
        if devcontainer:
            return ("ok", f"devcontainer: {devcontainer}", None)
        return (
            "warn",
            f"devcontainer: none in main repo ({main_repo}) — "
            "dcode will fall back to opening the directory directly",
            "add .devcontainer/devcontainer.json to enable container support",
        )
    devcontainer = find_devcontainer(target)
    if devcontainer:
        return ("ok", f"devcontainer: {devcontainer}", None)
    return (
        "warn",
        f"devcontainer: none found in {target} — "
        "dcode will open the folder directly without a container",
        "add .devcontainer/devcontainer.json to enable container support",
    )


def check_devcontainer_parses(target: Path) -> CheckResult:
    worktree = resolve_worktree(target)
    if worktree is not None:
        main_repo, _ = worktree
        devcontainer = find_devcontainer(main_repo)
    else:
        devcontainer = find_devcontainer(target)
    if devcontainer is None:
        return ("skip", "devcontainer.json: no file to parse", None)
    try:
        parsed = json5.loads(devcontainer.read_text())
    except (OSError, ValueError) as exc:
        return (
            "fail",
            f"devcontainer.json: parse error ({exc})",
            "validate the file with \"json5\" or \"node -e require('json5')\"",
        )
    if not isinstance(parsed, dict):
        return ("warn", "devcontainer.json: top-level is not an object", None)
    return ("ok", "devcontainer.json: parses cleanly", None)


def check_worktree(target: Path) -> CheckResult:
    git_file = target / ".git"
    if git_file.is_dir():
        return ("ok", "worktree: target is a regular git repo (or non-repo)", None)
    if git_file.is_file():
        worktree = resolve_worktree(target)
        if worktree is not None:
            main_repo, _ = worktree
            return ("ok", f"worktree: detected; main repo at {main_repo}", None)
        return (
            "warn",
            f"worktree: {target} looks like a worktree or submodule but cannot be "
            "resolved (external worktree or submodule)",
            "dcode opens this path directly without shared-container support",
        )
    return ("ok", "worktree: not a git repo", None)


def check_version() -> CheckResult:
    local = dcode.__version__
    try:
        info = version_check.get_latest_release()
    except version_check.NetworkError as exc:
        return (
            "warn",
            f"dcode version: cannot reach GitHub API ({exc})",
            're-run when online; or skip with "dcode update --check"',
        )
    latest_tag = info["tag_name"]
    url = info["html_url"]
    try:
        cmp = version_check.compare_versions(local, latest_tag.lstrip("v"))
    except ValueError:
        return ("warn", f"dcode version: cannot parse local version {local!r}", None)
    if cmp < 0:
        return (
            "warn",
            f"dcode version: {local} installed; latest is {latest_tag} ({url})",
            'run "dcode update" to upgrade',
        )
    try:
        _, local_is_dev = version_check.parse_version(local)
    except ValueError:
        local_is_dev = False
    if cmp > 0 or local_is_dev:
        return (
            "ok",
            f"dcode version: {local} (ahead of latest release {latest_tag})",
            None,
        )
    return ("ok", f"dcode version: {local} (latest)", None)


def check_install_method() -> CheckResult:
    method = update.detect_install_method()
    if method == "uv-tool":
        return (
            "ok",
            'install method: uv tool (upgradable via "dcode update")',
            None,
        )
    if method == "uv-missing":
        return (
            "warn",
            "install method: uv not on PATH; cannot detect or upgrade automatically",
            'install uv (https://docs.astral.sh/uv/) to enable "dcode update"',
        )
    if method == "not-uv-tool":
        return (
            "warn",
            'install method: dcode is not installed via "uv tool" — '
            '"dcode update" will not work',
            "re-install via \"uv tool install git+https://github.com/rosstaco/dcode\" "
            "to use dcode update",
        )
    return (
        "warn",
        'install method: could not run "uv tool list"',
        None,
    )


# ---------------------------------------------------------------------------
# Plan summary
# ---------------------------------------------------------------------------


def _render_wsl_settings_preview(*, insiders: bool) -> None:
    settings_path = _get_windows_vscode_settings_path(insiders=insiders)
    if settings_path is None:
        print(
            "  WSL settings: cannot resolve Windows-side settings.json "
            "(no patch will be attempted)",
            file=sys.stderr,
        )
        return
    distro = get_wsl_distro()
    desired: dict = {"dev.containers.executeInWSL": True}
    if distro:
        desired["dev.containers.executeInWSLDistro"] = distro

    settings: dict = {}
    if settings_path.is_file():
        try:
            text = settings_path.read_text()
            parsed = json5.loads(text) if text.strip() else {}
            if isinstance(parsed, dict):
                settings = parsed
        except (OSError, ValueError) as exc:
            print(
                f"  WSL settings: cannot parse {settings_path} ({exc}) — "
                "would print hint instead of patching",
                file=sys.stderr,
            )
            return
    pending = {k: v for k, v in desired.items() if settings.get(k) != v}
    if not pending:
        print(
            f"  WSL settings: {settings_path} already correct — no patch needed",
            file=sys.stderr,
        )
    else:
        diff = ", ".join(f'"{k}": {json.dumps(v)}' for k, v in pending.items())
        print(
            f"  WSL settings: would patch {settings_path} to set {{{diff}}}",
            file=sys.stderr,
        )


def render_plan(target: Path, code_present: bool, insiders_present: bool) -> None:
    """Print the read-only `Plan for <target>:` section to stderr."""
    target = target.resolve()

    # Editor selection (smart fallback per intent #1)
    if not code_present and not insiders_present:
        print("  no editor available — skipping plan", file=sys.stderr)
        return
    if code_present:
        editor = "code"
        extra_note = (
            "  also available: `dcode -i <path>` would use code-insiders"
            if insiders_present
            else None
        )
    else:
        editor = "code-insiders"
        extra_note = '  (showing code-insiders plan; "code" not on PATH)'

    worktree = resolve_worktree(target)
    if worktree is not None:
        main_repo, rel_path = worktree
        devcontainer = find_devcontainer(main_repo)
    else:
        main_repo, rel_path = None, None
        devcontainer = find_devcontainer(target)

    # No-devcontainer branch
    if devcontainer is None:
        if main_repo is not None:
            print(
                f"  Detected git worktree (main repo: {main_repo}).",
                file=sys.stderr,
            )
            print(
                f"  No devcontainer found in main repo — would open {target} in "
                f"{editor} directly (no container).",
                file=sys.stderr,
            )
        else:
            git_file = target / ".git"
            if git_file.is_file():
                print(
                    f"  {target} looks like a worktree or submodule but cannot "
                    "be resolved.",
                    file=sys.stderr,
                )
                print(
                    f"  Would open {target} in {editor} directly without "
                    "shared-container support.",
                    file=sys.stderr,
                )
            else:
                print(
                    f"  No devcontainer found — would open {target} in {editor} "
                    "directly.",
                    file=sys.stderr,
                )
        if extra_note:
            print(extra_note, file=sys.stderr)
        return

    # Devcontainer branch
    if main_repo is not None:
        host_path = str(main_repo)
        base = get_workspace_folder(devcontainer, main_repo)
        workspace_folder = f"{base}/{rel_path.as_posix()}"
        print("  Detected git worktree.", file=sys.stderr)
        print(
            f"  Would open the MAIN repo at {main_repo} so all worktrees share "
            "one container.",
            file=sys.stderr,
        )
        print(f"  editor: {editor}", file=sys.stderr)
        print(f"  host path: {host_path}", file=sys.stderr)
        print(f"  effective workspaceFolder: {workspace_folder} (= {base} + /{rel_path.as_posix()})", file=sys.stderr)
    else:
        host_path = str(target)
        workspace_folder = get_workspace_folder(devcontainer, target)
        print(f"  editor: {editor}", file=sys.stderr)
        print(f"  host path: {host_path}", file=sys.stderr)
        print(f"  effective workspaceFolder: {workspace_folder}", file=sys.stderr)

    print(f"  devcontainer config path: {devcontainer}", file=sys.stderr)

    if is_wsl():
        win_path = _wsl_to_windows_path(host_path)
        print(f"  Windows UNC path: {win_path}", file=sys.stderr)
        uri = build_uri_wsl(host_path, workspace_folder)
        print(f"  URI: {uri}", file=sys.stderr)
        _render_wsl_settings_preview(insiders=(editor == "code-insiders"))
    else:
        uri = build_uri(host_path, workspace_folder)
        print(f"  URI: {uri}", file=sys.stderr)

    if extra_note:
        print(extra_note, file=sys.stderr)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def _print_result(result: CheckResult) -> None:
    status, message, hint = result
    print(f"{status:4s}  {message}", file=sys.stderr)
    if hint and status in ("warn", "fail"):
        print(f"      hint: {hint}", file=sys.stderr)


def run_doctor(path: Path) -> int:
    """Run all doctor checks against *path* and print the plan summary."""
    target = path.resolve()
    code_present = shutil.which("code") is not None
    insiders_present = shutil.which("code-insiders") is not None

    results: list[CheckResult] = []

    def run(fn, *args):
        result = fn(*args)
        results.append(result)
        _print_result(result)
        return result

    run(check_editor)
    run(check_extension)
    run(check_docker)
    run(check_git)

    run(check_wsl)
    if is_wsl():
        run(check_wsl_distro)
        for r in check_wsl_settings_paths():
            results.append(r)
            _print_result(r)
        for r in check_wsl_executeInWSL_settings():
            results.append(r)
            _print_result(r)

    run(check_devcontainer, target)
    run(check_devcontainer_parses, target)
    run(check_worktree, target)
    run(check_version)
    run(check_install_method)

    n_ok = sum(1 for r in results if r[0] == "ok")
    n_warn = sum(1 for r in results if r[0] == "warn")
    n_fail = sum(1 for r in results if r[0] == "fail")

    print(f"\ndcode doctor: {n_ok} ok, {n_warn} warn, {n_fail} fail", file=sys.stderr)

    print(f"\nPlan for {target}:", file=sys.stderr)
    try:
        render_plan(target, code_present, insiders_present)
    except Exception as exc:  # noqa: BLE001 - render errors must not affect exit code
        print(f"  warn: plan summary failed ({exc})", file=sys.stderr)

    return 0 if n_fail == 0 else 1
