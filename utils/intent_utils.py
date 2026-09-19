"""用户任务意图识别。

把用户输入归类为 Agent 的第一层任务类型。任务类型会直接影响后续系统提示词、
工具权限、是否允许修改代码、是否创建 Pull Request 等关键行为。

当前实现采用"模型结构化分类 + 规则兜底 + 安全降级"的三段式方案：

1. 轻量、确定性的特殊任务仍然先用关键词快速识别，例如只查看工作区、只 git pull。
2. 普通任务调用模型，并使用 LangChain ``with_structured_output()`` 返回 Pydantic 对象。
3. 如果模型失败、结构化解析失败，或模型把只读任务误判为 coding，则回退或降级。

为什么不完全依赖模型：

- coding 是唯一允许修改文件、提交、push、创建 PR 的模式，不能完全交给模型自由判断。
- 用户明确说"不要修改代码""只审查""只给方案"时，必须强制只读。
- 关键词备份版本保留在 ``_KEYWORD_CLASSIFY`` 逻辑中，既能回滚，也能作为兜底。
"""

from __future__ import annotations

import logging
from functools import lru_cache

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, field_validator

from agent.graph.model import make_intent_model
from config.constants import TaskKind

logger = logging.getLogger("agent.run.task_intent")


class IntentClassification(BaseModel):
    """模型意图分类的结构化输出。

    使用 Pydantic 的意义是把"模型必须返回固定字段、固定枚举"的要求交给框架校验。
    如果模型输出不符合结构，LangChain/Pydantic 会抛出异常，外层再回退到关键词规则。
    """

    task_kind: TaskKind = Field(description="用户任务类型，只能是固定枚举之一。")
    confidence: float = Field(ge=0, le=1, description="分类置信度，范围 0 到 1。")
    reason: str = Field(description="中文短理由，不超过 40 个字。")

    @field_validator("reason")
    @classmethod
    def _trim_reason(cls, value: str) -> str:
        """截断并清理分类理由文本。

        Args:
            value: 原始理由字符串。

        Returns:
            去除两端空白并截断到 40 字符后的字符串。
        """
        return value.strip()[:40]


def _normalize_prompt(prompt: str) -> str:
    """把用户输入规整成适合本地规则判断的短文本。

    转小写、合并连续空白为单个空格，方便后续关键词匹配。

    Args:
        prompt: 用户原始输入。

    Returns:
        规范化后的短文本。
    """

    return " ".join((prompt or "").lower().split())


def _contains_any(text: str, keywords: list[str]) -> bool:
    """判断文本是否包含任意关键词。

    Args:
        text: 待检测的文本。
        keywords: 关键词列表。

    Returns:
        包含任一关键词时返回 ``True``。
    """

    return any(keyword in text for keyword in keywords)


def is_pull_only_task(prompt: str) -> bool:
    """判断用户是否只想同步远程仓库代码。

    不调用模型。git pull 属于轻量同步任务，没必要消耗一次模型调用；
    如果同时出现"修改、提交、push、PR"等词，就不再当作单纯同步任务。

    Args:
        prompt: 用户原始输入。

    Returns:
        是纯同步任务时返回 ``True``。
    """

    normalized = _normalize_prompt(prompt)
    pull_keywords = ["git pull", " pull", "pull一下", "拉取", "同步远程", "更新远程", "拉一下"]
    change_keywords = [
        "修改", "新增", "修复", "创建pr", "创建 pr", "pull request",
        "提交", "push", "实现", "开发",
    ]
    return _contains_any(normalized, pull_keywords) and not _contains_any(normalized, change_keywords)


def is_workspace_listing_task(prompt: str) -> bool:
    """判断用户是否只是询问本地工作区有哪些项目。

    只有明确要求列出项目时才走本地直达分支。单独询问"工作目录在哪里"
    需要交给 Agent 回答，因为它可能是在询问当前会话绑定的项目目录，不能
    被误判成全局工作区项目列表。

    Args:
        prompt: 用户原始输入。

    Returns:
        是工作区查询任务时返回 ``True``。
    """

    normalized = _normalize_prompt(prompt)
    list_keywords = [
        "有哪些项目", "项目列表", "列出项目", "列一下项目", "查看项目目录",
        "list projects", "list project",
    ]
    change_keywords = ["修改", "修复", "创建", "提交", "push", "pr", "实现", "开发"]
    return _contains_any(normalized, list_keywords) and not _contains_any(normalized, change_keywords)


