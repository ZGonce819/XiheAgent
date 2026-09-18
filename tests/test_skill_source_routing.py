"""技能源路由：不同运行环境各挂载哪几层。

这是需求①的落点——沙箱会话属于项目集合时要同时挂上项目集合层，不属于时
只挂全局层。历史上沙箱走的是 ``/skills-global/`` 这类虚拟路径，而沙箱 backend
不做虚拟路径映射，导致沙箱技能从来没加载成功过；这里把四种组合的返回值钉死。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import server
from services.skill_registry import (
    VIRTUAL_GLOBAL_SKILLS,
    VIRTUAL_LOCAL_SKILLS,
    VIRTUAL_PROJECT_SET_SKILLS,
)


class FakeStore:
    """同时满足会话反查与项目文件夹反查的假 store。"""

    def __init__(
        self,
        *,
        thread: dict[str, Any] | None = None,
        local_folder: str | None = None,
    ) -> None:
        self._thread = thread
        self._local_folder = local_folder

    def get_thread(self, _thread_id: str) -> dict[str, Any] | None:
        return self._thread

    def get_project_folder(self, _project_name: str) -> dict[str, Any] | None:
        if self._local_folder is None:
            return None
        return {"local_folder": self._local_folder}


@pytest.fixture
def route(monkeypatch: pytest.MonkeyPatch):
    """返回一个「按需装配 store 并解析技能源」的调用器。"""

    def install(
        *,
        project_name: str | None = None,
        local_folder: str | None = None,
    ) -> FakeStore:
        thread = {"project_name": project_name} if project_name is not None else {}
        store = FakeStore(thread=thread, local_folder=local_folder)
        monkeypatch.setattr("agent.runtime._helpers.get_store", lambda: store)
        monkeypatch.setattr("agent.graph.graph.get_store", lambda: store)
        return store

    return install


def test_sandbox_without_project_mounts_only_global(route):
    """沙箱 + 不属于任何项目集合 → 只挂全局层。"""
    route()

    assert server._resolve_skill_sources("t1", "sandbox") == ["/workspace/skills/"]


def test_sandbox_with_project_mounts_project_set_layer(route):
    """沙箱 + 属于项目集合 → 全局层与项目集合层都挂（本次修复的核心）。"""
    route(project_name="Xihe")

    assert server._resolve_skill_sources("t1", "sandbox") == [
        "/workspace/skills/",
        "/workspace/skills/.project-set/Xihe/",
    ]


def test_sandbox_never_mounts_local_layer(route, tmp_path: Path):
    """即使项目绑定了本地文件夹，容器里也没有那一层。"""
    route(project_name="Xihe", local_folder=str(tmp_path))

    assert server._resolve_skill_sources("t1", "sandbox") == [
        "/workspace/skills/",
        "/workspace/skills/.project-set/Xihe/",
    ]


def test_local_without_project_mounts_only_global(route):
    """本地 + 不属于任何项目 → 只挂全局虚拟路径。"""
    route()

    assert server._resolve_skill_sources("t1", "local") == [VIRTUAL_GLOBAL_SKILLS]


def test_local_with_unbound_project_mounts_project_set(route):
    """项目集合层不要求绑定本地文件夹——未绑定也应挂上。"""
    route(project_name="Xihe")

    assert server._resolve_skill_sources("t1", "local") == [
        VIRTUAL_GLOBAL_SKILLS,
        VIRTUAL_PROJECT_SET_SKILLS,
    ]


def test_local_with_bound_project_mounts_all_three(route, tmp_path: Path):
    """绑定了本地文件夹时三层齐全，顺序为全局 → 项目集合 → 本地。"""
    route(project_name="Xihe", local_folder=str(tmp_path))

    assert server._resolve_skill_sources("t1", "local") == [
        VIRTUAL_GLOBAL_SKILLS,
        VIRTUAL_PROJECT_SET_SKILLS,
        VIRTUAL_LOCAL_SKILLS,
    ]


def test_sandbox_and_local_never_share_paths(route, tmp_path: Path):
    """两种环境的挂载源不能重叠，否则会出现「挂了但没同步」的错位。"""
    route(project_name="Xihe", local_folder=str(tmp_path))

    sandbox_sources = set(server._resolve_skill_sources("t1", "sandbox"))
    local_sources = set(server._resolve_skill_sources("t1", "local"))

    assert sandbox_sources.isdisjoint(local_sources)
