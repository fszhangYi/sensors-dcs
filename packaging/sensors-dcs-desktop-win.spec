# -*- mode: python ; coding: utf-8 -*-
"""Windows / Wine onedir spec for sensors-dcs."""

from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821
sys.path.insert(0, str(ROOT / "packaging"))
from hardware_bundle import extend_analysis  # noqa: E402

SRC = ROOT / "src"
SENSORS_SRC = (ROOT / "sensors" / "src").resolve()
if not SENSORS_SRC.is_dir():
    SENSORS_SRC = (ROOT.parent / "sensors" / "src").resolve()
SENSORS_CFG = (ROOT / "sensors" / "configs").resolve()
if not SENSORS_CFG.is_dir():
    SENSORS_CFG = (ROOT.parent / "sensors" / "configs").resolve()

datas = [
    (str(ROOT / "configs"), "configs"),
]
binaries: list = []
if SENSORS_SRC.is_dir():
    datas.append((str(SENSORS_SRC / "sensors"), "sensors_src/sensors"))
if SENSORS_CFG.is_dir():
    datas.append((str(SENSORS_CFG), "sensors/configs"))

frontend_dist = ROOT / "packaging" / "frontend-dist-stub"
if frontend_dist.is_dir():
    datas.append((str(frontend_dist), "frontend-dist"))

hiddenimports = [
    "uvicorn",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "fastapi",
    "starlette",
    "yaml",
    "pydantic",
    "numpy",
    "pkgutil",
    "sensors_dcs",
    "sensors_dcs.cli",
    "sensors_dcs.config",
    "sensors_dcs.desktop_main",
    "sensors_dcs.frame",
    "sensors_dcs.buffer",
    "sensors_dcs.paths",
    "sensors_dcs.runtime",
    "sensors_dcs.viz",
    "sensors_dcs.agents",
    "sensors_dcs.agents.base",
    "sensors_dcs.agents.gello_agent",
    "sensors_dcs.agents.gripper_read_agent",
    "sensors",
    "sensors.core",
    "sensors.core.base",
    "sensors.core.config",
    "sensors.core.health",
    "sensors.core.kinds",
    "sensors.core.registry",
    "sensors.runtime",
    "sensors.runtime.manager",
    "sensors.drivers",
]
hiddenimports += collect_submodules("sensors.drivers")
hiddenimports += collect_submodules("sensors_dcs")
datas, binaries, hiddenimports = extend_analysis(datas, binaries, hiddenimports)

pathex = [str(SRC)]
if SENSORS_SRC.is_dir():
    pathex.append(str(SENSORS_SRC))

a = Analysis(
    [str(SRC / "sensors_dcs" / "desktop_main.py")],
    pathex=pathex,
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(ROOT / "packaging" / "runtime_hook_path.py")],
    excludes=["tkinter", "matplotlib", "PySide2", "PySide6", "PyQt5", "PyQt6"],
    noarchive=False,
    module_collection_mode={
        "numpy": "py",
    },
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="sensors-dcs",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="sensors-dcs",
)
