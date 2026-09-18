"""自动化任务的常驻调度。

:mod:`services.automation_loop` 里的 :func:`~services.automation_loop.is_due` 与
:func:`~services.automation_loop.due_automations` 只会回答「现在该跑哪几个」，
本模块负责**按分钟去问**并把活派下去。两者分开是为了让循环本身不需要模型、
不需要线程就能测。

工作方式：进程内起一个后台线程，每 ``tick_seconds`` 秒扫一遍：

1. 到点的自动化任务**先写触发时间标记，再启动一轮** —— ``is_due`` 靠
   ``lastTriggeredAt`` 判断「这一分钟跑过没有」，标记写在启动之后就晚了，
   同一分钟会被重复触发；
2. 每一轮在自己的线程里跑，一个慢轮次不会挡住后面的任务；
3. 同一个自动化上一轮还没跑完时，这一分钟跳过它；
4. 一轮没跑成功（失败、或者根本没执行）时，把原因写进定义的 ``lastError``。
   不写的话，一个坏掉的任务在列表上和正常的长得一模一样 —— 只有点进详情
   才看得见，等于没人会发现。跑成功的那次触发会把这条错误清掉；
   「这一轮跑完了、但配好的检查项一条都没执行」也归这一条 —— 验证门是空的
   同样是坏掉的一种，只是从结果上看不出来；
5. 循环已经把自己停了的任务**不再触发**。触发它只会空转，却会把
   ``lastTriggeredAt`` 写新，界面上显示成「刚触发过」。

Note:
    调度是**进程内**的，但**扫描**这一步在多个进程之间用一份 Redis 租约串起来
    （见 :class:`ScannerLease`）：谁拿到租约谁扫，没拿到的这一轮什么都不做。
    没有它，多进程部署时每个进程都会扫一遍，同一个任务会被重复触发。
    租约只是「谁负责触发」的收敛，不改变「一轮在自己的线程里跑」这件事。

Note:
    轮次线程是 daemon。进程退出时不打断正在跑的那一轮，它下一轮会自己重新触发
    （触发时间已经标记过，但那一分钟已过，下个到点会正常再跑）。
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

from config.automation_config import mark_automation_run, mark_automation_triggered
from config.automation_state import stop_status
from services.automation_loop import CycleResult, due_automations, run_cycle
from services.automation_runner import make_agent_round_runner, resolve_backend_type

logger = logging.getLogger(__name__)

DEFAULT_TICK_SECONDS = 30
"""多久扫一次。cron 的最小粒度是分钟，30 秒足够，也不至于空转太频繁。"""

LEASE_KEY = "xihe:automation:scheduler:lease"
"""扫描租约的 Redis key。"""

DEFAULT_LEASE_SECONDS = 90
"""租约有效期，取 ``3 ×`` 默认扫描间隔。

