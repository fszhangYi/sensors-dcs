"""PyInstaller collection for sensors-dcs desktop (core, hardware, export, UI)."""

from __future__ import annotations

from PyInstaller.utils.hooks import collect_all, collect_submodules

# Always required in desktop builds
CORE_PACKAGES = ("numpy",)

# Gello / gripper serial + camera JPEG encode (requirements-hardware.txt)
HARDWARE_PACKAGES = (
    "dynamixel_sdk",
    "serial",
    "cv2",
)

# export-timeline / filter-timeline (requirements-desktop.txt / pyproject [export])
EXPORT_PACKAGES = (
    "pandas",
    "pyarrow",
)

# --ui pywebview window (requirements-desktop.txt; import name is webview)
UI_PACKAGES = ("webview",)

# Installed via requirements.txt uvicorn[standard] — collect for frozen WS server
RUNTIME_PACKAGES = (
    "httptools",
    "websockets",
    "watchfiles",
)

# pyproject [realsense] — NOT bundled; target machine installs Intel SDK + wheel
TARGET_OPTIONAL = ("pyrealsense2",)

_TEST_PATH_MARKERS = (
    "/numpy/tests/",
    "/numpy/_core/tests/",
    "/numpy/f2py/tests/",
    "/numpy/typing/tests/",
    "/pandas/tests/",
    "/pyarrow/tests/",
)


def _filter_test_artifacts(datas: list) -> list:
    out = []
    for item in datas:
        path = str(item[0]).replace("\\", "/")
        if any(marker in path for marker in _TEST_PATH_MARKERS):
            continue
        out.append(item)
    return out


def _collect_package(
    pkg: str,
    *,
    datas: list,
    binaries: list,
    hiddenimports: list,
    missing: list[str],
) -> None:
    try:
        d, b, h = collect_all(pkg)
    except Exception as exc:  # noqa: BLE001
        missing.append(f"{pkg} ({exc})")
        return
    if pkg in CORE_PACKAGES or pkg in EXPORT_PACKAGES:
        d = _filter_test_artifacts(d)
    datas += d
    binaries += b
    hiddenimports += h
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception:  # noqa: BLE001
        pass


def extend_analysis(
    datas: list,
    binaries: list,
    hiddenimports: list,
) -> tuple[list, list, list]:
    missing: list[str] = []
    for pkg in (
        *CORE_PACKAGES,
        *HARDWARE_PACKAGES,
        *EXPORT_PACKAGES,
        *UI_PACKAGES,
        *RUNTIME_PACKAGES,
    ):
        _collect_package(pkg, datas=datas, binaries=binaries, hiddenimports=hiddenimports, missing=missing)

    hiddenimports += [
        "numpy",
        "numpy.__config__",
        "numpy.version",
        "numpy.core",
        "numpy.core._multiarray_umath",
        "pandas",
        "pyarrow",
        "pyarrow.lib",
        "pyarrow.parquet",
        "dynamixel_sdk",
        "dynamixel_sdk.robotis_def",
        "dynamixel_sdk.packet_handler",
        "dynamixel_sdk.port_handler",
        "dynamixel_sdk.group_sync_read",
        "serial",
        "serial.serialutil",
        "serial.tools",
        "serial.tools.list_ports",
        "cv2",
        "webview",
        "pydantic_core",
        "httptools",
        "websockets",
        "watchfiles",
        "sensors_dcs.ui_serve",
        "sensors_dcs.export",
        "sensors_dcs.export.timeline",
        "sensors_dcs.export.filter",
    ]
    if missing:
        raise RuntimeError(
            "desktop bundle packages missing in build env "
            "(pip install -r requirements.txt -r requirements-desktop.txt -r requirements-hardware.txt): "
            + "; ".join(missing)
        )
    return datas, binaries, hiddenimports
