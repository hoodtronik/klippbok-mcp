"""Read / write / mutate Klippbok triage manifests.

Klippbok's ``triage`` command writes one of two JSON shapes:

- ``triage_manifest.json`` — clip-level, top-level ``clips: [...]`` array.
  Each clip has an ``include: true|false`` flag and a ``matches: [...]``
  array with CLIP similarity scores.

- ``scene_triage_manifest.json`` — scene-level, top-level ``videos: [...]``
  each with a nested ``scenes: [...]`` array. The ``triage_mode: "scene"``
  marker is present at the root. Each scene has its own ``include`` flag
  plus ``start_time`` / ``end_time``.

Callers (MCP tools in ``server.py``) shouldn't care which shape they get.
This module flattens either into a uniform ``list[ManifestEntry]`` with
writeback pointers so mutation + save preserves every field the agent
didn't touch.

Pure stdlib — no json/pathlib dependency bumps needed. Safe to call from
MCP async handlers (all functions are synchronous; their I/O is bounded
by file size).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal, Optional


ManifestKind = Literal["clip", "scene"]


# CLAUDE-NOTE: ManifestEntry intentionally flattens both schemas into one
# shape. _clip_idx / _video_idx / _scene_idx point back into the original
# raw dict for writeback — exactly one pair is populated per kind. Leading
# underscore is a convention signalling "don't surface these to the MCP
# client"; ``to_public_dict`` below strips them.
@dataclass
class ManifestEntry:
    idx: int
    kind: ManifestKind
    source_path: str
    label: str
    start_seconds: Optional[float]
    end_seconds: Optional[float]
    score: float
    best_concept: str
    include: bool
    text_overlay: bool
    use_case: Optional[str]
    # Writeback pointers — not exposed to the MCP caller.
    _clip_idx: Optional[int] = None
    _video_idx: Optional[int] = None
    _scene_idx: Optional[int] = None

    def to_public_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for key in list(d.keys()):
            if key.startswith("_"):
                del d[key]
        return d


@dataclass
class ManifestSummary:
    """What the MCP tool surfaces for a loaded/updated manifest."""

    path: str
    kind: ManifestKind
    total_entries: int
    included: int
    excluded: int
    text_overlays: int
    concepts: list[str] = field(default_factory=list)
    triage_threshold: Optional[float] = None
    triage_model: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------- load


def read_manifest(path: str | Path) -> tuple[ManifestSummary, list[ManifestEntry], dict[str, Any]]:
    """Parse either manifest schema.

    Returns ``(summary, entries, raw_dict)``. The ``raw_dict`` is the
    full unmodified JSON, preserved so a later ``save_manifest`` can
    write ``include`` flags back without touching any other field —
    this is how we keep round-trips lossless across Klippbok versions.
    """
    p = Path(path)
    raw = json.loads(p.read_text(encoding="utf-8"))

    triage_meta = raw.get("triage") or {}
    concepts = [c.get("name") for c in (raw.get("concepts") or []) if c.get("name")]

    if raw.get("triage_mode") == "scene" or "videos" in raw:
        entries = _parse_scene(raw)
        kind: ManifestKind = "scene"
    elif "clips" in raw:
        entries = _parse_clips(raw)
        kind = "clip"
    else:
        raise ValueError(
            "Unrecognized manifest schema. Expected either `clips` (clip-level) "
            "or `videos` / `triage_mode: scene` (scene-level). Got top-level "
            f"keys: {sorted(raw.keys())}"
        )

    included = sum(1 for e in entries if e.include)
    text_overlays = sum(1 for e in entries if e.text_overlay)
    summary = ManifestSummary(
        path=str(p),
        kind=kind,
        total_entries=len(entries),
        included=included,
        excluded=len(entries) - included,
        text_overlays=text_overlays,
        concepts=concepts,
        triage_threshold=triage_meta.get("threshold"),
        triage_model=triage_meta.get("model"),
    )
    return summary, entries, raw


def _parse_clips(raw: dict[str, Any]) -> list[ManifestEntry]:
    entries: list[ManifestEntry] = []
    for ci, clip in enumerate(raw.get("clips") or []):
        matches = clip.get("matches") or []
        best = matches[0] if matches else {}
        source = clip.get("path") or clip.get("file", "")
        entries.append(
            ManifestEntry(
                idx=len(entries),
                kind="clip",
                source_path=source,
                label=Path(source).name or clip.get("file", "<unknown>"),
                start_seconds=None,
                end_seconds=None,
                score=float(best.get("similarity", 0.0)),
                best_concept=str(best.get("concept", "")),
                include=bool(clip.get("include", True)),
                text_overlay=bool(clip.get("text_overlay", False)),
                use_case=clip.get("use_case"),
                _clip_idx=ci,
            )
        )
    return entries


def _parse_scene(raw: dict[str, Any]) -> list[ManifestEntry]:
    entries: list[ManifestEntry] = []
    for vi, video in enumerate(raw.get("videos") or []):
        vpath = video.get("path") or video.get("file", "")
        vname = Path(vpath).name or "<unknown>"
        for si, scene in enumerate(video.get("scenes") or []):
            matches = scene.get("matches") or []
            best = matches[0] if matches else {}
            start = float(scene.get("start_time", 0.0))
            end_raw = scene.get("end_time")
            end = float(end_raw) if end_raw is not None else None
            if end is not None:
                label = f"{vname} [scene {si} {start:.1f}s-{end:.1f}s]"
            else:
                label = f"{vname} [scene {si} {start:.1f}s+]"
            entries.append(
                ManifestEntry(
                    idx=len(entries),
                    kind="scene",
                    source_path=vpath,
                    label=label,
                    start_seconds=start,
                    end_seconds=end,
                    score=float(best.get("similarity", 0.0)),
                    best_concept=str(best.get("concept", "")),
                    include=bool(scene.get("include", True)),
                    text_overlay=bool(scene.get("text_overlay", False)),
                    use_case=None,
                    _video_idx=vi,
                    _scene_idx=si,
                )
            )
    return entries


# -------------------------------------------------------------- mutate + save


def apply_mutations(
    entries: list[ManifestEntry],
    *,
    include_above_threshold: Optional[float] = None,
    include_indices: Optional[Iterable[int]] = None,
    exclude_indices: Optional[Iterable[int]] = None,
    include_all: bool = False,
    exclude_all: bool = False,
) -> None:
    """Apply mutations in a defined, predictable order on the list in place.

    Order matters — later operations override earlier ones:

      1. ``include_all`` / ``exclude_all`` bulk resets.
      2. ``include_above_threshold`` score-gated pass.
      3. ``include_indices`` forced-True (overrides 1 and 2).
      4. ``exclude_indices`` forced-False (wins over everything; final pass).

    Rationale: agents typically say "include everything above 0.7, then drop
    these specific indices I know are bad, and also force-include these two
    I know are good." The order above makes that composable.
    """
    if include_all and exclude_all:
        raise ValueError("include_all and exclude_all are mutually exclusive.")

    if include_all:
        for e in entries:
            e.include = True
    elif exclude_all:
        for e in entries:
            e.include = False

    if include_above_threshold is not None:
        thr = float(include_above_threshold)
        for e in entries:
            e.include = e.score >= thr

    if include_indices:
        idxs = set(int(i) for i in include_indices)
        for e in entries:
            if e.idx in idxs:
                e.include = True

    if exclude_indices:
        idxs = set(int(i) for i in exclude_indices)
        for e in entries:
            if e.idx in idxs:
                e.include = False


def save_manifest(
    raw: dict[str, Any],
    entries: list[ManifestEntry],
    output_path: str | Path,
) -> None:
    """Write ``include`` flags from ``entries`` back into ``raw`` and serialize.

    Every other field in ``raw`` is preserved byte-for-byte. ``ensure_ascii=False``
    keeps non-ASCII filenames intact; ``indent=2`` matches Klippbok's own
    writer so a round-trip is a no-op diff in git.
    """
    for e in entries:
        if e.kind == "clip" and e._clip_idx is not None:
            raw["clips"][e._clip_idx]["include"] = bool(e.include)
        elif e.kind == "scene" and e._video_idx is not None and e._scene_idx is not None:
            raw["videos"][e._video_idx]["scenes"][e._scene_idx]["include"] = bool(e.include)
    Path(output_path).write_text(
        json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def reviewed_path_for(original: str | Path) -> Path:
    """``foo.json`` -> ``foo_reviewed.json`` — stable so "review again" overwrites."""
    p = Path(original)
    return p.with_name(p.stem + "_reviewed" + p.suffix)
