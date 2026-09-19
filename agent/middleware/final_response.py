"""Ensure a completed tool run ends with an assistant response."""

from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage


class FinalResponseMiddleware(AgentMiddleware):
    """Only finalize completed runs; approval interrupts never reach after_agent."""

    def __init__(self, model: Any) -> None:
        super().__init__()
        self.model = model

    def _input(self, state: AgentState) -> list[Any] | None:
        messages = state.get("messages", [])
        if not messages or not isinstance(messages[-1], ToolMessage):
            return None
        return [*messages, SystemMessage(content=(
            "工具执行已经结束。请根据本轮实际结果给用户最终中文回复，说明完成情况、"
            "失败或拒绝的操作及未完成事项。不得再调用工具，不得把之前轮次的方案当作本轮结果。"
        ))]

    @staticmethod
    def _update(reply: Any) -> dict[str, Any]:
        if not isinstance(reply, AIMessage) or reply.tool_calls or not reply.content:
            raise RuntimeError("模型未返回有效的最终 AI 回复")
        return {"messages": [reply]}

    def after_agent(self, state: AgentState, runtime: Any) -> dict[str, Any] | None:
        messages = self._input(state)
        return self._update(self.model.invoke(messages)) if messages else None

    async def aafter_agent(self, state: AgentState, runtime: Any) -> dict[str, Any] | None:
        messages = self._input(state)
        return self._update(await self.model.ainvoke(messages)) if messages else None
