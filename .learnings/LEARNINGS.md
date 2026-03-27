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

## [LRN-20260326-010] correction

**Logged**: 2026-03-26T13:08:00+01:00
**Priority**: high
**Status**: promoted
**Area**: messaging

### Summary
When a Codex launch fails, proactively report it immediately and restart or choose a fallback right away; do not leave the task idle until the user asks.

### Details
The user asked for a retrospective on why a failed Package 2 launch was neither proactively reported nor immediately relaunched. Claw already had a general rule for launch failures, but in practice relied on stale state and did not execute the required response loop. The correct behavior is: verify launch success, and if the executor did not actually start, send the fixed failure update immediately and then perform one of the allowed next actions without waiting for another user nudge.

### Suggested Action
Treat launch failure handling as a two-step atomic process: (1) immediate user-facing fixed-template failure update, (2) immediate recovery action or explicit no-executor declaration.

### Metadata
- Source: user_feedback
- Related Files: MEMORY.md
- Tags: startup-failure, reporting, recovery, codex
- Pattern-Key: launch-failure.report-then-recover-immediately
- Recurrence-Count: 1
- First-Seen: 2026-03-26
- Last-Seen: 2026-03-26
- Promoted: MEMORY.md

### Resolution
- **Resolved**: 2026-03-26T13:08:00+01:00
- **Commit/PR**: pending
- **Notes**: A launch failure is not complete until both the report and the recovery decision have been executed.

---

## [LRN-20260326-009] correction

**Logged**: 2026-03-26T09:06:00+01:00
**Priority**: high
**Status**: promoted
**Area**: reporting

### Summary
When reporting completed coding work, always include the total Codex runtime duration; do not omit it just because the run already finished.

### Details
The user corrected Claw after a completion update omitted the runtime duration. Claw already had the general rule to report Codex runtime, but missed it in a finished-task status. The duration requirement applies both while running and after completion.

### Suggested Action
Every completion update should include a field like `本次 Codex 运行时长：XX 分 YY 秒`, even when the run has already ended.

### Metadata
- Source: user_feedback
- Related Files: MEMORY.md
- Tags: reporting, codex, runtime-duration, completion-updates
- Pattern-Key: reporting.always-include-runtime-duration-after-completion
- Recurrence-Count: 1
- First-Seen: 2026-03-26
- Last-Seen: 2026-03-26
- Promoted: MEMORY.md

### Resolution
- **Resolved**: 2026-03-26T09:06:00+01:00
- **Commit/PR**: pending
- **Notes**: Duration should appear in both in-flight and completed development updates.

---

## [LRN-20260326-008] correction

**Logged**: 2026-03-26T07:23:00+01:00
**Priority**: high
**Status**: promoted
**Area**: reporting

### Summary
Future development progress reports must include the Codex runtime duration for the current run.

### Details
The user explicitly required that progress reports also state how long Codex ran for the current execution. This should be reported either as total elapsed runtime after completion or as current elapsed runtime when still running.

### Suggested Action
In future coding updates, include a duration field such as: `本次 Codex 运行时长：XX 分 YY 秒` or `当前已运行：XX 分钟`.

### Metadata
- Source: user_feedback
- Related Files: MEMORY.md
- Tags: reporting, runtime-duration, codex
- Pattern-Key: reporting.include-codex-runtime-duration
- Recurrence-Count: 1
- First-Seen: 2026-03-26
- Last-Seen: 2026-03-26
- Promoted: MEMORY.md

### Resolution
- **Resolved**: 2026-03-26T07:23:00+01:00
- **Commit/PR**: pending
- **Notes**: Future progress reports should include how long Codex ran for that run.

---

## [LRN-20260326-007] correction

**Logged**: 2026-03-26T05:31:00+01:00
**Priority**: high
**Status**: promoted
**Area**: messaging

### Summary
When a message arrives with reply context, prioritize the replied-to message over short imperative text in the new message body.

### Details
The user pointed out that a reply to an earlier question looked like it was being treated as a fresh "continue development" instruction. Even if the latest body contains phrases like "继续" or "继续开发", reply context must come first. If the reply target and the new body create ambiguity, the assistant should slow down, explain the ambiguity, or ask a clarifying question instead of auto-continuing a background coding stream.

