"""dcode shell: exec into a running devcontainer.

Locates the container by the Docker labels that ``devcontainers/cli`` sets
(``devcontainer.local_folder`` and ``devcontainer.config_file``), resolves a
shell using VS Code's settings priority chain, forwards the SSH agent socket
when available, then ``os.execvp``s into ``docker exec -it [...] <shell>``.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import json5

from dcode.core import find_devcontainer, get_workspace_folder, resolve_worktree
from dcode.wsl import _wsl_to_windows_path, get_windows_vscode_settings_path, is_wsl

_ContainerState = Literal[
    "running", "stopped", "missing", "ambiguous", "docker_unavailable"
]


@dataclass(frozen=True, slots=True)
class ContainerLookup:
    """Result of looking up a devcontainer by its Docker labels."""

    state: _ContainerState
    id: str | None = None
    ids: tuple[str, ...] = ()
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedShell:
    """A shell resolved from a VS Code terminal profile."""

    path: str
    args: tuple[str, ...] = ()
    env: tuple[tuple[str, str], ...] = ()


# ---------------------------------------------------------------------------
# JSONC loading
# ---------------------------------------------------------------------------


def _load_jsonc(path: Path) -> dict:
    """Load a JSONC file as a dict.

    Returns ``{}`` for missing files (silently), or for malformed / non-dict
    contents (with a stderr warning).
    """
    if not path.is_file():
        return {}
    try:
        text = path.read_text()
    except OSError as exc:
        print(f"dcode: failed to read {path}: {exc}", file=sys.stderr)
        return {}
    if not text.strip():
        return {}
    try:
        parsed = json5.loads(text)
    except ValueError as exc:
        print(f"dcode: failed to parse {path}: {exc}", file=sys.stderr)
        return {}
    if not isinstance(parsed, dict):
        print(
            f"dcode: ignoring {path}: top-level value is not an object",
            file=sys.stderr,
        )
        return {}
    return parsed


# ---------------------------------------------------------------------------
# Container discovery
# ---------------------------------------------------------------------------


def _docker_ps(filters: list[str], *, all_states: bool = False) -> tuple[int, str, str]:
    """Run ``docker ps [-a] -q --filter ...`` and return (rc, stdout, stderr)."""
    argv = ["docker", "ps"]
    if all_states:
        argv.append("-a")
    argv.append("-q")
    for f in filters:
        argv.extend(["--filter", f])
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        return (-1, "", str(exc))
    except OSError as exc:
        return (-1, "", str(exc))
    return (proc.returncode, proc.stdout, proc.stderr)


def find_container(host_path: str, config_path: str) -> ContainerLookup:
    """Locate the devcontainer for a project.

    Tries ``devcontainer.local_folder`` + ``devcontainer.config_file`` first;
    falls back to the single-label query, then probes ``docker ps -a`` to
    detect a stopped-but-existing container. Distinguishes running, stopped,
    missing, ambiguous, and ``docker_unavailable`` states.

    On WSL, both ``host_path`` and ``config_path`` are converted to Windows
    paths before being used as label values, since VS Code on Windows stores
    the labels in Windows-path format.
    """
    if is_wsl():
        host_label_value = _wsl_to_windows_path(host_path)
        config_label_value = _wsl_to_windows_path(config_path)
    else:
        host_label_value = host_path
        config_label_value = config_path

    two_label = [
        f"label=devcontainer.local_folder={host_label_value}",
        f"label=devcontainer.config_file={config_label_value}",
    ]
    one_label = [f"label=devcontainer.local_folder={host_label_value}"]

    rc, out, err = _docker_ps(two_label)
    if rc != 0:
        return ContainerLookup(state="docker_unavailable", detail=err.strip() or None)
    ids = out.split()
    if len(ids) > 1:
        return ContainerLookup(state="ambiguous", ids=tuple(ids))
    if len(ids) == 1:
        return ContainerLookup(state="running", id=ids[0])

    # Fallback: single-label lookup (legacy containers, moved configs).
    rc, out, err = _docker_ps(one_label)
    if rc != 0:
        return ContainerLookup(state="docker_unavailable", detail=err.strip() or None)
    ids = out.split()
    if len(ids) > 1:
        return ContainerLookup(state="ambiguous", ids=tuple(ids))
    if len(ids) == 1:
        return ContainerLookup(state="running", id=ids[0])

    # Nothing running — check for a stopped container.
    rc, out, err = _docker_ps(one_label, all_states=True)
    if rc != 0:
        return ContainerLookup(state="docker_unavailable", detail=err.strip() or None)
    ids = out.split()
    if ids:
        # Could be 1 or many; report state=stopped either way.
        return ContainerLookup(state="stopped", id=ids[0], ids=tuple(ids))

    return ContainerLookup(state="missing")


# ---------------------------------------------------------------------------
# User-level VS Code settings
# ---------------------------------------------------------------------------


def get_user_settings_path(insiders: bool) -> Path | None:
    """Return the host user's VS Code ``settings.json`` path (or ``None``)."""
    code_dir = "Code - Insiders" if insiders else "Code"

    if is_wsl():
        return get_windows_vscode_settings_path(insiders)

    system = platform.system()
    if system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
        return base / code_dir / "User" / "settings.json"
    if system == "Linux":
        base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
        return base / code_dir / "User" / "settings.json"
    return None


