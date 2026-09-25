# Xihe 简历指标实测记录（2026-09-24）

本目录保存本次实际运行的模型评测脚本与原始 JSON。评测只读取 `D:\Bemyself\Python\MOE` 中的权重和代码，不修改训练产物。

## 环境与方法

- 本机：NVIDIA GeForce RTX 4060 Laptop GPU（8 GB），Windows WDDM，PyTorch 2.11.0+cu128，Transformers 5.17.0。
- 模型：`Xihe-106M` 的 Base / Instruct / Distill，`Xihe-147M-MoE` 的 Base / Instruct；`.pth` 以 `strict=True` 加载。
- 文本 PPL：使用项目训练之后形成的 Markdown 文档，按同一 6,400 词表 tokenizer 取前 4,096 个 token，以不重叠 256-token 窗口计算 next-token NLL；实际有效目标 token 为 4,095。PPL = `exp(总 NLL / 有效 token 数)`。没有把 MoE 辅助损失计入语言模型 NLL。
- 解码速度：batch 1、128-token 输入、固定生成 64 token、贪心选择、KV Cache、`logits_to_keep=1`；排除首 token 预填充时间，统计后续 63 token 的速度。预热 2 次，正式重复 10 次，中位数为报告值。GPU 计时前后调用 `torch.cuda.synchronize()`。
- 代码版本：MOE `4cfb356ac622f491eaf18b7e4620840166e60a0f`；XiheAgent `d6638ef7d7360deddd8716e3e987ba118e6d9e1a`。

## 实测结果

| 模型 | `xihe/README.md` 文本 PPL | MOE 训练教程文本 PPL | RTX 4060 解码 token/s 中位数 |
| --- | ---: | ---: | ---: |
| Dense Base | 1982.1 | 3147.1 | 67.0 |
| Dense Instruct | 164.1 | 220.4 | 68.7 |
| Dense Distill | 130.5 | 194.4 | 67.4 |
| MoE Base | 2455.8 | 未测 | 57.7 |
| MoE Instruct | 114.7 | 未测 | 56.9 |

第一份文档的原始结果见 `results/dense_gpu_4096.json` 和 `results/moe_gpu_4096.json`；第二份文档见 `results/dense_blog_gpu_4096.json`。`results/cpu_4096.json` 是 CPU 交叉验证，Dense 三个版本的 PPL 与 GPU 数值一致到显示精度。

这些数值仅描述两份特定后训练文档的 token 预测表现，以及当前 Windows 机器上的单请求贪心解码速度。两份文档虽晚于模型权重，但尚未做语料去重或独立抽样，不应称为正式 held-out benchmark；PPL 也不等于代码能力、对话质量或蒸馏后“精度保持率”。

## 复现命令

在 `D:\Bemyself\Python\xihe` 目录执行（评测环境位于 `.eval-venv`）：

```powershell
.\.eval-venv\Scripts\python.exe .\resume_eval\model_metrics.py `
  --project D:\Bemyself\Python\MOE `
  --text .\README.md `
  --roles Base Instruct Distill `
  --max-tokens 4096 --window 256 `
  --prompt-tokens 128 --new-tokens 64 `
  --warmups 2 --repeats 10 --device cuda `
  --output .\resume_eval\results\dense_gpu_4096.json
```

MoE 对照增加 `--model-name Xihe-147M-MoE --roles Base Instruct`。更换 `--text` 可以评测新文档，汇总时必须标明文本来源和有效 token 数。

## 尚未完成的指标与运行条件

- **HumanEval / MBPP pass@1**：需要固定官方题集、生成协议，并在隔离容器中执行模型生成的代码。Docker 已确认在 WSL 的 Ubuntu-24.04 中运行；该项尚未给出分数。
- **Agent 任务完成率、工具选择准确率、中断恢复率、SSE 时延**：需要固定多类型测试仓库任务集，并保存每条 `thread_id/run_id` 的事件、测试结果和失败原因。MySQL、Redis、PostgreSQL 已确认在 WSL 容器中运行，项目 Windows Python 环境使用现有配置成功连通三项服务。先前仅检查 Windows 命令和端口得出的“服务不可用”结论有误。现有 Skill 检索评测的 150 条技能 / 25 条查询结果只证明提示词压缩与检索命中，不是任务成功率。
- **业务节省比例**：需要同一批任务的人工作业时间和 Agent 辅助时间，记录审查 / 单测质量，并用成对样本比较。当前无可用的人工基线。

下一步优先补一份去重的中文文本验证集，并对所有权重重复 PPL；在 WSL 隔离容器中执行 HumanEval / MBPP，并使用已连通的 Agent 依赖服务做固定仓库任务集。

### HumanEval 格式适配试跑

已下载官方 `HumanEval.jsonl.gz`（SHA-256：`B796127E635A67F93FB35C04F4CB03CF06F38C8072EE7CEE8833D7BEE06979EF`）并用 `humaneval_generate.py` 在前 5 题上固定贪心解码、每题最多 192 个新 token 做格式适配试跑。原始输出保存在 `results/humaneval_base_pilot_5.jsonl`、`results/humaneval_instruct_pilot_5.jsonl` 和 `results/humaneval_instruct_chat_pilot_5.jsonl`。Base 用官方原始函数前缀时 5/5 题立即输出 EOS；Instruct 用原始前缀时 5/5 题重复引号至长度上限；Instruct 用本项目 chat template 时主要生成解释性文字，未得到可供函数测试的实现。这是生成格式诊断，不是完整 164 题的 HumanEval pass@1，也没有执行题目测试，因此不能写作基准成绩。后续需要固定可产生代码的提示协议，再在 WSL Docker 隔离环境里运行官方测试；如果仍无法产生有效实现，应如实报告该模型的代码能力边界。

## Agent 端到端冒烟测试

在 2026-09-24 使用 `resume_eval/agent_smoke.py` 调用真实 `/api/v1/threads/stream-message`，工作目录为 `fixtures/agent_smoke`。任务是读取 `facts.txt` 并只返回预置的 release code，正确性以会话最终持久化回答、`finished` 状态和无文件改动共同判定。原始 SSE 事件和核对结果见 `results/agent_smoke_verified_20260924.json`。

| 样本 | 最终回答 | 文件改动 | 首次文本事件 | 请求完成 | 判定 |
| --- | --- | --- | ---: | ---: | --- |
| 只读文件问答，n=1 | `CEDAR-27` | 0 | 8.609 秒 | 8.849 秒 | 通过 |

首次在受限网络环境下运行的记录见 `results/agent_smoke_20260924.json`，外部模型连接失败，不能计为模型任务失败。放开模型 API 网络访问后成功重跑。另一次运行 `results/agent_smoke_escalated_20260924.json` 的最终会话回答也正确，但旧版评测脚本将两个 `text_delta` 直接相加，误判为失败。实测流式增量拼接得到 `CEDCEDAR-27`，而最终会话文本为 `CEDAR-27`；流式文本存在重复前缀，应单独排查，不能把拼接结果当作最终回答。这个极小且简单的样本仅证明端到端链路可运行，不构成可写入简历的任务成功率或时延分布。

复现时启动后端并关闭自动任务调度，再运行：

```powershell
$env:XIHE_AUTOMATION_SCHEDULER='false'
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 2025
.\.venv\Scripts\python.exe .\resume_eval\agent_smoke.py --output .\resume_eval\results\agent_smoke_repeat.json
```
