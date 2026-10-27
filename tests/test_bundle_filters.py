"""Packaging filters: keep cs66 runtime code, drop research media from datas/delta."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "packaging"))

from bundle_filters import (  # noqa: E402
    collect_filtered_datas,
    should_skip_bundle_rel,
    should_skip_delta_path,
)


def test_cs66_keeps_code_drops_media() -> None:
    assert not should_skip_bundle_rel("cs66_kinematics/ik.py")
    assert not should_skip_bundle_rel("cs66_kinematics/profiles/elite_cs66.yaml")
    assert should_skip_bundle_rel("cs66_kinematics/示教器信息/IMG_9610.HEIC")
    assert should_skip_bundle_rel("cs66_kinematics/交叉验证/IMG_9666.HEIC")
    assert should_skip_bundle_rel("cs66_kinematics/构建指南.docx")
    assert should_skip_bundle_rel("cs66_kinematics/guide_assets/formulas/eq.png")
    assert should_skip_bundle_rel("cs66_kinematics/build_guide_docx.py")
    assert should_skip_bundle_rel("cs66_kinematics/.ipynb_checkpoints/ik-checkpoint.py")
    assert should_skip_bundle_rel("foo/__pycache__/x.pyc")


def test_collect_filtered_datas_on_real_tree() -> None:
    root = (_ROOT / "sensors" / "src" / "sensors").resolve()
    if not root.is_dir():
        return
    datas = collect_filtered_datas(root, "sensors_src/sensors")
    rels = []
    for src, _dest in datas:
        rels.append(Path(src).resolve().relative_to(root).as_posix())
    assert any(r.endswith("cs66_kinematics/ik.py") for r in rels)
    assert any("profiles/elite_cs66.yaml" in r for r in rels)
    assert not any(r.lower().endswith(".heic") for r in rels)
    assert not any("示教器信息" in r for r in rels)
    assert not any("交叉验证" in r for r in rels)
    assert not any(r.endswith(".docx") for r in rels)
    # Rough size budget: filtered list should be tiny vs raw tree.
    assert len(rels) < 500


def test_delta_skips_pycache_and_media() -> None:
    assert should_skip_delta_path("_internal/numpy/__pycache__/version.cpython-310.pyc")
    assert should_skip_delta_path(
        "_internal/sensors_src/sensors/cs66_kinematics/示教器信息/a.HEIC"
    )
    assert not should_skip_delta_path("sensors-dcs.exe")
    assert not should_skip_delta_path(
        "_internal/sensors_src/sensors/cs66_kinematics/ik.py"
    )
