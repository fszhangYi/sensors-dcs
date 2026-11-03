"""Directory sync backends for session gather (resume-friendly, not Peer).

Priority: rclone sync → rsync → robocopy (Win UNC) → pure-Python resume copy.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable


ProgressCb = Callable[[dict[str, Any]], None]


def which(cmd: str) -> str | None:
    return shutil.which(cmd)


def join_publish_uri(publish_uri: str, *parts: str) -> str:
    """Join a publish root (UNC / smb / sftp / posix) with relative segments."""
    root = str(publish_uri or "").strip().rstrip("/\\")
    segs = [str(p).strip().replace("\\", "/").strip("/") for p in parts if str(p).strip()]
    if not root:
        raise ValueError("publish_uri is empty")
    if root.startswith("\\\\") or root.startswith("//"):
        # UNC: \\host\share\a\b
        unc = root.replace("/", "\\")
        return unc.rstrip("\\") + "\\" + "\\".join(s.replace("/", "\\") for s in segs)
    if "://" in root:
        return root.rstrip("/") + "/" + "/".join(segs)
    # POSIX / local absolute
    return str(Path(root).joinpath(*segs))


def resume_copy_tree(
    src: str | Path,
    dest: str | Path,
    *,
    force: bool = False,
    progress: ProgressCb | None = None,
) -> dict[str, Any]:
    """Copy tree skipping unchanged files (same size + mtime within 2s). Resume-safe."""
    src_p = Path(src)
    dest_p = Path(dest)
    if not src_p.is_dir():
        raise FileNotFoundError(f"gather source missing: {src_p}")
    dest_p.mkdir(parents=True, exist_ok=True)
    copied = skipped = 0
    bytes_copied = 0
    t0 = time.monotonic()
    for root, _dirs, files in os.walk(src_p):
        rel_root = Path(root).relative_to(src_p)
        out_dir = dest_p / rel_root
        out_dir.mkdir(parents=True, exist_ok=True)
        for name in files:
            s = Path(root) / name
            d = out_dir / name
            try:
                st = s.stat()
            except OSError:
                continue
            if (
                not force
                and d.is_file()
                and d.stat().st_size == st.st_size
                and abs(d.stat().st_mtime - st.st_mtime) < 2.0
            ):
                skipped += 1
                continue
            tmp = d.with_suffix(d.suffix + ".gather_partial")
            try:
                shutil.copy2(s, tmp)
                os.replace(tmp, d)
            finally:
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
            copied += 1
            bytes_copied += int(st.st_size)
            if progress and (copied + skipped) % 50 == 0:
                progress(
                    {
                        "copied": copied,
                        "skipped": skipped,
                        "bytes_copied": bytes_copied,
                    }
                )
    return {
        "ok": True,
        "backend": "python_resume",
        "copied": copied,
        "skipped": skipped,
        "bytes_copied": bytes_copied,
        "elapsed_s": round(time.monotonic() - t0, 3),
        "src": str(src_p),
        "dest": str(dest_p),
    }


def _run(cmd: list[str], *, timeout_s: float | None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )


def sync_directory(
    src: str | Path,
    dest: str,
    *,
    backend: str = "auto",
    force: bool = False,
    timeout_s: float | None = None,
    progress: ProgressCb | None = None,
) -> dict[str, Any]:
    """Push/sync ``src`` directory to ``dest`` (local/UNC/rclone URI)."""
    src_p = Path(src)
    if not src_p.is_dir():
        raise FileNotFoundError(f"gather source missing: {src_p}")
    be = (backend or "auto").strip().lower()
    dest_s = str(dest).strip()
    if not dest_s:
        raise ValueError("gather dest is empty")

    # Local or UNC path → prefer native tools; rclone still works for smb:// sftp://
    is_uri = "://" in dest_s and not dest_s.startswith("file://")
    is_unc = dest_s.startswith("\\\\") or dest_s.startswith("//")

    if be in ("auto", "rclone") and which("rclone") and (is_uri or be == "rclone"):
        # trailing slash semantics: sync contents into dest
        src_arg = str(src_p)
        cmd = [
            "rclone",
            "sync",
            src_arg,
            dest_s,
            "--create-empty-src-dirs",
            "--transfers",
            "8",
            "--checkers",
            "16",
        ]
        if force:
            # still incremental by content; --ignore-times forces re-check
            cmd.append("--checksum")
        t0 = time.monotonic()
        proc = _run(cmd, timeout_s=timeout_s)
        ok = proc.returncode == 0
        return {
            "ok": ok,
            "backend": "rclone",
            "returncode": proc.returncode,
            "stdout": (proc.stdout or "")[-4000:],
            "stderr": (proc.stderr or "")[-4000:],
            "elapsed_s": round(time.monotonic() - t0, 3),
            "src": src_arg,
            "dest": dest_s,
            "error": None if ok else (proc.stderr or proc.stdout or "rclone failed"),
        }

    if be in ("auto", "rsync") and which("rsync") and not is_unc:
        src_arg = str(src_p).rstrip("/\\") + "/"
        cmd = ["rsync", "-a", "--partial", "--info=progress2", src_arg, dest_s]
        t0 = time.monotonic()
        proc = _run(cmd, timeout_s=timeout_s)
        ok = proc.returncode == 0
        return {
            "ok": ok,
            "backend": "rsync",
            "returncode": proc.returncode,
            "stdout": (proc.stdout or "")[-4000:],
            "stderr": (proc.stderr or "")[-4000:],
            "elapsed_s": round(time.monotonic() - t0, 3),
            "src": src_arg,
            "dest": dest_s,
            "error": None if ok else (proc.stderr or proc.stdout or "rsync failed"),
        }

    if (be in ("auto", "robocopy") or is_unc) and os.name == "nt" and which("robocopy"):
        # robocopy /Z restartable; exit codes 0–7 often success
        dest_path = dest_s.replace("/", "\\")
        Path(dest_path).mkdir(parents=True, exist_ok=True)
        cmd = [
            "robocopy",
            str(src_p),
            dest_path,
            "/E",
            "/Z",
            "/R:2",
            "/W:2",
            "/MT:8",
            "/NFL",
            "/NDL",
            "/NJH",
            "/NJS",
        ]
        t0 = time.monotonic()
        proc = _run(cmd, timeout_s=timeout_s)
        ok = proc.returncode < 8
        return {
            "ok": ok,
            "backend": "robocopy",
            "returncode": proc.returncode,
            "stdout": (proc.stdout or "")[-4000:],
            "stderr": (proc.stderr or "")[-4000:],
            "elapsed_s": round(time.monotonic() - t0, 3),
            "src": str(src_p),
            "dest": dest_path,
            "error": None if ok else (proc.stderr or proc.stdout or "robocopy failed"),
        }

    if is_uri:
        raise RuntimeError(
            f"dest {dest_s!r} needs rclone/rsync but neither is available "
            f"(backend={be})"
        )

    # Pure Python resume (local / UNC if OS can open it)
    return resume_copy_tree(src_p, dest_s, force=force, progress=progress)
