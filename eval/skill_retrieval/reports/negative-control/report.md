# 技能检索开关评估报告

- 运行标签：`negative-control`
- 技能目录：150 条（真实落盘的 `SKILL.md`，由 DeepAgents 加载器解析）
- 查询集：25 条（门槛样本 20 条，未校准 5 条）
- 技能源：`/skills-global/` / `/skills-set/` / `/skills/`
- backend：`LocalShellBackend(local_folder_mode=True)`
- `top_k`：20
- token 编码：`cl100k_base`

## 一、结论摘要

硬约束全部通过；有 2 条效果门槛未达到。

- 技能段长度：`off` 20334 字符 → `on` 4391 字符，**降幅 78.4%**
- 整条提示词 token：`off` 15050 → `on` 8815，降幅 41.4%（基础提示词本身占大头，开关管不到那部分）
- 列出技能数：`off` 150.0 条 → `on` 20.0 条
- `on` 模式命中率：20.0%（4/20）
- 列出的技能里无关项占比：`off` 97.3% → `on` 84.0%（受 `top_k` 影响，仅供参考）
- 常驻技能在位率：`off` 100.0%、`on` 100.0%

## 二、验收门槛

| 门槛 | 类型 | 目标 | 实测 | 判定 |
|---|---|---|---|---|
| 关闭模式列出全部技能 | 硬约束 | 每次运行列出 150 条 | 150.0 条（最少 150，最多 150） | 通过 |
| 常驻技能始终在位 | 硬约束 | 两种模式下 3 条常驻技能每次都出现 | 关闭 100%、开启 100% | 通过 |
| 开启模式命中率 | 效果 | 门槛样本命中率 ≥ 90% | 20.0%（4/20） | 未通过 |
| 开启模式省下的技能段长度 | 效果 | 技能段字符数降幅 ≥ 60% | 78.4% | 通过 |
| 极小名额下仍能命中（top_k=5） | 效果 | 命中率 ≥ 80% | 10.0%（2/20） | 未通过 |

## 三、两种模式对比

| 指标 | `off`（默认） | `on` | 变化 |
|---|---|---|---|
| 技能段字符数 | 20334 | 4391 | -78.4% |
| 平均列出技能数 | 150.0 | 20.0 | -130.0 |
| 命中率 | 100.0% | 20.0% | +80.0pp |
| 无关项占比 | 97.3% | 84.0% | -13.3pp |
| 整条 system message 字符数 | 31484 | 15541 | -50.6% |
| 整条 system message token 数 | 15050 | 8815 | -41.4% |

说明：

- `off` 模式下命中率必然是 100%（全部列出），这一列只用于确认基线正确；真正的测点是 `on` 模式的命中率。
- 最后两行是**用户实际感知到的成本**，但它同时取决于基础提示词有多大（本次基础提示词占了大头），开关管不到那部分，因此不设门槛。
- 「无关项占比」受 `top_k` 影响，同样不设门槛。

## 四、命中率对 `top_k` 的敏感度

把 `top_k` 逐步收紧，看命中率掉得多快。掉得慢说明相关技能稳稳排在前面；掉得快说明它只是勉强挤进名额。

| `top_k` | 平均列出 | 命中率 | 技能段字符数 | 常驻在位 |
|---|---|---|---|---|
| 5 | 5.0 | 10.0%（2/20） | 2561 | 100.0% |
| 10 | 10.0 | 15.0%（3/20） | 3180 | 100.0% |
| 20 | 20.0 | 20.0%（4/20） | 4391 | 100.0% |
| 40 | 40.0 | 50.0%（10/20） | 6880 | 100.0% |

## 五、逐条明细（门槛样本）

