# BRIEFING — 2026-07-16T15:39:51+03:00

## Mission
Refactor Invest-AI monoliths, eliminate NLP duplication, preserve strict public APIs, fix private imports, and verify 100% test completion.

## 🔒 My Identity
- Archetype: teamwork_preview_orchestrator
- Roles: orchestrator, user_liaison, human_reporter, successor
- Working directory: /home/ijstt/News/.agents/orchestrator/
- Original parent: sentinel
- Original parent conversation ID: 21146468-b70a-4f0d-833a-6b21d87e2b4f

## 🔒 My Workflow
- **Pattern**: Project
- **Scope document**: /home/ijstt/News/PROJECT.md
1. **Decompose**: Decompose the refactoring requirements into logical milestones representing separate architectural modules.
2. **Dispatch & Execute** (pick ONE):
   - **Delegate (sub-orchestrator)**: When an item is too large, spawn a sub-orchestrator for it.
3. **On failure** (in this order):
   - Retry: nudge stuck agent or re-send task
   - Replace: spawn fresh agent with partial progress
   - Skip: proceed without (only if non-critical)
   - Redistribute: split stuck agent's remaining work
   - Redesign: re-partition decomposition
   - Escalate: report to parent (sub-orchestrators only, last resort)
4. **Succession**: At 16 spawns, write handoff.md, spawn successor.
- **Work items**:
  1. Set up E2E tests and infrastructure [pending]
  2. Refactor processing.py and extract iterator/helper [pending]
  3. Refactor sentiment.py and nlp/_seqcls.py to eliminate duplication [pending]
  4. Refactor geoanalytics/cli.py to cli/ package [pending]
  5. Refactor geoanalytics/api/web.py to modular routers [pending]
  6. Fix private imports in nlp/fundamentals.py [pending]
  7. Final E2E and unit test pass and coverage verification [pending]
- **Current phase**: 1
- **Current focus**: Decompose and plan

## 🔒 Key Constraints
- NEVER write, modify, or create source code files directly.
- NEVER run build/test commands yourself — require workers to do so.
- You MAY use file-editing tools ONLY for metadata/state files (.md) in your .agents/ folder.
- Binary veto by Forensic Auditor: any integrity violation or cheating means failure.
- Never reuse a subagent after it has delivered its handoff — always spawn fresh

## Current Parent
- Conversation ID: 21146468-b70a-4f0d-833a-6b21d87e2b4f
- Updated: yes

## Key Decisions Made
- Use Project Orchestrator pattern.
- Initialize E2E test suite tracking first.

## Team Roster
| Agent | Type | Work Item | Status | Conv ID |
|-------|------|-----------|--------|---------|
| sub_orch_web_fixes | self | Fix failing web tests | completed | 851f54b6-d3c9-4c6c-a2ea-7e4b1074badb |
| sub_orch_processing | self | Refactor processing.py | failed | e60632f7-f1b1-41c7-a50c-900af0332219 |
| sub_orch_processing_2 | self | Refactor processing.py (attempt 2) | completed | 9253a136-8d66-42b1-813c-e4866186a0d6 |
| sub_orch_processing_3 | self | Refactor processing.py (attempt 3) | completed | 379c472d-00da-41ba-bd97-1a26a539d36d |
| sub_orch_nlp | self | Refactor NLP packages | failed | 62d5da59-eed7-4d5b-a551-00280c05b8d0 |
| sub_orch_nlp_2 | self | Refactor NLP packages (attempt 2) | completed | 9fbcc80c-d59b-4399-a9e8-5923972c67c4 |
| sub_orch_nlp_3 | self | Refactor NLP packages (attempt 3) | completed | 28c37c42-ab4b-492c-88aa-f171b5c1e837 |
| sub_orch_web_api | self | Split api/web.py | failed | b718efd8-6df0-40e1-bd22-15372c192d0d |
| sub_orch_web_api_2 | self | Split api/web.py (attempt 2) | in-progress | 26f89cd9-3261-42a6-9ba0-1e483aa8df18 |

## Succession Status
- Succession required: no
- Spawn count: 9 / 16
- Pending subagents: 26f89cd9-3261-42a6-9ba0-1e483aa8df18
- Predecessor: none
- Successor: not yet spawned

## Active Timers
- Heartbeat cron: task-17
- Safety timer: none
- On succession: kill all timers before spawning successor
- On context truncation: run `manage_task(Action="list")` — re-create if missing

## Artifact Index
- /home/ijstt/News/PROJECT.md — Global index, milestones, interfaces, code layout.
- /home/ijstt/News/.agents/orchestrator/plan.md — Detailed execution plan.
- /home/ijstt/News/.agents/orchestrator/progress.md — Status and heartbeat.
