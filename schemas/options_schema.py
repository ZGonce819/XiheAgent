"""模型选项响应 Schema。

定义 ``/api/v1/options`` 接口返回的模型列表和默认配置。
"""

from __future__ import annotations

from pydantic import BaseModel


class ModelOption(BaseModel):
    """单个模型选项。

    Attributes:
        id: 模型唯一标识。
        label: 模型显示名称。
        efforts: 可用的推理努力程度列表。
        default_effort: 默认推理努力程度。
        supports_images: 是否支持图片输入。
    """

    id: str
    label: str
    efforts: list[str]
    default_effort: str
    supports_images: bool = False


class OptionsResponse(BaseModel):
    """页面选项响应，包含可用模型列表和默认配置。

    Attributes:
        models: 可用模型选项列表。
        default_agent_model: 默认主模型 ID。
        default_agent_reasoning_effort: 默认主模型推理努力程度。
        default_agent_subagent_model: 默认子代理模型 ID。
        default_agent_subagent_reasoning_effort: 默认子代理推理努力程度。
    """

    models: list[ModelOption]
    default_agent_model: str
    default_agent_reasoning_effort: str
    default_agent_subagent_model: str
    default_agent_subagent_reasoning_effort: str
