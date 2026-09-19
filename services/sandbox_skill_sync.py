"""技能变更后的沙箱同步服务模块。

技能是「主机磁盘上的唯一副本 + 沙箱容器里的拷贝」这种双份结构。网页或 Agent
改了主机上的技能之后，容器里的拷贝不会自己更新，而沙箱会话读的是容器里那一份，
于是出现「接口说改成功了，但对话里加载的还是旧技能」。

本模块负责把这个缺口补上，规则由两条决定：

1. **按项目集合决定同步范围。** 会话属于某个项目集合时，同步「全局层 + 该项目
   集合层」；不属于任何项目集合时只同步全局层。与
   :func:`services.skill_registry.sandbox_skill_sources` 的挂载口径严格一致。
2. **只在对话跑完时才动容器。** 会话处于 ``running`` / ``awaiting_approval`` 时
   正在使用容器，中途换掉技能目录会让同一轮回答前后依据不一致，因此只登记一个
   「待同步」标记，等下一次构建智能体前再补上（见
   ``server._apply_pending_skill_refresh``）。

Example:
    >>> from services.sandbox_skill_sync import refresh_sandbox_skills
    >>> refresh_sandbox_skills(scope="global")["synced"]
    [{'thread_id': '...', 'files': 3}]
"""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

# 会话处于这些状态时，说明沙箱里正在跑（或正等待审批继续跑）对话。
# 注意 ``pending`` 不在其中：那表示从未跑过或上一轮已结束，此时容器是空闲的。
BUSY_STATUSES = frozenset({"running", "awaiting_approval"})

_pending_threads: set[str] = set()
_pending_lock = threading.Lock()


def mark_pending_refresh(thread_id: str) -> None:
    """登记一个待同步的会话。

    Args:
        thread_id: 会话唯一标识。
    """
    with _pending_lock:
        _pending_threads.add(thread_id)


def consume_pending_refresh(thread_id: str) -> bool:
    """取出并清除某个会话的待同步标记。

    Args:
        thread_id: 会话唯一标识。

    Returns:
        ``True`` 表示该会话确有欠账需要补同步，且标记已被消费。
    """
    with _pending_lock:
        if thread_id not in _pending_threads:
            return False
        _pending_threads.discard(thread_id)
        return True


def has_pending_refresh(thread_id: str) -> bool:
    """查询某个会话是否有待同步的技能变更。

    Args:
        thread_id: 会话唯一标识。

    Returns:
        有待同步标记时返回 ``True``。
    """
    with _pending_lock:
        return thread_id in _pending_threads


def refresh_sandbox_skills(
    *, scope: str, project_name: str | None = None
) -> dict[str, Any]:
    """把最新的技能集同步进受影响的活跃沙箱。

    只处理当前进程 backend 缓存里还在的沙箱会话。不在缓存里的会话不需要处理：
    它们的沙箱会在下一次构建智能体时重新同步（
    :func:`backends.opensandbox.create_opensandbox_backend` 每次都会同步技能）。

    Args:
        scope: 发生变更的作用域，``global`` / ``project_set`` / ``local``。
            ``local`` 与沙箱无关，直接跳过。
        project_name: ``project_set`` 作用域下变更所属的项目集合名。

    Returns:
        执行摘要，含 ``synced``（已同步）、``pending``（运行中，改为延迟同步）、
        ``failed``（同步失败）、``skipped``（作用域与沙箱无关）与 ``reason``
        （整体未执行的原因）。
    """
    from services.skill_registry import GLOBAL_SCOPE, LOCAL_SCOPE, PROJECT_SET_SCOPE

    summary: dict[str, Any] = {
        "synced": [],
        "pending": [],
        "failed": [],
        "skipped": 0,
        "reason": None,
    }

    if scope == LOCAL_SCOPE:
        # 本地层技能只存在于项目文件夹里，沙箱容器从来不会挂载它。
        summary["reason"] = "本地层技能与沙箱无关"
        return summary

    try:
        from config.app_config import get_settings

        if not get_settings().use_sandbox:
            summary["reason"] = "沙箱未开启"
            return summary
    except Exception:
        summary["reason"] = "读取沙箱配置失败"
        logger.warning("读取沙箱配置失败，跳过技能同步", exc_info=True)
        return summary

    try:
        from server import _BACKENDS
    except Exception:
        summary["reason"] = "无法访问 backend 缓存"
        logger.warning("无法访问 backend 缓存，跳过技能同步", exc_info=True)
        return summary

    from agent.graph.graph import get_store
    from backends.opensandbox import OpenSandboxBackend, sync_sandbox_skills

    store = get_store()
    for thread_id, backend in list(_BACKENDS.items()):
        if not isinstance(backend, OpenSandboxBackend):
            continue

        try:
            thread = store.get_thread(thread_id)
        except Exception:
            logger.warning("读取会话记录失败：thread_id=%s", thread_id, exc_info=True)
            continue
        if not thread:
            continue

        raw_project = thread.get("project_name")
        thread_project = (
            raw_project.strip() if isinstance(raw_project, str) and raw_project.strip() else None
        )

        # 全局层变更影响所有沙箱会话；项目集合层变更只影响该项目集合下的会话。
        if scope == PROJECT_SET_SCOPE and thread_project != project_name:
            continue
        if scope not in {GLOBAL_SCOPE, PROJECT_SET_SCOPE}:
            summary["skipped"] += 1
            continue

        status = str(thread.get("latest_run_status") or "")
        if status in BUSY_STATUSES:
            # 对话正在跑，标记欠账，等下一次构建智能体前补同步。
            mark_pending_refresh(thread_id)
            summary["pending"].append(thread_id)
            logger.info(
                "会话正在执行，技能同步延后：thread_id=%s status=%s", thread_id, status
            )
            continue

        try:
            written = sync_sandbox_skills(backend, thread_project)
        except Exception as exc:
            # 沙箱已被 Docker 回收（超时、手动删除等），缓存中的 backend 已失效。
            # 从缓存中移除并销毁底层资源，避免后续操作继续访问幽灵沙箱。
            if "SANDBOX_NOT_FOUND" in str(exc):
                _BACKENDS.pop(thread_id, None)
                try:
                    backend.destroy()
                except Exception:
                    pass
                logger.info(
                    "沙箱已失效，清除缓存：thread_id=%s", thread_id
                )
                continue
            logger.warning("同步技能到沙箱失败：thread_id=%s", thread_id, exc_info=True)
            summary["failed"].append(thread_id)
            continue
        summary["synced"].append({"thread_id": thread_id, "files": written})

    logger.info(
        "沙箱技能同步结束：scope=%s project=%s synced=%d pending=%d failed=%d",
        scope,
        project_name or "-",
        len(summary["synced"]),
        len(summary["pending"]),
        len(summary["failed"]),
    )
    return summary
