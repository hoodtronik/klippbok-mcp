"""Tests for the manifest adapter."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from klippbok_mcp import manifest as mft


def _clip_manifest(include_flags=None, scores=None) -> dict:
    n = 5
    include_flags = include_flags or [True] * n
    scores = scores or [0.5 + 0.1 * i for i in range(n)]
    return {
        "triage": {"model": "clip-vit-base-patch32", "threshold": 0.7},
        "concepts": [{"name": "cat"}],
        "clips": [
            {
                "file": f"a{i}.mp4",
                "path": f"F:/videos/a{i}.mp4",
                "use_case": "character",
                "matches": [{"concept": "cat", "similarity": scores[i], "best_frame": 0}],
                "include": include_flags[i],
            }
            for i in range(n)
        ],
    }


def _scene_manifest() -> dict:
    return {
        "triage_mode": "scene",
        "triage": {"threshold": 0.65},
        "concepts": [{"name": "dance"}],
        "videos": [
            {
                "file": "long.mp4",
                "path": "F:/v/long.mp4",
                "scenes": [
                    {
                        "scene_index": i,
                        "start_time": i * 2.0,
                        "end_time": (i + 1) * 2.0,
                        "text_overlay": i == 2,
                        "include": True,
                        "matches": (
                            [{"concept": "dance", "similarity": 0.6 + i * 0.1}] if i < 3 else []
                        ),
                    }
                    for i in range(4)
                ],
            }
        ],
    }


@pytest.fixture
def clip_manifest_path(tmp_path: Path) -> Path:
    p = tmp_path / "triage_manifest.json"
    p.write_text(json.dumps(_clip_manifest()))
    return p


@pytest.fixture
def scene_manifest_path(tmp_path: Path) -> Path:
    p = tmp_path / "scene_triage_manifest.json"
    p.write_text(json.dumps(_scene_manifest()))
    return p


# ---- load ----------------------------------------------------------


def test_read_clip_manifest(clip_manifest_path: Path):
    summary, entries, raw = mft.read_manifest(clip_manifest_path)
    assert summary.kind == "clip"
    assert summary.total_entries == 5
    assert summary.included == 5
    assert summary.excluded == 0
    assert summary.concepts == ["cat"]
    assert summary.triage_threshold == 0.7
    assert summary.triage_model == "clip-vit-base-patch32"
    # First entry score = 0.5, last = 0.9.
    assert entries[0].score == 0.5
    assert entries[-1].score == pytest.approx(0.9)
    assert entries[0].best_concept == "cat"
    # Writeback pointer set; underscore-hidden in to_public_dict.
    assert entries[0]._clip_idx == 0
    assert "_clip_idx" not in entries[0].to_public_dict()


def test_read_scene_manifest(scene_manifest_path: Path):
    summary, entries, raw = mft.read_manifest(scene_manifest_path)
    assert summary.kind == "scene"
    assert summary.total_entries == 4
    assert summary.text_overlays == 1
    assert entries[0].start_seconds == 0.0
    assert entries[0].end_seconds == 2.0
    assert entries[0]._video_idx == 0 and entries[0]._scene_idx == 0


def test_read_unknown_schema_raises(tmp_path: Path):
    p = tmp_path / "junk.json"
    p.write_text(json.dumps({"garbage": 1}))
    with pytest.raises(ValueError, match="Unrecognized manifest schema"):
        mft.read_manifest(p)


# ---- apply_mutations ----------------------------------------------


def test_apply_include_above_threshold(clip_manifest_path: Path):
    _, entries, _ = mft.read_manifest(clip_manifest_path)
    mft.apply_mutations(entries, include_above_threshold=0.75)
    # Scores are 0.5, 0.6, 0.7, 0.8, 0.9. Only 0.8 and 0.9 pass.
    assert [e.include for e in entries] == [False, False, False, True, True]


def test_apply_include_all_then_threshold(clip_manifest_path: Path):
    _, entries, _ = mft.read_manifest(clip_manifest_path)
    mft.apply_mutations(entries, include_all=True, include_above_threshold=0.85)
    # include_all is first; threshold then overrides — only 0.9 passes.
    assert [e.include for e in entries] == [False, False, False, False, True]


def test_apply_exclude_indices_wins(clip_manifest_path: Path):
    _, entries, _ = mft.read_manifest(clip_manifest_path)
    mft.apply_mutations(
        entries,
        include_all=True,
        include_indices=[0, 1, 2],
        exclude_indices=[1],
    )
    # include_all True, then force-include 0/1/2 (no-op), then exclude 1 wins.
    assert [e.include for e in entries] == [True, False, True, True, True]


def test_apply_mutex(clip_manifest_path: Path):
    _, entries, _ = mft.read_manifest(clip_manifest_path)
    with pytest.raises(ValueError, match="mutually exclusive"):
        mft.apply_mutations(entries, include_all=True, exclude_all=True)


# ---- save round-trip ----------------------------------------------


def test_save_clip_preserves_untouched_fields(clip_manifest_path: Path, tmp_path: Path):
    _, entries, raw = mft.read_manifest(clip_manifest_path)
    entries[0].include = False
    entries[-1].include = False
    dest = mft.reviewed_path_for(clip_manifest_path)
    mft.save_manifest(raw, entries, dest)
    round_trip = json.loads(dest.read_text(encoding="utf-8"))
    # Include flags match our mutations.
    assert [c["include"] for c in round_trip["clips"]] == [False, True, True, True, False]
    # Matches array preserved byte-for-byte.
    original = _clip_manifest()
    for i, clip in enumerate(round_trip["clips"]):
        assert clip["matches"] == original["clips"][i]["matches"]
        assert clip["use_case"] == original["clips"][i]["use_case"]


def test_save_scene_preserves_untouched_fields(scene_manifest_path: Path):
    _, entries, raw = mft.read_manifest(scene_manifest_path)
    entries[0].include = False
    dest = mft.reviewed_path_for(scene_manifest_path)
    mft.save_manifest(raw, entries, dest)
    round_trip = json.loads(dest.read_text(encoding="utf-8"))
    assert round_trip["videos"][0]["scenes"][0]["include"] is False
    assert round_trip["videos"][0]["scenes"][2]["text_overlay"] is True
    assert round_trip["triage_mode"] == "scene"


def test_reviewed_path_for():
    assert mft.reviewed_path_for("foo.json") == Path("foo_reviewed.json")
    assert mft.reviewed_path_for("/a/b/triage_manifest.json") == Path(
        "/a/b/triage_manifest_reviewed.json"
    )
