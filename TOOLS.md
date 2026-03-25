# TOOLS.md - Local Notes

Skills define _how_ tools work. This file is for _your_ specifics — the stuff that's unique to your setup.

## What Goes Here

Things like:

- Camera names and locations
- SSH hosts and aliases
- Preferred voices for TTS
- Speaker/room names
- Device nicknames
- Anything environment-specific

## Examples

```markdown
### Cameras

- living-room → Main area, 180° wide angle
- front-door → Entrance, motion-triggered

### SSH

- home-server → 192.168.1.100, user: admin

### TTS

- Preferred voice: "Nova" (warm, slightly British)
- Default speaker: Kitchen HomePod
```

## Why Separate?

Skills are shared. Your setup is yours. Keeping them apart means you can update skills without losing your notes, and share skills without leaking your infrastructure.

---

### ACPX / Codex CLI

- acpx binary path: `/home/cn/.openclaw/tools/acpx/node_modules/.bin/acpx`
- 这台机器上 direct acpx（直接 acpx）可先用：
  - `"/home/cn/.openclaw/tools/acpx/node_modules/.bin/acpx" --help`
  - `"/home/cn/.openclaw/tools/acpx/node_modules/.bin/acpx" codex --help`
- 关键语法坑：`--cwd`、`--format`、`--approve-all` 这类是 **acpx 全局参数**，要放在 `codex` 前面，不要按 CLI 直觉乱塞到后面。
- 当前确认可用的一次性 direct acpx（直接 acpx）调用模板：
  - `"/home/cn/.openclaw/tools/acpx/node_modules/.bin/acpx" --cwd <worktree> --format quiet --approve-all --model openai-codex/gpt-5.4 codex exec "<prompt>"`
- Codex CLI（代码代理命令行）默认要求：模型固定 `openai-codex/gpt-5.4`，思考深度（thinking level，思考深度）默认按老板规则视为 `high`。
- 如果要先看帮助再下手，顺序是：先 `acpx --help`，再 `acpx codex --help`，再 `acpx codex sessions --help`。
- 原则：切 direct acpx（直接 acpx）路径时，优先按帮助输出的真实语法执行，不要凭记忆手写 session（会话）子命令格式。

Add whatever helps you do your job. This is your cheat sheet.
