## 2026-07-16T20:11:02Z
<USER_REQUEST>
Analyze `/home/ijstt/News/src/geoanalytics/processing.py` to identify the offset-batch-pagination loop patterns and the 7 repeated `full_text` constructions. Propose a refactoring strategy to extract the loop patterns into a shared generic iterator and the `full_text` constructions into a single helper, splitting the file if necessary so that no file exceeds 600 lines, whilst preserving the strict public APIs. Write your analysis/proposed design to `.agents/explorer_processing_3_1/analysis.md` and report back.
</USER_REQUEST>
