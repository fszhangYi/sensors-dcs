#!/usr/bin/env python3
"""Audit importable modules for sensors-dcs desktop / export features."""

from __future__ import annotations

import importlib.util
import sys

# (import_name, feature, required_for_desktop_build)
CHECKS: list[tuple[str, str, bool]] = [
    ("numpy", "core / viz / export", True),
    ("yaml", "YAML config", True),
    ("pydantic", "config schema", True),
    ("pydantic_core", "pydantic v2 runtime", True),
    ("fastapi", "viz HTTP API", True),
    ("uvicorn", "local server", True),
    ("starlette", "fastapi dependency", True),
    ("httptools", "uvicorn HTTP (standard extra)", True),
    ("websockets", "viz WebSocket", True),
    ("watchfiles", "uvicorn reload (standard extra)", True),
    ("cv2", "camera JPEG encode", True),
    ("dynamixel_sdk", "Gello agent", True),
    ("serial", "Gello / gripper serial", True),
    ("pandas", "export-timeline / filter-timeline", True),
    ("pyarrow", "Parquet export", True),
    ("webview", "desktop --ui window", True),
    ("elite", "Elite arm EC SDK (elirobots)", True),
    ("pyrealsense2", "live RealSense", True),
]

OPTIONAL_OK = {name for name, _, required in CHECKS if not required}


def main() -> int:
    missing_required: list[str] = []
    missing_optional: list[str] = []
    ok: list[str] = []

    for mod, feature, required in CHECKS:
        if importlib.util.find_spec(mod) is None:
            line = f"  {mod:16}  {feature}"
            if required:
                missing_required.append(line)
            else:
                missing_optional.append(line)
        else:
            ok.append(mod)

    print("sensors-dcs desktop dependency audit\n")
    print(f"OK ({len(ok)}): {', '.join(ok)}")
    if missing_optional:
        print("\nOptional (expected missing in dev / bundled in target notes):")
        print("\n".join(missing_optional))
    if missing_required:
        print("\nMISSING (install before desktop build):")
        print("\n".join(missing_required))
        print(
            "\nInstall:\n"
            "  pip install -r requirements.txt "
            "-r requirements-desktop.txt -r requirements-hardware.txt"
        )
        return 1
    print("\nAll required desktop build imports present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
