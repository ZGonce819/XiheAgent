"""模型选项响应 Schema。

定义 ``/dashboard/api/options`` 接口返回的模型列表和默认配置。
"""

from __future__ import annotations

from pydantic import BaseModel


class ModelOption(BaseModel):
    """单个模型选项。"""

    id: str
    label: str
    efforts: list[str]
    default_effort: str
    supports_images: bool = False


class OptionsResponse(BaseModel):
    """页面选项响应，包含可用模型列表和默认配置。"""

    models: list[ModelOption]
    default_agent_model: str
    default_agent_reasoning_effort: str
    default_agent_subagent_model: str
    default_agent_subagent_reasoning_effort: str
