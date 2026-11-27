"""Antigravity Dynamic Workflow (`/run-workflow`) template for air_simulation.

Usage inside Antigravity App / CLI:
    /run-workflow Run the corporate 3-tier closed loop in .agents/workflows/company_loop_workflow.py

Implements:
  - Phase 1 (Plan): Main orchestrator decomposes mission into exclusive-scope WBS tickets
  - Phase 2 (Parallel Execute): `team-lead` subagents (`flash` / Hydrogen Low) run in
    isolated Git worktrees (`workspace="branch"`)
  - Phase 3 (Central Watchdog + 2-Strike Escalation): `qa-watchdog` audits diffs and
    escalates to `principal-advisor` (`pro` / Opus 4.6 or Hydrogen XHigh) on 2nd failure
  - Phase 4 (Freeze Gate): Verifies minimum Red-Teaming budget + zero P0 violations
"""

# Note: `phase`, `agent`, `parallel`, `log` are injected by Antigravity's
# `/run-workflow` runtime (https://antigravity.google/docs/dynamic-workflows/).

TICKET_SCHEMA = {
    "type": "object",
    "properties": {
        "tickets": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "owned_scope": {"type": "string"},
                    "dod": {"type": "string"},
                },
                "required": ["id", "title", "owned_scope", "dod"],
            },
        }
    },
    "required": ["tickets"],
}

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["PASS", "REJECT", "ESCALATE_TO_ADVISOR", "FREEZE_PASS"]},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "reason"],
}


async def run_corporate_loop(goal: str) -> dict:
    phase("1. Main Plan & WBS Decomposition (Hydrogen Medium)")
    plan = await agent(
        prompt=(
            f"目標: {goal}\n"
            "1. `python tools/redteam_sanity_screen.py --json` の結果と `AGENTS.md` を確認せよ。\n"
            "2. 排他ファイル境界付きで最小の WBS チケット一覧を JSON で出力せよ。"
        ),
        schema=TICKET_SCHEMA,
        role="Main PM",
        model="inherit",
    )
    tickets = plan.get("tickets", [])
    log(f"Planned {len(tickets)} WBS ticket(s).")

    phase("2. Parallel Team-Lead Execution + Hard 2-Strike Advisor Escalation")

    async def execute_ticket(ticket: dict) -> dict:
        advice = ""
        for attempt in range(1, 4):
            sub_out = await agent(
                prompt=(
                    f"Ticket: {ticket['id']} - {ticket['title']}\n"
                    f"Owned Scope: {ticket['owned_scope']}\nDoD: {ticket['dod']}\n"
                    f"Attempt: {attempt}\nAdvisor Guidance: {advice or '(none)'}\n"
                    "対症療法ペナルティを禁止し、根本の物理式・制約を修正してセルフチェックせよ。"
                ),
                schema={"type": "string"},
                role=f"TeamLead-{ticket['id']}",
                type_name="team-lead",
                workspace="branch",
                model="flash",
            )
            qa = await agent(
                prompt=(
                    f"Ticket `{ticket['id']}` の成果物を監査せよ:\n{sub_out}\n"
                    "Evidence-First・対症療法禁止・`check_agents_hygiene.py` を確認し verdict を返せ。"
                ),
                schema=VERDICT_SCHEMA,
                role=f"Watchdog-{ticket['id']}",
                type_name="qa-watchdog",
                model="flash",
            )
            if qa["verdict"] in ("PASS", "FREEZE_PASS"):
                return {"ticket_id": ticket["id"], "status": "PASSED", "attempts": attempt}

            if qa["verdict"] == "ESCALATE_TO_ADVISOR" or attempt >= 2:
                advice = await agent(
                    prompt=(
                        f"Ticket `{ticket['id']}` が {attempt} 回失敗した。Watchdog理由: {qa['reason']}\n"
                        "読み取り専用で根本原因と物理式・制約の修正方針および合否基準を提示せよ。"
                    ),
                    schema={"type": "string"},
                    role=f"Advisor-{ticket['id']}",
                    type_name="principal-advisor",
                    model="pro",
                )
        return {"ticket_id": ticket["id"], "status": "FAILED", "attempts": 3}

    results = await parallel(tickets, execute_ticket)

    phase("3. Final Freeze Gate & Telemetry")
    log(f"Completed tickets: {results}")
    return {"goal": goal, "results": results}
