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

DEFAULT_WORKER_MODEL = "kilo/nvidia/nemotron-3-ultra-550b-a55b:free"


def get_kilo_bin() -> str:
    exe = resolve_kilo_executable()
    if not exe:
        raise RuntimeError("Kilo Code executable not found on host.")
    return exe


def execute_kilo_worker(
    prompt: str,
    model: str = DEFAULT_WORKER_MODEL,
    session_id: Optional[str] = None,
    continue_session: bool = False,
    auto_approve: bool = True,
    output_file: Optional[Path] = None,
    timeout: Optional[int] = None,
    verbose: bool = False,
) -> Dict[str, Any]:
    """
    Run Kilo Code as a worker and stream/collect its output.
    """
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

    if verbose:
        print(f"[*] Starting Kilo Worker with model: {model}", file=sys.stderr)
        print(f"[*] Command: {' '.join(cmd[:5])} ...", file=sys.stderr)

    start_time = time.time()
    proc = subprocess.Popen(
        cmd,
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
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
        }

    full_output = "".join(collected_text).strip()
    duration = time.time() - start_time
    stderr_content = proc.stderr.read() if proc.stderr else ""

    if proc.returncode != 0 and not error_message:
        error_message = stderr_content.strip() or f"Process exited with code {proc.returncode}"

    success = proc.returncode == 0 and not error_message

    if success and output_file:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(full_output, encoding="utf-8")
        if verbose:
            print(f"\n[✓] Saved output to {output_file}", file=sys.stderr)

    return {
        "success": success,
        "exit_code": proc.returncode,
        "error": error_message,
        "session_id": recorded_session_id,
        "text": full_output,
        "stderr": stderr_content,
        "duration": duration,
        "events_count": len(events),
    }


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

    args = parser.parse_args()

    if args.check:
        exe = resolve_kilo_executable()
        print(f"Kilo Executable: {exe}")
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
