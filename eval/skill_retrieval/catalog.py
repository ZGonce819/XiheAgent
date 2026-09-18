"""技能检索 eval 的技能目录与查询集。

本模块负责两件事：

1. **造一份有规模的技能目录**（:data:`SKILL_COUNT` 条，默认 150）。技能会被
   真实写盘成 ``<名字>/SKILL.md``，再由 DeepAgents 的真实加载器解析，而不是
   手工拼 ``skills_metadata`` 字典 —— 这样评估走的是生产同一条链路。
2. **造查询集**（:data:`QUERIES`）。查询分「门槛样本」与「未校准样本」两档，前者
   计入命中率门槛，后者只观测（口径见 :class:`Query`）。

目录构成（三部分之和恰好等于 :data:`SKILL_COUNT`）：

``ALWAYS_ON``
    与 :data:`services.skill_retrieval.ALWAYS_ON_SKILLS` 同名的三条。生产环境
    里 ``prompt.py`` 按名字引用它们，因此它们必须始终出现在提示词里。eval 把
    它们放进目录，是为了验证检索不会把它们挤掉。
``TARGETS``
    查询指向的技能。描述写得**有区分度**（含专有说法，如「N+1 查询」
    「trace id」），使「能不能挑中」有明确答案。
``NOISE``
    干扰项，由「主题家族 × 切面 × 修饰」确定性组合生成。同一家族内的干扰项
    与目标技能**共享主题词**（如都出现「数据库」「迁移」），构成难例 —— 如果
    检索只靠「有没有出现主题词」就能过，那这份 eval 没有意义。

模块内所有内容都是**确定性**的：没有随机数、没有时间戳、没有集合迭代顺序
依赖。同样的代码跑两次必须得到同样的目录与同样的查询。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from services.skill_retrieval import ALWAYS_ON_SKILLS

SKILL_COUNT = 150
"""技能目录的目标条数。"""


@dataclass(frozen=True)
class SkillSpec:
    """一条待落盘的技能。

    Attributes:
        name: 技能名，同时是目录名。
        description: frontmatter 里的说明，也是检索打分的主要文本。
        family: 主题家族标识，仅用于报告分组。
        metadata: frontmatter 里的 ``metadata`` 映射。检索会把它纳入打分，
            因此这里刻意给部分技能放了 ``keywords``。
    """

    name: str
    description: str
    family: str
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Query:
    """一条评估查询。

    查询分两档，对应两种不同的用途：

    **门槛样本**（``gated=True``）
        期望技能的**描述里含有查询的关键说法**，因此「命中」在词法上是可达的。
        这类样本计入命中率门槛，用来验证检索基本可用。
    **未校准样本**（``gated=False``）
        期望技能的描述里**不含**查询的关键说法，需要理解语义才可能命中；或者
        压根是领域外查询。这类样本**只观测、不判失败** —— 它们的期望值本身没有
        经过校准，拿它们当质量门槛会得出误导性的结论。

    Attributes:
        query_id: 稳定标识，出现在报告里。
        text: 用户消息正文，检索查询就是它。
        expected: 期望被列出的技能名；``None`` 表示没有期望。
        family: 目标技能所属家族，用于报告分组。
        gated: 是否计入命中率门槛。
        note: 这条查询想覆盖什么。
    """

    query_id: str
    text: str
    expected: str | None
    family: str
    gated: bool = True
    note: str = ""


# ── 常驻技能（与生产 prompt.py 引用的三个同名） ──────────────────────────────

_ALWAYS_ON_DESCRIPTIONS: dict[str, str] = {
    "repo-bootstrap-analysis": "仓库引导分析，快速摸清结构、启动方式与测试方式",
    "ai-coding-implementation": "AI 编码实现，辅助代码生成并控制实施节奏",
    "code-review": "代码审查，按规则给出带定位与严重级别的发现",
}

ALWAYS_ON: tuple[SkillSpec, ...] = tuple(
    SkillSpec(
        name=name,
        description=_ALWAYS_ON_DESCRIPTIONS[name],
        family="builtin",
    )
    for name in ALWAYS_ON_SKILLS
)

if tuple(spec.name for spec in ALWAYS_ON) != tuple(ALWAYS_ON_SKILLS):
    msg = "常驻技能名单与 services.skill_retrieval.ALWAYS_ON_SKILLS 不一致"
    raise RuntimeError(msg)


# ── 目标技能（查询指向它们） ─────────────────────────────────────────────────

TARGETS: tuple[SkillSpec, ...] = (
    SkillSpec(
        "db-migration-check",
        "数据库迁移检查，校验 schema 变更、回滚脚本与数据兼容性",
        "db",
        {"keywords": "migration, schema, rollback, 迁移, 回滚"},
    ),
    SkillSpec(
        "db-rollback-drill",
        "数据库回滚演练，在预发环境验证回滚步骤与耗时",
        "db",
    ),
    SkillSpec(
        "frontend-theme-token",
        "前端主题令牌梳理，统一配色与间距变量",
        "web",
    ),
    SkillSpec(
        "frontend-a11y-audit",
        "前端无障碍审查，检查对比度、键盘可达与屏幕阅读器",
        "web",
    ),
    SkillSpec(
        "api-timeout-tuning",
        "接口超时调优，梳理重试与熔断参数",
        "net",
        {"keywords": "timeout, retry, circuit breaker, 超时, 熔断"},
    ),
    SkillSpec(
        "dns-failover-plan",
        "域名解析故障转移，配置多线路切换与健康检查",
        "net",
    ),
    SkillSpec(
        "oauth-token-rotation",
        "OAuth 令牌轮换，处理刷新与吊销",
        "auth",
    ),
    SkillSpec(
        "rbac-permission-matrix",
        "权限矩阵梳理，按角色列出可访问资源",
        "auth",
    ),
    SkillSpec(
        "log-trace-correlation",
        "日志链路关联，把 trace id 串进结构化日志",
        "log",
    ),
    SkillSpec(
        "audit-log-retention",
        "审计日志留存策略，按合规要求设定保留期",
        "log",
    ),
    SkillSpec(
        "flaky-test-triage",
        "不稳定用例排查，定位偶发失败的根因",
        "test",
    ),
    SkillSpec(
        "coverage-gate-setup",
        "覆盖率门槛设置，按目录设定阈值",
        "test",
    ),
    SkillSpec(
        "deploy-rollback-runbook",
        "部署回滚手册，写清触发条件与执行步骤",
        "deploy",
    ),
    SkillSpec(
        "container-image-slimming",
        "容器镜像瘦身，裁剪基础镜像与构建层",
        "deploy",
    ),
    SkillSpec(
        "changelog-automation",
        "变更记录自动化，从提交信息生成 changelog",
        "docs",
    ),
    SkillSpec(
        "adr-writing-guide",
        "架构决策记录写作指南，说明何时写与怎么写",
        "docs",
    ),
    SkillSpec(
        "cache-stampede-guard",
        "缓存击穿防护，加锁与空值缓存",
        "perf",
    ),
    SkillSpec(
        "n-plus-one-query-fix",
        "N+1 查询修复，批量加载关联数据",
        "perf",
    ),
    SkillSpec(
        "secret-scanning-setup",
        "密钥扫描接入，阻止凭据进入提交",
        "sec",
    ),
    SkillSpec(
        "dependency-vuln-triage",
        "依赖漏洞分级，按可利用性排序修复顺序",
        "sec",
    ),
)


# ── 干扰项（确定性组合生成） ─────────────────────────────────────────────────

_FAMILIES: tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...] = (
    ("db", "数据库", (("migration", "迁移"), ("index", "索引"), ("slow-query", "慢查询"), ("connection-pool", "连接池"), ("backup", "备份"))),
    ("web", "前端", (("theme", "主题"), ("component", "组件"), ("route", "路由"), ("bundle", "打包"), ("i18n", "国际化"))),
    ("net", "网络", (("timeout", "超时"), ("retry", "重试"), ("circuit-breaker", "熔断"), ("proxy", "代理"), ("dns", "域名解析"))),
    ("auth", "认证", (("token", "令牌"), ("session", "会话"), ("permission", "权限"), ("sso", "单点登录"), ("password", "口令"))),
    ("log", "日志", (("level", "级别"), ("rotation", "轮转"), ("trace", "链路追踪"), ("alert", "告警"), ("audit", "审计"))),
    ("test", "测试", (("unit", "单元测试"), ("fixture", "夹具"), ("coverage", "覆盖率"), ("mock", "打桩"), ("e2e", "端到端测试"))),
    ("deploy", "部署", (("pipeline", "流水线"), ("rollback", "回滚"), ("container", "容器"), ("env-var", "环境变量"), ("release", "发布"))),
    ("docs", "文档", (("readme", "说明文档"), ("changelog", "变更记录"), ("api-ref", "接口文档"), ("diagram", "架构图"), ("comment", "注释"))),
    ("perf", "性能", (("cache", "缓存"), ("memory", "内存"), ("concurrency", "并发"), ("profiling", "性能剖析"), ("batch", "批处理"))),
    ("sec", "安全", (("injection", "注入防护"), ("secret", "密钥"), ("dependency", "依赖漏洞"), ("least-privilege", "最小权限"), ("encrypt", "加密"))),
)

_MODIFIERS: tuple[tuple[str, str], ...] = (
    ("", "，梳理常见做法与注意事项"),
    ("-automation", "，支持自动化执行与结果归档"),
    ("-checklist", "，提供逐项检查清单与常见误区"),
)


def _noise(count: int) -> tuple[SkillSpec, ...]:
    """按确定性组合生成干扰技能。

    ``(家族, 切面)`` 组合共 50 种，先铺满一轮，再按修饰逐轮叠加，因此同一个
    切面会出现「基础 / 自动化 / 清单」三个高度相似的技能 —— 这是刻意制造的
    难例。

    Args:
        count: 要生成的条数。

    Returns:
        干扰技能元组，顺序确定。
    """
    specs: list[SkillSpec] = []
    for index in range(count):
        family_id, family_label, aspects = _FAMILIES[index % len(_FAMILIES)]
        aspect_slug, aspect_label = aspects[(index // len(_FAMILIES)) % len(aspects)]
        suffix, purpose = _MODIFIERS[index // 50]
        specs.append(
            SkillSpec(
                name=f"{family_id}-{aspect_slug}{suffix}",
                description=f"{family_label}{aspect_label}{purpose}",
                family=family_id,
            )
        )
    return tuple(specs)


def build_catalog() -> tuple[SkillSpec, ...]:
    """构造完整的技能目录。

    干扰项条数由 :data:`SKILL_COUNT` 减去常驻与目标技能数推导，保证总数恰好
    等于 :data:`SKILL_COUNT`。

    Returns:
        技能目录，顺序为「干扰项 → 目标技能 → 常驻技能」，与生产加载顺序
        （源顺序）无关，仅保证确定性。

    Raises:
        RuntimeError: 生成结果里出现重名，或总数不等于 :data:`SKILL_COUNT`。
    """
    noise_count = SKILL_COUNT - len(TARGETS) - len(ALWAYS_ON)
    catalog = (*_noise(noise_count), *TARGETS, *ALWAYS_ON)

    names = [spec.name for spec in catalog]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        msg = f"技能目录出现重名：{duplicates}"
        raise RuntimeError(msg)
    if len(catalog) != SKILL_COUNT:
        msg = f"技能目录条数 {len(catalog)} 与 SKILL_COUNT={SKILL_COUNT} 不符"
        raise RuntimeError(msg)
    return catalog


# ── 查询集 ───────────────────────────────────────────────────────────────────

QUERIES: tuple[Query, ...] = (
    Query("q01", "这次数据库迁移能不能回滚？帮我核对一下 schema 变更和回滚脚本", "db-migration-check", "db"),
    Query("q02", "想在预发环境做一次数据库回滚演练，验证步骤和耗时", "db-rollback-drill", "db"),
    Query("q03", "前端主题的配色和间距变量有点乱，帮我统一一下", "frontend-theme-token", "web"),
    Query("q04", "帮我审查前端的无障碍，重点看对比度和键盘可达性", "frontend-a11y-audit", "web"),
    Query("q05", "接口老是超时，帮我梳理重试和熔断参数", "api-timeout-tuning", "net"),
    Query("q06", "域名解析要做多线路故障转移，健康检查怎么配", "dns-failover-plan", "net"),
    Query("q07", "OAuth 令牌轮换怎么做，刷新和吊销要怎么处理", "oauth-token-rotation", "auth"),
    Query("q08", "按角色梳理一份权限矩阵，列出每个角色能访问的资源", "rbac-permission-matrix", "auth"),
    Query("q09", "结构化日志里怎么把 trace id 串起来做链路关联", "log-trace-correlation", "log"),
    Query("q10", "审计日志按合规要求要留多久，留存策略怎么定", "audit-log-retention", "log"),
    Query("q11", "有几个用例偶发失败，帮我排查不稳定用例的根因", "flaky-test-triage", "test"),
    Query("q12", "想按目录设置覆盖率门槛，阈值怎么配", "coverage-gate-setup", "test"),
    Query("q13", "写一份部署回滚手册，说明触发条件和执行步骤", "deploy-rollback-runbook", "deploy"),
    Query("q14", "容器镜像太大了，帮我裁剪基础镜像和构建层", "container-image-slimming", "deploy"),
    Query("q15", "想从提交信息自动生成变更记录", "changelog-automation", "docs"),
    Query("q16", "架构决策记录该怎么写，有模板吗", "adr-writing-guide", "docs"),
    Query("q17", "缓存击穿怎么防，加锁和空值缓存哪种好", "cache-stampede-guard", "perf"),
    Query("q18", "接口里 N+1 查询很多，想改成批量加载关联数据", "n-plus-one-query-fix", "perf"),
    Query("q19", "想接入密钥扫描，阻止凭据被提交进仓库", "secret-scanning-setup", "sec"),
    Query("q20", "依赖漏洞太多，怎么按可利用性分级排修复顺序", "dependency-vuln-triage", "sec"),
    Query(
        "u01",
        "线上接口变慢了，怎么定位是哪一段查询拖的",
        "n-plus-one-query-fix",
        "perf",
        gated=False,
        note="目标技能描述里没有「变慢」「定位」这类词，需要理解才能命中",
    ),
    Query(
        "u02",
        "我们的代码里会不会不小心把密码提交上去了",
        "secret-scanning-setup",
        "sec",
        gated=False,
        note="口语化说法，目标技能描述里用的是「密钥」「凭据」",
    ),
    Query(
        "u03",
        "新同学接手这个仓库要多久才能上手",
        "repo-bootstrap-analysis",
        "builtin",
        gated=False,
        note="指向常驻技能：它无论相关与否都必须被列出，这里观测的是常驻机制本身",
    ),
    Query(
        "obs01",
        "今天天气怎么样",
        None,
        "",
        gated=False,
        note="领域外查询，观测检索会挑出什么",
    ),
    Query(
        "obs02",
        "帮我写一首关于秋天的诗",
        None,
        "",
        gated=False,
        note="领域外查询，观测检索会挑出什么",
    ),
)

GATED_QUERIES: tuple[Query, ...] = tuple(query for query in QUERIES if query.gated)


def write_skill(root: Path, spec: SkillSpec) -> Path:
    """把一个技能落盘成 ``<root>/<名字>/SKILL.md``。

    frontmatter 按 Agent Skills 规范写，``metadata`` 非空时写成嵌套映射 ——
    这是为了顺带验证「检索面包含 metadata」在生产解析链路下确实成立。

    Args:
        root: 技能根目录（会自动创建）。
        spec: 待落盘的技能。

    Returns:
        写入的技能目录。
    """
    directory = root / spec.name
    directory.mkdir(parents=True, exist_ok=True)

    frontmatter = [f"name: {spec.name}", f"description: {spec.description}"]
    if spec.metadata:
        frontmatter.append("metadata:")
        frontmatter.extend(f"  {key}: {value}" for key, value in spec.metadata.items())

    body = "\n".join(
        [
            "---",
            *frontmatter,
            "---",
            "",
            f"# {spec.name}",
            "",
            "这里是技能正文。它不应出现在提示词里，只有模型主动读取时才会被加载。",
            "",
        ]
    )
    (directory / "SKILL.md").write_text(body, encoding="utf-8")
    return directory