# ---------------------------------------------------------------------------
# Profile resolution
# ---------------------------------------------------------------------------


def _extract_profiles_layer(settings: dict) -> tuple[str | None, dict]:
    """Pull (defaultProfile.linux, profiles.linux) from a settings dict.

    Accepts both flat keys (``terminal.integrated.profiles.linux``) and the
    nested form (``terminal: {integrated: {profiles: {linux: ...}}}``).
    """
    default_name: str | None = None
    profiles: dict = {}

    flat_default = settings.get("terminal.integrated.defaultProfile.linux")
    if isinstance(flat_default, str):
        default_name = flat_default
    flat_profiles = settings.get("terminal.integrated.profiles.linux")
    if isinstance(flat_profiles, dict):
        profiles = flat_profiles

    nested = settings.get("terminal")
    if isinstance(nested, dict):
        integrated = nested.get("integrated")
        if isinstance(integrated, dict):
            n_default = integrated.get("defaultProfile")
            if isinstance(n_default, dict):
                v = n_default.get("linux")
                if isinstance(v, str):
                    default_name = v
            n_profiles = integrated.get("profiles")
            if isinstance(n_profiles, dict):
                v = n_profiles.get("linux")
                if isinstance(v, dict):
                    # Nested form fully replaces the flat form for this layer.
                    profiles = v
    return default_name, profiles


def _merge_profiles(layers: list[dict]) -> dict:
    """Deep-merge ``profiles.linux`` dicts across layers (lower → higher).

    A ``null`` value at any layer DELETES that profile entry from the merged
    dict (matches VS Code's documented mechanism for disabling built-ins).
    """
    merged: dict = {}
    for layer in layers:
        for name, value in layer.items():
            if value is None:
                merged.pop(name, None)
            else:
                merged[name] = value
    return merged


def _profile_to_resolved(
    profile: dict,
    *,
    warn_substitution: list[bool],
) -> ResolvedShell | None:
    """Convert a profile dict to a ResolvedShell, or None if unusable."""
    raw_path = profile.get("path")
    if isinstance(raw_path, list):
        raw_path = raw_path[0] if raw_path else None
    if not isinstance(raw_path, str) or not raw_path:
        return None

    raw_args = profile.get("args") or []
    args: list[str] = []
    if isinstance(raw_args, list):
        for a in raw_args:
            if isinstance(a, str):
                if "${" in a and not warn_substitution[0]:
                    print(
                        "dcode: terminal profile contains ${...} substitution; "
                        "passing through unchanged (variable substitution is not "
                        "yet implemented)",
                        file=sys.stderr,
                    )
                    warn_substitution[0] = True
                args.append(a)

    raw_env = profile.get("env") or {}
    env_pairs: list[tuple[str, str]] = []
    if isinstance(raw_env, dict):
        for k, v in raw_env.items():
            if not isinstance(k, str) or not isinstance(v, str):
                continue
            if "${" in v and not warn_substitution[0]:
                print(
                    "dcode: terminal profile contains ${...} substitution; "
                    "passing through unchanged (variable substitution is not "
                    "yet implemented)",
                    file=sys.stderr,
                )
                warn_substitution[0] = True
            env_pairs.append((k, v))

    return ResolvedShell(path=raw_path, args=tuple(args), env=tuple(env_pairs))


