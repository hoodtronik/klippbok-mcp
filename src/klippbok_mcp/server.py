"""klippbok-mcp — FastMCP server wrapping the Klippbok video dataset CLI.

Exposed surface (13 tools, 1 resource, 2 prompts):

    Pipeline tools  — one per Klippbok CLI subcommand. Shell out only;
                      never import klippbok internals.
        klippbok_check_installation
        klippbok_scan
        klippbok_triage
        klippbok_ingest
        klippbok_normalize
        klippbok_caption
        klippbok_score
        klippbok_extract
        klippbok_audit
        klippbok_validate
        klippbok_organize

    Manifest tools — operate on files Klippbok writes. Schema-aware
                     (both triage_manifest.json clip-level and
                     scene_triage_manifest.json scene-level).
        klippbok_read_manifest
        klippbok_update_manifest

    Resource
        klippbok://pipelines  — training-goal -> pipeline recipe guide

    Prompts
        plan_dataset_pipeline   — ask the model to plan a pipeline
        review_triage_results   — ask the model to help review a manifest
"""
from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path
from typing import Any, Literal, Optional

from mcp.server.fastmcp import FastMCP

from . import manifest as mft
from . import runner


# ---------------------------------------------------------------- constants


# CLAUDE-NOTE: FastMCP() takes `instructions=`, not `description=` / `version=`
# as the original build spec assumed. mcp 1.27 SDK. Version is inferred from
# package metadata (pyproject.toml).
SERVER_INSTRUCTIONS = (
    "MCP server for Klippbok (https://github.com/alvdansen/klippbok), a "
    "video dataset curation toolkit for LoRA training. Exposes the full "
    "pipeline: scan, triage, ingest, normalize, caption, score, extract, "
    "audit, validate, organize. Also exposes manifest read/update tools "
    "for reviewing triage output programmatically. Every pipeline tool "
    "shells out to `python -m klippbok ... --help`-documented CLI commands; "
    "no Klippbok internals are imported, so the server stays compatible as "
    "Klippbok evolves. Set KLIPPBOK_PYTHON if Klippbok is installed in a "
    "separate venv from the one running this server. Call "
    "klippbok_check_installation first to verify the environment. For "
    "pipeline planning, see the klippbok://pipelines resource and the "
    "plan_dataset_pipeline prompt."
)

mcp = FastMCP("klippbok", instructions=SERVER_INSTRUCTIONS)


# Literal aliases so MCP clients see enum-typed parameters instead of free strings.
Provider = Literal["gemini", "replicate", "openai"]
UseCase = Literal["character", "style", "motion", "object"]


# ---------------------------------------------------------------- helpers


def _api_env() -> dict[str, str]:
    """Pluck known VLM API keys from the server env for subprocess pass-through.

    MCP clients (Claude Desktop / Cursor / Claude Code) inject these via the
    ``env:`` field in their server config. They never live on disk unless
    the user puts them in ``.env`` and loads manually — the MCP server is
    stateless.
    """
    return {
        k: os.environ[k]
        for k in ("GEMINI_API_KEY", "REPLICATE_API_TOKEN")
        if os.environ.get(k)
    }


# ---------------------------------------------------------------- pipeline tools


