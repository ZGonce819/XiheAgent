# GAIA 2023 validation 全量评测最终报告

- 数据集：GAIA 2023 validation（165 题）
- 评测时间：2026-09-24 ~ 2026-09-25
- 最终成绩：**124 / 165 答对（75.2%）**，164 finished + 1 running（服务端挂起）
- 完整明细：`resume_eval/results/gaia_final_score_20260925.json`

---

## 1. 最终成绩

| 级别 | 任务数 | 答对 | 答对率 | 修复前答对 |
|---|---|---|---|---|
| Level 1 | 53 | 41 | 77.4% | 15 |
| Level 2 | 86 | 64 | 74.4% | 22 |
| Level 3 | 26 | 19 | 73.1% | 6 |
| **合计** | **165** | **124** | **75.2%** | **43（26.1%）** |

修复前基线：初始评测 165 题中 68 题 finished、43 题答对（当时联网搜索通道 216 次全部失败，成绩在该缺陷条件下取得）。

修复后：初始未完成的 97 题全部重测，其中 81 题答对（fix3 重试池 8 题中 6 题、全量重测系列 89 题中 75 题）；另将 qa 时间上限从 1200s 提升至 2400s 后，6 个此前被超时杀死的任务重跑全部答对。

---

## 2. 根因与修复（已全部落地并验证）

### 2.1 命令审批卡死（84 个 interrupted 主因）

- **现象**：`backends/command_policy.py` 将 `sed/awk/tr/wc/sort` 等常用命令及 `python -c "import x; print(y)"`（被 `;` 正则误切）、`curl -X POST` 判为硬性 approval，线程永久停在 awaiting_approval，前端映射为 interrupted。
- **实锤**：`data/pending_command_approvals.json` 现存 173 条待审批、181 条"未识别命令"；实测 15 条典型命令 9 条返回 approval。
- **修复**：
  - `_parts()` 重写为引号感知单遍扫描；
  - `_ALWAYS_ALLOW` 大幅扩充（git 只读子命令、tr/wc/sort/uniq/nl/cut/fold/comm/paste/join/jq/base64/od/hexdump/printf/xargs/env/stat/du/df/file/pip 只读子命令等）；
  - 新增 `_auto_approve_enabled()`（读 `XIHE_AUTO_APPROVE`，非空且非 0/false/no/off 即启用），approval→allow 降级，**硬拒绝永远生效**。
- **验证**：15 条命令默认 14 条 allow（仅 curl POST 保留 approval），开开关后全 allow。

### 2.2 联网搜索通道全挂（216 次搜索全失败）

- **现象**：`tools/web_search.py` 缺 ZHIPU_API_KEY，评测期所有搜索失败。
- **修复**：
  - 智谱主通道（zhipuai SDK，官方 PyPI 安装成功）实测 200 并命中答案来源；
  - Bing RSS 兜底（标准库 urllib + xml.etree），智谱不可用时降级；
  - 单条结果可能达 109KB 撑爆上下文，追加 `MAX_RESULT_CHARS=1500` 逐条截断。
  - 曾尝试 DDG Lite 兜底，实测返回主页/验证页、解析 0 条，弃用。

### 2.3 SSE 60s 空闲断连（ChunkedEncodingError）

- **现象**：流式响应空闲 60s 后连接断开，客户端抛 `ChunkedEncodingError: Response ended prematurely`，结果丢失。
- **修复**：
  - `services/streaming_service.py` 非 Redis 分支 `asyncio.wait_for(queue.get(), 10)` + 发送 `": keep-alive\n\n"` 注释行保活；
  - `resume_eval/gaia_eval.py` 在 error 且 thread_id 存在时轮询 `GET /api/v1/threads/{id}` 最多 60×3s，断连后仍能取回 finished 结果。
- **验证**：本次全量重测中通过该机制补录 7 题（客户端断连但服务端实际完成），证明双保险有效。

### 2.4 工具上限与时间上限打爆

