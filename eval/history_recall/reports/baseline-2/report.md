# 历史召回评估报告

- 运行标签：`baseline-2`
- 问题集：`eval\history_recall\samples.jsonl`
- 数据库 schema：`xihe_knowledge_eval_baseline_2_92ed4e`（一次性，运行结束后删除）
- 样本数：执行 9 / 共 13（构造或执行出错 4）
- 开关：检索 `True`、结论写入 `True`、允许列表 `未限制`

## 一、结论摘要

- 通过 8 条，未通过 5 条
- 硬约束违规：0 条（无）
- Recall@k：100.0%（门槛样本 5 条，5/5 个必中语料）
- 只观测类样本：0/1 条满足期望（这类样本不判失败，用于测量能力边界）
- 尚未校准的样本：13 条（未校准的期望值不作为质量门槛）
- **未测量的指标**：
  - 引用可打开率（目标 ≥95%）：需要完整 Agent 执行并观察其答案引用，本次只跑了检索层
  - 重复工具调用数中位数下降（目标 ≥20%）：需要「无召回 / 有召回」两组 Agent 运行
  - 纯数据库检索 p95（目标 ≤300ms）：上报的是含本地适用性检查的端到端耗时，两者未分开测量

## 二、验收门槛

| 门槛 | 目标 | 实测 | 判定 | 说明 |
| --- | --- | --- | --- | --- |
| 越界召回与伪造验证 | 0 条 | 0 条 | 达标 | 不设容忍区间，也不因样本未校准而豁免：出现即缺陷 |
| Recall@k | ≥ 85% | 100.0%（5/5，门槛样本 5 条） | 不适用（未测量） | 门槛样本只有 5 条，少于 20 条，样本不足时比例与分位数都在追噪声，本次仅观测 |
| 无匹配时回落真实工具 | 全部按要求提示 | 1/1 | 达标 | 只核对了提示语；模型是否真的改用实时工具属 Agent 层，未测量 |
| 检索 p95 时延（含适用性检查） | ≤ 2000 ms | p95 16 ms / p50 0 ms（9 条） | 不适用（未测量） | 样本只有 9 条，不足以取分位数，本次仅观测 |
| 引用可打开率 | ≥ 95% | 未测量 | 不适用（未测量） | 需要完整 Agent 执行，本次只跑了检索层 |
| 重复工具调用数中位数下降 | ≥ 20% | 未测量 | 不适用（未测量） | 需要「无召回 / 有召回」两组 Agent 运行，本次只跑了检索层 |

## 三、按类别统计

| 类别 | 样本 | 已执行 | 通过 | 门槛样本 | Recall@k |
| --- | --- | --- | --- | --- | --- |
| cross_repo | 1 | 1 | 1 | 1 | 1/1 |
| deleted_thread | 1 | 1 | 1 | 0 | — |
| edited_branch | 2 | 2 | 2 | 0 | — |
| historical_decision | 2 | 1 | 1 | 1 | 1/1 |
| incident_fix | 2 | 0 | 0 | 0 | — |
| no_answer | 1 | 1 | 1 | 1 | — |
| stale_knowledge | 2 | 2 | 2 | 2 | 3/3 |
| synonym_rewrite | 1 | 1 | 0 | 0 | — |
| version_change | 1 | 0 | 0 | 0 | — |

按类别分开统计是计划第 15 节的硬要求：P1 身份修复、P2 记忆修复都是独立收益，不分层会把它们记到召回功能头上。

## 四、逐条结果