| 查询 | 期望技能 | `off` 列出 | `on` 列出 | 命中 |
|---|---|---|---|---|
| q01 | `db-migration-check` | 150 | 20 | **否** |
| q02 | `db-rollback-drill` | 150 | 20 | **否** |
| q03 | `frontend-theme-token` | 150 | 20 | **否** |
| q04 | `frontend-a11y-audit` | 150 | 20 | **否** |
| q05 | `api-timeout-tuning` | 150 | 20 | 是 |
| q06 | `dns-failover-plan` | 150 | 20 | **否** |
| q07 | `oauth-token-rotation` | 150 | 20 | **否** |
| q08 | `rbac-permission-matrix` | 150 | 20 | **否** |
| q09 | `log-trace-correlation` | 150 | 20 | **否** |
| q10 | `audit-log-retention` | 150 | 20 | 是 |
| q11 | `flaky-test-triage` | 150 | 20 | **否** |
| q12 | `coverage-gate-setup` | 150 | 20 | **否** |
| q13 | `deploy-rollback-runbook` | 150 | 20 | **否** |
| q14 | `container-image-slimming` | 150 | 20 | **否** |
| q15 | `changelog-automation` | 150 | 20 | **否** |
| q16 | `adr-writing-guide` | 150 | 20 | 是 |
| q17 | `cache-stampede-guard` | 150 | 20 | 是 |
| q18 | `n-plus-one-query-fix` | 150 | 20 | **否** |
| q19 | `secret-scanning-setup` | 150 | 20 | **否** |
| q20 | `dependency-vuln-triage` | 150 | 20 | **否** |

### 未命中的查询

- **q01**：这次数据库迁移能不能回滚？帮我核对一下 schema 变更和回滚脚本
  - 期望 `db-migration-check`
  - 实际列出：`ai-coding-implementation`, `code-review`, `repo-bootstrap-analysis`, `adr-writing-guide`, `api-timeout-tuning`, `audit-log-retention`, `auth-password`, `auth-password-automation`, `auth-permission`, `auth-permission-automation`, `auth-permission-checklist`, `auth-session`, `auth-session-automation`, `auth-session-checklist`, `auth-sso`, `auth-sso-automation`, `auth-token`, `auth-token-automation`, `auth-token-checklist`, `cache-stampede-guard`
- **q02**：想在预发环境做一次数据库回滚演练，验证步骤和耗时
  - 期望 `db-rollback-drill`
  - 实际列出：`ai-coding-implementation`, `code-review`, `repo-bootstrap-analysis`, `adr-writing-guide`, `api-timeout-tuning`, `audit-log-retention`, `auth-password`, `auth-password-automation`, `auth-permission`, `auth-permission-automation`, `auth-permission-checklist`, `auth-session`, `auth-session-automation`, `auth-session-checklist`, `auth-sso`, `auth-sso-automation`, `auth-token`, `auth-token-automation`, `auth-token-checklist`, `cache-stampede-guard`
- **q03**：前端主题的配色和间距变量有点乱，帮我统一一下
  - 期望 `frontend-theme-token`
  - 实际列出：`ai-coding-implementation`, `code-review`, `repo-bootstrap-analysis`, `adr-writing-guide`, `api-timeout-tuning`, `audit-log-retention`, `auth-password`, `auth-password-automation`, `auth-permission`, `auth-permission-automation`, `auth-permission-checklist`, `auth-session`, `auth-session-automation`, `auth-session-checklist`, `auth-sso`, `auth-sso-automation`, `auth-token`, `auth-token-automation`, `auth-token-checklist`, `cache-stampede-guard`
- **q04**：帮我审查前端的无障碍，重点看对比度和键盘可达性
  - 期望 `frontend-a11y-audit`
  - 实际列出：`ai-coding-implementation`, `code-review`, `repo-bootstrap-analysis`, `adr-writing-guide`, `api-timeout-tuning`, `audit-log-retention`, `auth-password`, `auth-password-automation`, `auth-permission`, `auth-permission-automation`, `auth-permission-checklist`, `auth-session`, `auth-session-automation`, `auth-session-checklist`, `auth-sso`, `auth-sso-automation`, `auth-token`, `auth-token-automation`, `auth-token-checklist`, `cache-stampede-guard`
- **q06**：域名解析要做多线路故障转移，健康检查怎么配
  - 期望 `dns-failover-plan`
  - 实际列出：`ai-coding-implementation`, `code-review`, `repo-bootstrap-analysis`, `adr-writing-guide`, `api-timeout-tuning`, `audit-log-retention`, `auth-password`, `auth-password-automation`, `auth-permission`, `auth-permission-automation`, `auth-permission-checklist`, `auth-session`, `auth-session-automation`, `auth-session-checklist`, `auth-sso`, `auth-sso-automation`, `auth-token`, `auth-token-automation`, `auth-token-checklist`, `cache-stampede-guard`
