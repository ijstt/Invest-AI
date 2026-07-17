# BRIEFING — 2026-07-17T14:11:15+03:00

## Mission
Modularize the web API in `src/geoanalytics/api/web.py` into modular routers under 600 lines, ensuring all tests pass and verification is clean.

## 🔒 My Identity
- Archetype: sub_orchestrator
- Roles: orchestrator, user_liaison, human_reporter, successor
- Working directory: /home/ijstt/News/.agents/sub_orch_web_api_2/
- Original parent: parent
- Original parent conversation ID: 21146468-b70a-4f0d-833a-6b21d87e2b4f

## 🔒 My Workflow
- **Pattern**: Project
- **Scope document**: /home/ijstt/News/.agents/sub_orch_web_api_2/SCOPE.md
1. **Decompose**: Split `api/web.py` into logically grouped routers under `src/geoanalytics/api/routers/`, ensuring all files are <600 lines and public API is preserved.
2. **Dispatch & Execute** (pick ONE):
   - **Delegate (sub-orchestrator)**: [TBD]
   - **Direct (iteration loop)**: Explorer → Worker → Reviewer → Challenger → Auditor loop.
3. **On failure** (in this order):
   - Retry: nudge stuck agent or re-send task
   - Replace: spawn fresh agent with partial progress
   - Skip: proceed without (only if non-critical)
   - Redistribute: split stuck agent's remaining work
   - Redesign: re-partition decomposition
   - Escalate: report to parent (sub-orchestrators only, last resort)
4. **Succession**: Spawn successor after 16 spawns.
- **Work items**:
  1. Analyze `web.py` and planned modules [pending]
  2. Implement modularization [pending]
  3. Verify with unit/integration tests [pending]
  4. Perform review and audit check [pending]
- **Current phase**: 1
- **Current focus**: Analyze `web.py` and planned modules

## 🔒 Key Constraints
- All refactored/created files must be strictly under 600 lines.
- Preserve strict public APIs so that FastAPI app runs and all tests pass 100%.
- Do not write code directly, delegate to subagents.

## Current Parent
- Conversation ID: 21146468-b70a-4f0d-833a-6b21d87e2b4f
- Updated: not yet

## Key Decisions Made
- None yet.

## Team Roster
| Agent | Type | Work Item | Status | Conv ID |
|-------|------|-----------|--------|---------|

## Succession Status
- Succession required: no
- Spawn count: 0 / 16
- Pending subagents: none
- Predecessor: none
- Successor: not yet spawned

## Active Timers
- Heartbeat cron: 26f89cd9-3261-42a6-9ba0-1e483aa8df18/task-19
- Safety timer: none

## Artifact Index
- /home/ijstt/News/.agents/sub_orch_web_api_2/SCOPE.md — Milestone Scope Document
- /home/ijstt/News/.agents/sub_orch_web_api_2/ORIGINAL_REQUEST.md — Original User Request Record
