"""主任务在构建 Agent 前准备本地 Git 仓库。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from deepagents.backends.sandbox import ExecuteResponse


class FakeStore:
    def __init__(self, local_work_dir: str | None = None) -> None:
        self.saved: dict[str, Any] | None = None
        self.local_work_dir = local_work_dir

    def get_thread(self, _thread_id: str) -> dict[str, Any]:
        return {"local_work_dir": self.local_work_dir}

    def upsert_mapping(self, **kwargs: Any) -> None:
        self.saved = kwargs


def _install(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    clone_exit_code: int = 0,
    local_work_dir: str | None = None,
) -> tuple[FakeStore, list[tuple[str, str]]]:
    from agent.runtime import task_router
    from config.app_config import get_settings

    store = FakeStore(local_work_dir)
    calls: list[tuple[str, str]] = []
    settings = get_settings()
    monkeypatch.setattr(settings, "workspace_root", tmp_path)
    monkeypatch.setattr(settings, "projects_dir", tmp_path / "projects")
    monkeypatch.setattr(task_router, "get_store", lambda: store)
    monkeypatch.setattr("agent.runtime._helpers.get_store", lambda: store)
    monkeypatch.setattr(task_router, "record_event", lambda *args, **kwargs: None)

    def fake_run(self: Any, command: str, cwd: str = ".", timeout: int = 300) -> Any:
        calls.append((command, cwd))
        if command.startswith("git clone") and clone_exit_code == 0:
            (tmp_path / "projects" / "sample" / ".git").mkdir(parents=True)
        if command == "git init" and clone_exit_code == 0 and local_work_dir:
            (Path(local_work_dir) / ".git").mkdir(parents=True)
        stdout = "origin/main\n" if command.startswith("git symbolic-ref") else ""
        return SimpleNamespace(
            exit_code=clone_exit_code,
            stdout=stdout,
            stderr="clone failed" if clone_exit_code else "",
        )

    monkeypatch.setattr("backends.local_shell.LocalShellBackend.run", fake_run)
    return store, calls


def test_missing_git_directory_is_cloned_before_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent.runtime.task_router import _ensure_local_repository

    store, calls = _install(monkeypatch, tmp_path)
    repo = SimpleNamespace(repo="sample", clone_url="https://gitee.com/acme/sample.git")

    target = _ensure_local_repository(thread_id="t1", repo=repo)

    assert target == (tmp_path / "projects" / "sample").resolve()
    assert calls == [
        ("git clone https://gitee.com/acme/sample.git sample", "projects")
    ]
    assert store.saved is not None


def test_existing_git_directory_is_not_cloned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent.runtime.task_router import _ensure_local_repository

    _, calls = _install(monkeypatch, tmp_path)
    (tmp_path / "projects" / "sample" / ".git").mkdir(parents=True)
    repo = SimpleNamespace(repo="sample", clone_url="https://gitee.com/acme/sample.git")

    _ensure_local_repository(thread_id="t1", repo=repo)

    assert calls == []


def test_clone_failure_continues_without_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent.runtime.task_router import _ensure_local_repository

    _, calls = _install(monkeypatch, tmp_path, clone_exit_code=128)
    repo = SimpleNamespace(repo="sample", clone_url="https://gitee.com/acme/sample.git")

    target = _ensure_local_repository(thread_id="t1", repo=repo)

    assert target is None
    assert len(calls) == 1


def test_unavailable_repository_prompt_disables_repository_operations() -> None:
    from agent.runtime._helpers import build_agent_user_content, build_plan_user_content

    agent_content = build_agent_user_content(
        repo_url="https://gitee.com/acme/missing.git",
        task_kind="qa",
        prompt="解释一下 Python 列表",
        repository_available=False,
    )
    plan_content = build_plan_user_content(
        repo_url="https://gitee.com/acme/missing.git",
        prompt="修改登录逻辑",
        repository_available=False,
    )

    for content in (agent_content, plan_content):
        assert "无仓库模式" in content
        assert "不要执行 Git 命令" in content
        assert "当前仓库地址不可用" in content


class FakeSandboxBackend:
    def __init__(self, responses: list[tuple[int, str]]) -> None:
        self.responses = list(responses)
        self.calls: list[str] = []

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        self.calls.append(command)
        exit_code, output = self.responses.pop(0)
        return ExecuteResponse(output=output, exit_code=exit_code, truncated=False)


def test_sandbox_clones_into_workspace_projects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.runtime import task_router

    backend = FakeSandboxBackend([(1, ""), (0, ""), (0, "cloned"), (0, "")])
    monkeypatch.setattr("backends.opensandbox.OpenSandboxBackend", FakeSandboxBackend)
    monkeypatch.setattr("server.ensure_backend_for_thread", lambda *args, **kwargs: backend)
    monkeypatch.setattr(task_router, "record_event", lambda *args, **kwargs: None)
    repo = SimpleNamespace(repo="sample", clone_url="https://gitee.com/acme/sample.git")

    target = task_router._ensure_sandbox_repository(thread_id="t1", repo=repo)

    assert target == "/workspace/projects/sample"
    assert backend.calls == [
        "test -d /workspace/projects/sample/.git",
        "mkdir -p /workspace/projects",
        "git clone https://gitee.com/acme/sample.git /workspace/projects/sample",
        "test -d /workspace/projects/sample/.git",
    ]


def test_sandbox_clone_failure_continues_without_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.runtime import task_router

    backend = FakeSandboxBackend(
        [(1, ""), (0, ""), (128, "repository not found"), (1, "")]
    )
    monkeypatch.setattr("backends.opensandbox.OpenSandboxBackend", FakeSandboxBackend)
    monkeypatch.setattr("server.ensure_backend_for_thread", lambda *args, **kwargs: backend)
    monkeypatch.setattr(task_router, "record_event", lambda *args, **kwargs: None)
    repo = SimpleNamespace(repo="missing", clone_url="https://gitee.com/acme/missing.git")

    target = task_router._ensure_sandbox_repository(thread_id="t1", repo=repo)

    assert target is None
    assert any(command.startswith("git clone ") for command in backend.calls)


def test_sandbox_repository_path_is_injected_into_prompt() -> None:
    from agent.runtime._helpers import build_agent_user_content

    content = build_agent_user_content(
        repo_url="https://gitee.com/acme/sample.git",
        task_kind="analysis",
        prompt="分析项目",
        repository_path="/workspace/projects/sample",
    )

    assert "仓库已准备在 `/workspace/projects/sample`" in content
    assert "以本条路径为准" in content


def test_sandbox_repository_memory_uses_real_workspace_path() -> None:
    from agent.memory.repo import build_initial_repo_memory

    repo = SimpleNamespace(
        owner="acme",
        repo="sample",
        clone_url="https://gitee.com/acme/sample.git",
    )

    content = build_initial_repo_memory(
        repo=repo,
        project_dir="projects/sample",
        sandbox_mode=True,
    )

    assert "本地目录：/workspace/projects/sample" in content
    assert "命令目录：/workspace/projects/sample" in content
    assert "execute` 默认目录为 `/workspace`" in content


def test_local_folder_fetches_and_checks_out_default_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent.runtime.task_router import _ensure_local_repository

    local_folder = tmp_path / "selected"
    local_folder.mkdir()
    store, calls = _install(
        monkeypatch,
        tmp_path,
        local_work_dir=str(local_folder),
    )
    repo = SimpleNamespace(repo="sample", clone_url="https://gitee.com/acme/sample.git")

    target = _ensure_local_repository(thread_id="t1", repo=repo)

    assert target == local_folder.resolve()
    assert ("git init", ".") in calls
    assert ("git fetch origin", ".") in calls
    assert ("git checkout -B main --track origin/main", ".") in calls
    assert store.saved is None
