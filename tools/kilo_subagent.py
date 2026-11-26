#!/usr/bin/env python3
"""
Kilo Code Subagent CLI Dispatcher & Worker Tool

Antigravity や他の主エージェントから Kilo Code CLI をサブエージェント（ワーカー）
として呼び出し、非同期または同期でタスクを実行・管理するための統合ツール。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.bridge_backends import (
    KILO_DEFAULT_MODEL,
    KILO_MODELS,
    StreamEvent,
    detect_backend,
    normalize_model,
    resolve_kilo_executable,
)
from tools.bridge_router import is_rate_limited
from tools.proxy_config import ProxyRotator, format_proxy_summary, inject_proxy_to_env

DEFAULT_WORKER_MODEL = "kilo/nvidia/nemotron-3-ultra-550b-a55b:free"


def get_kilo_bin() -> str:
    exe = resolve_kilo_executable()
    if not exe:
        raise RuntimeError("Kilo Code executable not found on host.")
    return exe


def _execute_kilo_attempt(
    prompt: str,
    model: str,
    session_id: Optional[str],
    continue_session: bool,
    auto_approve: bool,
    timeout: Optional[int],
    verbose: bool,
    proxy: Optional[str],
) -> Dict[str, Any]:
    """Execute a single attempt of Kilo Code worker."""
    exe = get_kilo_bin()
    cmd = [
        exe,
        "run",
        "--model",
        normalize_model(model, "kilo"),
        "--format",
        "json",
    ]
    if auto_approve:
        cmd.append("--auto")

    if session_id:
        cmd.extend(["--session", session_id])
    elif continue_session:
        cmd.append("--continue")

    cmd.extend(["--", prompt])

    env = inject_proxy_to_env(explicit_proxy=proxy)
    env["PYTHONIOENCODING"] = "utf-8"

    if verbose:
        print(f"[*] Starting Kilo Worker with model: {model}", file=sys.stderr)
        print(f"[*] Proxy config: {format_proxy_summary(explicit_proxy=proxy)}", file=sys.stderr)
        print(f"[*] Command: {' '.join(cmd[:5])} ...", file=sys.stderr)

    start_time = time.time()
    proc = subprocess.Popen(
        cmd,
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    collected_text: List[str] = []
    events: List[Dict[str, Any]] = []
    recorded_session_id = session_id
    error_message = None

    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line_str = line.strip()
            if not line_str:
                continue
            try:
                data = json.loads(line_str)
                events.append(data)

                # Check session id
                sid = data.get("sessionID") or (data.get("part") or {}).get("sessionID")
                if sid:
                    recorded_session_id = sid

                event_type = str(data.get("type") or "")
                part = data.get("part") or {}
                part_type = str(part.get("type") or "")

                if event_type == "text" and "text" in part:
                    text_chunk = part["text"]
                    collected_text.append(text_chunk)
                    if verbose:
                        print(text_chunk, end="", flush=True)

                if "error" in event_type or "error" in part_type:
                    err = part.get("error") or data.get("error") or part.get("message")
                    error_message = str(err)
                    if verbose:
                        print(f"\n[!] Kilo Error event: {error_message}", file=sys.stderr)

            except json.JSONDecodeError:
                if verbose:
                    print(f"\n[raw] {line_str}", file=sys.stderr)
                collected_text.append(line_str + "\n")

        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        return {
            "success": False,
            "error": f"Timeout expired ({timeout}s)",
            "session_id": recorded_session_id,
            "text": "".join(collected_text),
            "events_count": len(events),
            "duration": time.time() - start_time,
            "rate_limited": False,
        }
    except Exception as ex:
        proc.kill()
        return {
            "success": False,
            "error": str(ex),
            "session_id": recorded_session_id,
            "text": "".join(collected_text),
            "events_count": len(events),
            "duration": time.time() - start_time,
            "rate_limited": False,
        }

    full_output = "".join(collected_text).strip()
    duration = time.time() - start_time
    stderr_content = proc.stderr.read() if proc.stderr else ""

    if proc.returncode != 0 and not error_message:
        error_message = stderr_content.strip() or f"Process exited with code {proc.returncode}"

    is_rl = is_rate_limited(full_output + "\n" + (error_message or "") + "\n" + stderr_content)
    success = proc.returncode == 0 and not error_message and not is_rl

    return {
        "success": success,
        "exit_code": proc.returncode,
        "error": error_message,
        "session_id": recorded_session_id,
        "text": full_output,
        "stderr": stderr_content,
        "duration": duration,
        "events_count": len(events),
        "rate_limited": is_rl,
    }


def execute_kilo_worker(
    prompt: str,
    model: str = DEFAULT_WORKER_MODEL,
    session_id: Optional[str] = None,
    continue_session: bool = False,
    auto_approve: bool = True,
    output_file: Optional[Path] = None,
    timeout: Optional[int] = None,
    verbose: bool = False,
    proxy: Optional[str] = None,
    max_proxy_rotations: int = 3,
) -> Dict[str, Any]:
    """Run Kilo Code as a worker, rotating proxy if a 429 rate limit is encountered."""
    rotator = ProxyRotator.from_config(explicit_proxy=proxy)
    current_proxy = rotator.current or proxy
    attempts = 0
    max_attempts = max(1, min(max_proxy_rotations + 1, len(rotator) if len(rotator) > 1 else max_proxy_rotations + 1))

    last_res: Dict[str, Any] = {}
    while attempts < max_attempts:
        attempts += 1
        use_continue = continue_session if attempts == 1 else True
        if session_id:
            use_continue = False

        res = _execute_kilo_attempt(
            prompt=prompt,
            model=model,
            session_id=session_id,
            continue_session=use_continue,
            auto_approve=auto_approve,
            timeout=timeout,
            verbose=verbose,
            proxy=current_proxy,
        )
        last_res = res
        if res.get("session_id"):
            session_id = res["session_id"]

        if res.get("success") or not res.get("rate_limited"):
            break

        # 429 encountered: attempt proxy rotation if available
        next_proxy = rotator.rotate()
        if verbose:
            print(
                f"\n[!] 429 Rate limit encountered on proxy: {current_proxy or 'direct'}. "
                f"Rotating to next proxy: {next_proxy or 'none'} (attempt {attempts}/{max_attempts})",
                file=sys.stderr,
            )
        current_proxy = next_proxy

    if last_res.get("success") and output_file:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(last_res.get("text", ""), encoding="utf-8")
        if verbose:
            print(f"\n[✓] Saved output to {output_file}", file=sys.stderr)

    return last_res


def main():
    parser = argparse.ArgumentParser(description="Kilo Code Subagent Worker CLI Tool")
    parser.add_argument("prompt", nargs="?", help="Task prompt for Kilo Code")
    parser.add_argument("-p", "--prompt-file", type=Path, help="Path to prompt file")
    parser.add_argument("-m", "--model", default=DEFAULT_WORKER_MODEL, help="Model identifier")
    parser.add_argument("-o", "--output", type=Path, help="Output destination file")
    parser.add_argument("-s", "--session", help="Session ID to continue")
    parser.add_argument("-c", "--continue", dest="continue_session", action="store_true", help="Continue last session")
    parser.add_argument("--json", action="store_true", help="Output summary in JSON format")
    parser.add_argument("-v", "--verbose", action="store_true", help="Print streaming text to stdout/stderr")
    parser.add_argument("--list-models", action="store_true", help="List registered Kilo free models")
    parser.add_argument("--check", action="store_true", help="Check Kilo Code CLI availability and path")
    parser.add_argument("--proxy", help="HTTP/HTTPS/SOCKS proxy URL to route Kilo Code traffic through")

    args = parser.parse_args()

    if args.check:
        exe = resolve_kilo_executable()
        print(f"Kilo Executable: {exe}")
        print(f"Proxy Config: {format_proxy_summary(explicit_proxy=args.proxy)}")
        if exe and Path(exe).exists():
            print("Status: Available")
            sys.exit(0)
        else:
            print("Status: NOT FOUND")
            sys.exit(1)

    if args.list_models:
        print("Available Free Kilo Code Models:")
        for m_id, name in KILO_MODELS.items():
            print(f"  - {m_id} ({name})")
        sys.exit(0)

    prompt = args.prompt
    if args.prompt_file:
        if not args.prompt_file.exists():
            print(f"Error: Prompt file not found: {args.prompt_file}", file=sys.stderr)
            sys.exit(1)
        prompt = args.prompt_file.read_text(encoding="utf-8")

    if not prompt:
        parser.print_help()
        sys.exit(1)

    result = execute_kilo_worker(
        prompt=prompt,
        model=args.model,
        session_id=args.session,
        continue_session=args.continue_session,
        output_file=args.output,
        verbose=args.verbose,
        proxy=args.proxy,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if not args.verbose and result["text"]:
            print(result["text"])
        if not result["success"]:
            print(f"Error: {result['error']}", file=sys.stderr)
            sys.exit(result.get("exit_code") or 1)


if __name__ == "__main__":
    main()
