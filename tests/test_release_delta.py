from __future__ import annotations

import json
import zipfile
from pathlib import Path

from scripts.release_delta import (
    build_delta_zip,
    compare_manifests,
    scan_release,
    write_manifest,
)


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_compare_and_delta_zip(tmp_path: Path) -> None:
    baseline = tmp_path / "release" / "sensors-dcs-desktop-windows-x64-20260101T000000Z"
    current = tmp_path / "release" / "sensors-dcs-desktop-windows-x64-20260102T000000Z"

    _write(baseline / "sensors-dcs.exe", b"exe-v1")
    _write(baseline / "_internal" / "mod.pyd", b"mod-v1")
    _write(baseline / "README.txt", b"readme-v1")

    _write(current / "sensors-dcs.exe", b"exe-v2")
    _write(current / "_internal" / "mod.pyd", b"mod-v1")
    _write(current / "_internal" / "new.dll", b"new")
    _write(current / "README.txt", b"readme-v2")

    baseline_files = scan_release(baseline)
    current_files = scan_release(current)
    changed, added, removed, skipped = compare_manifests(current_files, baseline_files)

    assert "sensors-dcs.exe" in changed
    assert "_internal/new.dll" in added
    assert removed == []
    assert "BUILD_INFO.json" not in changed

    write_manifest(baseline)
    write_manifest(current)

    out = current.with_name(f"{current.name}-delta.zip")
    summary = build_delta_zip(current_dir=current, baseline_dir=baseline, output_zip=out)

    assert set(summary.changed) == {"sensors-dcs.exe", "README.txt"}
    assert summary.added == ["_internal/new.dll"]
    assert out.is_file()

    with zipfile.ZipFile(out, "r") as zf:
        names = set(zf.namelist())
        assert "sensors-dcs.exe" in names
        assert "_internal/new.dll" in names
        assert "README.txt" in names
        assert "_internal/mod.pyd" not in names
        info = json.loads(zf.read("DELTA_INFO.json"))
        assert info["baseline_release"] == baseline.name
        assert info["current_release"] == current.name
        assert info["removed"] == []
