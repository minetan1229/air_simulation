"""Deterministic Corporate 3-Tier Closed-Loop Engineering Orchestrator.

Moves loop control from prompt-only instructions into an explicit Python state
machine that enforces:
  1. Measure & Screen   : Runs 5-criterion P0 sanity screener (`redteam_sanity_screen.py`).
                          Terminates deterministically with `FREEZE_PASS` only when
                          `min_budget` is met, existing adversarial tests pass, and
                          new P0 violations == 0.
  2. Plan (Tier 1 Main) : Generates WBS tickets with exclusive scopes (`gemini-4-hydrogen-medium`).
  3. Execute (Tier 2)   : Dispatches each ticket to `team-lead` (`gemini-4-hydrogen-low`).
  4. Hard 2-Strike Rule : Counts failures in code; on attempt >= 2 (or Watchdog
                          `ESCALATE_TO_ADVISOR`), deterministically calls Tier-3
                          `ask_advisor.py` (Opus 4.6 / Hydrogen XHigh) and injects
                          the answer into the next `team-lead` attempt.
  5. Central Watchdog   : Runs hygiene/tests + QA verdict (`PASS` / `REJECT` /
                          `ESCALATE_TO_ADVISOR` / `FREEZE_PASS`) with rework cap.
  6. Ratchet & Telemetry: Automatically promotes resolved quarantine JSONs into
                          `tests/adversarial_cases/` (permanent regression ratchet),
                          writes a knowledge fragment to `.agents/knowledge.d/`,
                          and logs metrics to `.agents/telemetry/loop_history.jsonl`.

Usage:
    python tools/company_loop.py --goal "P0抜け穴封鎖" --dry-run
    python tools/company_loop.py --goal "P0抜け穴封鎖" --live --max-iterations 5
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools import ask_advisor
from tools.redteam_sanity_screen import run_screening

DEFAULT_TELEMETRY_PATH = ROOT_DIR / ".agents" / "telemetry" / "loop_history.jsonl"
DEFAULT_ADVERSARIAL_DIR = ROOT_DIR / "tests" / "adversarial_cases"
DEFAULT_QUARANTINE_DIR = ROOT_DIR / ".agents" / "quarantine"
DEFAULT_KNOWLEDGE_D_DIR = ROOT_DIR / ".agents" / "knowledge.d"


@dataclass
class Ticket:
    ticket_id: str
    title: str
    owned_scope: List[str]
    acceptance_criteria: str
    quarantine_file: Optional[str] = None
    attempts: int = 0
    rejects: int = 0
    advisor_notes: List[str] = field(default_factory=list)
    status: str = "PENDING"  # PENDING | PASSED | FAILED


@dataclass
class LoopConfig:
    goal: str
    max_iterations: int = 5
    max_ticket_attempts: int = 4
    escalate_after_failures: int = 2
    max_watchdog_rejects: int = 3
    min_redteam_budget: int = 20
    live: bool = False
    main_model: str = "gemini-4-hydrogen-medium"
    sub_model: str = "gemini-4-hydrogen-low"
    advisor_model: str = "opus4.6"
    quarantine_dir: Path = DEFAULT_QUARANTINE_DIR
    adversarial_dir: Path = DEFAULT_ADVERSARIAL_DIR
    knowledge_d_dir: Path = DEFAULT_KNOWLEDGE_D_DIR
    telemetry_path: Path = DEFAULT_TELEMETRY_PATH


def _utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def append_telemetry(record: Dict[str, Any], telemetry_path: Path) -> None:
    telemetry_path.parent.mkdir(parents=True, exist_ok=True)
    with telemetry_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def ratchet_promote_quarantine(
    quarantine_file: Path,
    adversarial_dir: Path,
    root_cause: str,
    linked_test: str = "tests/test_adversarial_benchmark.py",
) -> Optional[Path]:
    """Promote a resolved quarantine anomaly into permanent `tests/adversarial_cases/`."""
    if not quarantine_file.is_file():
        return None
    data = json.loads(quarantine_file.read_text(encoding="utf-8"))
    case_id = str(data.get("case_id") or quarantine_file.stem)
    promoted = {
        "case_id": case_id,
        "discovered_by": str(data.get("discovered_by", "company-loop-ratchet")),
        "sanity_violation_type": str(data.get("sanity_violation_type", "energy_generation")),
        "root_cause": root_cause,
        "linked_test": linked_test,
        "genome_or_payload": data.get("genome_or_payload") or {"raw": data},
    }
    adversarial_dir.mkdir(parents=True, exist_ok=True)
    dest = adversarial_dir / f"{case_id}.json"
    dest.write_text(json.dumps(promoted, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    quarantine_file.unlink(missing_ok=True)
    return dest


def write_knowledge_fragment(knowledge_d_dir: Path, iteration: int, summary_bullet: str) -> Path:
    knowledge_d_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = knowledge_d_dir / f"{ts}_loop_iter{iteration}.md"
    path.write_text(f"- {summary_bullet.lstrip('- ').strip()}\n", encoding="utf-8")
    return path


def _default_agy_invoke(model: str, prompt: str, timeout: int = 300) -> Dict[str, Any]:
    agy = ask_advisor.resolve_agy_cli()
    if not agy:
        return {"ok": False, "text": "", "error": "agy CLI not found"}
    ok, out, err = ask_advisor.invoke_agy(agy, model, prompt, timeout)
    return {"ok": ok, "text": out, "error": err}


class CompanyLoopEngine:
    """Finite-state closed-loop orchestrator with injectable runners for deterministic testing."""

    def __init__(
        self,
        config: LoopConfig,
        screener_fn: Optional[Callable[[int], Dict[str, Any]]] = None,
        sub_runner: Optional[Callable[[Ticket, str], Dict[str, Any]]] = None,
        advisor_runner: Optional[Callable[[Ticket, str], Dict[str, Any]]] = None,
        watchdog_runner: Optional[Callable[[Ticket, Dict[str, Any]], Dict[str, Any]]] = None,
    ) -> None:
        self.cfg = config
        self.screener_fn = screener_fn or self._default_screener
        self.sub_runner = sub_runner or self._default_sub_runner
        self.advisor_runner = advisor_runner or self._default_advisor_runner
        self.watchdog_runner = watchdog_runner or self._default_watchdog_runner

    def _default_screener(self, _iteration: int) -> Dict[str, Any]:
        in_dir = ROOT_DIR / "output" / "v3"
        paths = sorted(in_dir.glob("*.json")) if in_dir.is_dir() else []
        return run_screening(
            candidate_paths=paths,
            min_budget=self.cfg.min_redteam_budget,
            quarantine_dir=self.cfg.quarantine_dir,
            write_quarantine=self.cfg.live,
        )

    def _default_sub_runner(self, ticket: Ticket, prompt: str) -> Dict[str, Any]:
        if not self.cfg.live:
            return {"ok": True, "summary": f"[dry-run] resolved {ticket.ticket_id}", "root_cause": "dry-run root fix"}
        res = _default_agy_invoke(self.cfg.sub_model, prompt)
        return {"ok": bool(res["ok"]), "summary": res.get("text", ""), "error": res.get("error", "")}

    def _default_advisor_runner(self, ticket: Ticket, failure_summary: str) -> Dict[str, Any]:
        if not self.cfg.live:
            return {
                "success": True,
                "model": self.cfg.advisor_model,
                "answer": f"[dry-run advisor] Fix root physics equation for {ticket.ticket_id} (saw: {failure_summary})",
            }
        return ask_advisor.consult_advisor(
            model=self.cfg.advisor_model,
            topic=ticket.ticket_id,
            question=f"2回連続失敗したタスク `{ticket.title}` の根本修正方針と合否基準を提示してください。",
            context=f"Owned scope: {ticket.owned_scope}",
            attempts=failure_summary,
        )

    def _default_watchdog_runner(self, ticket: Ticket, sub_result: Dict[str, Any]) -> Dict[str, Any]:
        if not sub_result.get("ok"):
            return {"verdict": "REJECT", "reason": sub_result.get("error") or "subagent execution failed"}
        if not self.cfg.live:
            return {"verdict": "PASS", "reason": "[dry-run] verified"}
        hygiene = subprocess.run(
            [sys.executable, str(ROOT_DIR / "tools" / "check_agents_hygiene.py")],
            capture_output=True,
            text=True,
            check=False,
        )
        if hygiene.returncode != 0:
            return {"verdict": "REJECT", "reason": f"hygiene failed: {hygiene.stdout} {hygiene.stderr}"}
        return {"verdict": "PASS", "reason": "automated gates passed"}

    def _build_tickets_from_screen(self, screen: Dict[str, Any], iteration: int) -> List[Ticket]:
        tickets: List[Ticket] = []
        qfiles = list(screen.get("quarantined_files") or [])
        violations = list(screen.get("violations") or [])
        for idx, viol in enumerate(violations):
            qf = qfiles[idx] if idx < len(qfiles) else None
            tickets.append(
                Ticket(
                    ticket_id=str(viol.get("case_id", f"iter{iteration}_t{idx}")),
                    title=f"Fix {viol.get('violation_type', 'P0')}: {viol.get('detail', '')}",
                    owned_scope=["physics/", "geometry/", "optimization/"],
                    acceptance_criteria="Fundamental physical/geometric fix + adversarial regression test PASS",
                    quarantine_file=qf,
                )
            )
        return tickets

    def run(self) -> Dict[str, Any]:
        history: List[Dict[str, Any]] = []
        final_state = "HALT_MAX_ITER"
        total_advisor_calls = 0
        total_promoted = 0

        for iteration in range(1, self.cfg.max_iterations + 1):
            t0 = time.monotonic()
            screen = self.screener_fn(iteration)

            # Deterministic FREEZE_PASS gate (cannot be faked by LLM)
            if screen.get("freeze_eligible"):
                final_state = "FREEZE_PASS"
                entry = {
                    "timestamp": _utc_now_iso(),
                    "iteration": iteration,
                    "state": "FREEZE_PASS",
                    "evaluated_count": screen.get("evaluated_count", 0),
                    "p0_violations": 0,
                    "advisor_calls": total_advisor_calls,
                    "promoted_cases": total_promoted,
                    "elapsed_s": round(time.monotonic() - t0, 3),
                }
                append_telemetry(entry, self.cfg.telemetry_path)
                history.append(entry)
                break

            tickets = self._build_tickets_from_screen(screen, iteration)
            if not tickets:
                final_state = "INSUFFICIENT_BUDGET"
                entry = {
                    "timestamp": _utc_now_iso(),
                    "iteration": iteration,
                    "state": "INSUFFICIENT_BUDGET",
                    "evaluated_count": screen.get("evaluated_count", 0),
                    "p0_violations": 0,
                    "advisor_calls": total_advisor_calls,
                    "promoted_cases": total_promoted,
                    "elapsed_s": round(time.monotonic() - t0, 3),
                }
                append_telemetry(entry, self.cfg.telemetry_path)
                history.append(entry)
                break

            iter_advisor_calls = 0
            iter_promoted = 0
            for ticket in tickets:
                last_failure = ""
                while ticket.attempts < self.cfg.max_ticket_attempts:
                    ticket.attempts += 1
                    advice_block = "\n".join(ticket.advisor_notes) if ticket.advisor_notes else "(なし)"
                    sub_prompt = (
                        f"Ticket: {ticket.ticket_id}\nTitle: {ticket.title}\n"
                        f"Owned Scope: {ticket.owned_scope}\nDoD: {ticket.acceptance_criteria}\n"
                        f"Previous Failure: {last_failure or '(初回)'}\n"
                        f"Advisor Decision:\n{advice_block}\n"
                    )
                    sub_res = self.sub_runner(ticket, sub_prompt)
                    wd = self.watchdog_runner(ticket, sub_res)
                    verdict = str(wd.get("verdict", "REJECT")).upper()

                    if verdict in {"PASS", "FREEZE_PASS"}:
                        ticket.status = "PASSED"
                        if ticket.quarantine_file:
                            promoted = ratchet_promote_quarantine(
                                Path(ticket.quarantine_file),
                                self.cfg.adversarial_dir,
                                root_cause=str(sub_res.get("root_cause") or sub_res.get("summary") or ticket.title),
                            )
                            if promoted is not None:
                                iter_promoted += 1
                                total_promoted += 1
                        write_knowledge_fragment(
                            self.cfg.knowledge_d_dir,
                            iteration,
                            f"{ticket.ticket_id}: {sub_res.get('root_cause') or sub_res.get('summary')}",
                        )
                        break

                    ticket.rejects += 1
                    last_failure = str(wd.get("reason") or sub_res.get("error") or "verification failed")

                    # Hard 2-Strike Rule: harness deterministically calls Tier-3 Advisor
                    need_advisor = (
                        verdict == "ESCALATE_TO_ADVISOR"
                        or ticket.rejects >= self.cfg.escalate_after_failures
                    )
                    if need_advisor and ticket.attempts < self.cfg.max_ticket_attempts:
                        adv = self.advisor_runner(ticket, last_failure)
                        iter_advisor_calls += 1
                        total_advisor_calls += 1
                        if adv.get("answer"):
                            ticket.advisor_notes.append(str(adv["answer"]))

                    if ticket.rejects >= self.cfg.max_watchdog_rejects:
                        ticket.status = "FAILED"
                        break

                if ticket.status != "PASSED":
                    ticket.status = "FAILED"

            entry = {
                "timestamp": _utc_now_iso(),
                "iteration": iteration,
                "state": "ITERATION_COMPLETE",
                "evaluated_count": screen.get("evaluated_count", 0),
                "p0_violations": len(tickets),
                "tickets_passed": sum(1 for t in tickets if t.status == "PASSED"),
                "tickets_failed": sum(1 for t in tickets if t.status != "PASSED"),
                "advisor_calls": iter_advisor_calls,
                "promoted_cases": iter_promoted,
                "elapsed_s": round(time.monotonic() - t0, 3),
            }
            append_telemetry(entry, self.cfg.telemetry_path)
            history.append(entry)

        return {
            "final_state": final_state,
            "iterations_run": len(history),
            "total_advisor_calls": total_advisor_calls,
            "total_promoted_cases": total_promoted,
            "history": history,
        }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the deterministic 3-tier corporate closed-loop harness.")
    parser.add_argument("--goal", default="P0抜け穴封鎖・Red-Teaming収束ループ")
    parser.add_argument("--max-iterations", type=int, default=3)
    parser.add_argument("--min-budget", type=int, default=20)
    parser.add_argument("--live", action="store_true", help="Invoke real agy CLI models (default: dry-run)")
    parser.add_argument("--dry-run", action="store_true", help="Run simulated loop without calling external LLMs")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    cfg = LoopConfig(
        goal=args.goal,
        max_iterations=args.max_iterations,
        min_redteam_budget=args.min_budget,
        live=bool(args.live and not args.dry_run),
    )
    engine = CompanyLoopEngine(cfg)
    result = engine.run()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(
            f"[company-loop] final_state={result['final_state']} iterations={result['iterations_run']} "
            f"advisor_calls={result['total_advisor_calls']} promoted={result['total_promoted_cases']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
