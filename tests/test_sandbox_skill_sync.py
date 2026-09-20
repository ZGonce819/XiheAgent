"""沙箱技能同步的行为测试。

覆盖两件事：

1. :meth:`backends.opensandbox.OpenSandboxBackend.sync_skills` 是**幂等同步**
   而非增量追加——先清空再写入，且清空失败不能中断整批同步；
2. :func:`services.sandbox_skill_sync.refresh_sandbox_skills` 的调度规则——
   按项目集合圈定范围，且只在对话跑完时才真正动容器，运行中改为登记欠账。

这些断言不依赖真实沙箱：``OpenSandboxBackend`` 只用到 ``sandbox.id`` 和
``sandbox.files`` 两个接口，用一个记录调用的假对象即可完整覆盖。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from backends import opensandbox
from backends.opensandbox import OpenSandboxBackend
from services import sandbox_skill_sync
from services.skill_registry import sandbox_skill_roots, sandbox_skill_sources


class FakeFiles:
    """记录文件系统调用的假实现。

    ``calls`` 按发生顺序记录 ``(操作名, 路径列表)``，用于断言「先删后建再写」。
    """

    def __init__(self, *, fail_delete: bool = False) -> None:
        self.calls: list[tuple[str, list[str]]] = []
        self.written: list[tuple[str, Any]] = []
        self.fail_delete = fail_delete

    def delete_directories(self, paths: list[str]) -> None:
        self.calls.append(("delete", list(paths)))
        if self.fail_delete:
            raise RuntimeError("目录不存在")

    def create_directories(self, entries: list[Any]) -> None:
        self.calls.append(("create", [entry.path for entry in entries]))

    def write_files(self, entries: list[Any]) -> None:
        self.calls.append(("write", [entry.path for entry in entries]))
        self.written.extend((entry.path, entry.data) for entry in entries)


class FakeSandbox:
    """只满足 ``OpenSandboxBackend`` 实际用到的接口。"""

    def __init__(self, sandbox_id: str = "sbx-1", *, fail_delete: bool = False) -> None:
        self.id = sandbox_id
        self.files = FakeFiles(fail_delete=fail_delete)


def make_backend(*, sandbox_id: str = "sbx-1", fail_delete: bool = False) -> OpenSandboxBackend:
    """构造一个包着假沙箱的真 backend。"""
    return OpenSandboxBackend(  # type: ignore[arg-type]
        sandbox=FakeSandbox(sandbox_id, fail_delete=fail_delete)
    )


def operations(files: FakeFiles) -> list[str]:
    """返回操作名序列，便于断言调用顺序。"""
    return [name for name, _ in files.calls]


def deleted_paths(files: FakeFiles) -> list[str]:
    """汇总所有被清空的路径（每个根目录一次独立调用）。"""
    return [path for name, paths in files.calls if name == "delete" for path in paths]


@pytest.fixture(autouse=True)
def _clean_pending():
    """每个用例前后清空待同步标记，避免模块级状态串味。"""
    sandbox_skill_sync._pending_threads.clear()
    yield
    sandbox_skill_sync._pending_threads.clear()


# ── sync_skills：幂等同步 ───────────────────────────────────────────────────


def test_sync_skills_deletes_then_creates_then_writes():
    """顺序必须是先清空、再建目录、最后写入。"""
    backend = make_backend()

    written = backend.sync_skills(
        [("/workspace/skills/alpha/SKILL.md", b"# alpha")],
        roots=["/workspace/skills"],
    )

    assert written == 1
    assert operations(backend._sandbox.files) == ["delete", "create", "write"]
    assert backend._sandbox.files.calls[0][1] == ["/workspace/skills"]


def test_sync_skills_is_idempotent_not_incremental():
    """主机上删掉的技能必须从容器消失——这是与 upload_files 的关键区别。"""
    backend = make_backend()

    backend.sync_skills([("/workspace/skills/alpha/SKILL.md", b"a")], roots=["/workspace/skills"])
    backend.sync_skills([], roots=["/workspace/skills"])

    deletes = [paths for name, paths in backend._sandbox.files.calls if name == "delete"]
    assert deletes == [["/workspace/skills"], ["/workspace/skills"]]


def test_sync_skills_creates_roots_even_without_files():
    """技能为空也要建出根目录，否则 SkillsMiddleware 会刷 path_not_found。"""
    backend = make_backend()

    written = backend.sync_skills(
        [], roots=["/workspace/skills", "/workspace/skills/.project-set/X"]
    )

    assert written == 0
    # 每个根目录一次独立的清空调用，之后统一建目录；没有任何写入。
    assert operations(backend._sandbox.files) == ["delete", "delete", "create"]
    assert deleted_paths(backend._sandbox.files) == [
        "/workspace/skills",
        "/workspace/skills/.project-set/X",
    ]
    created = dict(backend._sandbox.files.calls)["create"]
    assert created == ["/workspace/skills", "/workspace/skills/.project-set/X"]


def test_sync_skills_creates_parent_directories_of_files():
    """写入前要先建好每个文件的父目录。"""
    backend = make_backend()

    backend.sync_skills(
        [("/workspace/skills/a/nested/SKILL.md", b"x")],
        roots=["/workspace/skills"],
    )

    created = dict(backend._sandbox.files.calls)["create"]
    assert "/workspace/skills/a/nested" in created


def test_sync_skills_tolerates_missing_root_on_first_run():
    """首次同步时根目录还不存在，删除报错不应中断后续写入。"""
    backend = make_backend(fail_delete=True)

    written = backend.sync_skills(
        [("/workspace/skills/alpha/SKILL.md", b"a")],
        roots=["/workspace/skills"],
    )

    assert written == 1
    assert operations(backend._sandbox.files) == ["delete", "create", "write"]


def test_sync_skills_falls_back_for_non_utf8_content():
    """非 UTF-8 字节不能让整批上传失败。"""
    backend = make_backend()

    backend.sync_skills(
        [("/workspace/skills/a/SKILL.md", b"\xff\xfe\x00")], roots=["/workspace/skills"]
    )

    path, data = backend._sandbox.files.written[0]
    assert path == "/workspace/skills/a/SKILL.md"
    assert isinstance(data, str)


# ── sync_sandbox_skills：落点与挂载源一致 ───────────────────────────────────


@pytest.mark.parametrize("project_name", [None, "Xihe"])
def test_sync_sandbox_skills_targets_mount_sources(skills_root: Path, project_name: str | None):
    """同步落点必须全部落在挂载源之下——本次修复的核心不变量。"""
    backend = make_backend()

    written = opensandbox.sync_sandbox_skills(backend, project_name)

    files = backend._sandbox.files
    assert deleted_paths(files) == sandbox_skill_roots(project_name)
    sources = [source.rstrip("/") + "/" for source in sandbox_skill_sources(project_name)]
    written_paths = [path for name, paths in files.calls if name == "write" for path in paths]
    for path in written_paths:
        assert any(path.startswith(source) for source in sources), path
    assert written == len(written_paths)


def test_sync_sandbox_skills_uploads_real_skills(skills_root: Path, make_skill):
    """主机上已有的技能会被真实读出并写进容器。"""
    make_skill(skills_root / "alpha", "alpha")
    make_skill(skills_root / ".project-set" / "Xihe" / "beta", "beta")
    backend = make_backend()

    written = opensandbox.sync_sandbox_skills(backend, "Xihe")

    assert written == 2
    written_paths = [path for path, _ in backend._sandbox.files.written]
    assert written_paths == [
        "/workspace/skills/alpha/SKILL.md",
        "/workspace/.project-set/Xihe/beta/SKILL.md",
    ]


def test_create_backend_swallows_sync_failure(monkeypatch: pytest.MonkeyPatch):
    """同步失败不能让上层误判成「沙箱创建失败」而静默回退到本地 Shell。"""
    monkeypatch.setattr(opensandbox, "get_or_create_sandbox", lambda *a, **k: FakeSandbox())

    def boom(*_args: Any, **_kwargs: Any) -> int:
        raise RuntimeError("同步炸了")

    monkeypatch.setattr(opensandbox, "sync_sandbox_skills", boom)

    backend = opensandbox.create_opensandbox_backend(thread_id="t1", project_name="Xihe")

    assert isinstance(backend, OpenSandboxBackend)


# ── refresh_sandbox_skills：调度规则 ────────────────────────────────────────


class FakeStore:
    """按 thread_id 返回预设会话记录的假 store。"""

    def __init__(self, threads: dict[str, dict[str, Any]]) -> None:
        self._threads = threads

    def get_thread(self, thread_id: str) -> dict[str, Any] | None:
        return self._threads.get(thread_id)


@pytest.fixture
def refresh_env(monkeypatch: pytest.MonkeyPatch):
    """搭好 refresh_sandbox_skills 需要的全部外部依赖。

    Returns:
        ``(install, sync_calls)``：``install`` 注入 backend 缓存与会话记录，
        ``sync_calls`` 记录实际发生的同步调用。
    """
    import server
    from config.app_config import get_settings

    monkeypatch.setattr(get_settings(), "use_sandbox", True)
    sync_calls: list[tuple[str, str | None]] = []

    def fake_sync(backend: OpenSandboxBackend, project_name: str | None = None) -> int:
        sync_calls.append((backend.id, project_name))
        return 7

    monkeypatch.setattr(opensandbox, "sync_sandbox_skills", fake_sync)

    saved = dict(server._BACKENDS)

    def install(
        backends: dict[str, Any],
        threads: dict[str, dict[str, Any]],
    ) -> None:
        server._BACKENDS.clear()
        server._BACKENDS.update(backends)
        monkeypatch.setattr("agent.graph.graph.get_store", lambda: FakeStore(threads))

    yield install, sync_calls

    server._BACKENDS.clear()
    server._BACKENDS.update(saved)


def test_refresh_skips_local_scope(refresh_env):
    """本地层技能只存在于项目文件夹里，与沙箱无关。"""
    install, sync_calls = refresh_env
    install({"t1": make_backend()}, {})

    summary = sandbox_skill_sync.refresh_sandbox_skills(scope="local", project_name="Xihe")

    assert summary["reason"] == "本地层技能与沙箱无关"
    assert sync_calls == []


def test_refresh_skips_when_sandbox_disabled(refresh_env, monkeypatch: pytest.MonkeyPatch):
    """沙箱没开启时无事可做。"""
    from config.app_config import get_settings

    install, sync_calls = refresh_env
    install({"t1": make_backend()}, {})
    monkeypatch.setattr(get_settings(), "use_sandbox", False)

    summary = sandbox_skill_sync.refresh_sandbox_skills(scope="global")

    assert summary["reason"] == "沙箱未开启"
    assert sync_calls == []


def test_refresh_syncs_idle_sandbox(refresh_env):
    """对话已结束时立即刷新容器。"""
    install, sync_calls = refresh_env
    install(
        {"t1": make_backend()},
        {"t1": {"project_name": "Xihe", "latest_run_status": "completed"}},
    )

    summary = sandbox_skill_sync.refresh_sandbox_skills(scope="global")

    assert sync_calls == [("sbx-1", "Xihe")]
    assert summary["synced"] == [{"thread_id": "t1", "files": 7}]
    assert summary["pending"] == []


@pytest.mark.parametrize("status", ["running", "awaiting_approval"])
def test_refresh_defers_busy_thread(refresh_env, status: str):
    """对话正在跑时不动容器，改为登记欠账。"""
    install, sync_calls = refresh_env
    install(
        {"t1": make_backend()},
        {"t1": {"project_name": "Xihe", "latest_run_status": status}},
    )

    summary = sandbox_skill_sync.refresh_sandbox_skills(scope="global")

    assert sync_calls == []
    assert summary["pending"] == ["t1"]
    assert sandbox_skill_sync.has_pending_refresh("t1")


@pytest.mark.parametrize("status", ["pending", "completed", "failed", ""])
def test_refresh_treats_finished_states_as_idle(refresh_env, status: str):
    """pending / completed / failed 都表示容器空闲，可以直接刷新。"""
    install, sync_calls = refresh_env
    install({"t1": make_backend()}, {"t1": {"project_name": None, "latest_run_status": status}})

    sandbox_skill_sync.refresh_sandbox_skills(scope="global")

    assert sync_calls == [("sbx-1", None)]


def test_refresh_project_scope_only_touches_matching_project(refresh_env):
    """项目集合层的变更只影响该项目集合下的会话。"""
    install, sync_calls = refresh_env
    install(
        {
            "t-xihe": make_backend(),
            "t-other": make_backend(),
            "t-none": make_backend(),
        },
        {
            "t-xihe": {"project_name": "Xihe", "latest_run_status": "completed"},
            "t-other": {"project_name": "Other", "latest_run_status": "completed"},
            "t-none": {"project_name": None, "latest_run_status": "completed"},
        },
    )

    summary = sandbox_skill_sync.refresh_sandbox_skills(scope="project_set", project_name="Xihe")

    assert sync_calls == [("sbx-1", "Xihe")]
    assert [item["thread_id"] for item in summary["synced"]] == ["t-xihe"]


def test_refresh_global_scope_touches_every_sandbox(refresh_env):
    """全局层变更影响所有沙箱会话。"""
    install, sync_calls = refresh_env
    install(
        {
            "t-xihe": make_backend(sandbox_id="sbx-xihe"),
            "t-none": make_backend(sandbox_id="sbx-none"),
        },
        {
            "t-xihe": {"project_name": "Xihe", "latest_run_status": "completed"},
            "t-none": {"project_name": None, "latest_run_status": "completed"},
        },
    )

    summary = sandbox_skill_sync.refresh_sandbox_skills(scope="global")

    assert sorted(sync_calls) == [("sbx-none", None), ("sbx-xihe", "Xihe")]
    assert len(summary["synced"]) == 2


def test_refresh_ignores_non_sandbox_backend(refresh_env):
    """本地 Shell backend 没有容器可刷，直接跳过。"""
    install, sync_calls = refresh_env
    install(
        {"t1": object()},
        {"t1": {"project_name": "Xihe", "latest_run_status": "completed"}},
    )

    summary = sandbox_skill_sync.refresh_sandbox_skills(scope="global")

    assert sync_calls == []
    assert summary["synced"] == []


def test_refresh_records_failure_without_raising(refresh_env, monkeypatch: pytest.MonkeyPatch):
    """单个会话同步失败只记账，不影响其它会话。"""
    install, _ = refresh_env
    install(
        {"t1": make_backend(), "t2": make_backend()},
        {
            "t1": {"project_name": "Xihe", "latest_run_status": "completed"},
            "t2": {"project_name": "Xihe", "latest_run_status": "completed"},
        },
    )
    attempted: list[str] = []

    def flaky(backend: OpenSandboxBackend, project_name: str | None = None) -> int:
        attempted.append(backend.id)
        if len(attempted) == 1:
            raise RuntimeError("第一个失败")
        return 1

    monkeypatch.setattr(opensandbox, "sync_sandbox_skills", flaky)

    summary = sandbox_skill_sync.refresh_sandbox_skills(scope="global")

    assert len(summary["failed"]) == 1
    assert len(summary["synced"]) == 1


def test_refresh_skips_thread_missing_from_store(refresh_env):
    """会话记录已不存在时安静跳过。"""
    install, sync_calls = refresh_env
    install({"t-ghost": make_backend()}, {})

    summary = sandbox_skill_sync.refresh_sandbox_skills(scope="global")

    assert sync_calls == []
    assert summary["synced"] == []


# ── 待同步标记 ──────────────────────────────────────────────────────────────


def test_pending_marker_is_consumed_once():
    """标记取出即清除，避免同一笔欠账被重复补同步。"""
    sandbox_skill_sync.mark_pending_refresh("t1")

    assert sandbox_skill_sync.consume_pending_refresh("t1") is True
    assert sandbox_skill_sync.consume_pending_refresh("t1") is False
    assert sandbox_skill_sync.has_pending_refresh("t1") is False


def test_pending_marker_reports_absence():
    """没有欠账时如实返回 False。"""
    assert sandbox_skill_sync.has_pending_refresh("never-seen") is False
    assert sandbox_skill_sync.consume_pending_refresh("never-seen") is False


def test_mark_pending_is_idempotent():
    """重复登记同一会话不会产生多条记录。"""
    sandbox_skill_sync.mark_pending_refresh("t1")
    sandbox_skill_sync.mark_pending_refresh("t1")

    assert sandbox_skill_sync._pending_threads == {"t1"}
    assert sandbox_skill_sync.consume_pending_refresh("t1") is True
    assert sandbox_skill_sync.consume_pending_refresh("t1") is False
