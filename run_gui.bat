@echo off
cd /d "%~dp0"
python -m cube_netlabels --gui
if errorlevel 1 pause
