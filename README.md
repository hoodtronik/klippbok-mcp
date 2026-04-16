# klippbok-mcp

> MCP server for the [Klippbok](https://github.com/alvdansen/klippbok) video dataset curation CLI.

A community [Model Context Protocol](https://modelcontextprotocol.io) server
that wraps every Klippbok pipeline stage — scan, triage, ingest, normalize,
caption, score, extract, audit, validate, organize — as typed MCP tools.
Plugs into Claude Desktop, Claude Code, Cursor, Antigravity, or any
MCP-compatible client so an agent can orchestrate the full dataset curation
pipeline on your behalf.

> **This is an independent companion tool.** Klippbok itself is an
> [Alvdansen Labs](https://github.com/alvdansen/klippbok) project — install
> it separately; direct Klippbok issues and features to the upstream repo.

_Build in progress — see [ROADMAP](#roadmap) for what lands when._

## Install

Build details come in the next commit. Placeholder:

```bash
uv tool install klippbok-mcp
# or for dev:
git clone https://github.com/hoodtronik/klippbok-mcp
cd klippbok-mcp
uv sync
```

## Roadmap

- [x] Repo bootstrap
- [ ] `runner.py` async subprocess wrapper + tests
- [ ] `manifest.py` read/update for both triage manifest schemas
- [ ] Minimal 2-tool server (`klippbok_check_installation`, `klippbok_scan`) → verify via MCP Inspector
- [ ] Remaining 8 CLI wrapper tools
- [ ] Manifest tools + pipeline resource + prompts
- [ ] Full README with client configs (Claude Desktop / Code / Cursor / MCP Inspector)

## License

Apache-2.0. See [LICENSE](LICENSE).
