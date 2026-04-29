"""dcode — open folders in VS Code devcontainers from the CLI."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dcode.core import run_dcode
from dcode.doctor import run_doctor
from dcode.update import run_update, run_update_check

_SUBCOMMANDS = ("doctor", "update")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dcode",
        description=(
            "Open a folder in a VS Code devcontainer.\n"
            "\n"
            "`dcode doctor` and `dcode update` always run their respective subcommands. "
            "To open a folder literally named 'doctor' or 'update', "
            "run `dcode ./doctor` or `dcode ./update`."
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

    run_dcode(args.path, insiders=args.insiders)
