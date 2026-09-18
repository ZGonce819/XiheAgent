"""自动化任务的循环状态。

自动化任务目前只落盘了「定义」（:mod:`config.automation_config`），没有地方保存
**跨轮的状态**：上一轮做到哪、试过什么没成功、下一步该干什么。缺了这份状态，
按计划重复触发同一个自动化就只是「把同一段指令再喂一遍」，而不是循环。

本模块补上这个落点。每个自动化一份 JSON，位置
``Settings.data_dir/automation_state/<automationId>.json``，与定义文件
（``data_dir/automations.json``）分开存，理由有三条：

1. 不同自动化可能同时跑，分开存就不会互相覆盖；
2. 单个自动化的历史变长不会拖累其它自动化；
3. 删除自动化时直接删对应文件即可。

为什么不复用现有三处存储：

``store/event_repo.py`` 的 ``run_events``
    是临时表，每次运行开始会被 ``clear_run_events`` 清空，只服务当前这一轮。
``store/finding_repo.py`` 的 ``review_findings``
    只有 ``thread_id`` 维度、没有任何更新方法、``status`` 恒为 ``'open'``。
``agent/memory/repo_update.py`` 的仓库记忆
    是纯规则提取，只认完成时态动词，决策理由落不进去。

Note:
    本模块只读写状态文件，不触发任何自动化，也不改变现有任务的行为。
    调用方（将来的循环调度器）负责在每轮开始前读、结束后写。

Note:
    模块内用 ``RLock`` 串行化同一进程里的读改写，**不跨进程**。将来若出现多个
    进程同时跑同一个自动化，需要另外加文件锁。

Note:
    这里**不做读取缓存**。状态由循环自己频繁改写，而 Windows 的文件时间戳精度
    约 15ms，靠 ``st_mtime_ns`` 当缓存键会读到上一轮的值——对状态来说是致命的，
    代价只是一次几十字节的读盘。
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import threading
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config.app_config import get_settings

_STATE_DIRNAME = "automation_state"
_LOCK = threading.RLock()

SCHEMA_VERSION = 2
"""状态结构版本。v2 相对 v1 多了预算里的 ``maxConsecutiveFailures`` 与每轮的
``verification``；旧文件缺这些键，读取时补默认值即可。"""

OUTCOMES = ("succeeded", "failed", "blocked")
"""一轮运行的结果取值。``blocked`` 指这一轮没做完但也没有报错，需要下一轮接着做。"""

MAX_RUNS_KEPT = 20
"""每份状态最多保留多少轮历史，超出的按时间从旧到新丢弃。"""

MAX_ATTEMPTS_KEPT = 20
"""最多保留多少条「试过但没成功」的记录。"""

MAX_CHECKS_KEPT = 20
"""每轮最多保留多少条验证结果。"""

MAX_APPROVAL_VOTES_KEPT = 5
"""每轮最多保留几次审批表决的记录。

必须不小于 ``services.automation_loop.MAX_APPROVAL_VOTES_PER_ROUND``（一轮最多
表决几次），否则「用满上限退回人工」的那一轮会被截掉几条 —— 而那一条正是最该被
看见的。``config`` 不能反向依赖 ``services``，所以这两个值只能各自写死、各自注释。
"""

VOTE_DECISIONS = ("approve", "reject", "undecided", "refused")
"""一次审批表决的结论取值。

后两个都表示**没有得出可执行的结论**，这一轮要交回人工：``undecided`` 是评审没能
给出结论（报错、超时、返回不合法结构），``refused`` 是压根不该表决（例如命令按当前
策略已属硬拒绝）。
"""

_VOTER_ROLE_LIMIT = 8
"""每条表决最多保留几个评审的票。比实际的三个宽 —— 多几个角色也不该被无声截掉。"""

_EXECUTED_CHECK_STATUSES = frozenset({"passed", "failed"})
"""这些状态表示那条检查真的跑出了退出码。

