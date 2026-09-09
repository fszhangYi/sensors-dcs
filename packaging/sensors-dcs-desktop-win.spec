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
    (str(SRC / "sensors_dcs" / "static"), "sensors_dcs/static"),
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
    "pydantic_core",
    "httptools",
    "websockets",
    "webview",
    "numpy",
    "scipy",
    "scipy.optimize",
    "scipy.spatial.transform",
    "pandas",
    "pyarrow",
    "pyarrow.lib",
    "pyarrow.parquet",
    "elite",
    "elite._ec",
    "elite._monitor",
    "loguru",
    "pyrealsense2",
    "pkgutil",
    "sensors_dcs",
    "sensors_dcs.cli",
    "sensors_dcs.config",
    "sensors_dcs.desktop_main",
    "sensors_dcs.ui_serve",
    "sensors_dcs.arm_pose",
    "sensors_dcs.export",
    "sensors_dcs.export.timeline",
    "sensors_dcs.export.filter",
    "sensors_dcs.frame",
    "sensors_dcs.buffer",
    "sensors_dcs.paths",
    "sensors_dcs.static_assets",
    "sensors_dcs.runtime",
    "sensors_dcs.viz",
    "sensors_dcs.agents",
    "sensors_dcs.agents.base",
    "sensors_dcs.agents.gello_agent",
    "sensors_dcs.agents.gripper_read_agent",
    "sensors_dcs.agents.realsense_agent",
    "sensors_dcs.agents.arm_agent",
    "sensors_dcs.export.parquet_io",
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
    "sensors.kinematics",
    "sensors.kinematics.ik",
    "sensors.kinematics.fk",
]
hiddenimports += collect_submodules("sensors.drivers")
hiddenimports += collect_submodules("sensors.kinematics")
hiddenimports += collect_submodules("sensors_dcs")
# scipy/pandas/pyarrow collected in extend_analysis (with *.tests filtered).
# Skip duplicate collect_submodules("scipy") — it balloons Wine Analysis memory/time.
_no_tests = lambda name: ".tests" not in name and not name.endswith(".tests")  # noqa: E731
hiddenimports += collect_submodules("pyarrow", filter=_no_tests)
hiddenimports += collect_submodules("pandas", filter=_no_tests)
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
    runtime_hooks=[
        str(ROOT / "packaging" / "runtime_hook_path.py"),
        str(ROOT / "packaging" / "runtime_hook_pyarrow.py"),
    ],
    excludes=[
        "tkinter",
        "matplotlib",
        "PySide2",
        "PySide6",
        "PyQt5",
        "PyQt6",
        "watchfiles",
        "watchfiles.main",
        "watchfiles.run",
        "watchfiles._rust_notify",
        # Trim scipy surface for Wine 2GiB cgroup Analysis (IK only needs optimize + spatial).
        "scipy.special",
        "scipy.signal",
        "scipy.ndimage",
        "scipy.integrate",
        "scipy.fft",
        "scipy.fftpack",
        "scipy.interpolate",
        "scipy.stats",
        "scipy.io",
        "scipy.misc",
        "scipy.cluster",
        "scipy.odr",
        "scipy.datasets",
    ],
    noarchive=False,
    module_collection_mode={
        "numpy": "py",
    },
)

pyz = PYZ(a.pure)

_icon = SRC / "sensors_dcs" / "static" / "favicon.ico"
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
    icon=str(_icon) if _icon.is_file() else None,
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
