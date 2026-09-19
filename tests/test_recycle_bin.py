"""Recycle-bin behavior for model deletes and edited-message rollback."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from backends.local_shell import LocalShellBackend
from services.repo_rollback import rollback_run
from utils.recycle_bin import move_to_recycle_bin


def test_backend_delete_moves_nested_file_to_workspace_recyclebin(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    backend = LocalShellBackend(workspace=workspace)
    source = workspace / "projects" / "123" / "src" / "1.py"
    source.parent.mkdir(parents=True)
    source.write_text("print(1)", encoding="utf-8")

    result = backend.delete("/projects/123/src/1.py")

    assert result.error is None
    assert not source.exists()
    recycled = list((workspace / "recyclebin").glob("*/123/src/1.py"))
    assert len(recycled) == 1
    assert recycled[0].read_text(encoding="utf-8") == "print(1)"


def test_local_folder_delete_uses_global_workspace_recyclebin(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    project = tmp_path / "selected" / "123"
    backend = LocalShellBackend(
        workspace=project,
        local_folder_mode=True,
        global_workspace_root=workspace,
    )
    source = project / "src" / "1.py"
    source.parent.mkdir(parents=True)
    source.write_text("print(1)", encoding="utf-8")

    result = backend.delete("/projects/src/1.py")

    assert result.error is None
    assert not source.exists()
    assert len(list((workspace / "recyclebin").glob("*/123/src/1.py"))) == 1


def test_recyclebin_collision_uses_next_timestamp(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    project = workspace / "projects" / "123"
    source = project / "src" / "1.py"
    source.parent.mkdir(parents=True)
    source.write_text("new", encoding="utf-8")
    timestamp = datetime(2026, 9, 19, 9, 56, 13)
    occupied = workspace / "recyclebin" / "2026-09-19-09-56-13" / "123" / "src" / "1.py"
    occupied.parent.mkdir(parents=True)
    occupied.write_text("old", encoding="utf-8")

    destination = move_to_recycle_bin(
        source,
        project_root=project,
        workspace_root=workspace,
        now=timestamp,
    )

    assert destination == workspace / "recyclebin" / "2026-09-19-09-56-14" / "123" / "src" / "1.py"
    assert destination.read_text(encoding="utf-8") == "new"
    assert occupied.read_text(encoding="utf-8") == "old"


class _RollbackStore:
    def __init__(self, event_path: str) -> None:
        self.event_path = event_path

    def get_mapping_by_repo(self, _repo_url: str) -> dict[str, str]:
        return {"workspace_path": "projects/123"}

    def list_run_events(self, _thread_id: str) -> list[dict[str, Any]]:
        return [{
            "status": "completed",
            "detail": json.dumps({
                "tool": "write_file",
                "args": {"file_path": self.event_path},
            }),
        }]


@pytest.mark.parametrize("status", ["completed", "error", "rejected", "in_progress"])
def test_rollback_plain_folder_only_recycles_successful_creations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str,
) -> None:
    from config.app_config import get_settings

    workspace = tmp_path / "workspace"
    project = workspace / "projects" / "123"
    generated = project / "src" / "1.py"
    generated.parent.mkdir(parents=True)
    generated.write_text("print(1)", encoding="utf-8")
    unrelated = project / "user.txt"
    unrelated.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(get_settings(), "workspace_root", workspace)
    store = _RollbackStore("/projects/123/src/1.py")
    events = store.list_run_events("thread-1")
    events[0]["status"] = status
    monkeypatch.setattr(store, "list_run_events", lambda _: events)

    changed = rollback_run(
        "https://gitee.com/example/123.git", {"thread_id": "thread-1"}, store,
    )

    assert changed is (status == "completed")
    assert generated.exists() is (status != "completed")
    assert unrelated.read_text(encoding="utf-8") == "keep"
    if changed:
        recycled = list((workspace / "recyclebin").glob("*/123/src/1.py"))
        assert len(recycled) == 1
        assert recycled[0].read_text(encoding="utf-8") == "print(1)"


def test_rollback_recycles_generated_untracked_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from config.app_config import get_settings

    workspace = tmp_path / "workspace"
    repo = workspace / "projects" / "123"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    generated = repo / "src" / "1.py"
    generated.parent.mkdir()
    generated.write_text("print(1)", encoding="utf-8")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    monkeypatch.setattr(get_settings(), "workspace_root", workspace)

    rolled_back = rollback_run(
        "https://gitee.com/example/123.git",
        {"thread_id": "thread-1", "base_commit": head, "head_commit": head},
        _RollbackStore("/projects/123/src/1.py"),
    )

    assert rolled_back is True
    assert not generated.exists()
    assert len(list((workspace / "recyclebin").glob("*/123/src/1.py"))) == 1


def test_rollback_recycles_file_added_by_agent_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from config.app_config import get_settings

    workspace = tmp_path / "workspace"
    repo = workspace / "projects" / "123"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    tracked = repo / "README.md"
    tracked.write_text("base", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    generated = repo / "src" / "1.py"
    generated.parent.mkdir()
    generated.write_text("print(1)", encoding="utf-8")
    subprocess.run(["git", "add", "src/1.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "agent"], cwd=repo, check=True, capture_output=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    monkeypatch.setattr(get_settings(), "workspace_root", workspace)

    rolled_back = rollback_run(
        "https://gitee.com/example/123.git",
        {"thread_id": "thread-1", "base_commit": base, "head_commit": head},
        _RollbackStore("/projects/123/src/1.py"),
    )

    assert rolled_back is True
    assert not generated.exists()
    assert len(list((workspace / "recyclebin").glob("*/123/src/1.py"))) == 1
    current = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    assert current == base
