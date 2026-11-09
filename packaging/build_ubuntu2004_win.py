"""在 Windows 上打出 Ubuntu 20.04 x86_64 能直接运行的 sensors-dcs 目录包。

遵循 office_skills/win-to-ubuntu2004-package：micromamba 只求解 linux-64，
在 Windows 上把符号链接展开成副本；不经 PyInstaller / WSL / Docker。

本包为 **软件依赖 / dry-run 可用** 范围：含 FastAPI 栈、OpenCV headless、
导出用 pandas/pyarrow/h5py/scipy，以及 hik-sensors 源码与 configs。
不含 RealSense / Dynamixel / Elite 等硬件 SDK（需在目标机另行安装）。
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

try:
    from compression import zstd
except ImportError:  # pragma: no cover
    zstd = None

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packaging"))
from bundle_filters import should_skip_bundle_rel  # noqa: E402

UBUNTU_GLIBC = (2, 31)
MAMBA_URL = (
    "https://github.com/mamba-org/micromamba-releases/releases/"
    "download/2.3.3-0/micromamba-win-64.exe"
)
# Binary-heavy / ABI-sensitive: conda-forge linux-64 only.
# OpenCV 不用 conda：py-opencv 5.x 会拉 qt6+OpenVINO；改为 PyPI manylinux wheel。
LINUX_SPECS = (
    "python=3.12",
    "numpy>=1.26,<2.1",
    "pyyaml",
    "pydantic",
    "fastapi",
    "uvicorn",
    "scipy>=1.10,<1.14",
    "pandas>=2.0,<3",
    "pyarrow>=14",
    "h5py>=3.10",
    "httptools",
    "websockets",
    "watchfiles",
    "uvloop",
)
# Pure-Python extras not reliably solved as noarch-only on conda.
PYPI_PURE = (
    "python-multipart>=0.0.9",
    "typing-inspection>=0.4.0",
)
# manylinux wheel（含 .so）；须过 glibc≤2.31 门。
PYPI_LINUX_WHEELS = (
    "opencv-python-headless==4.10.0.84",
)
SYSTEM_SONAMES = frozenset(
    {
        "libc.so.6",
        "libm.so.6",
        "libdl.so.2",
        "libpthread.so.0",
        "librt.so.1",
        "libutil.so.1",
        "libresolv.so.2",
        "libcrypt.so.1",
        "ld-linux-x86-64.so.2",
        "linux-vdso.so.1",
    }
)
REQUIRED_IMPORT_MARKERS = (
    "fastapi/__init__.py",
    "uvicorn/main.py",
    "yaml/__init__.py",
    "pydantic/__init__.py",
    "numpy/__init__.py",
    "cv2/__init__.py",
    "scipy/__init__.py",
    "pandas/__init__.py",
    "pyarrow/__init__.py",
    "h5py/__init__.py",
)


def package_name(moment: datetime | None = None) -> str:
    moment = datetime.now() if moment is None else moment
    return f"sensors-dcs-ubuntu2004-x86_64-{moment.strftime('%Y%m%d-%H%M%S')}"


def highest_glibc(blob: bytes) -> tuple[int, int] | None:
    found = [(int(a), int(b)) for a, b in re.findall(br"GLIBC_(\d+)\.(\d+)", blob)]
    return max(found) if found else None


def required_glibc(depends: list[str]) -> tuple[int, int] | None:
    found = []
    for dep in depends:
        match = re.search(r"__glibc\s*>=\s*(\d+)\.(\d+)", dep)
        if match:
            found.append((int(match.group(1)), int(match.group(2))))
    return max(found) if found else None


def place_noarch_python(name: str) -> str:
    if name == "site-packages" or name.startswith("site-packages/"):
        return "lib/python3.12/" + name
    return name


def launcher_script() -> str:
    return (
        "#!/bin/sh\n"
        'cd "$(dirname "$0")" || exit 1\n'
        "ROOT=$(pwd)\n"
        "export PYTHONNOUSERSITE=1\n"
        'export PYTHONPATH="$ROOT/app${PYTHONPATH:+:$PYTHONPATH}"\n'
        'export LD_LIBRARY_PATH="$ROOT/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"\n'
        'export SENSORS_DCS_BUNDLE_ROOT="$ROOT"\n'
        'exec "$ROOT/bin/python" -m sensors_dcs.desktop_main "$@"\n'
    )


def readme_text() -> str:
    return (
        "sensors-dcs — Ubuntu 20.04 x86_64 自包含包（Windows 交叉构建）\n"
        "\n"
        "解压后在图形桌面或 SSH 会话中：\n"
        "  ./start-sensors-dcs.sh\n"
        "  ./start-sensors-dcs.sh -c configs/camera-only.yaml\n"
        "  ./start-sensors-dcs.sh --ui\n"
        "\n"
        "默认只起后端；控制台会打印 viz 地址（常见 http://127.0.0.1:7011/）。\n"
        "程序自带 Python 3.12，无需系统 pip/conda。\n"
        "\n"
        "范围：软件依赖 + dry-run；不含 RealSense / Dynamixel / Elite 等硬件 SDK。\n"
        "构建机无法执行这些 ELF；请在 Ubuntu 20.04 上验证启动。\n"
    )


def _inside(root: Path, name: str) -> Path:
    dest = (root / name).resolve()
    if not dest.is_relative_to(root.resolve()):
        raise SystemExit(f"压缩包路径越界：{name}")
    return dest


def materialize_tar(archive: tarfile.TarFile, root: Path, rename=None) -> None:
    def placed(name: str) -> str:
        name = name.lstrip("./")
        return name if rename is None else rename(name)

    root.mkdir(parents=True, exist_ok=True)
    links: list[tarfile.TarInfo] = []
    for member in archive.getmembers():
        name = placed(member.name)
        if not name or name == "info" or name.startswith("info/"):
            continue
        if member.isdir():
            _inside(root, name).mkdir(parents=True, exist_ok=True)
            continue
        if member.issym() or member.islnk():
            links.append(member)
            continue
        if not member.isfile():
            continue
        dest = _inside(root, name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        source = archive.extractfile(member)
        if source is None:
            raise SystemExit(f"压缩包里读不出 {name}")
        with dest.open("wb") as handle:
            shutil.copyfileobj(source, handle)
    pending = links
    for _ in range(len(links) + 1):
        still: list[tarfile.TarInfo] = []
        for member in pending:
            name = placed(member.name)
            dest = _inside(root, name)
            if member.islnk():
                target = _inside(root, placed(member.linkname))
            else:
                raw = member.linkname.replace("\\", "/")
                if raw.startswith("/"):
                    raise SystemExit(f"{name} 指向包外的 {raw}")
                target = _inside(root, str(Path(name).parent / raw))
            dest.parent.mkdir(parents=True, exist_ok=True)
            if target.is_dir():
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(target, dest)
                continue
            if not target.is_file():
                still.append(member)
                continue
            shutil.copyfile(target, dest)
        if not still:
            return
        if len(still) == len(pending):
            names = ", ".join(item.name for item in still[:6])
            raise SystemExit(f"无法展开链接：{names}")
        pending = still


def _download_mamba(target: Path) -> None:
    if target.is_file() and target.stat().st_size > 1_000_000:
        return
    # Reuse a known-good local micromamba if present.
    candidates = [
        Path(r"C:\Users\user\Desktop\scrn-shr\build\micromamba.exe"),
        ROOT / "build" / "micromamba.exe",
    ]
    for src in candidates:
        if src.is_file() and src.stat().st_size > 1_000_000 and src.resolve() != target.resolve():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
            return
    target.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(MAMBA_URL, target)


def _solve(mamba: Path, prefix: Path, root_prefix: Path) -> list[dict]:
    env = os.environ.copy()
    env["MAMBA_ROOT_PREFIX"] = str(root_prefix)
    env["CONDA_PLUGINS_AUTO_ACCEPT_TOS"] = "yes"
    env["CONDA_OVERRIDE_LINUX"] = "5.4.0"
    env["CONDA_OVERRIDE_GLIBC"] = "2.31"
    channels = (
        "conda-forge",
        "https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge",
    )
    # Prefer a unique prefix path so a stuck Windows file lock cannot block rebuilds.
    # (Caller still passes `prefix`; downloads go to shared conda-pkgs cache.)
    errors: list[str] = []
    for channel in channels:
        command = [
            str(mamba),
            "create",
            "--dry-run",
            "--json",
            "-y",
            "-p",
            str(prefix),
            "--platform",
            "linux-64",
            "--override-channels",
            "-c",
            channel,
            *LINUX_SPECS,
        ]
        print(f"求解通道 {channel} …", flush=True)
        try:
            completed = subprocess.run(command, env=env, check=True, capture_output=True)
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or b"").decode("utf-8", errors="replace")[-600:]
            errors.append(f"{channel} 退出 {exc.returncode} {detail}")
            continue
        text = completed.stdout.decode("utf-8", errors="replace")
        start = text.find("{")
        if start < 0:
            errors.append(f"{channel} 没有返回包列表")
            continue
        decoder = json.JSONDecoder()
        obj, _ = decoder.raw_decode(text[start:])
        link = obj.get("actions", {}).get("LINK") or []
        if not link:
            errors.append(f"{channel} 没有选出包")
            continue
        return link
    raise SystemExit("没有做成 Ubuntu 20.04 能用的运行环境。\n" + "\n".join(errors))


def _download_package(url: str, dest: Path, digest: str) -> None:
    if dest.is_file() and hashlib.sha256(dest.read_bytes()).hexdigest() == digest:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    mirror = url.replace(
        "https://conda.anaconda.org/conda-forge/",
        "https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge/",
    )
    candidates = [mirror] if mirror != url else []
    candidates.append(url)
    tmp = dest.with_suffix(dest.suffix + ".part")
    errors: list[str] = []
    for candidate in candidates:
        try:
            request = urllib.request.Request(
                candidate, headers={"User-Agent": "sensors-dcs-ubuntu2004-pack"}
            )
            with urllib.request.urlopen(request, timeout=90) as response, tmp.open("wb") as handle:
                shutil.copyfileobj(response, handle)
        except OSError as exc:
            errors.append(f"{candidate} {exc}")
            continue
        if hashlib.sha256(tmp.read_bytes()).hexdigest() != digest:
            errors.append(f"{candidate} 校验不对")
            continue
        tmp.replace(dest)
        return
    raise SystemExit(f"下载失败 {dest.name}\n" + "\n".join(errors))


def _noarch_python(package: Path) -> bool:
    if zstd is None or not package.name.endswith(".conda"):
        return False
    with zipfile.ZipFile(package) as outer:
        names = [item for item in outer.namelist() if item.startswith("info-")]
        if not names:
            return False
        blob = zstd.decompress(outer.read(names[0]))
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:") as archive:
        try:
            handle = archive.extractfile("info/link.json")
        except KeyError:
            return False
        if handle is None:
            return False
        data = json.loads(handle.read())
    return data.get("noarch", {}).get("type") == "python"


def _extract_package(package: Path, prefix: Path) -> None:
    rename = place_noarch_python if _noarch_python(package) else None
    if package.name.endswith(".conda"):
        if zstd is None:
            raise SystemExit("打包需要 Python 3.14 自带的 zstd（compression.zstd）。")
        with zipfile.ZipFile(package) as outer:
            name = next(item for item in outer.namelist() if item.startswith("pkg-"))
            blob = zstd.decompress(outer.read(name))
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:") as archive:
            materialize_tar(archive, prefix, rename)
        return
    if package.name.endswith(".tar.bz2"):
        with tarfile.open(package, mode="r:bz2") as archive:
            materialize_tar(archive, prefix, rename)
        return
    raise SystemExit(f"不认识的包格式：{package.name}")


def drop_host_linker(prefix: Path) -> None:
    for path in list(prefix.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        if path.name != "ld" and not path.name.endswith("-ld"):
            continue
        try:
            blob = path.read_bytes()
        except OSError:
            continue
        if not blob.startswith(b"\x7fELF"):
            continue
        version = highest_glibc(blob)
        if version is not None and version > UBUNTU_GLIBC:
            path.unlink()


def _create_prefix(mamba: Path, prefix: Path, root_prefix: Path) -> None:
    packages = _solve(mamba, prefix, root_prefix)
    too_new = [
        f"{item['name']} 需要 glibc {required_glibc(item.get('depends') or [])}"
        for item in packages
        if (required_glibc(item.get("depends") or []) or (0, 0)) > UBUNTU_GLIBC
    ]
    if too_new:
        raise SystemExit("这些包 Ubuntu 20.04 带不动：\n" + "\n".join(too_new))
    heavy = sorted(
        {
            item["name"]
            for item in packages
            if item["name"].startswith(("qt6-", "qt5-"))
            or "openvino" in item["name"]
            or item["name"] in {"qt6-main", "qt-main"}
        }
    )
    if heavy:
        raise SystemExit(
            "求解拉进了过重的 GUI/推理栈（请收紧 OpenCV 规格）：\n" + "\n".join(heavy)
        )
    solve_path = root_prefix.parent / "linux-solve.json"
    solve_path.write_text(json.dumps(packages, indent=2), encoding="utf-8")
    print(f"求解得到 {len(packages)} 个包 → {solve_path}", flush=True)
    if prefix.exists():
        shutil.rmtree(prefix)
    prefix.mkdir(parents=True)
    cache = root_prefix.parent / "conda-pkgs"
    for item in packages:
        filename = item["fn"]
        print(f"正在下载 {filename}", flush=True)
        package = cache / filename
        _download_package(item["url"], package, item["sha256"])
        _extract_package(package, prefix)
    drop_host_linker(prefix)


def _site_packages(prefix: Path) -> Path:
    found = sorted(prefix.glob("lib/python3.*/site-packages"))
    if not found:
        raise SystemExit("Linux 运行环境里没有 site-packages。")
    return found[-1]


def _pip_download(specs: tuple[str, ...], wheel_dir: Path, *, platforms: list[str]) -> None:
    indexes = (
        "https://pypi.tuna.tsinghua.edu.cn/simple",
        "https://pypi.org/simple",
    )
    for spec in specs:
        errors: list[str] = []
        ok = False
        for index in indexes:
            for platform in platforms:
                command = [
                    sys.executable,
                    "-m",
                    "pip",
                    "download",
                    spec,
                    "--no-deps",
                    "--only-binary=:all:",
                    "--python-version",
                    "3.12",
                    "--abi",
                    "cp312",
                    "--implementation",
                    "cp",
                    "-i",
                    index,
                    "-d",
                    str(wheel_dir),
                ]
                if platform != "any":
                    command.extend(["--platform", platform])
                try:
                    subprocess.check_call(command)
                    ok = True
                    break
                except subprocess.CalledProcessError as exc:
                    errors.append(f"{index} {platform} 退出 {exc.returncode}")
            if ok:
                break
        if not ok:
            raise SystemExit(f"没有下载到 {spec}\n" + "\n".join(errors))


def _install_pypi_pure(prefix: Path) -> None:
    wheel_dir = ROOT / "build" / "linux-wheels"
    if wheel_dir.exists():
        shutil.rmtree(wheel_dir)
    wheel_dir.mkdir(parents=True)
    if PYPI_PURE:
        _pip_download(PYPI_PURE, wheel_dir, platforms=["any"])
    site = _site_packages(prefix)
    for wheel in wheel_dir.glob("*.whl"):
        with zipfile.ZipFile(wheel) as archive:
            binary = [n for n in archive.namelist() if n.endswith((".so", ".pyd"))]
            if binary:
                raise SystemExit(f"{wheel.name} 含二进制，拒绝装入：{binary[:5]}")
            archive.extractall(site)


def _install_pypi_linux_wheels(prefix: Path) -> None:
    """Install manylinux wheels (e.g. opencv-headless) after conda prefix exists."""
    if not PYPI_LINUX_WHEELS:
        return
    wheel_dir = ROOT / "build" / "linux-wheels-abi"
    if wheel_dir.exists():
        shutil.rmtree(wheel_dir)
    wheel_dir.mkdir(parents=True)
    platforms = [
        "manylinux2014_x86_64",
        "manylinux_2_17_x86_64",
        "manylinux_2_28_x86_64",
    ]
    _pip_download(PYPI_LINUX_WHEELS, wheel_dir, platforms=platforms)
    site = _site_packages(prefix)
    for wheel in sorted(wheel_dir.glob("*.whl")):
        print(f"展开 Linux wheel {wheel.name}", flush=True)
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(site)


def _dt_needed(blob: bytes) -> list[str]:
    if len(blob) < 64 or blob[:4] != b"\x7fELF":
        return []
    if blob[4] != 2:  # ELFCLASS64
        return []
    endian = "<" if blob[5] == 1 else ">"
    e_phoff = struct.unpack_from(endian + "Q", blob, 32)[0]
    e_phentsize = struct.unpack_from(endian + "H", blob, 54)[0]
    e_phnum = struct.unpack_from(endian + "H", blob, 56)[0]
    loads: list[tuple[int, int, int]] = []
    dynamic: tuple[int, int] | None = None
    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        p_type, _p_flags, p_offset, p_vaddr, _p_paddr, p_filesz, p_memsz, _p_align = (
            struct.unpack_from(endian + "IIQQQQQQ", blob, off)
        )
        if p_type == 1:  # PT_LOAD
            loads.append((p_vaddr, p_offset, p_memsz))
        elif p_type == 2:  # PT_DYNAMIC
            dynamic = (p_offset, p_filesz)
    if dynamic is None or not loads:
        return []

    def vaddr_to_off(addr: int) -> int | None:
        for vaddr, offset, memsz in loads:
            if vaddr <= addr < vaddr + memsz:
                return offset + (addr - vaddr)
        return None

    dyn_off, dyn_size = dynamic
    needed_offs: list[int] = []
    strtab_addr: int | None = None
    entry_size = 16
    for i in range(dyn_size // entry_size):
        tag, val = struct.unpack_from(endian + "qQ", blob, dyn_off + i * entry_size)
        if tag == 0:
            break
        if tag == 1:  # DT_NEEDED
            needed_offs.append(val)
        elif tag == 5:  # DT_STRTAB
            strtab_addr = val
    if strtab_addr is None:
        return []
    strtab_off = vaddr_to_off(strtab_addr)
    if strtab_off is None:
        return []
    names: list[str] = []
    for rel in needed_offs:
        start = strtab_off + rel
        end = blob.find(b"\x00", start)
        if end < 0:
            continue
        names.append(blob[start:end].decode("ascii", errors="replace"))
    return names


def prune_unused_native(prefix: Path) -> None:
    """Drop optional natives that pull sonames we do not ship (Tk/X11, Arrow Flight)."""
    patterns = [
        "bin/tclsh*",
        "bin/wish*",
        "lib/libtk*",
        "lib/libtcl*",
        "lib/tcl8.6/**",
        "lib/tk8.6/**",
        "lib/python3.*/lib-dynload/_tkinter*.so",
        "lib/python3.*/site-packages/pyarrow/*flight*",
        "lib/python3.*/site-packages/pyarrow/*gandiva*",
        "lib/python3.*/site-packages/pyarrow/gandiva*",
        "include/**",  # headers unused at runtime
        "share/doc/**",
        "man/**",
    ]
    removed = 0
    for pattern in patterns:
        for path in prefix.glob(pattern):
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
            elif path.is_file():
                path.unlink(missing_ok=True)
                removed += 1
    # Second pass: pyarrow leftover flight/gandiva modules by name
    site = _site_packages(prefix)
    arrow = site / "pyarrow"
    if arrow.is_dir():
        for path in arrow.rglob("*"):
            if not path.is_file():
                continue
            name = path.name.lower()
            if "flight" in name or "gandiva" in name:
                path.unlink(missing_ok=True)
                removed += 1
    print(f"已裁掉可选原生文件约 {removed} 项", flush=True)


def ensure_gates(prefix: Path) -> None:
    worst: tuple[int, int] | None = None
    worst_path: Path | None = None
    elf_count = 0
    relocatable = False
    filenames: set[str] = set()
    elf_paths: list[Path] = []
    for path in prefix.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        filenames.add(path.name)
        try:
            head = path.read_bytes()[:4]
        except OSError:
            continue
        if head == b"\x7fELF":
            elf_paths.append(path)
    missing_needed: list[str] = []
    for path in elf_paths:
        try:
            blob = path.read_bytes()
        except OSError:
            continue
        elf_count += 1
        if path.parent.name == "bin" and path.name.startswith("python") and b"$ORIGIN" in blob:
            relocatable = True
        version = highest_glibc(blob)
        if version is not None and (worst is None or version > worst):
            worst = version
            worst_path = path
        for soname in _dt_needed(blob):
            if soname in SYSTEM_SONAMES or soname in filenames:
                continue
            missing_needed.append(f"{path.relative_to(prefix).as_posix()}: {soname}")
    if elf_count == 0 or worst is None or worst > UBUNTU_GLIBC:
        raise SystemExit(
            f"{worst_path} 需要的 glibc 是 {worst}，Ubuntu 20.04 只有 2.31，停止打包。"
        )
    if not relocatable:
        raise SystemExit("解释器不能跟着目录一起搬走（缺 $ORIGIN），停止打包。")
    if missing_needed:
        sample = "\n".join(missing_needed[:30])
        raise SystemExit(
            f"有 {len(missing_needed)} 处 DT_NEEDED 不在包内且非 glibc 基线：\n{sample}"
        )
    site = _site_packages(prefix)
    missing = [rel for rel in REQUIRED_IMPORT_MARKERS if not (site / rel).is_file()]
    if "cv2/__init__.py" in missing:
        if any(site.glob("cv2*/**/*.so")) or (site / "cv2").is_dir() or any(site.glob("cv2*.so")):
            missing = [m for m in missing if m != "cv2/__init__.py"]
    if missing:
        raise SystemExit("运行环境缺少这些库，停止打包：\n" + "\n".join(missing))


def _copy_tree_filtered(src: Path, dest: Path, *, under_cs66_root: bool = False) -> None:
    import os

    src = src.resolve()
    if not src.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        kept: list[str] = []
        for d in dirnames:
            if d in {"__pycache__", ".git", ".ipynb_checkpoints"}:
                continue
            if ":" in d or "\\" in d:
                continue
            kept.append(d)
        dirnames[:] = kept
        base = Path(dirpath)
        rel_dir = base.relative_to(src)
        for name in filenames:
            path = base / name
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                rel = rel_dir / name
            except (OSError, ValueError):
                continue
            if should_skip_bundle_rel(rel, under_cs66=("cs66_kinematics" in rel.parts)):
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def _copy_configs(src: Path, dest: Path) -> None:
    import os

    if not src.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        dirnames[:] = [
            d
            for d in dirnames
            if d not in {"__pycache__", ".git", "data", "data_pi05", "data_new"}
            and ":" not in d
        ]
        base = Path(dirpath)
        for name in filenames:
            path = base / name
            if not path.is_file() or path.is_symlink():
                continue
            rel = path.relative_to(src)
            suf = path.suffix.lower()
            is_rtsi_txt = suf == ".txt" and "rtsi" in rel.parts
            if suf not in {".yaml", ".yml", ".json"} and not is_rtsi_txt:
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def _copy_app(prefix: Path) -> None:
    app = prefix / "app"
    if app.exists():
        shutil.rmtree(app)
    app.mkdir(parents=True)
    _copy_tree_filtered(ROOT / "src" / "sensors_dcs", app / "sensors_dcs")
    sensors_src = (ROOT / "sensors" / "src" / "sensors").resolve()
    if not sensors_src.is_dir():
        sensors_src = (ROOT.parent / "sensors" / "src" / "sensors").resolve()
    if not sensors_src.is_dir():
        raise SystemExit(f"找不到 hik-sensors 源码：{sensors_src}")
    _copy_tree_filtered(sensors_src, app / "sensors")
    _copy_configs(ROOT / "configs", prefix / "configs")
    sensors_cfg = (ROOT / "sensors" / "configs").resolve()
    if not sensors_cfg.is_dir():
        sensors_cfg = (ROOT.parent / "sensors" / "configs").resolve()
    if sensors_cfg.is_dir():
        _copy_configs(sensors_cfg, prefix / "sensors" / "configs")
    # Marker so desktop can find bundled configs next to prefix root.
    (prefix / "BUNDLE_INFO.txt").write_text(
        "sensors-dcs win-to-ubuntu2004 bundle\n"
        f"built={datetime.now().isoformat(timespec='seconds')}\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_launchers(prefix: Path) -> None:
    files = {
        "start-sensors-dcs.sh": launcher_script(),
        "使用说明.txt": readme_text(),
    }
    for name, text in files.items():
        path = prefix / name
        path.write_text(text, encoding="utf-8", newline="\n")
        if name.endswith(".sh"):
            path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _archive(prefix: Path, destination: Path) -> None:
    if destination.exists():
        destination.unlink()
    destination.parent.mkdir(parents=True, exist_ok=True)
    folder = destination.name.removesuffix(".tar.gz")
    with tarfile.open(destination, "w:gz") as archive:
        for path in prefix.rglob("*"):
            relative = path.relative_to(prefix).as_posix()
            info = archive.gettarinfo(path, arcname=f"{folder}/{relative}")
            if path.is_symlink():
                archive.addfile(info)
                continue
            if path.is_dir():
                info.mode |= 0o755
                archive.addfile(info)
                continue
            mode = info.mode | 0o644
            if path.suffix == ".sh" or relative.startswith("bin/") or ".so" in path.name:
                mode |= 0o755
            info.mode = mode
            with path.open("rb") as handle:
                archive.addfile(info, handle)


def build(moment: datetime | None = None) -> Path:
    if sys.platform != "win32":
        raise SystemExit("请在 Windows 上打包。生成的程序在 Ubuntu 20.04 上运行。")
    if zstd is None:
        raise SystemExit("需要 Python 3.14+（标准库 compression.zstd）。")
    name = package_name(moment)
    build_root = ROOT / "build"
    mamba = build_root / "micromamba.exe"
    prefix = build_root / "linux-prefix"
    _download_mamba(mamba)
    _create_prefix(mamba, prefix, build_root / "mamba-root")
    _install_pypi_pure(prefix)
    _install_pypi_linux_wheels(prefix)
    prune_unused_native(prefix)
    ensure_gates(prefix)
    _copy_app(prefix)
    _write_launchers(prefix)
    output = ROOT / "dist" / f"{name}.tar.gz"
    _archive(prefix, output)
    print(f"已生成 {output}")
    print("请在 Ubuntu 20.04 上解压并运行 ./start-sensors-dcs.sh 做实机验证。")
    return output


def main() -> int:
    build()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
