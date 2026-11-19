#!/usr/bin/env python3
"""
Google AI Studio (Gemini API) Direct Query Tool

Google AI Studio APIキーを利用して、指定したモデル（既定: gemini-4-hydrogen または gemini-2.5-pro）
に直接質問・推論を行わせるユーティリティスクリプト。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from tools.notify_discord import load_env_file

load_env_file()

DEFAULT_MODEL = "gemini-4-hydrogen"
FALLBACK_MODELS = ["gemini-3.8-flash", "gemini-3.5-flash", "gemini-3.1-pro-preview"]


def query_gemini_studio(
    prompt: str,
    api_key: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    system_instruction: Optional[str] = None,
    temperature: float = 0.2,
    timeout: int = 45,
) -> str:
    key = api_key or os.getenv("GOOGLE_STUDIO_API_KEY") or os.getenv("GEMINI_API_KEY") or ""
    models_to_try = [model] + [m for m in FALLBACK_MODELS if m != model]

    last_error: Optional[Exception] = None

    for m in models_to_try:
        model_name = m.replace("models/", "")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={key}"

        payload: Dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ],
            "generationConfig": {
                "temperature": temperature,
            },
        }

        if system_instruction:
            payload["systemInstruction"] = {
                "parts": [{"text": system_instruction}]
            }

        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data_bytes,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                candidates = res_data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    texts = [p.get("text", "") for p in parts if "text" in p]
                    return "".join(texts)
                return "No candidate returned."
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8", errors="replace")
            last_error = RuntimeError(f"HTTPError {e.code} for model {model_name}: {err_msg}")
            # Try next model if 404, 429, 500, 502, 503, 504
            if e.code in (404, 429, 500, 502, 503, 504):
                continue
            raise last_error
        except Exception as e:
            last_error = e
            continue

    # Auto-fallback to Kilo Code worker if Studio API is exhausted or unavailable (Rule §1.6)
    try:
        from tools.kilo_subagent import execute_kilo_worker
        kilo_res = execute_kilo_worker(
            prompt=prompt,
            model="kilo/nvidia/nemotron-3-ultra-550b-a55b:free",
            timeout=timeout,
        )
        if kilo_res.get("success"):
            return f"[Fallback to Kilo Code (Nemotron 3 Ultra)]\n{kilo_res.get('output', '')}"
    except Exception:
        pass

    if last_error:
        raise last_error
    return "Failed to query Gemini Studio API."


def main() -> None:
    parser = argparse.ArgumentParser(description="Query Google AI Studio API")
    parser.add_argument("prompt", nargs="?", help="Prompt to send")
    parser.add_argument("--model", "-m", default=DEFAULT_MODEL, help="Model name")
    parser.add_argument("--system", "-s", default=None, help="System instruction")
    parser.add_argument("--key", "-k", default=None, help="API key")
    parser.add_argument("--file", "-f", help="Read prompt from file")
    args = parser.parse_args()

    prompt_text = args.prompt
    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            prompt_text = f.read()

    if not prompt_text:
        parser.error("Prompt must be provided as argument or via --file")

    result = query_gemini_studio(
        prompt=prompt_text,
        api_key=args.key,
        model=args.model,
        system_instruction=args.system,
    )
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(result)


if __name__ == "__main__":
    main()
