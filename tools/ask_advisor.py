"""Consult the Tier-3 Principal Advisor (Opus 4.6 or Gemini 4 Hydrogen XHigh).

Runs Antigravity CLI (`agy -p ... -m <model>`) in read-only advisory mode,
coordinates with `tools/rate_limit_guard.py` on 429/quota errors, automatically
falls back between Opus 4.6 and Hydrogen XHigh, and persists Q&A logs under
`.agents/consultations/` for closed-loop knowledge distillation.

Model IDs can be overridden via environment variables if local `agy` CLI naming
changes:
  - `AGY_MODEL_OPUS46` (default: `claude-opus-4-6-thinking`)
  - `AGY_MODEL_HYDROGEN_XHIGH` (default: `gemini-4-hydrogen-xhigh`)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.rate_limit_guard import clear_rate_limit, record_rate_limit, remaining_cooldown

WORKSPACE_DIR = Path(__file__).resolve().parent.parent
CONSULTATIONS_DIR = WORKSPACE_DIR / ".agents" / "consultations"


def get_model_aliases() -> Dict[str, str]:
    opus_id = os.getenv("AGY_MODEL_OPUS46", "claude-opus-4-6-thinking").strip() or "claude-opus-4-6-thinking"
    xhigh_id = os.getenv("AGY_MODEL_HYDROGEN_XHIGH", "gemini-4-hydrogen-xhigh").strip() or "gemini-4-hydrogen-xhigh"
    return {
        "opus4.6": opus_id,
        "opus": opus_id,
        "claude-opus-4-6-thinking": opus_id,
        "hydrogen-xhigh": xhigh_id,
        "xhigh": xhigh_id,
        "gemini-4-hydrogen-xhigh": xhigh_id,
    }


def get_fallback_pair() -> Dict[str, str]:
    aliases = get_model_aliases()
    opus_id = aliases["opus4.6"]
    xhigh_id = aliases["hydrogen-xhigh"]
    return {opus_id: xhigh_id, xhigh_id: opus_id}


def resolve_agy_cli() -> Optional[str]:
    explicit = os.getenv("AGY_CLI_PATH", "").strip()
    if explicit and Path(explicit).exists():
        return explicit
    return shutil.which("agy")


def slugify(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", text.strip().lower()).strip("-")
    return cleaned[:48] or "consultation"


def _looks_rate_limited(text: str) -> bool:
    low = (text or "").lower()
    return any(tok in low for tok in ("429", "resource_exhausted", "too many requests", "quota exceeded"))


def build_advisory_prompt(
    topic: str,
    context: str,
    question: str,
    attempts: str,
    files: List[str],
) -> str:
    file_block = "\n".join(f"- {f}" for f in files) if files else "- (なし)"
    return (
        "あなたは air_simulation プロジェクトの技術顧問（Principal Advisor / 読み取り専用）です。\n"
        "ファイル編集や破壊的コマンドは一切行わず、対症療法ペナルティではなく根本の物理式・幾何制約・設計の修正方針と反証可能な合否基準のみを日本語で簡潔に回答してください。\n\n"
        f"## 相談トピック\n{topic}\n\n"
        f"## 前提・対象箇所\n{context or '(未指定)'}\n\n"
        f"## 参照すべきファイル\n{file_block}\n\n"
        f"## 既に試したこと・失敗結果\n{attempts or '(初回相談)'}\n\n"
        f"## 質問（判断してほしいこと）\n{question}\n\n"
        "## 必須回答フォーマット\n"
        "1. **結論（Decision）**: 採用すべき根本方針（1〜3行）\n"
        "2. **根本原因・根拠（Root Cause & Rationale）**: ファイル/数式/仕様に基づく理由\n"
        "3. **実装手順（Actionable Plan）**: Hydrogen Low が迷わず実装できる具体的手順\n"
        "4. **合否基準 & 悪役ベンチマーク条件（Acceptance Criteria）**: 検証すべき数値条件・保存則・登録すべき最小失敗テスト\n"
        "5. **Knowledge 蒸留案（1〜2行）**: `.agents/knowledge.d/` へ記録すべき要約\n"
    )


def invoke_agy(agy_path: str, model_id: str, prompt: str, timeout: int) -> Tuple[bool, str, str]:
    cmd = [agy_path, "-p", prompt, "-m", model_id]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(WORKSPACE_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "", f"Timeout after {timeout}s for model {model_id}"
    except OSError as exc:
        return False, "", f"OS error invoking {agy_path}: {exc}"

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    if proc.returncode == 0 and stdout:
        return True, stdout, stderr
    return False, stdout, stderr or f"Non-zero exit code: {proc.returncode}"


def save_consultation_log(
    topic: str,
    requested_model: str,
    used_model: str,
    prompt: str,
    answer: str,
    consultations_dir: Optional[Path] = None,
) -> Path:
    cdir = consultations_dir or CONSULTATIONS_DIR
    cdir.mkdir(parents=True, exist_ok=True)
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = cdir / f"{ts}_{slugify(topic)}.md"
    content = (
        f"# Advisor Consultation: {topic}\n\n"
        f"- **Timestamp**: {dt.datetime.now().isoformat(timespec='seconds')}\n"
        f"- **Requested Model**: `{requested_model}`\n"
        f"- **Answered By**: `{used_model}`\n\n"
        f"## Question Payload\n\n{prompt}\n\n"
        f"## Advisor Response\n\n{answer}\n"
    )
    log_path.write_text(content, encoding="utf-8")
    return log_path


def consult_advisor(
    model: str,
    topic: str,
    question: str,
    context: str = "",
    attempts: str = "",
    files: Optional[List[str]] = None,
    fallback: bool = True,
    timeout: int = 180,
    rate_limit_path: Optional[Path] = None,
    consultations_dir: Optional[Path] = None,
) -> Dict[str, object]:
    aliases = get_model_aliases()
    fallback_pair = get_fallback_pair()
    primary_model = aliases.get(model.lower(), model)
    prompt = build_advisory_prompt(topic, context, question, attempts, files or [])

    agy_path = resolve_agy_cli()
    if not agy_path:
        return {"success": False, "error": "Antigravity CLI (`agy`) not found in PATH or AGY_CLI_PATH."}

    candidates = [primary_model]
    if fallback and primary_model in fallback_pair:
        candidates.append(fallback_pair[primary_model])

    # If primary is currently in shared cooldown and fallback is clear, try fallback first
    if len(candidates) == 2:
        cd0 = remaining_cooldown(candidates[0], state_path=rate_limit_path)
        cd1 = remaining_cooldown(candidates[1], state_path=rate_limit_path)
        if cd0 > 0.0 and cd1 == 0.0:
            candidates = [candidates[1], candidates[0]]

    errors: List[str] = []
    for candidate in candidates:
        ok, answer, err = invoke_agy(agy_path, candidate, prompt, timeout)
        if ok:
            clear_rate_limit(candidate, state_path=rate_limit_path)
            log_path = save_consultation_log(
                topic,
                primary_model,
                candidate,
                prompt,
                answer,
                consultations_dir=consultations_dir,
            )
            try:
                rel_log = log_path.relative_to(WORKSPACE_DIR).as_posix()
            except ValueError:
                rel_log = str(log_path)
            return {"success": True, "model": candidate, "log_path": rel_log, "answer": answer}
        if _looks_rate_limited(err) or _looks_rate_limited(answer):
            record_rate_limit(candidate, state_path=rate_limit_path)
        errors.append(f"{candidate}: {err}")

    return {"success": False, "error": " | ".join(errors)}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Consult Tier-3 Principal Advisor (Opus 4.6 / Hydrogen XHigh).")
    parser.add_argument("--model", default="opus4.6", help="opus4.6 | hydrogen-xhigh | exact CLI model id")
    parser.add_argument("--topic", required=True)
    parser.add_argument("--question", required=True)
    parser.add_argument("--context", default="")
    parser.add_argument("--attempts", default="")
    parser.add_argument("--file", action="append", default=[])
    parser.add_argument("--no-fallback", action="store_true")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    aliases = get_model_aliases()
    primary_model = aliases.get(args.model.lower(), args.model)
    prompt = build_advisory_prompt(args.topic, args.context, args.question, args.attempts, args.file)

    if args.dry_run:
        if args.json:
            print(json.dumps({"dry_run": True, "primary_model": primary_model, "prompt": prompt}, ensure_ascii=False))
        else:
            print(f"[dry-run] primary_model={primary_model}\n\n{prompt}")
        return 0

    res = consult_advisor(
        model=args.model,
        topic=args.topic,
        question=args.question,
        context=args.context,
        attempts=args.attempts,
        files=args.file,
        fallback=not args.no_fallback,
        timeout=args.timeout,
    )
    if args.json:
        print(json.dumps(res, ensure_ascii=False))
    elif res.get("success"):
        print(f"[ask_advisor] Answered by `{res['model']}` (saved: {res['log_path']})\n")
        print(res["answer"])
    else:
        print(f"[ask_advisor] FAILED: {res.get('error')}", file=sys.stderr)
    return 0 if res.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
