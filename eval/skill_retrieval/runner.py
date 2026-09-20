"""技能检索 eval 的运行器。

跑一次评估的完整流程：

1. 在临时目录里造一个「全局工作区 + 项目文件夹」，把 :data:`catalog.SKILL_COUNT`
   条技能**真实写盘**；
2. 用生产同款参数构造 ``LocalShellBackend``（本地文件夹模式）与
   ``SkillsMiddleware``，挂载 ``/skills-global`` / ``/skills-set`` / ``/skills``
   三个源 —— 与 ``services.skill_registry.agent_skill_sources`` 在「已绑定本地
   文件夹」时的返回值一致；
3. 调 ``before_agent`` 让 **DeepAgents 的真实加载器**解析技能，拿到
   ``skills_metadata``（含 ``metadata`` 嵌套映射）；
4. 对 ``off`` / ``on`` 两种模式，逐条查询调 ``modify_request``，解析渲染结果里
   实际列出的技能名，并量出整条 system message 的长度与 token 数；
5. 交给 :mod:`eval.skill_retrieval.judge` 算指标与门槛。

**不测什么**：本评估不运行真实模型，因此**不能**回答「模型是否真的选对了技能
并完成任务」。它测的是「模型收到的那条提示词变成什么样」—— 这恰好是本次改动
唯一影响的表面。要测任务成功率，需要另一套跑真实 LLM 的评估。

**隔离**：整个过程用临时目录，并把 ``Settings.data_dir`` 临时指向临时目录，
因此不会读写开发机上真实的技能目录与设置文件；运行结束后恢复原值。
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import HumanMessage, SystemMessage

from agent.middleware.skill_retrieval import SkillsMiddleware
from config.app_config import get_settings
from config.skill_retrieval_config import (
    DEFAULT_TOP_K,
    get_skill_retrieval_preferences,
    update_skill_retrieval_preferences,
)
from eval.skill_retrieval import report as report_module
from eval.skill_retrieval.catalog import (
    QUERIES,
    SKILL_COUNT,
    build_catalog,
    write_skill,
)
from eval.skill_retrieval.judge import (
    Row,
    compare,
    count_tokens,
    evaluate_gates,
    query_table,
    summarize_mode,
)
from prompt import get_system_prompt
from services.skill_retrieval import ALWAYS_ON_SKILLS

MODES = ("off", "on")

SWEEP_TOP_K: tuple[int, ...] = (5, 10, 20, 40)
"""敏感度扫描要试的 ``top_k``。

``select_skills`` 返回的是目录原序而不是相关度序，因此「命中排第几」量不出排序
质量。改成把 ``top_k`` 逐步收紧，看命中率掉得多快 —— 掉得慢说明相关技能稳稳排在
前面，掉得快说明它只是勉强挤进名额。这比一个假的「名次」有意义。
"""

SKILL_SOURCES = ("/skills-global/", "/skills-set/", "/skills/")
"""挂载的技能源，与生产「已绑定本地文件夹」时的列表一致。"""

_LISTED_PATTERN = re.compile(
    r"-> Read `(?P<path>[^`]+?)/SKILL\.md` for full instructions"
)
"""从渲染结果里抠出实际列出的技能。

