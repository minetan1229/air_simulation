# Local MCP Server Runtime

Project-local runtime for the MCP servers registered in `../kilo.jsonc`.
All servers are **local stdio** processes and require **no external API key**.

| id | package | runtime | purpose |
|---|---|---|---|
| `context7` | `@upstash/context7-mcp` | node | latest library specifications / documentation |
| `sequential-thinking` | `@modelcontextprotocol/server-sequential-thinking` | node | structured reasoning for multi-step investigations |
| `fetch` | `mcp-server-fetch` (PyPI) | python venv | web page / paper retrieval as Markdown |
| `memory` | `@modelcontextprotocol/server-memory` | node | persistent cross-session knowledge graph |
| `playwright` | `@playwright/mcp` | node | real browser automation for the Three.js UI |

## Layout

```
.kilo/mcp/
  launch.mjs      entry point used by kilo.jsonc (resolves paths independent of cwd)
  package.json    npm manifest for the node servers
  node_modules/   installed node packages (git ignored)
  venv/           dedicated python venv for the fetch server (git ignored)
  memory.jsonl    knowledge graph written by the memory server (git ignored)
  playwright/     playwright traces / screenshots output (git ignored)
```

## Reinstall procedure

Run from the repository root:

```powershell
npm install --prefix .kilo/mcp --no-audit --no-fund
python -m venv .kilo/mcp/venv
.kilo\mcp\venv\Scripts\python.exe -m pip install -U pip mcp-server-fetch
```

## Verification

```powershell
python tools/verify_mcp_servers.py            # handshake + tool call for every server
python tools/verify_mcp_servers.py --list-only # tool catalogue only
kilo mcp list                                  # Kilo side connection status
```

## Notes

- The `fetch` server keeps its default hardening: private/loopback destinations are
  refused and `robots.txt` is respected. Do **not** pass `--allow-private-ips` or
  `--ignore-robots-txt`; that would weaken the project security baseline.
- `playwright` reuses the Chromium already provisioned under
  `%LOCALAPPDATA%\ms-playwright` (revision detected at start-up), so no second
  browser download is required.
