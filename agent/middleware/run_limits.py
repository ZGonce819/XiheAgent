"""Agent 单轮运行保护阈值。

长任务运行时需要防止两类问题：
1. 模型不断调用工具，导致费用和耗时失控；
2. 某些任务卡在长时间等待或重复循环，无法及时返回。

本模块提供轻量级运行限制：按任务类型配置最大工具调用次数和最大运行秒数。
它不依赖具体模型供应商，也不解析完整 token 计费信息，只基于 LangGraph 事件流
做稳定的运行保护。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Final

from config.constants import DEFAULT_MAX_SECONDS, DEFAULT_MAX_TOOL_CALLS, TASK_KIND_DEFAULT_LIMITS
from config.env_config import get_env

# 运行停滞检测阈值（秒）：最后一次工具调用后，若超过该时长仍无任何工具事件
# 且未结束，判定为纯模型空转并强制截断。仅对已发生过工具调用的运行生效，
# 不影响正常"搜索→委派→组织答案"流程（组织答案阶段远短于此阈值）。
IDLE_TOOL_TIMEOUT: Final[int] = 1200

# 绕圈收敛检测：同一工具调用签名（工具名+参数）在滑动窗口内重复达到阈值，
# 判定模型在同一目标上反复失败（典型：大页面被截断后反复重抓同一 URL），
# 立即触发软中断收尾（runtime 注入"基于已有证据直接出答案"），避免无限空转。
LOOP_WINDOW: Final[int] = 6    # 观察最近 N 次工具调用
LOOP_REPEAT: Final[int] = 3    # 同一签名重复 >= 3 次判定绕圈


class AgentRunLimitExceeded(RuntimeError):
    """Agent 本轮运行超过保护上限。

    runtime 捕获该异常后应停止继续消费事件流，并向用户返回明确的超限原因。
    异常消息包含超限类型（时间或工具调用次数）和当前任务类型。
    """


@dataclass
class AgentRunLimits:
    """Agent Loop 的轻量保护阈值。

    运行在 FastAPI 内，这里只保留更可靠的时间和工具调用上限。
    不要用 raw event 里的 ``message-start`` 统计模型调用次数，因为 DeepAgents
    的流式片段、子 Agent 和中间 assistant message 都可能产生该事件，容易误判。
    """

    max_tool_calls: int
    max_seconds: int
    task_kind: str = "default"

    @classmethod
    def from_env(cls, task_kind: str | None = None) -> AgentRunLimits:
        """按任务类型读取运行保护阈值。

        配置优先级：
        1. ``AGENT_<TASK_KIND>_MAX_TOOL_CALLS`` / ``AGENT_<TASK_KIND>_MAX_SECONDS``
        2. ``AGENT_MAX_TOOL_CALLS`` / ``AGENT_MAX_SECONDS``
        3. 代码内置的任务类型默认值

        Args:
            task_kind: 任务类型，如 ``"coding"``、``"review"``；为 ``None`` 时使用默认值。

        Returns:
            按优先级解析后的运行限制实例。
        """
        normalized_kind = (task_kind or "default").lower()
        default_tool_calls, default_seconds = TASK_KIND_DEFAULT_LIMITS.get(
            normalized_kind,
            (DEFAULT_MAX_TOOL_CALLS, DEFAULT_MAX_SECONDS),
        )
        env_prefix = f"AGENT_{normalized_kind.upper()}_"
        return cls(
            max_tool_calls=int(
                get_env(
                    f"{env_prefix}MAX_TOOL_CALLS",
                    get_env("AGENT_MAX_TOOL_CALLS", str(default_tool_calls)),
                )
            ),
            max_seconds=int(
                get_env(
                    f"{env_prefix}MAX_SECONDS",
                    get_env("AGENT_MAX_SECONDS", str(default_seconds)),
                )
            ),
            task_kind=normalized_kind,
        )


class AgentRunLimitTracker:
    """根据 LangGraph 事件流统计本轮 Agent 运行规模。

    Tracker 是单轮任务级对象，不应跨 thread 或跨 run 复用。
    每次创建时记录开始时间，并随着事件流推进累计工具调用次数。
    """

    def __init__(self, limits: AgentRunLimits | None = None, task_kind: str | None = None):
        """初始化运行限制跟踪器。

        Args:
            limits: 预构建的运行限制；为 ``None`` 时自动按 ``task_kind`` 从环境变量读取。
            task_kind: 任务类型，仅在 ``limits`` 为 ``None`` 时生效。
        """
        self.limits = limits or AgentRunLimits.from_env(task_kind)
        self.started_at = time.monotonic()
        self.tool_calls = 0
        self.last_tool_event_at: float | None = None
        self._tool_sig_history: list[str] = []

    def observe_event(self, event: Any) -> None:
        """读取一个 raw event 并检查是否超过限制。

        工具调用事件会累计到 ``tool_calls``，每次检查时间和工具调用次数是否超限。

        Args:
            event: LangGraph 事件流中的原始事件字典。

        Raises:
            AgentRunLimitExceeded: 运行时长或工具调用次数超过当前阈值。
        """
        self._check_time()
        if not isinstance(event, dict):
            return

        method = event.get("method")
        if method in {"tool_calls", "tools"}:
            payload = self._first_payload(event)
            event_name = payload.get("event") if isinstance(payload, dict) else None
            if method == "tool_calls" or event_name == "tool-started":
                self.tool_calls += 1
                self.last_tool_event_at = time.monotonic()
                self._check_tool_calls()
                self._observe_tool_signature(payload)
        self._check_idle()

    def _observe_tool_signature(self, payload: Any) -> None:
        """记录工具调用签名（工具名+参数），检测同一目标重复尝试（绕圈）。

        典型场景：fetch_url 抓取大页面被截断后，模型反复用同一 URL 重抓；
        或 web_search 反复用同一 query。重复达到阈值即抛超限，触发 runtime
        的软中断收尾（注入"基于已有证据直接出答案"），不再无限空转。

        Args:
            payload: 工具调用事件的 payload 字典。

        Raises:
            AgentRunLimitExceeded: 同一签名在滑动窗口内重复达到阈值。
        """
        if not isinstance(payload, dict):
            return
        name = payload.get("name") or payload.get("tool") or payload.get("tool_name")
        if not name:
            return
        args = payload.get("input") or payload.get("args") or payload.get("arguments") or {}
        try:
            sig = f"{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)[:200]}"
        except Exception:
            sig = f"{name}:{str(args)[:200]}"
        self._tool_sig_history.append(sig)
        if len(self._tool_sig_history) > LOOP_WINDOW:
            self._tool_sig_history = self._tool_sig_history[-LOOP_WINDOW:]
        if self._tool_sig_history.count(sig) >= LOOP_REPEAT:
            raise AgentRunLimitExceeded(
                f"检测到重复尝试同一来源（{name} 相同参数）已 {LOOP_REPEAT} 次，"
                f"判定绕圈（任务类型：{self.limits.task_kind}），强制收敛止损。"
            )

    def _first_payload(self, event: dict[str, Any]) -> Any:
        """从 LangGraph 事件的 ``params.data`` 中提取第一个 payload。

        Args:
            event: LangGraph 原始事件字典。

        Returns:
            第一个 payload 对象；不存在时返回 ``None``。
        """
        params = event.get("params")
        if not isinstance(params, dict):
            return None
        data = params.get("data")
        if isinstance(data, tuple):
            return data[0] if data else None
        if isinstance(data, list):
            return data[0] if data else None
        return data

    def _check_time(self) -> None:
        """检查当前运行耗时是否超过阈值。

        Raises:
            AgentRunLimitExceeded: 运行时长超过 ``max_seconds``。
        """
        elapsed = time.monotonic() - self.started_at
        # A non-positive value explicitly disables the wall-clock guard for a
        # task class (useful for long-running evaluation questions).
        if self.limits.max_seconds <= 0:
            return
        if elapsed > self.limits.max_seconds:
            raise AgentRunLimitExceeded(
                f"本轮运行已超过 {self.limits.max_seconds} 秒保护上限"
                f"（任务类型：{self.limits.task_kind}）"
            )

    def _check_tool_calls(self) -> None:
        """检查工具调用次数是否超过阈值。

        Raises:
            AgentRunLimitExceeded: 工具调用次数超过 ``max_tool_calls``。
        """
        if self.tool_calls > self.limits.max_tool_calls:
            raise AgentRunLimitExceeded(
                f"本轮工具调用已超过 {self.limits.max_tool_calls} 次保护上限"
                f"（任务类型：{self.limits.task_kind}）。"
                "复杂开发任务可以提高 AGENT_CODING_MAX_TOOL_CALLS，"
                "或让智能体分阶段继续完成 Git 提交、推送和 Pull Request。"
            )

    def _check_idle(self) -> None:
        """检查运行停滞：长时间无任何工具调用且未结束，判定为空转。

        纯模型自调用（无工具事件）不增加 tool_calls、也不刷新工具时间戳；
        若距最后一次工具调用超过 IDLE_TOOL_TIMEOUT 秒仍未提交答案，说明
        agent 在无工具增量的情况下空转，强制截断止损。

        Raises:
            AgentRunLimitExceeded: 停滞时间超过阈值。
        """
        if IDLE_TOOL_TIMEOUT <= 0 or self.last_tool_event_at is None:
            return
        if time.monotonic() - self.last_tool_event_at > IDLE_TOOL_TIMEOUT:
            raise AgentRunLimitExceeded(
                f"本轮运行停滞：连续 {IDLE_TOOL_TIMEOUT} 秒无任何工具调用"
                f"（任务类型：{self.limits.task_kind}），判定为纯模型空转，强制止损。"
            )
