## [LRN-20260321-001] correction

**Logged**: 2026-03-21T08:39:00+01:00
**Priority**: high
**Status**: promoted
**Area**: config

### Summary
System-generated runtime notifications do not count as Claw proactively reporting background Codex task exits to the user.

### Details
A background Codex session exited and OpenClaw surfaced an automatic `Exec completed` runtime message. Claw incorrectly treated that system hint as if the user had already been proactively updated. The user corrected this. The correct behavior is stricter: when any background Codex task exits, Claw must immediately send a formal human-written status update with the required fields, rather than waiting for the user to ask and rather than relying on runtime-generated notifications.

### Suggested Action
Preserve an explicit rule in long-term memory: `Exec completed`, commit-trigger notices, and other system/runtime events are signals for Claw to act, not substitutes for Claw's own status report.

### Metadata
- Source: user_feedback
- Related Files: MEMORY.md, memory/2026-03-21.md, memory/dev-status.md
- Tags: codex, status-reporting, background-tasks, proactive-updates
- Pattern-Key: reporting.runtime-events-do-not-replace-user-updates
- Recurrence-Count: 5
- First-Seen: 2026-03-21
- Last-Seen: 2026-03-21
- Promoted: MEMORY.md
- See Also: repeated failure to convert runtime exit events into user-facing status updates on the same day; changing orchestration from exec background to sessions_spawn/ACP alone was insufficient without an explicit main-session completion summarizer; summarizer itself must be treated as a mandatory failure-sensitive step

### Resolution
- **Resolved**: 2026-03-21T09:50:00+01:00
- **Commit/PR**: pending
- **Notes**: Escalated from single correction to an explicit anti-assumption rule: no automatic event path counts as final user notification; Claw must always send the formal status update itself.

---

## [LRN-20260325-004] correction

**Logged**: 2026-03-25T11:58:48+01:00
**Priority**: high
**Status**: promoted
**Area**: infra

### Summary
For all future direct acpx/Codex CLI launches, explicitly pin the model to `gpt-5.4` instead of relying on ambiguous defaults.

### Details
The user explicitly required that future Codex CLI runs use `gpt-5.4 high`. A prior explanation had to distinguish between the main OpenClaw session being on `gpt-5.4` and direct acpx/Codex CLI launches not explicitly pinning the model. The correct behavior going forward is to remove that ambiguity by always passing the model explicitly on the direct acpx path and treating `high` as the default thinking depth per existing user preference.

### Suggested Action
Preserve this as a standing rule in long-term memory and tool notes: direct acpx/Codex CLI launches should include the explicit model pin for `openai-codex/gpt-5.4`, and execution updates should assume `high` unless the user overrides it.

### Metadata
- Source: user_feedback
- Related Files: MEMORY.md, TOOLS.md
- Tags: codex, acpx, model, defaults
- Pattern-Key: codexcli.pin-gpt54-high-by-default
- Recurrence-Count: 1
- First-Seen: 2026-03-25
- Last-Seen: 2026-03-25
- Promoted: MEMORY.md, TOOLS.md

### Resolution
- **Resolved**: 2026-03-25T11:58:48+01:00
- **Commit/PR**: pending
- **Notes**: Future direct acpx/Codex CLI launches should not rely on implicit defaults for the model.

---

## [LRN-20260325-003] correction

**Logged**: 2026-03-25T11:46:00+01:00
**Priority**: high
**Status**: promoted
**Area**: docs

### Summary
When reporting development verification to the user, do not surface raw pytest test function names; summarize them in plain human language instead.

### Details
The user explicitly corrected Claw after seeing raw test function names in a status explanation. The correct reporting style is to translate technical test identifiers into plain-language descriptions of what was verified and whether it passed. Raw function names can still be used internally or when debugging, but not in normal user-facing status updates unless the user explicitly asks for them.

### Suggested Action
Preserve this as a standing reporting rule in long-term memory: in development updates, summarize tests as intent + result, not function-name strings.

### Metadata
- Source: user_feedback
- Related Files: MEMORY.md
- Tags: reporting, testing, user-facing, style
- Pattern-Key: reporting.avoid-raw-test-function-names
- Recurrence-Count: 1
- First-Seen: 2026-03-25
- Last-Seen: 2026-03-25
- Promoted: MEMORY.md

### Resolution
- **Resolved**: 2026-03-25T11:46:00+01:00
- **Commit/PR**: pending
- **Notes**: Future verification updates should say what was tested in plain language and whether it passed.

---

## [LRN-20260325-002] correction

**Logged**: 2026-03-25T11:33:06+01:00
**Priority**: high
**Status**: promoted
**Area**: infra

### Summary
When switching from ACP runtime to direct acpx for Codex work, do not improvise acpx syntax; verify the real command shape from `acpx --help` and subcommand help first.

### Details
The user explicitly asked Claw to remember the correct acpx usage/syntax. Claw had attempted a direct acpx handoff with an incorrectly assembled command, which caused the process to print help text and exit with code 1 instead of starting the Codex task. The correct behavior is to treat acpx syntax as strict: inspect `acpx --help`, `acpx codex --help`, and if needed `acpx codex sessions --help`, then use the documented form. On this machine, the confirmed binary path is `/home/cn/.openclaw/tools/acpx/node_modules/.bin/acpx`, and a confirmed one-shot pattern is `acpx --cwd <worktree> --format quiet --approve-all codex exec "<prompt>"`.

### Suggested Action
Preserve the direct acpx gotcha in tool notes and long-term memory: global flags go before `codex`, and direct acpx launches should be based on current help output instead of memory or guessed syntax.

### Metadata
- Source: user_feedback
- Related Files: TOOLS.md, MEMORY.md
- Tags: acpx, codex, cli, syntax, fallback-path
- Pattern-Key: acpx.verify-real-syntax-before-direct-use
- Recurrence-Count: 1
- First-Seen: 2026-03-25
- Last-Seen: 2026-03-25
- Promoted: MEMORY.md, TOOLS.md

### Resolution
- **Resolved**: 2026-03-25T11:33:06+01:00
- **Commit/PR**: pending
- **Notes**: User explicitly asked that the correct acpx usage be remembered; promoted immediately to workspace memory/tool notes.

---
