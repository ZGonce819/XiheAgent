"""技能检索开关的端到端评估。

回答一个问题：**打开技能检索，到底省了多少、代价是什么？**

用法::

    .venv/Scripts/python.exe -m eval.skill_retrieval --label my-run

产出在 ``eval/skill_retrieval/reports/<标签>/``（``report.json`` + ``report.md``）。

规模与判定标准见 :mod:`eval.skill_retrieval.catalog` 与
:mod:`eval.skill_retrieval.judge`；跑法见 :mod:`eval.skill_retrieval.runner`。
"""

from eval.skill_retrieval.runner import run

__all__ = ["run"]
