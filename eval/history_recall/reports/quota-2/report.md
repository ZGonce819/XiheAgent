# 历史召回评估报告

- 运行标签：`quota-2`
- 问题集：`eval\history_recall\samples.jsonl`
- 数据库 schema：`xihe_knowledge_eval_quota_2_d39f6f`（一次性，运行结束后删除）
- 样本数：执行 75 / 共 75（构造或执行出错 0）
- 开关：检索 `True`、结论写入 `True`、允许列表 `未限制`

## 一、结论摘要

- 硬门槛样本：通过 64 条，未通过 1 条（共 65 条）
- 硬约束违规：0 条（无）
- Recall@k：100.0%（门槛样本 55 条，43/43 个必中语料）
- 只观测类样本：0/10 条满足期望（这类样本不判失败，用于测量能力边界）
- 尚未校准的样本：20 条（未校准的期望值不作为质量门槛）
- **未测量的指标**：
  - 引用可打开率（目标 ≥95%）：需要完整 Agent 执行并观察其答案引用，本次只跑了检索层
  - 重复工具调用数中位数下降（目标 ≥20%）：需要「无召回 / 有召回」两组 Agent 运行
  - 纯数据库检索 p95（目标 ≤300ms）：上报的是含本地适用性检查的端到端耗时，两者未分开测量

## 二、验收门槛

| 门槛 | 目标 | 实测 | 判定 | 说明 |
| --- | --- | --- | --- | --- |
| 越界召回与伪造验证 | 0 条 | 0 条 | 达标 | 不设容忍区间，也不因样本未校准而豁免：出现即缺陷 |
| Recall@k | ≥ 85% | 100.0%（43/43，门槛样本 55 条） | 达标 | 另有 20 条未校准样本不计入（hr-synonym-001、hr-version-001、hr-structure-002…）：期望值未确认前它只能观测，不能定标准 |
| 无匹配时回落真实工具 | 全部按要求提示 | 13/13 | 达标 | 只核对了提示语；模型是否真的改用实时工具属 Agent 层，未测量 |
| 检索 p95 时延（含适用性检查） | ≤ 2000 ms | p95 1858 ms / p50 0 ms（75 条） | 达标 | — |
| 引用可打开率 | ≥ 95% | 未测量 | 不适用（未测量） | 需要完整 Agent 执行，本次只跑了检索层 |
| 重复工具调用数中位数下降 | ≥ 20% | 未测量 | 不适用（未测量） | 需要「无召回 / 有召回」两组 Agent 运行，本次只跑了检索层 |

## 三、按类别统计

| 类别 | 样本 | 已执行 | 通过 | 门槛样本 | Recall@k |
| --- | --- | --- | --- | --- | --- |
| cross_repo | 6 | 6 | 6 | 6 | 5/5 |
| deleted_thread | 4 | 4 | 4 | 4 | 1/1 |
| edited_branch | 4 | 4 | 4 | 3 | 2/2 |
| historical_decision | 10 | 10 | 10 | 10 | 10/10 |
| incident_fix | 10 | 10 | 10 | 10 | 10/10 |
| no_answer | 8 | 8 | 8 | 8 | — |
| stale_knowledge | 10 | 10 | 10 | 9 | 10/10 |
| structure_repeat | 7 | 7 | 7 | 0 | — |
| synonym_rewrite | 10 | 10 | 0 | 0 | — |
| version_change | 6 | 6 | 5 | 5 | 5/5 |

按类别分开统计是计划第 15 节的硬要求：P1 身份修复、P2 记忆修复都是独立收益，不分层会把它们记到召回功能头上。

## 四、逐条结果

