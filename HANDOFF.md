# HANDOFF — klippbok-mcp (for the next agent)

This file is for an agent (or a human contributor) picking this up cold.
It captures where the build stopped, what's verified, what isn't, and
what to do next. Delete it when the build is feature-complete.

## Status as of 2026-04-16 (commit `HEAD` on `main`)

**Feature state: infrastructure + 2 of 13 tools shipped.**

The repo has:
- A working FastMCP server exposing `klippbok_check_installation` and
  `klippbok_scan`. Both tools are defined via `@mcp.tool()` decorators
  in `src/klippbok_mcp/server.py` and call into the async subprocess
  runner.
- A self-tested async subprocess wrapper (`runner.py`) with 7 inline
  test cases passing.
- A self-tested manifest adapter (`manifest.py`) supporting both
  Klippbok triage schemas (clip-level and scene-level) with 10 inline
  test cases passing.
- `docs/cli_help.txt` generated against klippbok 0.1.0 in the sibling
  Pinokio launcher's venv — **this is authoritative**, read it before
  adding any tool.

**Not yet done** (everything below is pending — this is the next-agent
work list):

1. Test the existing 2-tool server with MCP Inspector end-to-end (see
   "Resume" below).
2. **Pause for user review** — the user explicitly asked for this
   checkpoint in the original spec. Do not proceed past here without
   confirmation.
3. Add the remaining 8 CLI wrapper tools: `klippbok_triage`,
   `klippbok_ingest`, `klippbok_normalize`, `klippbok_caption`,
   `klippbok_score`, `klippbok_extract`, `klippbok_audit`,
   `klippbok_validate`, `klippbok_organize`.
4. Add 2 manifest tools: `klippbok_read_manifest`,
   `klippbok_update_manifest` (the logic already exists in
   `manifest.py`; just wrap with `@mcp.tool()`).
5. Add the `klippbok://pipelines` resource (static markdown explaining
   which pipeline to use for which training scenario; pull from the
   Klippbok README or write fresh).
6. Add 2 prompts: `plan_dataset_pipeline`, `review_triage_results`.
7. Write pytest-based tests (runner + manifest already have inline
   self-tests; porting them is mostly mechanical).
8. Expand the README from stub to full doc — client config blocks for
   Claude Desktop / Claude Code / Cursor / MCP Inspector, a tool
   reference table, troubleshooting.

The original build spec lives in the git history's first commit message
and in the user's conversation — work from that.

## How to resume

### 1. Get your environment set up (if you're on a fresh clone)

```bash
cd f:/__PROJECTS/klippbok-mcp
uv venv --python 3.11 .venv
uv pip install -e '.[dev]'
# .env copy if you need API keys
cp .env.example .env
```

### 2. Verify the current 2-tool server works

**Quick smoke test** — confirm imports and tool registration:

```bash
.venv/Scripts/python.exe -c "from klippbok_mcp import server; print('tools:', len(server.mcp._tool_manager._tools))"
# Expected: tools: 2
```

**MCP Inspector** — the official testing harness:

```bash
# Terminal 1: run the server on HTTP so Inspector can connect.
set KLIPPBOK_PYTHON=f:/__PROJECTS/Klippbok/env/Scripts/python.exe
.venv/Scripts/klippbok-mcp.exe --transport http --port 8000

# Terminal 2: launch MCP Inspector (separate install via npm / npx).
# Then connect to http://localhost:8000/mcp and try:
#   - klippbok_check_installation (no args)
#   - klippbok_scan with directory=f:/__PROJECTS/KlippbokTest/Project
```

**Stdio transport** — what real clients use:

```bash
# From Claude Desktop's claude_desktop_config.json:
{
  "mcpServers": {
    "klippbok": {
      "command": "uv",
      "args": ["run", "--directory", "f:/__PROJECTS/klippbok-mcp", "klippbok-mcp"],
      "env": {
        "KLIPPBOK_PYTHON": "f:/__PROJECTS/Klippbok/env/Scripts/python.exe",
        "GEMINI_API_KEY": "..."
      }
    }
  }
}
```

