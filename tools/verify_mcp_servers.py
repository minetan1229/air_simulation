#!/usr/bin/env python3
"""Verify that every MCP server declared in ``.kilo/kilo.jsonc`` starts and answers.

The script speaks the MCP stdio protocol (newline delimited JSON-RPC 2.0)
directly, so it needs no third party dependency and works on a clean checkout.

Usage::

    python tools/verify_mcp_servers.py             # verify every enabled server
    python tools/verify_mcp_servers.py context7    # verify a single server
    python tools/verify_mcp_servers.py --list-only # only list the tool catalogue

Exit code 0 means every checked server completed the initialize / tools/list
handshake and the optional smoke call succeeded.
"""

from __future__ import annotations

import json
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / ".kilo" / "kilo.jsonc"
PROTOCOL_VERSION = "2024-11-05"
DEFAULT_TIMEOUT = 60.0

#: Smoke calls executed after ``tools/list`` succeeded. The first key that is
#: present in the advertised tool list is used.
SMOKE_CALLS: dict[str, list[tuple[str, dict[str, Any]]]] = {
    "context7": [
        ("resolve-library-id", {"libraryName": "numpy"}),
        ("query-docs", {"libraryName": "numpy", "query": "array creation"}),
    ],
    "sequential-thinking": [
        (
            "sequentialthinking",
            {
                "thought": "MCP self-enhancement verification: confirm the reasoning tool answers.",
                "nextThoughtNeeded": False,
                "thoughtNumber": 1,
            },
        )
    ],
    "memory": [
        ("search_nodes", {"query": "mcp-verification"}),
        ("read_graph", {}),
    ],
    "fetch": [
        ("fetch", {"url": "https://example.com/", "max_length": 300}),
    ],
    "playwright": [
        ("browser_navigate", {"url": "about:blank"}),
    ],
}


class MCPError(RuntimeError):
    """Raised when a server violates the MCP protocol or times out."""


def strip_jsonc(text: str) -> str:
    """Remove ``//`` and ``/* */`` comments plus trailing commas from JSONC."""
    out: list[str] = []
    i = 0
    in_string = False
    quote = ""
    while i < len(text):
        ch = text[i]
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < len(text):
                out.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                in_string = False
            i += 1
            continue
        if ch in "\"'":
            in_string = True
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < len(text) and text[i + 1] == "/":
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < len(text) and text[i + 1] == "*":
            i += 2
            while i + 1 < len(text) and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def load_servers() -> dict[str, dict[str, Any]]:
    """Return the MCP server table declared in the project Kilo configuration."""
    raw = CONFIG_PATH.read_text(encoding="utf-8")
    config = json.loads(strip_jsonc(raw))
    servers = config.get("mcp", {})
    if not isinstance(servers, dict):
        raise MCPError(f"'mcp' key in {CONFIG_PATH} is not an object")
    return servers


