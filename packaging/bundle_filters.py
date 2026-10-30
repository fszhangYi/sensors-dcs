"""Filters for PyInstaller datas / release delta (keep runtime, drop research bulk)."""

from __future__ import annotations

from pathlib import Path

# Directory name segments never needed at runtime in the desktop onedir.
_SKIP_DIR_NAMES = frozenset(
    {
        "__pycache__",
        ".ipynb_checkpoints",
        ".git",
        "示教器信息",
        "交叉验证",
        "guide_assets",
        # Accidental local data dirs under configs/ (not runtime YAML).
        "data",
        "data_pi05",
        "data_new",
        r"D:\data_new",
    }
)

# Docs / photos / archives — not importable runtime.
_SKIP_SUFFIXES = frozenset(
    {
        ".heic",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".bmp",
        ".docx",
        ".doc",
        ".xlsx",
        ".xls",
        ".pdf",
        ".zip",
        ".7z",
        ".rar",
        ".pyc",
        ".pyo",
    }
)

_SKIP_NAME_PREFIXES_CS66 = (
    "构建指南",
    "交叉验证",
    "现场采集",
    "build_guide_",
)

# cs66_kinematics: keep code + YAML profiles only.
_CS66_KEEP_SUFFIXES = frozenset({".py", ".yaml", ".yml", ".json"})


def _norm_parts(rel: Path) -> tuple[str, ...]:
    return tuple(p for p in Path(rel).as_posix().split("/") if p and p != ".")


def should_skip_bundle_rel(rel: Path | str, *, under_cs66: bool = False) -> bool:
    """Return True if this path relative to ``sensors/`` should not be bundled."""
    path = Path(rel)
    parts = _norm_parts(path)
    if any(p in _SKIP_DIR_NAMES for p in parts):
        return True
    name = path.name
    if ".bak" in name:  # *.bak, *.bak-broken, *.bak-omml-broken
        return True
    suffix = path.suffix.lower()
    in_cs66 = under_cs66 or ("cs66_kinematics" in parts)

    if in_cs66:
        if name == "build_guide_docx.py":
            return True
        if any(name.startswith(prefix) for prefix in _SKIP_NAME_PREFIXES_CS66):
            return True
        if suffix and suffix not in _CS66_KEEP_SUFFIXES:
            return True
        return False

    # Non-cs66 sensors tree: drop bytecode / obvious non-runtime blobs.
    if suffix in {".pyc", ".pyo", ".heic", ".docx", ".zip", ".pdf"}:
        return True
    return False


def collect_filtered_datas(src_root: Path, dest_root: str) -> list[tuple[str, str]]:
    """List ``(abspath, dest_dir)`` for PyInstaller ``datas`` (filtered tree).

    Uses ``os.walk`` (followlinks=False) so Wine/Windows builds do not crash on
    broken symlinks or stray absolute-looking paths under the tree.
    """
    import os

    src_root = src_root.resolve()
    if not src_root.is_dir():
        return []
    out: list[tuple[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(src_root, followlinks=False):
        # Prune skip dirs / Windows-absolute-looking junk so Wine rglob never sees them.
        kept: list[str] = []
        for d in dirnames:
            if d in _SKIP_DIR_NAMES:
                continue
            if ":" in d or "\\" in d or d.startswith("D:"):
                continue
            kept.append(d)
        dirnames[:] = kept
        base = Path(dirpath)
        for name in filenames:
            path = base / name
            try:
                if path.is_symlink():
                    continue
                if not path.is_file():
                    continue
                rel = path.relative_to(src_root)
            except (OSError, ValueError):
                continue
            if should_skip_bundle_rel(rel):
                continue
            # Desktop configs: YAML/JSON + CS66 RTSI recipe .txt under configs/rtsi/.
            if dest_root.rstrip("/").endswith("configs") or dest_root == "configs":
                suf = path.suffix.lower()
                parts = _norm_parts(rel)
                is_rtsi_txt = suf == ".txt" and "rtsi" in parts
                if suf not in {".yaml", ".yml", ".json"} and not is_rtsi_txt:
                    continue
            dest_dir = Path(dest_root) / rel.parent
            dest = dest_dir.as_posix().rstrip("/") or dest_root.rstrip("/")
            out.append((str(path), dest))
    return out


def should_skip_delta_path(rel: str) -> bool:
    """Ignore hash-churn / research bulk when building incremental delta.zip."""
    n = rel.replace("\\", "/").lstrip("./")
    if n in {"MANIFEST.json", "BUILD_INFO.json", "DELTA_INFO.json"}:
        return True
    parts = n.split("/")
    if any(p in _SKIP_DIR_NAMES for p in parts):
        return True
    if n.lower().endswith((".pyc", ".pyo")):
        return True
    marker = "sensors_src/sensors/"
    if marker in n:
        sub = n.split(marker, 1)[1]
        if should_skip_bundle_rel(sub):
            return True
    return False
