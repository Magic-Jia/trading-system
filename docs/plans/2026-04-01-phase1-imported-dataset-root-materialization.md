# Phase-1 Imported Dataset Root Materialization / Validation Plan

## Scope
- Only touch phase-1 imported dataset root materialization / validation.
- Binance first, futures first.
- Dataset kinds limited to `ohlcv`, `funding`, `open_interest`.
- No downloader / network code.

## Steps
1. Inspect current worktree state and separate `memory/dev-status.md` noise from real code changes.
2. Identify committed baseline files for importer / dataset validation and the narrow tests that cover them.
3. If unfinished code already exists in the worktree, run the focused tests first to verify its current behavior.
4. Write one failing focused test for missing or incorrect imported dataset root behavior.
5. Run that focused test and confirm the failure is caused by the missing phase-1 behavior.
6. Implement the minimal code needed to materialize / validate the imported dataset root.
7. Re-run the focused test until it passes.
8. Re-run adjacent focused tests for Binance futures `ohlcv` / `funding` / `open_interest` paths.
9. Review the diff to ensure no downloader/network scope leaked in.
10. Attempt a local commit if the tree is clean enough; if commit is blocked, report it cleanly.
