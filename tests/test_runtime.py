import sys

from cube_netlabels.runtime import cli_process_command


def test_cli_command_uses_python_module_in_source_runtime():
    program, arguments = cli_process_command(["--list-designators", "board.SchDoc"])

    assert program == sys.executable
    assert arguments == [
        "-m", "cube_netlabels", "--list-designators", "board.SchDoc",
    ]


def test_cli_command_preserves_argument_boundaries():
    arguments = ["--designator", "U1", "input schematic.SchDoc"]

    program, actual_arguments = cli_process_command(arguments)

    assert program == sys.executable
    assert actual_arguments == ["-m", "cube_netlabels", *arguments]





