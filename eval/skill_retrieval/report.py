"""技能检索 eval 的报告输出。

一次运行产出两个文件，放在 ``reports/<标签>/`` 下：

``report.json``
    机器可读的完整数据，含逐条明细。便于跨次比对或喂给别的脚本。
``report.md``
    人读的结论。**先给结论与门槛判定，再给数据**，因为多数人只想看前两节。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _fmt_pct(value: float) -> str:
    """把 0~1 的比例格式化成百分比。

    Args:
        value: 比例。

    Returns:
        形如 ``87.3%`` 的字符串。
    """
    return f"{value * 100:.1f}%"


def _verdict(passed: bool) -> str:
    """把判定结果转成报告里的中文标记。

    Args:
        passed: 是否通过。

    Returns:
        ``通过`` 或 ``未通过``。
    """
    return "通过" if passed else "未通过"


def render_markdown(payload: dict[str, Any]) -> str:
    """把报告数据渲染成 Markdown。

    Args:
        payload: :func:`eval.skill_retrieval.runner.run` 的返回值。

    Returns:
        Markdown 文本。
    """
    meta = payload["meta"]
    metrics = payload["metrics"]
    off = metrics["off"]
    on = metrics["on"]
    diff = metrics["diff"]
    gates = payload["gates"]

    failed = [gate for gate in gates if not gate["passed"]]
    hard_failed = [gate for gate in failed if gate["kind"] == "hard"]

    lines: list[str] = [
        "# 技能检索开关评估报告",
        "",
        f"- 运行标签：`{meta['label']}`",
        f"- 技能目录：{meta['skill_count']} 条（真实落盘的 `SKILL.md`，由 DeepAgents 加载器解析）",
        f"- 查询集：{meta['query_count']} 条（门槛样本 {meta['gated_query_count']} 条，"
        f"未校准 {meta['query_count'] - meta['gated_query_count']} 条）",
        f"- 技能源：{' / '.join(f'`{s}`' for s in meta['skill_sources'])}",
        f"- backend：`{meta['backend']}`",
        f"- `top_k`：{meta['top_k']}",
        f"- token 编码：`{meta['encoding']}`",
        "",
        "## 一、结论摘要",
        "",
    ]

    if hard_failed:
        lines.append(
            f"**有 {len(hard_failed)} 条硬约束未通过 —— 这属于缺陷，不是效果问题。**"
        )
    elif failed:
        lines.append(f"硬约束全部通过；有 {len(failed)} 条效果门槛未达到。")
    else:
        lines.append("全部门槛通过。")

    lines.extend(
        [
            "",
            f"- 技能段长度：`off` {off['skills_section_chars_avg']:.0f} 字符 → "
            f"`on` {on['skills_section_chars_avg']:.0f} 字符，"
            f"**降幅 {_fmt_pct(diff['skills_section_chars_reduction'])}**",
            f"- 整条提示词 token：`off` {off['system_tokens_avg']:.0f} → "
            f"`on` {on['system_tokens_avg']:.0f}，降幅 {_fmt_pct(diff['system_tokens_reduction'])}"
            "（基础提示词本身占大头，开关管不到那部分）",
            f"- 列出技能数：`off` {off['listed_avg']:.1f} 条 → "
            f"`on` {on['listed_avg']:.1f} 条",
            f"- `on` 模式命中率：{_fmt_pct(on['hit_rate'])}"
            f"（{on['hits']}/{on['gated']}）",
            f"- 列出的技能里无关项占比：`off` {_fmt_pct(off['noise_ratio'])} → "
            f"`on` {_fmt_pct(on['noise_ratio'])}（受 `top_k` 影响，仅供参考）",
            f"- 常驻技能在位率：`off` {_fmt_pct(off['always_on_rate'])}、"
            f"`on` {_fmt_pct(on['always_on_rate'])}",
            "",
            "## 二、验收门槛",
            "",
            "| 门槛 | 类型 | 目标 | 实测 | 判定 |",
            "|---|---|---|---|---|",
        ]
    )

    for gate in gates:
        kind = "硬约束" if gate["kind"] == "hard" else "效果"
        lines.append(
            f"| {gate['name']} | {kind} | {gate['target']} | {gate['actual']} | "
            f"{_verdict(gate['passed'])} |"
        )

    lines.extend(
        [
            "",
            "## 三、两种模式对比",
            "",
            "| 指标 | `off`（默认） | `on` | 变化 |",
            "|---|---|---|---|",
            f"| 技能段字符数 | {off['skills_section_chars_avg']:.0f} | "
            f"{on['skills_section_chars_avg']:.0f} | "
            f"-{_fmt_pct(diff['skills_section_chars_reduction'])} |",
            f"| 平均列出技能数 | {off['listed_avg']:.1f} | {on['listed_avg']:.1f} | "
            f"-{diff['listed_drop']:.1f} |",
            f"| 命中率 | {_fmt_pct(off['hit_rate'])} | {_fmt_pct(on['hit_rate'])} | "
            f"{diff['hit_rate_drop'] * 100:+.1f}pp |",
            f"| 无关项占比 | {_fmt_pct(off['noise_ratio'])} | "
            f"{_fmt_pct(on['noise_ratio'])} | "
            f"-{diff['noise_ratio_drop'] * 100:.1f}pp |",
            f"| 整条 system message 字符数 | {off['system_chars_avg']:.0f} | "
            f"{on['system_chars_avg']:.0f} | -{_fmt_pct(diff['system_chars_reduction'])} |",
            f"| 整条 system message token 数 | {off['system_tokens_avg']:.0f} | "
            f"{on['system_tokens_avg']:.0f} | -{_fmt_pct(diff['system_tokens_reduction'])} |",
            "",
            "说明：",
            "",
            "- `off` 模式下命中率必然是 100%（全部列出），这一列只用于确认基线正确；"
            "真正的测点是 `on` 模式的命中率。",
            "- 最后两行是**用户实际感知到的成本**，但它同时取决于基础提示词有多大"
            "（本次基础提示词占了大头），开关管不到那部分，因此不设门槛。",
            "- 「无关项占比」受 `top_k` 影响，同样不设门槛。",
            "",
            "## 四、命中率对 `top_k` 的敏感度",
            "",
            "把 `top_k` 逐步收紧，看命中率掉得多快。掉得慢说明相关技能稳稳排在前面；"
            "掉得快说明它只是勉强挤进名额。",
            "",
            "| `top_k` | 平均列出 | 命中率 | 技能段字符数 | 常驻在位 |",
            "|---|---|---|---|---|",
        ]
    )

    for value, sweep_metrics in sorted(metrics["sweep"].items(), key=lambda kv: int(kv[0])):
        lines.append(
            f"| {value} | {sweep_metrics['listed_avg']:.1f} | "
            f"{_fmt_pct(sweep_metrics['hit_rate'])}"
            f"（{sweep_metrics['hits']}/{sweep_metrics['gated']}） | "
            f"{sweep_metrics['skills_section_chars_avg']:.0f} | "
            f"{_fmt_pct(sweep_metrics['always_on_rate'])} |"
        )

    lines.extend(
        [
            "",
            "## 五、逐条明细（门槛样本）",
            "",
            "| 查询 | 期望技能 | `off` 列出 | `on` 列出 | 命中 |",
            "|---|---|---|---|---|",
        ]
    )

    for row in payload["queries"]:
        if not row["gated"]:
            continue
        hit = "是" if row["on_hit"] else "**否**"
        lines.append(
            f"| {row['query_id']} | `{row['expected']}` | {row['off_listed']} | "
            f"{row['on_listed']} | {hit} |"
        )

    missed = [row for row in payload["queries"] if row["gated"] and not row["on_hit"]]
    if missed:
        lines.extend(["", "### 未命中的查询", ""])
        for row in missed:
            lines.append(f"- **{row['query_id']}**：{row['text']}")
            lines.append(f"  - 期望 `{row['expected']}`")
            lines.append(
                f"  - 实际列出：{', '.join(f'`{n}`' for n in row['on_listed_names'])}"
            )

    uncalibrated = [row for row in payload["queries"] if not row["gated"]]
    if uncalibrated:
        lines.extend(
            [
                "",
                "### 未校准样本（只观测，不判失败）",
                "",
                "| 查询 | 期望 | `on` 列出 | 命中 | 备注 |",
                "|---|---|---|---|---|",
            ]
        )
        for row in uncalibrated:
            expected = f"`{row['expected']}`" if row["expected"] else "（无期望）"
            hit = (
                "—"
                if row["expected"] is None
                else ("是" if row["on_hit"] else "否")
            )
            lines.append(
                f"| {row['query_id']} | {expected} | {row['on_listed']} | {hit} | "
                f"{row['note']} |"
            )

        fallback = [
            row for row in uncalibrated if row["on_listed"] >= meta["skill_count"]
        ]
        if fallback:
            lines.extend(
                [
                    "",
                    f"其中 {len(fallback)} 条触发了 fail-open（列出全部 "
                    f"{meta['skill_count']} 条）：",
                    "",
                ]
            )
            for row in fallback:
                lines.append(
                    f"- **{row['query_id']}**（{row['text']}）：查询切不出与技能目录"
                    "重合的词元，按设计回退为列出全部技能。这类查询省不下长度，"
                    "是词法检索的已知边界。"
                )

    lines.extend(
        [
            "",
            "## 六、这套评估的局限",
            "",
            f"- **模型行为没测**：{meta['not_measured']}。本评估只量模型收到的提示词，"
            "因此**不能**据此断言任务成功率会提高。",
            "- **门槛样本偏易**：门槛样本的期望技能，其描述里含有查询的关键说法，"
            "「命中」在词法上是可达的，所以高命中率是预期内的，它验证的是「检索基本"
            "可用」，不是「检索很聪明」。真正有区分度的是上面那批未校准样本。",
            "- **技能目录是生成的**：不是开发机上的真实技能库。真实库的描述质量参差、"
            "命名风格不一，命中率可能更低。",
            f"- **绝对长度会漂移**：{meta['base_prompt_note']}",
            "",
            "## 七、复现",
            "",
            "```bash",
            f".venv/Scripts/python.exe -m eval.skill_retrieval --label {meta['label']}",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def write(payload: dict[str, Any], directory: Path) -> None:
    """把报告写到指定目录。

    Args:
        payload: 报告数据。
        directory: 目标目录，不存在时创建。
    """
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (directory / "report.md").write_text(render_markdown(payload), encoding="utf-8")
