"""自动化任务常驻调度的测试。

调度本身不调模型：``round_runner_factory`` 是注入的，跑一轮的活儿由假对象接手。
覆盖四件事：

1. 到点的任务会被启动，**并且触发时间是在启动之前写下的** —— ``is_due`` 靠它
   判断「这一分钟跑过没有」，写晚了同一分钟会重复触发；
2. 同一个任务已经有一轮在跑时，这一分钟跳过它 —— 判据是那份两边共用的运行登记，
   所以用户手动点的那一轮也算；
3. 出任何错都不能把扫描线程带走 —— 线程死了的表现是「所有定时任务都停了」；
4. 扫描租约：拿到才扫，别人的租约不抢、只删自己那份。

调度器一律走 :func:`make_scheduler` 造 —— 默认租约与默认登记都连着真的 Redis，
直接用会把用例之间串起来（详见 :class:`StubLease`）。
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from config.automation_config import (
    create_automation,
    get_automation,
    update_automation,
)
from config.automation_state import mark_stopped
from services.automation_guard import TaskRunGuard
from services.automation_loop import CycleResult, RoundResult
from services.automation_scheduler import (
    LEASE_KEY,
    AutomationScheduler,
    ScannerLease,
    cycle_problem,
)

MONDAY_9AM = datetime(2026, 9, 21, 9, 0, 30, tzinfo=UTC)


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 ``Settings.data_dir`` 指向临时目录。

    Returns:
        临时的 data 目录。
    """
    from config.app_config import get_settings

    target = tmp_path / "data"
    monkeypatch.setattr(get_settings(), "data_dir", target)
    return target


def make_automation(**overrides: Any) -> dict[str, Any]:
    """建一个每天 09:00 跑的自动化任务。

    Returns:
        与 :mod:`config.automation_config` 同构的字典。
    """
    payload: dict[str, Any] = {
        "name": "每日分诊",
        "prompt": "读昨天的 CI 失败，整理成 TODO",
        "schedule": "0 9 * * *",
        "repo": "https://github.com/owner/repo.git",
        "checks": [],
    }
    payload.update(overrides)
    return create_automation(payload)


def make_factory(
    started: list[dict[str, Any]],
    *,
    release: threading.Event | None = None,
    failure: Exception | None = None,
) -> Any:
    """造一个假的「跑一轮」工厂，记录每次收到的参数。

    Args:
        started: 记录容器。
        release: 非空时，跑一轮会先等这个事件被 set（用来制造「上一轮还没跑完」）。
        failure: 非空时，跑一轮会抛出它。

    Returns:
        符合 ``RoundRunnerFactory`` 签名的可调用对象。
    """

    def factory(**kwargs: Any) -> Any:
        def run(request: Any) -> Any:
            started.append(kwargs)
            if release is not None:
                release.wait(timeout=5)
            if failure is not None:
                raise failure
            return RoundResult(outcome="succeeded", summary="做完了")

        return run

    return factory


