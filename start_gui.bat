@echo off
rem Double-click to launch the AutoFishing GUI.
cd /d "%~dp0"
python gui.py
if errorlevel 1 (
    echo.
    echo AutoFishing exited with an error. Did you run:  pip install -r requirements.txt ?
    pause
)
