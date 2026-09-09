"""PyInstaller collection for sensors-dcs desktop (core, hardware, export, UI)."""

from __future__ import annotations

from PyInstaller.utils.hooks import (
    collect_all,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
)

# Always required in desktop builds
CORE_PACKAGES = ("numpy",)

# Gello / gripper serial + camera JPEG encode + Elite arm + RealSense (requirements-hardware.txt)
HARDWARE_PACKAGES = (
    "dynamixel_sdk",
    "serial",
    "cv2",
    "elite",
    "loguru",
    "pyrealsense2",
)

# pyproject [realsense] alias — bundled via HARDWARE_PACKAGES for desktop
TARGET_OPTIONAL: tuple[str, ...] = ()

# Infer / arm IK (requirements-desktop.txt; sensors.kinematics.ik)
KINEMATICS_PACKAGES = (
    "scipy",
)

# export-timeline / filter-timeline (requirements-desktop.txt / pyproject [export])
EXPORT_PACKAGES = (
    "pandas",
    "pyarrow",
)

# --ui pywebview window (requirements-desktop.txt; import name is webview)
UI_PACKAGES = ("webview",)

# Installed via requirements.txt uvicorn[standard] — collect for frozen WS server.
# watchfiles: never collect_all / binaries. Its Rust extension (_rust_notify.pyd)
# links bcryptprimitives.dll (missing under Wine 6 / Win7-ish prefixes) and can
# stall PyInstaller binary dependency resolution. Desktop freeze does not need
# uvicorn --reload. Name may still appear as a soft hiddenimport on Linux only.
RUNTIME_PACKAGES = (
    "httptools",
    "websockets",
)
# Intentionally empty: do not pull watchfiles into Analysis binaries.
RUNTIME_OPTIONAL_PACKAGES: tuple[str, ...] = ()

_TEST_PATH_MARKERS = (
    "/numpy/tests/",
    "/numpy/_core/tests/",
    "/numpy/f2py/tests/",
    "/numpy/typing/tests/",
    "/pandas/tests/",
    "/pyarrow/tests/",
    "/scipy/stats/tests/",
    "/scipy/optimize/tests/",
    "/scipy/spatial/tests/",
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
    if pkg in CORE_PACKAGES or pkg in EXPORT_PACKAGES or pkg in KINEMATICS_PACKAGES:
        d = _filter_test_artifacts(d)
    datas += d
    binaries += b
    hiddenimports += h
    try:
        # Skip *.tests under Wine Analysis (pytest missing; also huge / slow).
        if pkg in EXPORT_PACKAGES or pkg in KINEMATICS_PACKAGES or pkg in CORE_PACKAGES:
            hiddenimports += collect_submodules(
                pkg,
                filter=lambda name: ".tests" not in name and not name.endswith(".tests"),
            )
        else:
            hiddenimports += collect_submodules(pkg)
    except Exception:  # noqa: BLE001
        pass


def _collect_libs_folder(pkg: str, *, datas: list) -> None:
    """Bundle PEP-600 style ``<pkg>.libs`` MSVC runtime DLLs (required for pyarrow on Windows)."""
    try:
        mod = __import__(pkg)
    except Exception:  # noqa: BLE001
        return
    from pathlib import Path

    libs = Path(mod.__file__).resolve().parent.parent / f"{pkg}.libs"
    if not libs.is_dir():
        return
    for item in sorted(libs.iterdir()):
        if item.is_file():
            datas.append((str(item), f"{pkg}.libs"))


def extend_analysis(
    datas: list,
    binaries: list,
    hiddenimports: list,
) -> tuple[list, list, list]:
    missing: list[str] = []
    for pkg in (
        *CORE_PACKAGES,
        *HARDWARE_PACKAGES,
        # scipy: selective below (full collect_all blows Wine ~2GiB cgroup)
        *EXPORT_PACKAGES,
        *UI_PACKAGES,
        *RUNTIME_PACKAGES,
    ):
        _collect_package(pkg, datas=datas, binaries=binaries, hiddenimports=hiddenimports, missing=missing)

    soft_missing: list[str] = []
    for pkg in RUNTIME_OPTIONAL_PACKAGES:
        _collect_package(
            pkg, datas=datas, binaries=binaries, hiddenimports=hiddenimports, missing=soft_missing
        )
    if soft_missing:
        print(
            "[hardware_bundle] optional package collect skipped (Wine/DLL ok to miss): "
            + "; ".join(soft_missing)
        )
        # Still list the top-level name so frozen uvicorn can try to import it.
        hiddenimports += list(RUNTIME_OPTIONAL_PACKAGES)

    # SciPy is not collect_all'd above — only listed in hiddenimports + dynamic libs
    # loops below (IK needs optimize + spatial; full collect_all OOMs Wine 2GiB cgroup).

    hiddenimports += [
        "numpy",
        "numpy.__config__",
        "numpy.version",
        "numpy.core",
        "numpy.core._multiarray_umath",
        "scipy",
        "scipy.optimize",
        "scipy.optimize._lsq",
        "scipy.optimize._lsq.least_squares",
        "scipy.spatial",
        "scipy.spatial.transform",
        "scipy.spatial.transform._rotation_groups",
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
        "elite",
        "elite._ec",
        "elite._monitor",
        "loguru",
        "pyrealsense2",
        "pyrealsense2.pyrealsense2",
        "webview",
        "pydantic_core",
        "httptools",
        "websockets",
        "sensors_dcs.ui_serve",
        "sensors_dcs.arm_pose",
        "sensors_dcs.export",
        "sensors_dcs.export.timeline",
        "sensors_dcs.export.filter",
        "sensors_dcs.export.parquet_io",
        "sensors.kinematics",
        "sensors.kinematics.ik",
        "sensors.kinematics.fk",
    ]
    for pkg in (*EXPORT_PACKAGES, *KINEMATICS_PACKAGES):
        _collect_libs_folder(pkg, datas=datas)

    for pkg in (*HARDWARE_PACKAGES, *KINEMATICS_PACKAGES, *EXPORT_PACKAGES):
        try:
            datas += copy_metadata(pkg)
        except Exception:  # noqa: BLE001
            pass
        if pkg in ("pyrealsense2", "cv2", "pyarrow", "pandas", "scipy"):
            try:
                binaries += collect_dynamic_libs(pkg)
            except Exception:  # noqa: BLE001
                pass

    hiddenimports += [
        "pyarrow._parquet",
        "pyarrow._fs",
        "pyarrow._hdfs",
        "pyarrow._gcsfs",
        "pyarrow._s3fs",
        "pyarrow.vendored.version",
    ]

    if missing:
        raise RuntimeError(
            "desktop bundle packages missing in build env "
            "(pip install -r requirements.txt -r requirements-desktop.txt -r requirements-hardware.txt): "
            + "; ".join(missing)
        )
    return datas, binaries, hiddenimports
