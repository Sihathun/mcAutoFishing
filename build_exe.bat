@echo off
rem Builds dist\AutoFishing.exe in an isolated virtual environment (.venv-build).
cd /d "%~dp0"

if not exist .venv-build (
    echo Creating build environment...
    python -m venv .venv-build || goto :fail
)
call .venv-build\Scripts\activate.bat || goto :fail

python -m pip install --quiet --upgrade pip || goto :fail
python -m pip install --quiet -r requirements-build.txt || goto :fail

pyinstaller --noconfirm --clean AutoFishing.spec || goto :fail

echo.
echo Done: dist\AutoFishing.exe
exit /b 0

:fail
echo.
echo Build failed.
exit /b 1
