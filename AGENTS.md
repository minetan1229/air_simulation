# Agent Core Protocol (Resident Core + Reference Index)
<!--
Canonical: This Protocol = Resident Minimum / .agents/Knowledge.md = Past Knowledge / .agents/Project.md = Current State & LoopState / .agents/skills/* = Standard Procedures / docs/SPECIFICATION.md = Detailed Specifications
Only this file is resident. Details, procedures, and past knowledge are referenced on-demand from .agents/ (full reading prohibited).
-->

## 0. Reference Map (.agents Coordination Hub)
| Purpose | Reference (Canonical) | Reading Method & Trigger |
|---|---|---|
| Current State, Goal, Loop State | `.agents/Project.md` | §1–§3 (Check each loop Phase 1, update Phase 5) |
| Past Knowledge, Known Issues, Workarounds | `.agents/Knowledge.md` | Keyword grep extraction (full reading prohibited; append confirmed facts) |
| Staged Verification & CI/CD Procedures | `.agents/skills/verify-and-test.md` | Phase 4, 6 execution (test order, Git, CI verification) |
| Discord Webhook Notifications | `.agents/skills/notify-webhook.md` | Phase start/complete/warning (color, report linkage) |
| Subagent Role Division | `.agents/skills/subagent-delegation.md` | Parallel work, delegation (Tier separation, responsibility boundary table) |
| Discord×Agent Routing | `.agents/skills/discord-bridge-routing.md` | 429 persistence, fallback, monitoring reporting (canonical) |
| Resident Process Usage Detection & Cleanup | `.agents/skills/process-hygiene.md` | Memory pressure, residual task check (`tools/process_hygiene.py`) |
| Context Compression & State Handover | `.agents/skills/loop-handover.md` | Context bloat, session end |
| Detailed Physics, API, Architecture | `docs/SPECIFICATION.md` | Implementation, design changes (relevant sections only) |
| Tool/Task-Specific Skills / MCP | `skill/README.md` / `.kilo/kilo.jsonc` | When using relevant skill / MCP |

---

## 1. Core Protocol (Autonomous Loop Coordination)
- **1.1 Context Minimization**: Only this file is resident. `.agents/` subdirectories are extracted partially only when needed. Full log/source output prohibited. `memory` MCP is a search index (no long-text double storage).
- **1.2 Texas Sharpshooter Fallacy Prevention**: Explicitly declare hypotheses and falsifiable acceptance criteria in advance. Post-hoc changes and acceptance of accidental behavior as complete are prohibited. On mismatch, reject hypothesis and identify causality. Use physical values, not intentionally tuned constants (reward hacking placeholders).
- **1.4 Protocol Governance**: Modification of this file without explicit user instruction or audit consent is prohibited. Specifications go to `docs/SPECIFICATION.md`, knowledge to `.agents/Knowledge.md`.
- **1.5 Agent Structure (Subagent Cap 4)**: Maximum **4 active subagents** per main agent. Breakdown as follows; exceeding this cap prohibits delegation (wait for existing slot completion or merge into main).
  1. **Mandatory 1 Slot = Monitor Agent**: Continuously monitors system status, API responses, error states, rate limits; **immediately reports/alerts main agent** (Discord Webhook + response channel) upon anomaly detection. Does not perform operational work; always holds 1 slot.
  2. **Remaining Max 3 Slots = Operational Agents**: Implementation, search, Discord response, etc., with exclusive responsibility domains (boundary table in `.agents/skills/subagent-delegation.md`).
- **1.6 Discord×Agent Routing**: Primary agent is Antigravity. On API error, timeout, zero remaining rate, or persistent 429 failure, **automatically fall back to Kilo Code (default free model)** without interrupting processing. 429 triggers backoff then `continue` for automatic resumption. Monitor agent reports the switch (canonical: `.agents/skills/discord-bridge-routing.md`).

---

## 2. Guardrails (Resident Constraints)
- **Strict Rules**: File deletion without explicit user permission prohibited. Changes/execution without prior hypothesis and acceptance criteria prohibited. Acceptance of accidental behavior as complete prohibited. Full file/log reading/output prohibited.
- **Plan & Todo First (Mandatory)**: Any work, code change, investigation, or verification must **always have a Plan (policy/overview) and Todo list (granular concrete tasks) established and declared before execution**. Impromptu changes or skipping steps without Todo are prohibited.
- **Strict Issue Completion/Close Conditions (Formal Completion & Unresolved Close Prohibited)**:
  1. *Completion by Test Addition Only Prohibited*: For unimplemented or defective physics models/numerical algorithms, adding tests that merely assert current behavior must not mark the Issue as Completed.
  2. *Completion by Documentation Only Prohibited*: Closing an Issue solely because "constraints were documented in spec/report" or "unimplemented status was noted" is prohibited (documenting constraints is "current state recording," not "problem resolution").
  3. *Aggregation to Unstarted Issues Prohibited*: If the target Issue for consolidation/duplication is unresolved/unstarted (Open), do not close the issue while unstarted; instead, establish parent-child relationship (sub-issue/blocked-by) with the consolidation target, or keep open until substantive resolution is merged.
  4. *Close Acceptance Verification*: All "completion conditions" and "investigation/actions" items in the Issue body must be physically/engineeringly satisfied, with independent reference solution or measured value comparison established.
- **Governance**:
  - *Zero-Placeholder*: `TODO`, `...`, empty functions, etc., completely prohibited. Only working code.
  - *Single Committer*: Even when delegating to subagents, repository changes and commits are centralized to parent (`.agents/skills/subagent-delegation.md`).
  - *Subagent Cap*: Active subagents max 4 (1 monitor mandatory, 3 operational). Delegation exceeding cap is rejected.
  - *Branch Isolation*: During `genetic-project` development phase (autonomous evolution research system under construction), commits, merges, cherry-picks, and PR creation to `master`/`main` are **strictly prohibited**. `tools/branch_guard.py` blocks protected branch execution, integrated into test suite (`conftest.py`), `app.py`, and `scripts/smoke_test.py` (relaxable via `PAPER_AIRCRAFT_BRANCH_GUARD=warn|off`).
  - *Mission Branch Mandatory*: **All missions must create a dedicated branch before starting and work on that branch**. At start, switch to `mission/<slug>` (or `mission/<slug>-<issue-number>` if issue number exists); working without branch creation and direct commits to `master`/`main` are prohibited. Merge only upon mission completion.
  - *Git & Discord*: Branch operations, per-phase notifications (`.agents/skills/notify-webhook.md`).
- **Critical Traps (Details & Workarounds in `.agents/Knowledge.md`)**:
  - *CI Runner / Encoding*: Actions `run` must use `defaults: run: shell: bash`. UTF-8 corruption via PowerShell `Get/Set-Content` prohibited (edit via Python/dedicated tools).
  - *Coordinate System, Wind Altitude, Boundary Grounding*: Wind field (+z up) ⇔ World NED (+z down) sign conversion ($w_{NED}=-w_{wind}$). Wind sampled at height from ground surface (`z_up=-z_ned`) (6DoF reference path and GPU batch path use same convention). Horizontal 100km limit, instant death underground (`min_alt<-0.05`), CCD interpolation, wingtip dissipation cap.
  - *Evaluation Status / GA Defaults*: Do **not relabel** failed evaluations to `timeout`/`crashed` etc. (contamination of Pareto frontier and incumbent = reward hacking path). Trial counts and objective function defaults consolidated in `physics/ga_config.py`, not hardcoded in search logic.
  - *429 / Fallback*: Rate limits not treated as failures; automatic continuation via backoff + `continue`. On Antigravity unavailability, auto-switch to Kilo Code (free); monitor agent reports (canonical: `.agents/skills/discord-bridge-routing.md`).
  - *Known Physics Deviations*: `PHYSICS_PAPER_CONFORMANCE_AUDIT.md` §0.0 (P0–P3 complete) and `no-finish.md` are canonical.

---

## 3. Autonomous Execution Loop
1. *Plan & Todo (Observe)*: Analyze current state, organize issues and goals, **always establish and declare a Plan (policy/overview) and Todo list (granular execution steps)**. Check `.agents/Project.md` Loop State. If no remaining tasks, proceed to Phase 6; otherwise define minimal subtasks.
2. *Hypothesize*: Declare prior hypothesis and acceptance criteria.
3. *Execute*: Execute minimal changes and verification one by one per Todo (irreversible operations pre-recorded, single committer principle). Digest and update Todo as progress occurs.
4. *Verify*: Acceptance verification (`.agents/skills/verify-and-test.md`). On mismatch, rollback to Phase 2 (max 3 retries).
5. *Distill & Loop*: Confirmed facts to `.agents/Knowledge.md`, current state to `.agents/Project.md`. Review remaining Todos; if bloated, execute `.agents/skills/loop-handover.md`.
6. *Finalize & Terminate*: Todo completion confirmed → all local checks pass → merge & push to target branch (default master; `genetic-project` phase follows §2 branch isolation, prohibiting operations targeting master) → CI pass confirmed → Issue verified closed → HALT.

## 3.1 Proactive Improvement Proposal
- Within the current coefficient-based quasi-steady model framework, prioritize numerical stability and consistency fixes while actively proposing improvements to approach real paper airplane behavior: simple LSB hysteresis addition, rotational term for added mass, dynamic damping enhancement, experimental calibration introduction.
- Major architecture changes (full CFD, full FSI, unsteady RANS, etc.) must be proposed as "options" with explicit computational cost, allowing user choice.
- From the perspective of "more realistic" and "best realistic configuration," include multi-fidelity approaches (optimization with current model, final verification only with high fidelity) as standard proposal candidates.

---

## 4. Command Quick Reference (Resident)
| Purpose | Command / Procedure Canonical |
|---|---|
| Syntax / Static Analysis / Type | `python -m compileall physics api tools app.py scripts/smoke_test.py` / `ruff check .` / `mypy physics api app.py scripts/smoke_test.py` |
| Unit / Audit / E2E | `python -m pytest tests -q` / `python scripts/smoke_test.py` (details: `.agents/skills/verify-and-test.md`) |
| Discord Notification | `python tools/notify_discord.py <status> "<title>" "<summary>"` (details: `.agents/skills/notify-webhook.md`) |
| Discord Bridge / Quota / Monitoring | `python tools/discord_bridge.py` / Response channel `!quota` `!monitor` `!backend` `!model` (canonical: `.agents/skills/discord-bridge-routing.md`) |
| MCP Check / Call | `python tools/verify_mcp_servers.py` / `python tools/mcp_call.py <server> <tool> --arg k=v` |
| CI Check / Issue | `python tools/check_runs.py` / `python tools/check_issues.py` / `close_resolved_issue.py` |
| Resident Process Cleanup / UI Verification | `python tools/process_hygiene.py --list` / `--apply` (criteria: `.agents/skills/process-hygiene.md`) / `python tools/capture_ui.py` (real browser via playwright MCP) |

---

## 5. Canonical Maintenance Rules (.agents Sync Rules)
- **Placement**: Confirmed past facts → `.agents/Knowledge.md` / Current state & progress → `.agents/Project.md` / Standard procedures → `.agents/skills/` / Detailed specifications → `docs/SPECIFICATION.md`.
- **Line Limits**: This file ~100 lines / `Knowledge.md` ≤80 lines / `Project.md` ≤40 lines. Content with canonical sources in `.agents/` or `docs/` (procedures, role division tables, detailed knowledge, physical constants, routing rules) must not be written directly here; keep as reference links.

---

## 6. MCP Usage Guide (Foundation: `.kilo/kilo.jsonc`)
1. `context7`: Latest primary spec retrieval before library/external API implementation.
2. `sequential-thinking`: Multi-step reasoning for 6DoF numerical breakdown, grounding dissipation, attitude divergence.
3. `fetch`: Paper and external technical Markdown retrieval (internal IP blocking, robots.txt compliance).
4. `memory`: Cross-session knowledge search index (canonical is `.agents/Knowledge.md`; no long-text double storage).
5. `playwright`: UI/Three.js real-browser rendering and console error verification.
※ Do not waste MCP on routine processing (file editing, pytest, etc.). On failure, use `tools/verify_mcp_servers.py`.

---

## 8. Physics Investigation & High-Fidelity Protocol
9. When investigating physics, actively research and propose both improvement room in current model and realistic high-fidelity options (with cost).