def resolve_terminal_profile(
    workspace_root: Path,
    devcontainer_cfg: dict,
    insiders: bool,
) -> ResolvedShell | None:
    """Resolve a shell from VS Code settings, walking the priority chain.

    Order (lower → higher precedence): user → devcontainer remote → workspace.
    The merged ``profiles.linux`` is then used to look up the highest-scope
    ``defaultProfile.linux``. If that profile name is missing or unusable,
    falls through to lower-scope default names. Returns ``None`` if no scope
    yields a usable profile (caller should fall back to the login shell).
    """
    # Load layers. User scope is lowest precedence, workspace is highest.
    user_settings: dict = {}
    user_path = get_user_settings_path(insiders)
    if user_path is not None:
        user_settings = _load_jsonc(user_path)

    dc_customizations = devcontainer_cfg.get("customizations") or {}
    dc_vscode = (
        dc_customizations.get("vscode") if isinstance(dc_customizations, dict) else None
    )
    dc_settings: dict = {}
    if isinstance(dc_vscode, dict):
        s = dc_vscode.get("settings")
        if isinstance(s, dict):
            dc_settings = s

    workspace_settings = _load_jsonc(workspace_root / ".vscode" / "settings.json")

    # Extract per-layer (defaultProfile, profiles) tuples.
    layers_in_order = [user_settings, dc_settings, workspace_settings]
    layer_data = [_extract_profiles_layer(s) for s in layers_in_order]

    merged_profiles = _merge_profiles([profiles for _, profiles in layer_data])

    # Try defaultProfile.linux from highest scope down.
    warn_state = [False]
    for default_name, _ in reversed(layer_data):
        if not default_name:
            continue
        profile = merged_profiles.get(default_name)
        if not isinstance(profile, dict):
            # Strict resolution: never treat a profile name as an executable.
            continue
        resolved = _profile_to_resolved(profile, warn_substitution=warn_state)
        if resolved is not None:
            return resolved

    return None


# ---------------------------------------------------------------------------
# Login-shell detection
# ---------------------------------------------------------------------------


def _docker_exec_capture(container_id: str, argv: list[str]) -> subprocess.CompletedProcess:
    """Run ``docker exec <id> <argv>`` and capture output."""
    full = ["docker", "exec", container_id, *argv]
    return subprocess.run(full, capture_output=True, text=True, check=False)


def detect_login_shell(container_id: str, exec_user: str | None) -> str:
    """Detect the login shell for ``exec_user`` inside the container.

    If ``exec_user`` is None, probes ``id -un`` to discover the effective
    container user. Falls back through ``getent passwd`` → ``/bin/bash`` →
    ``/bin/sh``. Rejects ``nologin``/``false`` shells.
    """
    user = exec_user
    if user is None:
        proc = _docker_exec_capture(container_id, ["id", "-un"])
        if proc.returncode == 0:
            user = proc.stdout.strip() or None

    if user:
        proc = _docker_exec_capture(container_id, ["getent", "passwd", user])
        if proc.returncode == 0:
            line = proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else ""
            fields = line.split(":")
            if len(fields) >= 7:
                shell_path = fields[6].strip()
                if shell_path:
                    base = os.path.basename(shell_path)
                    if base not in ("nologin", "false"):
                        return shell_path

    # Fallback: probe /bin/bash, then /bin/sh.
    for candidate in ("/bin/bash", "/bin/sh"):
        proc = _docker_exec_capture(container_id, ["test", "-x", candidate])
        if proc.returncode == 0:
            return candidate

    # Last-ditch: return /bin/sh and let docker exec fail with a clear error.
    return "/bin/sh"


