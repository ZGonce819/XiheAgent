"""技能检索开关的测试。

覆盖三块：

1. :mod:`services.skill_retrieval` 的检索行为与两条安全性质（fail-open、
   保持原始顺序）；
2. :mod:`agent.middleware.skill_retrieval` 的开关语义，其中最关键的一条是
   **开关关闭时渲染结果与 DeepAgents 内置实现逐字一致**；
3. :mod:`config.skill_retrieval_config` 的默认值与入参校验。

中间件用例不需要真实 backend：``modify_request`` 只读 ``state`` 与消息列表，
不碰 ``self._backend``，因此传 ``backend=None`` 即可。
"""

from __future__ import annotations

from typing import Any

import pytest
from deepagents.middleware.skills import SkillsMiddleware as BuiltinSkillsMiddleware
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import HumanMessage, SystemMessage

from agent.middleware.skill_retrieval import SkillsMiddleware, latest_user_text
from config.skill_retrieval_config import (
    DEFAULT_MODE,
    get_skill_retrieval_preferences,
    skill_retrieval_active,
    update_skill_retrieval_preferences,
)
from services.skill_retrieval import (
    ALWAYS_ON_SKILLS,
    retrieval_document,
    select_skills,
    tokenize,
)

SOURCES = ["/skills-global/"]


