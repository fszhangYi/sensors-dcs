"""PyInstaller runtime hook: keep frozen sys.path / native libs sane before imports."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _apply() -> None:
    if not getattr(sys, "frozen", False):
        return
    meipass = getattr(sys, "_MEIPASS", None)
    if not meipass:
        return
    root = Path(meipass).resolve()

    lib_dirs = [
        root,
        root / "numpy.libs",
        root / "pyarrow.libs",
        root / "pandas.libs",
    ]
    prepend = [str(p) for p in lib_dirs if p.is_dir()]

    if sys.platform == "win32":
        for d in prepend:
            try:
                os.add_dll_directory(d)
            except Exception:  # noqa: BLE001
                pass
        if prepend:
            cur = os.environ.get("PATH", "")
            os.environ["PATH"] = os.pathsep.join(prepend + ([cur] if cur else []))
    elif prepend:
        cur = os.environ.get("LD_LIBRARY_PATH", "")
        parts = prepend + ([cur] if cur else [])
        os.environ["LD_LIBRARY_PATH"] = os.pathsep.join(parts)

    try:
        cwd = Path.cwd().resolve()
    except Exception:  # noqa: BLE001
        cwd = None
    if cwd is not None and (cwd / "__init__.py").is_file() and cwd.name in {
        "numpy",
        "sensors",
        "sensors_dcs",
    }:
        safe = root.parent
        try:
            os.chdir(safe)
        except Exception:  # noqa: BLE001
            pass

    mp = str(root)
    cleaned: list[str] = []
    for p in sys.path:
        if p in ("", "."):
            continue
        try:
            if Path(p).resolve() == root:
                continue
        except Exception:  # noqa: BLE001
            pass
        if p not in cleaned:
            cleaned.append(p)
    sys.path[:] = [mp, *cleaned]


_apply()
