"""Tests for dcode.shell."""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from conftest import _make_worktree

from dcode.shell import (
    ContainerLookup,
    ResolvedShell,
    _build_missing_container,
    _inspect_container_metadata,
    _load_jsonc,
    _obtain_or_install_cli,
    _prompt_yes_no,
    _resolve_exec_user,
    detect_login_shell,
    find_container,
    find_ssh_socket,
    get_user_settings_path,
    probe_workdir,
    resolve_terminal_profile,
    run_shell,
)


def _completed(rc: int = 0, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    """Return a stand-in for ``subprocess.CompletedProcess`` with fixed fields."""
    return SimpleNamespace(returncode=rc, stdout=stdout, stderr=stderr)


class _TTYStringIO(io.StringIO):
    def __init__(self, value: str = "", *, isatty: bool = True):
        super().__init__(value)
        self._isatty = isatty

    def isatty(self) -> bool:
        return self._isatty


# ---------------------------------------------------------------------------
# _load_jsonc
# ---------------------------------------------------------------------------


class TestLoadJsonc:
    def test_missing_file_returns_empty_dict_silently(self, tmp_path, capsys):
        result = _load_jsonc(tmp_path / "nope.json")
        assert result == {}
        assert capsys.readouterr().err == ""

    def test_valid_jsonc_with_comments_and_trailing_commas(self, tmp_path):
        f = tmp_path / "settings.json"
        f.write_text('// hi\n{\n  "a": 1,\n  "b": [1, 2,],\n}\n')
        assert _load_jsonc(f) == {"a": 1, "b": [1, 2]}

    def test_top_level_array_warns_and_returns_empty(self, tmp_path, capsys):
        f = tmp_path / "settings.json"
        f.write_text("[1, 2]")
        result = _load_jsonc(f)
        assert result == {}
        err = capsys.readouterr().err
        assert str(f) in err

    def test_malformed_json_warns_and_returns_empty(self, tmp_path, capsys):
        f = tmp_path / "settings.json"
        f.write_text("{this is not json")
        result = _load_jsonc(f)
        assert result == {}
        err = capsys.readouterr().err
        assert str(f) in err

    def test_top_level_scalar_warns_and_returns_empty(self, tmp_path, capsys):
        f = tmp_path / "settings.json"
        f.write_text("42")
        result = _load_jsonc(f)
        assert result == {}
        err = capsys.readouterr().err
        assert str(f) in err


# ---------------------------------------------------------------------------
# find_container
# ---------------------------------------------------------------------------


class TestFindContainer:
    def _patch_run(self, results):
        """Return a MagicMock that returns successive results from `results`."""
        m = MagicMock(side_effect=results)
        return patch("dcode.shell.subprocess.run", m), m

    def test_two_label_hit_running(self):
        results = [_completed(0, "abc123\n", "")]
        ctx, m = self._patch_run(results)
        with patch("dcode.shell.is_wsl", return_value=False), ctx:
            result = find_container("/host/proj", "/host/proj/.devcontainer/devcontainer.json")
        assert result == ContainerLookup(state="running", id="abc123")
        assert m.call_count == 1
        # Two filters in argv:
        argv = m.call_args_list[0].args[0]
        assert argv.count("--filter") == 2

    def test_single_label_fallback_uses_one_filter(self):
        results = [_completed(0, "", ""), _completed(0, "deadbeef\n", "")]
        ctx, m = self._patch_run(results)
        with patch("dcode.shell.is_wsl", return_value=False), ctx:
            result = find_container("/host/proj", "/host/proj/.devcontainer/devcontainer.json")
        assert result.state == "running"
        assert result.id == "deadbeef"
        argv2 = m.call_args_list[1].args[0]
        assert argv2.count("--filter") == 1

    def test_stopped_container_via_dash_a(self):
        results = [
            _completed(0, "", ""),
            _completed(0, "", ""),
            _completed(0, "stopped1\nstopped2\n", ""),
        ]
        ctx, m = self._patch_run(results)
        with patch("dcode.shell.is_wsl", return_value=False), ctx:
            result = find_container("/host/proj", "/host/proj/.devcontainer/devcontainer.json")
        assert result.state == "stopped"
        assert result.id == "stopped1"
        assert result.ids == ("stopped1", "stopped2")
        # Verify third call had -a:
        argv3 = m.call_args_list[2].args[0]
        assert "-a" in argv3

    def test_missing_when_no_results_anywhere(self):
        results = [_completed(0, "", "")] * 3
        ctx, _ = self._patch_run(results)
        with patch("dcode.shell.is_wsl", return_value=False), ctx:
            result = find_container("/host/proj", "/host/proj/.devcontainer/devcontainer.json")
        assert result == ContainerLookup(state="missing")

    def test_ambiguous_when_two_label_returns_multiple(self):
        results = [_completed(0, "id1\nid2\nid3\n", "")]
        ctx, _ = self._patch_run(results)
        with patch("dcode.shell.is_wsl", return_value=False), ctx:
            result = find_container("/host/proj", "/host/proj/.devcontainer/devcontainer.json")
        assert result.state == "ambiguous"
        assert result.ids == ("id1", "id2", "id3")

    def test_docker_unavailable_when_file_not_found(self):
        ctx, _ = self._patch_run([FileNotFoundError("docker not found")])
        with patch("dcode.shell.is_wsl", return_value=False), ctx:
            result = find_container("/host/proj", "/host/proj/.devcontainer/devcontainer.json")
        assert result.state == "docker_unavailable"
        assert result.detail and "docker" in result.detail.lower()

    def test_docker_unavailable_when_nonzero_returncode(self):
        results = [_completed(1, "", "Cannot connect to the Docker daemon")]
        ctx, _ = self._patch_run(results)
        with patch("dcode.shell.is_wsl", return_value=False), ctx:
            result = find_container("/host/proj", "/host/proj/.devcontainer/devcontainer.json")
        assert result.state == "docker_unavailable"
        assert result.detail and "Docker daemon" in result.detail

    def test_wsl_converts_both_paths_for_label_filters(self):
        results = [_completed(0, "wid\n", "")]
        ctx, m = self._patch_run(results)
        with (
            patch("dcode.shell.is_wsl", return_value=True),
            patch("dcode.shell._wsl_to_windows_path", side_effect=lambda p: f"WIN({p})"),
            ctx,
        ):
            result = find_container("/h/proj", "/h/proj/.devcontainer/devcontainer.json")
        assert result.state == "running"
        argv = m.call_args_list[0].args[0]
        joined = " ".join(argv)
        assert "label=devcontainer.local_folder=WIN(/h/proj)" in joined
        assert "label=devcontainer.config_file=WIN(/h/proj/.devcontainer/devcontainer.json)" in joined


# ---------------------------------------------------------------------------
# get_user_settings_path
# ---------------------------------------------------------------------------


class TestGetUserSettingsPath:
    def test_macos_default(self, monkeypatch):
        monkeypatch.setattr("dcode.shell.platform.system", lambda: "Darwin")
        with patch("dcode.shell.is_wsl", return_value=False):
            p = get_user_settings_path(insiders=False)
        assert p == Path.home() / "Library" / "Application Support" / "Code" / "User" / "settings.json"

    def test_macos_insiders(self, monkeypatch):
        monkeypatch.setattr("dcode.shell.platform.system", lambda: "Darwin")
        with patch("dcode.shell.is_wsl", return_value=False):
            p = get_user_settings_path(insiders=True)
        assert p is not None
        assert "Code - Insiders" in str(p)

    def test_linux_no_xdg(self, monkeypatch):
        monkeypatch.setattr("dcode.shell.platform.system", lambda: "Linux")
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        with patch("dcode.shell.is_wsl", return_value=False):
            p = get_user_settings_path(insiders=False)
        assert p == Path.home() / ".config" / "Code" / "User" / "settings.json"

    def test_linux_with_xdg(self, monkeypatch):
        monkeypatch.setattr("dcode.shell.platform.system", lambda: "Linux")
        monkeypatch.setenv("XDG_CONFIG_HOME", "/custom")
        with patch("dcode.shell.is_wsl", return_value=False):
            p = get_user_settings_path(insiders=False)
        assert p == Path("/custom") / "Code" / "User" / "settings.json"

    def test_wsl_delegates_to_windows_helper(self):
        sentinel = Path("/mnt/c/Users/me/AppData/Roaming/Code/User/settings.json")
        with (
            patch("dcode.shell.is_wsl", return_value=True),
            patch("dcode.shell.get_windows_vscode_settings_path", return_value=sentinel) as m,
        ):
            p = get_user_settings_path(insiders=True)
        assert p == sentinel
        m.assert_called_once_with(True)

    def test_returns_path_even_when_not_existing(self, monkeypatch):
        monkeypatch.setattr("dcode.shell.platform.system", lambda: "Linux")
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        with patch("dcode.shell.is_wsl", return_value=False):
            p = get_user_settings_path(insiders=False)
        # Path is returned regardless of existence.
        assert p is not None


# ---------------------------------------------------------------------------
# resolve_terminal_profile
# ---------------------------------------------------------------------------


class TestResolveTerminalProfile:
    def _setup(self, tmp_path, user, workspace):
        """Write user + workspace settings; return main_repo path."""
        user_path = tmp_path / "user-settings.json"
        user_path.write_text(json.dumps(user))
        main_repo = tmp_path / "proj"
        (main_repo / ".vscode").mkdir(parents=True)
        (main_repo / ".vscode" / "settings.json").write_text(json.dumps(workspace))
        return main_repo, user_path

    def test_workspace_beats_devcontainer_beats_user(self, tmp_path):
        user = {
            "terminal.integrated.defaultProfile.linux": "user-shell",
            "terminal.integrated.profiles.linux": {"user-shell": {"path": "/u"}},
        }
        dc_cfg = {
            "customizations": {
                "vscode": {
                    "settings": {
                        "terminal.integrated.defaultProfile.linux": "dc-shell",
                        "terminal.integrated.profiles.linux": {"dc-shell": {"path": "/d"}},
                    }
                }
            }
        }
        workspace = {
            "terminal.integrated.defaultProfile.linux": "ws-shell",
            "terminal.integrated.profiles.linux": {"ws-shell": {"path": "/w"}},
        }
        main_repo, user_path = self._setup(tmp_path, user, workspace)
        with patch("dcode.shell.get_user_settings_path", return_value=user_path):
            r = resolve_terminal_profile(main_repo, dc_cfg, insiders=False)
        assert r == ResolvedShell(path="/w")

    def test_deep_merge_across_layers(self, tmp_path):
        user = {"terminal.integrated.profiles.linux": {"alpha": {"path": "/a"}}}
        dc_cfg = {
            "customizations": {
                "vscode": {
                    "settings": {
                        "terminal.integrated.profiles.linux": {"beta": {"path": "/b"}}
                    }
                }
            }
        }
        workspace = {
            "terminal.integrated.defaultProfile.linux": "alpha",
            "terminal.integrated.profiles.linux": {"gamma": {"path": "/g"}},
        }
        main_repo, user_path = self._setup(tmp_path, user, workspace)
        with patch("dcode.shell.get_user_settings_path", return_value=user_path):
            r = resolve_terminal_profile(main_repo, dc_cfg, insiders=False)
        # alpha was defined only in user layer; merge preserves it.
        assert r == ResolvedShell(path="/a")

    def test_null_at_higher_layer_deletes_profile(self, tmp_path):
        user = {
            "terminal.integrated.defaultProfile.linux": "alpha",
            "terminal.integrated.profiles.linux": {"alpha": {"path": "/a"}},
        }
        workspace = {"terminal.integrated.profiles.linux": {"alpha": None}}
        main_repo, user_path = self._setup(tmp_path, user, workspace)
        with patch("dcode.shell.get_user_settings_path", return_value=user_path):
            r = resolve_terminal_profile(main_repo, {}, insiders=False)
        assert r is None

    def test_default_pointing_to_missing_profile_returns_none(self, tmp_path):
        user = {}
        workspace = {"terminal.integrated.defaultProfile.linux": "foo"}
        main_repo, user_path = self._setup(tmp_path, user, workspace)
        with patch("dcode.shell.get_user_settings_path", return_value=user_path):
            r = resolve_terminal_profile(main_repo, {}, insiders=False)
        assert r is None

    def test_default_pointing_to_null_profile_returns_none(self, tmp_path):
        user = {
            "terminal.integrated.defaultProfile.linux": "foo",
            "terminal.integrated.profiles.linux": {"foo": None},
        }
        workspace = {}
        main_repo, user_path = self._setup(tmp_path, user, workspace)
        with patch("dcode.shell.get_user_settings_path", return_value=user_path):
            r = resolve_terminal_profile(main_repo, {}, insiders=False)
        assert r is None

    def test_path_as_list_uses_first_entry(self, tmp_path):
        workspace = {
            "terminal.integrated.defaultProfile.linux": "a",
            "terminal.integrated.profiles.linux": {"a": {"path": ["/first", "/second"]}},
        }
        main_repo, user_path = self._setup(tmp_path, {}, workspace)
        with patch("dcode.shell.get_user_settings_path", return_value=user_path):
            r = resolve_terminal_profile(main_repo, {}, insiders=False)
        assert r is not None
        assert r.path == "/first"

    def test_bare_name_returned_as_is(self, tmp_path):
        workspace = {
            "terminal.integrated.defaultProfile.linux": "a",
            "terminal.integrated.profiles.linux": {"a": {"path": "zsh"}},
        }
        main_repo, user_path = self._setup(tmp_path, {}, workspace)
        with patch("dcode.shell.get_user_settings_path", return_value=user_path):
            r = resolve_terminal_profile(main_repo, {}, insiders=False)
        assert r is not None
        assert r.path == "zsh"

    def test_args_become_tuple(self, tmp_path):
        workspace = {
            "terminal.integrated.defaultProfile.linux": "a",
            "terminal.integrated.profiles.linux": {"a": {"path": "/bin/zsh", "args": ["-l"]}},
        }
        main_repo, user_path = self._setup(tmp_path, {}, workspace)
        with patch("dcode.shell.get_user_settings_path", return_value=user_path):
            r = resolve_terminal_profile(main_repo, {}, insiders=False)
        assert r is not None
        assert r.args == ("-l",)

    def test_env_becomes_tuple_of_tuples(self, tmp_path):
        workspace = {
            "terminal.integrated.defaultProfile.linux": "a",
            "terminal.integrated.profiles.linux": {
                "a": {"path": "/bin/zsh", "env": {"FOO": "bar"}}
            },
        }
        main_repo, user_path = self._setup(tmp_path, {}, workspace)
        with patch("dcode.shell.get_user_settings_path", return_value=user_path):
            r = resolve_terminal_profile(main_repo, {}, insiders=False)
        assert r is not None
        assert r.env == (("FOO", "bar"),)

    def test_substitution_warning_emitted_at_most_once(self, tmp_path, capsys):
        workspace = {
            "terminal.integrated.defaultProfile.linux": "a",
            "terminal.integrated.profiles.linux": {
                "a": {
                    "path": "/bin/zsh",
                    "args": ["${env:FOO}", "${env:BAR}"],
                    "env": {"X": "${env:Y}"},
                }
            },
        }
        main_repo, user_path = self._setup(tmp_path, {}, workspace)
        with patch("dcode.shell.get_user_settings_path", return_value=user_path):
            r = resolve_terminal_profile(main_repo, {}, insiders=False)
        assert r is not None
        # Substitution values passed through verbatim.
        assert r.args == ("${env:FOO}", "${env:BAR}")
        assert r.env == (("X", "${env:Y}"),)
        err = capsys.readouterr().err
        # Single warning line — count by line-prefix to avoid matching the word
        # "substitution" twice within the message body itself.
        assert err.count("dcode: terminal profile contains") == 1

    def test_profile_without_path_returns_none(self, tmp_path):
        workspace = {
            "terminal.integrated.defaultProfile.linux": "a",
            "terminal.integrated.profiles.linux": {"a": {"args": ["-l"]}},
        }
        main_repo, user_path = self._setup(tmp_path, {}, workspace)
        with patch("dcode.shell.get_user_settings_path", return_value=user_path):
            r = resolve_terminal_profile(main_repo, {}, insiders=False)
        assert r is None


# ---------------------------------------------------------------------------
# detect_login_shell
# ---------------------------------------------------------------------------


class TestDetectLoginShell:
    def test_getent_returns_zsh(self):
        results = [_completed(0, "node:x:1000:1000::/home/node:/bin/zsh\n", "")]
        with patch("dcode.shell.subprocess.run", side_effect=results):
            assert detect_login_shell("cid", "node") == "/bin/zsh"

    def test_nologin_falls_through_to_bash(self):
        results = [
            _completed(0, "svc:x:0:0::/:/usr/sbin/nologin\n", ""),
            _completed(0, "", ""),  # /bin/bash test -x
        ]
        with patch("dcode.shell.subprocess.run", side_effect=results):
            assert detect_login_shell("cid", "svc") == "/bin/bash"

    def test_false_shell_falls_through_to_bash(self):
        results = [
            _completed(0, "svc:x:0:0::/:/bin/false\n", ""),
            _completed(0, "", ""),
        ]
        with patch("dcode.shell.subprocess.run", side_effect=results):
            assert detect_login_shell("cid", "svc") == "/bin/bash"

    def test_getent_failure_uses_bash_when_available(self):
        results = [
            _completed(2, "", "no such user"),
            _completed(0, "", ""),  # /bin/bash exists
        ]
        with patch("dcode.shell.subprocess.run", side_effect=results):
            assert detect_login_shell("cid", "ghost") == "/bin/bash"

    def test_no_bash_falls_through_to_sh(self):
        results = [
            _completed(2, "", ""),  # getent fails
            _completed(1, "", ""),  # /bin/bash test -x fails
            _completed(0, "", ""),  # /bin/sh test -x ok
        ]
        with patch("dcode.shell.subprocess.run", side_effect=results):
            assert detect_login_shell("cid", "x") == "/bin/sh"

    def test_exec_user_none_invokes_id_un_first(self):
        results = [
            _completed(0, "vscode\n", ""),  # id -un
            _completed(0, "vscode:x:1000:1000::/home/vscode:/bin/zsh\n", ""),
        ]
        m = MagicMock(side_effect=results)
        with patch("dcode.shell.subprocess.run", m):
            assert detect_login_shell("cid", None) == "/bin/zsh"
        # First call must be id -un:
        assert m.call_args_list[0].args[0][-2:] == ["id", "-un"]
        # Second call must include `getent passwd vscode`:
        argv2 = m.call_args_list[1].args[0]
        assert argv2[-3:] == ["getent", "passwd", "vscode"]


# ---------------------------------------------------------------------------
# _inspect_container_metadata
# ---------------------------------------------------------------------------


class TestInspectContainerMetadata:
    def test_returns_list_for_array_label(self):
        label = json.dumps([{"id": "feat"}, {"remoteUser": "node"}])
        with patch(
            "dcode.shell.subprocess.run",
            return_value=_completed(0, label + "\n", ""),
        ) as m:
            result = _inspect_container_metadata("cid")
        assert result == [{"id": "feat"}, {"remoteUser": "node"}]
        # Verify the docker invocation shape:
        argv = m.call_args.args[0]
        assert argv[:3] == ["docker", "inspect", "cid"]
        assert "--format" in argv
        fmt = argv[argv.index("--format") + 1]
        assert "devcontainer.metadata" in fmt

    def test_object_label_wrapped_in_single_entry(self):
        # Older/custom images may write a JSON object instead of an array.
        label = json.dumps({"remoteUser": "vscode"})
        with patch(
            "dcode.shell.subprocess.run",
            return_value=_completed(0, label + "\n", ""),
        ):
            assert _inspect_container_metadata("cid") == [{"remoteUser": "vscode"}]

    def test_non_dict_array_entries_filtered(self):
        label = json.dumps([{"a": 1}, "junk", 42, None, {"b": 2}])
        with patch(
            "dcode.shell.subprocess.run",
            return_value=_completed(0, label + "\n", ""),
        ):
            assert _inspect_container_metadata("cid") == [{"a": 1}, {"b": 2}]

    def test_missing_label_returns_empty(self):
        # Docker's --format prints the empty string when the label is absent
        # (or "<no value>" depending on Docker version).
        for stdout in ("", "\n", "<no value>\n"):
            with patch(
                "dcode.shell.subprocess.run",
                return_value=_completed(0, stdout, ""),
            ):
                assert _inspect_container_metadata("cid") == []

    def test_malformed_json_returns_empty(self):
        with patch(
            "dcode.shell.subprocess.run",
            return_value=_completed(0, "not json at all\n", ""),
        ):
            assert _inspect_container_metadata("cid") == []

    def test_top_level_scalar_returns_empty(self):
        with patch(
            "dcode.shell.subprocess.run",
            return_value=_completed(0, '"justastring"\n', ""),
        ):
            assert _inspect_container_metadata("cid") == []

    def test_docker_nonzero_returns_empty(self):
        with patch(
            "dcode.shell.subprocess.run",
            return_value=_completed(1, "", "no such container"),
        ):
            assert _inspect_container_metadata("cid") == []

    def test_docker_missing_returns_empty(self):
        with patch(
            "dcode.shell.subprocess.run",
            side_effect=FileNotFoundError("docker"),
        ):
            assert _inspect_container_metadata("cid") == []


# ---------------------------------------------------------------------------
# _resolve_exec_user
# ---------------------------------------------------------------------------


class TestResolveExecUser:
    def test_devcontainer_json_remote_user(self):
        assert _resolve_exec_user({"remoteUser": "vscode"}) == "vscode"

    def test_devcontainer_json_container_user_when_no_remote(self):
        assert _resolve_exec_user({"containerUser": "vscode"}) == "vscode"

    def test_remote_user_preferred_over_container_user(self):
        cfg = {"remoteUser": "node", "containerUser": "root"}
        assert _resolve_exec_user(cfg) == "node"

    def test_metadata_remote_user_used_when_json_empty(self):
        assert (
            _resolve_exec_user({}, [{"remoteUser": "node"}]) == "node"
        )

    def test_devcontainer_json_overrides_metadata(self):
        # Local devcontainer.json is the highest-precedence layer.
        cfg = {"remoteUser": "vscode"}
        meta = [{"remoteUser": "node"}]
        assert _resolve_exec_user(cfg, meta) == "vscode"

    def test_last_metadata_layer_wins(self):
        # Mirrors devcontainers/cli mergeConfiguration: reversed().find(...)
        # — the last entry in the metadata array wins per key.
        meta = [
            {"remoteUser": "base"},
            {"remoteUser": "feature"},
            {"remoteUser": "final"},
        ]
        assert _resolve_exec_user({}, meta) == "final"

    def test_remote_user_in_earlier_layer_beats_container_user_in_later(self):
        # Per-key independent reverse walk + remoteUser-over-containerUser
        # precedence: an older remoteUser still wins over a newer
        # containerUser, matching devcontainers/cli semantics.
        meta = [
            {"remoteUser": "node"},
            {"containerUser": "root"},
        ]
        assert _resolve_exec_user({}, meta) == "node"

    def test_blank_or_non_string_values_ignored(self):
        meta = [
            {"remoteUser": "  "},
            {"remoteUser": None},
            {"remoteUser": 123},
            {"remoteUser": "vscode"},
        ]
        assert _resolve_exec_user({}, meta) == "vscode"

    def test_returns_none_when_nothing_set(self):
        assert _resolve_exec_user({}, []) is None
        assert _resolve_exec_user({}) is None


# ---------------------------------------------------------------------------
# find_ssh_socket
# ---------------------------------------------------------------------------


class TestFindSshSocket:
    def test_found_via_inspect_env(self):
        env_json = json.dumps(["FOO=bar", "SSH_AUTH_SOCK=/host/sock"])
        results = [_completed(0, env_json + "\n", "")]
        with patch("dcode.shell.subprocess.run", side_effect=results):
            assert find_ssh_socket("cid") == "/host/sock"

    def test_inspect_empty_then_ls_single_path_with_socket(self):
        results = [
            _completed(0, "[]\n", ""),
            _completed(0, "/tmp/vscode-ssh-auth-1.sock\n", ""),  # ls -t
            _completed(0, "", ""),  # test -S ok
        ]
        with patch("dcode.shell.subprocess.run", side_effect=results):
            assert find_ssh_socket("cid") == "/tmp/vscode-ssh-auth-1.sock"

    def test_ls_multiline_uses_first(self):
        # ls -t output is already piped to head -1 on the container side, so
        # only the first line is returned by stdout in practice. Simulate that:
        results = [
            _completed(0, "[]\n", ""),
            _completed(0, "/tmp/vscode-ssh-auth-newer.sock\n", ""),
            _completed(0, "", ""),
        ]
        with patch("dcode.shell.subprocess.run", side_effect=results):
            assert find_ssh_socket("cid") == "/tmp/vscode-ssh-auth-newer.sock"

    def test_ls_empty_returns_none(self):
        results = [
            _completed(0, "[]\n", ""),
            _completed(0, "", ""),  # nothing matched
        ]
        with patch("dcode.shell.subprocess.run", side_effect=results):
            assert find_ssh_socket("cid") is None

    def test_ls_path_but_not_socket_returns_none(self):
        results = [
            _completed(0, "[]\n", ""),
            _completed(0, "/tmp/vscode-ssh-auth-x.sock\n", ""),
            _completed(1, "", ""),  # test -S fails
        ]
        with patch("dcode.shell.subprocess.run", side_effect=results):
            assert find_ssh_socket("cid") is None

    def test_inspect_malformed_json_falls_through(self):
        results = [
            _completed(0, "not json at all\n", ""),
            _completed(0, "", ""),  # ls produces nothing
        ]
        with patch("dcode.shell.subprocess.run", side_effect=results):
            assert find_ssh_socket("cid") is None


# ---------------------------------------------------------------------------
# probe_workdir
# ---------------------------------------------------------------------------


class TestProbeWorkdir:
    def test_candidate_exists(self, capsys):
        with patch("dcode.shell.subprocess.run", side_effect=[_completed(0, "", "")]):
            assert probe_workdir("cid", "/workspaces/proj/sub", "/workspaces/proj") == "/workspaces/proj/sub"
        assert capsys.readouterr().err == ""

    def test_candidate_missing_fallback_succeeds(self, capsys):
        results = [_completed(1, "", ""), _completed(0, "", "")]
        with patch("dcode.shell.subprocess.run", side_effect=results):
            assert probe_workdir("cid", "/workspaces/proj/sub", "/workspaces/proj") == "/workspaces/proj"
        err = capsys.readouterr().err
        assert "/workspaces/proj/sub" in err

    def test_both_fail_returns_none(self):
        results = [_completed(1, "", ""), _completed(1, "", "")]
        with patch("dcode.shell.subprocess.run", side_effect=results):
            assert probe_workdir("cid", "/c", "/f") is None


# ---------------------------------------------------------------------------
# run_shell — orchestration helpers + tests
# ---------------------------------------------------------------------------


def _make_project(tmp_path: Path, devcontainer_text: str = "{}") -> Path:
    main_repo = tmp_path / "proj"
    (main_repo / ".devcontainer").mkdir(parents=True)
    (main_repo / ".devcontainer" / "devcontainer.json").write_text(devcontainer_text)
    return main_repo


class _RunShellHarness:
    """Patches all subprocess-touching helpers to safe defaults for run_shell."""

    def __init__(
        self,
        *,
        container_id: str = "cid123",
        ssh_sock: str | None = "/host/ssh.sock",
        workdir: str | None = "/workspaces/proj",
        profile: ResolvedShell | None = None,
        login_shell: str = "/bin/bash",
        isatty: bool = True,
        execvp_side_effect=None,
        metadata_entries: list | None = None,
    ):
        self.container_id = container_id
        self.ssh_sock = ssh_sock
        self.workdir = workdir
        self.profile = profile
        self.login_shell = login_shell
        self.isatty = isatty
        self.execvp = MagicMock(side_effect=execvp_side_effect)
        self.metadata_entries = metadata_entries if metadata_entries is not None else []

    def __enter__(self):
        self._patches = [
            patch(
                "dcode.shell.find_container",
                return_value=ContainerLookup(state="running", id=self.container_id),
            ),
            patch(
                "dcode.shell._inspect_container_metadata",
                return_value=list(self.metadata_entries),
            ),
            patch("dcode.shell.find_ssh_socket", return_value=self.ssh_sock),
            patch("dcode.shell.probe_workdir", return_value=self.workdir),
            patch("dcode.shell.resolve_terminal_profile", return_value=self.profile),
            patch("dcode.shell.detect_login_shell", return_value=self.login_shell),
            patch("dcode.shell.os.execvp", self.execvp),
            patch("sys.stdin"),
            patch("sys.stdout"),
        ]
        self._opened = [p.start() for p in self._patches]
        # isatty configuration:
        import sys as _sys
        _sys.stdin.isatty = MagicMock(return_value=self.isatty)
        _sys.stdout.isatty = MagicMock(return_value=self.isatty)
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()


class TestRunShell:
    def test_happy_path_argv(self, tmp_path):
        proj = _make_project(tmp_path, '{"workspaceFolder": "/workspaces/proj"}')
        with _RunShellHarness(workdir="/workspaces/proj") as h:
            rc = run_shell(str(proj), insiders=False, shell_override=None)
        assert rc == 0
        h.execvp.assert_called_once()
        argv = h.execvp.call_args.args[1]
        assert argv[:3] == ["docker", "exec", "-it"]
        assert argv[-2:] == ["cid123", "/bin/bash"]

    def test_remote_user_adds_u_flag(self, tmp_path):
        proj = _make_project(tmp_path, '{"remoteUser": "node"}')
        with _RunShellHarness() as h:
            run_shell(str(proj), insiders=False, shell_override=None)
        argv = h.execvp.call_args.args[1]
        i = argv.index("-u")
        assert argv[i + 1] == "node"

    def test_container_user_used_when_no_remote_user(self, tmp_path):
        proj = _make_project(tmp_path, '{"containerUser": "vscode"}')
        with _RunShellHarness() as h:
            run_shell(str(proj), insiders=False, shell_override=None)
        argv = h.execvp.call_args.args[1]
        assert "vscode" in argv

    def test_no_user_when_neither_present(self, tmp_path):
        proj = _make_project(tmp_path, "{}")
        with _RunShellHarness() as h:
            run_shell(str(proj), insiders=False, shell_override=None)
        argv = h.execvp.call_args.args[1]
        assert "-u" not in argv

    def test_remote_user_from_image_metadata_label(self, tmp_path):
        # devcontainer.json doesn't set remoteUser; it comes from the base
        # image (e.g. mcr.microsoft.com/devcontainers/javascript-node).
        proj = _make_project(tmp_path, "{}")
        with _RunShellHarness(metadata_entries=[{"remoteUser": "node"}]) as h:
            run_shell(str(proj), insiders=False, shell_override=None)
        argv = h.execvp.call_args.args[1]
        i = argv.index("-u")
        assert argv[i + 1] == "node"

    def test_devcontainer_json_overrides_image_metadata(self, tmp_path):
        proj = _make_project(tmp_path, '{"remoteUser": "vscode"}')
        with _RunShellHarness(metadata_entries=[{"remoteUser": "node"}]) as h:
            run_shell(str(proj), insiders=False, shell_override=None)
        argv = h.execvp.call_args.args[1]
        i = argv.index("-u")
        assert argv[i + 1] == "vscode"

    def test_workdir_present(self, tmp_path):
        proj = _make_project(tmp_path)
        with _RunShellHarness(workdir="/workspaces/proj") as h:
            run_shell(str(proj), insiders=False, shell_override=None)
        argv = h.execvp.call_args.args[1]
        i = argv.index("-w")
        assert argv[i + 1] == "/workspaces/proj"

    def test_no_workdir_flag_when_probe_returns_none(self, tmp_path):
        proj = _make_project(tmp_path)
        with _RunShellHarness(workdir=None) as h:
            run_shell(str(proj), insiders=False, shell_override=None)
        argv = h.execvp.call_args.args[1]
        assert "-w" not in argv

    def test_ssh_socket_forwarded(self, tmp_path):
        proj = _make_project(tmp_path)
        with _RunShellHarness(ssh_sock="/host/ssh.sock") as h:
            run_shell(str(proj), insiders=False, shell_override=None)
        argv = h.execvp.call_args.args[1]
        assert "SSH_AUTH_SOCK=/host/ssh.sock" in argv

    def test_no_ssh_socket_warns(self, tmp_path, capsys):
        proj = _make_project(tmp_path)
        with _RunShellHarness(ssh_sock=None) as h:
            run_shell(str(proj), insiders=False, shell_override=None)
        argv = h.execvp.call_args.args[1]
        assert not any(a.startswith("SSH_AUTH_SOCK=") for a in argv)
        err = capsys.readouterr().err
        assert "VS Code" in err and "SSH" in err

    def test_profile_env_in_argv(self, tmp_path):
        proj = _make_project(tmp_path)
        prof = ResolvedShell(path="/bin/zsh", env=(("FOO", "bar"), ("BAZ", "qux")))
        with _RunShellHarness(profile=prof) as h:
            run_shell(str(proj), insiders=False, shell_override=None)
        argv = h.execvp.call_args.args[1]
        assert "FOO=bar" in argv
        assert "BAZ=qux" in argv

    def test_profile_args_appended(self, tmp_path):
        proj = _make_project(tmp_path)
        prof = ResolvedShell(path="/bin/zsh", args=("-l", "-i"))
        with _RunShellHarness(profile=prof) as h:
            run_shell(str(proj), insiders=False, shell_override=None)
        argv = h.execvp.call_args.args[1]
        assert argv[-3:] == ["/bin/zsh", "-l", "-i"]
        # container id immediately precedes shell path
        assert argv[-4] == "cid123"

    def test_shell_override_uses_path_with_no_args_or_env(self, tmp_path):
        proj = _make_project(tmp_path)
        with _RunShellHarness() as h:
            run_shell(str(proj), insiders=False, shell_override="/bin/fish")
        argv = h.execvp.call_args.args[1]
        assert argv[-1] == "/bin/fish"
        # No extra env from a profile since override skips profile resolution.
        # SSH/etc may still add -e SSH_AUTH_SOCK=...; that's fine.

    def test_execvp_oserror_returns_127(self, tmp_path, capsys):
        proj = _make_project(tmp_path)
        with _RunShellHarness(execvp_side_effect=OSError("boom")) as h:
            rc = run_shell(str(proj), insiders=False, shell_override=None)
        assert rc == 127
        assert "failed to exec docker" in capsys.readouterr().err
        h.execvp.assert_called_once()

    def test_execvp_mocked_returns_zero(self, tmp_path):
        proj = _make_project(tmp_path)
        with _RunShellHarness():
            assert run_shell(str(proj), insiders=False, shell_override=None) == 0

    def test_non_tty_returns_nonzero_and_no_execvp(self, tmp_path, capsys):
        proj = _make_project(tmp_path)
        with _RunShellHarness(isatty=False) as h:
            rc = run_shell(str(proj), insiders=False, shell_override=None)
        assert rc != 0
        h.execvp.assert_not_called()
        assert "interactive terminal" in capsys.readouterr().err

    def test_tty_check_after_container_lookup(self, tmp_path):
        """find_container is called even when TTY check would fail."""
        proj = _make_project(tmp_path)
        find_mock = MagicMock(
            return_value=ContainerLookup(state="running", id="cid")
        )
        with (
            patch("dcode.shell.find_container", find_mock),
            patch("dcode.shell.find_ssh_socket", return_value=None),
            patch("dcode.shell.probe_workdir", return_value=None),
            patch("dcode.shell.resolve_terminal_profile", return_value=None),
            patch("dcode.shell.detect_login_shell", return_value="/bin/sh"),
            patch("dcode.shell.os.execvp"),
            patch("sys.stdin") as stdin,
            patch("sys.stdout") as stdout,
        ):
            stdin.isatty = MagicMock(return_value=False)
            stdout.isatty = MagicMock(return_value=True)
            rc = run_shell(str(proj), insiders=False, shell_override=None)
        assert rc != 0
        find_mock.assert_called_once()

    def test_worktree_uses_main_repo_for_lookup(self, tmp_path):
        main_repo, worktree = _make_worktree(tmp_path)
        # devcontainer lives in main repo
        (main_repo / ".devcontainer").mkdir()
        (main_repo / ".devcontainer" / "devcontainer.json").write_text(
            '{"workspaceFolder": "/workspaces/main-repo"}'
        )
        # Target is the worktree root itself (it has the gitdir pointer file).
        target = worktree

        find_mock = MagicMock(
            return_value=ContainerLookup(state="running", id="cid")
        )
        probe_mock = MagicMock(return_value="/workspaces/main-repo")
        with (
            patch("dcode.shell.find_container", find_mock),
            patch("dcode.shell.find_ssh_socket", return_value=None),
            patch("dcode.shell.probe_workdir", probe_mock),
            patch("dcode.shell.resolve_terminal_profile", return_value=None),
            patch("dcode.shell.detect_login_shell", return_value="/bin/sh"),
            patch("dcode.shell.os.execvp"),
            patch("sys.stdin") as stdin,
            patch("sys.stdout") as stdout,
        ):
            stdin.isatty = MagicMock(return_value=True)
            stdout.isatty = MagicMock(return_value=True)
            run_shell(str(target), insiders=False, shell_override=None)

        # find_container received the MAIN repo path, not the worktree.
        host_arg = find_mock.call_args.args[0]
        assert host_arg == str(main_repo.resolve())

        # probe_workdir candidate is workspaceFolder / rel_path (URI-style).
        candidate = probe_mock.call_args.args[1]
        assert candidate == "/workspaces/main-repo/.worktrees/pr-34"


class TestRunShellStoppedPrompt:
    def _run_stopped(
        self,
        tmp_path,
        monkeypatch,
        answer: str,
        *,
        isatty: bool = True,
        start_rc: int = 0,
        start_stderr: str = "",
    ):
        proj = _make_project(tmp_path)
        stdout = _TTYStringIO(isatty=isatty)
        monkeypatch.setattr("sys.stdin", _TTYStringIO(answer, isatty=isatty))
        monkeypatch.setattr("sys.stdout", stdout)

        start = MagicMock(return_value=_completed(start_rc, "abc123\n", start_stderr))
        execvp = MagicMock()
        with (
            patch(
                "dcode.shell.find_container",
                return_value=ContainerLookup(
                    state="stopped", id="abc123", ids=("abc123",)
                ),
            ),
            patch("dcode.shell.subprocess.run", start),
            patch("dcode.shell._inspect_container_metadata", return_value=[]),
            patch("dcode.shell.find_ssh_socket", return_value="/host/ssh.sock"),
            patch("dcode.shell.probe_workdir", return_value="/workspaces/proj"),
            patch("dcode.shell.resolve_terminal_profile", return_value=None),
            patch("dcode.shell.detect_login_shell", return_value="/bin/bash"),
            patch("dcode.shell.os.execvp", execvp),
        ):
            rc = run_shell(str(proj), insiders=False, shell_override=None)

        return SimpleNamespace(rc=rc, start=start, execvp=execvp, stdout=stdout)

    def test_y_starts_container_then_execs(self, tmp_path, monkeypatch, capsys):
        result = self._run_stopped(tmp_path, monkeypatch, "y\n")

        assert result.rc == 0
        result.start.assert_called_once_with(
            ["docker", "start", "abc123"],
            capture_output=True,
            text=True,
            check=False,
        )
        result.execvp.assert_called_once()
        err = capsys.readouterr().err
        assert "Start it now? [Y/n]" in err
        assert "starting container abc123" in err
        assert "container started" in err

    def test_enter_defaults_to_yes(self, tmp_path, monkeypatch):
        result = self._run_stopped(tmp_path, monkeypatch, "\n")

        assert result.rc == 0
        result.start.assert_called_once()
        result.execvp.assert_called_once()

    def test_yes_word_is_case_insensitive(self, tmp_path, monkeypatch):
        result = self._run_stopped(tmp_path, monkeypatch, "YeS\n")

        assert result.rc == 0
        result.start.assert_called_once()
        result.execvp.assert_called_once()

    def test_n_aborts_without_start_or_exec(self, tmp_path, monkeypatch, capsys):
        result = self._run_stopped(tmp_path, monkeypatch, "n\n")

        assert result.rc != 0
        result.start.assert_not_called()
        result.execvp.assert_not_called()
        assert "aborted" in capsys.readouterr().err

    def test_no_aborts_without_start_or_exec(self, tmp_path, monkeypatch, capsys):
        result = self._run_stopped(tmp_path, monkeypatch, "no\n")

        assert result.rc != 0
        result.start.assert_not_called()
        result.execvp.assert_not_called()
        assert "aborted" in capsys.readouterr().err

    def test_docker_start_failure_includes_stderr(
        self, tmp_path, monkeypatch, capsys
    ):
        result = self._run_stopped(
            tmp_path,
            monkeypatch,
            "y\n",
            start_rc=1,
            start_stderr="some docker error",
        )

        assert result.rc != 0
        result.start.assert_called_once()
        result.execvp.assert_not_called()
        err = capsys.readouterr().err
        assert "failed to start container abc123" in err
        assert "some docker error" in err

    def test_non_tty_stopped_does_not_prompt_or_start(
        self, tmp_path, monkeypatch, capsys
    ):
        result = self._run_stopped(tmp_path, monkeypatch, "y\n", isatty=False)

        assert result.rc != 0
        result.start.assert_not_called()
        result.execvp.assert_not_called()
        err = capsys.readouterr().err
        assert "run interactively to be prompted to start it" in err
        assert "Start it now? [Y/n]" not in err

    def test_prompt_is_written_to_stderr_not_stdout(
        self, tmp_path, monkeypatch, capsys
    ):
        result = self._run_stopped(tmp_path, monkeypatch, "y\n")

        err = capsys.readouterr().err
        assert "Start it now? [Y/n]" in err
        assert result.stdout.getvalue() == ""


# ---------------------------------------------------------------------------
# _prompt_yes_no
# ---------------------------------------------------------------------------


class TestPromptYesNo:
    def _run(self, answer: str, *, default_yes: bool, capsys):
        with patch("sys.stdin", _TTYStringIO(answer)):
            return _prompt_yes_no("Q?", default_yes=default_yes)

    def test_y_accepts(self, capsys):
        assert self._run("y\n", default_yes=True, capsys=capsys) is True

    def test_yes_word_accepts_case_insensitive(self, capsys):
        assert self._run("YeS\n", default_yes=False, capsys=capsys) is True

    def test_empty_default_yes_accepts(self, capsys):
        assert self._run("\n", default_yes=True, capsys=capsys) is True

    def test_empty_default_no_declines(self, capsys):
        assert self._run("\n", default_yes=False, capsys=capsys) is False
        assert "aborted" in capsys.readouterr().err

    def test_n_declines(self, capsys):
        assert self._run("n\n", default_yes=True, capsys=capsys) is False
        assert "aborted" in capsys.readouterr().err

    def test_garbage_declines(self, capsys):
        assert self._run("maybe\n", default_yes=True, capsys=capsys) is False
        assert "aborted" in capsys.readouterr().err

    def test_question_and_suffix_on_stderr(self, capsys):
        with patch("sys.stdin", _TTYStringIO("y\n")):
            _prompt_yes_no("Build it?", default_yes=True)
        err = capsys.readouterr().err
        assert "Build it?" in err
        assert "[Y/n]" in err

    def test_default_no_renders_lower_y(self, capsys):
        with patch("sys.stdin", _TTYStringIO("y\n")):
            _prompt_yes_no("Install?", default_yes=False)
        assert "[y/N]" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# _obtain_or_install_cli
# ---------------------------------------------------------------------------


class TestObtainOrInstallCli:
    def test_returns_existing_path_without_prompting(self, capsys):
        with (
            patch("dcode.shell.devcontainer_cli.find_cli", return_value=Path("/x/dc")),
            patch("dcode.shell.devcontainer_cli.install_cli") as install,
            patch("sys.stdin", _TTYStringIO("")),
        ):
            assert _obtain_or_install_cli() == Path("/x/dc")
        install.assert_not_called()
        # Nothing should have been prompted.
        assert "Install" not in capsys.readouterr().err

    def test_declines_install_returns_none_with_hint(self, capsys):
        with (
            patch("dcode.shell.devcontainer_cli.find_cli", return_value=None),
            patch("dcode.shell.devcontainer_cli.install_cli") as install,
            patch("sys.stdin", _TTYStringIO("n\n")),
        ):
            assert _obtain_or_install_cli() is None
        install.assert_not_called()
        err = capsys.readouterr().err
        assert "install" in err.lower()
        assert "VS Code" in err  # alternative hint

    def test_accepts_install_returns_installed_path(self, capsys):
        with (
            patch("dcode.shell.devcontainer_cli.find_cli", return_value=None),
            patch(
                "dcode.shell.devcontainer_cli.install_cli",
                return_value=Path("/home/u/.devcontainers/bin/devcontainer"),
            ) as install,
            patch("sys.stdin", _TTYStringIO("y\n")),
        ):
            assert _obtain_or_install_cli() == Path(
                "/home/u/.devcontainers/bin/devcontainer"
            )
        install.assert_called_once()

    def test_install_failure_returns_none(self, capsys):
        with (
            patch("dcode.shell.devcontainer_cli.find_cli", return_value=None),
            patch("dcode.shell.devcontainer_cli.install_cli", return_value=None),
            patch("sys.stdin", _TTYStringIO("y\n")),
        ):
            assert _obtain_or_install_cli() is None
        assert "install" in capsys.readouterr().err.lower()


# ---------------------------------------------------------------------------
# _build_missing_container
# ---------------------------------------------------------------------------


class TestBuildMissingContainer:
    def test_returns_container_id_on_success(self, tmp_path, capsys):
        proj = tmp_path / "proj"
        cfg = tmp_path / "proj/.devcontainer/devcontainer.json"
        with (
            patch(
                "dcode.shell._obtain_or_install_cli",
                return_value=Path("/x/devcontainer"),
            ),
            patch(
                "dcode.shell.devcontainer_cli.up",
                return_value=("abc123def456", ""),
            ) as up,
        ):
            cid = _build_missing_container(proj, cfg)
        assert cid == "abc123def456"
        up.assert_called_once_with(Path("/x/devcontainer"), proj, cfg)
        err = capsys.readouterr().err
        assert "abc123def456"[:12] in err  # short-id printed

    def test_no_cli_returns_none(self, tmp_path):
        with patch("dcode.shell._obtain_or_install_cli", return_value=None):
            assert _build_missing_container(tmp_path, tmp_path / "x.json") is None

    def test_up_failure_prints_error_log(self, tmp_path, capsys):
        with (
            patch(
                "dcode.shell._obtain_or_install_cli",
                return_value=Path("/x/devcontainer"),
            ),
            patch(
                "dcode.shell.devcontainer_cli.up",
                return_value=(None, "Dockerfile RUN failed: package foo not found"),
            ),
        ):
            cid = _build_missing_container(tmp_path, tmp_path / "x.json")
        assert cid is None
        err = capsys.readouterr().err
        assert "build failed" in err
        assert "package foo not found" in err


# ---------------------------------------------------------------------------
# run_shell — missing → build flow
# ---------------------------------------------------------------------------


class TestRunShellMissingBuild:
    def _patches(
        self,
        *,
        answer: str,
        built_container_id: str | None = "newcid",
        metadata_entries=None,
    ):
        """Patches simulating find_container='missing' + a build outcome."""
        proj_metadata = list(metadata_entries) if metadata_entries else []
        return [
            patch(
                "dcode.shell.find_container",
                return_value=ContainerLookup(state="missing"),
            ),
            patch(
                "dcode.shell._build_missing_container",
                return_value=built_container_id,
            ),
            patch(
                "dcode.shell._inspect_container_metadata",
                return_value=proj_metadata,
            ),
            patch("dcode.shell.find_ssh_socket", return_value="/host/ssh.sock"),
            patch("dcode.shell.probe_workdir", return_value="/workspaces/proj"),
            patch("dcode.shell.resolve_terminal_profile", return_value=None),
            patch("dcode.shell.detect_login_shell", return_value="/bin/bash"),
            patch("sys.stdin", _TTYStringIO(answer)),
        ]

    def _enter_all(self, stack, patches):
        return [stack.enter_context(p) for p in patches]

    def test_accept_build_then_exec(self, tmp_path, monkeypatch, capsys):
        from contextlib import ExitStack

        proj = _make_project(tmp_path)
        monkeypatch.setattr("sys.stdout", _TTYStringIO())

        execvp = MagicMock()
        with ExitStack() as stack:
            stack.enter_context(patch("dcode.shell.os.execvp", execvp))
            self._enter_all(
                stack,
                self._patches(answer="y\n", built_container_id="newcid"),
            )
            rc = run_shell(str(proj), insiders=False, shell_override=None)

        assert rc == 0
        execvp.assert_called_once()
        argv = execvp.call_args.args[1]
        assert "newcid" in argv
        err = capsys.readouterr().err
        assert "Build & start it now?" in err

    def test_decline_build_returns_nonzero_no_exec(self, tmp_path, monkeypatch, capsys):
        proj = _make_project(tmp_path)
        monkeypatch.setattr("sys.stdout", _TTYStringIO())

        execvp = MagicMock()
        build = MagicMock()
        with (
            patch("dcode.shell.os.execvp", execvp),
            patch(
                "dcode.shell.find_container",
                return_value=ContainerLookup(state="missing"),
            ),
            patch("dcode.shell._build_missing_container", build),
            patch("sys.stdin", _TTYStringIO("n\n")),
        ):
            rc = run_shell(str(proj), insiders=False, shell_override=None)

        assert rc != 0
        execvp.assert_not_called()
        build.assert_not_called()
        err = capsys.readouterr().err
        assert "aborted" in err

    def test_accept_but_build_fails_returns_nonzero(self, tmp_path, monkeypatch):
        from contextlib import ExitStack

        proj = _make_project(tmp_path)
        monkeypatch.setattr("sys.stdout", _TTYStringIO())

        execvp = MagicMock()
        with ExitStack() as stack:
            stack.enter_context(patch("dcode.shell.os.execvp", execvp))
            self._enter_all(
                stack,
                self._patches(answer="y\n", built_container_id=None),
            )
            rc = run_shell(str(proj), insiders=False, shell_override=None)

        assert rc != 0
        execvp.assert_not_called()

    def test_built_container_metadata_used_for_remote_user(
        self, tmp_path, monkeypatch
    ):
        from contextlib import ExitStack

        proj = _make_project(tmp_path)  # devcontainer.json has no remoteUser
        monkeypatch.setattr("sys.stdout", _TTYStringIO())

        execvp = MagicMock()
        with ExitStack() as stack:
            stack.enter_context(patch("dcode.shell.os.execvp", execvp))
            self._enter_all(
                stack,
                self._patches(
                    answer="y\n",
                    built_container_id="newcid",
                    metadata_entries=[{"remoteUser": "node"}],
                ),
            )
            run_shell(str(proj), insiders=False, shell_override=None)

        argv = execvp.call_args.args[1]
        i = argv.index("-u")
        assert argv[i + 1] == "node"


# ---------------------------------------------------------------------------
# run_shell — error paths
# ---------------------------------------------------------------------------


class TestRunShellErrors:
    def _run_with(self, lookup_state, *, detail=None, ids=()):
        lookup = ContainerLookup(
            state=lookup_state,
            id=ids[0] if ids else None,
            ids=ids,
            detail=detail,
        )
        return patch("dcode.shell.find_container", return_value=lookup)

    def test_missing_devcontainer(self, tmp_path, capsys):
        # tmp_path has no .devcontainer at all
        proj = tmp_path / "empty"
        proj.mkdir()
        rc = run_shell(str(proj), insiders=False, shell_override=None)
        assert rc != 0
        assert "dcode doctor" in capsys.readouterr().err

    def test_state_missing_non_tty_message(self, tmp_path, capsys, monkeypatch):
        proj = _make_project(tmp_path)
        monkeypatch.setattr("sys.stdin", _TTYStringIO(isatty=False))
        monkeypatch.setattr("sys.stdout", _TTYStringIO(isatty=False))
        with self._run_with("missing"):
            rc = run_shell(str(proj), insiders=False, shell_override=None)
        assert rc != 0
        err = capsys.readouterr().err
        assert "no devcontainer is running" in err
        assert "run interactively to be prompted to build it" in err
        assert f"dcode {proj}" in err

    def test_state_stopped_non_tty_message(self, tmp_path, capsys, monkeypatch):
        proj = _make_project(tmp_path)
        monkeypatch.setattr("sys.stdin", _TTYStringIO(isatty=False))
        monkeypatch.setattr("sys.stdout", _TTYStringIO(isatty=False))
        with self._run_with("stopped", ids=("abc",)):
            rc = run_shell(str(proj), insiders=False, shell_override=None)
        assert rc != 0
        err = capsys.readouterr().err
        assert "run interactively to be prompted to start it" in err
        assert f"dcode {proj}" in err

    def test_state_ambiguous_lists_ids(self, tmp_path, capsys):
        proj = _make_project(tmp_path)
        with self._run_with("ambiguous", ids=("id1", "id2")):
            rc = run_shell(str(proj), insiders=False, shell_override=None)
        assert rc != 0
        err = capsys.readouterr().err
        assert "id1" in err and "id2" in err

    def test_state_docker_unavailable_includes_detail(self, tmp_path, capsys):
        proj = _make_project(tmp_path)
        with self._run_with("docker_unavailable", detail="cannot connect"):
            rc = run_shell(str(proj), insiders=False, shell_override=None)
        assert rc != 0
        err = capsys.readouterr().err
        assert "Docker" in err
        assert "cannot connect" in err

    def test_remote_env_warning_fires_when_present(self, tmp_path, capsys):
        proj = _make_project(tmp_path, '{"remoteEnv": {}}')
        with _RunShellHarness():
            run_shell(str(proj), insiders=False, shell_override=None)
        err = capsys.readouterr().err
        assert "remoteEnv" in err and "not applied" in err

    def test_remote_env_warning_silent_when_absent(self, tmp_path, capsys):
        proj = _make_project(tmp_path, "{}")
        with _RunShellHarness():
            run_shell(str(proj), insiders=False, shell_override=None)
        err = capsys.readouterr().err
        assert "remoteEnv" not in err
