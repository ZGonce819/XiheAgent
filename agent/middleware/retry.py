"""模型调用的有限重试中间件。

只重试瞬时网络、限流和服务端错误；参数错误、权限错误和工具调用错误直接交给
上层处理，避免重复执行非幂等操作。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse

logger = logging.getLogger(__name__)


def _retryable(exc: BaseException) -> bool:
    text = str(exc).lower()
    if any(marker in text for marker in ("invalid", "unauthorized", "forbidden", "bad request", "quota exhausted", "insufficient_quota", "tool")):
        return False
    return isinstance(exc, (TimeoutError, ConnectionError, OSError)) or any(
        marker in text for marker in ("timeout", "rate limit", "429", "500", "502", "503", "504", "temporar",
                                  "connection error", "getaddrinfo failed", "peer closed", "incomplete chunked read", "response ended prematurely")
    )


class ModelRetryMiddleware(AgentMiddleware):
    """对模型请求做指数退避重试，并限制重试次数。

    重试仍失败且配置了 ``fallback_model`` 时，把本次请求降级到备用模型
    重发一次（主模型挂起/无响应场景，避免直接中断对话）。
    """

    def __init__(
        self,
        *,
        max_attempts: int = 3,
        backoff_seconds: float = 0.5,
        fallback_model: Any | None = None,
    ) -> None:
        super().__init__()
        self.max_attempts = max(1, max_attempts)
        self.backoff_seconds = max(0.0, backoff_seconds)
        self.fallback_model = fallback_model

    def _run_with_fallback(self, request, handler):
        """重试主模型；耗尽后降级到备用模型重发一次。"""
        last: BaseException | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = handler(request)
                if not response or not getattr(response, "result", None):
                    raise RuntimeError("模型返回为空")
                return response
            except Exception as exc:  # noqa: BLE001
                last = exc
                if attempt >= self.max_attempts or not _retryable(exc):
                    break
                delay = self.backoff_seconds * (2 ** (attempt - 1))
                logger.warning("模型调用失败，%s 秒后重试 (%s/%s): %s", delay, attempt, self.max_attempts, exc)
                time.sleep(delay)
        # 重试耗尽：尝试降级到备用模型
        if last is not None and self.fallback_model is not None:
            try:
                logger.warning("主模型重试耗尽，降级到备用模型 %s：%s", getattr(self.fallback_model, "model_name", self.fallback_model), last)
                fallback_req = replace(request, model=self.fallback_model)
                response = handler(fallback_req)
                if not response or not getattr(response, "result", None):
                    raise RuntimeError("模型返回为空")
                return response
            except Exception as fexc:  # noqa: BLE001
                logger.warning("备用模型也失败：%s", fexc)
                raise last or fexc
        raise last or RuntimeError("模型调用失败")

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        return self._run_with_fallback(request, handler)

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        last: BaseException | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = await handler(request)
                if not response or not getattr(response, "result", None):
                    raise RuntimeError("模型返回为空")
                return response
            except Exception as exc:  # noqa: BLE001
                last = exc
                if attempt >= self.max_attempts or not _retryable(exc):
                    break
                delay = self.backoff_seconds * (2 ** (attempt - 1))
                logger.warning("模型调用失败，%s 秒后异步重试 (%s/%s): %s", delay, attempt, self.max_attempts, exc)
                await asyncio.sleep(delay)
        if last is not None and self.fallback_model is not None:
            try:
                logger.warning("主模型重试耗尽，降级到备用模型 %s：%s", getattr(self.fallback_model, "model_name", self.fallback_model), last)
                fallback_req = replace(request, model=self.fallback_model)
                response = await handler(fallback_req)
                if not response or not getattr(response, "result", None):
                    raise RuntimeError("模型返回为空")
                return response
            except Exception as fexc:  # noqa: BLE001
                logger.warning("备用模型也失败：%s", fexc)
                raise last or fexc
        raise last or RuntimeError("模型调用失败")