- **q07**：OAuth 令牌轮换怎么做，刷新和吊销要怎么处理
  - 期望 `oauth-token-rotation`
  - 实际列出：`ai-coding-implementation`, `code-review`, `repo-bootstrap-analysis`, `adr-writing-guide`, `api-timeout-tuning`, `audit-log-retention`, `auth-password`, `auth-password-automation`, `auth-permission`, `auth-permission-automation`, `auth-permission-checklist`, `auth-session`, `auth-session-automation`, `auth-session-checklist`, `auth-sso`, `auth-sso-automation`, `auth-token`, `auth-token-automation`, `auth-token-checklist`, `cache-stampede-guard`
- **q08**：按角色梳理一份权限矩阵，列出每个角色能访问的资源
  - 期望 `rbac-permission-matrix`
  - 实际列出：`ai-coding-implementation`, `code-review`, `repo-bootstrap-analysis`, `adr-writing-guide`, `api-timeout-tuning`, `audit-log-retention`, `auth-password`, `auth-password-automation`, `auth-permission`, `auth-permission-automation`, `auth-permission-checklist`, `auth-session`, `auth-session-automation`, `auth-session-checklist`, `auth-sso`, `auth-sso-automation`, `auth-token`, `auth-token-automation`, `auth-token-checklist`, `cache-stampede-guard`
- **q09**：结构化日志里怎么把 trace id 串起来做链路关联
  - 期望 `log-trace-correlation`
  - 实际列出：`ai-coding-implementation`, `code-review`, `repo-bootstrap-analysis`, `adr-writing-guide`, `api-timeout-tuning`, `audit-log-retention`, `auth-password`, `auth-password-automation`, `auth-permission`, `auth-permission-automation`, `auth-permission-checklist`, `auth-session`, `auth-session-automation`, `auth-session-checklist`, `auth-sso`, `auth-sso-automation`, `auth-token`, `auth-token-automation`, `auth-token-checklist`, `cache-stampede-guard`
- **q11**：有几个用例偶发失败，帮我排查不稳定用例的根因
  - 期望 `flaky-test-triage`
  - 实际列出：`ai-coding-implementation`, `code-review`, `repo-bootstrap-analysis`, `adr-writing-guide`, `api-timeout-tuning`, `audit-log-retention`, `auth-password`, `auth-password-automation`, `auth-permission`, `auth-permission-automation`, `auth-permission-checklist`, `auth-session`, `auth-session-automation`, `auth-session-checklist`, `auth-sso`, `auth-sso-automation`, `auth-token`, `auth-token-automation`, `auth-token-checklist`, `cache-stampede-guard`
- **q12**：想按目录设置覆盖率门槛，阈值怎么配
  - 期望 `coverage-gate-setup`
  - 实际列出：`ai-coding-implementation`, `code-review`, `repo-bootstrap-analysis`, `adr-writing-guide`, `api-timeout-tuning`, `audit-log-retention`, `auth-password`, `auth-password-automation`, `auth-permission`, `auth-permission-automation`, `auth-permission-checklist`, `auth-session`, `auth-session-automation`, `auth-session-checklist`, `auth-sso`, `auth-sso-automation`, `auth-token`, `auth-token-automation`, `auth-token-checklist`, `cache-stampede-guard`
- **q13**：写一份部署回滚手册，说明触发条件和执行步骤
  - 期望 `deploy-rollback-runbook`
  - 实际列出：`ai-coding-implementation`, `code-review`, `repo-bootstrap-analysis`, `adr-writing-guide`, `api-timeout-tuning`, `audit-log-retention`, `auth-password`, `auth-password-automation`, `auth-permission`, `auth-permission-automation`, `auth-permission-checklist`, `auth-session`, `auth-session-automation`, `auth-session-checklist`, `auth-sso`, `auth-sso-automation`, `auth-token`, `auth-token-automation`, `auth-token-checklist`, `cache-stampede-guard`
- **q14**：容器镜像太大了，帮我裁剪基础镜像和构建层
  - 期望 `container-image-slimming`
  - 实际列出：`ai-coding-implementation`, `code-review`, `repo-bootstrap-analysis`, `adr-writing-guide`, `api-timeout-tuning`, `audit-log-retention`, `auth-password`, `auth-password-automation`, `auth-permission`, `auth-permission-automation`, `auth-permission-checklist`, `auth-session`, `auth-session-automation`, `auth-session-checklist`, `auth-sso`, `auth-sso-automation`, `auth-token`, `auth-token-automation`, `auth-token-checklist`, `cache-stampede-guard`