| 样本 | 类别 | 查询 | 结果 | 必中排名 | 说明 |
| --- | --- | --- | --- | --- | --- |
| hr-decision-001 | historical_decision | checkpoint 存储 | 通过 | c-checkpoint-redis→1 | 历史决策类基线：已接受、单一证据的决策应被召回并排在首位 |
| hr-decision-002 | historical_decision | import-linter | 通过 | c-import-linter→1 | 带依赖路径的决策：适用性应为检查通过态，不能被误标为需复核 |
| hr-incident-001 | incident_fix | pytest 收集失败 | 通过 | c-pytest-cjk→1 | 旧故障修复：先失败后成功的两条证据都要能读到，且失败证据的退出码保持 1 不被改写 |
| hr-incident-002 | incident_fix | authentication plugin | 通过 | c-pymysql-crypto→1 | 证据只到 observed：pip show 能证明装了包，不能单独证明它解决了认证报错，不应被升级成 supported |
| hr-noanswer-001 | no_answer | Kubernetes 部署清单 | 通过 | — | 范围内确实无依据：必须返回 no_match 并提示改用实时工具，不能含糊说成「没有历史」 |
| hr-stale-001 | stale_knowledge | 测试入口 | 通过 | c-entry-new→1 | 过期知识不进当前模式：被替代的旧结论必须被排除，不能因为时间较新就静默覆盖 |
| hr-stale-002 | stale_knowledge | 测试入口 | 通过 | c-entry-old→1、c-entry-new→2 | history 模式用于追溯「当初为什么改」：旧结论要返回，并带出它被谁替代 |
| hr-crossrepo-001 | cross_repo | 测试入口 | 通过 | c-entry-mine→1 | 跨仓库同名内容：另一仓库的结论即使内容完全一致也不得召回，对应「跨仓库泄漏为 0」的硬约束 |
| hr-edited-001 | edited_branch | 编辑废弃分支 | 通过 | — | 编辑废弃分支：当前模式必须排除，它是「旧分支退出当前召回」的可见行为 |
| hr-edited-002 | edited_branch | 编辑废弃分支 | 通过 | c-edited-branch→1 | 同一语料在 history 模式的对照：历史分支可追溯，但不能反过来泄漏到 current |
| hr-deleted-001 | deleted_thread | 异步队列 | 通过 | — | 删除优先于一切：连 history 模式都不能召回，对应用户删除优先于 append-only 的要求 |
| hr-synonym-001 | synonym_rewrite | 跑单测的命令是啥 | 观测未达 | c-synonym-entry→未命中 | 期望命中（found），实际 no_match；候选 0 条，说明：该仓库范围内没有匹配的当前仍有效的结论。请使用实时文件、Git 与测试命令核实，不要据此推断结论不存在。；未命中 c-synonym-entry |
| hr-version-001 | version_change | MySQL 认证依赖哪个库 | **未通过** | c-deps-cryptography→未命中 | 期望命中（found），实际 no_match；候选 0 条，说明：该仓库范围内没有匹配的当前仍有效的结论。请使用实时文件、Git 与测试命令核实，不要据此推断结论不存在。；未命中 c-deps-cryptography；首位期望 c-deps-cryptography，实际为 无结果；c-deps-cryptography 未出现在结果里，无法核对其适用性 |
| hr-version-002 | version_change | cryptography | 通过 | c-deps-crypto→1 | 依赖已变的调参样本：查询按工具文档要求用关键词，让这条真的走到适用性判定。hr-version-001 保留自然语言问句形态（留出集，不调参），用于观察词面召回边界 |
| hr-decision-003 | historical_decision | alembic | 通过 | c-migration-alembic→1 | 已接受决策 + 单一成功证据：应当被召回并排在首位，且证据因决策类型封顶在 observed |
| hr-decision-004 | historical_decision | Argon2id | 通过 | c-password-argon2→1 | 安全相关决策：查询词是算法名，考的是术语入口能否直接命中 |
| hr-decision-005 | historical_decision | structlog | 通过 | c-logging-structlog→1 | 日志规范决策：写入链路与查询词同为库名 |
| hr-decision-006 | historical_decision | Zustand | 通过 | c-frontend-state→1 | 技术选型决策：与另一条候选（Redux）同句出现，考的是否被正确召回而非歧义 |
| hr-decision-007 | historical_decision | codeup 流水线 | 通过 | c-ci-codeup→1 | 平台迁移决策：查询用中文术语，考的是中文短语能否命中 |
| hr-decision-008 | historical_decision | 游标分页 | 通过 | c-pagination-cursor→1 | 接口约定决策：同时出现 offset 反例，验证召回的是正例 |
| hr-decision-009 | historical_decision | UTC | 通过 | c-timezone-utc→1 | 带依赖路径的决策：适用性应为检查通过态，与 hr-decision-002 一起覆盖该路径的两条样例 |
| hr-decision-010 | historical_decision | 懒加载 | 通过 | c-orm-explicit-join→1 | 性能约定决策：查询词是概念名，考的是概念词与正文的重叠是否够召回 |
| hr-incident-003 | incident_fix | collections.Mapping | 通过 | c-collections-abc→1 | 先失败后成功两条证据：失败证据的退出码必须保持 1，不能被改写成成功 |
| hr-incident-004 | incident_fix | 413 Request Entity Too Large | 通过 | c-nginx-body-size→1 | 部署层配置问题：症状是 HTTP 状态码，考的是错误信息原文能否召回 |
| hr-incident-005 | incident_fix | connection pool exhausted | 通过 | c-conn-pool-leak→1 | 资源泄漏类故障：症状与根因不在同一层，考的是能否直接命中根因结论 |
| hr-incident-006 | incident_fix | No space left on device | 通过 | c-log-rotation→1 | 运维类故障：查的是错误原文，命中后应避免再全盘搜索日志配置 |
| hr-incident-007 | incident_fix | ERESOLVE | 通过 | c-npm-eresolve→1 | 前端依赖故障：结论里带明确的反模式（--force），命中后不应再尝试强行安装 |
| hr-incident-008 | incident_fix | UnicodeDecodeError | 通过 | c-read-encoding→1 | 本地环境故障：Windows 特有的默认编码问题，是典型重复踩坑场景 |
| hr-incident-009 | incident_fix | EADDRINUSE | 通过 | c-port-in-use→1 | 启动类故障：结论明确反对绕过式修复，命中后应少走一次试错 |
| hr-incident-010 | incident_fix | certificate verify failed | 通过 | c-tls-expired-cert→1 | 证据只到 observed：curl 的报错输出能说明现象，但退出码未知时不足以支撑断言 |
| hr-structure-002 | structure_repeat | rate_limit | 通过 | c-rate-limit-module→1 | 结构入口类：命中后应省掉一次全仓搜索；重复工具调用数在 P6 对照试验中度量，本层不宣称 |
| hr-structure-003 | structure_repeat | app_config | 通过 | c-config-loader→1 | 配置入口类：这类问题在真实会话里会反复出现，是重复搜索的主要来源 |
| hr-structure-004 | structure_repeat | run_knowledge_migrations | 通过 | c-migrate-entry→1 | 迁移入口类：结构事实被召回后，模型不必再 grep 一遍迁移函数 |
| hr-structure-005 | structure_repeat | api/client | 通过 | c-frontend-api-client→1 | 跨端结构入口：查询词是文件名的自然写法，考的是词面召回 |
| hr-structure-006 | structure_repeat | registerServices | 通过 | c-grpc-register→1 | 另一技术栈的结构入口：避免结构类样本全部落在 Python 上 |
| hr-structure-007 | structure_repeat | daily_sync | 通过 | c-dag-entry→1 | 调度入口类：结构事实与调度配置同处一文件 |
| hr-structure-008 | structure_repeat | task_router | 通过 | c-task-router-entry→1 | 本项目真实结构入口：与 hr-structure-001 同主题但从检索层判定 |
| hr-noanswer-002 | no_answer | Helm chart 回滚 | 通过 | — | 空语料：没有可命中项时必须返回 no_match，并在提示语里明确请用实时工具核实 |
| hr-noanswer-003 | no_answer | 多租户数据隔离方案 | 通过 | — | 空语料：设计类问题最容易出现「凭印象回答」，这条考的是会不会老实说没有依据 |
| hr-noanswer-004 | no_answer | Sentry 告警阈值 | 通过 | — | 空语料：第三方服务配置不在仓库范围内 |
| hr-noanswer-005 | no_answer | 灰度发布比例 | 通过 | — | 有相邻但无关的语料：不能因为「仓库里有些知识」就返回不相干内容 |
| hr-noanswer-006 | no_answer | GraphQL 订阅 | 通过 | — | 词汇完全不相交的相邻语料：验证模糊匹配不会把无关结论拉进来 |
| hr-noanswer-007 | no_answer | 移动端推送证书 | 通过 | — | 空语料：跨端主题，仓库里没有任何相关知识 |
| hr-noanswer-008 | no_answer | Kafka 分区数 | 通过 | — | 留出集：空语料的无答案场景，用于观察「没有依据时的措辞」是否稳定 |
| hr-stale-003 | stale_knowledge | 部署方式 | 通过 | c-deploy-k8s→1 | 被替代的部署方式不得在 current 模式出现；新结论必须返回 |
| hr-stale-004 | stale_knowledge | 部署方式 | 通过 | c-deploy-docker→1、c-deploy-k8s→2 | history 模式对照：要能看到被替代的旧结论，用于回答「当初为什么换」 |
| hr-stale-005 | stale_knowledge | 接口认证 | 通过 | c-auth-jwt→1 | 安全相关过期知识：旧认证方式若被当作当前事实采用，后果直接 |
| hr-stale-006 | stale_knowledge | 缓存 | 通过 | c-cache-redis→1 | 同主体键的两代结论：current 模式只保留新的一条 |
| hr-stale-007 | stale_knowledge | 分支命名 | 通过 | — | 已撤销的约定：撤销与替代不同，它没有后继结论，current 模式一条都不该返回 |
| hr-stale-008 | stale_knowledge | 接口认证 | 通过 | c-auth-cookie→1、c-auth-jwt→2 | 留出集：历史模式追溯认证方式变更的原因，不参与调参 |
| hr-stale-009 | stale_knowledge | uv | 通过 | c-pkg-uv→1 | 工具链换代：旧命令若被召回，会直接被拿去执行并失败 |
| hr-stale-010 | stale_knowledge | Parquet | 通过 | c-report-parquet→1 | 格式约定变更：新旧结论共用主体键，考的是替代关系是否生效 |
| hr-crossrepo-002 | cross_repo | 部署脚本 | 通过 | c-deploy-mine→1 | 同名主体键、不同内容：另一仓库命中措辞同样贴题，泄漏检查才有意义 |
| hr-crossrepo-003 | cross_repo | 覆盖率阈值 | 通过 | c-coverage-mine→1 | 内容完全一致的两条结论：字面无法区分，只能靠仓库范围挡住 |
| hr-crossrepo-004 | cross_repo | 数据库连接池大小 | 通过 | — | 留出集：本仓库没有这条知识，另一仓库有 —— 期望是「本仓库没有」，不是「随便给一条」 |
| hr-crossrepo-005 | cross_repo | 接口超时 | 通过 | c-timeout-mine→1 | 另一仓库的结论更长、与查询重叠更多：范围先于相似度，不能被更相似的越界内容顶掉 |
| hr-crossrepo-006 | cross_repo | 会话缓存 | 通过 | c-cache-current→1 | 生命周期与仓库范围两条过滤叠加：被替代的旧结论与别的仓库都不能出现 |
| hr-edited-003 | edited_branch | 适配器注册 | 通过 | — | 旧分支上的结论在 current 模式必须排除：它曾有证据，但证据全部挂在废弃分支上 |
| hr-edited-004 | edited_branch | 适配器注册 | 通过 | c-edited-adapter→1 | history 模式对照：废弃分支可追溯，但不能反过来泄漏到 current |
| hr-deleted-002 | deleted_thread | 指标上报 | 通过 | — | current 模式下的已删除来源：证据来源进入删除流程后，结论不得再被召回 |
| hr-deleted-003 | deleted_thread | 指标上报 | 通过 | c-metrics-active→1 | 同一 subject_key 的两条结论，一条来源被删除：只排除被删的那条，其余照常返回 |
| hr-deleted-004 | deleted_thread | 发布窗口 | 通过 | — | history 模式也拿不到：用户删除优先于 append-only，这条是硬约束 |
| hr-synonym-002 | synonym_rewrite | 怎么跑测试 | 观测未达 | c-run-tests→未命中 | 期望命中（found），实际 no_match；候选 0 条，说明：该仓库范围内没有匹配的当前仍有效的结论。请使用实时文件、Git 与测试命令核实，不要据此推断结论不存在。；未命中 c-run-tests |
| hr-synonym-003 | synonym_rewrite | 代码风格检查用啥 | 观测未达 | c-lint-tool→未命中 | 期望命中（found），实际 no_match；候选 0 条，说明：该仓库范围内没有匹配的当前仍有效的结论。请使用实时文件、Git 与测试命令核实，不要据此推断结论不存在。；未命中 c-lint-tool |
| hr-synonym-004 | synonym_rewrite | 数据库连接怎么配 | 观测未达 | c-db-url→未命中 | 期望命中（found），实际 no_match；候选 0 条，说明：该仓库范围内没有匹配的当前仍有效的结论。请使用实时文件、Git 与测试命令核实，不要据此推断结论不存在。；未命中 c-db-url |
| hr-synonym-005 | synonym_rewrite | 发版流程 | 观测未达 | c-release-flow→未命中 | 期望命中（found），实际 no_match；候选 0 条，说明：该仓库范围内没有匹配的当前仍有效的结论。请使用实时文件、Git 与测试命令核实，不要据此推断结论不存在。；未命中 c-release-flow |
| hr-synonym-006 | synonym_rewrite | 回滚咋弄 | 观测未达 | c-rollback→未命中 | 期望命中（found），实际 no_match；候选 0 条，说明：该仓库范围内没有匹配的当前仍有效的结论。请使用实时文件、Git 与测试命令核实，不要据此推断结论不存在。；未命中 c-rollback |
| hr-synonym-007 | synonym_rewrite | 缓存穿透怎么防 | 观测未达 | c-cache-null→未命中 | 期望命中（found），实际 no_match；候选 0 条，说明：该仓库范围内没有匹配的当前仍有效的结论。请使用实时文件、Git 与测试命令核实，不要据此推断结论不存在。；未命中 c-cache-null |
| hr-synonym-008 | synonym_rewrite | 日志在哪看 | 观测未达 | c-log-path→未命中 | 期望命中（found），实际 no_match；候选 0 条，说明：该仓库范围内没有匹配的当前仍有效的结论。请使用实时文件、Git 与测试命令核实，不要据此推断结论不存在。；未命中 c-log-path |
| hr-version-003 | version_change | field_validator | 通过 | c-pydantic-v2→1 | 依赖已变：结论本身仍可能成立，但必须带需复核标记，不能被当成当前事实直接用 |
| hr-version-004 | version_change | Node 版本 | 通过 | c-node-engine→1 | 留出集：前端工具链版本要求，观察依赖变化是否同样被标出来 |
| hr-version-005 | version_change | numpy 复制 | 通过 | c-numpy-copy→1 | 依赖已变：查询词与正文连续，确保这条真的走到适用性判定而不是停在召回 |
| hr-version-006 | version_change | pg_trgm | 通过 | c-pg-trgm→1 | 留出集：本项目真实依赖（模糊匹配本身依赖它），依赖变化后的复核行为需要观察 |
| hr-synonym-009 | synonym_rewrite | pytest 怎么调 | 观测未达 | c-pytest-invoke→未命中 | 期望命中（found），实际 no_match；候选 0 条，说明：该仓库范围内没有匹配的当前仍有效的结论。请使用实时文件、Git 与测试命令核实，不要据此推断结论不存在。；未命中 c-pytest-invoke |
| hr-synonym-010 | synonym_rewrite | 覆盖率报告在哪 | 观测未达 | c-coverage-report→未命中 | 期望命中（found），实际 no_match；候选 0 条，说明：该仓库范围内没有匹配的当前仍有效的结论。请使用实时文件、Git 与测试命令核实，不要据此推断结论不存在。；未命中 c-coverage-report |

