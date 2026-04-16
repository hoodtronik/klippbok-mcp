"""klippbok-mcp — FastMCP server wrapping the Klippbok video dataset CLI.

This checkpoint ships just two tools so the MCP plumbing can be verified
end-to-end before fanning the pattern out to every pipeline stage:

  * ``klippbok_check_installation`` — no-arg probe returning klippbok /
    ffmpeg / Python / .env status. Call this first when configuring a
    client; surfaces environment issues before any other tool fails.
  * ``klippbok_scan`` — read-only quality diagnostic on a directory of
    video clips. Safe first command to run against a new dataset.

Remaining tools (triage, ingest, normalize, caption, score, extract, audit,
validate, organize, manifest read/update, pipeline resource, prompts)
land in the next commit once this loop is verified.
"""
from __future__ import annotations

import argparse
import shutil
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from . import runner


# CLAUDE-NOTE: FastMCP() takes `instructions=`, not the `description=` field
# the spec assumed. Version comes from package metadata, not a constructor
# kwarg. Instructions are what MCP clients display as the server's top-level
# summary when the user picks it from a server list.
SERVER_INSTRUCTIONS = (
    "MCP server for Klippbok (https://github.com/alvdansen/klippbok), a "
    "video dataset curation toolkit for LoRA training. Exposes the full "
    "pipeline: scan, triage, ingest, normalize, caption, score, extract, "
    "audit, validate, organize. Also exposes manifest read/update tools "
    "for reviewing triage output without the Gradio UI. Every tool shells "
    "out to `python -m klippbok ... --help`-documented CLI commands; no "
    "Klippbok internals are imported, so the server stays compatible as "
    "Klippbok evolves. Set KLIPPBOK_PYTHON if Klippbok is installed in a "
    "separate venv from the one running this server."
)

mcp = FastMCP("klippbok", instructions=SERVER_INSTRUCTIONS)


# ---------------------------------------------------------------- tools


@mcp.tool(
    description=(
        "Verify the Klippbok CLI, ffmpeg, and Python are all available in "
        "the server's subprocess environment. Call this first when wiring "
        "up the MCP server to a client — it reports the same info a human "
        "would check manually, and surfaces common misconfigurations "
        "(missing klippbok, ffmpeg not on PATH, wrong Python) with clear "
        "messages."
    )
)
async def klippbok_check_installation() -> dict[str, Any]:
    """Run the three environment probes and return a structured report."""
    # 1. klippbok import check via the configured Python.
    klippbok_check = await runner.run_command(
        [runner.python_executable(), "-c", "import klippbok; print(getattr(klippbok, '__version__', '?'))"],
        timeout=15.0,
    )
    klippbok_ok = klippbok_check.succeeded
    klippbok_version = klippbok_check.stdout.strip() if klippbok_ok else None
    klippbok_error = None
    if not klippbok_ok:
        klippbok_error = (klippbok_check.stderr or klippbok_check.stdout or "unknown").strip().splitlines()[-1:]
        klippbok_error = klippbok_error[0] if klippbok_error else "klippbok not importable"

    # 2. ffmpeg presence. shutil.which is synchronous but fast; no need for
    #    an async subprocess probe when we don't need to parse version text.
    ffmpeg_path = shutil.which("ffmpeg")
    ffmpeg_version: Optional[str] = None
    ffmpeg_error: Optional[str] = None
    if ffmpeg_path:
        ffmpeg_probe = await runner.run_command(["ffmpeg", "-version"], timeout=5.0)
        if ffmpeg_probe.succeeded and ffmpeg_probe.stdout:
            ffmpeg_version = ffmpeg_probe.stdout.splitlines()[0]
        else:
            ffmpeg_error = "ffmpeg on PATH but returned non-zero"
    else:
        ffmpeg_error = (
            "ffmpeg NOT on PATH. Install from https://www.ffmpeg.org/download.html "
            "(Windows), `brew install ffmpeg` (macOS), or `apt install ffmpeg` "
            "(Linux). Klippbok requires it for scene detection and normalization."
        )

    # 3. Python runtime info.
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
                "KLIPPBOK_PYTHON is set" if "KLIPPBOK_PYTHON" in __import__("os").environ
                else "KLIPPBOK_PYTHON not set; using sys.executable"
            ),
        },
    }


@mcp.tool(
    description=(
        "Run `klippbok.video scan` on a directory of video clips. Read-only "
        "diagnostic: reports resolution, fps, frame count, and codec issues "
        "without modifying any files. Safe first command when inspecting a "
        "new dataset. Returns exit code, duration, and the full stdout "
        "report. Look at stdout for the per-clip breakdown; non-zero exit "
        "means Klippbok couldn't process the directory (wrong path, no "
        "videos, or a config error)."
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
        directory: Path to the directory of video clips to scan. Required.
        fps: Target frame rate (Klippbok default: 16 for Wan models). Only
            passed to the CLI if specified.
        verbose: If True, emit per-clip details instead of a grouped summary.
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


# ---------------------------------------------------------------- entry point


def main() -> None:
    """Console entry point — wired to ``klippbok-mcp`` via pyproject.toml."""
    parser = argparse.ArgumentParser(
        description="MCP server wrapping the Klippbok video dataset curation CLI."
    )
    # CLAUDE-NOTE: stdio is the default because every MCP client we care
    # about (Claude Desktop / Claude Code / Cursor) spawns servers over
    # stdio. streamable-http is for remote access and MCP Inspector testing.
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
