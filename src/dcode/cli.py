"""dcode — open folders in VS Code devcontainers from the CLI."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dcode.core import run_dcode
from dcode.doctor import run_doctor
from dcode.update import run_update, run_update_check

_SUBCOMMANDS = ("doctor", "update", "shell")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dcode",
        description=(
            "Open a folder in a VS Code devcontainer.\n"
            "\n"
            "`dcode doctor`, `dcode update`, and `dcode shell` always run their "
            "respective subcommands. To open a folder literally named "
            "'doctor', 'update', or 'shell', run `dcode ./doctor`, "
            "`dcode ./update`, or `dcode ./shell`."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-i",
        "--insiders",
        action="store_true",
        help="use VS Code Insiders",
    )
    subparsers = parser.add_subparsers(dest="command", required=False, metavar="COMMAND")

    p_doctor = subparsers.add_parser(
        "doctor",
        help="diagnose the local environment for dcode",
        description="Diagnose the local environment for dcode and report issues.",
    )
    p_doctor.add_argument(
        "doctor_path",
        nargs="?",
        default=None,
        metavar="path",
        help="directory to inspect (default: current directory)",
    )

    p_update = subparsers.add_parser(
        "update",
        help="upgrade dcode via 'uv tool upgrade dcode'",
        description="Upgrade the installed dcode tool via 'uv tool upgrade dcode'.",
    )
    p_update.add_argument(
        "--check",
        action="store_true",
        help="check for an available update without installing it",
    )

    p_shell = subparsers.add_parser(
        "shell",
        help="Open a shell in the project's running devcontainer",
        description=(
            "Open an interactive shell inside the running devcontainer for "
            "the project at `path`. Mirrors VS Code's integrated terminal: "
            "respects terminal profile settings (workspace > devcontainer > "
            "user), forwards the SSH agent socket when available, runs as "
            "`remoteUser`/`containerUser` from devcontainer.json. Requires "
            "an interactive terminal. To open a folder literally named "
            "'shell', use `dcode ./shell`."
        ),
    )
    p_shell.add_argument(
        "shell_path",
        nargs="?",
        default=".",
        metavar="path",
        help="project folder (default: current directory)",
    )
    p_shell.add_argument(
        "--shell",
        default=None,
        dest="shell_override",
        metavar="EXECUTABLE",
        help=(
            "literal shell executable to use (overrides VS Code settings); "
            "no shell-style argument splitting"
        ),
    )

    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="folder to open (default: current directory)",
    )
    return parser


def _looks_like_subcommand(argv: list[str]) -> bool:
    """Peek argv to decide whether to use the subcommand-aware parser.

    argparse with both a top-level positional ``path`` and subparsers
    misparses ``dcode ./somepath`` ("invalid choice"). Workaround: only
    enable subparsers when the first non-flag token is a known subcommand.
    """
    for tok in argv:
        if tok in ("-h", "--help"):
            # Let the full parser handle help so users see subcommands.
            return True
        if tok.startswith("-"):
            continue
        return tok in _SUBCOMMANDS
    return False


def main() -> None:
    argv = sys.argv[1:]
    parser: argparse.ArgumentParser | None = None
    if _looks_like_subcommand(argv):
        parser = _build_parser()
        args = parser.parse_args(argv)
    else:
        # Legacy path-only parser — avoids argparse's subparser/positional clash.
        legacy = argparse.ArgumentParser(prog="dcode")
        legacy.add_argument("-i", "--insiders", action="store_true")
        legacy.add_argument("path", nargs="?", default=".")
        args = legacy.parse_args(argv)
        args.command = None

    if args.command == "doctor":
        path = Path(args.doctor_path) if args.doctor_path else Path.cwd()
        sys.exit(run_doctor(path))

    if args.command == "update":
        if args.check:
            sys.exit(run_update_check())
        sys.exit(run_update())

    if args.command == "shell":
        shell_override = args.shell_override
        if shell_override is not None and (
            shell_override.strip() != shell_override or any(c.isspace() for c in shell_override)
        ):
            assert parser is not None
            parser.error(
                "--shell must be a single executable path or name (no arguments); "
                "use VS Code terminal profile args for that"
            )
        from dcode.shell import run_shell
        sys.exit(run_shell(args.shell_path, insiders=args.insiders, shell_override=shell_override))

    run_dcode(args.path, insiders=args.insiders)