与 ``services.automation_verification`` 里的那份同义 —— 那边落盘、这边读回，
两边都要认同一件事才算数。
"""

MAX_SUMMARY_CHARS = 1000
MAX_TEXT_CHARS = 300
"""单条文本字段的截断长度，防止状态文件无上限膨胀。"""

MAX_FEEDBACK_CHARS = 3000
"""上一轮验证反馈的截断长度。比摘要宽松：反馈里带着失败输出的末尾，
下一轮要靠它定位问题。"""

_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
"""合法的自动化 id。定义侧的 id 是 ``uuid4()[:8]``，这里留宽一点但挡掉路径字符。"""


def _now() -> str:
    """返回当前时间的 ISO 8601 文本。

    Returns:
        UTC 时间，带时区偏移，例如 ``2026-09-18T04:20:00+00:00``。
    """
    return datetime.now(UTC).isoformat()


def _check_id(automation_id: Any) -> str:
    """校验自动化 id 可以安全地当作文件名。

    Args:
        automation_id: 待校验的 id。

    Returns:
        去掉首尾空白后的 id。

    Raises:
        ValueError: id 为空、含路径分隔符或其它非法字符。
    """
    text = str(automation_id or "").strip()
    if not _ID_PATTERN.match(text):
        raise ValueError(f"自动化任务 id 非法：{automation_id!r}")
    return text


def _clip(value: Any, limit: int) -> str:
    """把文本裁到指定长度。

    Args:
        value: 原始值，``None`` 视为空串。
        limit: 保留的最大字符数。

    Returns:
        裁好的文本，超长时保留前 ``limit`` 个字符并追加 ``...``。
    """
    text = str(value or "").strip()
    return text if len(text) <= limit else text[:limit] + "..."


def _clip_list(values: Any, limit: int) -> list[str]:
    """把字符串序列逐条裁剪并去空。

    Args:
        values: 可迭代对象；``None`` 视为空。
        limit: 单条文本的最大字符数。

    Returns:
        裁剪后的字符串列表。
    """
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    result = []
    for item in values:
        text = _clip(item, limit)
        if text:
            result.append(text)
    return result


def _verification_executed(verification: dict[str, Any]) -> bool:
    """这一轮的检查项有没有真的跑起来过。

    ``executed`` 是后加的字段，老状态文件里没有。缺了它就从每条的结论推 ——
    **不能默认成「跑过」**，那正是要消掉的误判：默认配置（仓库在沙箱容器里）下
    每一轮都跑不成，默认成「跑过」等于把环境问题记成「没通过」。

    读取侧的 ``runs`` 是原样透传的（:func:`_normalize` 只过滤非字典项），所以这条
    推导不能只写在写入口。

    Args:
        verification: 一轮的验证结论。

    Returns:
        有任意一条检查的结论是 ``passed`` / ``failed`` 时返回 ``True``。
    """
    executed = verification.get("executed")
    if isinstance(executed, bool):
        return executed
    checks = verification.get("checks")
    if not isinstance(checks, list):
        return False
    return any(
        isinstance(item, dict) and item.get("status") in _EXECUTED_CHECK_STATUSES
        for item in checks
    )


def _normalize_verification(raw: Any) -> dict[str, Any] | None:
    """把一轮的验证结论归一成固定形状。

    形状由 :meth:`services.automation_verification.VerificationResult.as_state_dict`
    产出；这里只做收窄与裁剪，不重新判断对错。传进来的东西认不出来时返回
    ``None``，而不是编一个「通过」出来 —— 验证结论缺失必须看得出来。

    Args:
        raw: 验证结论，通常是一个字典；``None`` 表示这一轮没做验证。

    Returns:
        含 ``passed`` / ``executed`` / ``summary`` / ``checks`` 的字典，或 ``None``。
    """
    if not isinstance(raw, dict) or "passed" not in raw:
        return None

    checks: list[dict[str, Any]] = []
    for item in raw.get("checks") or []:
        if not isinstance(item, dict):
            continue
        exit_code = item.get("exitCode")
        if exit_code is not None:
            try:
                exit_code = int(exit_code)
            except (TypeError, ValueError):
                exit_code = None
        checks.append(
            {
                "command": _clip(item.get("command"), MAX_TEXT_CHARS),
                "description": _clip(item.get("description"), MAX_TEXT_CHARS),
                "status": _clip(item.get("status"), 32),
                "exitCode": exit_code,
            }
        )
        if len(checks) >= MAX_CHECKS_KEPT:
            break

    executed = _verification_executed(raw)

    return {
        "passed": bool(raw.get("passed")),
        "executed": executed,
        "summary": _clip(raw.get("summary"), MAX_SUMMARY_CHARS),
        "checks": checks,
    }


def _normalize_approval_votes(raw: Any) -> list[dict[str, Any]]:
    """把一轮里的审批表决记录归一成固定形状。

    形状由 :meth:`services.approval_vote.VoteOutcome.as_state_dict` 产出；这里只做
    收窄与裁剪，不重新判断对错。认不出来的整条丢掉 —— 表决记录是用来**追责**的，
    宁少一条，也不要留一条字段残缺、看不出谁投了什么的。

    Note:
        读取侧不用管这个字段：老状态文件里没有 ``approvalVotes``，缺了就是「那一轮
        没有表决」，本来就是事实，不需要像 ``executed`` 那样从别的字段推。

    Args:
        raw: 表决记录，通常是一个列表或元组；``None`` 表示这一轮没有表决。

    Returns:
        归一后的记录列表，最多 :data:`MAX_APPROVAL_VOTES_KEPT` 条。
    """
    # 收列表**和元组**：``automation_loop`` 交过来的是 ``RoundResult.approval_votes``，
    # 那是个元组。只认 ``list`` 会把真实轮次的表决记录静默丢光。
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        return []

    records: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        decision = str(item.get("decision") or "")
        if decision not in VOTE_DECISIONS:
            continue
        votes: list[dict[str, Any]] = []
        for vote in item.get("votes") or []:
            if not isinstance(vote, dict):
                continue
            votes.append(
                {
                    "role": _clip(vote.get("role"), 64),
                    "label": _clip(vote.get("label"), 32),
                    "approve": bool(vote.get("approve")),
                    "reason": _clip(vote.get("reason"), MAX_TEXT_CHARS),
                    "error": _clip(vote.get("error"), MAX_TEXT_CHARS),
                }
            )
            if len(votes) >= _VOTER_ROLE_LIMIT:
                break
        approve_count = 0
        reject_count = 0
        with contextlib.suppress(TypeError, ValueError):
            approve_count = max(0, int(item.get("approve") or 0))
        with contextlib.suppress(TypeError, ValueError):
            reject_count = max(0, int(item.get("reject") or 0))
        records.append(
            {
                "commands": _clip_list(item.get("commands"), MAX_TEXT_CHARS),
                "approve": approve_count,
                "reject": reject_count,
                "decision": decision,
                "reason": _clip(item.get("reason"), MAX_SUMMARY_CHARS),
                "votes": votes,
            }
        )
        if len(records) >= MAX_APPROVAL_VOTES_KEPT:
            break
    return records


def state_dir() -> Path:
    """返回循环状态的存放目录。

    Returns:
        ``Settings.data_dir/automation_state``。
    """
    return get_settings().data_dir / _STATE_DIRNAME


def state_path(automation_id: Any) -> Path:
    """返回某个自动化的状态文件路径。

    Args:
        automation_id: 自动化任务 id。

    Returns:
        状态文件的完整路径；文件不一定存在。

    Raises:
        ValueError: id 非法。
    """
    return state_dir() / f"{_check_id(automation_id)}.json"


def initial_state(automation_id: Any) -> dict[str, Any]:
    """构造一份全新的空状态。

    Args:
        automation_id: 自动化任务 id。

    Returns:
        含 ``version`` / ``automationId`` / ``cursor`` / ``budget`` / ``stop``
        / ``runs`` / ``attempts`` 的初始状态。
    """
    return {
        "version": SCHEMA_VERSION,
        "automationId": _check_id(automation_id),
        "updatedAt": _now(),
        "cursor": {},
        "budget": {
            "maxRuns": 0,
            "runsUsed": 0,
            "maxTokens": 0,
            "tokensUsed": 0,
            "maxConsecutiveFailures": 0,
        },
        "stop": {"reached": False, "reason": None, "at": None},
        "runs": [],
        "attempts": [],
        "lastFeedback": "",
    }


def _normalize(raw: Any, automation_id: str) -> dict[str, Any]:
    """把磁盘上读到的内容补齐成完整状态。

    读取侧一律容错：文件被手工改坏、字段缺失、类型不对都退回默认值，
    不能让一份坏状态把循环卡死。

    Args:
        raw: 从磁盘解析出的对象。
        automation_id: 自动化任务 id，用于兜底。

    Returns:
        结构完整的状态字典。
    """
    state = initial_state(automation_id)
    if not isinstance(raw, dict):
        return state

    cursor = raw.get("cursor")
    state["cursor"] = dict(cursor) if isinstance(cursor, dict) else {}

    budget = raw.get("budget")
    if isinstance(budget, dict):
        for key in ("maxRuns", "runsUsed", "maxTokens", "tokensUsed", "maxConsecutiveFailures"):
            with contextlib.suppress(TypeError, ValueError):
                state["budget"][key] = max(0, int(budget.get(key) or 0))

    stop = raw.get("stop")
    if isinstance(stop, dict):
        state["stop"] = {
            "reached": bool(stop.get("reached")),
            "reason": _clip(stop.get("reason"), MAX_SUMMARY_CHARS) or None,
            "at": str(stop.get("at") or "") or None,
        }

    for key in ("runs", "attempts"):
        items = raw.get(key)
        if isinstance(items, list):
            state[key] = [item for item in items if isinstance(item, dict)]

    updated = str(raw.get("updatedAt") or "").strip()
    if updated:
        state["updatedAt"] = updated

    state["lastFeedback"] = _clip(raw.get("lastFeedback"), MAX_FEEDBACK_CHARS)
    return state


def load_state(automation_id: Any) -> dict[str, Any]:
    """读取某个自动化的循环状态。

    文件不存在、不可读或内容损坏时返回初始状态，不抛异常。

    Args:
        automation_id: 自动化任务 id。

    Returns:
        结构完整的状态字典。

    Raises:
        ValueError: id 非法。
    """
    identifier = _check_id(automation_id)
    path = state_path(identifier)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return initial_state(identifier)
    return _normalize(raw, identifier)


def save_state(automation_id: Any, state: dict[str, Any]) -> dict[str, Any]:
    """把状态写回磁盘。

    先写同目录下的临时文件再 ``os.replace`` 顶替，避免进程中途退出时留下
    半截 JSON——状态文件被写坏会直接让下一轮读到空历史。

    Args:
        automation_id: 自动化任务 id。
        state: 要写入的状态字典。

    Returns:
        实际写入的状态（``updatedAt`` 已刷新）。

    Raises:
        ValueError: id 非法或 ``state`` 不是字典。
    """
    if not isinstance(state, dict):
        raise ValueError("自动化任务状态必须是字典")
    identifier = _check_id(automation_id)
    payload = _normalize(state, identifier)
    payload["automationId"] = identifier
    payload["version"] = SCHEMA_VERSION
    payload["updatedAt"] = _now()

    path = state_path(identifier)
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f"{path.name}.tmp")
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temp, path)
    return payload


def reset_state(automation_id: Any) -> dict[str, Any]:
    """把状态清空成初始状态并落盘。

    Args:
        automation_id: 自动化任务 id。

    Returns:
        清空后的状态。

    Raises:
        ValueError: id 非法。
    """
    return save_state(automation_id, initial_state(automation_id))


def delete_state(automation_id: Any) -> bool:
    """删除某个自动化的状态文件。

    Args:
        automation_id: 自动化任务 id。

    Returns:
        删掉了返回 ``True``；文件本来就不存在返回 ``False``。

    Raises:
        ValueError: id 非法。
    """
    path = state_path(automation_id)
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def record_run(
    automation_id: Any,
    *,
    outcome: str,
    summary: str = "",
    run_id: str | None = None,
    thread_id: str | None = None,
    changed: Any = None,
    next_steps: Any = None,
    verification: dict[str, Any] | None = None,
    tokens: int = 0,
    waiting_approval: bool = False,
    approval_votes: Any = None,
    started_at: str | None = None,
    finished_at: str | None = None,
) -> dict[str, Any]:
    """记录一轮运行的结果，并把这轮的用量计入预算。

    这是循环写状态的主要入口。记完之后再调 :func:`stop_status` 就能判断
    下一轮该不该继续。

    Args:
        automation_id: 自动化任务 id。
        outcome: 本轮结果，取值见 :data:`OUTCOMES`。
        summary: 这一轮做了什么、结果如何。
        run_id: 本轮运行 id；不传则生成一个。
        thread_id: 本轮对应的会话 id。
        changed: 本轮改动的文件或对象列表。
        next_steps: 留给下一轮的待办列表。
        verification: 本轮的验证结论，形状见 :func:`_normalize_verification`。
        tokens: 本轮消耗的 token 数，累加进预算。
        waiting_approval: 这一轮是不是停在等人工确认。是的话循环会停下来等人，
            不再把它当成一轮普通的「没做完」—— 无人值守的循环里，没人会去点那个
            确认，下一轮还会卡在同一个地方。
        approval_votes: 这一轮里几次审批表决的记录，形状见
            :func:`_normalize_approval_votes`。**得不出结论的那几次也要传进来**：
            「开了全自动表决但这次没能定」必须看得出来，只记放行的会让人以为
            这一轮没有任何人工介入。
        started_at: 本轮开始时间；不传则留空。
        finished_at: 本轮结束时间；不传则用当前时间。

    Returns:
        写入后的完整状态。

    Raises:
        ValueError: id 非法，或 ``outcome`` 不在 :data:`OUTCOMES` 中。
    """
    if outcome not in OUTCOMES:
        raise ValueError(f"未知的运行结果：{outcome}")

    state = load_state(automation_id)
    state["runs"].insert(
        0,
        {
            "runId": str(run_id or uuid.uuid4().hex[:12]),
            "threadId": str(thread_id) if thread_id else None,
            "startedAt": str(started_at) if started_at else None,
            "finishedAt": str(finished_at or _now()),
            "outcome": outcome,
            "summary": _clip(summary, MAX_SUMMARY_CHARS),
            "changed": _clip_list(changed, MAX_TEXT_CHARS),
            "nextSteps": _clip_list(next_steps, MAX_TEXT_CHARS),
            "verification": _normalize_verification(verification),
            "waitingApproval": bool(waiting_approval),
            "approvalVotes": _normalize_approval_votes(approval_votes),
        },
    )
    del state["runs"][MAX_RUNS_KEPT:]

    budget = state["budget"]
    budget["runsUsed"] = int(budget["runsUsed"]) + 1
    with contextlib.suppress(TypeError, ValueError):
        budget["tokensUsed"] = int(budget["tokensUsed"]) + max(0, int(tokens))
    return save_state(automation_id, state)


def record_attempt(
    automation_id: Any,
    *,
    tried: str,
    result: str = "",
    reason: str = "",
) -> dict[str, Any]:
    """记录一次「试过了但没成功」的尝试。

    这张表的作用是让循环别在同一轮又一轮里重复同一个无效做法。Addy Osmani
    举的状态文件例子里，每个待办下面都挂着「已尝试：xxx → 无效」。

    Args:
        automation_id: 自动化任务 id。
        tried: 试了什么。
        result: 结果如何，例如「无效」。
        reason: 为什么无效。

    Returns:
        写入后的完整状态。

    Raises:
        ValueError: id 非法。
    """
    state = load_state(automation_id)
    state["attempts"].insert(
        0,
        {
            "at": _now(),
            "tried": _clip(tried, MAX_TEXT_CHARS),
            "result": _clip(result, MAX_TEXT_CHARS),
            "reason": _clip(reason, MAX_TEXT_CHARS),
        },
    )
    del state["attempts"][MAX_ATTEMPTS_KEPT:]
    return save_state(automation_id, state)


def set_last_feedback(automation_id: Any, text: str) -> dict[str, Any]:
    """记下上一轮的验证反馈。

    只留**最近一条**，不按轮次累积：循环真正需要的只是「上一次为什么没过」，
    更早的反馈已经被后面的覆盖了。传空串表示这一轮通过了，清掉旧的反馈。

    Args:
        automation_id: 自动化任务 id。
        text: 反馈正文；空串表示清空。

    Returns:
        写入后的完整状态。

    Raises:
        ValueError: id 非法。
    """
    state = load_state(automation_id)
    state["lastFeedback"] = _clip(text, MAX_FEEDBACK_CHARS)
    return save_state(automation_id, state)


def set_cursor(automation_id: Any, values: dict[str, Any]) -> dict[str, Any]:
    """更新游标，也就是「上一轮看到哪」。

    循环靠它避免重复处理同一批工作，例如上次读到的提交号或上次运行时间。
    只覆盖传入的键，其余保持不变。

    Args:
        automation_id: 自动化任务 id。
        values: 要写入的键值对。

    Returns:
        写入后的完整状态。

    Raises:
        ValueError: id 非法或 ``values`` 不是字典。
    """
    if not isinstance(values, dict):
        raise ValueError("游标必须是字典")
    state = load_state(automation_id)
    state["cursor"].update(values)
    return save_state(automation_id, state)


def set_budget(
    automation_id: Any,
    *,
    max_runs: int | None = None,
    max_tokens: int | None = None,
    max_consecutive_failures: int | None = None,
) -> dict[str, Any]:
    """设置停止条件的预算上限。

    上限为 ``0`` 表示不限制。停止条件必须机器可判定，否则循环停不下来。

    Args:
        automation_id: 自动化任务 id。
        max_runs: 最多跑多少轮；``None`` 表示不改。
        max_tokens: 最多累计消耗多少 token；``None`` 表示不改。
        max_consecutive_failures: 连续多少轮未通过验证就停下交给人；``None`` 表示不改。

    Returns:
        写入后的完整状态。

    Raises:
        ValueError: id 非法，或传入负数。
    """
    state = load_state(automation_id)
    for key, value, label in (
        ("maxRuns", max_runs, "最多运行轮数"),
        ("maxTokens", max_tokens, "最多消耗 token 数"),
        ("maxConsecutiveFailures", max_consecutive_failures, "连续未通过次数上限"),
    ):
        if value is None:
            continue
        if int(value) < 0:
            raise ValueError(f"{label}不能为负数")
        state["budget"][key] = int(value)
    return save_state(automation_id, state)


def mark_stopped(automation_id: Any, reason: str) -> dict[str, Any]:
    """显式把循环标记为停止。

    给「预算还没用完，但已经没有该做的事了」这种情况用。

    Args:
        automation_id: 自动化任务 id。
        reason: 停止原因，会展示给人看。

    Returns:
        写入后的完整状态。

    Raises:
        ValueError: id 非法或 ``reason`` 为空。
    """
    text = _clip(reason, MAX_SUMMARY_CHARS)
    if not text:
        raise ValueError("停止原因不能为空")
    state = load_state(automation_id)
    state["stop"] = {"reached": True, "reason": text, "at": _now()}
    return save_state(automation_id, state)


def clear_stopped(automation_id: Any) -> dict[str, Any]:
    """清除显式的停止标记，让循环可以继续。

    Args:
        automation_id: 自动化任务 id。

    Returns:
        写入后的完整状态。

    Raises:
        ValueError: id 非法。
    """
    state = load_state(automation_id)
    state["stop"] = {"reached": False, "reason": None, "at": None}
    return save_state(automation_id, state)


def clear_waiting_approval(automation_id: Any) -> dict[str, Any]:
    """清掉「最近一轮停在等人工确认」这个标记，让循环可以继续。

    人工处理完待审批的命令之后再调。这个标记不清的话，:func:`stop_decision`
    会一直因为那一轮停着 —— 审批已经处理完了，循环却再也起不来。

    Args:
        automation_id: 自动化任务 id。

    Returns:
        写入后的完整状态。

    Raises:
        ValueError: id 非法。
    """
    state = load_state(automation_id)
    runs = state["runs"]
    if runs:
        runs[0]["waitingApproval"] = False
    return save_state(automation_id, state)


def _round_failed(run: dict[str, Any]) -> bool:
    """判断一轮算不算没通过。

    有验证结论时以验证结论为准；没做验证时退回看 ``outcome``。

    **「没跑成验证」不算没通过。** 一条检查都没执行时（默认配置下仓库在沙箱容器里
    就是这种），我们不知道活儿干得怎么样 —— 那说的是环境不通，不是活儿没干好。
    算进来的话，``maxConsecutiveFailures`` 会数着环境问题把任务停掉，而停的理由
    写着「未通过验证」，把人指去一个完全错误的方向。

    Args:
        run: 一条运行记录。

    Returns:
        这一轮算未通过返回 ``True``。
    """
    verification = run.get("verification")
    if isinstance(verification, dict) and "passed" in verification:
        if verification["passed"]:
            return False
        return _verification_executed(verification)
    return run.get("outcome") == "failed"


def failure_streak(automation_id: Any) -> int:
    """统计从最近一轮往前、连续多少轮没通过。

    这是「连续失败太多次就停下来交给人」这条停止条件的依据。上限受
    :data:`MAX_RUNS_KEPT` 影响：连续超过该轮数的历史会被丢弃，因此返回值不会
    超过它 —— 连续 20 轮没过已经远超任何合理的重试上限。

    Args:
        automation_id: 自动化任务 id。

    Returns:
        连续未通过的轮数；最近一轮是通过的则为 0。

    Raises:
        ValueError: id 非法。
    """
    streak = 0
    for run in load_state(automation_id)["runs"]:
        if not _round_failed(run):
            break
        streak += 1
    return streak


def _waiting_approval_reason(run: dict[str, Any]) -> str:
    """给「停在等人工确认」这一条拼一句给人看的原因。

    原因直接取那一轮的 summary —— 跑那一轮的时候已经把待处理的命令列进去了。

    Args:
        run: 最近一条运行记录。

    Returns:
        给人看的中文短句。
    """
    summary = str(run.get("summary") or "").strip()
    if not summary:
        return "上一轮停在等待人工确认，需要有人处理之后才会继续"
    return _clip(summary, MAX_SUMMARY_CHARS)


def stop_decision(automation_id: Any) -> tuple[str | None, str | None]:
    """算出循环是**因为哪一条**停下的，以及给人看的原因。

    停止判定有五个来源，恢复方式各不相同：显式标记和「停在等人工确认」可以直接
    清掉；轮数 / token / 连续未通过这三条要改预算上限才有效。只把「停了」告诉
    用户，他会去点一个点了没反应的按钮。

    Args:
        automation_id: 自动化任务 id。

    Returns:
        ``(来源, 原因)``。来源是 ``"marked"`` / ``"awaiting_approval"`` /
        ``"max_runs"`` / ``"max_tokens"`` / ``"failures"`` 之一；不该停时两者
        都是 ``None``。

    Raises:
        ValueError: id 非法。
    """
    state = load_state(automation_id)
    stop = state["stop"]
    if stop.get("reached"):
        return "marked", str(stop.get("reason") or "已标记停止")

    # 停在等人工确认要排在上限之前：这一条是「需要你动手」，比「额度用完了」
    # 更紧急 —— 只报上限的话，人以为调高上限就能继续，其实还是卡在同一个地方。
    runs = state["runs"]
    if runs and runs[0].get("waitingApproval"):
        return "awaiting_approval", _waiting_approval_reason(runs[0])

    budget = state["budget"]
    max_runs = int(budget.get("maxRuns") or 0)
    if max_runs and int(budget.get("runsUsed") or 0) >= max_runs:
        return "max_runs", f"已达最多运行轮数（{max_runs} 轮）"

    max_tokens = int(budget.get("maxTokens") or 0)
    if max_tokens and int(budget.get("tokensUsed") or 0) >= max_tokens:
        return "max_tokens", f"已达最多 token 数（{max_tokens}）"

    max_failures = int(budget.get("maxConsecutiveFailures") or 0)
    if max_failures and failure_streak(automation_id) >= max_failures:
        return "failures", f"已连续 {max_failures} 轮未通过验证，需要人工介入"
    return None, None


def stop_status(automation_id: Any) -> tuple[bool, str | None]:
    """判断循环是否应该停下。

    五个来源合在一起判断：显式标记、停在等人工确认、轮数上限、token 上限、连续
    未通过次数。需要知道是哪一条触发的，用 :func:`stop_decision`。

    Args:
        automation_id: 自动化任务 id。

    Returns:
        ``(是否停止, 原因)``；不该停时原因为 ``None``。

    Raises:
        ValueError: id 非法。
    """
    source, reason = stop_decision(automation_id)
    return source is not None, reason


def _verification_mark(verification: dict[str, Any]) -> str:
    """给一轮的验证结论挑一个说法。

    「没跑成」要单独一个说法：写成「未通过」会让人以为检查项跑过并且失败了，
    从而去改代码，实际该改的是「为什么跑不起来」。

    Args:
        verification: 归一化之后的验证结论。

    Returns:
        ``验证通过`` / ``验证未通过`` / ``验证未执行`` 三者之一。
    """
    if verification.get("passed"):
        return "验证通过"
    if not _verification_executed(verification):
        return "验证未执行"
    return "验证未通过"


def _vote_mark(decision: Any) -> str:
    """给一次审批表决挑一个说法。

    「没定下来」和「否决」必须分开说：写成「否决」会让人以为有人看过并否掉了这条
    命令，实际该看的是「为什么表决没能得出结论」。与 :func:`_verification_mark`
    把「未执行」和「未通过」分开是同一条道理。

    Args:
        decision: 表决结论取值。

    Returns:
        ``审批表决放行`` / ``审批表决否决`` / ``审批表决未定`` / ``审批表决未进行``。
    """
    return {
        "approve": "审批表决放行",
        "reject": "审批表决否决",
        "undecided": "审批表决未定",
        "refused": "审批表决未进行",
    }.get(str(decision or ""), "审批表决")


def _render_vote(vote: dict[str, Any]) -> str:
    """把一次表决渲染成一行给人看的文本。

    Args:
        vote: 归一化之后的表决记录。

    Returns:
        单行文本，例如 ``审批表决放行：2/3 票同意放行（命令：git push）``。
    """
    line = f"    - {_vote_mark(vote.get('decision'))}：{vote.get('reason') or '（无说明）'}"
    commands = vote.get("commands") or []
    if commands:
        line += f"（命令：{'；'.join(commands)}）"
    return line


def render_state(automation_id: Any) -> str:
    """把状态渲染成给人看的纯文本。

    用途有两个：在界面上展示，以及将来作为一段上下文注入提示词，让下一轮
    知道自己上一轮干到哪、哪些做法已经证明没用。

    Args:
        automation_id: 自动化任务 id。

    Returns:
        多行纯文本；没有任何记录时也会返回一份说明性的骨架。

    Raises:
        ValueError: id 非法。
    """
    state = load_state(automation_id)
    budget = state["budget"]
    lines = [
        f"# 自动化任务循环状态（{state['automationId']}）",
        "",
        f"最后更新：{state['updatedAt']}",
        f"已运行 {budget['runsUsed']} 轮，累计 {budget['tokensUsed']} token",
    ]

    max_runs = int(budget.get("maxRuns") or 0)
    max_tokens = int(budget.get("maxTokens") or 0)
    max_failures = int(budget.get("maxConsecutiveFailures") or 0)
    if max_runs or max_tokens or max_failures:
        limits = []
        if max_runs:
            limits.append(f"最多 {max_runs} 轮")
        if max_tokens:
            limits.append(f"最多 {max_tokens} token")
        if max_failures:
            limits.append(f"连续 {max_failures} 轮未通过就停")
        lines.append("预算：" + "，".join(limits))

    streak = failure_streak(automation_id)
    if streak:
        lines.append(f"已连续 {streak} 轮未通过验证")

    stopped, reason = stop_status(automation_id)
    lines.append(f"状态：{'已停止（' + str(reason) + '）' if stopped else '可继续'}")

    cursor = state["cursor"]
    if cursor:
        lines += ["", "## 上次处理到哪"]
        lines += [f"- {key}：{value}" for key, value in cursor.items()]

    feedback = str(state.get("lastFeedback") or "").strip()
    if feedback:
        lines += ["", "## 上一轮验证反馈", "", feedback]

    runs = state["runs"]
    if runs:
        lines += ["", f"## 最近 {len(runs)} 轮"]
        for run in runs:
            lines.append(
                f"- {run.get('finishedAt') or '未知时间'} [{run.get('outcome')}] "
                f"{run.get('summary') or '（无摘要）'}"
            )
            verification = run.get("verification")
            if isinstance(verification, dict):
                lines.append(
                    f"    - {_verification_mark(verification)}："
                    f"{verification.get('summary') or '（无说明）'}"
                )
            for vote in run.get("approvalVotes") or []:
                if isinstance(vote, dict):
                    lines.append(_render_vote(vote))
            for item in run.get("changed") or []:
                lines.append(f"    - 改动：{item}")
            for item in run.get("nextSteps") or []:
                lines.append(f"    - 待办：{item}")

    attempts = state["attempts"]
    if attempts:
        lines += ["", "## 试过但没成功的做法"]
        for attempt in attempts:
            detail = attempt.get("reason") or "（未记录原因）"
            lines.append(
                f"- {attempt.get('at') or '未知时间'} 试过：{attempt.get('tried')}"
                f" → {attempt.get('result') or '无效'}；{detail}"
            )

    if not runs and not attempts:
        lines += ["", "还没有任何运行记录。"]
    return "\n".join(lines)