| 样本 | 类别 | 查询 | 结果 | 必中排名 | 说明 |
| --- | --- | --- | --- | --- | --- |
| hr-decision-001 | historical_decision | checkpoint 存储 | 通过 | c-checkpoint-redis→1 | 历史决策类基线：已接受、单一证据的决策应被召回并排在首位 |
| hr-decision-002 | historical_decision | import-linter | 出错 | — | 语料构造失败：Git 命令失败（init -q -c init.defaultBranch=main）：error: unknown switch `c' usage: git init [-q \| --quiet] [--bare] [--template=<template-directory>] [--separate-git-dir <git-dir>] [--object-format=<format>] [--ref-format=<format>] [-b <branch-name> \| --initial-branch=<branch-name>] [--shared[=<permissions>]] [<directory>] --[no-]template <template-directory> directory from which templates will be used --[no-]bare create a bare repository --shared[=<permissions>] specify that the git repository is to be shared amongst several users -q, --[no-]quiet be quiet --[no-]separate-git-dir <gitdir> separate git dir from working tree -b, --[no-]initial-branch <name> override the name of the initial branch --[no-]object-format <hash> specify the hash algorithm to use --[no-]ref-format <format> specify the reference format to use |
| hr-incident-001 | incident_fix | pytest 收集失败 | 出错 | — | 语料构造失败：Git 命令失败（init -q -c init.defaultBranch=main）：error: unknown switch `c' usage: git init [-q \| --quiet] [--bare] [--template=<template-directory>] [--separate-git-dir <git-dir>] [--object-format=<format>] [--ref-format=<format>] [-b <branch-name> \| --initial-branch=<branch-name>] [--shared[=<permissions>]] [<directory>] --[no-]template <template-directory> directory from which templates will be used --[no-]bare create a bare repository --shared[=<permissions>] specify that the git repository is to be shared amongst several users -q, --[no-]quiet be quiet --[no-]separate-git-dir <gitdir> separate git dir from working tree -b, --[no-]initial-branch <name> override the name of the initial branch --[no-]object-format <hash> specify the hash algorithm to use --[no-]ref-format <format> specify the reference format to use |
| hr-incident-002 | incident_fix | authentication plugin | 出错 | — | 语料构造失败：Git 命令失败（init -q -c init.defaultBranch=main）：error: unknown switch `c' usage: git init [-q \| --quiet] [--bare] [--template=<template-directory>] [--separate-git-dir <git-dir>] [--object-format=<format>] [--ref-format=<format>] [-b <branch-name> \| --initial-branch=<branch-name>] [--shared[=<permissions>]] [<directory>] --[no-]template <template-directory> directory from which templates will be used --[no-]bare create a bare repository --shared[=<permissions>] specify that the git repository is to be shared amongst several users -q, --[no-]quiet be quiet --[no-]separate-git-dir <gitdir> separate git dir from working tree -b, --[no-]initial-branch <name> override the name of the initial branch --[no-]object-format <hash> specify the hash algorithm to use --[no-]ref-format <format> specify the reference format to use |
| hr-noanswer-001 | no_answer | Kubernetes 部署清单 | 通过 | — | 范围内确实无依据：必须返回 no_match 并提示改用实时工具，不能含糊说成「没有历史」 |
| hr-stale-001 | stale_knowledge | 测试入口 | 通过 | c-entry-new→1 | 过期知识不进当前模式：被替代的旧结论必须被排除，不能因为时间较新就静默覆盖 |
| hr-stale-002 | stale_knowledge | 测试入口 | 通过 | c-entry-old→1、c-entry-new→2 | history 模式用于追溯「当初为什么改」：旧结论要返回，并带出它被谁替代 |
| hr-crossrepo-001 | cross_repo | 测试入口 | 通过 | c-entry-mine→1 | 跨仓库同名内容：另一仓库的结论即使内容完全一致也不得召回，对应「跨仓库泄漏为 0」的硬约束 |
| hr-edited-001 | edited_branch | 编辑废弃分支 | 仅供参考（语料分歧） | — | 编辑废弃分支：当前模式必须排除，它是「旧分支退出当前召回」的可见行为 |
| hr-edited-002 | edited_branch | 编辑废弃分支 | 仅供参考（语料分歧） | c-edited-branch→1 | 同一语料在 history 模式的对照：历史分支可追溯，但不能反过来泄漏到 current |
| hr-deleted-001 | deleted_thread | 异步队列 | 仅供参考（语料分歧） | — | 删除优先于一切：连 history 模式都不能召回，对应用户删除优先于 append-only 的要求 |
| hr-synonym-001 | synonym_rewrite | 跑单测的命令是啥 | 观测未达 | c-synonym-entry→未命中 | 期望命中（found），实际 no_match；候选 0 条，说明：该仓库范围内没有匹配的当前仍有效的结论。请使用实时文件、Git 与测试命令核实，不要据此推断结论不存在。；未命中 c-synonym-entry |
| hr-version-001 | version_change | MySQL 认证依赖哪个库 | 出错 | — | 语料构造失败：Git 命令失败（init -q -c init.defaultBranch=main）：error: unknown switch `c' usage: git init [-q \| --quiet] [--bare] [--template=<template-directory>] [--separate-git-dir <git-dir>] [--object-format=<format>] [--ref-format=<format>] [-b <branch-name> \| --initial-branch=<branch-name>] [--shared[=<permissions>]] [<directory>] --[no-]template <template-directory> directory from which templates will be used --[no-]bare create a bare repository --shared[=<permissions>] specify that the git repository is to be shared amongst several users -q, --[no-]quiet be quiet --[no-]separate-git-dir <gitdir> separate git dir from working tree -b, --[no-]initial-branch <name> override the name of the initial branch --[no-]object-format <hash> specify the hash algorithm to use --[no-]ref-format <format> specify the reference format to use |

