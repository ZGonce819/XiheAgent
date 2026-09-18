"""技能检索（RAG）开关偏好。

技能目录是**每次模型调用**都要注入 system prompt 的固定成本：DeepAgents 的
``SkillsMiddleware`` 会把每个技能的 ``name`` / ``description`` / 路径拼成两行
写进提示词，技能变多以后这笔成本线性上涨，而且主 Agent 与两个子 Agent 各注入一份。

本模块提供用户可控的开关，让用户决定是否按当前任务检索相关技能、只注入命中项。
三种模式：

``off``
    始终注入全部技能，行为与引入检索前逐字一致（**默认值**）。
``auto``
    仅当本次会话挂载的技能数超过 :data:`DEFAULT_AUTO_THRESHOLD` 时才检索。
``on``
    始终检索。

配置持久化到 ``Settings.data_dir/skill_retrieval.json``，与命令审批偏好
（:mod:`config.approval_config`）保持同一套读写形状：JSON 落盘 + 校验 +
``RLock``，读取侧对非法值一律回退到默认值。

Note:
    检索只筛选写进提示词的技能列表，不会切断读取通道。``list_skills`` 工具与
    ``read_file`` 仍然能触达全部技能，因此开关开错不会导致技能永久不可用。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from config.app_config import get_settings

_FILENAME = "skill_retrieval.json"
_LOCK = threading.RLock()

MODES = ("off", "auto", "on")
DEFAULT_MODE = "off"
DEFAULT_TOP_K = 20
DEFAULT_AUTO_THRESHOLD = 60

# 注意：``top_k`` 是列出总数的上限，而 :data:`services.skill_retrieval.ALWAYS_ON_SKILLS`
# 里的几个技能会一直列出、优先占掉名额（``prompt.py`` 按名字引用了它们，被筛掉就会
# 指向一个模型看不到的技能）。因此 ``top_k`` 若小于这几个技能的数量，筛选实际上
# 起不到作用——这是有意保留的，不是 bug。
MIN_TOP_K = 1
MAX_TOP_K = 200
MIN_AUTO_THRESHOLD = 1
MAX_AUTO_THRESHOLD = 1000

MODE_OPTIONS: tuple[dict[str, str], ...] = (
    {
        "id": "off",
        "label": "关闭",
        "description": "始终把全部技能提供给模型，与未开启时完全一致",
    },
    {
        "id": "auto",
        "label": "自动",
        "description": f"技能超过 {DEFAULT_AUTO_THRESHOLD} 个时才筛选，平时全部提供",
    },
    {
        "id": "on",
        "label": "始终开启",
        "description": "每次都按当前任务筛选，只提供最相关的几个",
    },
)

LIMITS: dict[str, dict[str, int]] = {
    "top_k": {"min": MIN_TOP_K, "max": MAX_TOP_K, "default": DEFAULT_TOP_K},
    "auto_threshold": {
        "min": MIN_AUTO_THRESHOLD,
        "max": MAX_AUTO_THRESHOLD,
        "default": DEFAULT_AUTO_THRESHOLD,
    },
}

_cached: tuple[str, int, int, dict[str, Any]] | None = None
"""按 ``(路径, mtime_ns, size)`` 缓存的已落盘配置。