必须明显大于扫描间隔：进程被挂起或某次 tick 卡住时，租约到期才能让别的进程接管。
同时它也不能太小 —— 每次 tick 都会续期，正常运行时不会过期。"""


class ScannerLeaseProtocol(Protocol):
    """扫描租约需要实现的两件事。"""

    def acquire(self) -> bool:
        """拿到（或续上）租约时返回 ``True``。"""
        ...

    def release(self) -> None:
        """主动放掉租约。"""
        ...


class ScannerLease:
    """基于 Redis 的扫描租约。

    ``SET key owner NX EX ttl`` 抢，抢到了就是自己的；本来就是自己的就续期。
    没有这个动作，多进程部署下每个进程都会扫到同一分钟、把同一个任务重复触发。

    **Redis 不可用时不拦住扫描**：记一条警告后照常扫。理由是另一头更糟 ——
    缓存故障会让所有定时任务静默停掉，而当前部署本来就是单进程（真出问题的是
    「本来就会重复触发」的多进程部署，那是配置问题，不是运行期故障）。
    这条取舍写在这里，换部署形态时要一起重新想。
    """

    def __init__(
        self,
        *,
        key: str = LEASE_KEY,
        ttl_seconds: int = DEFAULT_LEASE_SECONDS,
        owner: str | None = None,
        client: Any = None,
    ) -> None:
        """初始化租约。

        Args:
            key: 租约的 key。
            ttl_seconds: 有效期；必须大于扫描间隔。
            owner: 持有者标识；不传则现场生成一个进程内唯一的串。
            client: Redis 客户端；不传则用 :func:`database.redis.get_redis_stream_connection`。
        """
        self._key = key
        self._ttl_seconds = max(1, int(ttl_seconds))
        self._owner = owner or uuid.uuid4().hex
        self._client = client
        self._degraded_logged = False

    def _connection(self) -> Any:
        """取 Redis 连接（第一次用时才建）。"""
        if self._client is None:
            from database.redis import get_redis_stream_connection

            self._client = get_redis_stream_connection()
        return self._client

    def acquire(self) -> bool:
        """抢租约或续期。

        Returns:
            这一轮的扫描该由本进程负责时返回 ``True``。
        """
        try:
            client = self._connection()
            if client.set(self._key, self._owner, nx=True, ex=self._ttl_seconds):
                return True
            if client.get(self._key) == self._owner:
                client.expire(self._key, self._ttl_seconds)
                return True
            return False
        except Exception:
            if not self._degraded_logged:
                # 只报一次：每个 tick 都刷一条会把日志淹掉。
                logger.warning(
                    "拿不到扫描租约（Redis 不可用？），本次仍按单进程继续扫描", exc_info=True
                )
                self._degraded_logged = True
            return True

    def release(self) -> None:
        """放掉租约，让别的进程立刻能接管。

        只删自己的那一份：租约可能已经因为超时落到别人手里了，那时候删掉就等于
        把别人的锁撬开。
        """
        try:
            client = self._connection()
            if client.get(self._key) == self._owner:
                client.delete(self._key)
        except Exception:
            logger.debug("释放扫描租约失败", exc_info=True)


RoundRunnerFactory = Callable[..., Callable[[Any], Any]]


def cycle_problem(result: CycleResult) -> str | None:
    """看这一轮有没有需要写进定义、让用户在列表上看得见的问题。

    Args:
        result: 一轮的结论。

    Returns:
        有问题返回给人看的原因，没问题返回 ``None``。
    """
    if result.stopped:
        # 循环把自己停掉是设计内的行为，不是故障。
        return None
    if not result.ran:
        # 没执行的原因里既有「检查项配置写错了」这种真问题，也有「缺 id」这种
        # 不该出现的状况，两种都值得让人看见。
        return result.skipped_reason or "这一轮没有执行"
    if result.outcome == "failed":
        return result.summary or "这一轮没有跑完"
    if result.outcome == "blocked":
        # 「没做完但也没报错」。最常见的两种是「停在等人工审批」和「只读闸把这一轮拒了」。
        # 这两种都不该只在点进详情后才看得见 —— 定时任务在列表上看起来一切正常、
        # 实际每轮都在空转，是最难发现的一种坏法。
        return result.summary or "这一轮没有完成"
    verification = result.verification
    if isinstance(verification, dict) and not verification.get("executed", True):
        # 这一轮本身看着没事，但配好的检查项一条都没执行 —— 验证门是空的。
        # 放在最后判：这一轮真失败了的话，那条原因更该被看见，别被这条盖掉。
        # 容器里没能确定仓库目录、检查项全被审批策略拦下，都会落这里：任务在列表上
        # 看着一切正常、实际每轮的验证门都没关上，那正是最该被发现的事。
        # 直接用验证结论里的那句话：它已经把「没执行」和原因都说清了。
        return str(verification.get("summary") or "配好的检查项没有执行")
    return None


class AutomationScheduler:
    """按分钟扫描自动化任务，把到点的那几个跑起来。"""

    def __init__(
        self,
        *,
        tick_seconds: int = DEFAULT_TICK_SECONDS,
        backend_type: str | None = None,
        round_runner_factory: RoundRunnerFactory | None = None,
        lease_factory: Callable[[], ScannerLeaseProtocol] | None = None,
    ) -> None:
        """初始化调度器（不启动线程）。

        Args:
            tick_seconds: 扫描间隔，最小 1 秒。
            backend_type: 执行环境；``None`` 表示每轮按全局配置决定。
            round_runner_factory: 造「跑一轮」的工厂，默认接真实 Agent 运行时。
                注入是为了让调度本身可以在不调模型的情况下被测试。
            lease_factory: 造扫描租约的工厂；默认用 Redis 租约，
                **测试必须传一个假的**，否则会去连真的 Redis，还可能因为别的进程
                持有租约而让 tick 变成空转。
        """
        self.tick_seconds = max(1, int(tick_seconds))
        self._backend_type = backend_type
        self._round_runner_factory: RoundRunnerFactory = (
            round_runner_factory or make_agent_round_runner
        )
        self._lease_factory = lease_factory
        self._lease: ScannerLeaseProtocol | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._inflight: set[str] = set()
        self._thread: threading.Thread | None = None

    def _scanner_lease(self) -> ScannerLeaseProtocol:
        """取（必要时新建）扫描租约。

        Returns:
            本调度器使用的租约对象。
        """
        if self._lease is None:
            if self._lease_factory is not None:
                self._lease = self._lease_factory()
            else:
                self._lease = ScannerLease(
                    ttl_seconds=max(DEFAULT_LEASE_SECONDS, self.tick_seconds * 3)
                )
        return self._lease

    @property
    def running(self) -> frozenset[str]:
        """当前正在跑的自动化任务 id。

        Returns:
            正在跑的 id 集合。
        """
        with self._lock:
            return frozenset(self._inflight)

    def start(self) -> bool:
        """启动后台扫描线程。

        Returns:
            本次真的启动了返回 ``True``；已经在跑则返回 ``False``。
        """
        if self._thread is not None and self._thread.is_alive():
            return False
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="automation-scheduler", daemon=True
        )
        self._thread.start()
        logger.info("自动化任务调度已启动：每 %d 秒扫一次", self.tick_seconds)
        return True

    def stop(self, timeout: float = 5.0) -> None:
        """停掉扫描线程。

        正在跑的那几轮**不打断** —— 打断会让状态停在半路。它们各自跑完自己收尾。
        扫描租约同时放掉，让别的进程不用等到超时就能接管。

        Args:
            timeout: 等待扫描线程退出的秒数。
        """
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        if self._lease is not None:
            self._lease.release()
        logger.info("自动化任务调度已停止")

    def tick(self, now: datetime | None = None) -> list[str]:
        """扫一遍并启动到点的任务。

        只负责「启动」，不等结果 —— 一轮可能要跑几分钟。

        扫描本身要先拿到租约：多进程部署时，同一个到点任务只该由一个进程触发。

        Args:
            now: 扫描时刻；不传则取当前 UTC 时间。

        Returns:
            本次启动了哪些自动化任务（id 列表）。
        """
        if not self._scanner_lease().acquire():
            logger.debug("扫描租约不在本进程，跳过这一轮扫描")
            return []

        moment = now or datetime.now(UTC)
        scan = due_automations(moment)
        for identifier, problem in scan.problems:
            # 执行计划写错的要报出来。静默略过的表现是「这个任务再也没跑过」，
            # 从界面上完全看不出原因。
            logger.warning("自动化任务 %s 的执行计划有问题：%s", identifier, problem)

        started: list[str] = []
        for automation in scan.due:
            identifier = str(automation.get("id") or "")
            if not identifier:
                continue
            stopped, reason = stop_status(identifier)
            if stopped:
                # 循环已经把自己停了（连续没通过、到达轮数上限等），再触发也是空转 ——
                # run_cycle 会立刻返回。但触发标记会被写掉，界面上就显示成「刚触发过」，
                # 实际什么都没做。跳过它，让停止状态在界面上保持诚实。
                logger.debug("自动化任务 %s 已停止（%s），本次不触发", identifier, reason)
                continue
            with self._lock:
                if identifier in self._inflight:
                    logger.info(
                        "自动化任务 %s 上一轮还没跑完，这一分钟跳过", identifier
                    )
                    continue
                self._inflight.add(identifier)

            thread_id = str(uuid.uuid4())
            mark_automation_triggered(identifier, thread_id=thread_id, at=moment)
            self._spawn(automation, thread_id, moment)
            started.append(identifier)
        return started

    def _spawn(
        self, automation: Mapping[str, Any], thread_id: str, moment: datetime
    ) -> None:
        """把一轮放进独立线程。

        Args:
            automation: 自动化定义。
            thread_id: 这一轮的会话 id。
            moment: 触发时刻。要一路带到 :meth:`_run_one` —— 那一轮出问题时
                写的 ``lastTriggeredAt`` 必须还是这个时刻，不能是「跑完的时刻」。
        """
        identifier = str(automation.get("id") or "")
        try:
            worker = threading.Thread(
                target=self._run_one,
                args=(automation, thread_id, moment),
                name=f"automation-{identifier}",
                daemon=True,
            )
            worker.start()
        except Exception:
            # 线程都起不来时必须把标记摘掉，否则这个任务会永远停在「正在跑」，
            # 之后再也不会被触发。
            with self._lock:
                self._inflight.discard(identifier)
            logger.exception("自动化任务 %s 启动失败", identifier)

    def _record_error(
        self, identifier: str, thread_id: str, moment: datetime, reason: str
    ) -> None:
        """把这一轮的问题写进定义。

        Args:
            identifier: 自动化任务 id。
            thread_id: 这一轮的会话 id。
            moment: 触发时刻。
            reason: 给人看的原因。
        """
        mark_automation_triggered(
            identifier, thread_id=thread_id, error=reason, at=moment
        )

    def _run_one(
        self, automation: Mapping[str, Any], thread_id: str, moment: datetime
    ) -> None:
        """跑一轮并把异常兜住。

        Args:
            automation: 自动化定义。
            thread_id: 这一轮的会话 id。
            moment: 触发时刻。
        """
        identifier = str(automation.get("id") or "")
        environment = resolve_backend_type(self._backend_type)
        try:
            result = run_cycle(
                automation,
                self._round_runner_factory(
                    thread_id=thread_id, backend_type=environment
                ),
                backend_type=environment,
            )
        except Exception as exc:
            # 「跑一轮」自己的异常已经被 run_cycle 记成失败轮次了，能漏到这里的
            # 只剩编排本身的问题，记进定义让用户看得见。
            logger.exception("自动化任务 %s 自动触发失败", identifier)
            self._record_error(
                identifier, thread_id, moment, f"{type(exc).__name__}: {exc}"
            )
        else:
            # 跑完了才拿得到运行 id，写进定义让界面能直接跳到这一轮的记录。
            mark_automation_run(identifier, run_id=result.run_id)
            problem = cycle_problem(result)
            if problem:
                self._record_error(identifier, thread_id, moment, problem)
        finally:
            with self._lock:
                self._inflight.discard(identifier)

    def _loop(self) -> None:
        """后台扫描循环。"""
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                # 扫描出错不能让线程死掉 —— 死了就再也不会自己恢复，而且
                # 表现是「所有定时任务都停了」，很难查到原因。
                logger.exception("自动化任务扫描失败，下个间隔继续")
            self._stop.wait(self.tick_seconds)


_scheduler: AutomationScheduler | None = None
_scheduler_lock = threading.Lock()


def start_scheduler(
    *, tick_seconds: int = DEFAULT_TICK_SECONDS, backend_type: str | None = None
) -> AutomationScheduler:
    """启动全局调度器（重复调用不会起第二个）。

    返回的实例由调用方持有并在关闭时 :meth:`AutomationScheduler.stop` ——
    进程里只有一个启动点（``app.py`` 的 lifespan），没必要再维护一个
    「取当前调度器」的入口。

    Args:
        tick_seconds: 扫描间隔。
        backend_type: 执行环境；``None`` 表示按全局配置决定。

    Returns:
        全局调度器实例。
    """
    global _scheduler
    with _scheduler_lock:
        if _scheduler is None:
            _scheduler = AutomationScheduler(
                tick_seconds=tick_seconds, backend_type=backend_type
            )
        _scheduler.start()
        return _scheduler