## 五、未通过详情

### hr-synonym-001（synonym_rewrite）

- 查询：`跑单测的命令是啥`  模式：`current`  期望状态：`found`  实际状态：`no_match`
- 样本意图：只观测不判失败：查询与结论几无字面重叠，用于测量词面召回的上限，作为是否需要向量的证据
- 未满足：期望命中（found），实际 no_match；候选 0 条，说明：该仓库范围内没有匹配的当前仍有效的结论。请使用实时文件、Git 与测试命令核实，不要据此推断结论不存在。
- 未满足：未命中 c-synonym-entry
- 实际返回：无

### hr-version-001（version_change）

- 查询：`MySQL 认证依赖哪个库`  模式：`current`  期望状态：`found`  实际状态：`no_match`
- 样本意图：结论仍可能有效但依赖已变：必须标为需复核，且不允许在未复核时被直接当成当前事实采用
- 未满足：期望命中（found），实际 no_match；候选 0 条，说明：该仓库范围内没有匹配的当前仍有效的结论。请使用实时文件、Git 与测试命令核实，不要据此推断结论不存在。
- 未满足：未命中 c-deps-cryptography
- 未满足：首位期望 c-deps-cryptography，实际为 无结果
- 未满足：c-deps-cryptography 未出现在结果里，无法核对其适用性
- 实际返回：无
- 为构造依赖变更而故意留下的未提交改动：`pyproject.toml`