@mcp.tool(
    description=(
        "Verify the Klippbok CLI, ffmpeg, and Python are all available in "
        "the server's subprocess environment. Call this first when wiring "
        "up the MCP server to a client — it reports the same info a human "
        "would check manually and surfaces common misconfigurations "
        "(missing klippbok, ffmpeg not on PATH, wrong Python) with clear "
        "messages."
    )
)
async def klippbok_check_installation() -> dict[str, Any]:
    """Run the three environment probes and return a structured report."""
    klippbok_check = await runner.run_command(
        [runner.python_executable(), "-c",
         "import klippbok; print(getattr(klippbok, '__version__', '?'))"],
        timeout=15.0,
    )
    klippbok_ok = klippbok_check.succeeded
    klippbok_version = klippbok_check.stdout.strip() if klippbok_ok else None
    klippbok_error = None
    if not klippbok_ok:
        err = (klippbok_check.stderr or klippbok_check.stdout or "unknown").strip().splitlines()
        klippbok_error = err[-1] if err else "klippbok not importable"

    ffmpeg_path = shutil.which("ffmpeg")
    ffmpeg_version: Optional[str] = None
    ffmpeg_error: Optional[str] = None
    if ffmpeg_path:
        probe = await runner.run_command(["ffmpeg", "-version"], timeout=5.0)
        if probe.succeeded and probe.stdout:
            ffmpeg_version = probe.stdout.splitlines()[0]
        else:
            ffmpeg_error = "ffmpeg on PATH but returned non-zero"
    else:
        ffmpeg_error = (
            "ffmpeg NOT on PATH. Install from https://www.ffmpeg.org/download.html "
            "(Windows), `brew install ffmpeg` (macOS), or `apt install ffmpeg` "
            "(Linux). Klippbok requires it for scene detection, normalization, "
            "and caption frame sampling."
        )

    py_probe = await runner.run_command(
        [runner.python_executable(), "-c", "import sys; print(sys.version.split()[0])"],
        timeout=5.0,
    )
    python_version = py_probe.stdout.strip() if py_probe.succeeded else "?"

    return {
        "klippbok": {
            "ok": klippbok_ok,
            "version": klippbok_version,
            "error": klippbok_error,
            "python_path": runner.python_executable(),
        },
        "ffmpeg": {
            "ok": bool(ffmpeg_path) and ffmpeg_error is None,
            "path": ffmpeg_path,
            "version_line": ffmpeg_version,
            "error": ffmpeg_error,
        },
        "python": {
            "version": python_version,
            "path": runner.python_executable(),
            "klippbok_python_env_var": (
                "set" if "KLIPPBOK_PYTHON" in os.environ else "not set"
            ),
        },
        "api_keys_detected": list(_api_env().keys()),
    }


@mcp.tool(
    description=(
        "Run `klippbok.video scan` on a directory of video clips. Read-only "
        "diagnostic: reports resolution, fps, frame count, and codec issues "
        "without modifying any files. Safe first command when inspecting a "
        "new dataset."
    )
)
async def klippbok_scan(
    directory: str,
    fps: Optional[int] = None,
    verbose: bool = False,
    config: Optional[str] = None,
) -> dict[str, Any]:
    """Probe a clips directory for quality issues.

    Args:
        directory: Path to the directory of video clips to scan.
        fps: Target frame rate (Klippbok default: 16 for Wan models).
        verbose: If True, emit per-clip details instead of grouped summary.
        config: Optional path to a ``klippbok_data.yaml`` config file.
    """
    args: list[str] = [directory]
    if config:
        args += ["--config", config]
    if fps is not None:
        args += ["--fps", str(int(fps))]
    if verbose:
        args.append("--verbose")
    result = await runner.run_klippbok("klippbok.video", "scan", args, timeout=120.0)
    return result.to_dict()


