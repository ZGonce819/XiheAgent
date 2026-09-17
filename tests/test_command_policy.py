from __future__ import annotations

import pytest

from backends.command_policy import evaluate_command
from config import approval_config


@pytest.mark.parametrize(
    ("command", "category"),
    [
        ("sudo rm -rf /", "privilege_escalation"),
        ("git reset --hard HEAD~1", "git_destructive"),
        ("git push --force origin main", "git_destructive"),
        ("cat ~/.ssh/id_rsa", "protected_secrets"),
        ("docker run --privileged image", "sandbox_escape"),
    ],
)
def test_immutable_deny_rules(command: str, category: str) -> None:
    result = evaluate_command(command, "sandbox")
    assert result.action == "deny"
    assert result.category == category


def test_local_and_sandbox_defaults_are_different(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backends.command_policy.preference_for",
        lambda backend, category: (
            approval_config.SANDBOX_DEFAULTS if backend == "sandbox" else approval_config.LOCAL_DEFAULTS
        )[category],
    )
    assert evaluate_command("npm run build", "local").action == "approval"
    assert evaluate_command("npm run build", "sandbox").action == "allow"


def test_compound_command_uses_strictest_segment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backends.command_policy.preference_for", lambda *_: "allow")
    assert evaluate_command("git status && git push origin main", "sandbox").action == "approval"
    assert evaluate_command("git status && sudo whoami", "sandbox").action == "deny"


def test_unknown_and_external_write_require_approval() -> None:
    assert evaluate_command("some-new-tool --apply", "sandbox").action == "approval"
    assert evaluate_command("curl -X POST https://example.com", "sandbox").action == "approval"


def test_settings_filter_unknown_and_locked_keys(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    path = tmp_path / "approval.json"
    monkeypatch.setattr(approval_config, "_path", lambda: path)
    saved = approval_config.update_command_approval_preferences(
        {"local": {"run_tests": "approval", "privilege_escalation": "allow"}, "sandbox": {"run_build": "allow", "unknown": "allow"}}
    )
    assert saved["local"]["run_tests"] == "approval"
    assert "privilege_escalation" not in saved["local"]
    assert "unknown" not in saved["sandbox"]
