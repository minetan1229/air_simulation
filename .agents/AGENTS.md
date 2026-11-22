# Autonomous Execution Loop OS (Entry Point)

This file defines the **loading order and canonical map** for the `.agents/` workspace. The protocol clauses themselves are not duplicated (see root `AGENTS.md` §4 "Canonical Principle").

---

## 1. Canonical Map (Which File is Canonical)

| Concern | Canonical Source | Reading Method |
|---|---|---|
| Agent Core Protocol OS (Resident Rules) | Root `AGENTS.md` | Read every session (§1–§4) |
| Detailed Technical Specs (Physics/API/Security/Module Structure) | `docs/SPECIFICATION.md` | Read relevant sections only (line-specified) |
| Current Goal, Milestones, Loop State | `.agents/Project.md` | Read only when checking diffs |
| Resident Constraints & Technical Guardrails | `.agents/Knowledge-important.md` | ≤50 lines, resident |
| Confirmed Knowledge Base (Past Knowledge) | `.agents/Knowledge.md` | Keyword search for partial extraction (do not read full) |
| Standard Procedures (Skills) | `.agents/skills/<name>.md` | Relevant section only, discard after completion |

**No Duplicate Storage**: Physical constants, spec values, known issues are stored **only** in `docs/SPECIFICATION.md` and `.agents/Knowledge.md`. Do not transcribe to root `AGENTS.md`.

---

## 2. Loading Order (Context Minimization)

1. **Resident**: Root `AGENTS.md` §1–§4 + `.agents/Knowledge-important.md`
2. **Appendix (When Relevant)**: Root `AGENTS.md` §5–§8 / `.agents/Project.md` / `.agents/Knowledge.md` / `docs/SPECIFICATION.md`
3. **Prohibited**: Full log/source output. Enforce line/range specification and keyword search.

---

## 3. Three Exclusion Rules (_protocol Canonical: Root `AGENTS.md`)

| Rule | Canonical | Summary |
|---|---|---|
| Context Minimization | Root `AGENTS.md` §1.1 | Resident is §1–§4 only. Appendix: relevant sections only |
| Anti-Fallacy (Texas Sharpshooter) | Root `AGENTS.md` §1.2 | No changes/execution without declaring prior hypothesis and acceptance criteria. Do not treat accidental behavior as complete. |
| Autonomous Execution Loop & Proposal | Root `AGENTS.md` §3, §3.1 | 6 phases Observe→Finalize + proactive proposals for physics improvement & reality conformance |

**Loop State canonical is `.agents/Project.md` §"Loop State"**; _protocol defines phase contents, retry limits, rollback rules in root `AGENTS.md` §1.3. The two do not hold identical content.

---

## 4. File Naming Conventions

- `.agents/Project.md` (capital `P`), `.agents/Knowledge.md`, `.agents/Knowledge-important.md`, `.agents/AGENTS.md` (capital `AGENTS`) are fixed.
- Root protocol canonical is `AGENTS.md` (**uppercase**). Do not mix case. Must resolve on case-sensitive filesystems (Linux / macOS / CI runner).