### hr-synonym-002（synonym_rewrite）

- 查询：`怎么跑测试`  模式：`current`  期望状态：`found`  实际状态：`no_match`
- 样本意图：只观测不判失败：查询口语化，与正文几乎没有字面重叠，用于记录词面召回的上限
- 未满足：期望命中（found），实际 no_match；候选 0 条，说明：该仓库范围内没有匹配的当前仍有效的结论。请使用实时文件、Git 与测试命令核实，不要据此推断结论不存在。
- 未满足：未命中 c-run-tests
- 实际返回：无

### hr-synonym-003（synonym_rewrite）

- 查询：`代码风格检查用啥`  模式：`current`  期望状态：`found`  实际状态：`no_match`
- 样本意图：留出集：口语提问与工具名的对应关系，不参与调参
- 未满足：期望命中（found），实际 no_match；候选 0 条，说明：该仓库范围内没有匹配的当前仍有效的结论。请使用实时文件、Git 与测试命令核实，不要据此推断结论不存在。
- 未满足：未命中 c-lint-tool
- 实际返回：无

### hr-synonym-004（synonym_rewrite）

- 查询：`数据库连接怎么配`  模式：`current`  期望状态：`found`  实际状态：`no_match`
- 样本意图：只观测：问的是做法，正文是配置项名，两者没有共同词面
- 未满足：期望命中（found），实际 no_match；候选 0 条，说明：该仓库范围内没有匹配的当前仍有效的结论。请使用实时文件、Git 与测试命令核实，不要据此推断结论不存在。
- 未满足：未命中 c-db-url
- 实际返回：无