def is_simple_coding_task(prompt: str) -> bool:
    """判断是否为简单编码任务，可以跳过方案规划直接执行。

    简单任务包括：创建空文件、删除单个文件、重命名文件等不需要复杂设计的操作。
    这些任务太简单，不值得消耗一次模型调用来生成方案。

    Args:
        prompt: 用户原始输入。

    Returns:
        是简单编码任务时返回 ``True``。
    """

    normalized = _normalize_prompt(prompt)

    # 创建空文件的模式
    create_empty_patterns = [
        "创建空", "新建空", "创建一个空", "新建一个空",
        "创建.*空文件", "新建.*空文件", "创建.*空的", "新建.*空的",
    ]
    if _contains_any(normalized, create_empty_patterns):
        return True

    # 简单的文件操作（删除、重命名）
    simple_file_ops = [
        "删除文件", "删除这个文件", "删掉文件", "移除文件",
        "重命名文件", "改名文件", "文件改名",
    ]
    if _contains_any(normalized, simple_file_ops):
        return True

    # 非常短的指令（通常很简单）
    if len(normalized) < 20:
        # 但要排除明确需要方案的词
        planning_words = ["方案", "计划", "设计", "怎么做", "如何实现"]
        if not _contains_any(normalized, planning_words):
            return True

    return False


@lru_cache(maxsize=1)
def _make_structured_intent_model():
    """创建带结构化输出约束的意图分类模型。

    DeepSeek 使用 OpenAI-compatible 接口，选择 ``json_mode`` 保证兼容性。
    使用 ``@lru_cache`` 缓存，整个进程只创建一次。

    Returns:
        带 ``IntentClassification`` 结构化输出约束的 LangChain runnable。
    """

    return make_intent_model().with_structured_output(IntentClassification, method="json_mode")


def _classify_by_keyword_backup(prompt: str) -> TaskKind:
    """关键词分类兜底逻辑。

    模型不可用、结构化解析失败或测试环境没有真实 key 时，仍然可以保持项目可用。

    Args:
        prompt: 用户原始输入。

    Returns:
        关键词匹配到的任务类型，无命中时默认返回 ``"qa"``。
    """

    normalized = _normalize_prompt(prompt)
    if is_pull_only_task(prompt):
        return "sync"
    if is_workspace_listing_task(prompt):
        return "inspect"

    review_fix_markers = [
        "读取审查报告", "根据审查报告", "基于审查报告", "结合审查报告", "修复审查报告",
        "review findings", "review finding", "review_findings", "结构化 findings",
        "根据 findings", "结合 findings", "/reviews/",
        "根据 review", "根据review", "根据审查", "修复审查", "修复 review", "修复review",
    ]
    has_review_fix_context = _contains_any(normalized, review_fix_markers)
    approval_markers = [
        "确认实施", "开始实施", "按方案实施", "执行方案", "生成代码", "修改代码", "开始修改",
    ]
    negative_change_markers = [
        "先不要修改", "不要修改", "不修改", "先不要改", "不要改", "不改代码", "不要改代码",
    ]
    has_negative = _contains_any(normalized, negative_change_markers)
    if has_review_fix_context and _contains_any(normalized, approval_markers) and not has_negative:
        return "coding"

    planning_keywords = [
        "方案", "计划", "设计", "步骤", "怎么做", "如何做", "先列出", "先帮我设计", "不要改", "由我确认",
    ]
    analysis_keywords = [
        "分析", "解析", "目录结构", "梳理", "解释", "查看", "有哪些", "说明", "理解", "检查一下", "帮我看看",
    ]
    qa_keywords = ["为什么", "是什么", "在哪里", "是否", "能不能", "请问"]
    review_keywords = [
        "review", "reviewer", "代码审查", "代码评审", "审查", "评审",
        "检查pr", "审查pr", "评审pr", "pull request审查", "pull request review",
        "pr review", "pr审查", "pr评审", "输出中文审查报告",
        "读取 pr diff", "读取pr diff", "pr diff", "审查规则",
        "只做代码 review", "只做代码review", "不要修改代码",
    ]
    coding_keywords = [
        "修改", "修复", "新增", "增加", "实现", "开发", "改为", "改成", "改造",
        "迁移", "迁移到", "切换为", "替换为", "接入", "升级", "删除功能", "重构",
        "提交", "创建pr", "pull request", "push", "运行测试",
    ]

    has_coding = _contains_any(normalized, coding_keywords)
    has_planning = _contains_any(normalized, planning_keywords)
    has_analysis = _contains_any(normalized, analysis_keywords)
    has_review = _contains_any(normalized, review_keywords)
    review_only_markers = [
        "只 review", "只review", "只做review", "只做代码审查", "只做审查",
        "不要修改", "不修改", "不改代码", "不要改代码", "不要提交", "不要push",
    ]
    is_explicit_review_only = has_review and _contains_any(normalized, review_only_markers)
    review_coding_override_keywords = [
        "创建pr", "创建 pr", "创建pull request", "创建 pull request",
        "提交", "push", "实现", "开发", "新增", "增加", "修复", "改造", "迁移",
    ]
    has_review_coding_override = _contains_any(normalized, review_coding_override_keywords)

    if has_review_fix_context and (has_planning or has_negative):
        return "planning"
    if has_review_fix_context and not has_coding:
        return "planning"
    if has_review and (is_explicit_review_only or has_negative):
        return "review"
    if has_review and (not has_coding or is_explicit_review_only or not has_review_coding_override):
        return "review"
    if has_planning and not has_coding:
        return "planning"
    if has_analysis and not has_coding:
        return "analysis"
    if _contains_any(normalized, qa_keywords) and not has_coding:
        return "qa"
    if has_coding:
        return "coding"
    return "qa"


