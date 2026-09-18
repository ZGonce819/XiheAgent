# 技能检索开关评估

回答一个问题：**打开技能检索，到底省了多少、代价是什么？**

## 怎么跑

```bash
.venv/Scripts/python.exe -m eval.skill_retrieval --label baseline
```

产出在 `reports/<标签>/`：`report.json`（完整数据）+ `report.md`（人读的结论）。

常用参数：

| 参数 | 说明 |
|---|---|
| `--label` | 报告目录名，省略时用时间戳 |
| `--top-k` | 主对比里「开启」模式最多列出多少条技能，默认 20 |
| `--reports-dir` | 报告根目录，默认 `eval/skill_retrieval/reports` |
| `--negative-control` | 把检索打分打坏再跑一遍，验证评估本身能发现退化 |

退出码：`0` 全过 / `1` 硬约束失败（缺陷）/ `2` 效果门槛未达标 / `3` 负向对照失效（评估没测量能力）。

## 它测什么

技能目录 **150 条**（真实落盘的 `SKILL.md`，由 DeepAgents 的加载器解析，不是手工拼的字典），
查询集 **25 条**（20 条门槛样本 + 5 条未校准样本）。走的是生产同一条链路：

```
写盘的 SKILL.md
  → LocalShellBackend（本地文件夹模式，挂 /skills-global、/skills-set、/skills）
  → DeepAgents 的 before_agent 真实加载器 → skills_metadata
  → agent.middleware.skill_retrieval.SkillsMiddleware.modify_request
  → 解析渲染后的 system message，量长度、数 token、抠出实际列出的技能名
```

量的是**模型真正收到的那条提示词**。

## 它不测什么

**不运行真实模型**，因此**不能**回答「模型是否真的选对了技能并完成任务」。
它测的是提示词的形状，这恰好是检索开关唯一影响的表面。

另外：技能目录是生成的，不是开发机上的真实技能库；真实库的描述质量参差，
命中率可能更低。基础提示词取自 `prompt.get_system_prompt('coding')`，所以
整条提示词的绝对长度会随提示词本身变化（技能段的量与两种模式的差值不受影响）。

## 门槛

| 门槛 | 类型 | 目标 |
|---|---|---|
| 关闭模式列出全部技能 | 硬约束 | 每次运行列出 150 条 |
| 常驻技能始终在位 | 硬约束 | 两种模式下 3 条常驻技能每次都出现 |
| 开启模式命中率 | 效果 | 门槛样本命中率 ≥ 90% |
| 开启模式省下的技能段长度 | 效果 | 技能段字符数降幅 ≥ 60% |
| 极小名额下仍能命中 | 效果 | `top_k=5` 时命中率 ≥ 80% |

硬约束是**逻辑上必然成立**的，不成立就是 bug。效果门槛在写代码时就定好了，
不随结果调整。降幅门槛的分母是**技能段**而不是整条提示词 —— 理由见
`judge.py` 的模块文档（第一版口径写错过）。

## 负向对照

```bash
.venv/Scripts/python.exe -m eval.skill_retrieval --negative-control --label negative-control
```

把 `select_skills` 换成「完全不看查询、只按目录顺序取前 N 条」，期望命中率门槛
**失败**。如果它还能通过，说明这套评估恒过、没有测量能力，必须修。

一个恒过的评估比没有评估更糟。

## 文件

| 文件 | 职责 |
|---|---|
| `catalog.py` | 技能目录与查询集（全部确定性，无随机数） |
| `runner.py` | 跑一次评估：造环境、走真实链路、收集观测 |
| `judge.py` | 指标口径与门槛判定 |
| `report.py` | 渲染 `report.json` / `report.md` |
| `negative_control.py` | 打坏检索，验证评估有测量能力 |
| `__main__.py` | 命令行入口 |