## 五、未通过详情

### hr-decision-002（historical_decision）

- 查询：`import-linter`  模式：`current`  期望状态：`found`  实际状态：`None`
- 样本意图：带依赖路径的决策：适用性应为检查通过态，不能被误标为需复核
- 执行错误：语料构造失败：Git 命令失败（init -q -c init.defaultBranch=main）：error: unknown switch `c'
usage: git init [-q | --quiet] [--bare] [--template=<template-directory>]
                [--separate-git-dir <git-dir>] [--object-format=<format>]
                [--ref-format=<format>]
                [-b <branch-name> | --initial-branch=<branch-name>]
                [--shared[=<permissions>]] [<directory>]

    --[no-]template <template-directory>
                          directory from which templates will be used
    --[no-]bare           create a bare repository
    --shared[=<permissions>]
                          specify that the git repository is to be shared amongst several users
    -q, --[no-]quiet      be quiet
    --[no-]separate-git-dir <gitdir>
                          separate git dir from working tree
    -b, --[no-]initial-branch <name>
                          override the name of the initial branch
    --[no-]object-format <hash>
                          specify the hash algorithm to use
    --[no-]ref-format <format>
                          specify the reference format to use
- 实际返回：无

### hr-incident-001（incident_fix）

- 查询：`pytest 收集失败`  模式：`current`  期望状态：`found`  实际状态：`None`
- 样本意图：旧故障修复：先失败后成功的两条证据都要能读到，且失败证据的退出码保持 1 不被改写
- 执行错误：语料构造失败：Git 命令失败（init -q -c init.defaultBranch=main）：error: unknown switch `c'
usage: git init [-q | --quiet] [--bare] [--template=<template-directory>]
                [--separate-git-dir <git-dir>] [--object-format=<format>]
                [--ref-format=<format>]
                [-b <branch-name> | --initial-branch=<branch-name>]
                [--shared[=<permissions>]] [<directory>]

    --[no-]template <template-directory>
                          directory from which templates will be used
    --[no-]bare           create a bare repository
    --shared[=<permissions>]
                          specify that the git repository is to be shared amongst several users
    -q, --[no-]quiet      be quiet
    --[no-]separate-git-dir <gitdir>
                          separate git dir from working tree
    -b, --[no-]initial-branch <name>
                          override the name of the initial branch
    --[no-]object-format <hash>
                          specify the hash algorithm to use
    --[no-]ref-format <format>
                          specify the reference format to use
- 实际返回：无

### hr-incident-002（incident_fix）