刻意解析**渲染后的文本**而不是直接读中间件内部状态：这样量到的就是模型真正
收到的东西。用 ``-> Read`` 那一行做锚点，避免把提示词模板里其它 ``- **xxx**``
形式的条目误当成技能。
"""


def listed_skills(system_text: str) -> tuple[str, ...]:
    """从渲染后的 system message 里解析出列出的技能名。

    Args:
        system_text: 渲染后的 system message 正文。

    Returns:
        技能名元组，顺序与提示词一致。
    """
    names: list[str] = []
    for match in _LISTED_PATTERN.finditer(system_text):
        names.append(Path(match.group("path")).name)
    return tuple(names)


class _Harness:
    """一次评估运行所需的临时环境。

    用上下文管理器包住，保证 ``Settings`` 的临时改动一定被还原。
    """

    def __init__(self, tmp_root: Path) -> None:
        """初始化环境。

        Args:
            tmp_root: 本次运行独占的临时根目录。
        """
        self.tmp_root = tmp_root
        self.global_root = tmp_root / "global"
        self.project_root = tmp_root / "project"
        self.data_dir = tmp_root / "data"
        self._saved: dict[str, Any] = {}

    def __enter__(self) -> _Harness:
        """建目录、写技能、构造 backend 与中间件。

        Returns:
            自身。
        """
        from backends.local_shell import PROJECT_SET_DIR_NAME, LocalShellBackend

        skills_root = self.global_root / "skills"
        skills_root.mkdir(parents=True, exist_ok=True)
        # ``/skills-set`` 指向全局技能目录下的 .project-set，生产环境该目录由
        # backend 的 _ensure_layout 之外的地方创建。这里显式建出来，否则
        # DeepAgents 会报「路径不存在」并把加载告警写进提示词，污染长度测量。
        (skills_root / PROJECT_SET_DIR_NAME).mkdir(parents=True, exist_ok=True)

        self.catalog = build_catalog()
        for spec in self.catalog:
            write_skill(skills_root, spec)

        settings = get_settings()
        self._saved = {
            "data_dir": settings.data_dir,
            "_skills_dir": getattr(settings, "_skills_dir", None),
        }
        settings.data_dir = self.data_dir
        settings._skills_dir = skills_root

        self.backend = LocalShellBackend(
            workspace=self.project_root,
            local_folder_mode=True,
            global_workspace_root=self.global_root,
        )
        self.middleware = SkillsMiddleware(
            backend=self.backend, sources=list(SKILL_SOURCES)
        )
        return self

    def __exit__(self, *_exc: object) -> None:
        """还原 ``Settings`` 的临时改动。"""
        settings = get_settings()
        if "data_dir" in self._saved:
            settings.data_dir = self._saved["data_dir"]
        if self._saved.get("_skills_dir") is not None:
            settings._skills_dir = self._saved["_skills_dir"]

    def load_skills(self) -> list[dict[str, Any]]:
        """走 DeepAgents 的真实加载器把技能读进 ``skills_metadata``。

        Returns:
            技能元数据列表。
        """
        update = self.middleware.before_agent({}, None, {})  # type: ignore[arg-type]
        return list((update or {}).get("skills_metadata") or [])


def _collect(
    harness: _Harness,
    skills: list[dict[str, Any]],
    *,
    mode: str,
    top_k: int,
    base_prompt: str,
) -> list[Row]:
    """把开关设成指定模式，跑完整个查询集并收集观测。

    Args:
        harness: 已就绪的临时环境。
        skills: ``skills_metadata``。
        mode: 开关模式。
        top_k: 最多列出多少条。
        base_prompt: 基础 system prompt（用于算技能段长度）。

    Returns:
        每条查询一条观测。

    Raises:
        RuntimeError: 写入的开关没有生效（读回来与写入值不一致）。
    """
    update_skill_retrieval_preferences(
        {"mode": mode, "top_k": top_k, "auto_threshold": 60}
    )
    effective = get_skill_retrieval_preferences()
    if effective["mode"] != mode or int(effective["top_k"]) != top_k:
        msg = (
            f"开关未生效：期望 mode={mode} top_k={top_k}，"
            f"实际 mode={effective['mode']} top_k={effective['top_k']}"
        )
        raise RuntimeError(msg)

    base_chars = len(base_prompt)
    rows: list[Row] = []
    for query in QUERIES:
        request = ModelRequest(
            model=None,  # type: ignore[arg-type]
            messages=[HumanMessage(content=query.text)],
            system_message=SystemMessage(content=base_prompt),
            state={"messages": [], "skills_metadata": skills},
        )
        rendered = harness.middleware.modify_request(request)
        text = rendered.system_message.text  # type: ignore[union-attr]
        rows.append(
            Row(
                query_id=query.query_id,
                mode=mode,
                expected=query.expected,
                gated=query.gated,
                listed=listed_skills(text),
                system_chars=len(text),
                system_tokens=count_tokens(text),
                skills_section_chars=len(text) - base_chars,
            )
        )
    return rows


def run(
    label: str,
    *,
    top_k: int = DEFAULT_TOP_K,
    reports_dir: Path,
    sweep_top_k: Sequence[int] = SWEEP_TOP_K,
) -> dict[str, Any]:
    """跑一次完整评估并写出报告。

    Args:
        label: 本次运行的标签，决定报告目录名。
        top_k: 主对比里「开启」模式最多列出多少条技能。
        reports_dir: 报告根目录。
        sweep_top_k: 敏感度扫描要试的 ``top_k`` 值。

    Returns:
        报告数据（与落盘的 ``report.json`` 同构）。

    Raises:
        RuntimeError: 技能加载条数与 :data:`catalog.SKILL_COUNT` 不符，或开关未生效。
    """
    base_prompt = get_system_prompt("coding")
    base_tokens = count_tokens(base_prompt)

    with (
        tempfile.TemporaryDirectory(prefix=f"xihe-skill-eval-{label}-") as tmp,
        _Harness(Path(tmp)) as harness,
    ):
        skills = harness.load_skills()
        if len(skills) != SKILL_COUNT:
            msg = (
                f"技能加载条数 {len(skills)} 与预期 {SKILL_COUNT} 不符，"
                "检查技能源路径与落盘位置"
            )
            raise RuntimeError(msg)

        rows: list[Row] = []
        for mode in MODES:
            rows.extend(
                _collect(
                    harness,
                    skills,
                    mode=mode,
                    top_k=top_k,
                    base_prompt=base_prompt,
                )
            )

        sweep: dict[int, dict[str, Any]] = {}
        for value in sweep_top_k:
            sweep_rows = _collect(
                harness,
                skills,
                mode="on",
                top_k=value,
                base_prompt=base_prompt,
            )
            sweep[value] = summarize_mode(
                sweep_rows,
                always_on=ALWAYS_ON_SKILLS,
                skill_count=SKILL_COUNT,
            )

    off = summarize_mode(
        [row for row in rows if row.mode == "off"],
        always_on=ALWAYS_ON_SKILLS,
        skill_count=SKILL_COUNT,
    )
    on = summarize_mode(
        [row for row in rows if row.mode == "on"],
        always_on=ALWAYS_ON_SKILLS,
        skill_count=SKILL_COUNT,
    )
    diff = compare(off, on)
    gates = evaluate_gates(
        off,
        on,
        diff,
        skill_count=SKILL_COUNT,
        always_on_count=len(ALWAYS_ON_SKILLS),
        sweep=sweep,
    )

    payload: dict[str, Any] = {
        "meta": {
            "label": label,
            "skill_count": SKILL_COUNT,
            "query_count": len(QUERIES),
            "gated_query_count": len([q for q in QUERIES if q.gated]),
            "top_k": top_k,
            "sweep_top_k": list(sweep_top_k),
            "modes": list(MODES),
            "skill_sources": list(SKILL_SOURCES),
            "backend": "LocalShellBackend(local_folder_mode=True)",
            "encoding": "cl100k_base",
            "base_prompt_tokens": base_tokens,
            "base_prompt_note": (
                "基础提示词取自 prompt.get_system_prompt('coding')，因此整条长度会"
                "随提示词本身变化；技能段的量与两种模式的差值不受影响"
            ),
            "measured": "模型收到的 system message（技能列表 + 长度 + token）",
            "not_measured": "模型是否真的选对技能并完成任务（需要真实 LLM 运行）",
        },
        "metrics": {"off": off, "on": on, "diff": diff, "sweep": sweep},
        "gates": [
            {
                "name": gate.name,
                "target": gate.target,
                "actual": gate.actual,
                "passed": gate.passed,
                "kind": gate.kind,
            }
            for gate in gates
        ],
        "queries": query_table(rows, QUERIES),
    }

    report_module.write(payload, reports_dir / label)
    return payload
