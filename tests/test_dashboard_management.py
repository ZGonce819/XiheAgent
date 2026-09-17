from __future__ import annotations

from types import SimpleNamespace

from api import dashboard
from config import automation_config


def test_automation_crud_uses_persisted_contract(tmp_path, monkeypatch):
    storage = tmp_path / "automations.json"
    monkeypatch.setattr(automation_config, "_automations_path", lambda: storage)

    created = dashboard.dashboard_create_schedule(
        {
            "name": "每日检查",
            "prompt": "检查依赖更新",
            "schedule": "0 9 * * *",
            "repo": "xihe/agent",
            "model_id": "__builtin__",
        }
    )
    assert created["enabled"] is True
    assert dashboard.dashboard_schedules()[0]["name"] == "每日检查"

    updated = dashboard.dashboard_update_schedule(created["id"], {"enabled": False})
    assert updated["enabled"] is False

    dashboard.dashboard_delete_schedule(created["id"])
    assert dashboard.dashboard_schedules() == []


def test_skills_endpoint_discovers_skill_metadata(tmp_path, monkeypatch):
    skill_dir = tmp_path / "skills" / "review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: code-review\ndescription: Review changed code.\n---\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        dashboard,
        "get_settings",
        lambda: SimpleNamespace(skills_dir=tmp_path / "skills"),
    )

    payload = dashboard.dashboard_skills()
    assert payload["available"] is True
    assert payload["items"] == [
        {
            "id": "review",
            "name": "code-review",
            "description": "Review changed code.",
            "virtualPath": "/skills/review/SKILL.md",
            "updatedAt": payload["items"][0]["updatedAt"],
        }
    ]
