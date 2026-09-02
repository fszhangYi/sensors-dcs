#!/usr/bin/env python3
"""Build incremental desktop release zip (changed files vs previous build)."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE_DIR = ROOT / "release"
RELEASE_PREFIX = "sensors-dcs-desktop-windows-x64-"
MANIFEST_NAME = "MANIFEST.json"
DELTA_INFO_NAME = "DELTA_INFO.json"

# Metadata files that change every build but are not needed for runtime overlay.
SKIP_DELTA_PATHS = frozenset({MANIFEST_NAME, "BUILD_INFO.json"})


@dataclass(frozen=True)
class FileEntry:
    size: int
    sha256: str


def utc_stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def is_release_dir(path: Path) -> bool:
    return path.is_dir() and path.name.startswith(RELEASE_PREFIX)


def list_release_dirs(release_root: Path = RELEASE_DIR) -> list[Path]:
    if not release_root.is_dir():
        return []
    dirs = [p for p in release_root.iterdir() if is_release_dir(p)]
    return sorted(dirs, key=lambda p: p.name)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def scan_release(release_dir: Path) -> dict[str, FileEntry]:
    root = release_dir.resolve()
    out: dict[str, FileEntry] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        out[rel] = FileEntry(size=path.stat().st_size, sha256=sha256_file(path))
    return out


def write_manifest(release_dir: Path) -> Path:
    manifest_path = release_dir / MANIFEST_NAME
    payload = {
        "release_dir": release_dir.name,
        "generated_at": utc_stamp(),
        "files": {rel: asdict(entry) for rel, entry in sorted(scan_release(release_dir).items())},
    }
    manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def load_manifest(path: Path) -> dict[str, FileEntry]:
    data = json.loads(path.read_text(encoding="utf-8"))
    files = data.get("files") or {}
    return {
        rel: FileEntry(size=int(entry["size"]), sha256=str(entry["sha256"]))
        for rel, entry in files.items()
    }


def resolve_baseline_files(baseline: Path) -> dict[str, FileEntry]:
    manifest = baseline / MANIFEST_NAME
    if manifest.is_file():
        return load_manifest(manifest)
    return scan_release(baseline)


@dataclass
class DeltaSummary:
    baseline: str
    current: str
    changed: list[str]
    added: list[str]
    removed: list[str]
    skipped: list[str]
    output_zip: str


def compare_manifests(
    current_files: dict[str, FileEntry],
    baseline_files: dict[str, FileEntry],
) -> tuple[list[str], list[str], list[str], list[str]]:
    changed: list[str] = []
    added: list[str] = []
    removed: list[str] = []
    skipped: list[str] = []

    for rel, entry in sorted(current_files.items()):
        if rel in SKIP_DELTA_PATHS:
            skipped.append(rel)
            continue
        base = baseline_files.get(rel)
        if base is None:
            added.append(rel)
        elif base.sha256 != entry.sha256:
            changed.append(rel)

    for rel in sorted(baseline_files):
        if rel in SKIP_DELTA_PATHS:
            continue
        if rel not in current_files:
            removed.append(rel)

    return changed, added, removed, skipped


def collect_delta_paths(changed: list[str], added: list[str]) -> list[str]:
    return sorted(set(changed) | set(added))


def build_delta_zip(
    *,
    current_dir: Path,
    baseline_dir: Path,
    output_zip: Path | None = None,
) -> DeltaSummary:
    current_dir = current_dir.resolve()
    baseline_dir = baseline_dir.resolve()
    if current_dir == baseline_dir:
        raise SystemExit("current and baseline release directories are the same")

    current_files = scan_release(current_dir)
    baseline_files = resolve_baseline_files(baseline_dir)
    changed, added, removed, skipped = compare_manifests(current_files, baseline_files)
    delta_paths = collect_delta_paths(changed, added)

    if output_zip is None:
        output_zip = current_dir.with_name(f"{current_dir.name}-delta.zip")
    output_zip = output_zip.resolve()
    if output_zip.exists():
        output_zip.unlink()

    info = {
        "kind": "sensors-dcs-desktop-delta",
        "generated_at": utc_stamp(),
        "baseline_release": baseline_dir.name,
        "current_release": current_dir.name,
        "apply_hint": "Unzip over your existing sensors-dcs install folder (same level as sensors-dcs.exe).",
        "counts": {
            "changed": len(changed),
            "added": len(added),
            "removed": len(removed),
            "skipped": len(skipped),
            "files_in_zip": len(delta_paths),
        },
        "changed": changed,
        "added": added,
        "removed": removed,
        "skipped": skipped,
    }

    with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel in delta_paths:
            zf.write(current_dir / rel, arcname=rel)
        zf.writestr(DELTA_INFO_NAME, json.dumps(info, indent=2) + "\n")

    return DeltaSummary(
        baseline=baseline_dir.name,
        current=current_dir.name,
        changed=changed,
        added=added,
        removed=removed,
        skipped=skipped,
        output_zip=str(output_zip),
    )


def pick_baseline(current_dir: Path, release_root: Path = RELEASE_DIR) -> Path | None:
    releases = list_release_dirs(release_root)
    current_dir = current_dir.resolve()
    for idx, release in enumerate(releases):
        if release.resolve() == current_dir and idx > 0:
            return releases[idx - 1]
    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build delta.zip with files changed since a previous desktop release",
    )
    parser.add_argument(
        "--current",
        type=Path,
        default=None,
        help="current release directory (default: newest under release/)",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="previous release directory (default: release dir before --current)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="output delta zip path (default: <current>-delta.zip)",
    )
    parser.add_argument(
        "--write-manifest",
        action="store_true",
        help="only write MANIFEST.json for --current and exit",
    )
    args = parser.parse_args()

    current = args.current
    if current is None:
        releases = list_release_dirs()
        if not releases:
            raise SystemExit(f"no release directories found under {RELEASE_DIR}")
        current = releases[-1]

    current = current.resolve()
    if not current.is_dir():
        raise SystemExit(f"current release directory not found: {current}")

    if args.write_manifest:
        path = write_manifest(current)
        print(f"manifest: {path}")
        return

    baseline = args.baseline
    if baseline is None:
        baseline = pick_baseline(current)
        if baseline is None:
            raise SystemExit(
                "no baseline release found (need at least two builds under release/ "
                "or pass --baseline)"
            )
    baseline = baseline.resolve()
    if not baseline.is_dir():
        raise SystemExit(f"baseline release directory not found: {baseline}")

    summary = build_delta_zip(current_dir=current, baseline_dir=baseline, output_zip=args.output)
    print(f"baseline: {summary.baseline}")
    print(f"current:  {summary.current}")
    print(
        f"delta:    changed={len(summary.changed)} added={len(summary.added)} "
        f"removed={len(summary.removed)} skipped={len(summary.skipped)}"
    )
    print(f"zip:      {summary.output_zip}")
    if not summary.changed and not summary.added:
        print("[warn] delta zip contains only DELTA_INFO.json (no file changes)")


if __name__ == "__main__":
    main()
