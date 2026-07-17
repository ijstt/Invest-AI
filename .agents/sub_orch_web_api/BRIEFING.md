# BRIEFING — 2026-07-17T09:21:00Z

## Mission
Modularize the Web API by splitting `src/geoanalytics/api/web.py` into smaller files under 600 lines, ensuring public APIs are preserved and all tests pass.

## 🔒 My Identity
- Archetype: sub_orch
- Roles: orchestrator, user_liaison, human_reporter, successor
- Working directory: /home/ijstt/News/.agents/sub_orch_web_api/
- Original parent: parent
- Original parent conversation ID: 21146468-b70a-4f0d-833a-6b21d87e2b4f

## 🔒 My Workflow
- **Pattern**: Project Pattern (Sub-orchestrator)
- **Scope document**: /home/ijstt/News/.agents/sub_orch_web_api/SCOPE.md
1. **Decompose**: Decompose the web API routes into entity/feature modules.
2. **Dispatch & Execute**:
   - Iterate: Explorer -> Worker -> Reviewer -> Challenger -> Auditor.
3. **On failure** (in this order):
   - Retry: nudge stuck agent or re-send task
   - Replace: spawn fresh agent with partial progress
   - Skip: proceed without (only if non-critical)
   - Redistribute: split stuck agent's remaining work
   - Redesign: re-partition decomposition
   - Escalate: report to parent (sub-orchestrators only, last resort)
4. **Succession**: Self-succeed if spawns >= 16.
- **Work items**:
  1. Analyze web.py and current tests [pending]
  2. Split web.py into modular routers [pending]
  3. Verify with Reviewers, Challengers, and Forensic Auditor [pending]
- **Current phase**: 1
- **Current focus**: Analysis and planning

## 🔒 Key Constraints
- All refactored/created files must be strictly under 600 lines.
- Preserve strict public APIs so that FastAPI app runs and tests pass 100%.
- Never reuse a subagent after it has delivered its handoff — always spawn fresh.

## Current Parent
- Conversation ID: 21146468-b70a-4f0d-833a-6b21d87e2b4f
- Updated: not yet

## Key Decisions Made
- None yet

## Team Roster
| Agent | Type | Work Item | Status | Conv ID |
|-------|------|-----------|--------|---------|
| explorer_1 | teamwork_preview_explorer | Analyze web.py and tests | completed | 2dd3b2c0-d84e-4b49-88c8-d9bcc4ca1569 |
| worker_1 | teamwork_preview_worker | Modularize web.py and run tests | in-progress | f968d418-0855-44ad-8871-efa044429f6d |

## Succession Status
- Succession required: no
- Spawn count: 2 / 16
- Pending subagents: [f968d418-0855-44ad-8871-efa044429f6d]
- Predecessor: none
- Successor: not yet spawned

## Active Timers
- Heartbeat cron: task-15
- Safety timer: none
- On succession: kill all timers before spawning successor
- On context truncation: run manage_task(Action="list") — re-create if missing

## Artifact Index
- /home/ijstt/News/.agents/sub_orch_web_api/SCOPE.md — Scope document
- /home/ijstt/News/.agents/sub_orch_web_api/ORIGINAL_REQUEST.md — Original request copy
