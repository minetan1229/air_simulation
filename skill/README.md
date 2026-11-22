# Skill Catalog (Skill Index)

This directory (`skill/`) contains on-demand knowledge and procedure modules to maximize context efficiency and task execution accuracy for the Paper Airplane 3D Simulator development and operations.

## 1. Purpose & Operating Policy
- **Maximize Context Efficiency**: Instead of loading all design docs and procedures every time, referencing only the skill (`SKILL.md`) relevant to the instruction reduces LLM token consumption and enables high-precision reasoning.
- **Ensure Reproducibility & Safety**: Standardize "prerequisites," "step-by-step procedures," and "constraints & prohibitions" for each task.

---

## 2. Common Skill Format Specification
Each skill folder's `SKILL.md` has the following YAML Frontmatter and structure:

```markdown
---
name: [skill identifier (kebab-case)]
description: [trigger condition / overview of when to load this skill]
prerequisites:
  - [required files, libraries, environment settings]
---

# [Skill Title]

## 1. Prerequisites
...
## 2. Trigger Conditions
...
## 3. Step-by-Step Workflow
...
## 4. Constraints & Prohibitions
...
```

---

## 3. Skill Index

| Skill Name | Location | Main Target Tasks / Trigger Conditions | Status |
| :--- | :--- | :--- | :---: |
| **`verification-pipeline`** | [`skill/verification-pipeline/SKILL.md`](./verification-pipeline/SKILL.md) | Syntax check, E2E smoke test, Pytest execution, CI verification | **Implemented (Core)** |
| **`flight-physics-tuning`** | [`skill/flight-physics-tuning/SKILL.md`](./flight-physics-tuning/SKILL.md) | 6DoF dynamics, aero model diagnosis, morphing tuning, preset evaluation | **Implemented (Core)** |

---

## 4. Skill Usage Quick Reference
- **Post-code-change verification**: Load `skill/verification-pipeline/SKILL.md` and execute Steps 1–5.
- **Physics/flight anomaly investigation**: Load `skill/flight-physics-tuning/SKILL.md` and run diagnostic scripts.