- **q15**：想从提交信息自动生成变更记录
  - 期望 `changelog-automation`
  - 实际列出：`ai-coding-implementation`, `code-review`, `repo-bootstrap-analysis`, `adr-writing-guide`, `api-timeout-tuning`, `audit-log-retention`, `auth-password`, `auth-password-automation`, `auth-permission`, `auth-permission-automation`, `auth-permission-checklist`, `auth-session`, `auth-session-automation`, `auth-session-checklist`, `auth-sso`, `auth-sso-automation`, `auth-token`, `auth-token-automation`, `auth-token-checklist`, `cache-stampede-guard`
- **q18**：接口里 N+1 查询很多，想改成批量加载关联数据
  - 期望 `n-plus-one-query-fix`
  - 实际列出：`ai-coding-implementation`, `code-review`, `repo-bootstrap-analysis`, `adr-writing-guide`, `api-timeout-tuning`, `audit-log-retention`, `auth-password`, `auth-password-automation`, `auth-permission`, `auth-permission-automation`, `auth-permission-checklist`, `auth-session`, `auth-session-automation`, `auth-session-checklist`, `auth-sso`, `auth-sso-automation`, `auth-token`, `auth-token-automation`, `auth-token-checklist`, `cache-stampede-guard`
- **q19**：想接入密钥扫描，阻止凭据被提交进仓库
  - 期望 `secret-scanning-setup`
  - 实际列出：`ai-coding-implementation`, `code-review`, `repo-bootstrap-analysis`, `adr-writing-guide`, `api-timeout-tuning`, `audit-log-retention`, `auth-password`, `auth-password-automation`, `auth-permission`, `auth-permission-automation`, `auth-permission-checklist`, `auth-session`, `auth-session-automation`, `auth-session-checklist`, `auth-sso`, `auth-sso-automation`, `auth-token`, `auth-token-automation`, `auth-token-checklist`, `cache-stampede-guard`
- **q20**：依赖漏洞太多，怎么按可利用性分级排修复顺序
  - 期望 `dependency-vuln-triage`
  - 实际列出：`ai-coding-implementation`, `code-review`, `repo-bootstrap-analysis`, `adr-writing-guide`, `api-timeout-tuning`, `audit-log-retention`, `auth-password`, `auth-password-automation`, `auth-permission`, `auth-permission-automation`, `auth-permission-checklist`, `auth-session`, `auth-session-automation`, `auth-session-checklist`, `auth-sso`, `auth-sso-automation`, `auth-token`, `auth-token-automation`, `auth-token-checklist`, `cache-stampede-guard`

### 未校准样本（只观测，不判失败）

| 查询 | 期望 | `on` 列出 | 命中 | 备注 |
|---|---|---|---|---|
| obs01 | （无期望） | 20 | — | 领域外查询，观测检索会挑出什么 |
| obs02 | （无期望） | 20 | — | 领域外查询，观测检索会挑出什么 |
| u01 | `n-plus-one-query-fix` | 20 | 否 | 目标技能描述里没有「变慢」「定位」这类词，需要理解才能命中 |
| u02 | `secret-scanning-setup` | 20 | 否 | 口语化说法，目标技能描述里用的是「密钥」「凭据」 |
| u03 | `repo-bootstrap-analysis` | 20 | 是 | 指向常驻技能：它无论相关与否都必须被列出，这里观测的是常驻机制本身 |

## 六、这套评估的局限

- **模型行为没测**：模型是否真的选对技能并完成任务（需要真实 LLM 运行）。本评估只量模型收到的提示词，因此**不能**据此断言任务成功率会提高。
- **门槛样本偏易**：门槛样本的期望技能，其描述里含有查询的关键说法，「命中」在词法上是可达的，所以高命中率是预期内的，它验证的是「检索基本可用」，不是「检索很聪明」。真正有区分度的是上面那批未校准样本。
- **技能目录是生成的**：不是开发机上的真实技能库。真实库的描述质量参差、命名风格不一，命中率可能更低。
- **绝对长度会漂移**：基础提示词取自 prompt.get_system_prompt('coding')，因此整条长度会随提示词本身变化；技能段的量与两种模式的差值不受影响

## 七、复现

```bash
.venv/Scripts/python.exe -m eval.skill_retrieval --label negative-control
```
