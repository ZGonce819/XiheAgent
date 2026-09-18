"""自动化任务的「这个任务正在跑」登记。

两个入口会同时想跑同一个任务：调度线程到点了要跑，用户在界面上点「立即跑一轮」
也要跑。两边各记各的账时会撞车 —— 调度器只认自己起的线程集合，接口层完全不知道
有一轮在跑，于是一次点击撞上一次自动触发就是两个线程同跑一轮：各自读同一份
``budget.runsUsed``，跑完再各自把整个状态文件写回去，轮次计数会丢、历史会互相盖。

登记放在这里而不是挂在调度器上，是因为调度器实例由 ``app.py`` 的 lifespan 持有，
:func:`services.automation_scheduler.start_scheduler` 也没有「取当前调度器」的入口，
接口层拿不到它。两个入口共用这里的模块级单例，才谈得上互斥。

两层互斥，各自解决一类问题：

1. **进程内**一份集合 —— 挡住同一进程里的并发（连点两次、手动撞上自动触发）；
2. **Redis** 上一把 ``SET NX EX`` 的锁 —— 挡住多进程部署下跨进程的并发。
   多进程时 HTTP 请求可能落到任意一个进程，只有进程内集合是不够的。

Note:
    Redis 不可用时**降级为仅进程内互斥**并记一条警告。取舍与扫描租约一致：
    缓存故障不该让所有定时任务静默停掉，而当前部署本来就是单进程 ——
    真正会漏掉的是「多进程 + Redis 同时挂」这种组合。换部署形态时要一起重新想。
"""

from __future__ import annotations

import logging
import threading
import uuid
from typing import Any, Protocol

logger = logging.getLogger(__name__)

RUN_KEY_PREFIX = "xihe:automation:running"
"""「某个任务正在跑」的 Redis key 前缀，完整的 key 是 ``前缀:任务id``。"""

DEFAULT_RUN_SECONDS = 3600
"""登记的有效期，单位秒。

取得比一轮的正常耗时长得多：一轮要跑模型、要执行验证检查项，几十分钟是常态。
它只是**进程崩掉之后的兜底自愈** —— 正常跑完会主动放掉，不会走到超时这一步。
调小的代价是还在跑的那一轮中途失去保护（后到的线程能抢进来），调大的代价是
进程崩过之后要多等一会儿这个任务才能再跑。两害相权，取长。
"""


class TaskRunGuardProtocol(Protocol):
    """运行登记需要实现的三件事。"""

    def acquire(self, task_id: str) -> str | None:
        """把「这个任务正在跑」记在自己名下。

        Args:
            task_id: 自动化任务 id。

        Returns:
            抢到返回本次的凭据（释放时要带回来）；已经有一轮在跑返回 ``None``。
        """
        ...

    def release(self, task_id: str, token: str) -> None:
        """放掉自己那一份登记。

        Args:
            task_id: 自动化任务 id。
            token: :meth:`acquire` 返回的凭据。
        """
        ...

    def held(self) -> frozenset[str]:
        """本进程正在跑的任务 id。

        Returns:
            本进程持有登记的任务 id 集合。
        """
        ...


class TaskRunGuard:
    """按任务 id 的运行登记（进程内 + Redis）。

    进程内那一层先抢：同一进程里的并发是**当下真实存在**的问题，不该依赖 Redis
    好不好。Redis 那层随后再抢，抢不到（别的进程正持有）就把刚记下的进程内登记
    撤回 —— 不撤的话这个任务在本进程里会一直显示「正在跑」，永远触发不了。

    Attributes:
        _held: 本进程持有的登记，``任务 id -> 凭据``。
    """

    def __init__(
        self,
        *,
        ttl_seconds: int = DEFAULT_RUN_SECONDS,
        client: Any = None,
        use_redis: bool = True,
    ) -> None:
        """初始化。

        Args:
            ttl_seconds: 登记在 Redis 上的有效期。
            client: Redis 客户端；不传则第一次用时才建。
            use_redis: ``False`` 表示只在本进程内互斥（测试用）。
        """
        self._ttl_seconds = max(1, int(ttl_seconds))
        self._client = client
        self._use_redis = use_redis
        self._lock = threading.Lock()
        self._held: dict[str, str] = {}
        self._degraded_logged = False

    @classmethod
    def in_memory(cls, **kwargs: Any) -> TaskRunGuard:
        """造一个只在本进程内互斥的登记。

        Args:
            **kwargs: 透传给构造函数的其余参数。

        Returns:
            不接 Redis 的登记。
        """
        return cls(use_redis=False, **kwargs)

    def _connection(self) -> Any:
        """取 Redis 连接（第一次用时才建）。

        Returns:
            Redis 客户端。
        """
        if self._client is None:
            from database.redis import get_redis_stream_connection

            self._client = get_redis_stream_connection()
        return self._client

    def _key(self, task_id: str) -> str:
        """拼出这个任务在 Redis 上的 key。

        Args:
            task_id: 自动化任务 id。

        Returns:
            完整的 Redis key。
        """
        return f"{RUN_KEY_PREFIX}:{task_id}"

    def acquire(self, task_id: str) -> str | None:
        """抢登记。

        Args:
            task_id: 自动化任务 id。

        Returns:
            抢到返回凭据；已经有一轮在跑返回 ``None``。
        """
        token = uuid.uuid4().hex
        with self._lock:
            if task_id in self._held:
                return None
            self._held[task_id] = token

        if not self._use_redis:
            return token

        try:
            client = self._connection()
            if client.set(self._key(task_id), token, nx=True, ex=self._ttl_seconds):
                return token
        except Exception:
            if not self._degraded_logged:
                # 只报一次：每轮都刷一条会把日志淹掉。
                logger.warning(
                    "拿不到运行登记（Redis 不可用？），本次只按进程内互斥",
                    exc_info=True,
                )
                self._degraded_logged = True
            return token

        # 别的进程正持有这一份。把刚记下的进程内登记撤回再拒 —— 留着的话这个任务
        # 在本进程里会一直显示「正在跑」，除非重启，否则再也触发不了。
        with self._lock:
            if self._held.get(task_id) == token:
                self._held.pop(task_id, None)
        return None

    def release(self, task_id: str, token: str) -> None:
        """放掉登记。

        只放自己那一份：登记可能已经因为超时落到别人手里了，那时候删掉就等于
        把别人的锁撬开。凭据对不上时什么都不做。

        Args:
            task_id: 自动化任务 id。
            token: :meth:`acquire` 返回的凭据。
        """
        with self._lock:
            if self._held.get(task_id) == token:
                self._held.pop(task_id, None)

        if not self._use_redis:
            return
        try:
            client = self._connection()
            if client.get(self._key(task_id)) == token:
                client.delete(self._key(task_id))
        except Exception:
            logger.debug("释放运行登记失败", exc_info=True)

    def held(self) -> frozenset[str]:
        """本进程正在跑的任务 id。

        Returns:
            本进程持有登记的任务 id 集合。
        """
        with self._lock:
            return frozenset(self._held)


_guard: TaskRunGuard | None = None
_guard_lock = threading.Lock()


def task_guard() -> TaskRunGuard:
    """取全局运行登记（第一次用时建）。

    调度器和手动触发接口都从这里拿，拿到的是同一个对象 —— 这是两边互斥的前提。

    Returns:
        全局登记实例。
    """
    global _guard
    with _guard_lock:
        if _guard is None:
            _guard = TaskRunGuard()
        return _guard
