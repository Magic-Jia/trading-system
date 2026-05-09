# Development Workflow

This repository uses a deterministic trading-system development loop. The goal is not to claim the process is literally perfect, but to make each step executable, auditable, and harder to skip.

## Core loop

1. Pick one high-value correctness gap.
2. Write the smallest test that exposes it.
3. Verify RED → GREEN:
   - RED: the new test fails for the expected reason.
   - GREEN: the minimal implementation makes it pass.
4. Run the owning test file.
5. Run `git diff --check HEAD`.
6. Run an impacted regression with `scripts/verify.py`.
7. Commit only the relevant source/test/docs files.
8. Exclude executor bookkeeping noise such as `memory/dev-status.md` unless explicitly requested.
9. After several slices or a wide subsystem change, run `python3 scripts/verify.py --suite evidence-chain` or `python3 scripts/verify.py --suite full`.

## Verification entrypoint

Use `scripts/verify.py` instead of hand-typing long regression lists when possible:

```bash
python3 scripts/verify.py --auto-changed
python3 scripts/verify.py --changed trading_system/app/main.py
python3 scripts/verify.py --suite evidence-chain
python3 scripts/verify.py --suite runtime-main
python3 scripts/verify.py --suite universe
python3 scripts/verify.py --suite portfolio
python3 scripts/verify.py --suite backtest-core
python3 scripts/verify.py --suite archive-data
python3 scripts/verify.py --suite runtime-support
python3 scripts/verify.py --suite app-smoke
python3 scripts/verify.py --suite workflow-meta
python3 scripts/verify.py --suite full
python3 scripts/verify.py --list-suites
```

`--dry-run` prints the commands without executing them. Add `--json` to emit a machine-readable plan for CI/controller auditing:

```bash
python3 scripts/verify.py --dry-run --json --auto-changed
```

The JSON payload includes `changed`, `suites`, `tests`, `commands`, `full`, and `full_checkpoint_reason`. `--json requires --dry-run`; using `--json` without `--dry-run` fails fast instead of executing tests while a caller expects JSON.

Use full-suite checkpoint policy when multiple slices have landed since the last full run:

```bash
python3 scripts/verify.py --dry-run --json --require-full-after 3 --slice-count 3 --auto-changed
```

When `--slice-count` reaches `--require-full-after`, the plan is forced to `--suite full` even if narrower impacted tests were also selected.

## Codex / Controller split

Codex can implement and self-test in isolated worktrees, but Controller remains the final integration gate.

- Codex summary is not final evidence.
- Controller must inspect git state and rerun focused or impacted tests.
- Controller must run `git diff --check HEAD`.
- Controller cherry-picks only independently verified commits.
- Completion order is not integration order.

## Side-effect boundary

During offline development and hardening:

- No real orders.
- No testnet orders unless explicitly approved in the current turn.
- Offline fixtures, parsers, reports, validation scripts, and tests are allowed.

## Regression policy

- Small slice: owning tests + impacted `scripts/verify.py` command.
- Evidence/live-readiness slice: `python3 scripts/verify.py --suite evidence-chain`.
- Multiple integrated slices or broad refactor: full suite.

## Worker commit audit

Before cherry-picking or trusting an isolated Codex worker commit, run controller-side audit:

```bash
python3 scripts/audit_worker_commit.py --commit <worker-commit>
```

The audit emits JSON with changed files and the impacted verification plan. It rejects forbidden files such as `memory/dev-status.md`. The audit output is not a replacement for controller-side pytest; it is the preflight that tells the controller which tests to rerun. The JSON explicitly includes `final_merge_proof=false`, `controller_next_steps`, and `worktree_dirty` so agents cannot mistake preflight for merge approval or ignore local controller dirtiness.

## Failure classification

- Missing test path or malformed `pytest -k`: verification-command failure, not product failure.
- Network/testnet HTTP during offline verification: environment pollution unless the test intentionally covers that path.
- Codex tail summary mismatch: self-summary pollution unless controller evidence confirms it.