本模块的读取侧会在每次模型调用时被触发（主 Agent 与两个子 Agent 各一次），
因此用 ``stat()`` 结果当缓存键，避免每次都真的读文件。写入侧会主动失效缓存。
"""


def _path() -> Path:
    """返回配置文件的落盘位置。

    Returns:
        ``Settings.data_dir/skill_retrieval.json``。
    """
    return get_settings().data_dir / _FILENAME


def _load_stored() -> dict[str, Any]:
    """读取磁盘上的原始配置。

    Returns:
        解析出的字典；文件不存在、不可读或内容非法时返回空字典。
    """
    global _cached
    path = _path()
    try:
        stat = path.stat()
    except OSError:
        _invalidate_cache()
        return {}

    key = (str(path), stat.st_mtime_ns, stat.st_size)
    with _LOCK:
        if _cached is not None and _cached[:3] == key:
            return _cached[3]

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        value = {}
    if not isinstance(value, dict):
        value = {}

    with _LOCK:
        _cached = (*key, value)
    return value


def _invalidate_cache() -> None:
    """丢弃已落盘配置的缓存，强制下次读取重新读文件。"""
    global _cached
    with _LOCK:
        _cached = None


def _coerce_mode(value: Any) -> str:
    """把读取到的模式折叠到合法值。

    Args:
        value: 原始值。

    Returns:
        合法的模式标识；无法识别时回退到 :data:`DEFAULT_MODE`。
    """
    text = str(value or "").strip().lower()
    return text if text in MODES else DEFAULT_MODE


def _coerce_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    """把读取到的整数夹到合法区间。

    Args:
        value: 原始值。
        default: 无法转换时的回退值。
        minimum: 允许的下界。
        maximum: 允许的上界。

    Returns:
        落在 ``[minimum, maximum]`` 内的整数。
    """
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def _validate_int(
    value: Any, *, name: str, minimum: int, maximum: int, default: int
) -> int:
    """校验调用方传来的整数入参。

    与 :func:`_coerce_int` 的区别是**不做静默夹取**：用户显式提交的值超出范围
    时报错，避免出现「界面显示已保存、实际被改成另一个数」的错觉。

    Args:
        value: 原始入参；``None`` 表示沿用默认值。
        name: 字段中文名，用于错误信息。
        minimum: 允许的下界。
        maximum: 允许的上界。
        default: ``value`` 为 ``None`` 时的取值。

    Returns:
        校验通过的整数。

    Raises:
        ValueError: 无法转为整数，或超出允许区间。
    """
    if value is None:
        return default
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name}必须是整数") from exc
    if not minimum <= number <= maximum:
        raise ValueError(f"{name}需在 {minimum} 到 {maximum} 之间")
    return number


def _effective_preferences() -> dict[str, Any]:
    """返回生效的开关取值（不含展示用元数据）。

    这是给运行时读取的轻量版本：不构造 ``modes`` / ``limits`` 这类只有前端
    才需要的结构，避免在每次模型调用时做无谓的分配。

    Returns:
        含 ``mode`` / ``top_k`` / ``auto_threshold`` 的字典。
    """
    stored = _load_stored()
    return {
        "mode": _coerce_mode(stored.get("mode")),
        "top_k": _coerce_int(
            stored.get("top_k"),
            default=DEFAULT_TOP_K,
            minimum=MIN_TOP_K,
            maximum=MAX_TOP_K,
        ),
        "auto_threshold": _coerce_int(
            stored.get("auto_threshold"),
            default=DEFAULT_AUTO_THRESHOLD,
            minimum=MIN_AUTO_THRESHOLD,
            maximum=MAX_AUTO_THRESHOLD,
        ),
    }


def get_skill_retrieval_preferences() -> dict[str, Any]:
    """返回技能检索开关的当前配置。

    Returns:
        含 ``mode`` / ``top_k`` / ``auto_threshold``，以及供前端渲染的
        ``modes`` 选项元数据与 ``limits`` 取值区间。
    """
    return {
        **_effective_preferences(),
        "modes": [dict(option) for option in MODE_OPTIONS],
        "limits": {key: dict(value) for key, value in LIMITS.items()},
    }


def update_skill_retrieval_preferences(payload: Any) -> dict[str, Any]:
    """写入技能检索开关配置。

    未提供的字段沿用当前值，因此前端可以只提交被改动的那一项。

    Args:
        payload: 含 ``mode`` / ``top_k`` / ``auto_threshold`` 的字典，均可缺省。

    Returns:
        写入后的完整配置，与 :func:`get_skill_retrieval_preferences` 同构。

    Raises:
        ValueError: ``payload`` 不是字典，或任一字段非法。
    """
    if not isinstance(payload, dict):
        raise ValueError("技能检索设置格式无效")

    current = _effective_preferences()

    mode = payload.get("mode")
    if mode is None:
        mode_text = str(current["mode"])
    else:
        mode_text = str(mode).strip().lower()
        if mode_text not in MODES:
            raise ValueError(f"未知的技能检索模式：{mode}")

    stored = {
        "mode": mode_text,
        "top_k": _validate_int(
            payload.get("top_k"),
            name="最多列出的技能数",
            minimum=MIN_TOP_K,
            maximum=MAX_TOP_K,
            default=int(current["top_k"]),
        ),
        "auto_threshold": _validate_int(
            payload.get("auto_threshold"),
            name="开始筛选的技能数",
            minimum=MIN_AUTO_THRESHOLD,
            maximum=MAX_AUTO_THRESHOLD,
            default=int(current["auto_threshold"]),
        ),
    }

    path = _path()
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # 必须显式失效缓存：Windows 的文件时间戳精度约 15ms，连续两次保存很可能
        # 拿到相同的 ``mtime_ns``，若内容长度也没变，光靠 stat 做缓存键会读到旧值。
        _invalidate_cache()
    return get_skill_retrieval_preferences()


def skill_retrieval_mode() -> str:
    """返回当前生效的检索模式。

    Returns:
        ``off`` / ``auto`` / ``on``。
    """
    return str(_effective_preferences()["mode"])


def skill_retrieval_active(skill_count: int) -> bool:
    """判断给定技能数量下是否应该走检索。

    这是模式语义的唯一落点：``off`` 恒不检索，``on`` 恒检索，``auto`` 只在
    技能数**超过**阈值时检索。

    Args:
        skill_count: 本次会话实际挂载的技能数量。

    Returns:
        应该走检索返回 ``True``。
    """
    preferences = _effective_preferences()
    mode = preferences["mode"]
    if mode == "off":
        return False
    if mode == "on":
        return True
    return skill_count > int(preferences["auto_threshold"])
