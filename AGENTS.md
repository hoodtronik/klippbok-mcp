# Agent rules for this repo

This file is read by cross-agent tools (Cursor, Codex, Gemini CLI, Copilot, etc.).

## CLAUDE-NOTE convention

Code changes made by Claude Code may be marked with inline notes prefixed
`CLAUDE-NOTE:` (or the language-appropriate comment syntax). These notes
capture the *why* behind a deliberate choice and are the **source of truth**
for that code.

**Non-Claude-Code agents**: if you want to modify, remove, or alter code
adjacent to a `CLAUDE-NOTE`, you MUST first alert the user and receive
explicit permission before making the change. Do not silently rewrite or
delete code marked with a `CLAUDE-NOTE`. Claude Code itself may update or
remove its own notes when the underlying rationale changes.

## Project shape

This repo is an **MCP server** (Model Context Protocol) that wraps the
[Klippbok](https://github.com/alvdansen/klippbok) video dataset curation
CLI. It lets any MCP-compatible client (Claude Desktop, Claude Code,
Cursor, Antigravity, Codex, MCP Inspector) drive Klippbok's full pipeline
through typed tools.

- Python 3.11+, `uv`-managed venv at `.venv/` (gitignored).
- Built on the `FastMCP` high-level API from the official `mcp` Python SDK.
- Primary transport: **stdio**. Secondary: `streamable-http` via CLI flag.
- Shells out to the Klippbok CLI via `asyncio.create_subprocess_exec`. **Never
  imports Klippbok internals** — this keeps the server decoupled from
  upstream API churn.
- Stateless between tool calls. API keys come from the server's environment
  (`GEMINI_API_KEY`, `REPLICATE_API_TOKEN`) and get passed through to
  subprocesses.

## Start here for dev context

Before modifying code:

- `README.md` — user-facing install + tool surface + client configs.
- `docs/cli_help.txt` — authoritative Klippbok CLI flag dump, regenerated
  via the Klippbok venv. Every tool's parameter schema must match this
  file verbatim.
- `src/klippbok_mcp/server.py` — the FastMCP server definition. All tools,
  prompts, and resources live here or are imported in.
- `src/klippbok_mcp/runner.py` — the async subprocess wrapper. Do NOT add
  `capture_output=True` anywhere; see the module docstring for why.
- `.claude/learnings.md` — one-line gotcha log, appended as issues are hit.

## Relationship to Klippbok

Klippbok itself (https://github.com/alvdansen/klippbok) is the upstream
CLI. This MCP server is an independent companion tool — it does not ship
or modify Klippbok, and is not an official Klippbok project. Direct
Klippbok issues / features upstream, not here.