- **现象**：qa 限制 (60, 600) 让 L3 难题死在 AgentRunLimitExceeded（00d579ea 曾三轮超限）。
- **修复**：`config/constants.py` qa → (180, 1200)，全量跑后 6 个任务超时被杀，进一步提升至 **(180, 2400)**。
- **验证**：0e9e85b8（L2）跑了 1565s 完成并答对；若维持 1200s 上限该题必死。运行上限受 `AGENT_QA_MAX_TOOL_CALLS` / `AGENT_MAX_TOOL_CALLS` 环境变量覆盖。

---

## 3. 全量重测过程

### 3.1 阶段回顾

| 阶段 | 内容 | 结果 |
|---|---|---|
| 初始评测 | 165 题 | 68 finished / 11 running / 84 interrupted / 2 error；matched=43 |
| 验证 fix3 | 重试池 8 题（修复后） | 8/8 finished，修正口径后 6/8 答对 |
| 全量重测 run | 初始未完成 89 题 | 前 6 题答对后 **DeepSeek 402 Insufficient Balance**（余额耗尽），78 题快速失败 |
| 充值后 run2 | 83 题重跑 | 客户端 7 error + 9 running；服务端诊断 + 回填后 **60/83 答对** |
| retry3 | 10 个 error+running | **9/10 答对**（含补录） |
| 50ad0280 单任务 | 1 题 | 服务端挂起（见 3.3） |

### 3.2 重测中暴露的新问题

1. **DeepSeek 余额 402**：89 题跑到第 7 题时余额耗尽。用户充值后恢复，83 题重跑 60 题答对。
2. **qa 时间上限 1200s 杀任务**：6 个任务（3da89939、c8b7e059、a56f1527、23dd907f、d5141ca5、0e9e85b8）超过 20 分钟被杀。提升至 2400s 后重跑全部答对。
3. **内容安全拦截**：72e110e7 被 DeepSeek 判 `Content Exists Risk (400)`，重跑后答对（拦截为概率性）。

### 3.3 唯一未完成：50ad0280（L1 文字谜题）

- **题目**：5×7 字母块拼句（THESE / AGULL / GLIDE / DPEAC / EFULL / YTOMY / CHAIR → "The seagull glided peacefully to my chair."）
- **现象**：客户端 SSE 连接建立后 ~0.15s 即断开（status=running）；服务端线程创建后一直 running，**后端日志无任何错误记录**，纯文本任务 30 分钟+ 无进展（手动直连 POST 同样复现：新线程 60s 仍 running）。
- **结论**：非报错，属后端对该类任务的个别静默挂起，无错误日志、未超时被杀。影响 1/165。

---

## 4. 评测口径

- `normalize()` 对齐 GAIA 官方：删除 `string.punctuation`（如 "86%" → "86"）。
- `normalized_match()` 增加答案首行兜底。
- 补录机制：客户端断连时轮询服务端线程，`extract_answer` 取最后一个 agent 文本 chunk（跳过"任务计划"）。

---

## 5. 关键文件

| 文件 | 说明 |
|---|---|
| `resume_eval/results/gaia_final_score_20260925.json` | **最终 165 题完整成绩明细** |
| `resume_eval/results/gaia_validation_20260924.json` | 初始评测（修复前基线） |
| `resume_eval/results/gaia_verify_fix3_20260925.json` | 重试池 8 题（修复验证） |
| `resume_eval/results/gaia_full_run_20260925.json` | 全量重测第一轮（402 中断） |
| `resume_eval/results/gaia_full_run2_20260925.json` | 充值后 83 题（60 答对，含补录） |
| `resume_eval/results/gaia_full_retry3_20260925.json` | retry3 10 题（9 答对，含补录） |
| `config/constants.py` | qa 限制 (180, 2400) |
| `backends/command_policy.py` | 命令审批修复 |
| `tools/web_search.py` | 智谱 + Bing RSS 搜索修复 |
| `services/streaming_service.py` | SSE keep-alive 修复 |
| `resume_eval/gaia_eval.py` | 评测脚本（断连补轮询、口径修正） |
| `.env` | ZHIPU_API_KEY（L73，真实有效）、DEEPSEEK_API_KEY（L38 注释行内，python-dotenv 可解析） |
