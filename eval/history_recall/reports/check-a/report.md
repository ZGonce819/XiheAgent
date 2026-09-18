# 历史召回评估报告

- 运行标签：`check-a`
- 问题集：`eval\history_recall\samples.jsonl`
- 数据库 schema：`xihe_knowledge_eval_check_a_9ecbfa`（一次性，运行结束后删除）
- 样本数：执行 13 / 共 20（构造或执行出错 7）
- 开关：检索 `True`、结论写入 `True`、允许列表 `未限制`

## 一、结论摘要

- 硬门槛样本：通过 13 条，未通过 7 条（共 20 条）
- 硬约束违规：0 条（无）
- Recall@k：100.0%（门槛样本 4 条，4/4 个必中语料）
- 只观测类样本：0/0 条满足期望（这类样本不判失败，用于测量能力边界）
- 尚未校准的样本：16 条（未校准的期望值不作为质量门槛）
- **未测量的指标**：
  - 引用可打开率（目标 ≥95%）：需要完整 Agent 执行并观察其答案引用，本次只跑了检索层
  - 重复工具调用数中位数下降（目标 ≥20%）：需要「无召回 / 有召回」两组 Agent 运行
  - 纯数据库检索 p95（目标 ≤300ms）：上报的是含本地适用性检查的端到端耗时，两者未分开测量

## 二、验收门槛

| 门槛 | 目标 | 实测 | 判定 | 说明 |
| --- | --- | --- | --- | --- |
| 越界召回与伪造验证 | 0 条 | 0 条 | 达标 | 不设容忍区间，也不因样本未校准而豁免：出现即缺陷 |
| Recall@k | ≥ 85% | 100.0%（4/4，门槛样本 4 条） | 不适用（未测量） | 门槛样本只有 4 条，少于 20 条，样本不足时比例与分位数都在追噪声，本次仅观测；另有 16 条未校准样本不计入（hr-decision-003、hr-decision-004、hr-decision-005…）：期望值未确认前它只能观测，不能定标准 |
| 无匹配时回落真实工具 | 全部按要求提示 | 0/0 | 不适用（未测量） | 只核对了提示语；模型是否真的改用实时工具属 Agent 层，未测量 |
| 检索 p95 时延（含适用性检查） | ≤ 2000 ms | p95 1875 ms / p50 15 ms（13 条） | 不适用（未测量） | 样本只有 13 条，不足以取分位数，本次仅观测 |
| 引用可打开率 | ≥ 95% | 未测量 | 不适用（未测量） | 需要完整 Agent 执行，本次只跑了检索层 |
| 重复工具调用数中位数下降 | ≥ 20% | 未测量 | 不适用（未测量） | 需要「无召回 / 有召回」两组 Agent 运行，本次只跑了检索层 |

## 三、按类别统计

| 类别 | 样本 | 已执行 | 通过 | 门槛样本 | Recall@k |
| --- | --- | --- | --- | --- | --- |
| historical_decision | 10 | 10 | 10 | 2 | 2/2 |
| incident_fix | 10 | 3 | 3 | 2 | 2/2 |

按类别分开统计是计划第 15 节的硬要求：P1 身份修复、P2 记忆修复都是独立收益，不分层会把它们记到召回功能头上。

## 四、逐条结果

