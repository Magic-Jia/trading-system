# Codex Worker Prompt Template

Use this template for isolated trading-system hardening workers.

## Launch contract

Controller launch should use:

```bash
codex exec -m gpt-5.5 -c 'model_provider="testvideo"' -c 'model_reasoning_effort="medium"' "$(cat /tmp/codex_prompt.txt)"
```

For prompt metadata include these exact values for auditability:

- model_provider="testvideo"
- model_reasoning_effort="medium"

## Side-effect boundary

- No real orders.
- No testnet orders.
- Do not touch live services, cron jobs, API credentials, or exchange-facing state.
- Offline code, tests, fixtures, parsers, reports, and validation artifacts are allowed.

## Agent boundary

- Do not run nested Codex.
- Do not run Claude, OpenCode, or any other nested AI agent.
- Do not edit `memory/dev-status.md` intentionally.
- If it changes as executor bookkeeping, leave it unstaged and report it.

## Task

Goal:

```
<one narrow defect class or workflow improvement>
```

Allowed files:

```
<explicit allowlist>
```

Do not touch:

```
<explicit denylist>
```

## Required TDD evidence

Report these fields exactly:

- RED command:
- RED failure:
- GREEN change:
- GREEN command:
- Focused verification:
- Impacted verification:
- Verification plan JSON:
  - Include `plan_version`, `plan_kind`, and `command_argv` from `scripts/verify.py --dry-run --json` / `scripts/ci_verify.py --dry-run --json` output.
  - Include entrypoint JSON `suites` from CI/nightly dry-run JSON when applicable so the controller does not infer suite names from command strings.
  - If reporting suite inventory, include `inventory_version` and `inventory_kind` from `scripts/verify.py --list-suites --json` output.
- Changed files:
- Commit hash:
- Known limitations:

## Verification

Prefer repository verification entrypoints when applicable:

```bash
python3 scripts/verify.py --auto-changed
python3 scripts/verify.py --dry-run --json --auto-changed
python3 scripts/ci_verify.py --dry-run --json
python3 scripts/verify.py --suite evidence-chain
git diff --check HEAD
```

For a narrow slice, also run the owning test file directly before committing.

## Controller audit command

After the worker commits, the controller should run:

```bash
python3 scripts/audit_worker_commit.py --commit <worker-commit>
```

For uncommitted worker diffs, pass explicit changed paths with `--changed-file <path>`; empty changed-file input must fail with `changed file must be non-empty`, and duplicate changed-file input must fail with `duplicate changed file`.

The worker must not treat this as proof of merge readiness; it is a controller preflight for changed files and impacted verification plan.

## Commit behavior

Commit only if focused verification passes. Keep the commit focused. Do not include `memory/dev-status.md` or unrelated files.
