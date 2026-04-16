# klippbok-mcp

> Community MCP server for the [Klippbok](https://github.com/alvdansen/klippbok) video dataset curation CLI.

A [Model Context Protocol](https://modelcontextprotocol.io) server that
wraps every Klippbok pipeline stage — scan, triage, ingest, normalize,
caption, score, extract, audit, validate, organize — as typed MCP tools.
Plug it into **Claude Desktop**, **Claude Code**, **Cursor**, or any
MCP-compatible client, and an agent can drive the full dataset curation
pipeline for you.

> This is an **independent companion tool**. Klippbok itself is an
> [Alvdansen Labs](https://github.com/alvdansen/klippbok) project — install
> it separately; direct Klippbok issues and features to the
> [upstream repo](https://github.com/alvdansen/klippbok).

---

## Quick start (Claude Desktop)

1. **Install Klippbok** somewhere the server can reach. If you already
   use the [Klippbok Pinokio launcher](https://github.com/hoodtronik/klippbok-pinokio)
   its venv is fine — point `KLIPPBOK_PYTHON` at it.
2. **Install this server:**
   ```bash
   git clone https://github.com/hoodtronik/klippbok-mcp
   cd klippbok-mcp
   uv sync
   ```
3. **Register it with Claude Desktop** — add to
   `%APPDATA%\Claude\claude_desktop_config.json` (Windows) or
   `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS):
   ```json
   {
     "mcpServers": {
       "klippbok": {
         "command": "uv",
         "args": ["run", "--directory", "/absolute/path/to/klippbok-mcp", "klippbok-mcp"],
         "env": {
           "KLIPPBOK_PYTHON": "/absolute/path/to/your/klippbok/venv/python",
           "GEMINI_API_KEY": "your-gemini-key-here",
           "REPLICATE_API_TOKEN": "your-replicate-token-here"
         }
       }
     }
   }
   ```
4. **Restart Claude Desktop.** The Klippbok tools appear in the
   server picker. Ask it to `klippbok_check_installation` first to verify
   the environment.

---

## Tools

| Tool | Wraps | Notes |
|------|-------|-------|
| `klippbok_check_installation` | — | Probe klippbok / ffmpeg / Python. Run this first. |
| `klippbok_scan` | `klippbok.video scan` | Read-only quality diagnostic on a clips dir. |
| `klippbok_triage` | `klippbok.video triage` | CLIP matching; writes a triage manifest. |
| `klippbok_ingest` | `klippbok.video ingest` | Scene-split long videos. Consumes a triage manifest. |
| `klippbok_normalize` | `klippbok.video normalize` | Standardize fps / resolution on pre-split clips. |
| `klippbok_caption` | `klippbok.video caption` | VLM-generated `.txt` sidecars. Needs API key. |
| `klippbok_score` | `klippbok.video score` | Local heuristic caption quality check. |
| `klippbok_extract` | `klippbok.video extract` | Export PNG reference frames. |
| `klippbok_audit` | `klippbok.video audit` | Re-caption with VLM, diff. Needs API key. |
| `klippbok_validate` | `klippbok.dataset validate` | Dataset completeness / quality. |
| `klippbok_organize` | `klippbok.dataset organize` | Trainer-specific layout (aitoolkit / musubi / flat). |
| `klippbok_read_manifest` | — | Parse + summarize a triage manifest. Both schemas supported. |
| `klippbok_update_manifest` | — | Mutate include/exclude flags; save `*_reviewed.json`. |

Every pipeline tool returns a structured result
`{command, exit_code, stdout, stderr, duration_seconds, timed_out}`.
Non-zero exits come back as data — the server never raises on a Klippbok
command failure, so the agent can decide what to do next.

## Resources

| URI | Contents |
|-----|----------|
| `klippbok://pipelines` | Markdown guide mapping training goals (character / style / motion / object LoRA) to concrete pipeline recipes with tool-call sequences. |

## Prompts

| Name | Purpose |
|------|---------|
| `plan_dataset_pipeline` | Ask the model to plan a curation pipeline given a training goal + source material. Reads the `klippbok://pipelines` resource for canonical recipes. |
| `review_triage_results` | Ask the model to read a triage manifest, analyze the score distribution, flag suspicious entries, and propose `klippbok_update_manifest` calls for review. |

---

## Configuration

The server reads these environment variables:

| Variable | Purpose | Default |
|----------|---------|---------|
| `KLIPPBOK_PYTHON` | Path to the Python interpreter with Klippbok installed. Pointed at via its `sys.executable` so it runs `python -m klippbok ...`. | `sys.executable` (the server's own Python) |
| `GEMINI_API_KEY` | Required for `klippbok_caption` / `klippbok_audit` with `provider=gemini`. | — |
| `REPLICATE_API_TOKEN` | Required for `provider=replicate`. | — |

API keys are never written to disk by the server — they come in via the
MCP client's server config and get passed through to the subprocess env.

---

## Client configs

### Claude Desktop

Already shown in [Quick start](#quick-start-claude-desktop) above.

### Claude Code

```bash
claude mcp add klippbok \
  -- uv run --directory /absolute/path/to/klippbok-mcp klippbok-mcp

# Add env vars
claude mcp set klippbok KLIPPBOK_PYTHON=/path/to/klippbok/venv/python
claude mcp set klippbok GEMINI_API_KEY=...
claude mcp set klippbok REPLICATE_API_TOKEN=...
```

### Cursor

`~/.cursor/mcp.json` or project-local `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "klippbok": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/klippbok-mcp", "klippbok-mcp"],
      "env": {
        "KLIPPBOK_PYTHON": "/absolute/path/to/your/klippbok/venv/python",
        "GEMINI_API_KEY": "your-key-here"
      }
    }
  }
}
```

### MCP Inspector (for testing)

Run the server on HTTP:

```bash
cd /absolute/path/to/klippbok-mcp
KLIPPBOK_PYTHON=/path/to/klippbok/venv/python \
  uv run klippbok-mcp --transport http --port 8000
```

Then launch the Inspector (`npx @modelcontextprotocol/inspector`) and
connect to `http://localhost:8000/mcp`.

---

## Development

### Run the tests

```bash
uv run pytest
```

22 tests covering the subprocess wrapper and the manifest adapter (both
schemas + mutation precedence + lossless round-trip).

### Regenerate `docs/cli_help.txt`

This file is gitignored but authoritative for every tool's parameter
schema. Regenerate it against your Klippbok install when upgrading:

```bash
cd docs
KLIPPBOK_PYTHON=/path/to/klippbok/venv/python \
  python -c "
import os, subprocess, sys
os.environ['COLUMNS'] = '200'
subs = [('klippbok.video', c) for c in ('scan','triage','ingest','normalize','caption','score','extract','audit')]
subs += [('klippbok.dataset', c) for c in ('validate','organize')]
py = os.environ['KLIPPBOK_PYTHON']
with open('cli_help.txt', 'w', encoding='utf-8') as fh:
    for m, c in subs:
        fh.write(f'=== {m} {c} ===\n')
        r = subprocess.run([py, '-m', m, c, '--help'], capture_output=True, text=True)
        fh.write(r.stdout + '\n')
"
```

Diff against the last known good version to spot flag drift before it
breaks tool calls.

### Run the server in a loop with the Inspector

```bash
uv run klippbok-mcp --transport http --port 8000
```

Poke at individual tools from the Inspector's UI; try `klippbok_check_installation`
first since it's no-arg and diagnoses the rest.

---

## How the server is organized

```
src/klippbok_mcp/
  __init__.py      version marker
  server.py        FastMCP("klippbok"); all @mcp.tool / @mcp.prompt / @mcp.resource
  runner.py        async subprocess wrapper; forces PYTHONUTF8=1 on child env
                   (Windows cp1252 crash-avoidance learned from sibling project)
  manifest.py      schema-aware reader + writer for both triage manifest shapes
tests/             pytest suites for runner + manifest
docs/
  cli_help.txt     gitignored; regenerated by install.js equivalent
HANDOFF.md         dev handoff; delete when feature-complete
```

Key design invariant: **shell out only**. The server never imports
Klippbok internals. As Klippbok evolves, this server stays compatible as
long as the CLI flag surface stays compatible — and `docs/cli_help.txt`
will catch drift on reinstall.

---

## Relationship to Klippbok

Klippbok (https://github.com/alvdansen/klippbok) is the upstream toolkit
this server wraps. It is an independent project by Alvdansen Labs. This
MCP server:

- **Does not** bundle or modify Klippbok.
- **Does not** add features Klippbok doesn't expose (except the manifest
  read/update tools, which operate on files Klippbok produces — not
  Klippbok's internal state).
- **Is not** an official part of the Klippbok project.

File Klippbok CLI bugs upstream. File MCP-server-specific issues here.

---

## License

[Apache-2.0](LICENSE). Matches Klippbok's own license.
