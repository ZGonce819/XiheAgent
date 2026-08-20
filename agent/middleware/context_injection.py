"""仓库级上下文注入中间件。

当前项目的仓库长期记忆保存在 LangGraph Store 中，并通过 DeepAgents 的
StoreBackend 暴露为类似 ``/memories/{owner}/{repo}.md`` 的虚拟文件。
如果只依赖模型主动去读这个虚拟文件，模型可能会忘记读，或者在复杂任务中
先浪费很多工具调用扫描仓库。因此这里在整轮 Agent 开始前，把仓库标识、
记忆文件路径和已有记忆内容主动注入为一条 SystemMessage。

注意：
1. 这里不修改用户消息，也不替换系统提示词，只是在消息列表最前面追加一份
   "当前仓库上下文"。
2. 这里注入的是长期上下文摘要，不代表真实仓库状态；如果它和文件系统、
   Git 命令、测试输出冲突，Agent 必须以实时工具结果为准。
3. 这里不写入记忆。记忆写回只由 runtime 在 coding 任务成功结束后负责。
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import SystemMessage
from langgraph.config import get_config

from agent.graph import get_langgraph_store
from agent.memory.repo import build_repo_memory_namespace, repo_memory_store_key, repo_memory_virtual_path
from tools.gitee_api import mask_token, parse_gitee_repo_url

logger = logging.getLogger(__name__)

MAX_REPO_MEMORY_CHARS = 6000


def _repo_url_and_content_from_config() -> tuple[str | None, str | None]:
    """从 LangGraph 运行时配置中读取 repo_url 和缓存的记忆内容。

    Runtime 对象本身没有 config 字段，必须通过 ``langgraph.config.get_config()``
    才能访问 RunnableConfig。

    Returns:
        repo_url: 当前任务绑定的 Gitee 仓库地址。
        memory_content: runtime 预先读取并放入 configurable 的仓库记忆内容。
    """
    configurable = get_config().get("configurable", {})
    if not isinstance(configurable, dict):
        return None, None
    repo_url = configurable.get("repo_url")
    repo_url = repo_url if isinstance(repo_url, str) and repo_url.strip() else None
    memory_content = configurable.get("_repo_memory_content")
    memory_content = str(memory_content).strip() if isinstance(memory_content, str) and memory_content.strip() else None
    return repo_url, memory_content


def _build_repo_context_notice(
    *,
    owner: str,
    repo: str,
    repo_url: str,
    memory_content: str | None,
) -> str:
    """生成仓库上下文的 SystemMessage 文本。

    无论记忆文件是否存在，都至少注入仓库标识和记忆文件路径，让 Agent 知道
    自己在哪个仓库工作、可以从哪里读取长期上下文。
    """
    memory_path = repo_memory_virtual_path(owner, repo)
    lines = [
        "【当前仓库上下文】",
        f"仓库地址：{repo_url}",
        f"owner/repo：{owner}/{repo}",
        f"仓库记忆文件：{memory_path}",
    ]
    if memory_content:
        trimmed = memory_content.strip()
        if len(trimmed) > MAX_REPO_MEMORY_CHARS:
            trimmed = trimmed[:MAX_REPO_MEMORY_CHARS].rstrip() + "\n\n...（仓库记忆已截断）"
        lines += [
            "",
            f"仓库记忆内容（来自 `{memory_path}`，仅作为长期上下文参考。",
            "如果它与真实仓库文件、Git 状态或命令输出冲突，必须以真实仓库为准）：",
            "",
            mask_token(trimmed),
        ]
    else:
        lines += [
            "",
            "该仓库的记忆文件尚未创建。你可以通过 write_file 将仓库的结构、",
            "技术栈、启动命令、测试命令等稳定结论写入该文件，供后续任务参考。",
        ]
    return "\n".join(lines)


class ContextInjectionMiddleware(AgentMiddleware):
    """在整轮 Agent 开始前注入仓库级长期记忆上下文。

    记忆只在 ``before_agent`` / ``abefore_agent`` 注入一次，避免在每次模型调用前
    重复增加 token 成本。即使仓库记忆尚未创建，也会注入基础仓库标识和文件路径。
    """

    state_schema = AgentState

    def before_agent(self, state: AgentState, runtime: Any) -> dict[str, Any] | None:
        repo_url, memory_content = _repo_url_and_content_from_config()
        if not repo_url:
            return None

        repo = parse_gitee_repo_url(repo_url)
        if memory_content is None:
            try:
                namespace = build_repo_memory_namespace(repo.owner, repo.repo)
                item = get_langgraph_store().get(namespace, repo_memory_store_key(repo.owner, repo.repo))
                memory_content = str(item.value.get("content") or "").strip() if item is not None else None
            except Exception:
                logger.warning("仓库记忆读取失败，跳过记忆注入：repo=%s/%s", repo.owner, repo.repo, exc_info=True)

        if memory_content:
            logger.info("注入仓库记忆上下文：repo=%s/%s length=%s", repo.owner, repo.repo, len(memory_content))
        else:
            logger.info("仓库记忆尚不存在，注入基础上下文：repo=%s/%s", repo.owner, repo.repo)

        notice = _build_repo_context_notice(
            owner=repo.owner,
            repo=repo.repo,
            repo_url=repo.clone_url,
            memory_content=memory_content,
        )
        messages = list(state.get("messages", []))
        return {"messages": [SystemMessage(content=notice), *messages]}

    async def abefore_agent(self, state: AgentState, runtime: Any) -> dict[str, Any] | None:
        return self.before_agent(state, runtime)