def _classify_by_model(prompt: str) -> TaskKind:
    """调用模型完成第一层用户意图分类。

    构造系统提示词约束模型只返回 JSON，解析为 ``IntentClassification`` 对象。

    Args:
        prompt: 用户原始输入。

    Returns:
        模型判定的任务类型。
    """

    system_prompt = """你是 AI Coding 系统的第一层用户意图分类器。
你只负责分类，不要回答用户问题，不要生成方案，不要写代码。

你必须从以下固定类别中选择一个：
- coding：用户明确要求修改、实现、生成代码、执行已确认方案、提交、push、创建或复用 PR。
- planning：用户要求技术方案、实施方案、设计步骤、修改建议，或要求先给方案等待确认。
- analysis：用户要求分析项目、梳理结构、解释代码、检查现状、总结模块。
- qa：用户问概念、原因、区别、是否可以、某个文件/函数是做什么的。
- sync：用户只要求 git pull、拉取、同步远程代码，不要求修改。
- inspect：用户只问本地工作区、当前工作目录、有哪些项目。
- review：用户要求代码审查、PR review、读取 PR diff、输出审查报告，尤其是明确不要修改代码。

你必须只返回 JSON，不要返回 Markdown，不要返回额外解释。
JSON 字段必须包含：
- task_kind：固定类别之一。
- confidence：0 到 1 之间的小数。
- reason：中文短理由，不超过 40 个字。
"""
    result = _make_structured_intent_model().invoke(
        [SystemMessage(content=system_prompt), HumanMessage(content=f"用户输入：{prompt}")]
    )
    if isinstance(result, dict):
        parsed = IntentClassification.model_validate(result)
    elif isinstance(result, IntentClassification):
        parsed = result
    else:
        parsed = IntentClassification.model_validate(result)

    logger.info(
        "模型意图分类完成：task_kind=%s confidence=%s reason=%s",
        parsed.task_kind,
        parsed.confidence,
        parsed.reason,
    )
    return parsed.task_kind


def _has_negative_change_marker(normalized: str) -> bool:
    """识别用户明确要求不要改代码、不要提交、只读处理的表达。

    Args:
        normalized: 已规范化的用户输入。

    Returns:
        包含只读/否定标记时返回 ``True``。
    """

    return _contains_any(
        normalized,
        [
            "先不要修改", "不要修改", "不修改", "先不要改", "不要改", "不改代码", "不要改代码",
            "只分析", "只给方案", "只做方案", "只 review", "只review",
            "只做代码审查", "只做审查", "不要提交", "不要push", "不要 push",
            "不要创建pr", "不要创建 pr",
        ],
    )


def _has_explicit_coding_marker(normalized: str) -> bool:
    """识别用户明确允许进入写代码阶段的表达。

    Args:
        normalized: 已规范化的用户输入。

    Returns:
        包含确认实施或编码类标记时返回 ``True``。
    """

    return _contains_any(
        normalized,
        [
            "确认实施", "确定实施", "开始实施", "确认，实施", "确认该方案",
            "按照方案实施", "按方案实施", "执行方案", "生成代码", "修改代码",
            "开始修改", "实现这个功能", "开发这个功能", "提交", "push",
            "创建pr", "创建 pr", "创建 pull request",
        ],
    )


def _has_review_marker(normalized: str) -> bool:
    """识别代码审查相关表达。

    Args:
        normalized: 已规范化的用户输入。

    Returns:
        包含审查类标记时返回 ``True``。
    """

    return _contains_any(
        normalized,
        [
            "review", "reviewer", "代码审查", "代码评审", "审查pr", "审查 pr",
            "评审pr", "评审 pr", "pr review", "pr审查", "pr 审查", "pr评审", "pr 评审",
            "pull request review", "输出中文审查报告",
            "读取 pr diff", "读取pr diff", "pr diff", "审查报告",
        ],
    )


