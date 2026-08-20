"""仓库级长期记忆写回中间件。

在整轮 Agent 结束后，尝试把"稳定、可复用"的任务结论写回仓库级记忆。
定位是兜底机制：

1. 主要写回路径在 runtime。runtime 更清楚当前任务类型、最终输出内容、
   分支和 PR 等运行结果，因此成功任务优先由 runtime 主动调用
   ``agent.memory.repo_update``。
2. 本中间件在 ``after_agent`` / ``aafter_agent`` 阶段再次尝试提取最后一条
   assistant 消息，避免未来新增运行路径时忘记写回仓库记忆。
3. 不会把整段对话原样写入记忆，只把最终 assistant 消息交给
   ``repo_update`` 做提炼和结构化更新。

安全边界：
- 如果最终消息疑似包含 ``.env``、``.secrets``、api_key、私钥等敏感信息，
  直接跳过写入。
- 写入前会经过 ``mask_token`` 脱敏检查。
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langgraph.config import get_config

from agent.graph import get_langgraph_store
from agent.memory.repo_update import RepoMemoryUpdate, update_repo_memory_from_text
from tools.gitee_api import parse_gitee_repo_url

logger = logging.getLogger(__name__)

SENSITIVE_MARKERS = (".env", ".secrets", "api_key", "apikey", "private key", "私钥")


def _repo_url_from_config() -> str | None:
    """从 LangGraph 运行时配置中读取 repo_url。"""
    configurable = get_config().get("configurable", {})
    repo_url = configurable.get("repo_url") if isinstance(configurable, dict) else None
    return repo_url if isinstance(repo_url, str) and repo_url.strip() else None


def _task_kind_from_config() -> str:
    """从运行配置中读取任务类型。

    task_kind 用于 ``repo_update`` 判断记忆来自哪类任务：
    planning 适合沉淀设计结论，coding 适合沉淀修改模块和测试命令等。
    """
    configurable = get_config().get("configurable", {})
    return str(configurable.get("task_kind", "unknown")) if isinstance(configurable, dict) else "unknown"


def _last_assistant_text(state: AgentState) -> str:
    """从 AgentState 中提取最后一条 assistant/AI 消息文本。

    只取最后一条 assistant 消息，因为中间的 assistant 消息经常是过程推理，
    不一定稳定。同时兼容 ``str`` 和 ``list`` 两种 content 格式。
    """
    for message in reversed(state.get("messages", [])):
        message_type = str(getattr(message, "type", "") or "").lower()
        if message_type not in {"ai", "assistant"}:
            continue
        content = getattr(message, "content", "")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("content")
                    if text:
                        parts.append(str(text))
            text = "\n".join(part.strip() for part in parts if part and part.strip()).strip()
            if text:
                return text
    return ""


def _message_type_summary(state: AgentState) -> str:
    """记录 after_agent 中最后几条消息类型，便于排查框架状态结构。"""
    messages = list(state.get("messages", []))
    tail = [str(getattr(message, "type", type(message).__name__)) for message in messages[-5:]]
    return f"count={len(messages)} tail={tail}"


class MemoryUpdateMiddleware(AgentMiddleware):
    """在整轮 Agent 结束后把稳定结论追加到仓库记忆。

    不直接拼接 Markdown，只负责从 AgentState 中提取最终 assistant 文本，
    然后交给 ``agent.memory.repo_update`` 做清洗、提炼和结构化写回。

    注意：这是兜底写回。正常情况下 runtime 会在任务成功结束后主动更新记忆。
    """

    state_schema = AgentState

    def after_agent(self, state: AgentState, runtime: Any) -> dict[str, Any] | None:
        repo_url = _repo_url_from_config()
        if not repo_url:
            logger.warning("MemoryUpdateMiddleware: get_config 中没有 repo_url，跳过记忆更新")
            return None

        final_text = _last_assistant_text(state)
        if not final_text or any(marker in final_text.lower() for marker in SENSITIVE_MARKERS):
            logger.info(
                "MemoryUpdateMiddleware: 助手消息无可写入的稳定结论，跳过更新：%s",
                _message_type_summary(state),
            )
            return None

        repo = parse_gitee_repo_url(repo_url)
        update_repo_memory_from_text(
            store=get_langgraph_store(),
            repo=repo,
            update=RepoMemoryUpdate(task_kind=_task_kind_from_config(), final_text=final_text),
        )
        return None

    async def aafter_agent(self, state: AgentState, runtime: Any) -> dict[str, Any] | None:
        return self.after_agent(state, runtime)
