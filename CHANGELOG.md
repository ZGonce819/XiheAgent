# 变更记录（自 fd63f4bc 以来）

**区间**：2026-09-25（`fd63f4bc` feat: 多模型编排——主/子 Agent 异构模型分工）→ 2026-10-09
**规模**：26 个修改文件（+1334 / −192 行），新增评测系统、监控面板、运维脚本等 10+ 文件
**主题**：GAIA 2023 validation 评测体系 + 主流程可靠性加固 + 评测能力通用化

---

## 一、GAIA 评测系统（`resume_eval/`）

### `gaia_eval.py` —— 评测主脚本

- **内建自动审批**：同一进程内完成三评审表决并直接恢复执行，无需另起审批监控进程（`--no-auto-approve` 可关闭）
- **恢复续跑**：`--resume-from` / `--retry-from` 按 `thread_id` 复用 checkpoint，续跑 interrupted/error 题，不重跑已完成
- **判死跳过**（`MAX_DEAD_ATTEMPTS=3`）：无答案的技术失败（final_status ∈ {None, error, interrupted, running}）累计 3 次后不再进重试队列；finished（无论对错）计数重置
- **格式强化 prompt**（FORMAT REQUIREMENT）：要求最终答案最纯——禁前缀、解释、Markdown、代码块、引号、括号
- **静默挂死看门狗**（`SILENT_HANG_S=900`）：线程 running 但 `updatedAt` 连续 15 分钟无更新 → 记 `thread_silent_hang` 放弃（上游模型/工具无响应时不再空转数小时）
- **结果统计**：`n_finished` / `matched` / `status_counts` / `by_level`，每题含 `approval_attempts` / `approval_errors` 便于排查

### `gaia_approval_monitor.py` —— 审批监控（独立运行时用，gaia_eval 已内建同逻辑）

- **作弊命令硬拦截**（`_cheat_block`）：命中 `GAIA_web.jsonl` / `WebVoyager` / `raw.githubusercontent.com` / `cdn.jsdelivr.net` / `ghproxy` 等下载特征直接 reject，**不走投票**（防模型下载公开答案数据集）
- **审批恢复 SSE 无数据超时收紧到 120s**（原 1800s）：恢复流挂起不再拖死整轮评测
- 单测 7/7（`logs/_test_cheat_block.py`）

### `evaluate.py` —— 评测入口单实例锁

- 启动时写 pid 锁（`logs/gaia_eval.single.lock`），已有存活实例时第二个实例直接退出——防止 watchdog 与手动启动同时拉起两个 evaluate 抢同一队列（曾发生：双实例重复提交任务、结果文件写竞争）

### 其他评测脚本

| 脚本 | 用途 |
|---|---|
| `gaia_state.py` | 评测线程状态模型与解析 |
| `prepare_gaia_resume.py` / `prepare_gaia_misses.py` | 生成重试/漏题清单 |
| `reconcile_gaia_results.py` | 备份并核对历史结果（不重跑） |
| `diagnose_gaia_states.py` | 卡死/异常线程诊断 |
| `results/` | 评测结果与中间产物 |

---

## 二、主流程可靠性加固

| 文件 | 改动 |
|---|---|
| `agent/graph/model.py` | **所有模型调用 `timeout=120`**（OpenAI SDK 默认 600s，上游无响应会静默挂起 10 分钟）；`MAIN_API_KEY` / `MAIN_BASE_URL` 优先于 `DEEPSEEK_*`；新增 `make_validator_model`（独立证据校验模型，支持 `FINAL_VALIDATOR_THINKING`） |
| `backends/local_shell.py` | **网络命令强制 60s 超时**（`NETWORK_CMD_TIMEOUT`，覆盖 iwr/curl/wget/bitsadmin/aria2 等），防下载挂死 |
| `agent/runtime/streaming_runtime.py` | **软中断自修复**：`AgentRunLimitExceeded` 不再直接报错暂停，注入"基于已有证据给出最终答案"修复消息、用同一 checkpoint 恢复（修复轮限 240s / 2 工具） |
| `agent/middleware/message_sanitize.py` | 上下文滚动压缩：长任务每轮不再原样重发全部历史消息 |
| `agent/middleware/delegation.py` | 多来源检索自动委派只读子任务（≥3 次搜索/页面或 ≥3 文件时触发一次） |
| `agent/middleware/retry.py` | 模型瞬时错误指数退避重试（超时/限流/连接异常/5xx；参数错误不重试） |
| `tools/web_search.py` / `tools/deep_search.py` | 搜索预算（每任务 8 次 web+deep）、结果截断、deep_search 多轮去重与交叉来源验证 |
| `prompt.py` | `EXECUTION_RETRY_POLICY`：同一来源连续 3 次失败必须换方法；预算用尽必须给最可能答案并说明依据 |
| `database/redis.py` | `socket_timeout` 5→15（修复 SSE `XREADGROUP` 阻塞在超时边缘导致的 `Timeout reading from socket` 流中断） |
| `config/approval_runtime.py` | 审批状态**原子写入**（mkstemp + fsync + os.replace），防进程崩溃写坏 JSON |
| `.env` | `AGENT_QA_MAX_SECONDS=3600`：qa 类任务（含全部 GAIA 评测题）恢复时长上限，超时触发软中断收尾，防"绕圈"题无限烧额度（原为 0 = 不限） |
| `api/routes.py` | 新增 `GET /health/ready`（检查 MySQL/Redis 就绪，任一不可用返回 503，不调用模型） |

---

## 三、最终答案能力与评测功能通用化

> 目标：评测专属能力不再依赖题目前缀，成为平台通用行为；GAIA 只是自然覆盖的任务之一。