| 样本 | 类别 | 查询 | 结果 | 必中排名 | 说明 |
| --- | --- | --- | --- | --- | --- |
| hr-decision-001 | historical_decision | checkpoint 存储 | 通过 | c-checkpoint-redis→1 | 历史决策类基线：已接受、单一证据的决策应被召回并排在首位 |
| hr-decision-002 | historical_decision | import-linter | 通过 | c-import-linter→1 | 带依赖路径的决策：适用性应为检查通过态，不能被误标为需复核 |
| hr-incident-001 | incident_fix | pytest 收集失败 | 通过 | c-pytest-cjk→1 | 旧故障修复：先失败后成功的两条证据都要能读到，且失败证据的退出码保持 1 不被改写 |
| hr-incident-002 | incident_fix | authentication plugin | 通过 | c-pymysql-crypto→1 | 证据只到 observed：pip show 能证明装了包，不能单独证明它解决了认证报错，不应被升级成 supported |
| hr-decision-003 | historical_decision | alembic | 通过 | c-migration-alembic→1 | 已接受决策 + 单一成功证据：应当被召回并排在首位，且证据因决策类型封顶在 observed |
| hr-decision-004 | historical_decision | Argon2id | 通过 | c-password-argon2→1 | 安全相关决策：查询词是算法名，考的是术语入口能否直接命中 |
| hr-decision-005 | historical_decision | structlog | 通过 | c-logging-structlog→1 | 日志规范决策：写入链路与查询词同为库名 |
| hr-decision-006 | historical_decision | Zustand | 通过 | c-frontend-state→1 | 技术选型决策：与另一条候选（Redux）同句出现，考的是否被正确召回而非歧义 |
| hr-decision-007 | historical_decision | codeup 流水线 | 通过 | c-ci-codeup→1 | 平台迁移决策：查询用中文术语，考的是中文短语能否命中 |
| hr-decision-008 | historical_decision | 游标分页 | 通过 | c-pagination-cursor→1 | 接口约定决策：同时出现 offset 反例，验证召回的是正例 |
| hr-decision-009 | historical_decision | UTC | 通过 | c-timezone-utc→1 | 带依赖路径的决策：适用性应为检查通过态，与 hr-decision-002 一起覆盖该路径的两条样例 |
| hr-decision-010 | historical_decision | 懒加载 | 通过 | c-orm-explicit-join→1 | 性能约定决策：查询词是概念名，考的是概念词与正文的重叠是否够召回 |
| hr-incident-003 | incident_fix | ImportError collections Mapping | 出错 | — | 语料构造失败：Git 命令失败（-c user.name=eval-harness -c user.email=eval@local -c commit.gpgsign=false commit -q -m 评估语料初始版本）：On branch main Initial commit nothing to commit (create/copy files and use "git add" to track) |
| hr-incident-004 | incident_fix | 413 Request Entity Too Large | 出错 | — | 语料构造失败：Git 命令失败（-c user.name=eval-harness -c user.email=eval@local -c commit.gpgsign=false commit -q -m 评估语料初始版本）：On branch main Initial commit nothing to commit (create/copy files and use "git add" to track) |
| hr-incident-005 | incident_fix | connection pool exhausted | 出错 | — | 语料构造失败：Git 命令失败（-c user.name=eval-harness -c user.email=eval@local -c commit.gpgsign=false commit -q -m 评估语料初始版本）：On branch main Initial commit nothing to commit (create/copy files and use "git add" to track) |
| hr-incident-006 | incident_fix | No space left on device | 出错 | — | 语料构造失败：Git 命令失败（-c user.name=eval-harness -c user.email=eval@local -c commit.gpgsign=false commit -q -m 评估语料初始版本）：On branch main Initial commit nothing to commit (create/copy files and use "git add" to track) |
| hr-incident-007 | incident_fix | npm ERESOLVE | 出错 | — | 语料构造失败：Git 命令失败（-c user.name=eval-harness -c user.email=eval@local -c commit.gpgsign=false commit -q -m 评估语料初始版本）：On branch main Initial commit nothing to commit (create/copy files and use "git add" to track) |
| hr-incident-008 | incident_fix | UnicodeDecodeError gbk | 出错 | — | 语料构造失败：Git 命令失败（-c user.name=eval-harness -c user.email=eval@local -c commit.gpgsign=false commit -q -m 评估语料初始版本）：On branch main Initial commit nothing to commit (create/copy files and use "git add" to track) |
| hr-incident-009 | incident_fix | EADDRINUSE | 出错 | — | 语料构造失败：Git 命令失败（-c user.name=eval-harness -c user.email=eval@local -c commit.gpgsign=false commit -q -m 评估语料初始版本）：On branch main Initial commit nothing to commit (create/copy files and use "git add" to track) |
| hr-incident-010 | incident_fix | certificate verify failed | 通过 | c-tls-expired-cert→1 | 证据只到 observed：curl 的报错输出能说明现象，但退出码未知时不足以支撑断言 |

## 五、未通过详情

### hr-incident-003（incident_fix）

