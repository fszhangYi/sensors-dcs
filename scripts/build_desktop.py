#!/usr/bin/env python3
"""Build sensors-dcs desktop packages (Windows via Wine — Scheme A)."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
APP_ID = "sensors-dcs"
APP_EXE = "sensors-dcs"
PIP_INDEX = os.environ.get("PIP_INDEX_URL", "https://pypi.tuna.tsinghua.edu.cn/simple")
TMP_BASE = Path(os.environ.get("SENSORS_DCS_BUILD_TMP", "/root/autodl-tmp/tmp"))


def _utc_stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def _run(
    cmd: list[str],
    *,
    env: dict | None = None,
    cwd: Path | None = None,
    check: bool = True,
) -> int:
    print("+", " ".join(cmd), flush=True)
    rc = subprocess.call(cmd, env=env, cwd=str(cwd or ROOT))
    if check and rc != 0:
        raise subprocess.CalledProcessError(rc, cmd)
    return rc


def _linux_to_wine_path(path: Path) -> str:
    p = path.resolve()
    return "Z:" + str(p).replace("/", "\\")


def _ensure_win_python(tools: Path) -> Path:
    py = tools / "win-python"
    python_exe = py / "python.exe"
    if python_exe.is_file():
        return py

    # Reuse sibling sensors-view embed Python when available (saves download).
    sibling = ROOT.parent / "sensors-view" / ".tools" / "win-python" / "python.exe"
    if sibling.is_file():
        print(f"[win-python] copy from {sibling.parent}", flush=True)
        tools.mkdir(parents=True, exist_ok=True)
        if py.exists():
            shutil.rmtree(py)
        shutil.copytree(sibling.parent, py)
        return py

    tools.mkdir(parents=True, exist_ok=True)
    ver = "3.10.11"
    urls = [
        f"https://registry.npmmirror.com/-/binary/python/{ver}/python-{ver}-embed-amd64.zip",
        f"https://www.python.org/ftp/python/{ver}/python-{ver}-embed-amd64.zip",
    ]
    zip_path = tools / f"python-{ver}-embed-amd64.zip"
    if not zip_path.is_file() or zip_path.stat().st_size < 1_000_000:
        ok = False
        for url in urls:
            try:
                _run(["curl", "-L", "--retry", "3", "-o", str(zip_path), url])
                if zip_path.is_file() and zip_path.stat().st_size > 1_000_000:
                    ok = True
                    break
            except Exception:
                continue
        if not ok:
            raise SystemExit("failed to download embeddable CPython")
    if py.exists():
        shutil.rmtree(py)
    py.mkdir(parents=True)
    _run(["unzip", "-o", str(zip_path), "-d", str(py)])

    pth = next(py.glob("python*._pth"))
    lines = pth.read_text(encoding="utf-8").splitlines()
    out = []
    for line in lines:
        s = line.strip()
        if s.startswith("#") and "import site" in s:
            out.append("import site")
        elif s == "import site":
            out.append("import site")
        else:
            out.append(line)
    if "import site" not in "\n".join(out):
        out.append("import site")
    if "Lib\\site-packages" not in "\n".join(out) and "Lib/site-packages" not in "\n".join(out):
        out.append("Lib\\site-packages")
    pth.write_text("\n".join(out) + "\n", encoding="utf-8")
    (py / "Lib" / "site-packages").mkdir(parents=True, exist_ok=True)

    get_pip = tools / "get-pip.py"
    if not get_pip.is_file():
        _run(["curl", "-L", "-o", str(get_pip), "https://bootstrap.pypa.io/get-pip.py"])
    return py


def _wine_env(prefix: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["WINEPREFIX"] = str(prefix)
    env["WINEARCH"] = "win64"
    env["WINEDEBUG"] = "-all"
    env["PIP_INDEX_URL"] = PIP_INDEX
    env["PYTHONNOUSERSITE"] = "1"
    return env


def _wine_bin() -> str:
    for cand in ("wine64", "wine"):
        if shutil.which(cand):
            return cand
    raise SystemExit("wine64/wine not found — install wine (win64) first")


def _verify_bundled_imports(py_cmd: list[str], *, env: dict | None = None) -> None:
    required = [
        "dynamixel_sdk",
        "serial",
        "numpy",
        "scipy",
        "pandas",
        "pyarrow",
        "elite",
        "pyrealsense2",
        "cv2",
        "webview",
        "httptools",
        "websockets",
        "pydantic_core",
        "pydantic",
        "fastapi",
        "uvicorn",
    ]
    # watchfiles is optional under Wine (Rust extension often fails DLL load); still try.
    optional = ["watchfiles"]
    script = TMP_BASE / "_verify_wine_imports.py"
    script.write_text(
        "\n".join(
            [
                "import importlib",
                "import importlib.util as u",
                "import os",
                "import sys",
                "import tempfile",
                f"mods = {required!r}",
                f"optional = {optional!r}",
                "missing = [m for m in mods if u.find_spec(m) is None]",
                "if missing:",
                "    sys.exit('missing ' + str(missing))",
                "import pydantic",
                "import pydantic_core",
                "import fastapi",
                "import pandas as pd",
                "import pyarrow",
                "from scipy.optimize import least_squares",
                "from scipy.spatial.transform import Rotation",
                "from elite import EC",
                "import pyrealsense2",
                'p = os.path.join(tempfile.gettempdir(), "sensors_dcs_parquet_smoke.parquet")',
                'pd.DataFrame({"x": [1]}).to_parquet(p, index=False, engine="pyarrow")',
                "os.remove(p)",
                "opt_ok = []",
                "for m in optional:",
                "    try:",
                "        importlib.import_module(m)",
                "        opt_ok.append(m)",
                "    except Exception as e:",
                "        print('optional_skip', m, type(e).__name__, e)",
                "print('bundled_ok', len(mods), 'optional', opt_ok, 'pydantic', pydantic.__version__, 'core', pydantic_core.__version__)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    script_arg = _linux_to_wine_path(script) if py_cmd[0] in ("wine64", "wine") else str(script)
    _run([*py_cmd, script_arg], env=env)


def _verify_bundled_pyarrow(release: Path) -> None:
    internal = release / "_internal"
    pyarrow_dir = internal / "pyarrow"
    libs_dir = internal / "pyarrow.libs"
    if not pyarrow_dir.is_dir():
        raise SystemExit(
            f"build verification failed: missing {pyarrow_dir} — "
            "pyarrow was not collected into the desktop bundle"
        )
    has_ext = any(pyarrow_dir.rglob("*.pyd")) or any(pyarrow_dir.rglob("*.so"))
    if not has_ext:
        raise SystemExit(
            f"build verification failed: no pyarrow binary modules under {pyarrow_dir}"
        )
    dlls = sorted(libs_dir.glob("*.dll"))
    if not dlls:
        raise SystemExit(
            f"build verification failed: missing MSVC DLLs under {libs_dir} "
            "(pyarrow.libs must be bundled for Windows parquet export)"
        )
    print(
        f"[verify] pyarrow bundled ({len(list(pyarrow_dir.rglob('*')))} files, "
        f"{len(dlls)} pyarrow.libs dlls)"
    )


def _bundle_native_libs(built: Path, py_dir: Path) -> None:
    """Copy ``*.libs`` MSVC runtime folders PyInstaller often drops for pyarrow/pandas/scipy."""
    internal = built / "_internal"
    site = py_dir / "Lib" / "site-packages"
    for name in ("pyarrow", "pandas", "scipy"):
        src = site / f"{name}.libs"
        dst = internal / f"{name}.libs"
        if not src.is_dir():
            if name == "pyarrow":
                raise SystemExit(
                    f"missing {src} in Wine embed Python — pip install pyarrow in build env"
                )
            print(f"[warn] {name}.libs not found under {site}")
            continue
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        print(f"[postprocess] bundled {name}.libs ({len(list(dst.glob('*.dll')))} dlls)")


def _verify_bundled_scipy(release: Path) -> None:
    internal = release / "_internal"
    scipy_dir = internal / "scipy"
    if not scipy_dir.is_dir():
        raise SystemExit(
            f"build verification failed: missing {scipy_dir} — "
            "scipy was not collected (Infer IK needs scipy.optimize.least_squares)"
        )
    has_ext = any(scipy_dir.rglob("*.pyd")) or any(scipy_dir.rglob("*.so"))
    if not has_ext:
        raise SystemExit(
            f"build verification failed: no scipy binary modules under {scipy_dir}"
        )
    print(f"[verify] scipy bundled ({len(list(scipy_dir.rglob('*')))} files)")


def _verify_frozen_parquet_smoke(
    wine: str,
    py_dir: Path,
    built: Path,
    env: dict[str, str],
) -> None:
    """Import pyarrow from the onedir _internal tree under Wine (matches Windows layout)."""
    internal_w = _linux_to_wine_path(built / "_internal")
    script = TMP_BASE / "_verify_frozen_parquet.py"
    script.write_text(
        "\n".join(
            [
                "import os, sys, tempfile",
                f"internal = {internal_w!r}",
                'pyarrow_libs = internal + r"\\pyarrow.libs"',
                'pyarrow_dir = internal + r"\\pyarrow"',
                "path_add = os.pathsep.join([pyarrow_libs, pyarrow_dir, internal])",
                'os.environ["PATH"] = path_add + os.pathsep + os.environ.get("PATH", "")',
                "sys.path.insert(0, internal)",
                "import pyarrow",
                "import pandas as pd",
                'p = os.path.join(tempfile.gettempdir(), "sensors_dcs_parquet_smoke.parquet")',
                'pd.DataFrame({"x": [1]}).to_parquet(p, index=False, engine="pyarrow")',
                "os.remove(p)",
                'print("frozen_parquet_ok", pyarrow.__version__)',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    py_wine = _linux_to_wine_path(py_dir / "python.exe")
    script_w = _linux_to_wine_path(script)
    _run([wine, py_wine, script_w], env=env)


def _verify_bundled_robot_sdk(release: Path) -> None:
    internal = release / "_internal"
    elite_dir = internal / "elite"
    if not elite_dir.is_dir():
        raise SystemExit(
            f"build verification failed: missing {elite_dir} "
            "(pip install elirobots in Wine build env)"
        )
    rs = list(internal.glob("pyrealsense2/**/*.pyd")) + list(internal.glob("pyrealsense2/*.pyd"))
    if not rs:
        raise SystemExit(
            f"build verification failed: pyrealsense2 binaries missing under {internal / 'pyrealsense2'}"
        )
    print(f"[verify] elite + pyrealsense2 bundled ({len(list(elite_dir.rglob('*.py')))} elite py files)")


def _postprocess_windows(built: Path, py_dir: Path) -> None:
    _bundle_native_libs(built, py_dir)
    internal = built / "_internal"
    internal.mkdir(parents=True, exist_ok=True)
    embed_zip = py_dir / "python310.zip"
    if embed_zip.is_file():
        shutil.copy2(embed_zip, internal / "python310.zip")

    base = internal / "base_library.zip"
    if not base.is_file():
        hits = list(internal.glob("base_library.zip"))
        if hits:
            base = hits[0]
    if not base.is_file() or not embed_zip.is_file():
        print("[warn] skip base_library inject (missing zip)")
        return

    import zipfile as zfmod

    need_prefixes = (
        "pkgutil",
        "inspect",
        "copy",
        "pathlib",
        "typing",
        "contextlib",
        "dataclasses",
        "importlib",
        "json",
        "logging",
        "collections",
        "urllib",
        "email",
        "html",
        "xml",
        "encodings",
        "asyncio",
        "concurrent",
        "multiprocessing",
        "zoneinfo",
    )
    with zfmod.ZipFile(embed_zip, "r") as src, zfmod.ZipFile(base, "a") as dst:
        existing = set(dst.namelist())
        for name in src.namelist():
            top = name.split("/")[0].split(".")[0]
            if any(top == p or name.startswith(p + "/") or name.startswith(p + ".") for p in need_prefixes):
                if name not in existing:
                    dst.writestr(name, src.read(name))


def _maybe_build_delta(release: Path) -> Path | None:
    sys.path.insert(0, str(SCRIPTS))
    try:
        from release_delta import build_delta_zip, pick_baseline, write_manifest
    finally:
        if sys.path and sys.path[0] == str(SCRIPTS):
            sys.path.pop(0)

    write_manifest(release)
    baseline = pick_baseline(release)
    if baseline is None:
        print("[delta] skip (no previous release under release/)")
        return None

    delta_zip = release.with_name(f"{release.name}-delta.zip")
    summary = build_delta_zip(current_dir=release, baseline_dir=baseline, output_zip=delta_zip)
    print(
        f"[delta] baseline={summary.baseline} "
        f"changed={len(summary.changed)} added={len(summary.added)} "
        f"removed={len(summary.removed)}"
    )
    print(f"delta zip: {delta_zip}")
    return delta_zip


def build_windows(*, skip_frontend: bool = True, delta: bool = True) -> Path:
    del skip_frontend  # UI is FastAPI-embedded; stub frontend-dist is enough
    if not (ROOT / "sensors" / "src" / "sensors").is_dir():
        raise SystemExit(
            "missing ./sensors symlink → hik-sensors src "
            "(ln -sfn ~/autodl-tmp/sensors ./sensors)"
        )

    wine = _wine_bin()
    tools = ROOT / ".tools"
    prefix = Path(os.environ.get("WINEPREFIX") or (ROOT / ".wine-sensors-dcs"))
    prefix.parent.mkdir(parents=True, exist_ok=True)
    py_dir = _ensure_win_python(tools)
    get_pip = tools / "get-pip.py"
    if not get_pip.is_file():
        _run(["curl", "-L", "-o", str(get_pip), "https://bootstrap.pypa.io/get-pip.py"])
    env = _wine_env(prefix)

    _run([wine, "wineboot", "--init"], env=env)

    py_wine = _linux_to_wine_path(py_dir / "python.exe")
    get_pip_w = _linux_to_wine_path(get_pip)
    _run([wine, py_wine, get_pip_w, "-i", PIP_INDEX], env=env)
    try:
        _run([wine, py_wine, "-m", "pip", "--version"], env=env)
    except subprocess.CalledProcessError as e:
        raise SystemExit(
            "pip not available in Wine embed Python after get-pip; "
            "re-download bootstrap.pypa.io/get-pip.py and ensure python*._pth has 'import site'"
        ) from e

    req = _linux_to_wine_path(ROOT / "requirements.txt")
    req_d = _linux_to_wine_path(ROOT / "requirements-desktop.txt")
    req_h = _linux_to_wine_path(ROOT / "requirements-hardware.txt")
    _run([wine, py_wine, "-m", "pip", "install", "-i", PIP_INDEX, "-r", req], env=env)
    _run([wine, py_wine, "-m", "pip", "install", "-i", PIP_INDEX, "-r", req_d], env=env)
    _run([wine, py_wine, "-m", "pip", "install", "-i", PIP_INDEX, "-r", req_h], env=env)
    # Force numpy pin last, then reinstall scipy in the range compatible with 1.23.5.
    _run(
        [
            wine,
            py_wine,
            "-m",
            "pip",
            "install",
            "-i",
            PIP_INDEX,
            "numpy==1.23.5",
            "scipy>=1.10,<1.12",
        ],
        env=env,
    )
    # Wine 6 / Win7-ish prefixes lack bcryptprimitives.dll; pydantic-core>=2.18 links it
    # and breaks PyInstaller Analysis (`import pydantic` → DLL load failed). Pin a stack
    # that still uses bcrypt.dll (present under Wine). Real Win10+ targets are fine either way.
    _run(
        [
            wine,
            py_wine,
            "-m",
            "pip",
            "install",
            "-i",
            PIP_INDEX,
            "pydantic-core==2.14.6",
            "pydantic==2.5.3",
            "fastapi==0.115.6",
        ],
        env=env,
    )
    # Drop watchfiles before Analysis: its _rust_notify.pyd also needs
    # bcryptprimitives.dll and has stalled Wine PyInstaller binary resolution.
    # uvicorn --reload is unused in the frozen desktop server.
    _run(
        [wine, py_wine, "-m", "pip", "uninstall", "-y", "watchfiles"],
        env=env,
        check=False,
    )
    _verify_bundled_imports([wine, py_wine], env=env)

    # AutoDL container cgroup is often ~2GiB. Wine Analysis peaks near that; restart
    # wineserver so a previous crashed build does not keep dirty pages around.
    _run([wine, "wineboot", "--end-session"], env=env, check=False)
    _run([wine, "wineboot", "--kill"], env=env, check=False)
    time.sleep(2)
    _run([wine, "wineboot", "--init"], env=env, check=False)

    TMP_BASE.mkdir(parents=True, exist_ok=True)
    dist = TMP_BASE / "sensors-dcs-dist-win"
    work = TMP_BASE / "sensors-dcs-work-win"
    for d in (dist, work):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)

    runner = _linux_to_wine_path(ROOT / "packaging" / "run_pyinstaller_wine.py")
    spec = _linux_to_wine_path(ROOT / "packaging" / "sensors-dcs-desktop-win.spec")
    dist_w = _linux_to_wine_path(dist)
    work_w = _linux_to_wine_path(work)
    _run(
        [
            wine,
            py_wine,
            runner,
            "--noconfirm",
            "--clean",
            "--distpath",
            dist_w,
            "--workpath",
            work_w,
            spec,
        ],
        env=env,
    )

    built = dist / APP_EXE
    if not built.is_dir():
        raise SystemExit(f"PyInstaller output missing: {built}")

    _postprocess_windows(built, py_dir)
    _verify_bundled_pyarrow(built)
    _verify_bundled_scipy(built)
    _verify_bundled_robot_sdk(built)
    _verify_frozen_parquet_smoke(wine, py_dir, built, env)

    stamp = _utc_stamp()
    release = ROOT / "release" / f"{APP_ID}-desktop-windows-x64-{stamp}"
    if release.exists():
        shutil.rmtree(release)
    shutil.copytree(built, release)

    info = {
        "app_id": APP_ID,
        "platform": "windows-x64",
        "self_contained": True,
        "requires_python_on_target": False,
        "built_at": stamp,
        "entry": f"{APP_EXE}.exe",
        "notes": "Built on Linux via Wine + embeddable CPython + PyInstaller (scheme-a-linux-to-windows-desktop)",
    }
    (release / "BUILD_INFO.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    (release / "README.txt").write_text(
        "\n".join(
            [
                "sensors-dcs desktop (Windows x64)",
                "",
                "1. Unzip this folder",
                "2. Double-click sensors-dcs.exe (headless backend; no window)",
                "   Or in cmd: sensors-dcs.exe --ui  (open viz window/browser)",
                "   Viz URL default: http://127.0.0.1:7011/",
                "3. No need to install Python / Node",
                "4. User data: %APPDATA%\\sensors-dcs\\",
                "5. Config after first run: %APPDATA%\\sensors-dcs\\configs\\",
                "   default / gello_only / gello_gripper / camera-only / camera-multi / full_cell",
                "   sensors_gello*.yaml → endpoint: \"COM3\" (or your COM port)",
                "   sensors_camera.yaml  → serial already 336222075436",
                "   sensors_cameras.yaml / sensors_full_cell.yaml",
                "     → middle fixed; fill left/right serials",
                "6. Or set env before launch: set SENSORS_DCS_DRY_RUN=0",
                "7. Live UI shows dry_run=false and no [synth] tag when real",
                "8. Open UI from terminal: sensors-dcs.exe --ui",
                "   Or env: set SENSORS_DCS_UI=1",
                "9. Force browser instead of webview: SENSORS_DCS_BROWSER=1",
                "10. Desktop window needs Microsoft Edge WebView2 Runtime",
                "11. Hardware: dynamixel-sdk + pyserial bundled (Gello)",
                "    Install FTDI/USB-serial driver; match baudrate (default 57600)",
                "12. RealSense: pyrealsense2 bundled; USB camera still needs Intel driver",
                "13. Export: sensors-dcs.exe export-timeline -e episode_00000 --align asof",
                "    filter-timeline also bundled (pandas/pyarrow in _internal/pyarrow/)",
                "    First install: use the FULL .zip; delta.zip only patches an existing install",
                "14. Elite arm (arm_read): elite SDK bundled (PyPI elirobots); set robot_ip in config",
                "    Monitor port 8056; control port not used (read-only driver)",
                "",
            ]
        ),
        encoding="utf-8",
    )

    zip_path = release.with_suffix(".zip")
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in release.rglob("*"):
            if path.is_file():
                zf.write(path, arcname=str(path.relative_to(release.parent)))
    print(f"release: {release}")
    print(f"zip: {zip_path}")
    if delta:
        _maybe_build_delta(release)
    return release


def build_linux(*, skip_frontend: bool = True, delta: bool = True) -> Path:
    del skip_frontend
    if not (ROOT / "sensors" / "src" / "sensors").is_dir():
        raise SystemExit(
            "missing ./sensors symlink → hik-sensors src "
            "(ln -sfn ~/autodl-tmp/sensors ./sensors)"
        )

    venv = ROOT / ".tools" / "linux-desktop-venv"
    py = venv / "bin" / "python"
    if not py.is_file():
        _run([sys.executable, "-m", "venv", str(venv)])
    env = os.environ.copy()
    env["PIP_INDEX_URL"] = PIP_INDEX
    env["PYTHONNOUSERSITE"] = "1"
    _run([str(py), "-m", "pip", "install", "-U", "pip", "wheel", "-i", PIP_INDEX], env=env)
    _run(
        [str(py), "-m", "pip", "install", "-i", PIP_INDEX, "-r", str(ROOT / "requirements.txt")],
        env=env,
    )
    _run(
        [
            str(py),
            "-m",
            "pip",
            "install",
            "-i",
            PIP_INDEX,
            "-r",
            str(ROOT / "requirements-desktop.txt"),
        ],
        env=env,
    )
    # Hardware extras are optional on Linux hosts without RealSense/Elite wheels.
    try:
        _run(
            [
                str(py),
                "-m",
                "pip",
                "install",
                "-i",
                PIP_INDEX,
                "-r",
                str(ROOT / "requirements-hardware.txt"),
            ],
            env=env,
        )
    except subprocess.CalledProcessError as e:
        print(f"[linux] warn: hardware requirements install failed ({e}); continuing", flush=True)
    _run([str(py), "-m", "pip", "install", "-i", PIP_INDEX, "pyinstaller"], env=env)
    _verify_bundled_imports([str(py)], env=env)

    TMP_BASE.mkdir(parents=True, exist_ok=True)
    dist = TMP_BASE / "sensors-dcs-dist-linux"
    work = TMP_BASE / "sensors-dcs-work-linux"
    if dist.exists():
        shutil.rmtree(dist)
    if work.exists():
        shutil.rmtree(work)
    dist.mkdir(parents=True)
    work.mkdir(parents=True)

    spec = ROOT / "packaging" / "sensors-dcs-desktop-linux.spec"
    _run(
        [
            str(py),
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            f"--distpath={dist}",
            f"--workpath={work}",
            str(spec),
        ],
        env=env,
    )

    built = dist / APP_EXE
    if not built.is_dir():
        raise SystemExit(f"PyInstaller output missing: {built}")

    _verify_bundled_scipy(built)

    stamp = _utc_stamp()
    release = ROOT / "release" / f"{APP_ID}-desktop-linux-x64-{stamp}"
    if release.exists():
        shutil.rmtree(release)
    shutil.copytree(built, release)

    info = {
        "app_id": APP_ID,
        "platform": "linux-x64",
        "self_contained": True,
        "requires_python_on_target": False,
        "built_at": stamp,
        "entry": APP_EXE,
        "notes": "Built on Linux with native PyInstaller (scheme-a-linux-to-linux-desktop)",
    }
    (release / "BUILD_INFO.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    (release / "README.txt").write_text(
        "\n".join(
            [
                "sensors-dcs desktop (Linux x64)",
                "",
                "1. Extract this folder",
                "2. ./sensors-dcs            # headless backend",
                "   ./sensors-dcs --ui       # open viz window/browser",
                "   SENSORS_DCS_PORT=6006 ./sensors-dcs",
                "3. Open http://127.0.0.1:<port>/",
                "",
            ]
        ),
        encoding="utf-8",
    )

    zip_path = Path(str(release) + ".zip")
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in release.rglob("*"):
            if path.is_file():
                zf.write(path, arcname=str(path.relative_to(release.parent)))
    print(f"release: {release}")
    print(f"zip: {zip_path}")
    if delta:
        _maybe_build_delta(release)
    return release


def main() -> None:
    parser = argparse.ArgumentParser(description="Build sensors-dcs desktop packages")
    parser.add_argument(
        "--target",
        choices=["windows", "linux", "native"],
        default="windows",
        help="windows = Wine cross-build; linux/native = host PyInstaller onedir",
    )
    parser.add_argument("--skip-frontend", action="store_true", default=True)
    parser.add_argument(
        "--no-delta",
        action="store_true",
        help="skip incremental delta.zip vs previous release",
    )
    args = parser.parse_args()
    target = "linux" if args.target in ("linux", "native") else args.target
    if target == "windows":
        build_windows(skip_frontend=args.skip_frontend, delta=not args.no_delta)
    elif target == "linux":
        build_linux(skip_frontend=args.skip_frontend, delta=not args.no_delta)
    else:
        raise SystemExit(f"unsupported target: {args.target}")


if __name__ == "__main__":
    main()