Restart Claude Desktop; Klippbok tools should appear in the server list.

### 3. Confirm with the user before adding more tools

The user's explicit preference (captured in their cross-session Claude
memory at `~/.claude/projects/f----PROJECTS-Klippbok/memory/feedback_delivery_cadence.md`)
is **staged delivery with pause for review** between big checkpoints.
This is one of those checkpoints. Show them the working 2 tools, get
approval, then proceed.

### 4. Pattern for adding remaining CLI tools

Every tool follows the same shape (see `klippbok_scan` for the canonical
example):

```python
@mcp.tool(description="One-paragraph description shown in MCP clients.")
async def klippbok_<name>(
    # positional args first, using the types from docs/cli_help.txt
    required_arg: str,
    optional_arg: Optional[str] = None,
    flag: bool = False,
    number: Optional[int] = None,
) -> dict[str, Any]:
    """Docstring surfaced to MCP clients as the tool help.

    Args:
        required_arg: ...
        optional_arg: ...
    """
    args: list[str] = [required_arg]
    if optional_arg:
        args += ["--optional-arg", optional_arg]
    if flag:
        args.append("--flag")
    if number is not None:
        args += ["--number", str(int(number))]

    result = await runner.run_klippbok(
        "klippbok.video",  # or "klippbok.dataset" for validate/organize
        "<subcommand>",
        args,
        timeout=<per-command>,  # see below
        extra_env=_api_keys_from_env() if needs_api_key else None,
    )
    return result.to_dict()
```

**Per-command timeout guidance:**
- Read-only / quick: 120s (scan, score, validate)
- CPU-heavy or model-loading: 1800s (triage with CLIP, caption, audit)
- Long-running: 3600s (ingest on a multi-hour video)

Default is 600s in `runner.run_klippbok`; override per-tool.

**API keys**: Caption + Audit + Ingest's `--caption` path need keys.
Grab them from the server's own env (the MCP client injects them via
the `env:` field in client config) and pass via `extra_env`:

```python
def _api_env() -> dict[str, str]:
    return {
        k: os.environ[k]
        for k in ("GEMINI_API_KEY", "REPLICATE_API_TOKEN")
        if os.environ.get(k)
    }
```

### 5. Manifest tools — wiring existing logic

`manifest.py` already has `read_manifest`, `apply_mutations`,
`save_manifest`, `reviewed_path_for`. Just wrap them:

```python
@mcp.tool(description="Read and summarize a triage manifest JSON.")
async def klippbok_read_manifest(manifest_path: str) -> dict[str, Any]:
    summary, entries, _raw = manifest.read_manifest(manifest_path)
    return {
        "summary": summary.to_dict(),
        "entries": [e.to_public_dict() for e in entries],
    }


@mcp.tool(description="Update include/exclude flags and save *_reviewed.json.")
async def klippbok_update_manifest(
    manifest_path: str,
    include_above_threshold: Optional[float] = None,
    include_indices: Optional[list[int]] = None,
    exclude_indices: Optional[list[int]] = None,
    include_all: bool = False,
    exclude_all: bool = False,
    overwrite_original: bool = False,
) -> dict[str, Any]:
    summary, entries, raw = manifest.read_manifest(manifest_path)
    manifest.apply_mutations(
        entries,
        include_above_threshold=include_above_threshold,
        include_indices=include_indices,
        exclude_indices=exclude_indices,
        include_all=include_all,
        exclude_all=exclude_all,
    )
    dest = Path(manifest_path) if overwrite_original else manifest.reviewed_path_for(manifest_path)
    manifest.save_manifest(raw, entries, dest)
    # Re-read for accurate summary stats
    final_summary, _, _ = manifest.read_manifest(dest)
    return {"summary": final_summary.to_dict(), "written_to": str(dest)}
```

Both functions are sync (file I/O is bounded) — wrapping them in an
async tool is fine; asyncio just awaits a synchronous body.

### 6. Resource pattern

