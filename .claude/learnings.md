# Project learnings

Append one-line notes about non-obvious gotchas hit while building this repo.
Keep each entry under 15 words. Prune stale entries as the code evolves.

- 2026-04-16 — FastMCP() takes `instructions=`, not `description=`/`version=`; version is package metadata
- 2026-04-16 — mcp 1.27.0 installed (spec asked for >=1.20); `run_stdio_async` + `run_streamable_http_async` for transport