def _has_planning_marker(normalized: str) -> bool:
    """识别方案类表达。

    Args:
        normalized: 已规范化的用户输入。

    Returns:
        包含方案/计划类标记时返回 ``True``。
    """

    return _contains_any(
        normalized,
        [
            "方案", "计划", "设计", "步骤", "怎么做", "如何做",
            "先列出", "先帮我设计", "由我确认", "修改建议", "实施建议",
        ],
    )


def _has_review_fix_plan_marker(normalized: str) -> bool:
    """识别"基于审查结果生成修复方案"的只读方案任务。

    需要同时包含审查来源标记和方案类标记才判定为是。

    Args:
        normalized: 已规范化的用户输入。

    Returns:
        同时命中审查来源和修复方案标记时返回 ``True``。
    """

    has_review_source = _contains_any(
        normalized,
        ["审查报告", "review findings", "结构化 findings", "findings", "/reviews/"],
    )
    has_fix_plan = _contains_any(
        normalized,
        ["修复方案", "修改方案", "整改方案", "生成方案", "给我一个方案"],
    )
    return has_review_source and has_fix_plan


def _has_coding_marker(normalized: str) -> bool:
    """识别普通开发实现类表达。

    Args:
        normalized: 已规范化的用户输入。

    Returns:
        包含开发实现类标记时返回 ``True``。
    """

    return _contains_any(
        normalized,
        [
            "修改", "修复", "新增", "增加", "实现", "开发", "改为", "改成", "改造",
            "迁移", "接入", "升级", "重构", "提交", "push", "运行测试",
        ],
    )


def _apply_security_guard(prompt: str, predicted: TaskKind) -> TaskKind:
    """对模型分类结果做权限安全修正。

    这里是整个分类链路的安全阀：模型可以建议任务类型，但不能突破用户明确给出的只读边界。

    Args:
        prompt: 用户原始输入。
        predicted: 模型预测的任务类型。

    Returns:
        经安全规则修正后的最终任务类型。
    """

    normalized = _normalize_prompt(prompt)
    has_negative = _has_negative_change_marker(normalized)
    has_explicit_coding = _has_explicit_coding_marker(normalized)
    has_review = _has_review_marker(normalized)
    has_planning = _has_planning_marker(normalized)
    has_review_fix_plan = _has_review_fix_plan_marker(normalized)
    has_coding = _has_coding_marker(normalized)

    if has_review_fix_plan and has_negative:
        return "planning"
    if has_review and has_negative:
        return "review"
    if predicted == "coding" and has_negative:
        if has_review:
            return "review"
        if has_planning:
            return "planning"
        return "analysis"

    if has_explicit_coding and not has_negative:
        return "coding"

    if has_review and not has_coding:
        return "review"

    if has_planning and not has_coding:
        return "planning"

    return predicted


def classify_task_kind(prompt: str) -> TaskKind:
    """按用户意图选择 Agent 工作模式。

    coding 是唯一允许写文件、执行提交、push 和创建 PR 的模式。其余模式都按只读任务处理，
    最多准备或读取本地仓库，然后输出分析、方案、审查报告或答案。

    分类链路：关键词快速匹配 → 模型结构化分类 → 安全规则修正 → 失败回退关键词兜底。

    Args:
        prompt: 用户原始输入。

    Returns:
        最终确定的任务类型。
    """

    normalized = _normalize_prompt(prompt)
    if not normalized:
        return "qa"

    if is_pull_only_task(prompt):
        logger.info("关键词快速分类：task_kind=sync")
        return "sync"
    if is_workspace_listing_task(prompt):
        logger.info("关键词快速分类：task_kind=inspect")
        return "inspect"

    try:
        predicted = _classify_by_model(prompt)
    except Exception as exc:
        fallback = _classify_by_keyword_backup(prompt)
        logger.warning(
            "模型意图分类失败，回退关键词分类：fallback=%s error=%s",
            fallback,
            exc,
        )
        return _apply_security_guard(prompt, fallback)

    final_kind = _apply_security_guard(prompt, predicted)
    if final_kind != predicted:
        logger.info("意图分类被安全规则修正：model=%s final=%s", predicted, final_kind)
    return final_kind


def is_read_only_task(task_kind: TaskKind) -> bool:
    """只读任务不允许写文件、提交、push 和创建 PR。

    Args:
        task_kind: 待判定的任务类型。

    Returns:
        属于只读任务时返回 ``True``。
    """

    return task_kind in {"analysis", "planning", "qa", "inspect", "review"}
