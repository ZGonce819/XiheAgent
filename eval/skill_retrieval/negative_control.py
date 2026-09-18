"""负向对照：验证这套评估**真的有测量能力**。

一个恒过的评估比没有评估更糟 —— 它会给人虚假的安全感。所以这里刻意把检索打分
打坏（换成一个完全不看查询、只按目录顺序取前 ``top_k`` 条的实现），再跑一遍。
如果打坏之后命中率门槛仍然通过，说明评估本身有问题，必须立刻发现。

判定方向与正常运行**相反**：这里期望「开启模式命中率」门槛**失败**。

用法::

    .venv/Scripts/python.exe -m eval.skill_retrieval --negative-control
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from pathlib import Path
from typing import Any

import services.skill_retrieval as skill_retrieval
from eval.skill_retrieval.runner import run
from services.skill_retrieval import ALWAYS_ON_SKILLS

HIT_RATE_GATE = "开启模式命中率"
"""负向对照要盯的门槛名。"""


def _broken_select_skills(
    skills: Sequence[Mapping[str, Any]],
    query: str,
    *,
    top_k: int,
    always_on: Collection[str] = ALWAYS_ON_SKILLS,
) -> list[Mapping[str, Any]]:
    """故意退化的「检索」：完全不看 ``query``，只按目录顺序取前 ``top_k`` 条。

    签名与 :func:`services.skill_retrieval.select_skills` 保持一致，这样中间件
    在函数内做 ``from services.skill_retrieval import select_skills`` 时会拿到这个
    替身。

    Args:
        skills: 全部技能。
        query: 查询文本（本替身刻意忽略）。
        top_k: 取多少条。
        always_on: 常驻技能名，仍按契约保留。

    Returns:
        前 ``top_k`` 条技能（常驻技能优先占位）。
    """
    del query  # 负向对照的全部意义就在于不看查询
    always = {str(name).strip().lower() for name in always_on}
    keep: list[Mapping[str, Any]] = [
        item for item in skills if str(item.get("name") or "").strip().lower() in always
    ]
    for item in skills:
        if len(keep) >= top_k:
            break
        if item not in keep:
            keep.append(item)
    return keep


def run_negative_control(label: str, *, reports_dir: Path) -> dict[str, Any]:
    """跑一次「检索被打坏」的评估。

    Args:
        label: 报告目录名。
        reports_dir: 报告根目录。

    Returns:
        报告数据。
    """
    original = skill_retrieval.select_skills
    skill_retrieval.select_skills = _broken_select_skills  # type: ignore[assignment]
    try:
        return run(label, reports_dir=reports_dir)
    finally:
        skill_retrieval.select_skills = original  # type: ignore[assignment]


def hit_rate_gate_failed(payload: dict[str, Any]) -> bool:
    """判断负向对照里命中率门槛是否如期失败。

    Args:
        payload: :func:`run_negative_control` 的返回值。

    Returns:
        命中率门槛存在且未通过时返回 ``True``。
    """
    for gate in payload["gates"]:
        if gate["name"] == HIT_RATE_GATE:
            return not gate["passed"]
    return False
