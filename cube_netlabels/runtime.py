"""Resolve the source CLI command used by the GUI."""
import sys


def cli_process_command(arguments: list[str]) -> tuple[str, list[str]]:
    return sys.executable, ["-m", "cube_netlabels", *arguments]
