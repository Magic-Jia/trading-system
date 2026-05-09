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
python3 scripts/verify.py --suite full
```

`--dry-run` prints the commands without executing them.

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

## Failure classification

- Missing test path or malformed `pytest -k`: verification-command failure, not product failure.
- Network/testnet HTTP during offline verification: environment pollution unless the test intentionally covers that path.
- Codex tail summary mismatch: self-summary pollution unless controller evidence confirms it.
