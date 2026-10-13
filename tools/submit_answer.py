"""submit_final_answer 收尾工具。

把"输出最终答案"本身变成一个可调用工具，作为模型完成任务或预算耗尽后的强制出口：

- 正常完成取证时，模型可以调用本工具提交答案并立即结束；
- web_search / deep_search 预算耗尽、修复轮收尾时，提示语会引导模型直接调用本工具；
- FinalResponseMiddleware 会识别本工具调用，把 ``answer`` 参数提取为最终答案进入校验，
  模型无法再通过"继续输出过程说明"逃避收尾。

设计边界：
  1. 工具参数 ``answer`` 必填，描述硬约束禁止解释与过程说明；
  2. 工具返回结构化 JSON，供中间件解析；
  3. 本工具不消费任何搜索预算，不触发外部调用，只作收尾标记。
"""

from __future__ import annotations

import json
import logging

from langchain_core.tools import tool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# FinalResponseMiddleware 依据该常量识别收尾工具调用。
FINALIZE_TOOL_NAME = "submit_final_answer"


class SubmitFinalAnswerSchema(BaseModel):
    """submit_final_answer 工具输入参数。

    Attributes:
        answer: 本次任务最终答案文本，必须直接填写答案本身。
    """

    answer: str = Field(
        ...,
        description=(
            "本次任务的最终答案文本。必须直接填写答案本身：纯数值、数字 ID、标识符，"
            "或题目要求的原样文本。禁止解释、禁止过程说明、禁止附注、禁止 Markdown。"
        ),
    )


@tool(FINALIZE_TOOL_NAME, args_schema=SubmitFinalAnswerSchema)
def submit_final_answer(answer: str) -> str:
    """提交本次任务最终答案并立即结束任务。

    这是本次任务的最后一步：调用本工具即表示 ``answer`` 参数就是最终答案，
    不会再调用任何其他工具。适用于两类情况：

    1. 已完成取证、证据充足，直接提交结论；
    2. 工具预算已耗尽（搜索/深度搜索次数用完）或无法继续补查，直接提交当前最可能的答案。

    调用后任务立即进入最终答案校验，不需要（也不允许）再输出任何说明。
    """
    result = {"ok": True, "submitted": answer, "final": True}
    logger.info("submit_final_answer 被调用：answer_len=%s", len(str(answer or "")))
    return json.dumps(result, ensure_ascii=False)