- 查询：`ImportError collections Mapping`  模式：`current`  期望状态：`found`  实际状态：`None`
- 样本意图：先失败后成功两条证据：失败证据的退出码必须保持 1，不能被改写成成功
- 执行错误：语料构造失败：Git 命令失败（-c user.name=eval-harness -c user.email=eval@local -c commit.gpgsign=false commit -q -m 评估语料初始版本）：On branch main

Initial commit

nothing to commit (create/copy files and use "git add" to track)
- 实际返回：无

### hr-incident-004（incident_fix）

- 查询：`413 Request Entity Too Large`  模式：`current`  期望状态：`found`  实际状态：`None`
- 样本意图：部署层配置问题：症状是 HTTP 状态码，考的是错误信息原文能否召回
- 执行错误：语料构造失败：Git 命令失败（-c user.name=eval-harness -c user.email=eval@local -c commit.gpgsign=false commit -q -m 评估语料初始版本）：On branch main

Initial commit

nothing to commit (create/copy files and use "git add" to track)
- 实际返回：无

### hr-incident-005（incident_fix）

- 查询：`connection pool exhausted`  模式：`current`  期望状态：`found`  实际状态：`None`
- 样本意图：资源泄漏类故障：症状与根因不在同一层，考的是能否直接命中根因结论
- 执行错误：语料构造失败：Git 命令失败（-c user.name=eval-harness -c user.email=eval@local -c commit.gpgsign=false commit -q -m 评估语料初始版本）：On branch main

Initial commit

nothing to commit (create/copy files and use "git add" to track)
- 实际返回：无

### hr-incident-006（incident_fix）

- 查询：`No space left on device`  模式：`current`  期望状态：`found`  实际状态：`None`
- 样本意图：运维类故障：查的是错误原文，命中后应避免再全盘搜索日志配置
- 执行错误：语料构造失败：Git 命令失败（-c user.name=eval-harness -c user.email=eval@local -c commit.gpgsign=false commit -q -m 评估语料初始版本）：On branch main

Initial commit

nothing to commit (create/copy files and use "git add" to track)
- 实际返回：无

### hr-incident-007（incident_fix）

- 查询：`npm ERESOLVE`  模式：`current`  期望状态：`found`  实际状态：`None`
- 样本意图：前端依赖故障：结论里带明确的反模式（--force），命中后不应再尝试强行安装
- 执行错误：语料构造失败：Git 命令失败（-c user.name=eval-harness -c user.email=eval@local -c commit.gpgsign=false commit -q -m 评估语料初始版本）：On branch main

Initial commit

nothing to commit (create/copy files and use "git add" to track)
- 实际返回：无

### hr-incident-008（incident_fix）

- 查询：`UnicodeDecodeError gbk`  模式：`current`  期望状态：`found`  实际状态：`None`
- 样本意图：本地环境故障：Windows 特有的默认编码问题，是典型重复踩坑场景
- 执行错误：语料构造失败：Git 命令失败（-c user.name=eval-harness -c user.email=eval@local -c commit.gpgsign=false commit -q -m 评估语料初始版本）：On branch main

Initial commit

nothing to commit (create/copy files and use "git add" to track)
- 实际返回：无

### hr-incident-009（incident_fix）

- 查询：`EADDRINUSE`  模式：`current`  期望状态：`found`  实际状态：`None`
- 样本意图：启动类故障：结论明确反对绕过式修复，命中后应少走一次试错
- 执行错误：语料构造失败：Git 命令失败（-c user.name=eval-harness -c user.email=eval@local -c commit.gpgsign=false commit -q -m 评估语料初始版本）：On branch main

Initial commit

nothing to commit (create/copy files and use "git add" to track)
- 实际返回：无

## 六、语料构造分歧

无。样本声明的证据状态全部按声明构造出来了。

## 七、硬约束违规

无。越界召回与伪造验证均为 0。

## 八、复现方式

```bash
.venv/Scripts/python.exe -m eval.history_recall --samples eval\history_recall\samples.jsonl --label check-a
```

语料由样本自带声明经生产写入路径构造，检索走 `RepoKnowledgeService`，因此重跑结果可比。逐条判定规则见 `eval/history_recall/judge.py`。