### Suggested Action
Before acting on terse follow-up messages, check whether `reply_to_id` is present and whether the replied-to message changes the intended meaning. Treat reply-target context as the primary cue.

### Metadata
- Source: user_feedback
- Related Files: MEMORY.md
- Tags: messaging, reply-context, intent-resolution
- Pattern-Key: messaging.prioritize-reply-context-over-terse-body
- Recurrence-Count: 1
- First-Seen: 2026-03-26
- Last-Seen: 2026-03-26
- Promoted: MEMORY.md

### Resolution
- **Resolved**: 2026-03-26T05:31:00+01:00
- **Commit/PR**: pending
- **Notes**: Reply context should override shallow keyword matching on the newest message body.

---

## [LRN-20260326-006] correction

**Logged**: 2026-03-26T04:22:00+01:00
**Priority**: high
**Status**: promoted
**Area**: messaging

### Summary
Never send internal working notes, tool-planning text, or draft English process commentary to the user.

### Details
A user received a leaked internal planning-style message in English instead of the intended polished reply. This is not acceptable for user-facing communication. Internal scratch text, tool-call planning, or meta commentary must stay internal; the user should only see the final cleaned answer.

### Suggested Action
Before sending any reply, ensure the content is a polished user-facing message and does not contain internal workflow notes, tool-selection reasoning, or draft self-talk.

### Metadata
- Source: user_feedback
- Related Files: MEMORY.md
- Tags: messaging, leak, internal-notes, polish
- Pattern-Key: messaging.never-leak-internal-planning-text
- Recurrence-Count: 1
- First-Seen: 2026-03-26
- Last-Seen: 2026-03-26
- Promoted: MEMORY.md

### Resolution
- **Resolved**: 2026-03-26T04:22:00+01:00
- **Commit/PR**: pending
- **Notes**: User-facing replies must contain only the final answer, never internal planning text.

---

## [LRN-20260326-005] correction

**Logged**: 2026-03-26T04:00:00+01:00
**Priority**: high
**Status**: promoted
**Area**: reporting

### Summary
Future development progress reports must include code/file delta counts, not just narrative status.

### Details
The user explicitly required that development progress updates also state how many lines were added/removed and how many files were added/removed. This should be included in user-facing progress reports alongside the plain-language summary, verification result, Codex status, model, and thinking level.

### Suggested Action
In future coding updates, include a small delta summary such as: `代码：+X / -Y；文件：新增 N / 删除 M / 修改 K`.

### Metadata
- Source: user_feedback
- Related Files: MEMORY.md
- Tags: reporting, diff, progress-updates
- Pattern-Key: reporting.include-code-and-file-deltas
- Recurrence-Count: 1
- First-Seen: 2026-03-26
- Last-Seen: 2026-03-26
- Promoted: MEMORY.md

### Resolution
- **Resolved**: 2026-03-26T04:00:00+01:00
- **Commit/PR**: pending
- **Notes**: Future reports should include code and file delta counts in addition to plain-language explanation.

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

## [LRN-20260327-001] best_practice

**Logged**: 2026-03-27T02:31:00+01:00
**Priority**: high
**Status**: pending
**Area**: infra

### Summary
A long-running direct acpx / Codex execution with no new commit and no fresh verification for hours must be treated as stalled and explicitly terminated instead of being left to hang.

### Details
Package 4 Task 2 entered a bad state where the direct acpx executor stayed alive for roughly seven hours, but produced no commit, no fresh verification result, and no user-facing milestone. The correct handling is not to keep waiting just because the process still exists. Treat the run as stalled, report that clearly, preserve the working tree diff, terminate the executor, and restart from the saved working state with a prompt that demands an early concrete milestone.

### Suggested Action
For future long-running coding tasks, treat "active process but no milestone for too long" as a failure mode. Restart from the preserved diff and require the next run to reach either a focused failing-test result or a green verification result quickly.

### Metadata
- Source: simplify-and-harden
- Related Files: memory/dev-status.md, .worktrees/codex-b1-derivatives/memory/dev-status.md, .learnings/LEARNINGS.md
- Tags: codex, stalled-executor, recovery, reporting
- Pattern-Key: restart.stalled-direct-acpx-from-preserved-diff
- Recurrence-Count: 1
- First-Seen: 2026-03-27
- Last-Seen: 2026-03-27

---
