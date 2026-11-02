#!/usr/bin/env python3
"""Call a tool on one of the project's local MCP servers.

The MCP servers declared in ``.kilo/kilo.jsonc`` are local stdio processes that
speak newline-delimited JSON-RPC 2.0.  Kilo exposes them as first class tools
for interactive sessions, but the autonomous loop also has to drive them from
scripts (sequential-thinking derivations, context7 look-ups, memory writes and
the Playwright browser checks).  This helper is the scriptable counterpart of
``tools/verify_mcp_servers.py`` and shares its protocol implementation.

Usage::

    python tools/mcp_call.py sequential-thinking sequentialthinking --arg thought="..." --arg nextThoughtNeeded=false
    python tools/mcp_call.py context7 resolve-library-id --arg libraryName=three
    python tools/mcp_call.py memory read_graph --json '{"query": "origami"}'
    python tools/mcp_call.py playwright browser_navigate --arg url=http://127.0.0.1:5000/
    python tools/mcp_call.py --list context7

Exit code 0 means the tool call returned a result; the payload is printed as
JSON on stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.verify_mcp_servers import (  # noqa: E402
    MCPError,
    MCPStdioClient,
    load_servers,
    PROJECT_ROOT,
    PROTOCOL_VERSION,
)


def _coerce_scalar(text: str) -> Any:
    """Turn a command line scalar into a JSON value when it looks like one."""
    lowered = text.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    if lowered in ("null", "none"):
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def build_arguments(pairs: list[str], payload: str | None) -> dict[str, Any]:
    """Assemble the tool arguments from ``--arg`` pairs and an optional blob."""
    arguments: dict[str, Any] = {}
    if payload:
        decoded = json.loads(payload)
        if not isinstance(decoded, dict):
            raise MCPError("--json payload must be a JSON object")
        arguments.update(decoded)
    for pair in pairs:
        if "=" not in pair:
            raise MCPError(f"--arg expects key=value, got {pair!r}")
        key, raw = pair.split("=", 1)
        arguments[key] = _coerce_scalar(raw)
    return arguments


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("server", help="MCP server name from .kilo/kilo.jsonc")
    parser.add_argument("tool", nargs="?", help="tool name; omit with --list")
    parser.add_argument("--arg", action="append", default=[], help="key=value argument")
    parser.add_argument("--json", dest="payload", help="JSON object of arguments")
    parser.add_argument("--list", action="store_true", help="list the server tools")
    parser.add_argument("--timeout", type=float, default=120.0, help="timeout in seconds")
    args = parser.parse_args(argv)

    servers = load_servers()
    spec = servers.get(args.server)
    if spec is None:
        print(f"[FAIL] unknown MCP server {args.server!r}", file=sys.stderr)
        return 2
    command = spec.get("command")
    if not isinstance(command, list) or not command:
        print(f"[FAIL] invalid command for {args.server!r}", file=sys.stderr)
        return 2

    client = MCPStdioClient([str(part) for part in command], PROJECT_ROOT, args.timeout)
    try:
        client.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "mcp-call", "version": "1.0.0"},
            },
        )
        client.notify("notifications/initialized")
        tools = client.request("tools/list").get("tools", [])
        if args.list:
            print(json.dumps([t.get("name") for t in tools], ensure_ascii=False, indent=2))
            return 0
        if not args.tool:
            print("[FAIL] --list or a tool name is required", file=sys.stderr)
            return 2
        names = [t.get("name") for t in tools]
        if args.tool not in names:
            print(f"[FAIL] tool {args.tool!r} not in {names}", file=sys.stderr)
            return 2
        arguments = build_arguments(args.arg, args.payload)
        result = client.call_tool(args.tool, arguments)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except MCPError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        tail = client.stderr_tail
        if tail:
            print(tail, file=sys.stderr)
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