| 文件 | 改动 |
|---|---|
| `agent/middleware/final_response.py` | ① `submit_final_answer` 工具调用/结果解析为最终答案候选；② 证据链校验 + 最多 2 次修复轮（`_repair` 循环带补充搜索额度）；③ **纯答案模式通用化**：新增 `_wants_plain_answer(question)`——题目明示"只要答案 / 纯数字 / 单字 / 不要解释"（中英文）即走严格纯答案校验，**GAIA 前缀只是触发条件之一，不再是特权分支**；④ 图片证据检查泛化：任何含图片指示的题目要求 `inspect_image` 已验证转录；⑤ 修复/收尾提示词措辞通用化（本题→本次任务、原题→原始任务） |
| `tools/submit_answer.py` | `submit_final_answer` 收尾工具：把"输出最终答案"变成可调用工具（预算耗尽后的强制出口），**全局注册、所有任务可见** |
| `utils/message_utils.py` | `final_answer_error`：纯答案格式检查（禁前缀/解释/代码块等） |
| `schemas/request_schema.py` / `api/dashboard/threads.py` / `services/streaming_service.py` / `agent/runtime/task_router.py` | `skip_plan_confirmation` 透传链：无人值守任务（评测/自动化循环）跳过技术方案确认直接实现 |
| `agent/runtime/task_router.py` | GAIA 题（题面/续跑指令含 "GAIA benchmark question"）判为 `qa` 直答，不被方案确认拦截 |
| `agent/runtime/planning_router.py` | `repo_url` 可选：本地任务（无仓库）不再要求解析仓库 |

---

## 四、GAIA 监控面板（`gaia_monitor/`）

- **`gaia_monitor.py` + `gaia_monitor.html`**（端口 **8787**，需系统 Python311 启动，venv 起不来）
- 数据端点：
  - `/api/status` — 已完成 / 已命中 / 剩余 / 按难度命中分布
  - `/api/activity` — 实时活动流（每线程只保留首条起始事件，按时间升序）
  - `/api/apilog` — 模型请求实时流（最近 40 条，8s 轮询）
  - `/api/recent` — 最近完成结果（含判定/耗时/错误）
  - `/api/threads/<id>` — 线程详情（含输入问题与输出过程，查询超时放宽到 6s）
- 前端：`#apiflow` 区块实时模型请求、活动流按线程聚合、运行中题目卡片带消息进度

---

## 五、自动化运维

- **`resume_gaia.py`**：watchdog 脚本——serve（2026 端口）健康才返回；evaluate 进程数为 0 时拉起新评测轮次。由计划任务 `GAIA_ResumeWatchdog` 每分钟触发（经 `logs/gaia_watchdog_hidden.vbs` 隐藏窗口启动）
- **`.artifacts/gaia-key-rotation/`**：`serve.py`（LangGraph/DeepAgents 服务，端口 2026）+ `evaluate.py`（评测入口）+ `keys.json`（key 轮换——只匹配 `api.xiaomimimo.com` 主机；`token-plan-cn.xiaomimimo.com` 不参与轮换，等同单 key）
- 进程结构：serve（2026）+ evaluate（watchdog 守护）+ monitor（8787）三进程并行

---

## 六、配置与环境变量

- `config/constants.py`：`MODEL_RETRY_MAX_ATTEMPTS` / `MODEL_RETRY_BACKOFF_SECONDS` / `DEEP_SEARCH_DEFAULT_ROUNDS` 环境变量化；`TASK_KIND_DEFAULT_LIMITS`（qa=180 工具/2400s、coding=300/1800s 等）
- `config/model_config.py`：`MAIN_API_KEY` / `MAIN_BASE_URL` 优先于 DeepSeek 配置
- `.env.example`：新增 `MAIN_API_KEY` / `MAIN_BASE_URL` / `INTENT_MODEL` / `FINAL_VALIDATOR_*` / `MODEL_RETRY_*` / `DEEP_SEARCH_DEFAULT_ROUNDS` / `AGENT_QA_MAX_SECONDS`
- `pyproject.toml`：新增 Pillow 依赖（图片证据缩放与颜色分离）

---

## 新增文件清单

```
agent/middleware/delegation.py      # 自动委派只读子任务
agent/middleware/retry.py           # 模型瞬时错误重试中间件
resume_eval/gaia_state.py           # 评测线程状态模型
resume_eval/gaia_approval_monitor.py# 审批监控 + 作弊硬拦截
resume_eval/prepare_gaia_resume.py  # 重试清单生成
resume_eval/prepare_gaia_misses.py  # 漏题分析
resume_eval/reconcile_gaia_results.py # 结果核对
resume_eval/diagnose_gaia_states.py # 卡死线程诊断
resume_gaia.py                      # watchdog（serve + evaluate 守护）
gaia_monitor/gaia_monitor.py        # 8787 监控面板后端
gaia_monitor/gaia_monitor.html      # 8787 监控面板前端
.artifacts/gaia-key-rotation/       # serve / evaluate / keys.json
logs/_test_cheat_block.py           # 作弊拦截单测（7/7）
```

## 已知边界

- 模型 `timeout=120` 是 OpenAI 的 **read 间隔超时**：上游"慢吐 token"（每 30-60s 一个 chunk）不会触发，仍靠评测侧 15 分钟静默看门狗兜底
- `run_limits` 的时间上限只在事件到达时检查，线程静默挂死时永不触发——已由评测侧看门狗（updatedAt 静止 15 分钟）覆盖
- 主流程 GAIA 判定仍保留题目前缀（`task_router`），但 final_response 已改通用判定（`_wants_plain_answer`）；task_router 的任务类型化判定为下一步
- key 轮换仅匹配 `api.xiaomimimo.com`；`token-plan-cn` 等代理地址不轮换（符合"不需要开启切换 key"意图）