### hr-synonym-005（synonym_rewrite）

- 查询：`发版流程`  模式：`current`  期望状态：`found`  实际状态：`no_match`
- 样本意图：留出集：发版是高频问题，但问法千变万化，是判断要不要上向量的关键证据
- 未满足：期望命中（found），实际 no_match；候选 0 条，说明：该仓库范围内没有匹配的当前仍有效的结论。请使用实时文件、Git 与测试命令核实，不要据此推断结论不存在。
- 未满足：未命中 c-release-flow
- 实际返回：无

### hr-synonym-006（synonym_rewrite）

- 查询：`回滚咋弄`  模式：`current`  期望状态：`found`  实际状态：`no_match`
- 样本意图：只观测：口语化提问，且正文明确写了不支持的做法，命中价值高
- 未满足：期望命中（found），实际 no_match；候选 0 条，说明：该仓库范围内没有匹配的当前仍有效的结论。请使用实时文件、Git 与测试命令核实，不要据此推断结论不存在。
- 未满足：未命中 c-rollback
- 实际返回：无

### hr-synonym-007（synonym_rewrite）

- 查询：`缓存穿透怎么防`  模式：`current`  期望状态：`found`  实际状态：`no_match`
- 样本意图：留出集：术语提问与做法描述之间没有共同词面
- 未满足：期望命中（found），实际 no_match；候选 0 条，说明：该仓库范围内没有匹配的当前仍有效的结论。请使用实时文件、Git 与测试命令核实，不要据此推断结论不存在。
- 未满足：未命中 c-cache-null
- 实际返回：无