class MCPStdioClient:
    """Minimal MCP stdio client used for start-up verification."""

    def __init__(self, command: list[str], cwd: Path, timeout: float) -> None:
        self._timeout = timeout
        self._responses: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self._stderr: list[str] = []
        self._proc = subprocess.Popen(  # noqa: S603 - command comes from our own config
            command,
            cwd=str(cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self._id = 0
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()

    def _pump_stdout(self) -> None:
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self._responses.put(json.loads(line))
            except json.JSONDecodeError:
                continue

    def _pump_stderr(self) -> None:
        assert self._proc.stderr is not None
        for line in self._proc.stderr:
            self._stderr.append(line.rstrip())
            del self._stderr[:-40]

    @property
    def stderr_tail(self) -> str:
        return "\n".join(self._stderr[-15:])

    def request(self, method: str, params: Optional[dict[str, Any]] = None) -> Any:
        self._id += 1
        request_id = self._id
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        assert self._proc.stdin is not None
        self._proc.stdin.write(json.dumps(payload) + "\n")
        self._proc.stdin.flush()

        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            try:
                message = self._responses.get(timeout=deadline - time.monotonic())
            except queue.Empty:
                break
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise MCPError(f"{method} failed: {message['error']}")
            return message.get("result", {})
        raise MCPError(f"{method} timed out after {self._timeout:.0f}s")

    def notify(self, method: str, params: Optional[dict[str, Any]] = None) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        assert self._proc.stdin is not None
        self._proc.stdin.write(json.dumps(payload) + "\n")
        self._proc.stdin.flush()

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        return self.request("tools/call", {"name": name, "arguments": arguments})

    def close(self) -> None:
        try:
            if self._proc.stdin is not None:
                self._proc.stdin.close()
        except OSError:
            pass
        try:
            self._proc.terminate()
            self._proc.wait(timeout=5)
        except Exception:  # noqa: BLE001 - best effort teardown
            self._proc.kill()


def verify_server(name: str, spec: dict[str, Any], list_only: bool) -> tuple[bool, str]:
    """Start one MCP server and exercise the handshake. Returns (ok, detail)."""
    if spec.get("type", "local") != "local":
        return True, f"skipped (remote server type={spec.get('type')})"
    command = spec.get("command")
    if not isinstance(command, list) or not command:
        return False, "invalid configuration: 'command' must be a non-empty array"

    timeout = float(spec.get("timeout", DEFAULT_TIMEOUT)) / 1000.0
    client = MCPStdioClient([str(part) for part in command], PROJECT_ROOT, timeout)
    try:
        result = client.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "verify-mcp-servers", "version": "1.0.0"},
            },
        )
        server_info = result.get("serverInfo", {})
        client.notify("notifications/initialized")
        tools = client.request("tools/list").get("tools", [])
        tool_names = [t.get("name", "?") for t in tools]
        if not tool_names:
            return False, f"handshake ok but the server advertises no tool ({server_info})"

        if list_only:
            return True, f"{len(tool_names)} tools: {', '.join(tool_names)}"

        for tool_name, arguments in SMOKE_CALLS.get(name, []):
            if tool_name not in tool_names:
                continue
            client.call_tool(tool_name, arguments)
            return True, f"{len(tool_names)} tools, smoke call '{tool_name}' ok"
        return True, f"{len(tool_names)} tools (no smoke call defined): {', '.join(tool_names)}"
    except MCPError as exc:
        tail = client.stderr_tail
        return False, f"{exc}" + (f"\n      stderr: {tail}" if tail else "")
    except Exception as exc:  # noqa: BLE001 - report unexpected failures verbatim
        return False, f"unexpected error: {exc!r}"
    finally:
        client.close()


def main(argv: list[str]) -> int:
    list_only = "--list-only" in argv
    targets = [arg for arg in argv if not arg.startswith("--")]
    if not CONFIG_PATH.is_file():
        print(f"[FAIL] configuration not found: {CONFIG_PATH}")
        return 2
    try:
        servers = load_servers()
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] cannot parse {CONFIG_PATH}: {exc}")
        return 2

    if targets:
        unknown = [t for t in targets if t not in servers]
        if unknown:
            print(f"[FAIL] unknown server(s): {', '.join(unknown)}")
            return 2
        selected = {name: servers[name] for name in targets}
    else:
        selected = {name: spec for name, spec in servers.items() if spec.get("enabled", True)}

    if not selected:
        print("[FAIL] no enabled MCP server to verify")
        return 2

    print(f"Verifying {len(selected)} MCP server(s) from {CONFIG_PATH.relative_to(PROJECT_ROOT)}")
    failures = 0
    for name, spec in selected.items():
        ok, detail = verify_server(name, spec, list_only)
        status = "OK  " if ok else "FAIL"
        if not ok:
            failures += 1
        print(f"  [{status}] {name}: {detail}")

    if failures:
        print(f"RESULT: {failures} of {len(selected)} server(s) failed")
        return 1
    print(f"RESULT: all {len(selected)} server(s) passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
