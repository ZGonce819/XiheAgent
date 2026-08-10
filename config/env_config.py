"""环境变量加载模块。

从 .env 文件加载非空环境变量，空值不覆盖已有配置。
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_ENV = PROJECT_ROOT / ".env"


def _load_non_empty_env(path: Path, *, override: bool) -> None:
    r"""加载 .env 中的非空变量。

    python-dotenv 默认会把 ``KEY=`` 这种空值也写入环境变量。
    项目 .env 通常保留空字段作为模板，直接加载空值会覆盖真实配置。

    自定义加载规则：
    - 空值不写入环境变量；
    - ``override=True`` 时强制覆盖已有值；
    - ``override=False`` 时仅在目标变量缺失或为空时才写入。
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
    """

    _load_non_empty_env(LOCAL_ENV, override=True)

    os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")
    os.environ.setdefault("LANGSMITH_TRACING", "false")
    os.environ.setdefault("LANGCHAIN_API_KEY", "")


_loaded = False


def get_env(name: str, default: str = "") -> str:
    """读取环境变量，首次调用前自动执行 :func:`load_environment`。"""

    global _loaded
    if not _loaded:
        load_environment()
        _loaded = True
    return os.environ.get(name, default)


def require_env(name: str) -> str:
    """读取必填环境变量，缺失时抛出 :class:`RuntimeError`。"""

    value = get_env(name).strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value