### hr-synonym-008（synonym_rewrite）

- 查询：`日志在哪看`  模式：`current`  期望状态：`found`  实际状态：`no_match`
- 样本意图：只观测：措辞差异最大的一类问法，用来给出词面检索的下界
- 未满足：期望命中（found），实际 no_match；候选 0 条，说明：该仓库范围内没有匹配的当前仍有效的结论。请使用实时文件、Git 与测试命令核实，不要据此推断结论不存在。
- 未满足：未命中 c-log-path
- 实际返回：无

### hr-synonym-009（synonym_rewrite）

- 查询：`pytest 怎么调`  模式：`current`  期望状态：`found`  实际状态：`no_match`
- 样本意图：只观测：与 hr-synonym-002 问同一件事，但共享了实词 pytest —— 用来定位词面召回的边界在哪一侧
- 未满足：期望命中（found），实际 no_match；候选 0 条，说明：该仓库范围内没有匹配的当前仍有效的结论。请使用实时文件、Git 与测试命令核实，不要据此推断结论不存在。
- 未满足：未命中 c-pytest-invoke
- 实际返回：无

### hr-synonym-010（synonym_rewrite）

- 查询：`覆盖率报告在哪`  模式：`current`  期望状态：`found`  实际状态：`no_match`
- 样本意图：留出集：共享复合词「覆盖率报告」，与只共享单个实词的样本一起给出词面召回的可用区间
- 未满足：期望命中（found），实际 no_match；候选 0 条，说明：该仓库范围内没有匹配的当前仍有效的结论。请使用实时文件、Git 与测试命令核实，不要据此推断结论不存在。
- 未满足：未命中 c-coverage-report
- 实际返回：无

## 六、语料构造分歧

无。样本声明的证据状态全部按声明构造出来了。

## 七、硬约束违规

无。越界召回与伪造验证均为 0。

## 八、复现方式

```bash
.venv/Scripts/python.exe -m eval.history_recall --samples eval\history_recall\samples.jsonl --label quota-2
```

语料由样本自带声明经生产写入路径构造，检索走 `RepoKnowledgeService`，因此重跑结果可比。逐条判定规则见 `eval/history_recall/judge.py`。