# ---------------------------------------------------------------------------
# SSH socket discovery
# ---------------------------------------------------------------------------


def find_ssh_socket(container_id: str) -> str | None:
    """Find the SSH agent socket path inside the container, or None."""
    # Step 1: inspect Config.Env for SSH_AUTH_SOCK.
    try:
        proc = subprocess.run(
            [
                "docker",
                "inspect",
                container_id,
                "--format",
                "{{json .Config.Env}}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        proc = None

    if proc is not None and proc.returncode == 0 and proc.stdout.strip():
        try:
            env_list = json.loads(proc.stdout.strip())
        except ValueError:
            env_list = None
        if isinstance(env_list, list):
            for entry in env_list:
                if isinstance(entry, str) and entry.startswith("SSH_AUTH_SOCK="):
                    value = entry.split("=", 1)[1]
                    if value:
                        return value

    # Step 2: probe /tmp/vscode-ssh-auth-*.sock (newest first).
    proc = _docker_exec_capture(
        container_id,
        ["sh", "-c", "ls -t /tmp/vscode-ssh-auth*.sock 2>/dev/null | head -1"],
    )
    candidate = proc.stdout.strip() if proc.returncode == 0 else ""
    if candidate:
        check = _docker_exec_capture(container_id, ["test", "-S", candidate])
        if check.returncode == 0:
            return candidate

    return None


# ---------------------------------------------------------------------------
# Working-directory probe
# ---------------------------------------------------------------------------


def probe_workdir(container_id: str, candidate: str, fallback: str) -> str | None:
    """Return the best-existing working dir, or ``None`` if neither exists."""
    proc = _docker_exec_capture(container_id, ["test", "-d", candidate])
    if proc.returncode == 0:
        return candidate
    if fallback and fallback != candidate:
        print(
            f"dcode: working directory {candidate} not found in container; "
            f"falling back to {fallback}",
            file=sys.stderr,
        )
        proc = _docker_exec_capture(container_id, ["test", "-d", fallback])
        if proc.returncode == 0:
            return fallback
    return None


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _resolve_exec_user(devcontainer_cfg: dict) -> str | None:
    for key in ("remoteUser", "containerUser"):
        v = devcontainer_cfg.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _prompt_start_stopped(container_id: str, host_path: str | Path) -> bool:
    """Prompt to start a stopped container and run ``docker start`` if accepted."""
    sys.stderr.write(
        f"dcode: devcontainer for {host_path} is stopped. Start it now? [Y/n] "
    )
    sys.stderr.flush()

    answer = sys.stdin.readline().strip().lower()
    if answer not in ("", "y", "yes"):
        print("dcode: aborted", file=sys.stderr)
        return False

    short_id = container_id[:12]
    print(f"dcode: starting container {short_id}...", file=sys.stderr)
    try:
        proc = subprocess.run(
            ["docker", "start", container_id],
            capture_output=True,
            text=True,
            check=False,
        )
    except (FileNotFoundError, OSError) as exc:
        print(f"dcode: failed to start container {short_id}: {exc}", file=sys.stderr)
        return False

    if proc.returncode != 0:
        detail = (proc.stderr or "").strip()
        if not detail:
            detail = (proc.stdout or "").strip() or f"exit code {proc.returncode}"
        print(f"dcode: failed to start container {short_id}: {detail}", file=sys.stderr)
        return False

    print("dcode: container started", file=sys.stderr)
    return True


def run_shell(path: str, *, insiders: bool, shell_override: str | None) -> int:
    """Open an interactive shell in the running devcontainer for ``path``.

    Returns an exit code suitable for ``sys.exit``. On success, replaces the
    process via ``os.execvp`` (the explicit ``return 0`` is only reachable
    when ``execvp`` is mocked in tests).
    """
    target = Path(path).resolve()

    worktree = resolve_worktree(target)
    if worktree is not None:
        main_repo, rel_path = worktree
    else:
        main_repo = target
        rel_path = None

    devcontainer_path = find_devcontainer(main_repo)
    if devcontainer_path is None:
        print(
            f"dcode: no devcontainer.json found for {main_repo}; "
            f"run `dcode doctor` to diagnose",
            file=sys.stderr,
        )
        return 1

    devcontainer_cfg = _load_jsonc(devcontainer_path)
    workspace_folder = get_workspace_folder(devcontainer_path, main_repo)

    lookup = find_container(str(main_repo), str(devcontainer_path))
    if lookup.state in ("running", "stopped") and not (
        sys.stdin.isatty() and sys.stdout.isatty()
    ):
        if lookup.state == "stopped":
            print(
                f"dcode: devcontainer for {main_repo} exists but is stopped — "
                "run interactively to be prompted to start it, or run "
                f"`dcode {path}` first",
                file=sys.stderr,
            )
        else:
            print(
                "dcode: dcode shell requires an interactive terminal",
                file=sys.stderr,
            )
        return 1

    if lookup.state == "stopped":
        container_id = lookup.id
        if container_id is None:  # pragma: no cover - defensive
            print("dcode: container lookup returned no id", file=sys.stderr)
            return 1
        if not _prompt_start_stopped(container_id, main_repo):
            return 1
    if lookup.state == "missing":
        print(
            f"dcode: no devcontainer found running for {main_repo} — "
            f"open in VS Code first (`dcode {path}`)",
            file=sys.stderr,
        )
        return 1
    if lookup.state == "ambiguous":
        ids = ", ".join(lookup.ids)
        print(
            f"dcode: multiple devcontainers match {main_repo}: {ids} — "
            f"please remove duplicates with `docker rm`",
            file=sys.stderr,
        )
        return 1
    if lookup.state == "docker_unavailable":
        detail = lookup.detail or "unknown error"
        print(
            f"dcode: docker CLI not available — is Docker Desktop running? "
            f"({detail})",
            file=sys.stderr,
        )
        return 1

    container_id = lookup.id
    if container_id is None:  # pragma: no cover - defensive
        print("dcode: container lookup returned no id", file=sys.stderr)
        return 1

    exec_user = _resolve_exec_user(devcontainer_cfg)

    if "remoteEnv" in devcontainer_cfg:
        print(
            "dcode: devcontainer remoteEnv is not applied to this shell yet; "
            "environment may differ from VS Code terminal",
            file=sys.stderr,
        )

    # Resolve shell.
    if shell_override:
        resolved = ResolvedShell(path=shell_override)
    else:
        profile = resolve_terminal_profile(main_repo, devcontainer_cfg, insiders)
        if profile is not None:
            resolved = profile
        else:
            shell_path = detect_login_shell(container_id, exec_user)
            resolved = ResolvedShell(path=shell_path)

    # SSH agent socket forwarding.
    ssh_sock = find_ssh_socket(container_id)
    if ssh_sock is None:
        print(
            "dcode: SSH agent socket not found in container — SSH key auth "
            "may not work (open in VS Code to enable forwarding)",
            file=sys.stderr,
        )

    # Working directory probe.
    if rel_path is not None:
        candidate_workdir = f"{workspace_folder}/{rel_path.as_posix()}"
    else:
        candidate_workdir = workspace_folder
    workdir = probe_workdir(container_id, candidate_workdir, workspace_folder)

    # Build argv.
    argv: list[str] = ["docker", "exec", "-it"]
    if exec_user:
        argv.extend(["-u", exec_user])
    if workdir:
        argv.extend(["-w", workdir])
    if ssh_sock:
        argv.extend(["-e", f"SSH_AUTH_SOCK={ssh_sock}"])
    for k, v in resolved.env:
        argv.extend(["-e", f"{k}={v}"])
    argv.append(container_id)
    argv.append(resolved.path)
    argv.extend(resolved.args)

    try:
        os.execvp("docker", argv)
    except OSError as exc:
        print(f"dcode: failed to exec docker: {exc}", file=sys.stderr)
        return 127

    return 0  # only reached when os.execvp is mocked in tests