- 查询：`authentication plugin`  模式：`current`  期望状态：`found`  实际状态：`None`
- 样本意图：证据只到 observed：pip show 能证明装了包，不能单独证明它解决了认证报错，不应被升级成 supported
- 执行错误：语料构造失败：Git 命令失败（init -q -c init.defaultBranch=main）：error: unknown switch `c'
usage: git init [-q | --quiet] [--bare] [--template=<template-directory>]
                [--separate-git-dir <git-dir>] [--object-format=<format>]
                [--ref-format=<format>]
                [-b <branch-name> | --initial-branch=<branch-name>]
                [--shared[=<permissions>]] [<directory>]

    --[no-]template <template-directory>
                          directory from which templates will be used
    --[no-]bare           create a bare repository
    --shared[=<permissions>]
                          specify that the git repository is to be shared amongst several users
    -q, --[no-]quiet      be quiet
    --[no-]separate-git-dir <gitdir>
                          separate git dir from working tree
    -b, --[no-]initial-branch <name>
                          override the name of the initial branch
    --[no-]object-format <hash>
                          specify the hash algorithm to use
    --[no-]ref-format <format>
                          specify the reference format to use
- 实际返回：无

### hr-synonym-001（synonym_rewrite）

- 查询：`跑单测的命令是啥`  模式：`current`  期望状态：`found`  实际状态：`no_match`
- 样本意图：只观测不判失败：查询与结论几无字面重叠，用于测量词面召回的上限，作为是否需要向量的证据
- 未满足：期望命中（found），实际 no_match；候选 0 条，说明：该仓库范围内没有匹配的当前仍有效的结论。请使用实时文件、Git 与测试命令核实，不要据此推断结论不存在。
- 未满足：未命中 c-synonym-entry
- 实际返回：无

### hr-version-001（version_change）

- 查询：`MySQL 认证依赖哪个库`  模式：`current`  期望状态：`found`  实际状态：`None`
- 样本意图：结论仍可能有效但依赖已变：必须标为需复核，且不允许在未复核时被直接当成当前事实采用
- 执行错误：语料构造失败：Git 命令失败（init -q -c init.defaultBranch=main）：error: unknown switch `c'
usage: git init [-q | --quiet] [--bare] [--template=<template-directory>]
                [--separate-git-dir <git-dir>] [--object-format=<format>]
                [--ref-format=<format>]
                [-b <branch-name> | --initial-branch=<branch-name>]
                [--shared[=<permissions>]] [<directory>]

    --[no-]template <template-directory>
                          directory from which templates will be used
    --[no-]bare           create a bare repository
    --shared[=<permissions>]
                          specify that the git repository is to be shared amongst several users
    -q, --[no-]quiet      be quiet
    --[no-]separate-git-dir <gitdir>
                          separate git dir from working tree
    -b, --[no-]initial-branch <name>
                          override the name of the initial branch
    --[no-]object-format <hash>
                          specify the hash algorithm to use
    --[no-]ref-format <format>
                          specify the reference format to use
- 实际返回：无

## 六、语料构造分歧

这些样本的**语料声明**与构造结果对不上：要么期望写错了，要么当前实现做不到那个状态。它们不计入任何门槛 —— 连前提都没构造出来，结论没有意义。

- hr-edited-001：语料 c-edited-branch 声明证据状态 unverified，构造后实际为 observed
- hr-edited-002：语料 c-edited-branch 声明证据状态 unverified，构造后实际为 observed
- hr-deleted-001：语料 c-deleted-thread 声明证据状态 unverified，构造后实际为 observed

## 七、硬约束违规

无。越界召回与伪造验证均为 0。

## 八、复现方式

```bash
.venv/Scripts/python.exe -m eval.history_recall --samples eval\history_recall\samples.jsonl --label baseline-2
```

语料由样本自带声明经生产写入路径构造，检索走 `RepoKnowledgeService`，因此重跑结果可比。逐条判定规则见 `eval/history_recall/judge.py`。