@mcp.tool(
    description=(
        "Run `klippbok.video triage` — CLIP-based visual matching of clips "
        "against reference images. Writes `triage_manifest.json` (or "
        "`scene_triage_manifest.json` for long videos Klippbok auto-scene-"
        "detects). The concepts directory must have one subfolder per "
        "concept, each with 5-20 reference images (jpg/png) — Klippbok "
        "will NOT auto-generate these. CLIP model download (~150 MB on "
        "first run) is cached for subsequent invocations. GPU strongly "
        "recommended; CPU works but is slow."
    )
)
async def klippbok_triage(
    directory: str,
    concepts_dir: str,
    threshold: float = 0.70,
    frames: int = 5,
    output: Optional[str] = None,
    organize: Optional[str] = None,
    move: bool = False,
    frames_per_scene: int = 2,
    scene_threshold: float = 27.0,
    clip_model: str = "openai/clip-vit-base-patch32",
) -> dict[str, Any]:
    """Match clips against reference images; emit a triage manifest.

    Args:
        directory: Clips directory to triage.
        concepts_dir: Reference-images directory with subfolder per concept.
        threshold: CLIP similarity cutoff 0.0-1.0 (default 0.70).
        frames: Frames sampled per clip (default 5).
        output: Custom path for the manifest JSON.
        organize: If set, copy/move matched clips into concept-named folders.
        move: Move files instead of copying when --organize is set.
        frames_per_scene: Frames sampled per detected scene on long videos.
        scene_threshold: Scene detection sensitivity (default 27.0).
        clip_model: CLIP model id (default openai/clip-vit-base-patch32).
    """
    args: list[str] = [directory, "--concepts", concepts_dir]
    if threshold is not None:
        args += ["--threshold", str(float(threshold))]
    if frames:
        args += ["--frames", str(int(frames))]
    if output:
        args += ["--output", output]
    if organize:
        args += ["--organize", organize]
        if move:
            args.append("--move")
    if frames_per_scene:
        args += ["--frames-per-scene", str(int(frames_per_scene))]
    if scene_threshold is not None:
        args += ["--scene-threshold", str(float(scene_threshold))]
    if clip_model:
        args += ["--clip-model", clip_model]
    result = await runner.run_klippbok("klippbok.video", "triage", args, timeout=1800.0)
    return result.to_dict()


@mcp.tool(
    description=(
        "Run `klippbok.video ingest` — scene-detect, split, and normalize "
        "raw videos into training clips. Pass `triage_manifest` to only "
        "split scenes marked include:true in a reviewed manifest. Auto-"
        "captioning via `caption=True` requires the appropriate API key "
        "in the server's env."
    )
)
async def klippbok_ingest(
    video: str,
    output: str,
    config: Optional[str] = None,
    threshold: Optional[float] = None,
    max_frames: Optional[int] = None,
    triage_manifest: Optional[str] = None,
    caption: bool = False,
    provider: Optional[Provider] = None,
) -> dict[str, Any]:
    """Split long videos into scene-level clips and normalize them.

    Args:
        video: Path to a video file or directory of videos.
        output: Output directory for split clips (required).
        config: Optional klippbok_data.yaml path.
        threshold: Scene detection threshold (default 27.0).
        max_frames: Max frames per clip (default 81 = ~5s @ 16fps; 0=unlimited).
        triage_manifest: Path to a scene_triage_manifest.json to filter by.
        caption: If True, run caption on each clip after splitting.
        provider: VLM provider for inline captioning (gemini/replicate/openai).
    """
    args: list[str] = [video, "--output", output]
    if config:
        args += ["--config", config]
    if threshold is not None:
        args += ["--threshold", str(float(threshold))]
    if max_frames is not None:
        args += ["--max-frames", str(int(max_frames))]
    if triage_manifest:
        args += ["--triage", triage_manifest]
    if caption:
        args.append("--caption")
        if provider:
            args += ["--provider", provider]
    extra = _api_env() if caption else None
    result = await runner.run_klippbok(
        "klippbok.video", "ingest", args, timeout=3600.0, extra_env=extra
    )
    return result.to_dict()


@mcp.tool(
    description=(
        "Run `klippbok.video normalize` — batch fix fps / resolution / frame "
        "count on already-split clips. Use this when you already have short "
        "training clips and don't need scene detection."
    )
)
async def klippbok_normalize(
    directory: str,
    output: str,
    config: Optional[str] = None,
    fps: Optional[int] = None,
    format: Optional[Literal[".mp4", ".mov", ".mkv"]] = None,
) -> dict[str, Any]:
    """Standardize fps/resolution on pre-split clips.

    Args:
        directory: Directory of clips to normalize.
        output: Output directory for normalized clips (required).
        config: Optional klippbok_data.yaml path.
        fps: Target frame rate (Klippbok default: 16).
        format: Force output container format. None = match source.
    """
    args: list[str] = [directory, "--output", output]
    if config:
        args += ["--config", config]
    if fps is not None:
        args += ["--fps", str(int(fps))]
    if format:
        args += ["--format", format]
    result = await runner.run_klippbok(
        "klippbok.video", "normalize", args, timeout=1800.0
    )
    return result.to_dict()