@pytest.fixture
def data_dir(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """把 ``Settings.data_dir`` 指向临时目录，避免写到开发机的真实配置。

    Returns:
        临时的 data 目录。
    """
    from config.app_config import get_settings

    target = tmp_path / "data"
    monkeypatch.setattr(get_settings(), "data_dir", target)
    return target


def make_skill(name: str, description: str) -> dict[str, Any]:
    """构造一条与 ``SkillMetadata`` 同形的技能记录。

    Args:
        name: 技能名。
        description: 技能说明。

    Returns:
        可直接放进 ``state["skills_metadata"]`` 的字典。
    """
    return {
        "name": name,
        "description": description,
        "path": f"/skills-global/{name}/SKILL.md",
        "license": None,
        "compatibility": None,
        "metadata": {},
        "allowed_tools": [],
    }


CATALOG = [
    make_skill("api-timeout-tuning", "接口超时调优，梳理重试与熔断参数"),
    make_skill("db-migration-check", "数据库迁移检查，校验 schema 变更与回滚脚本"),
    make_skill("frontend-theme", "前端主题定制，调整配色与排版变量"),
]
"""中间件用例用的候选技能。

刻意**不含** :data:`~services.skill_retrieval.ALWAYS_ON_SKILLS` 里的名字，否则
常驻名额会吃掉 ``top_k``，筛选结果将只由常驻名单决定，看不出检索本身的效果。
常驻语义由 ``test_select_skills_keeps_always_on_skills`` 单独覆盖。
"""


def build_request(skills: list[dict[str, Any]], query: str = "帮我检查数据库迁移脚本") -> ModelRequest[Any]:
    """构造一个带技能目录的模型请求。

    Args:
        skills: 要放进 ``skills_metadata`` 的技能列表。
        query: 用户消息正文。

    Returns:
        用于驱动 ``modify_request`` 的请求对象。
    """
    return ModelRequest(
        model=None,  # type: ignore[arg-type]
        messages=[HumanMessage(content=query)],
        system_message=SystemMessage(content="基础提示词"),
        state={"messages": [], "skills_metadata": skills},
    )


def render(middleware: Any, request: ModelRequest[Any]) -> str:
    """跑一遍 ``modify_request`` 并取出渲染后的 system message。

    Args:
        middleware: 技能中间件实例。
        request: 模型请求。

    Returns:
        注入技能目录后的 system message 正文（纯文本，不含 block 结构的 repr）。
    """
    modified = middleware.modify_request(request)
    assert modified.system_message is not None
    return modified.system_message.text


# ── 检索层 ──────────────────────────────────────────────────────────────────


def test_tokenize_splits_cjk_bigrams_and_latin_words() -> None:
    """中文按二元组切分，拉丁字母数字按整串切分。"""
    assert tokenize("退款逻辑 refund v2") == ["退款", "款逻", "逻辑", "refund", "v2"]


def test_retrieval_document_covers_name_description_path_metadata() -> None:
    """参与打分的文本必须涵盖名称、摘要、路径与 metadata。"""
    skill = make_skill("db-check", "数据库迁移检查")
    skill["metadata"] = {"keywords": "migration rollback"}

    document = retrieval_document(skill)

    assert "db-check" in document
    assert "数据库迁移检查" in document
    assert skill["path"] in document
    assert "migration rollback" in document


def test_retrieval_document_ignores_skill_body() -> None:
    """技能正文绝不参与打分（也不写进提示词，由 read_file 按需加载）。"""
    skill = make_skill("db-check", "数据库迁移检查")
    skill["body"] = "ZEBRA_UNICORN_BODY"

    assert "ZEBRA_UNICORN_BODY" not in retrieval_document(skill)


def test_select_skills_matches_on_metadata_keywords() -> None:
    """description 里没有的词，靠 metadata 的 keywords 也能命中。"""
    targeted = make_skill("deploy-helper", "部署流程辅助")
    targeted["metadata"] = {"keywords": "k8s kubernetes helm 灰度"}
    other = make_skill("frontend-theme", "前端主题定制")

    picked = select_skills([targeted, other], "k8s 灰度发布怎么做", top_k=1)

    assert [item["name"] for item in picked] == ["deploy-helper"]


def test_select_skills_prefers_skill_whose_path_matches_project() -> None:
    """路径里的作用域信息（项目集合名）能作为相关性信号。"""
    generic = make_skill("db-check", "数据库迁移检查")
    scoped = make_skill("db-check", "数据库迁移检查")
    scoped["path"] = "/workspace/skills/.project-set/Xihe/db-check/SKILL.md"

    picked = select_skills([generic, scoped], "Xihe 这个项目数据库迁移要注意什么", top_k=1)

    assert picked == [scoped]


def test_select_skills_ranks_relevant_skill_first() -> None:
    """与查询语义相近的技能应被优先选中。"""
    picked = select_skills(CATALOG, "数据库迁移脚本怎么校验", top_k=1)

    assert [item["name"] for item in picked] == ["db-migration-check"]


def test_select_skills_preserves_original_order() -> None:
    """返回值沿用候选列表顺序，而不是按相关度重排（利于 prompt 缓存）。"""
    picked = select_skills(CATALOG, "数据库迁移脚本怎么校验", top_k=3)

    assert [item["name"] for item in picked] == [item["name"] for item in CATALOG]


def test_select_skills_fails_open_when_nothing_matches() -> None:
    """没有任何技能命中时返回完整候选列表，而不是空列表。"""
    picked = select_skills(CATALOG, "zzz qqq", top_k=1)

    assert len(picked) == len(CATALOG)


def test_select_skills_fails_open_on_empty_query() -> None:
    """查询切不出词元时不做筛选。"""
    assert select_skills(CATALOG, "！？。", top_k=1) == CATALOG


def test_select_skills_keeps_always_on_skills() -> None:
    """常驻技能即使与查询无关也必须留在结果里。"""
    catalog = [*CATALOG, make_skill("repo-bootstrap-analysis", "仓库引导分析")]
    picked = select_skills(catalog, "数据库迁移脚本怎么校验", top_k=2)

    names = [item["name"] for item in picked]
    assert "repo-bootstrap-analysis" in names
    assert len(names) == 2


def test_always_on_skills_outrank_top_k() -> None:
    """常驻技能多于 ``top_k`` 时只列出常驻技能，额度不覆盖它们。"""
    catalog = [make_skill(name, "无关说明") for name in ALWAYS_ON_SKILLS]
    catalog.append(make_skill("db-migration-check", "数据库迁移检查"))

    picked = select_skills(catalog, "数据库迁移检查", top_k=1)

    assert [item["name"] for item in picked] == list(ALWAYS_ON_SKILLS)


# ── 中间件开关 ──────────────────────────────────────────────────────────────


def test_off_mode_renders_identically_to_builtin(data_dir: Any) -> None:
    """默认（关闭）状态下渲染结果必须与内置实现逐字一致。

    这是整个改动的安全底线：开关没打开时，注入内容不允许有任何差异。
    """
    request = build_request(CATALOG)
    builtin = BuiltinSkillsMiddleware(backend=None, sources=SOURCES)  # type: ignore[arg-type]
    ours = SkillsMiddleware(backend=None, sources=SOURCES)  # type: ignore[arg-type]

    assert render(ours, request) == render(builtin, request)


def test_injected_catalog_is_summary_and_pointer_only(data_dir: Any) -> None:
    """写进提示词的只有「摘要 + 路径指针」，正文靠 ``read_file`` 按需加载。

    这是渐进披露的约定：进提示词的永远只是每个技能的两行摘要，``SKILL.md``
    正文不参与打分、也不进提示词。检索只是筛选这份摘要列表。
    """
    middleware = SkillsMiddleware(backend=None, sources=SOURCES)  # type: ignore[arg-type]

    text = render(middleware, build_request(CATALOG))

    assert "- **db-migration-check**: 数据库迁移检查，校验 schema 变更与回滚脚本" in text
    assert (
        "-> Read `/skills-global/db-migration-check/SKILL.md` for full instructions"
        in text
    )
    assert "Read the skill's full instructions" in text


def test_on_mode_filters_catalog(data_dir: Any) -> None:
    """模式为「始终开启」时，只列出命中的技能。"""
    update_skill_retrieval_preferences({"mode": "on", "top_k": 1})
    middleware = SkillsMiddleware(backend=None, sources=SOURCES)  # type: ignore[arg-type]

    text = render(middleware, build_request(CATALOG))

    assert "db-migration-check" in text
    assert "frontend-theme" not in text


def test_auto_mode_skips_retrieval_below_threshold(data_dir: Any) -> None:
    """自动模式下技能数未超阈值时保持全部列出。"""
    update_skill_retrieval_preferences({"mode": "auto", "auto_threshold": 10, "top_k": 1})
    middleware = SkillsMiddleware(backend=None, sources=SOURCES)  # type: ignore[arg-type]

    text = render(middleware, build_request(CATALOG))

    assert "frontend-theme" in text


def test_auto_mode_retrieves_above_threshold(data_dir: Any) -> None:
    """自动模式下技能数超过阈值时开始筛选。"""
    update_skill_retrieval_preferences({"mode": "auto", "auto_threshold": 2, "top_k": 1})
    middleware = SkillsMiddleware(backend=None, sources=SOURCES)  # type: ignore[arg-type]

    text = render(middleware, build_request(CATALOG))

    assert "db-migration-check" in text
    assert "frontend-theme" not in text


def test_no_user_message_skips_retrieval(data_dir: Any) -> None:
    """消息里没有用户消息时无法构造查询，应保持全量。"""
    update_skill_retrieval_preferences({"mode": "on", "top_k": 1})
    middleware = SkillsMiddleware(backend=None, sources=SOURCES)  # type: ignore[arg-type]
    request = ModelRequest(
        model=None,  # type: ignore[arg-type]
        messages=[SystemMessage(content="只有系统消息")],
        system_message=SystemMessage(content="基础提示词"),
        state={"messages": [], "skills_metadata": CATALOG},
    )

    text = render(middleware, request)

    assert "frontend-theme" in text


def test_wrap_model_call_applies_filtering(data_dir: Any) -> None:
    """继承来的 ``wrap_model_call`` 必须走到本类重写的 ``modify_request``。

    中间件真正被框架调用的入口是 ``wrap_model_call``（它内部再调
    ``modify_request``）。只测 ``modify_request`` 无法证明这条链路是通的。
    """
    update_skill_retrieval_preferences({"mode": "on", "top_k": 1})
    middleware = SkillsMiddleware(backend=None, sources=SOURCES)  # type: ignore[arg-type]
    captured: dict[str, str] = {}

    def handler(request: ModelRequest[Any]) -> str:
        assert request.system_message is not None
        captured["text"] = request.system_message.text
        return "handled"

    result = middleware.wrap_model_call(build_request(CATALOG), handler)  # type: ignore[arg-type]

    assert result == "handled"
    assert "db-migration-check" in captured["text"]
    assert "frontend-theme" not in captured["text"]


def test_retrieval_failure_falls_back_to_full_catalog(
    data_dir: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """检索抛异常时必须退化为列出全部技能，不能影响智能体运行。"""
    update_skill_retrieval_preferences({"mode": "on", "top_k": 1})

    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("检索炸了")

    monkeypatch.setattr("services.skill_retrieval.select_skills", boom)
    middleware = SkillsMiddleware(backend=None, sources=SOURCES)  # type: ignore[arg-type]

    text = render(middleware, build_request(CATALOG))

    assert "frontend-theme" in text
    assert "db-migration-check" in text


def test_latest_user_text_ignores_non_user_messages() -> None:
    """查询取最近一条用户消息，工具结果与系统消息都会被跳过。"""
    messages = [
        HumanMessage(content="先看这个"),
        SystemMessage(content="系统"),
        HumanMessage(content="数据库迁移怎么校验"),
    ]

    assert latest_user_text(messages) == "数据库迁移怎么校验"


def test_latest_user_text_handles_block_content() -> None:
    """分块正文（多模态消息）也能抽出文本。"""
    message = HumanMessage(content=[{"type": "text", "text": "检查迁移脚本"}])

    assert latest_user_text([message]) == "检查迁移脚本"


# ── 配置 ────────────────────────────────────────────────────────────────────


def test_defaults_disable_retrieval(data_dir: Any) -> None:
    """未配置时默认关闭，且任何技能数量下都不触发检索。"""
    preferences = get_skill_retrieval_preferences()

    assert DEFAULT_MODE == "off"
    assert preferences["mode"] == "off"
    assert skill_retrieval_active(0) is False
    assert skill_retrieval_active(10_000) is False


def test_update_persists_and_reloads(data_dir: Any) -> None:
    """写入后重新读取应拿到新值。"""
    update_skill_retrieval_preferences({"mode": "on", "top_k": 7, "auto_threshold": 12})

    preferences = get_skill_retrieval_preferences()
    assert preferences["mode"] == "on"
    assert preferences["top_k"] == 7
    assert preferences["auto_threshold"] == 12


def test_update_keeps_unspecified_fields(data_dir: Any) -> None:
    """只提交单个字段时，其余字段沿用当前值。"""
    update_skill_retrieval_preferences({"mode": "auto", "top_k": 5})
    update_skill_retrieval_preferences({"top_k": 9})

    preferences = get_skill_retrieval_preferences()
    assert preferences["mode"] == "auto"
    assert preferences["top_k"] == 9


def test_update_rejects_unknown_mode(data_dir: Any) -> None:
    """未知模式应报错而不是静默回退。"""
    with pytest.raises(ValueError, match="未知的技能检索模式"):
        update_skill_retrieval_preferences({"mode": "always"})


def test_update_rejects_out_of_range_top_k(data_dir: Any) -> None:
    """超出区间的 ``top_k`` 应报错，避免界面显示与实际生效值不一致。"""
    with pytest.raises(ValueError, match="最多列出的技能数"):
        update_skill_retrieval_preferences({"top_k": 10_000})


def test_update_rejects_non_integer_top_k(data_dir: Any) -> None:
    """非整数入参应报错。"""
    with pytest.raises(ValueError, match="必须是整数"):
        update_skill_retrieval_preferences({"top_k": "很多"})


def test_corrupt_config_file_falls_back_to_defaults(data_dir: Any) -> None:
    """配置文件损坏时回退到默认值，不影响智能体启动。"""
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "skill_retrieval.json").write_text("{ 不是 JSON", encoding="utf-8")

    assert get_skill_retrieval_preferences()["mode"] == DEFAULT_MODE


def test_auto_mode_threshold_is_exclusive(data_dir: Any) -> None:
    """自动模式在「等于」阈值时不检索，超过才检索。"""
    update_skill_retrieval_preferences({"mode": "auto", "auto_threshold": 5})

    assert skill_retrieval_active(5) is False
    assert skill_retrieval_active(6) is True
