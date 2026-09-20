"""Agent 技能工具的会话级本地目录隔离测试。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


class Store:
    def __init__(self, *, local_work_dir: str | None, stale_binding: str) -> None:
        self.local_work_dir = local_work_dir
        self.stale_binding = stale_binding

    def get_thread(self, _thread_id: str) -> dict[str, Any]:
        return {
            "project_name": "Xihe",
            "backend_type": "local",
            "local_work_dir": self.local_work_dir,
        }

    def get_project_folder(self, _project_name: str) -> dict[str, str]:
        return {"local_folder": self.stale_binding}


def test_list_skills_does_not_report_stale_project_binding(
    skills_root: Path,
    tmp_path: Path,
    make_skill,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools import skill_tools

    stale = tmp_path / "stale"
    stale.mkdir()
    make_skill(stale / ".xihe-agent" / "skills" / "stale-only", "stale-only")
    store = Store(local_work_dir=None, stale_binding=str(stale))
    monkeypatch.setattr("agent.graph.graph.get_store", lambda: store)
    monkeypatch.setattr(skill_tools, "get_runtime_thread_id", lambda: "thread-1")

    result = skill_tools.list_skills.invoke({"scope": "all"})

    assert all(item["id"] != "stale-only" for item in result["skills"])
    assert all(item["scope"] != "local" for item in result["skills"])


def test_list_skills_reads_only_current_thread_folder(
    skills_root: Path,
    tmp_path: Path,
    make_skill,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools import skill_tools

    current = tmp_path / "current"
    stale = tmp_path / "stale"
    current.mkdir()
    stale.mkdir()
    make_skill(current / ".xihe-agent" / "skills" / "current-only", "current-only")
    make_skill(stale / ".xihe-agent" / "skills" / "stale-only", "stale-only")
    store = Store(local_work_dir=str(current), stale_binding=str(stale))
    monkeypatch.setattr("agent.graph.graph.get_store", lambda: store)
    monkeypatch.setattr(skill_tools, "get_runtime_thread_id", lambda: "thread-1")

    result = skill_tools.list_skills.invoke({"scope": "all"})
    local_ids = [item["id"] for item in result["skills"] if item["scope"] == "local"]

    assert local_ids == ["current-only"]


def test_create_skill_writes_to_current_thread_folder(
    skills_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools import skill_tools

    current = tmp_path / "current"
    stale = tmp_path / "stale"
    current.mkdir()
    stale.mkdir()
    store = Store(local_work_dir=str(current), stale_binding=str(stale))
    monkeypatch.setattr("agent.graph.graph.get_store", lambda: store)
    monkeypatch.setattr(skill_tools, "get_runtime_thread_id", lambda: "thread-1")

    result = skill_tools.create_skill.invoke(
        {
            "name": "thread-skill",
            "description": "Only for this thread folder",
            "instructions": "# Steps\n\nUse the current folder.",
            "scope": "local",
        }
    )

    assert result["scope"] == "local"
    assert (
        current / ".xihe-agent" / "skills" / "thread-skill" / "SKILL.md"
    ).is_file()
    assert not (stale / ".xihe-agent" / "skills" / "thread-skill").exists()
