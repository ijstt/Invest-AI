# Refactoring Plan for Milestone 2: Processing Refactoring

## Objectives
- Extract offset-batch-pagination loop patterns into a shared generic iterator.
- Extract 7 repeated `full_text` constructions into a single helper.
- Ensure all files (modified/created) are under 600 lines.
- Preserve strict public APIs and verify all tests pass 100%.

## Detailed Steps

### Step 1: Exploration and Analysis
- Spawn 3 Explorer subagents to analyze `/home/ijstt/News/src/geoanalytics/processing.py`.
- Identify:
  1. Exact line numbers and structures of the pagination loops.
  2. Exact line numbers and structures of the `full_text` constructions.
  3. Total line counts and where files should be split.
- Aggregate their findings into a cohesive refactoring design.

### Step 2: Implementation
- Spawn a Worker subagent to:
  1. Extract the shared generic iterator (e.g. in a shared helper module or within the package).
  2. Extract the `full_text` helper.
  3. Refactor the pagination loops to use the generic iterator.
  4. Refactor `full_text` constructions to use the helper.
  5. Split the processing code into smaller files under a package `geoanalytics/processing/` or similar structure if any file exceeds 600 lines (with `processing.py` exposing the original public APIs).
  6. Run `pytest` to verify that all existing tests pass 100%.

### Step 3: Verification
- Spawn 2 Reviewer subagents to verify code structure, API preservation, line count limits (< 600 lines), and test outcomes.
- Spawn 2 Challenger subagents to verify edge cases, error handling, and performance characteristics.

### Step 4: Forensic Audit
- Spawn a Forensic Auditor subagent to run integrity verification checks on the refactored code.

### Step 5: Wrap-up & Reporting
- Summarize results in `handoff.md`.
- Send completion message to parent.