@mcp.tool(
    description=(
        "Run `klippbok.video caption` — generate .txt sidecar captions for "
        "each clip using a vision-language model. Requires a provider API "
        "key in the server env (GEMINI_API_KEY for gemini, REPLICATE_API_"
        "TOKEN for replicate; openai uses --base-url + --model, typically "
        "localhost:11434 for Ollama)."
    )
)
async def klippbok_caption(
    directory: str,
    provider: Provider = "gemini",
    use_case: Optional[UseCase] = None,
    anchor_word: Optional[str] = None,
    tags: Optional[list[str]] = None,
    overwrite: bool = False,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    caption_fps: int = 1,
) -> dict[str, Any]:
    """Write .txt captions next to each clip.

    Args:
        directory: Clips directory.
        provider: gemini (recommended), replicate, or openai (Ollama/OpenAI-compat).
        use_case: character | style | motion | object — prompt template selection.
        anchor_word: Prepended to every caption (e.g. character/style token).
        tags: Secondary anchor tags the model should mention when relevant.
        overwrite: Replace existing .txt captions.
        base_url: For provider=openai, OpenAI-compatible endpoint (default Ollama).
        model: For provider=openai, model name (default llama3.2-vision).
        caption_fps: Frame sampling rate for captioning (default 1 fps).
    """
    args: list[str] = [directory, "--provider", provider]
    if use_case:
        args += ["--use-case", use_case]
    if anchor_word:
        args += ["--anchor-word", anchor_word]
    if tags:
        args += ["--tags", *tags]
    if overwrite:
        args.append("--overwrite")
    if base_url:
        args += ["--base-url", base_url]
    if model:
        args += ["--model", model]
    if caption_fps:
        args += ["--caption-fps", str(int(caption_fps))]
    result = await runner.run_klippbok(
        "klippbok.video", "caption", args, timeout=1800.0, extra_env=_api_env()
    )
    return result.to_dict()


@mcp.tool(
    description=(
        "Run `klippbok.video score` — local heuristic quality check on "
        "existing .txt caption files. No API calls, no network. Fast."
    )
)
async def klippbok_score(directory: str) -> dict[str, Any]:
    """Score existing caption quality.

    Args:
        directory: Directory of `.txt` caption files.
    """
    result = await runner.run_klippbok(
        "klippbok.video", "score", [directory], timeout=120.0
    )
    return result.to_dict()


@mcp.tool(
    description=(
        "Run `klippbok.video extract` — export reference frames as PNG from "
        "a directory of clips (or already still images). Useful for making "
        "model preview thumbnails or curating concept references."
    )
)
async def klippbok_extract(
    directory: str,
    output: Optional[str] = None,
    strategy: Literal["first_frame", "best_frame"] = "first_frame",
    samples: int = 10,
    overwrite: bool = False,
    selections: Optional[str] = None,
    template: Optional[str] = None,
) -> dict[str, Any]:
    """Export reference frames as PNG.

    Args:
        directory: Clips or images directory.
        output: Output directory for PNG references.
        strategy: first_frame (default) or best_frame (scores samples).
        samples: Frames sampled when strategy=best_frame.
        overwrite: Replace existing PNGs.
        selections: Path to a JSON selections manifest (from --template).
        template: If set, write a selection template JSON to this path and exit.
            NOTE: Takes a path argument, not a boolean — caught via `--help`.
    """
    args: list[str] = [directory]
    if output:
        args += ["--output", output]
    if strategy:
        args += ["--strategy", strategy]
    if samples:
        args += ["--samples", str(int(samples))]
    if overwrite:
        args.append("--overwrite")
    if selections:
        args += ["--selections", selections]
    if template:
        args += ["--template", template]
    result = await runner.run_klippbok(
        "klippbok.video", "extract", args, timeout=600.0
    )
    return result.to_dict()


