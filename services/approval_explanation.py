"""Generate concise explanations for pending command approvals."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from agent.graph.model import make_main_model
from prompt import COMMAND_APPROVAL_EXPLANATION_SYSTEM_PROMPT
from utils.message_utils import message_content_to_text

_MAX_ACTIONS = 4
_MAX_COMMAND_LENGTH = 2_000
_MAX_REASON_LENGTH = 500
_MAX_OUTPUT_LENGTH = 1_200


def explain_pending_approval(pending: dict[str, Any]) -> str:
    """Ask the configured model to explain a server-side approval record."""
    actions = pending.get("actions")
    safe_actions: list[dict[str, str]] = []
    if isinstance(actions, list):
        for action in actions[:_MAX_ACTIONS]:
            if not isinstance(action, dict):
                continue
            safe_actions.append(
                {
                    "command": str(action.get("command") or "")[:_MAX_COMMAND_LENGTH],
                    "approval_reason": str(action.get("description") or "")[:_MAX_REASON_LENGTH],
                }
            )

    payload = json.dumps(
        {"commands": safe_actions}, ensure_ascii=False, separators=(",", ":")
    )
    model = make_main_model(
        str(pending.get("modelId") or "").strip() or None
    )
    response = model.invoke(
        [
            SystemMessage(content=COMMAND_APPROVAL_EXPLANATION_SYSTEM_PROMPT),
            HumanMessage(content=f"请解释以下待审批数据：\n{payload}"),
        ]
    )
    explanation = message_content_to_text(response.content).strip()
    if not explanation:
        raise RuntimeError("模型没有返回解释")
    return explanation[:_MAX_OUTPUT_LENGTH]
