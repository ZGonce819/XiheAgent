"""工具执行后最终回复中间件。

确保工具调用轮次结束后，Agent 始终以一条 assistant 消息收尾。
当最后一条消息是 ToolMessage 时，自动追加一条系统提示要求模型给出最终回复，
避免对话停在工具结果上而没有总结性回复。
"""

from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage


class FinalResponseMiddleware(AgentMiddleware):
    """在工具轮次结束后触发最终回复。

    仅当最后一个消息是 ToolMessage 时才追加系统提示并调用模型获取最终回复。
    审批中断等场景不会进入 ``after_agent``，因此不受影响。
    """

    def __init__(self, model: Any) -> None:
        """初始化中间件。

        Args:
            model: 用于生成最终回复的 ChatModel 实例。
        """
        super().__init__()
        self.model = model

    def _input(self, state: AgentState) -> list[Any] | None:
        """构造追加最终回复提示的消息列表。

        检查状态中最后一条消息是否为 ToolMessage；若是则在其后追加一条
        系统提示，要求模型根据本轮工具执行结果给出最终中文回复。

        Args:
            state: 当前 Agent 状态，包含消息列表。

        Returns:
            追加了系统提示的消息列表；最后一条不是 ToolMessage 时返回 ``None``。
        """
        messages = state.get("messages", [])
        if not messages or not isinstance(messages[-1], ToolMessage):
            return None
        return [*messages, SystemMessage(content=(
            "工具执行已经结束。请根据本轮实际结果给用户最终中文回复，说明完成情况、"
            "失败或拒绝的操作及未完成事项。不得再调用工具，不得把之前轮次的方案当作本轮结果。"
        ))]

    @staticmethod
    def _update(reply: Any) -> dict[str, Any]:
        """校验模型回复并构造状态更新字典。

        Args:
            reply: 模型返回的消息对象。

        Returns:
            包含最终 AI 回复的消息字典。

        Raises:
            RuntimeError: 当回复不是有效的 AIMessage、仍包含工具调用或内容为空时。
        """
        if not isinstance(reply, AIMessage) or reply.tool_calls or not reply.content:
            raise RuntimeError("模型未返回有效的最终 AI 回复")
        return {"messages": [reply]}

    def after_agent(self, state: AgentState, runtime: Any) -> dict[str, Any] | None:
        """同步钩子：在 Agent 执行结束后触发最终回复。

        Args:
            state: Agent 执行完成后的状态。
            runtime: LangGraph 运行时对象。

        Returns:
            包含最终回复的状态更新字典；无需触发时返回 ``None``。
        """
        messages = self._input(state)
        return self._update(self.model.invoke(messages)) if messages else None

    async def aafter_agent(self, state: AgentState, runtime: Any) -> dict[str, Any] | None:
        """异步钩子：在 Agent 执行结束后触发最终回复。

        Args:
            state: Agent 执行完成后的状态。
            runtime: LangGraph 运行时对象。

        Returns:
            包含最终回复的状态更新字典；无需触发时返回 ``None``。
        """
        messages = self._input(state)
        return self._update(await self.model.ainvoke(messages)) if messages else None
