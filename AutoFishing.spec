# PyInstaller spec -> dist/AutoFishing.exe (single file, no console window)
# Build with:  pyinstaller --noconfirm AutoFishing.spec
a = Analysis(
    ["gui.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    # Big packages that get pulled in transitively but are never used here.
    excludes=["matplotlib", "scipy", "pandas", "pytest", "IPython", "notebook"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="AutoFishing",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,        # UPX-packed exes trigger far more antivirus false positives
    runtime_tmpdir=None,
    console=False,    # GUI app: no terminal window
    disable_windowed_traceback=False,
)
