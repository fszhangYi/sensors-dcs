"""E4: tag vocab loading."""

from __future__ import annotations

from pathlib import Path

from sensors_dcs.tag_vocab import load_tag_vocab, load_tag_vocab_file, parse_tag_vocab


def test_parse_flat_and_nested() -> None:
    flat = parse_tag_vocab({"task_ids": ["a"], "env_ids": ["e"], "object_ids": ["o"]})
    assert flat["ok"] is True
    assert flat["task_ids"] == ["a"]
    nested = parse_tag_vocab({"tags": {"tasks": ["t2"], "envs": ["e2"], "objects": ["o2"]}})
    assert nested["task_ids"] == ["t2"]
    assert nested["env_ids"] == ["e2"]
    assert nested["object_ids"] == ["o2"]


def test_load_file_and_missing(tmp_path: Path) -> None:
    p = tmp_path / "tag_vocab.yaml"
    p.write_text(
        "task_ids: [pick]\nenv_ids: [lab]\nobject_ids: [cup]\n",
        encoding="utf-8",
    )
    loaded = load_tag_vocab_file(p)
    assert loaded["ok"] is True
    assert loaded["task_ids"] == ["pick"]
    missing = load_tag_vocab_file(tmp_path / "nope.yaml")
    assert missing["ok"] is False


def test_load_prefers_explicit(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("SENSORS_DCS_TAG_VOCAB", raising=False)
    p = tmp_path / "v.yaml"
    p.write_text("task_ids: [explicit_task]\n", encoding="utf-8")
    out = load_tag_vocab(explicit=p, config_path=None)
    assert out["ok"] is True
    assert out["task_ids"] == ["explicit_task"]
    assert out["path"] == str(p.resolve())
