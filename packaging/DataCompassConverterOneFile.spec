from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files


project_root = Path(SPECPATH).resolve().parent
datas = collect_data_files("docx")

analysis = Analysis(
    [str(project_root / "data_compass_exe_main.py")],
    pathex=[str(project_root / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=["yaml", "yaml._yaml"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pbixray", "pandas", "numpy", "apsw", "xpress8", "xpress9", "xmhuffman"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="DataCompassConverter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=True,
)
