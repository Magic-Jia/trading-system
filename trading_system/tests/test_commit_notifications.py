from pathlib import Path

from trading_system.devtools.commit_notifications import (
    CommitNotification,
    build_notification_text,
    build_openclaw_command,
)
from trading_system.devtools.install_commit_hook import build_install_commands


def test_build_notification_text_redacts_sensitive_fragments_and_truncates_subject():
    notification = CommitNotification(
        repo_name="trading-system-commit-trigger",
        branch="feature/token=super-secret-value",
        short_sha="abc1234",
        subject=(
            "feat: add automatic progress notification with "
            "api_key=very-secret-value and a very long explanation that should "
            "be shortened before it is sent to openclaw"
        ),
    )

    text = build_notification_text(notification, max_subject_length=72)

    assert "super-secret-value" not in text
    assert "very-secret-value" not in text
    assert "[REDACTED]" in text
    assert text.startswith(
        "Dev commit in trading-system-commit-trigger "
        "[feature/token=[REDACTED]] abc1234: "
        "feat: add automatic progress notification with api_key=[REDACTED]"
    )
    assert text.endswith("...")


def test_build_notification_text_uses_detached_head_label():
    notification = CommitNotification(
        repo_name="trading-system-commit-trigger",
        branch=None,
        short_sha="abc1234",
        subject="chore: verify hook path",
    )

    assert (
        build_notification_text(notification)
        == "Dev commit in trading-system-commit-trigger [detached] abc1234: chore: verify hook path"
    )


def test_build_openclaw_command_uses_immediate_mode_and_timeout():
    command = build_openclaw_command("hello world", mode="now", timeout_ms=1500)

    assert command == [
        "openclaw",
        "system",
        "event",
        "--text",
        "hello world",
        "--mode",
        "now",
        "--timeout",
        "1500",
    ]


def test_build_install_commands_uses_worktree_hooks_for_multi_worktree_repos():
    commands = build_install_commands(
        repo_root=Path("/tmp/trading-system-commit-trigger"),
        has_multiple_worktrees=True,
        worktree_config_enabled=False,
    )

    assert commands == [
        ["git", "config", "--local", "extensions.worktreeConfig", "true"],
        [
            "git",
            "config",
            "--worktree",
            "core.hooksPath",
            "/tmp/trading-system-commit-trigger/.githooks",
        ],
    ]


def test_build_install_commands_uses_local_config_for_single_worktree_repos():
    commands = build_install_commands(
        repo_root=Path("/tmp/trading-system-commit-trigger"),
        has_multiple_worktrees=False,
        worktree_config_enabled=False,
    )

    assert commands == [
        [
            "git",
            "config",
            "--local",
            "core.hooksPath",
            "/tmp/trading-system-commit-trigger/.githooks",
        ]
    ]
