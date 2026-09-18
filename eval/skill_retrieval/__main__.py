"""命令行入口：``python -m eval.skill_retrieval``。

退出码：

``0``
    全部门槛通过（``--negative-control`` 时为「命中率门槛如期失败」）。
``1``
    有**硬约束**未通过（关闭模式没列全、常驻技能掉了）—— 属于缺陷。
``2``
    硬约束通过，但有**效果门槛**未达到（命中率或降幅不达标）。
``3``
    ``--negative-control`` 下命中率门槛竟然通过了 —— 说明评估本身没有测量能力。
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from config.skill_retrieval_config import DEFAULT_TOP_K
from eval.skill_retrieval.negative_control import (
    hit_rate_gate_failed,
    run_negative_control,
)
from eval.skill_retrieval.runner import run

DEFAULT_REPORTS_DIR = Path(__file__).resolve().parent / "reports"


def _build_parser() -> argparse.ArgumentParser:
    """构造命令行解析器。

    Returns:
        解析器实例。
    """
    parser = argparse.ArgumentParser(
        prog="python -m eval.skill_retrieval",
        description="评估技能检索开关：对比关闭与开启两种模式下的提示词长度与命中率。",
    )
    parser.add_argument(
        "--label",
        default=None,
        help="本次运行标签，决定报告目录名；省略时用当前时间戳。",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=DEFAULT_TOP_K,
        help=f"开启检索时最多列出多少条技能（默认 {DEFAULT_TOP_K}）。",
    )
    parser.add_argument(
        "--reports-dir",
        type=Path,
        default=DEFAULT_REPORTS_DIR,
        help=f"报告根目录（默认 {DEFAULT_REPORTS_DIR}）。",
    )
    parser.add_argument(
        "--negative-control",
        action="store_true",
        help="把检索打分打坏再跑一遍，验证评估本身能发现退化。",
    )
    return parser


def _print_gates(gates: list[dict[str, object]]) -> None:
    """把门槛判定逐条打印出来。

    Args:
        gates: 报告里的门槛列表。
    """
    for gate in gates:
        mark = "通过" if gate["passed"] else "未通过"
        print(f"  [{mark}] {gate['name']}：{gate['actual']}")


def main(argv: list[str] | None = None) -> int:
    """运行一次评估。

    Args:
        argv: 命令行参数；``None`` 时取 ``sys.argv``。

    Returns:
        进程退出码，含义见模块文档。
    """
    args = _build_parser().parse_args(argv)
    label = args.label or datetime.now().strftime("%Y%m%d-%H%M%S")

    if args.negative_control:
        payload = run_negative_control(label, reports_dir=args.reports_dir)
        print(f"负向对照（{label}）：检索打分已被替换为「不看查询、按目录取前 N 条」")
        _print_gates(payload["gates"])
        if hit_rate_gate_failed(payload):
            print("命中率门槛如期失败 —— 评估有测量能力。")
            return 0
        print("命中率门槛竟然通过了 —— 这套评估没有测量能力，必须修。")
        return 3

    payload = run(label, top_k=args.top_k, reports_dir=args.reports_dir)

    metrics = payload["metrics"]
    diff = metrics["diff"]
    on = metrics["on"]
    gates = payload["gates"]
    hard_failed = [g for g in gates if not g["passed"] and g["kind"] == "hard"]
    effect_failed = [g for g in gates if not g["passed"] and g["kind"] == "effect"]

    print(f"运行标签：{label}")
    print(f"技能目录：{payload['meta']['skill_count']} 条，查询 {payload['meta']['query_count']} 条")
    print(
        f"提示词 token：off {metrics['off']['system_tokens_avg']:.0f} → "
        f"on {on['system_tokens_avg']:.0f}"
        f"（-{diff['system_tokens_reduction'] * 100:.1f}%）"
    )
    print(f"列出技能数：off {metrics['off']['listed_avg']:.1f} → on {on['listed_avg']:.1f}")
    print(f"开启模式命中率：{on['hit_rate'] * 100:.1f}%（{on['hits']}/{on['gated']}）")
    sweep = metrics.get("sweep") or {}
    if sweep:
        cells = "  ".join(
            f"top_k={value}→{sweep[value]['hit_rate'] * 100:.0f}%"
            for value in sorted(sweep)
        )
        print(f"命中率敏感度：{cells}")
    _print_gates(gates)
    print(f"报告：{args.reports_dir / label}")

    if hard_failed:
        return 1
    if effect_failed:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
