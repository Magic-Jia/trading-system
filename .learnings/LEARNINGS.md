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
