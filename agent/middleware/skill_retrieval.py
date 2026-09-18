"""可开关的技能检索中间件。

DeepAgents 的 ``SkillsMiddleware`` 会把技能目录全量拼进 system prompt，而
``wrap_model_call → modify_request`` 是**每一次模型调用**都执行一次的钩子。
技能变多以后这笔注入成本会线性上涨，且主 Agent 与两个子 Agent 各注入一份。

本模块提供一个受用户开关控制的筛选层：开关关闭时行为与内置实现逐字一致，
开启时按当前任务检索相关技能，只把命中的那些写进提示词。

## 为什么类名必须叫 SkillsMiddleware

DeepAgents 在 ``deepagents/graph.py`` 内部**硬编码**构造 ``SkillsMiddleware``
（主 Agent、general-purpose 子 Agent、子 Agent spec、fork 子 Agent 四处），
没有对外注入点。但它把调用方传入的 ``middleware=`` 交给
``_apply_custom_middleware`` 合并，而合并规则是**按 ``.name`` 匹配后原位替换**，
``.name`` 默认取 ``__class__.__name__``。

因此这里刻意定义一个**同名类**：它以同名实例的身份顶替内置实现，占住栈里
原来的位置，既不用 monkeypatch 第三方包，也不用自己重写技能加载逻辑。
名字若被改掉，合并会退化成「新增一个中间件」，而 ``create_agent`` 会因为
两个中间件同名而直接报错——失败是响亮的，不会静默失效。

## 为什么钩子选在 modify_request 而不是 before_agent

``before_agent`` 的加载结果会写进 ``state["skills_metadata"]``，而该字段会随
checkpoint 在会话内**持久化**（``PrivateStateAttr`` 只影响输入输出 schema，
不影响 checkpoint）。在 ``before_agent`` 里做过滤，会导致用户切换开关后本
会话不再重新加载、开关看起来「没生效」。

``modify_request`` 每次模型调用都会跑，且不改动 state 本身，因此开关能在
**下一条消息**就生效，这也符合用户对设置项的预期。
"""

from __future__ import annotations

import logging
from typing import Any

from deepagents.middleware.skills import SkillsMiddleware as _BuiltinSkillsMiddleware
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import HumanMessage

from config.skill_retrieval_config import (
    get_skill_retrieval_preferences,
    skill_retrieval_active,
)

logger = logging.getLogger(__name__)

__all__ = ["SkillsMiddleware"]


def _message_text(message: Any) -> str:
    """把一条消息的正文抽成纯文本。

    兼容三种形态：纯字符串正文、分块正文（多模态消息的 ``[{"type": "text", ...}]``），
    以及既不是字符串也不是列表的异常值。

    Args:
        message: LangChain 消息对象。

    Returns:
        去掉首尾空白后的正文；取不到正文时返回空串。
    """
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(parts).strip()
    return ""


def latest_user_text(messages: Any) -> str:
    """取最后一条用户消息的正文，作为检索查询。

    一轮任务里后续的模型调用往往以工具结果结尾，向前回溯到最近一条用户消息
    可以让同一轮内每次调用拿到**同一个**查询，从而渲染出稳定的技能目录文本，
    不会平白打断 prompt 缓存的前缀。

    Args:
        messages: 当前模型请求的消息列表。

    Returns:
        最后一条用户消息的正文；没有用户消息时返回空串。
    """
    for message in reversed(list(messages or [])):
        if isinstance(message, HumanMessage):
            return _message_text(message)
    return ""


class SkillsMiddleware(_BuiltinSkillsMiddleware):
    """带检索开关的技能中间件，按名称原位替换内置实现。

    只重写 :meth:`modify_request`：先按开关与技能数量判断是否需要检索，需要时
    按当前任务筛选 ``skills_metadata`` 再交给内置实现渲染。其余行为（加载、
    渲染、加载告警、``system_prompt`` 模板）全部沿用内置实现。
    """

    def _filtered_state(self, request: ModelRequest[Any]) -> dict[str, Any] | None:
        """计算筛选后的状态更新。

        Args:
            request: 待处理的模型请求。

        Returns:
            含筛选后 ``skills_metadata`` 的新状态字典；不需要筛选时返回 ``None``，
            表示调用方应原样交给内置实现。
        """
        skills = list(request.state.get("skills_metadata") or [])
        if not skills:
            return None
        if not skill_retrieval_active(len(skills)):
            return None

        query = latest_user_text(request.messages)
        if not query:
            return None

        preferences = get_skill_retrieval_preferences()
        # ``services`` 包的 ``__init__`` 会拉起 streaming_service，后者反向依赖
        # server，而 server 又导入本中间件。这里沿用项目既有约定改为函数内
        # 延迟导入，避免 ``agent.middleware`` 初始化期形成循环导入。
        from services.skill_retrieval import select_skills

        selected = select_skills(skills, query, top_k=int(preferences["top_k"]))
        if len(selected) >= len(skills):
            # 检索没有真的筛掉任何技能（例如无命中走了 fail-open），不做无谓的状态改写。
            return None

        logger.info(
            "技能检索生效：模式=%s 全部=%d 列出=%d",
            preferences["mode"],
            len(skills),
            len(selected),
        )
        return {**request.state, "skills_metadata": selected}

    def modify_request(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        """按开关筛选技能目录，再交给内置实现注入 system prompt。

        Args:
            request: 待处理的模型请求。

        Returns:
            处理后的模型请求。任何异常都会退化为「注入全部技能」，绝不让检索
            失败波及智能体的正常运行。
        """
        try:
            state = self._filtered_state(request)
        except Exception:
            logger.warning("技能检索失败，本次回退为注入全部技能", exc_info=True)
            return super().modify_request(request)
        if state is None:
            return super().modify_request(request)
        return super().modify_request(request.override(state=state))
