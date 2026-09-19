"""技能面板必须按当前会话目录读取本地技能。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


def test_dashboard_skills_prefers_current_folder_over_stale_project_binding(
    skills_root: Path,
    make_skill,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dashboard import dashboard_skills

    current = tmp_path / "current"
    stale = tmp_path / "stale"
    current.mkdir()
    stale.mkdir()
    make_skill(current / ".xihe-agent" / "skills" / "current-only", "current-only")
    make_skill(stale / ".xihe-agent" / "skills" / "stale-only", "stale-only")

    class Store:
        def get_project_folder(self, _project_name: str) -> dict[str, Any]:
            return {"local_folder": str(stale)}

    monkeypatch.setattr("agent.graph.graph.get_store", lambda: Store())

    payload = dashboard_skills(
        project_name="Xihe",
        backend_type="local",
        local_folder=str(current),
    )

    local_ids = [item["id"] for item in payload["items"] if item["scope"] == "local"]
    assert local_ids == ["current-only"]
    assert payload["scopes"][2]["directory"] == str(
        current.resolve() / ".xihe-agent" / "skills"
    )
    assert payload["writableScopes"] == ["global", "project_set", "local"]


def test_dashboard_skills_does_not_fall_back_when_current_folder_is_missing(
    skills_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dashboard import dashboard_skills

    stale = tmp_path / "stale"
    stale.mkdir()

    class Store:
        def get_project_folder(self, _project_name: str) -> dict[str, Any]:
            return {"local_folder": str(stale)}

    monkeypatch.setattr("agent.graph.graph.get_store", lambda: Store())

    payload = dashboard_skills(
        project_name="Xihe",
        backend_type="local",
        local_folder=str(tmp_path / "missing"),
    )

    assert all(item["scope"] != "local" for item in payload["items"])
    assert payload["scopes"][2]["available"] is False
    assert payload["writableScopes"] == ["global", "project_set"]


def test_session_without_local_folder_ignores_stale_project_binding(
    skills_root: Path,
    tmp_path: Path,
    make_skill,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.dashboard import dashboard_skills

    stale = tmp_path / "stale"
    stale.mkdir()
    make_skill(stale / ".xihe-agent" / "skills" / "stale-only", "stale-only")

    class Store:
        def get_project_folder(self, _project_name: str) -> dict[str, Any]:
            return {"local_folder": str(stale)}

    monkeypatch.setattr("agent.graph.graph.get_store", lambda: Store())

    payload = dashboard_skills(
        project_name="Xihe",
        backend_type="local",
        session_scoped=True,
    )

    assert all(item["scope"] != "local" for item in payload["items"])
    assert payload["scopes"][2]["available"] is False
    assert payload["writableScopes"] == ["global", "project_set"]
