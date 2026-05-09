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
python3 scripts/verify.py --suite paper-optimization
python3 scripts/verify.py --suite app-smoke
python3 scripts/verify.py --suite workflow-meta
python3 scripts/verify.py --suite full
python3 scripts/verify.py --list-suites
python3 scripts/verify.py --list-suites --json
```

`--dry-run` prints the commands without executing them. Add `--json` to emit a machine-readable plan for CI/controller auditing. `--list-suites --json` emits a versioned suite inventory with `inventory_version`, `inventory_kind`, `count`, and `tests` for automation that should not parse human text. Workflow-control files such as `AGENTS.md` route through `workflow-meta` so agent-rule changes cannot bypass process tests:

```bash
python3 scripts/verify.py --dry-run --json --auto-changed
python3 scripts/verify.py --dry-run --strict-auto-changed --auto-changed
python3 scripts/ci_verify.py
python3 scripts/ci_verify.py --dry-run --json
python3 scripts/nightly_verify.py
python3 scripts/nightly_verify.py --dry-run --json
```

The JSON payload includes `plan_version`, `plan_kind` where applicable, `changed`, `suites`, `tests`, `commands`, `command_argv`, `full`, and `full_checkpoint_reason`. Base `scripts/verify.py --dry-run --json` plans use `plan_kind: verification_plan`. CI/nightly entrypoint JSON plans include `suites` so controllers do not parse command strings to recover planned suite names. JSON payload includes `strict_changed_verification` so controllers can distinguish advisory plans from strict changed-path gates. `--json requires --dry-run`; using `--json` without `--dry-run` fails fast instead of executing tests while a caller expects JSON, including `scripts/ci_verify.py --json` and `scripts/nightly_verify.py --json`. Use `--strict-auto-changed` in controller/CI preflight when every changed path must map to at least one verification test and forbidden changed file noise such as `memory/dev-status.md` must be rejected explicitly. With explicit --changed paths, strict mode validates exactly those explicit `--changed` paths and does not mix in unrelated controller worktree dirtiness; without explicit paths, strict mode implies auto-discovery of changed/untracked paths. Verification selection inputs fail closed: empty paths fail with `changed path must be non-empty`; duplicates fail with `duplicate changed path`, `duplicate suite`, or `duplicate explicit test`; empty selectors fail with `suite must be non-empty` or `explicit test must be non-empty`; checkpoint policy counters fail with `require-full-after must be non-negative` and `slice-count must be non-negative`. registered suite test files map back to their owning verification suite, so a test-only commit cannot fail worker audit just because the changed file is itself a test path. Workflow-meta tests assert the exact top-level field set for verification plans, CI plans, nightly plans, suite inventory, and worker-audit JSON so schema drift fails closed. The text dry-run plans for `scripts/ci_verify.py` and `scripts/nightly_verify.py` must expose the same critical invariants as JSON: `plan_version: 1`, `plan_kind: ci_verification_plan`, `strict_changed_verification: true`, `suites: workflow-meta,evidence-chain` for CI preflight, `plan_kind: nightly_verification_plan`, `clean_env: true`, and `suites: full` for nightly full verification.

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

The audit emits JSON with changed files and the impacted verification plan. It rejects forbidden files such as `memory/dev-status.md` and fails fast with `no impacted verification tests` when changed files do not map to any verification path. The audit output is not a replacement for controller-side pytest; it is the preflight that tells the controller which tests to rerun. The JSON explicitly includes `audit_version`, `final_merge_proof=false`, `controller_next_steps`, `worktree_dirty`, and `worktree_dirty_paths` so agents cannot mistake preflight for merge approval or ignore local controller dirtiness and its exact dirty files. Its nested `verification_plan` includes `plan_kind: verification_plan` and has a nested `verification_plan` exact top-level field set so downstream tooling can distinguish the embedded base verification plan from CI/nightly plans and fail closed on schema drift.

## Failure classification

- Missing test path or malformed `pytest -k`: verification-command failure, not product failure.
- Network/testnet HTTP during offline verification: environment pollution unless the test intentionally covers that path.
- Codex tail summary mismatch: self-summary pollution unless controller evidence confirms it.
