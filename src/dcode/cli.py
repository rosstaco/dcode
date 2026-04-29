"""dcode — open folders in VS Code devcontainers from the CLI."""

from __future__ import annotations

import argparse

from dcode.core import run_dcode


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="dcode",
        description="Open a folder in a VS Code devcontainer.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="folder to open (default: current directory)",
    )
    parser.add_argument(
        "-i",
        "--insiders",
        action="store_true",
        help="use VS Code Insiders",
    )
    args = parser.parse_args()
    run_dcode(args.path, insiders=args.insiders)
