"""技能检索 eval 的判定规则与指标计算。

这个模块只做「从原始记录算指标」和「按门槛判定」，不碰文件系统、不碰 Agent ——
便于单独验证，也让「判定标准」和「怎么跑」解耦。

## 指标口径

:func:`summarize_mode` 对一种开关模式算出一组指标，其中：

- ``system_chars`` / ``system_tokens``：**整条** system message 的长度，不是只算
  技能那段。因为成本由整条提示词决定，只报技能段会夸大收益。
- ``skills_section_chars``：技能那一段的长度（整条减去基础提示词），这是**开关
  真正控制的量**。
- ``listed_avg``：平均列出多少个技能。
- ``hit_rate``：门槛样本里，期望技能被列出的比例。
- ``always_on_rate``：常驻技能全部在位的比例。
- ``noise_ratio``：列出的技能里，既不是期望技能也不是常驻技能的占比。这个值
  同时受 ``top_k`` 影响，因此**只作参考、不设门槛**。

**这里没有「命中名次」指标，这是有意的。** ``select_skills`` 返回的是**目录原序**
而不是相关度序（见 :mod:`services.skill_retrieval` 的「保持原始顺序」），目的是让
同一份技能目录在多次调用间渲染出稳定文本、不打断 prompt 缓存前缀。因此「期望技能
排第几」量到的是目录位置，与相关度无关 —— 第一版实现就踩了这个坑，把目录位置当成
名次报了出来。要看排序质量，应当看 :func:`summarize_mode` 之外的**敏感度扫描**：
把 ``top_k`` 调小，看命中率掉得多快（见 :func:`eval.skill_retrieval.runner.run` 的
``sweep``）。

## 为什么门槛要这样定

``off`` 与「常驻技能在位」是**逻辑上必然成立**的硬约束，一旦不成立说明代码有
bug，不是「效果不好」。命中率与降幅是**效果门槛**，定在改动前就能论证的水平，
而不是跑完看结果再倒推（那样门槛就没有意义了）。

**降幅门槛的分母是技能段，不是整条提示词。** 这一点值得说明：第一版写成「整条
system message 的降幅 ≥ 60%」，跑出来只有 39.5%。原因不是检索没效果，而是
``get_system_prompt('coding')`` 本身就有一万多 token，其中大部分与技能无关，
开关根本管不到 —— 拿一个不受改动控制的量当门槛，是门槛设计错误。整条提示词的
降幅仍然在报告里给出（它是用户真正感知到的成本），只是不参与判定。
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any

import tiktoken

from eval.skill_retrieval.catalog import Query

ENCODING_NAME = "cl100k_base"
"""估算 token 用的编码。选它是因为 ``tiktoken`` 能离线加载，结果可复现。"""

_ENCODER = tiktoken.get_encoding(ENCODING_NAME)


@dataclass(frozen=True)
class Row:
    """一条「查询 × 开关模式」的原始观测。

    Attributes:
        query_id: 查询标识。
        mode: 开关模式（``off`` / ``on``）。
        expected: 期望被列出的技能名；``None`` 表示没有期望。
        gated: 是否计入命中率门槛。
        listed: 实际列出的技能名，顺序与提示词一致。
        system_chars: 整条 system message 的字符数。
        system_tokens: 整条 system message 的 token 数。
        skills_section_chars: 技能段的字符数。
    """

    query_id: str
    mode: str
    expected: str | None
    gated: bool
    listed: tuple[str, ...]
    system_chars: int
    system_tokens: int
    skills_section_chars: int

    @property
    def hit(self) -> bool | None:
        """期望技能是否被列出；没有期望时返回 ``None``。"""
        if self.expected is None:
            return None
        return self.expected in self.listed


def count_tokens(text: str) -> int:
    """用固定编码数一段文本的 token。

    Args:
        text: 待计数文本。

    Returns:
        token 数。
    """
    return len(_ENCODER.encode(text))


def summarize_mode(
    rows: Sequence[Row],
    *,
    always_on: Collection[str],
    skill_count: int,
) -> dict[str, Any]:
    """汇总一种开关模式的指标。

    Args:
        rows: 该模式下的全部观测。
        always_on: 常驻技能名集合。
        skill_count: 技能目录总条数。

    Returns:
        指标字典，键的含义见模块文档。
    """
    gated = [row for row in rows if row.gated]
    uncalibrated = [row for row in rows if not row.gated]

    hits = [row for row in gated if row.hit]
    always_on_ok = [
        row for row in rows if all(name in row.listed for name in always_on)
    ]
    full_listing = [row for row in rows if len(row.listed) >= skill_count]

    noise_ratios: list[float] = []
    for row in gated:
        relevant = set(always_on) | {row.expected}
        noise = [name for name in row.listed if name not in relevant]
        noise_ratios.append(len(noise) / len(row.listed) if row.listed else 0.0)

    def mean(values: Sequence[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    return {
        "runs": len(rows),
        "gated": len(gated),
        "uncalibrated": len(uncalibrated),
        "listed_avg": mean([len(row.listed) for row in rows]),
        "listed_min": min((len(row.listed) for row in rows), default=0),
        "listed_max": max((len(row.listed) for row in rows), default=0),
        "system_chars_avg": mean([row.system_chars for row in rows]),
        "system_tokens_avg": mean([row.system_tokens for row in rows]),
        "skills_section_chars_avg": mean([row.skills_section_chars for row in rows]),
        "hit_rate": len(hits) / len(gated) if gated else 0.0,
        "hits": len(hits),
        "always_on_rate": len(always_on_ok) / len(rows) if rows else 0.0,
        "full_listing_rate": len(full_listing) / len(rows) if rows else 0.0,
        "noise_ratio": mean(noise_ratios),
        "uncalibrated_rows": [
            {
                "query_id": row.query_id,
                "expected": row.expected,
                "listed": len(row.listed),
                "hit": row.hit,
                "listed_names": list(row.listed[:5]),
            }
            for row in uncalibrated
        ],
    }


def compare(off: dict[str, Any], on: dict[str, Any]) -> dict[str, Any]:
    """算出开启检索相对关闭的变化。

    Args:
        off: ``off`` 模式的指标。
        on: ``on`` 模式的指标。

    Returns:
        含降幅与列出条数变化的字典；分母为 0 时降幅记 0。
    """

    def reduction(before: float, after: float) -> float:
        return (before - after) / before if before else 0.0

    return {
        "system_chars_reduction": reduction(
            off["system_chars_avg"], on["system_chars_avg"]
        ),
        "system_tokens_reduction": reduction(
            off["system_tokens_avg"], on["system_tokens_avg"]
        ),
        "skills_section_chars_reduction": reduction(
            off["skills_section_chars_avg"], on["skills_section_chars_avg"]
        ),
        "listed_drop": off["listed_avg"] - on["listed_avg"],
        "noise_ratio_drop": off["noise_ratio"] - on["noise_ratio"],
        "hit_rate_drop": off["hit_rate"] - on["hit_rate"],
    }


@dataclass(frozen=True)
class Gate:
    """一条验收门槛的判定结果。

    Attributes:
        name: 门槛名。
        target: 门槛要求，写成人能读的句子。
        actual: 实测值，写成人能读的句子。
        passed: 是否通过。
        kind: ``hard`` 表示逻辑上必然成立（不成立即 bug），``effect`` 表示效果门槛。
    """

    name: str
    target: str
    actual: str
    passed: bool
    kind: str


def evaluate_gates(
    off: dict[str, Any],
    on: dict[str, Any],
    diff: dict[str, Any],
    *,
    skill_count: int,
    always_on_count: int,
    sweep: dict[Any, dict[str, Any]],
) -> list[Gate]:
    """按预设门槛判定这次运行。

    门槛在写代码时就定好了（见模块文档），不随结果调整。

    Args:
        off: ``off`` 模式的指标。
        on: ``on`` 模式的指标。
        diff: :func:`compare` 的输出。
        skill_count: 技能目录总条数。
        always_on_count: 常驻技能条数。
        sweep: ``top_k`` 敏感度扫描结果，键为 ``top_k``。

    Returns:
        门槛判定列表。
    """
    gates: list[Gate] = [
        Gate(
            name="关闭模式列出全部技能",
            target=f"每次运行列出 {skill_count} 条",
            actual=f"{off['listed_avg']:.1f} 条（最少 {off['listed_min']}，最多 {off['listed_max']}）",
            passed=off["full_listing_rate"] == 1.0,
            kind="hard",
        ),
        Gate(
            name="常驻技能始终在位",
            target=f"两种模式下 {always_on_count} 条常驻技能每次都出现",
            actual=(
                f"关闭 {off['always_on_rate']:.0%}、"
                f"开启 {on['always_on_rate']:.0%}"
            ),
            passed=off["always_on_rate"] == 1.0 and on["always_on_rate"] == 1.0,
            kind="hard",
        ),
        Gate(
            name="开启模式命中率",
            target="门槛样本命中率 ≥ 90%",
            actual=f"{on['hit_rate']:.1%}（{on['hits']}/{on['gated']}）",
            passed=on["hit_rate"] >= 0.9,
            kind="effect",
        ),
        Gate(
            name="开启模式省下的技能段长度",
            target="技能段字符数降幅 ≥ 60%",
            actual=f"{diff['skills_section_chars_reduction']:.1%}",
            passed=diff["skills_section_chars_reduction"] >= 0.6,
            kind="effect",
        ),
    ]

    if sweep:
        tightest = min(sweep)
        tight = sweep[tightest]
        gates.append(
            Gate(
                name=f"极小名额下仍能命中（top_k={tightest}）",
                target="命中率 ≥ 80%",
                actual=f"{tight['hit_rate']:.1%}（{tight['hits']}/{tight['gated']}）",
                passed=tight["hit_rate"] >= 0.8,
                kind="effect",
            )
        )
    return gates


def query_table(rows: Sequence[Row], queries: Sequence[Query]) -> list[dict[str, Any]]:
    """把观测按查询整理成报告用的明细行。

    Args:
        rows: 全部观测（含两种模式）。
        queries: 查询集，用于补上查询正文与备注。

    Returns:
        每个查询一行，含两种模式下的命中情况。
    """
    by_query: dict[str, dict[str, Row]] = {}
    for row in rows:
        by_query.setdefault(row.query_id, {})[row.mode] = row

    meta = {query.query_id: query for query in queries}
    table: list[dict[str, Any]] = []
    for query_id in sorted(by_query):
        query = meta[query_id]
        off_row = by_query[query_id].get("off")
        on_row = by_query[query_id].get("on")
        table.append(
            {
                "query_id": query_id,
                "text": query.text,
                "expected": query.expected,
                "family": query.family,
                "note": query.note,
                "gated": query.gated,
                "off_listed": len(off_row.listed) if off_row else 0,
                "on_listed": len(on_row.listed) if on_row else 0,
                "on_hit": on_row.hit if on_row else None,
                "on_listed_names": list(on_row.listed) if on_row else [],
            }
        )
    return table
