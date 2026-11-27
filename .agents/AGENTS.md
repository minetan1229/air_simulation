# Autonomous Execution Loop OS (Entry Point)

This file defines the **loading order and canonical map** for the `.agents/` workspace. Protocol clauses are not duplicated here (canonical: root `AGENTS.md` §1–§5).

## 1. Canonical Map (Single Source of Truth)

| Concern | Canonical Source | Reading Method |
|---|---|---|
| Agent Core Protocol OS (Resident ≤80 lines) | Root `AGENTS.md` | Always resident (§1–§5) |
| Domain-Scoped Invariants & Non-Goals (≤30 lines each) | `{physics,geometry,optimization}/AGENTS.md` | Auto-loaded per directory |
| Corporate Role Subagents | `.agents/agents/{team-lead,qa-watchdog,principal-advisor}.md` | Auto-discovered by Antigravity |
| Active Session State (Per-Session, No Conflict) | `.agents/sessions/<session_id>.md` | 1 file per concurrent session |
| Append-Only Knowledge Fragments | `.agents/knowledge.d/<YYYYMMDD>_<session_id>.md` | Distilled at integration gate |
| Confirmed Knowledge Index (≤80 lines) | `.agents/Knowledge.md` | Keyword grep only (archive in `.agents/archive/`) |
| Advisor Consultation Logs | `.agents/consultations/*.md` | Reference by ticket ID |
| Adversarial Regression Cases | `tests/adversarial_cases/*.json` | Loaded by `tests/test_adversarial_benchmark.py` |
| Standard Procedures (Skills) | `.agents/skills/<name>/SKILL.md` | Load on trigger, discard after use |
| Detailed Technical Specs | `docs/SPECIFICATION.md` | Target section only |

## 2. Loading Order (Progressive Disclosure)
1. **Resident**: Root `AGENTS.md` (+ nested `<domain>/AGENTS.md` when editing that domain).
2. **On Demand**: `.agents/skills/<name>/SKILL.md`, `.agents/sessions/<id>.md`, `.agents/Knowledge.md` (grep only).
3. **Prohibited**: Flat duplicate `.agents/skills/<name>.md` files, direct concurrent edits to `.agents/Project.md` or `.agents/Knowledge.md`, and full-file log/source dumps.