@mcp.tool(
    description=(
        "Run `klippbok.video audit` — re-caption existing clips with a VLM "
        "and compare to existing captions. Catches caption drift and quality "
        "issues. Same API key requirements as klippbok_caption."
    )
)
async def klippbok_audit(
    directory: str,
    provider: Provider = "gemini",
    use_case: Optional[UseCase] = None,
    mode: Literal["report_only", "save_audit"] = "report_only",
) -> dict[str, Any]:
    """Audit caption quality by re-captioning with a VLM.

    Args:
        directory: Directory of captioned clips.
        provider: VLM provider.
        use_case: Prompt template selector.
        mode: report_only (default, no files written) or save_audit.
    """
    args: list[str] = [directory, "--provider", provider]
    if use_case:
        args += ["--use-case", use_case]
    if mode:
        args += ["--mode", mode]
    result = await runner.run_klippbok(
        "klippbok.video", "audit", args, timeout=1800.0, extra_env=_api_env()
    )
    return result.to_dict()


@mcp.tool(
    description=(
        "Run `klippbok.dataset validate` — dataset-level completeness and "
        "quality checks. Accepts either a dataset folder or a "
        "klippbok_data.yaml config file as the positional argument."
    )
)
async def klippbok_validate(
    path: str,
    config: Optional[str] = None,
    manifest: bool = False,
    buckets: bool = False,
    quality: bool = False,
    duplicates: bool = False,
    json_output: bool = False,
) -> dict[str, Any]:
    """Check dataset completeness before training.

    Args:
        path: Dataset folder or klippbok_data.yaml path.
        config: Optional klippbok_data.yaml override.
        manifest: Write klippbok_manifest.json to the dataset folder.
        buckets: Show training bucket preview.
        quality: Blur / exposure checks on reference images.
        duplicates: Perceptual duplicate detection.
        json_output: Emit JSON instead of a formatted report (maps to --json).
    """
    args: list[str] = [path]
    if config:
        args += ["--config", config]
    if manifest:
        args.append("--manifest")
    if buckets:
        args.append("--buckets")
    if quality:
        args.append("--quality")
    if duplicates:
        args.append("--duplicates")
    if json_output:
        args.append("--json")
    result = await runner.run_klippbok(
        "klippbok.dataset", "validate", args, timeout=120.0
    )
    return result.to_dict()


@mcp.tool(
    description=(
        "Run `klippbok.dataset organize` — restructure a validated dataset "
        "into a trainer-specific layout (musubi, aitoolkit, or flat)."
    )
)
async def klippbok_organize(
    path: str,
    output: str,
    layout: Literal["flat", "klippbok"] = "flat",
    trainer: Optional[list[str]] = None,
    concepts: Optional[str] = None,
    move: bool = False,
    dry_run: bool = False,
    strict: bool = False,
    config: Optional[str] = None,
    manifest: bool = False,
) -> dict[str, Any]:
    """Restructure dataset for a training framework.

    Args:
        path: Source dataset folder.
        output: Target directory (required).
        layout: flat (default, universal) or klippbok (hierarchical).
        trainer: One or more trainer configs to emit. Known: musubi, aitoolkit.
            The CLI is free-form text so custom trainers work too.
        concepts: Comma-separated concept-folder filter.
        move: Move files instead of copy (destructive).
        dry_run: Preview what would happen without touching files.
        strict: Also exclude samples with warnings, not just errors.
        config: Optional klippbok_data.yaml path.
        manifest: Write klippbok_manifest.json to the output.
    """
    args: list[str] = [path, "--output", output]
    if layout:
        args += ["--layout", layout]
    for t in trainer or []:
        args += ["--trainer", t]
    if concepts:
        args += ["--concepts", concepts]
    if move:
        args.append("--move")
    if dry_run:
        args.append("--dry-run")
    if strict:
        args.append("--strict")
    if config:
        args += ["--config", config]
    if manifest:
        args.append("--manifest")
    result = await runner.run_klippbok(
        "klippbok.dataset", "organize", args, timeout=600.0
    )
    return result.to_dict()


