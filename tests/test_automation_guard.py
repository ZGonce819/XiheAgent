"""自动化任务运行登记的测试。

登记是「这个任务正在跑」的唯一判据，调度侧与手动触发接口共用同一份。主流程用
不接 Redis 的那一档跑；Redis 那一层用一个假客户端单独验 —— 判定逻辑（谁拒谁、
拒了要不要撤回本地登记、超时后还能不能动别人的锁）都在这里，真连 Redis 只会
让用例之间互相串。

「拒掉」这件事有两层含义，两层都要看到：返回 ``None`` 拒掉调用方，**并且**不能
把自己刚记下的那一格留下 —— 留下的表现是这个任务永远显示「正在跑」，再也触发不了。
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from services.automation_guard import (
    RUN_KEY_PREFIX,
    TaskRunGuard,
)


class FakeRedis:
    """只实现登记用到的三个动作。

    额外记下每次 ``set`` 的参数，用来钉住「抢锁必须带 NX 与有效期」。
    """

    def __init__(self) -> None:
        """初始化一个空的假缓存。"""
        self.values: dict[str, str] = {}
        self.sets: list[tuple[Any, ...]] = []
        self.fail = False

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> Any:
        """按 NX 语义写值；``fail`` 为真时抛连接错误。"""
        self.sets.append((key, value, nx, ex))
        if self.fail:
            raise ConnectionError("redis 挂了")
        if nx and key in self.values:
            return None
        self.values[key] = value
        return True

    def get(self, key: str) -> str | None:
        """读值；``fail`` 为真时抛连接错误。"""
        if self.fail:
            raise ConnectionError("redis 挂了")
        return self.values.get(key)

    def delete(self, key: str) -> None:
        """删值。"""
        self.values.pop(key, None)


def test_a_second_acquisition_is_refused_while_held() -> None:
    """同一个任务已经有登记时，第二次抢要拒掉。"""
    guard = TaskRunGuard.in_memory()

    assert guard.acquire("a") is not None
    assert guard.acquire("a") is None


def test_different_tasks_do_not_block_each_other() -> None:
    """不同任务互不影响 —— 一个任务在跑不该挡住别的任务。"""
    guard = TaskRunGuard.in_memory()

    assert guard.acquire("a") is not None
    assert guard.acquire("b") is not None
    assert guard.held() == frozenset({"a", "b"})


def test_release_frees_the_task_for_the_next_round() -> None:
    """放掉之后这个任务能再跑一轮。"""
    guard = TaskRunGuard.in_memory()
    token = guard.acquire("a")
    assert token is not None

    guard.release("a", token)

    assert guard.held() == frozenset()
    assert guard.acquire("a") is not None


def test_releasing_with_a_stale_token_keeps_the_registration() -> None:
    """凭据对不上时什么都不做 —— 那一份已经不是自己的了。"""
    guard = TaskRunGuard.in_memory()
    assert guard.acquire("a") is not None

    guard.release("a", "不是这次的凭据")

    assert guard.held() == frozenset({"a"})


def test_the_redis_lock_is_taken_with_nx_and_a_ttl() -> None:
    """抢锁必须带 NX 与有效期：前者才是互斥，后者是进程崩溃后的自愈。"""
    client = FakeRedis()
    guard = TaskRunGuard(client=client, ttl_seconds=123)

    assert guard.acquire("a") is not None

    key, _, nx, ex = client.sets[-1]
    assert key == f"{RUN_KEY_PREFIX}:a"
    assert nx is True
    assert ex == 123


def test_release_removes_the_redis_lock() -> None:
    """放掉时要把 Redis 上那一份也删掉，别的进程才不用等到超时。"""
    client = FakeRedis()
    guard = TaskRunGuard(client=client, ttl_seconds=60)
    token = guard.acquire("a")
    assert token is not None

    guard.release("a", token)

    assert client.values == {}
    assert guard.held() == frozenset()


def test_a_lock_held_elsewhere_is_refused_without_local_residue() -> None:
    """别的进程持有登记时拒掉，而且不能把进程内那一格留下。

    留下的话这个任务在本进程里会一直显示「正在跑」，除非重启否则再也触发不了 ——
    比多跑一轮更难发现。
    """
    client = FakeRedis()
    client.values[f"{RUN_KEY_PREFIX}:a"] = "别的进程"
    guard = TaskRunGuard(client=client, ttl_seconds=60)

    assert guard.acquire("a") is None
    assert guard.held() == frozenset()


def test_release_does_not_touch_a_lock_that_moved_on() -> None:
    """凭据对不上就别删 —— 登记可能已经超时落到别人手里了。"""
    client = FakeRedis()
    guard = TaskRunGuard(client=client, ttl_seconds=60)
    token = guard.acquire("a")
    assert token is not None
    # 模拟超时：这份 key 落到了别的进程手里。
    client.values[f"{RUN_KEY_PREFIX}:a"] = "别的进程"

    guard.release("a", token)

    assert client.values[f"{RUN_KEY_PREFIX}:a"] == "别的进程"


def test_redis_failure_degrades_to_in_process_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Redis 不可用时改为只在本进程内互斥，并且只警告一次。"""
    client = FakeRedis()
    client.fail = True
    guard = TaskRunGuard(client=client, ttl_seconds=60)

    with caplog.at_level(logging.WARNING, logger="services.automation_guard"):
        first = guard.acquire("a")
        assert first is not None
        # 降级之后进程内那一层仍然管用：同一个任务第二次还是抢不到。
        assert guard.acquire("a") is None
        guard.release("a", first)
        assert guard.acquire("b") is not None

    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1


def test_the_global_guard_is_shared(monkeypatch: pytest.MonkeyPatch) -> None:
    """两个入口必须拿到同一份登记 —— 否则互斥根本无从谈起。"""
    import services.automation_guard as guard_module

    monkeypatch.setattr(guard_module, "_guard", None)

    assert guard_module.task_guard() is guard_module.task_guard()
