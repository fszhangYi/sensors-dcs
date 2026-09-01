"""Shared PyInstaller collection for sensors-dcs hardware extras (gello)."""

from __future__ import annotations

from PyInstaller.utils.hooks import collect_all, collect_submodules

HARDWARE_PACKAGES = (
    "dynamixel_sdk",
    "serial",
    "cv2",
)

CORE_PACKAGES = ("numpy",)


def extend_analysis(
    datas: list,
    binaries: list,
    hiddenimports: list,
) -> tuple[list, list, list]:
    missing: list[str] = []
    for pkg in (*CORE_PACKAGES, *HARDWARE_PACKAGES):
        try:
            d, b, h = collect_all(pkg)
        except Exception as exc:  # noqa: BLE001
            missing.append(f"{pkg} ({exc})")
            continue
        if pkg == "numpy":
            d = [
                item
                for item in d
                if not any(
                    part in str(item[0]).replace("\\", "/")
                    for part in (
                        "/numpy/tests/",
                        "/numpy/_core/tests/",
                        "/numpy/f2py/tests/",
                        "/numpy/typing/tests/",
                    )
                )
            ]
        datas += d
        binaries += b
        hiddenimports += h
        try:
            hiddenimports += collect_submodules(pkg)
        except Exception:  # noqa: BLE001
            pass

    hiddenimports += [
        "numpy",
        "numpy.__config__",
        "numpy.version",
        "numpy.core",
        "numpy.core._multiarray_umath",
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
    ]
    if missing:
        raise RuntimeError(
            "hardware/core packages missing in build env (install requirements-hardware.txt): "
            + "; ".join(missing)
        )
    return datas, binaries, hiddenimports