# ---------------------------------------------------------------- manifest tools


@mcp.tool(
    description=(
        "Read and summarize a Klippbok triage manifest (clip-level or scene-"
        "level auto-detected). Returns structured summary stats + a list of "
        "entries with scores and include flags so an agent can reason about "
        "what to include/exclude."
    )
)
async def klippbok_read_manifest(manifest_path: str) -> dict[str, Any]:
    """Parse a triage manifest and return structured content.

    Args:
        manifest_path: Path to triage_manifest.json or scene_triage_manifest.json.

    Returns:
        dict with ``summary`` (kind, totals, threshold, concepts) and
        ``entries`` (list of entry dicts each with idx, score, concept,
        include, etc.). The internal writeback pointers are hidden.
    """
    p = Path(manifest_path)
    if not p.exists():
        return {"error": f"Manifest file not found: {manifest_path}"}
    try:
        summary, entries, _raw = mft.read_manifest(p)
    except ValueError as exc:
        return {"error": str(exc), "path": str(p)}
    return {
        "summary": summary.to_dict(),
        "entries": [e.to_public_dict() for e in entries],
    }


@mcp.tool(
    description=(
        "Update include/exclude flags on a triage manifest and save. Bulk "
        "and per-index operations compose in a defined order: include_all/"
        "exclude_all -> include_above_threshold -> include_indices -> "
        "exclude_indices (later wins). Writes to `*_reviewed.json` alongside "
        "the original by default so your raw manifest stays untouched."
    )
)
async def klippbok_update_manifest(
    manifest_path: str,
    include_above_threshold: Optional[float] = None,
    include_indices: Optional[list[int]] = None,
    exclude_indices: Optional[list[int]] = None,
    include_all: bool = False,
    exclude_all: bool = False,
    overwrite_original: bool = False,
) -> dict[str, Any]:
    """Apply mutations and save.

    Args:
        manifest_path: Path to an existing manifest.
        include_above_threshold: Score cutoff; entries with score >= threshold
            get include=True, others include=False. Applied after bulk reset.
        include_indices: Force-include specific indices (overrides threshold).
        exclude_indices: Force-exclude specific indices (wins over include).
        include_all: Bulk-include everything first. Mutex with exclude_all.
        exclude_all: Bulk-exclude everything first. Mutex with include_all.
        overwrite_original: If True, save back to the original path. Otherwise
            writes to `<stem>_reviewed<ext>` alongside.
    """
    p = Path(manifest_path)
    if not p.exists():
        return {"error": f"Manifest file not found: {manifest_path}"}
    try:
        _summary, entries, raw = mft.read_manifest(p)
    except ValueError as exc:
        return {"error": str(exc), "path": str(p)}

    try:
        mft.apply_mutations(
            entries,
            include_above_threshold=include_above_threshold,
            include_indices=include_indices,
            exclude_indices=exclude_indices,
            include_all=include_all,
            exclude_all=exclude_all,
        )
    except ValueError as exc:
        return {"error": str(exc)}

    dest = p if overwrite_original else mft.reviewed_path_for(p)
    mft.save_manifest(raw, entries, dest)
    final_summary, _, _ = mft.read_manifest(dest)
    return {
        "summary": final_summary.to_dict(),
        "written_to": str(dest),
        "overwrote_original": overwrite_original,
    }


# ---------------------------------------------------------------- resource


