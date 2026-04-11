from pathlib import Path

import pytest

from trading_system.devtools import commit_notifications
from trading_system.devtools.commit_notifications import CommitNotification, build_notification_text, build_openclaw_command
from trading_system.devtools.install_commit_hook import (
    build_install_commands,
    resolve_hooks_dir,
    verify_installed_hook,
)


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


def test_build_openclaw_child_env_preserves_existing_env_token():
    env = {
        "OPENCLAW_GATEWAY_TOKEN": "from-env",
        "OPENCLAW_CONFIG_PATH": "/tmp/custom-openclaw.json",
    }

    child_env = commit_notifications.build_openclaw_child_env(env)

    assert child_env["OPENCLAW_GATEWAY_TOKEN"] == "from-env"
    assert child_env["OPENCLAW_CONFIG_PATH"] == "/tmp/custom-openclaw.json"


def test_build_openclaw_child_env_loads_token_from_config_path_when_missing(tmp_path: Path):
    config_path = tmp_path / "openclaw.json"
    config_path.write_text('{"gateway":{"auth":{"token":"from-config"}}}', encoding="utf-8")

    child_env = commit_notifications.build_openclaw_child_env({"OPENCLAW_CONFIG_PATH": str(config_path)})

    assert child_env["OPENCLAW_GATEWAY_TOKEN"] == "from-config"


def test_build_openclaw_child_env_tolerates_malformed_or_missing_config(tmp_path: Path):
    malformed_path = tmp_path / "malformed.json"
    malformed_path.write_text("{", encoding="utf-8")
    missing_path = tmp_path / "missing.json"

    malformed_env = commit_notifications.build_openclaw_child_env({"OPENCLAW_CONFIG_PATH": str(malformed_path)})
    missing_env = commit_notifications.build_openclaw_child_env({"OPENCLAW_CONFIG_PATH": str(missing_path)})

    assert "OPENCLAW_GATEWAY_TOKEN" not in malformed_env
    assert "OPENCLAW_GATEWAY_TOKEN" not in missing_env


def test_send_commit_notification_passes_built_env_into_popen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    def fake_popen(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(
        commit_notifications,
        "collect_head_commit",
        lambda repo_root: CommitNotification(
            repo_name=repo_root.name,
            branch="main",
            short_sha="abc1234",
            subject="test commit",
        ),
    )
    monkeypatch.setattr(commit_notifications, "_resolve_log_path", lambda _repo_root: tmp_path / "notify.log")
    monkeypatch.setattr(commit_notifications.subprocess, "Popen", fake_popen)

    env = {
        "OPENCLAW_COMMIT_NOTIFY": "1",
        "OPENCLAW_GATEWAY_TOKEN": "from-env",
        "OPENCLAW_CONFIG_PATH": "/tmp/custom-openclaw.json",
    }
    result = commit_notifications.send_commit_notification(tmp_path, env=env)

    assert result == 0
    assert captured["args"]
    passed_env = captured["kwargs"]["env"]
    assert passed_env["OPENCLAW_GATEWAY_TOKEN"] == "from-env"
    assert passed_env["OPENCLAW_CONFIG_PATH"] == "/tmp/custom-openclaw.json"


def test_build_install_commands_uses_worktree_hooks_for_multi_worktree_repos():
    commands = build_install_commands(
        repo_root=Path("/tmp/trading-system-commit-trigger"),
        has_multiple_worktrees=True,
        worktree_config_enabled=False,
    )

    assert commands == [["git", "config", "--local", "core.hooksPath", ".githooks"]]


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
            ".githooks",
        ]
    ]


def test_resolve_hooks_dir_interprets_relative_paths_from_repo_root():
    repo_root = Path("/tmp/trading-system-commit-trigger")

    assert resolve_hooks_dir(repo_root, ".githooks") == repo_root / ".githooks"


def test_verify_installed_hook_rejects_missing_hooks_path(tmp_path: Path):
    hook_dir = tmp_path / ".githooks"
    hook_dir.mkdir()
    hook_path = hook_dir / "post-commit"
    hook_path.write_text("#!/bin/sh\n", encoding="utf-8")
    hook_path.chmod(0o755)

    with pytest.raises(RuntimeError, match=r"core\.hooksPath is not configured"):
        verify_installed_hook(tmp_path, configured_hooks_path=None)


def test_verify_installed_hook_rejects_unexpected_hooks_path(tmp_path: Path):
    hook_dir = tmp_path / ".githooks"
    hook_dir.mkdir()
    hook_path = hook_dir / "post-commit"
    hook_path.write_text("#!/bin/sh\n", encoding="utf-8")
    hook_path.chmod(0o755)

    with pytest.raises(RuntimeError, match="does not point to"):
        verify_installed_hook(tmp_path, configured_hooks_path="/tmp/elsewhere/.githooks")
