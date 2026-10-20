# Changelog

## 0.1.0

First public version.

- Command-line tool and Qt GUI that place net labels from a CubeMX pinout CSV onto an Altium symbol.
- Multi-part symbols (U1A, U1B, ...) handled per part.
- Wire stub + net label per pin, with configurable font, colour, length and grid.
- Optional No ERC markers on unconnected, non-power pins.
- Restores pin function data that altium-monkey drops on save, then verifies the result.
- GUI: themes, remembered settings, SVG preview, "open output folder".