_PIPELINES_MD = """\
# Klippbok pipelines — which workflow for which training goal

Use this as a guide when planning a curation pipeline with the
`klippbok_*` tools. Each recipe lists the tools to call in order and
the key flag choices that matter.

## Recipe A — Character LoRA from raw footage

You have long videos (movies, TV, interviews). You want a LoRA that
reproduces one specific person.

1. `klippbok_scan(directory=<clips_dir>)` — inventory.
2. Populate `<concepts_dir>/<character_name>/` with 5–20 reference stills
   by hand. Klippbok does NOT auto-generate these.
3. `klippbok_triage(directory=<clips_dir>, concepts_dir=<concepts_dir>)`.
4. `klippbok_read_manifest(manifest_path=<triage output>)` to inspect.
5. `klippbok_update_manifest(manifest_path=..., include_above_threshold=0.75,
   exclude_indices=[bad], include_indices=[missed good])`.
6. `klippbok_ingest(video=<raw_source>, output=<clips_out>,
   triage_manifest=<reviewed_path>)`.
7. `klippbok_caption(directory=<clips_out>, provider="gemini",
   use_case="character", anchor_word="<name>")`.
8. `klippbok_validate(path=<clips_out>, quality=True, duplicates=True)`.
9. `klippbok_organize(path=<clips_out>, output=<final_dir>, trainer=["aitoolkit"])`.

## Recipe B — Pre-cut clips, no scene detection needed

You already have short clips.

1. `klippbok_scan`.
2. `klippbok_normalize(directory=<clips_dir>, output=<normalized_out>,
   fps=16)`.
3. `klippbok_caption`.
4. `klippbok_validate` then `klippbok_organize`.

## Recipe C — Style LoRA

Same as A but `use_case="style"`. Often skip `anchor_word` or use a
short style token. Collect 20–80 references sharing the aesthetic
across varied subjects.

## Recipe D — Motion LoRA (video only)

Same scaffolding as A but `use_case="motion"`. Typically smaller
datasets (10–30 clips) — caption quality matters more than volume. Make
sure clips isolate the motion; a 30-second clip of someone dancing
beats a 5-minute clip where they dance for 10 seconds.

## Recipe E — Re-caption an existing dataset

Skip scan/triage/ingest — you already have clips + captions.

1. `klippbok_caption(directory=..., overwrite=True, provider=<new>)`.
2. `klippbok_score` — local heuristic sanity check.
3. `klippbok_audit` — VLM re-caption vs existing.

## Recipe F — Experimental triage comparison

To A/B-test CLIP models or thresholds before committing to a big
ingest run:

1. Multiple `klippbok_triage` calls with different `clip_model` or
   `threshold` values, each with a distinct `output` path.
2. `klippbok_read_manifest` on each; compare `included` / `excluded`
   counts and score distributions.
3. Pick the one that matched your dataset best; continue from Recipe
   A step 5.

## Object LoRA

Same as A but `use_case="object"`. Collect 15–40 shots of the object
in varied contexts.

## Before training — always

- Run `klippbok_validate(quality=True, duplicates=True)` and fix
  errors. Warnings are judgment calls (a few low-res clips in a 200-
  clip set is probably fine).
- Run `klippbok_organize` to lay the dataset out the way your trainer
  expects (aitoolkit / musubi-tuner / flat).
"""


@mcp.resource("klippbok://pipelines", mime_type="text/markdown")
def pipelines_guide() -> str:
    """Static markdown listing canonical training-goal -> pipeline recipes."""
    return _PIPELINES_MD


# ---------------------------------------------------------------- prompts


