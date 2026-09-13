"""环境变量加载模块。

从 ``.env`` 文件加载非空环境变量，空值不覆盖已有配置。
提供懒加载机制：首次调用 :func:`get_env` 时自动执行加载，
避免模块导入阶段就产生副作用。
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

_PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]
"""项目根目录，直接计算以避免与 ``config.constants`` 循环导入。"""

LOCAL_ENV = _PROJECT_ROOT / ".env"
"""项目根目录下的 ``.env`` 文件路径。"""


def _load_non_empty_env(path: Path, *, override: bool) -> None:
    """加载 .env 中的非空变量。

    python-dotenv 默认会把 ``KEY=`` 这种空值也写入环境变量。
    项目 .env 通常保留空字段作为模板，直接加载空值会覆盖真实配置。

    自定义加载规则：
    - 空值不写入环境变量；
    - ``override=True`` 时强制覆盖已有值；
    - ``override=False`` 时仅在目标变量缺失或为空时才写入。

    Args:
        path: .env 文件路径。
        override: 是否强制覆盖已有环境变量。
    """

    if not path.exists():
        return
    for key, value in dotenv_values(path).items():
        if value is None or value.strip() == "":
            continue
        if override or key not in os.environ or os.environ.get(key, "").strip() == "":
            os.environ[key] = value


def load_environment() -> None:
    """加载项目运行需要的环境变量。

    本项目 ``.env`` 中的空值不会覆盖已有真实值。
    同时默认关闭 LangSmith/LangChain tracing，避免未配置时向外发送数据。

    本函数幂等，多次调用安全；``.env`` 不存在时静默跳过。

    Returns:
        无返回值，通过修改 ``os.environ`` 产生副作用。
    """

    _load_non_empty_env(LOCAL_ENV, override=True)

    os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")
    os.environ.setdefault("LANGSMITH_TRACING", "false")
    os.environ.setdefault("LANGCHAIN_API_KEY", "")


_loaded = False
"""模块级懒加载标志，首次调用 :func:`get_env` 时置为 ``True``。"""


def get_env(name: str, default: str = "") -> str:
    """读取环境变量，首次调用前自动执行 ``load_environment``。

    Args:
        name: 环境变量名称。
        default: 变量不存在时的默认值。

    Returns:
        环境变量值，未设置时返回 ``default``。
    """

    global _loaded
    if not _loaded:
        load_environment()
        _loaded = True
    return os.environ.get(name, default)


def require_env(name: str) -> str:
    """读取必填环境变量，缺失时抛出 ``RuntimeError``。

    Args:
        name: 必填的环境变量名称。

    Returns:
        非空的环境变量值。

    Raises:
        RuntimeError: 环境变量缺失或为空时抛出。
    """

    value = get_env(name).strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value