def wait_for(predicate: Any, timeout: float = 5.0) -> bool:
    """等到条件成立或超时。

    Returns:
        条件成立返回 ``True``。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class StubLease:
    """测试用的扫描租约：本进程永远负责扫描。

    默认租约接的是真的 Redis。测试里**必须**换掉它，否则同一个进程里前一个用例
    拿走的租约还在有效期内，后面每个用例的 ``tick`` 都会变成空转 ——
    表现是一整片「到点却没启动」，跟代码坏掉长得一模一样。
    """

    def __init__(self, grant: bool = True) -> None:
        """初始化。

        Args:
            grant: ``acquire`` 该返回什么。传 ``False`` 用来测「租约不在本进程」。
        """
        self.grant = grant
        self.acquires = 0
        self.releases = 0

    def acquire(self) -> bool:
        """记一次调用并返回预设结果。"""
        self.acquires += 1
        return self.grant

    def release(self) -> None:
        """记一次释放。"""
        self.releases += 1


def make_scheduler(**kwargs: Any) -> AutomationScheduler:
    """造一个调度器，扫描租约与运行登记都换成假的。

    用例只关心「到点启不启动」，租约与登记本身另有专门的用例覆盖。
    **登记必须换掉**：默认取的是全局那一份，它同样接真 Redis，而且同一个进程里
    前面用例留下的登记还在有效期内，会让后面的 ``tick`` 全部变成「已经有一轮在跑」。

    Returns:
        注入了假租约与假登记的调度器。
    """
    kwargs.setdefault("round_runner_factory", make_factory([]))
    kwargs.setdefault("lease_factory", StubLease)
    kwargs.setdefault("guard_factory", TaskRunGuard.in_memory)
    return AutomationScheduler(**kwargs)


def test_due_automation_is_started(data_dir: Path) -> None:
    """到点的任务会被启动。"""
    automation = make_automation()
    started: list[dict[str, Any]] = []
    scheduler = make_scheduler(round_runner_factory=make_factory(started))

    assert scheduler.tick(MONDAY_9AM) == [automation["id"]]
    assert wait_for(lambda: len(started) == 1)
    assert started[0]["backend_type"] in {"local", "sandbox"}


def test_trigger_time_is_written_before_the_round_starts(data_dir: Path) -> None:
    """触发时间必须写在启动之前 —— 写晚了同一分钟会被重复触发。"""
    automation = make_automation()
    started: list[dict[str, Any]] = []
    scheduler = make_scheduler(round_runner_factory=make_factory(started))

    scheduler.tick(MONDAY_9AM)
    saved = get_automation(automation["id"]) or {}
    assert saved["lastTriggeredAt"] is not None
    assert saved["lastThreadId"]

    # 同一分钟再扫一次，不该再启动
    assert scheduler.tick(MONDAY_9AM) == []


def test_same_minute_is_not_triggered_twice(data_dir: Path) -> None:
    """同一分钟内重复扫描只启动一次。"""
    make_automation()
    started: list[dict[str, Any]] = []
    scheduler = make_scheduler(round_runner_factory=make_factory(started))

    scheduler.tick(MONDAY_9AM)
    scheduler.tick(MONDAY_9AM.replace(second=50))
    assert wait_for(lambda: len(started) == 1)
    time.sleep(0.05)
    assert len(started) == 1


def test_disabled_automation_is_skipped(data_dir: Path) -> None:
    """停用的任务不启动。"""
    automation = make_automation()
    # 新建出来的任务默认是启用的（界面上也没有「建的时候顺便停用」这个入口），
    # 停用要走更新这条路径。
    update_automation(automation["id"], {"enabled": False})
    started: list[dict[str, Any]] = []
    scheduler = make_scheduler(round_runner_factory=make_factory(started))

    assert scheduler.tick(MONDAY_9AM) == []
    assert started == []


def test_a_round_still_running_is_not_started_again(data_dir: Path) -> None:
    """上一轮还没跑完时，这一分钟跳过它，而不是再起一轮。"""
    automation = make_automation(schedule="* * * * *")
    release = threading.Event()
    started: list[dict[str, Any]] = []
    scheduler = make_scheduler(
        round_runner_factory=make_factory(started, release=release)
    )

    assert scheduler.tick(MONDAY_9AM) == [automation["id"]]
    assert wait_for(lambda: automation["id"] in scheduler.running)

    # 下一分钟到了，但上一轮还占着
    assert scheduler.tick(MONDAY_9AM + timedelta(minutes=1)) == []

    release.set()
    assert wait_for(lambda: not scheduler.running)


def test_a_task_started_from_elsewhere_is_skipped(data_dir: Path) -> None:
    """别的入口正在跑这个任务时跳过它 —— 用户手动点的那一轮也算。

    「正在跑」只看两边共用的那份登记。调度器要是只认自己起过的线程，一次点击
    撞上一次自动触发就会两个线程同跑一轮。
    """
    automation = make_automation(schedule="* * * * *")
    guard = TaskRunGuard.in_memory()
    started: list[dict[str, Any]] = []
    scheduler = make_scheduler(
        round_runner_factory=make_factory(started), guard_factory=lambda: guard
    )
    assert guard.acquire(automation["id"]) is not None

    assert scheduler.tick(MONDAY_9AM) == []
    assert started == []
    # 关键是**没有写触发标记**：写了的话界面上显示成「刚触发过」，而这一分钟
    # 实际什么都没跑。
    assert (get_automation(automation["id"]) or {})["lastTriggeredAt"] is None


def test_a_broken_trigger_mark_holds_nothing(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """触发标记写不进去时既不启动，也不把登记攥在手里 —— 攥着就再也触发不了。"""
    import services.automation_scheduler as scheduler_module

    def broken(*args: Any, **kwargs: Any) -> None:
        raise OSError("盘满了")

    monkeypatch.setattr(scheduler_module, "mark_automation_triggered", broken)
    make_automation()
    guard = TaskRunGuard.in_memory()
    started: list[dict[str, Any]] = []
    scheduler = make_scheduler(
        round_runner_factory=make_factory(started), guard_factory=lambda: guard
    )

    assert scheduler.tick(MONDAY_9AM) == []
    assert started == []
    assert guard.held() == frozenset()


def test_a_finished_round_frees_the_slot(data_dir: Path) -> None:
    """跑完之后这个任务可以被下一次触发。"""
    automation = make_automation(schedule="* * * * *")
    started: list[dict[str, Any]] = []
    scheduler = make_scheduler(round_runner_factory=make_factory(started))

    scheduler.tick(MONDAY_9AM)
    assert wait_for(lambda: not scheduler.running)
    assert scheduler.tick(MONDAY_9AM + timedelta(minutes=1)) == [automation["id"]]
    assert wait_for(lambda: len(started) == 2)


def test_a_failing_round_records_the_error_on_the_definition(data_dir: Path) -> None:
    """一轮炸了要把原因记进定义，用户在界面上看得见。"""
    automation = make_automation()
    started: list[dict[str, Any]] = []
    scheduler = make_scheduler(
        round_runner_factory=make_factory(started, failure=RuntimeError("编排炸了"))
    )

    scheduler.tick(MONDAY_9AM)
    assert wait_for(lambda: not scheduler.running)

    saved = get_automation(automation["id"]) or {}
    assert "编排炸了" in str(saved["lastError"])
    assert saved["lastErrorAt"]
    # 记错误不能把触发时间改晚 —— is_due 靠它判断这一分钟跑过没有。
    assert saved["lastTriggeredAt"] == MONDAY_9AM.isoformat()


def test_a_later_success_clears_the_recorded_error(data_dir: Path) -> None:
    """跑成功的那次触发要把上一次的错误清掉，否则列表上一直挂着旧错误。"""
    automation = make_automation(schedule="* * * * *")
    attempts: list[int] = []

    def factory(**kwargs: Any) -> Any:
        def run(request: Any) -> Any:
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("第一次炸了")
            return RoundResult(outcome="succeeded", summary="做完了")

        return run

    scheduler = make_scheduler(round_runner_factory=factory)
    scheduler.tick(MONDAY_9AM)
    assert wait_for(lambda: not scheduler.running)
    assert "第一次炸了" in str((get_automation(automation["id"]) or {})["lastError"])

    scheduler.tick(MONDAY_9AM + timedelta(minutes=1))
    assert wait_for(lambda: not scheduler.running)
    saved = get_automation(automation["id"]) or {}
    assert saved["lastError"] is None
    assert saved["lastErrorAt"] is None


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (CycleResult(automation_id="a", ran=True, outcome="succeeded"), None),
        (
            CycleResult(automation_id="a", ran=True, outcome="failed", summary="炸了"),
            "炸了",
        ),
        (
            CycleResult(
                automation_id="a",
                ran=True,
                outcome="blocked",
                summary="这一轮停在等待人工审批，需要人工处理：git status",
            ),
            "这一轮停在等待人工审批，需要人工处理：git status",
        ),
        (
            CycleResult(automation_id="a", ran=True, outcome="blocked", summary=""),
            "这一轮没有完成",
        ),
        (
            CycleResult(
                automation_id="a",
                ran=False,
                skipped_reason="检查项配置有误：超时不是正整数",
            ),
            "检查项配置有误：超时不是正整数",
        ),
        (
            CycleResult(
                automation_id="a",
                ran=True,
                outcome="succeeded",
                verification={
                    "passed": False,
                    "executed": False,
                    "summary": "1 项检查没有执行，无法确认结果：说不清仓库在哪",
                },
            ),
            "1 项检查没有执行，无法确认结果：说不清仓库在哪",
        ),
        (
            CycleResult(
                automation_id="a",
                ran=True,
                outcome="failed",
                summary="这一轮没能跑完：RuntimeError",
                verification={
                    "passed": False,
                    "executed": False,
                    "summary": "1 项检查没有执行，无法确认结果",
                },
            ),
            "这一轮没能跑完：RuntimeError",
        ),
        (CycleResult(automation_id="a", ran=False, skipped_reason=""), "这一轮没有执行"),
        (
            CycleResult(
                automation_id="a",
                ran=False,
                stopped=True,
                stop_reason="连续 3 轮没有通过验证",
            ),
            None,
        ),
    ],
)
def test_cycle_problem_picks_out_what_the_user_should_see(
    result: CycleResult, expected: str | None
) -> None:
    """哪些结论算「用户该看见的问题」是有明确边界的，这里钉住它。"""
    assert cycle_problem(result) == expected


def test_a_stopped_loop_is_not_triggered(data_dir: Path) -> None:
    """循环已经把自己停了的任务不再触发。

    触发它只会空转（``run_cycle`` 立刻返回），却会把 ``lastTriggeredAt`` 写新，
    界面上显示成「刚触发过」，让人以为它还在干活。
    """
    automation = make_automation(schedule="* * * * *")
    mark_stopped(automation["id"], "连续 3 轮没有通过验证")
    started: list[dict[str, Any]] = []
    scheduler = make_scheduler(round_runner_factory=make_factory(started))

    assert scheduler.tick(MONDAY_9AM) == []
    assert started == []
    assert (get_automation(automation["id"]) or {})["lastTriggeredAt"] is None


def test_broken_schedule_is_reported_not_swallowed(data_dir: Path) -> None:
    """执行计划写错的要报出来，且不能拦住别的任务。"""
    make_automation(schedule="这不是 cron")
    good = make_automation(name="正常的那个")
    started: list[dict[str, Any]] = []
    scheduler = make_scheduler(round_runner_factory=make_factory(started))

    assert scheduler.tick(MONDAY_9AM) == [good["id"]]


def test_start_and_stop_are_idempotent(data_dir: Path) -> None:
    """重复启动不会起第二个线程；停掉之后可以再起。"""
    scheduler = make_scheduler(tick_seconds=1, round_runner_factory=make_factory([]))
    assert scheduler.start() is True
    assert scheduler.start() is False
    scheduler.stop()
    assert scheduler.start() is True
    scheduler.stop()


def test_scan_failure_does_not_kill_the_thread(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """扫描出错不能让线程死掉 —— 死了的表现是「所有定时任务都停了」。"""
    calls: list[int] = []

    def boom(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        raise RuntimeError("扫描炸了")

    monkeypatch.setattr("services.automation_scheduler.due_automations", boom)
    scheduler = make_scheduler(tick_seconds=1, round_runner_factory=make_factory([]))
    scheduler.start()
    assert wait_for(lambda: len(calls) >= 2, timeout=5)
    scheduler.stop()
    assert scheduler.running == frozenset()


def test_stop_does_not_interrupt_a_running_round(data_dir: Path) -> None:
    """停调度不能打断正在跑的那一轮 —— 打断会让状态停在半路。"""
    # 走真实时钟的扫描必须用每分钟都命中的执行计划，否则到不到点取决于跑测试的时刻。
    automation = make_automation(schedule="* * * * *")
    release = threading.Event()
    started: list[dict[str, Any]] = []
    scheduler = make_scheduler(
        tick_seconds=1, round_runner_factory=make_factory(started, release=release)
    )
    scheduler.start()
    assert wait_for(lambda: automation["id"] in scheduler.running)

    scheduler.stop()
    # 停的是扫描线程，不是这一轮
    assert automation["id"] in scheduler.running
    release.set()
    assert wait_for(lambda: not scheduler.running)


def test_a_lease_held_by_someone_else_skips_the_scan(data_dir: Path) -> None:
    """租约不在本进程时，这一轮一次扫描都不做。

    多进程部署下这条是「同一个到点任务只被触发一次」的全部依据：没拿到租约的进程
    如果照样扫，每个进程都会各触发一遍。
    """
    automation = make_automation()
    started: list[dict[str, Any]] = []
    lease = StubLease(grant=False)
    scheduler = make_scheduler(
        round_runner_factory=make_factory(started), lease_factory=lambda: lease
    )

    assert scheduler.tick(MONDAY_9AM) == []
    assert started == []
    assert lease.acquires == 1
    # 没扫描就不该动定义：触发时间必须还是空的，否则界面会显示「刚触发过」。
    assert (get_automation(automation["id"]) or {})["lastTriggeredAt"] is None


def test_stopping_the_scheduler_releases_the_lease(data_dir: Path) -> None:
    """停调度要把租约放掉，别让别的进程白等到超时。"""
    lease = StubLease()
    scheduler = make_scheduler(tick_seconds=1, lease_factory=lambda: lease)
    scheduler.tick(MONDAY_9AM)
    scheduler.stop()
    assert lease.releases == 1


class FakeRedis:
    """够用的假 Redis：只实现租约用到的那四个命令。"""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expires: list[tuple[str, int]] = []

    def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        """``SET NX EX`` 的最小实现。"""
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def get(self, key: str) -> str | None:
        """读回 key。"""
        return self.values.get(key)

    def expire(self, key: str, seconds: int) -> bool:
        """记一次续期。"""
        self.expires.append((key, seconds))
        return True

    def delete(self, key: str) -> int:
        """删掉 key。"""
        return 1 if self.values.pop(key, None) is not None else 0


def test_the_lease_is_renewed_when_it_is_already_ours() -> None:
    """租约本来就是自己的就续期，不能每次都重新抢。"""
    client = FakeRedis()
    lease = ScannerLease(client=client, owner="me", ttl_seconds=90)

    assert lease.acquire() is True
    assert lease.acquire() is True

    assert client.values[LEASE_KEY] == "me"
    assert client.expires == [(LEASE_KEY, 90)]


def test_the_lease_is_not_stolen_when_someone_else_holds_it() -> None:
    """别人拿着租约时不抢，也不续期别人的。"""
    client = FakeRedis()
    client.values[LEASE_KEY] = "someone-else"
    lease = ScannerLease(client=client, owner="me")

    assert lease.acquire() is False
    assert client.values[LEASE_KEY] == "someone-else"
    assert client.expires == []


def test_release_only_removes_our_own_lease() -> None:
    """租约可能已经超时落到别人手里，那时删掉等于撬开别人的锁。"""
    client = FakeRedis()
    lease = ScannerLease(client=client, owner="me")

    client.values[LEASE_KEY] = "someone-else"
    lease.release()
    assert client.values[LEASE_KEY] == "someone-else"

    client.values[LEASE_KEY] = "me"
    lease.release()
    assert LEASE_KEY not in client.values


def test_a_broken_redis_does_not_stop_the_scan() -> None:
    """Redis 不可用时照常扫 —— 缓存故障不该让所有定时任务静默停掉。"""

    class BrokenRedis:
        def set(self, *args: Any, **kwargs: Any) -> Any:
            raise ConnectionError("redis 挂了")

    lease = ScannerLease(client=BrokenRedis())

    assert lease.acquire() is True
    # 放不掉也不能抛：那会把 stop() 带崩。
    lease.release()