@mcp.prompt(
    description=(
        "Plan a Klippbok dataset curation pipeline for a specific training "
        "goal. The model will read the klippbok://pipelines resource and "
        "return an ordered plan of tool calls with flag values."
    )
)
def plan_dataset_pipeline(
    training_goal: str,
    source_material: str,
    target_trainer: Optional[str] = None,
) -> str:
    """Ask the agent to plan a pipeline.

    Args:
        training_goal: What you want the LoRA to do.
            E.g. "character LoRA of Jane from 2 hours of interview footage".
        source_material: Describe what you have on disk.
            E.g. "one MP4 file, 2h 15m, 1080p 30fps, no pre-cut clips".
        target_trainer: Optional — e.g. "musubi-tuner", "ai-toolkit".
    """
    trainer_hint = f"\n- Target trainer: {target_trainer}" if target_trainer else ""
    return (
        f"I'm preparing a LoRA training dataset using Klippbok via its MCP "
        f"server.\n\n"
        f"- Training goal: {training_goal}\n"
        f"- Source material: {source_material}{trainer_hint}\n\n"
        f"Please plan the pipeline. First, fetch the `klippbok://pipelines` "
        f"resource so you're working from the canonical recipes. Then:\n\n"
        f"1. Identify which recipe (A/B/C/D/E/F or a variant) fits best, and "
        f"why in one sentence.\n"
        f"2. List the ordered `klippbok_*` MCP tools I should call, one per "
        f"step, with specific flag values and their rationale.\n"
        f"3. Flag any manual steps that need me — populating a concepts "
        f"folder, reviewing a triage manifest — and WHEN in the sequence they "
        f"fit.\n"
        f"4. Tell me what output to expect at each step so I know when to "
        f"move on.\n"
        f"5. Call out any decisions I should make (thresholds to tune, "
        f"trainers to pick, API keys I'll need) before starting.\n\n"
        f"Do NOT start running tools yet — this is a planning turn. Wait for "
        f"my confirmation before any invocation."
    )


@mcp.prompt(
    description=(
        "Help review a triage manifest intelligently. The model will read "
        "the manifest, analyze the score distribution, flag suspicious "
        "entries, and propose a sequence of update calls — but won't run "
        "them until the user approves."
    )
)
def review_triage_results(manifest_path: str) -> str:
    """Ask the agent to help review a triage manifest.

    Args:
        manifest_path: Path to a triage_manifest.json or scene_triage_manifest.json
            that Klippbok just produced.
    """
    return (
        f"Please help me review my Klippbok triage manifest at:\n"
        f"`{manifest_path}`\n\n"
        f"Start by calling `klippbok_read_manifest(manifest_path=...)`. Then "
        f"analyze the result:\n\n"
        f"1. Summarize the score distribution — how many entries fall in "
        f"each 0.1 band (0.9+, 0.8-0.9, 0.7-0.8, ...).\n"
        f"2. Suggest a threshold based on the distribution: one that catches "
        f"the clear matches without dragging in noise. Explain why.\n"
        f"3. Flag anything suspicious:\n"
        f"   - concepts with zero matches (bad reference images?),\n"
        f"   - text_overlay entries currently included by default,\n"
        f"   - high-score entries whose label/filename suggests something "
        f"different from the concept,\n"
        f"   - obvious duplicates.\n"
        f"4. Propose a concrete sequence of `klippbok_update_manifest` calls "
        f"to get the manifest ready for Ingest. Use exclude_indices for "
        f"specific bad entries and include_indices for specific good ones "
        f"you want to force-include.\n\n"
        f"Do NOT run `klippbok_update_manifest` yet. Present the proposed "
        f"calls and wait for me to confirm. I want to see what you'd do "
        f"before it happens."
    )


# ---------------------------------------------------------------- entry point


def main() -> None:
    """Console entry point — wired to ``klippbok-mcp`` via pyproject.toml."""
    parser = argparse.ArgumentParser(
        description="MCP server wrapping the Klippbok video dataset curation CLI."
    )
    # CLAUDE-NOTE: stdio is the default because every MCP client we care
    # about (Claude Desktop / Claude Code / Cursor / Antigravity) spawns
    # servers over stdio. streamable-http is for remote access and MCP
    # Inspector testing.
    parser.add_argument(
        "--transport",
        choices=("stdio", "http"),
        default="stdio",
        help="MCP transport. `stdio` for local clients (default); `http` for "
             "remote clients and MCP Inspector.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host for --transport http.")
    parser.add_argument("--port", type=int, default=8000, help="Port for --transport http.")
    args = parser.parse_args()

    if args.transport == "http":
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
