# Developer Guide

## Set up from source

Use Python 3.12-3.14.

```bash
git clone https://github.com/MBrahim/cubemx-altium-netlabels.git
cd cubemx-altium-netlabels
python -m venv .venv
```

Activate the environment, then install the GUI and test dependencies:

```powershell
.venv\Scripts\Activate.ps1
pip install -e ".[gui,test]"
```

On Linux or macOS, activate with `source .venv/bin/activate` instead.

Run the GUI from source with `python -m cube_netlabels --gui` or run `python -m cube_netlabels` with the desired CLI options.

## Run tests

```bash
pip install -e ".[test]"
pytest
```

The tests cover CSV parsing, pin and part selection, direction logic, runtime launching, and pin-record preservation. They do not require Altium files; the GUI is not covered by automated tests.