```python
@mcp.resource("klippbok://pipelines", mime_type="text/markdown")
def pipelines_resource() -> str:
    return PIPELINES_MARKDOWN  # string constant, sourced from Klippbok README
```

### 7. Prompt pattern

```python
@mcp.prompt(description="Plan a dataset curation pipeline for a training goal.")
def plan_dataset_pipeline(
    training_goal: str,
    source_material: str,
    target_trainer: Optional[str] = None,
) -> list[dict[str, str]]:
    # Return MCP prompt messages; FastMCP will coerce a list of (role, content)
    # tuples to the right shape.
    return [
        {"role": "user", "content": f"..."},
    ]
```

## Key gotchas (already paid the cost of discovering)

Also in `.claude/learnings.md` as one-liners.

1. **`FastMCP()` takes `instructions=`, not `description=` / `version=`.**
   The user's original spec assumed the latter; the actual mcp 1.27 SDK
   API uses `instructions`. Version is inferred from package metadata.
2. **`mcp.run(transport=...)` is synchronous.** Wraps the async run
   internally. Don't double-wrap with `asyncio.run(...)`.
3. **Windows + cp1252 crash on Klippbok subprocess**. The runner sets
   `PYTHONUTF8=1` + `PYTHONIOENCODING=utf-8` in the env for every
   subprocess — this was learned the hard way on the sibling Pinokio
   launcher project and is already applied here. Don't remove.
4. **Klippbok has TWO manifest schemas.** `triage_manifest.json`
   (clip-level, top-level `clips[]`) and `scene_triage_manifest.json`
   (scene-level, top-level `videos[]` with nested `scenes[]`). The
   schema detector in `manifest.py` handles both; new manifest tools
   must go through it, not re-parse.
5. **Klippbok is installed in a sibling repo's venv.** The natural
   default for this machine is
   `KLIPPBOK_PYTHON=f:/__PROJECTS/Klippbok/env/Scripts/python.exe`.
   README should document this for users who followed the Pinokio
   launcher path; users who installed Klippbok in their own venv
   should override appropriately.
6. **`docs/cli_help.txt` is gitignored.** It's regenerated on install.
   Source of truth for form fields, but never committed. When you
   regenerate it, diff against the last known version if something
   broke.
7. **Package metadata lives in `pyproject.toml`, not anywhere else.**
   Version here, console script here, dependencies here. Don't
   scatter.

## Related work (context from sibling projects)

- `f:/__PROJECTS/Klippbok/` — the **Klippbok Pinokio launcher** I
  built for the same user. A Gradio UI wrapping the same Klippbok CLI
  — different surface (GUI instead of MCP), same underlying patterns.
  The launcher's `app/manifest.py` was the reference for this repo's
  `src/klippbok_mcp/manifest.py`. Its `app/runner.py` was the
  reference for this repo's `src/klippbok_mcp/runner.py` (adapted
  sync→async; streaming dropped since MCP is request/response).
  Read its `docs/ARCHITECTURE.md` for background on the pipeline
  stages and manifest formats.
- `https://github.com/alvdansen/klippbok` — the **upstream CLI** we
  wrap. Read its README for the pipeline semantics; the CLI flag
  details are in our own `docs/cli_help.txt`.

## User preferences to honor

(captured in cross-session Claude memory at
`~/.claude/projects/f----PROJECTS-Klippbok/memory/`)

- Pinokio-first distribution where applicable (not relevant for MCP).
- **Staged delivery with pause for review** between big milestones.
- **Check whether a feature already exists before offering to add it.**
  Don't duplicate UI surfaces.
- **95% confidence before coding** — ask clarifying questions if
  requirements are ambiguous.
- Commit and push regularly to GitHub; user has gh CLI authenticated
  as `hoodtronik`.

## Tracking

- Upstream Klippbok: https://github.com/alvdansen/klippbok
- This repo: https://github.com/hoodtronik/klippbok-mcp (public, Apache 2.0)
- Sibling Pinokio launcher: https://github.com/hoodtronik/klippbok-pinokio (private)
