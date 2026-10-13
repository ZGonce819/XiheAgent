"""Run GAIA validation tasks against the local XiheAgent SSE API."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from resume_eval.gaia_state import (
    atomic_json,
    checkpoint_lock,
    iter_sse,
    read_json,
    validate_record,
    write_checkpoint,
)

import requests
import string

try:
    import pandas as pd
except ImportError:  # 评测环境可只使用仓库随附的 CSV 元数据
    pd = None


def normalize(value: object) -> str:
    """对齐 GAIA 官方 evaluator：小写、删除全部标点、压缩空白。

    官方 normalization 对 string.punctuation 逐字符删除，因此 "86%" → "86"、
    "Indonesia, Myanmar" → "indonesia myanmar"。仅 strip 首尾标点会漏掉
    内部的 %、, 等，导致答案内容正确但被判不匹配。
    """
    text = str(value or "").strip().casefold()
    text = re.sub(r"[`*_#]", "", text)
    text = "".join(ch for ch in text if ch not in string.punctuation)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# 答案标记提取：模型可能输出 "答案：X" / "Answer: X" / "最终答案：X" 再带一段
# 核对说明。把标记后的首个非空行（去除行内括号注释）加入判分候选，与现有
# "首个非空行"兜底同性质——匹配规则仍是 normalize 后精确比较，未放宽。
_FINAL_ANSWER_MARKERS = re.compile(
    r"(?:最终答案|答案|Final\s*Answer|Answer|The answer|Result|结果)\s*[:：]\s*",
    re.I,
)


def _marked_answer_candidate(answer_text: str) -> str:
    """从长答案中提取『答案标记』后的首个非空行；失败返回空串。"""
    lines = [ln.strip() for ln in answer_text.splitlines()]
    for i, ln in enumerate(lines):
        if not ln:
            continue
        m = _FINAL_ANSWER_MARKERS.search(ln)
        if not m:
            continue
        rest = ln[m.end():].strip()
        if not rest:
            # 标记独占一行时，取下一非空行作为答案内容。
            rest = next((l for l in lines[i + 1:] if l), "")
        # 去除行内括号注释，如 "《A Nightmare on Elm Street》（猛鬼街，Wes Craven）"。
        rest = re.sub(r"[（(][^（()）]*[)）]", "", rest).strip()
        if rest:
            return rest
    return ""


def normalized_match(answer: object, expected: object) -> bool:
    """先按完整答案比对；不匹配时取首个非空行再比对。

    GAIA prompt 要求 “Return only the final answer”，但模型可能附带核对说明
    （如 “Indonesia, Myanmar\n\n核对说明（供参考，非答案的一部分）：…”）。
    官方 evaluator 按整串比对会把这种违规格式判错，这里给出宽容兜底，
    不影响单行答案题的判分。
    """
    answer_text = str(answer or "")
    candidates = [answer_text]
    first_line = next((line.strip() for line in answer_text.splitlines() if line.strip()), answer_text)
    if first_line and first_line != answer_text:
        candidates.append(first_line)
    marked = _marked_answer_candidate(answer_text)
    if marked and marked not in candidates:
        candidates.append(marked)
    normalized_expected = normalize(expected)
    return any(normalize(c) == normalized_expected for c in candidates)


def extract_answer(thread: dict) -> str:
    if isinstance(thread.get("finalAnswer"), str):
        return thread["finalAnswer"].strip()
    messages = thread.get("messages", [])
    if not messages or messages[-1].get("author") != "agent":
        return ""
    return "\n".join(
        str(chunk.get("text") or "") for chunk in messages[-1].get("chunks", [])
        if chunk.get("kind") == "text" and chunk.get("text") != "任务计划"
    ).strip()


def _attempts_file(output: Path) -> Path:
    """判死计数文件：与结果文件同目录，记录每道题的技术失败次数。"""
    return output.with_name(output.stem + "_attempts.json")


def _load_attempts(output: Path) -> dict:
    try:
        if _attempts_file(output).exists():
            return json.loads(_attempts_file(output).read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_attempts(output: Path, attempts: dict) -> None:
    atomic_json(_attempts_file(output), attempts)


# 瞬时技术错误特征：网络/服务层暂时性故障，恢复后重试完全可能成功，
# 不得计入判死（attempts >= 3 永久跳过）——判死只针对真实失败。
_TEMPORARY_ERROR_PATTERNS = (
    r"HTTPError: \d{3}",
    r"ConnectionError",
    r"ConnectionReset",
    r"ChunkedEncodingError",
    r"TimeoutError",
    r"Read timed out",
    r"Too Many Requests",
    r"thread_silent_hang",
    r"sse_error",
    r"approval_sse_error",
    r"approval_error",
    r"stream_error",
    r"worker:",
    r"Max retries exceeded",
    r"429",
    r"500",
)


def _is_temporary_error(error) -> bool:
    """判断错误是否为瞬时技术错误（不应判死）。"""
    if not error:
        return True
    text = str(error)
    return any(re.search(pattern, text, re.I) for pattern in _TEMPORARY_ERROR_PATTERNS)


def mark_running(output_path: Path, task_id: str, thread_id: str) -> None:
    """task_started 时把进行中的 thread_id 写入结果 json（标 running）。

    中断（余额/手动杀进程）后重启，resume 逻辑需要从 json 看到“当前在跑哪题、
    用哪个 thread”——否则只会看到上次的旧 error 记录，续跑无从谈起。
    workers=1 串行执行，配合 checkpoint_lock 防止与写回竞争。
    """
    if not output_path or not thread_id:
        return
    try:
        with checkpoint_lock(output_path):
            disk = read_json(output_path, {})
            for row in disk.get("results", []):
                if row.get("task_id") == task_id:
                    row["final_status"] = "running"
                    row["thread_id"] = thread_id
                    row["error"] = ""
                    break
            atomic_json(output_path, disk)
    except Exception as exc:
        print(json.dumps({"event": "mark_running_failed", "task_id": task_id,
                          "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False), flush=True)


def run_task(
    base_url: str, workdir: str, row: dict, timeout: tuple[int, int],
    auto_approve: bool = True, approval_max_retries: int = 3,
    resume_thread_id: str | None = None, output_path: Path | None = None,
) -> dict:
    question = str(row["Question"])
    file_name = str(row.get("file_name") or "").strip()
    attachment = f" The attached file is available as /projects/{file_name}." if file_name else ""
    # 格式强化：GAIA 命中判定是 normalize 后严格比较，答案带任何前缀/解释/Markdown
    # 都会导致校验失败或 normalized_match 不命中。必须让模型第一次就输出纯答案。
    format_rule = (
        "FORMAT REQUIREMENT: your final answer must be ONLY the answer itself — a single clean value "
        "or short text exactly as the question requests. No explanations, no reasoning, no source "
        "citations, no prefixes like \"The answer is\" or \"答案是\", no Markdown, no code blocks, "
        "no quotes, no parentheses or bracketed notes. If the question requests a specific list/line/"
        "number format, match it exactly. Return only the final answer. If your output does contain "
        "an answer marker like \"答案：/Answer:/最终答案：\", the marker must be followed only by the "
        "answer itself (a single value or short text) — no explanations, citations or reasoning after "
        "it; delete such extra content entirely."
    )
    prompt = (
        "Answer the following GAIA benchmark question. Use the available tools and attached file "
        "when needed.\n"
        f"{format_rule}\n\n"
        "Time rule: if the question mentions a specific date, year or time period, the information "
        "you retrieve must reflect that time point (e.g. rosters, prices, events as of that date), "
        "not the latest or current state.\n\n"
        "Integrity note: you are being evaluated — do NOT search for the benchmark's official answer "
        "key, 'validation answers', or any page that contains the expected answer for this question. "
        "Answer strictly from the actual content you retrieve and reason about.\n\n"
        f"{question}{attachment}"
    )
    # 续跑：余额/配额中断后，复用原 thread 从 LangGraph checkpoint 继续，
    # 而不是新建 thread 整题重跑。注入“继续完成”指令而非重复题面，
    # 避免模型把重复题目当成新一轮任务重新组织。
    resume_used = False
    if resume_thread_id:
        try:
            probe = requests.get(
                f"{base_url}/api/v1/threads/{resume_thread_id}", timeout=30,
            )
            if probe.status_code == 200:
                probe_thread = probe.json()
                if probe_thread.get("status") != "finished":
                    resume_used = True
        except requests.RequestException:
            pass
    continue_prompt = (
        "Continue your previous work on this GAIA benchmark question. You may keep using tools if needed. "
        "The original question was:\n\n"
        f"{question}{attachment}\n\n"
        "Time rule: if the question mentions a specific date, year or time period, the information "
        "you retrieve must reflect that time point, not the latest or current state.\n\n"
        "FORMAT REQUIREMENT: your final answer must be ONLY the answer itself — no explanations, no "
        "reasoning, no prefixes, no Markdown, no code blocks, no quotes. Match the exact format the "
        "question requests. Return only the final answer."
    )
    payload = {
        "content": continue_prompt if resume_used else prompt,
        "backend_type": "local",
        "local_work_dir": workdir,
        # GAIA 评测无人值守：跳过 serve 侧"先出技术方案、等确认"的流程，
        # 避免 GAIA 标准答案被中文 Markdown 技术方案模板误判为格式不符而强制重写。
        "skip_plan_confirmation": True,
        # 显式声明问答任务类型：主流程不再重复分类，避免含 "Pull out"
        # 等措辞的题面被误判为仓库同步而走错分支（主流程保持通用，无 GAIA 字样）。
        "task_kind": "qa",
    }
    endpoint = (
        f"{base_url}/api/v1/threads/{resume_thread_id}/stream-message"
        if resume_used else f"{base_url}/api/v1/threads/stream-message"
    )
    started = time.perf_counter()
    events: list[dict] = []
    thread_id = resume_thread_id if resume_used else None
    error = None
    first_text_s = None
    saw_done = False
    stream_error = None

    def _announce_start() -> None:
        # 统一在 thread_id 确定后立即标 running（续跑线程已知、新建从流中取得）
        print(json.dumps({"event": "task_started", "task_id": str(row["task_id"]),
                          "thread_id": thread_id}, ensure_ascii=False), flush=True)
        mark_running(output_path, str(row["task_id"]), thread_id)

    if thread_id:
        _announce_start()
    try:
        with requests.post(
            endpoint,
            json=payload,
            stream=True,
            timeout=timeout,
        ) as response:
            response.raise_for_status()
            for current_event, data in iter_sse(response):
                elapsed = round(time.perf_counter() - started, 3)
                if not thread_id and data.get("thread_id"):
                    thread_id = data["thread_id"]
                    _announce_start()
                if current_event == "text_delta" and first_text_s is None:
                    first_text_s = elapsed
                events.append({"event": current_event, "elapsed_s": elapsed, "data": data})
                if current_event == "error":
                    stream_error = str(data.get("message") or data.get("detail") or data)
                    error = f"sse_error: {stream_error}"
                if current_event == "done":
                    saw_done = True
                    break
            if not saw_done and not error:
                error = "sse_incomplete: 事件流未发送 done 即结束"
    except Exception as exc:
        # resume 续跑被 409（线程快照仍标 running，通常来自 serve 重启前的
        # 孤儿线程/快照残留）拒绝时，降级为新建线程整题重跑，而不是秒败丢弃。
        if (resume_used and isinstance(exc, requests.HTTPError)
                and exc.response is not None and exc.response.status_code == 409):
            print(json.dumps({"event": "resume_conflict_fallback", "task_id": str(row["task_id"]),
                              "thread_id": resume_thread_id}, ensure_ascii=False), flush=True)
            resume_used = False
            thread_id = None
            error = None
            saw_done = False
            try:
                with requests.post(
                    f"{base_url}/api/v1/threads/stream-message",
                    json={**payload, "content": prompt},
                    stream=True,
                    timeout=timeout,
                ) as response:
                    response.raise_for_status()
                    for current_event, data in iter_sse(response):
                        elapsed = round(time.perf_counter() - started, 3)
                        if not thread_id and data.get("thread_id"):
                            thread_id = data["thread_id"]
                            _announce_start()
                        if current_event == "text_delta" and first_text_s is None:
                            first_text_s = elapsed
                        events.append({"event": current_event, "elapsed_s": elapsed, "data": data})
                        if current_event == "error":
                            stream_error = str(data.get("message") or data.get("detail") or data)
                            error = f"sse_error: {stream_error}"
                        if current_event == "done":
                            saw_done = True
                            break
                    if not saw_done and not error:
                        error = "sse_incomplete: 事件流未发送 done 即结束"
            except Exception as exc2:
                error = f"{type(exc2).__name__}: {exc2}"
        else:
            error = f"{type(exc).__name__}: {exc}"

    thread = {}
    approval_attempts = {}
    approval_errors = []
    if thread_id:
        failures = 0
        # 静默挂死看门狗：serve 侧 run_limits 只在事件到达时检查时间上限，
        # 线程卡在"模型/工具无响应且无事件"时上限永不触发（曾实测挂 52 分钟）。
        # 评测侧盯着线程 updatedAt：running 但长时间无任何更新 → 判定上游挂起，
        # 记录 error 放弃（交给判死机制累计），避免整轮队列被单题拖死。
        # 阈值（1200s）必须大于 serve 侧事件流看门狗（600s）+ 软中断收尾（240s），
        # 让 serve 先完成软中断收尾出答案，而不是被评测侧抢先放弃。
        SILENT_HANG_S = 1200
        last_activity_at = None
        while True:
            try:
                detail = requests.get(f"{base_url}/api/v1/threads/{thread_id}", timeout=30)
                detail.raise_for_status()
                thread = detail.json()
                failures = 0
                pending = thread.get("pendingApproval")
                if (auto_approve and pending and not pending.get("claimed")
                        and thread.get("status") not in {"running", "finished"}):
                    # Scope approval strictly to this worker's own thread. Reuse the
                    # review/recovery helper, without starting a monitor process.
                    from resume_eval.gaia_approval_monitor import emit, recover

                    approval_id = pending.get("id")
                    if not approval_id or pending.get("threadId") != thread_id:
                        error = "approval_error: 待审批记录与当前题目不匹配"
                        break
                    count = approval_attempts.get(approval_id, 0)
                    if count >= max(1, approval_max_retries):
                        error = f"approval_error: 审批 {approval_id} 重试已耗尽"
                        emit(event="needs_attention", thread_id=thread_id, reason=error)
                        break
                    if count:
                        time.sleep(min(300, 30 * 2 ** (count - 1)))
                    approval_attempts[approval_id] = count + 1
                    try:
                        recover(base_url, pending, prompt)
                        # Approval streams can end at another approval. Fetch fresh
                        # state and handle the next ID before recording the answer.
                        error = None
                        stream_error = None
                    except Exception as exc:
                        message = f"{type(exc).__name__}: {exc}"
                        approval_errors.append(message)
                        error = f"approval_error: {message}"
                        emit(event="recovery_error", thread_id=thread_id, error=message)
                    continue
                if thread.get("status") != "running" or stream_error:
                    break
                # 静默挂死检测：updatedAt 停止前进超过阈值 → 放弃本题
                updated = thread.get("updatedAt")
                if updated:
                    if last_activity_at is None or updated > last_activity_at:
                        last_activity_at = updated
                    elif time.time() * 1000 - last_activity_at > SILENT_HANG_S * 1000:
                        error = (f"thread_silent_hang: 线程超过 {SILENT_HANG_S} 秒无更新"
                                 f"（updatedAt 静止于 {last_activity_at}），判定上游挂起")
                        break
                # No task wall-clock limit: only failed status requests are bounded.
                time.sleep(3)
            except requests.RequestException as exc:
                failures += 1
                if failures >= 3:
                    error = error or f"thread_fetch: {type(exc).__name__}: {exc}"
                    break
                # 指数退避（5/10/20/40/60s 封顶）：serve 繁忙窗口通常持续
                # 数十秒，固定 3s 重试会连续撞在同一窗口导致误判连接失败。
                time.sleep(min(60, 5 * 2 ** failures))
        if thread.get("status") == "finished" and not stream_error:
            error = None  # A transport interruption recovered from persisted state.

    answer = extract_answer(thread)
    expected = str(row.get("Final answer") or "")
    result = validate_record({
        "task_id": str(row["task_id"]),
        "level": int(row["Level"]),
        "file_name": file_name,
        "question": question,
        "expected": expected,
        "answer": answer,
        "normalized_match": normalized_match(answer, expected),
        "thread_id": thread_id,
        "final_status": thread.get("status"),
        "changed_files": thread.get("changedFiles"),
        "first_text_s": first_text_s,
        "total_s": round(time.perf_counter() - started, 3),
        "event_count": len(events),
        "tool_events": sum(item["event"] == "tool_update" for item in events),
        "error": error,
        "stream_error": stream_error,
        "approval_attempts": approval_attempts,
        "approval_errors": approval_errors,
        "resumed": resume_used,
    })
    if error and result.get("final_status") in {None, "running", "finished", "error"}:
        if answer:
            # 模型已提交最终答案（可能被 validator 否决）：算跑完，命中与否看 match。
            # 保留 error 字段用于追溯否决原因，但状态不降级为 error。
            # 线程状态为 error 但确有答案（软中断收尾/修复轮写回）同样按跑完处理。
            result["final_status"] = "finished"
            # 瞬时技术错误（网络/读超时/连接断开等）时答案由模型正常产出，应正常评分；
            # 仅非瞬时错误（秒败/中断/绕圈等）答案可能不可信，强制保持 miss。
            if not _is_temporary_error(error):
                result["normalized_match"] = False
        else:
            result["final_status"] = "error"
            result["normalized_match"] = False
    return result


_MANUAL_ADJUDICATIONS = Path(__file__).with_name("manual_adjudications.json")


def _apply_manual_adjudications(results: list[dict]) -> None:
    """应用人工裁决：评测脚本内存中的记录可能覆盖用户对个别题命中与否的手动标记，
    每次写盘前重新读取裁决文件并覆盖对应记录的 normalized_match。"""
    try:
        if not _MANUAL_ADJUDICATIONS.exists():
            return
        adj = json.loads(_MANUAL_ADJUDICATIONS.read_text(encoding="utf-8"))
        for item in results:
            a = adj.get(str(item.get("task_id")))
            if a and "normalized_match" in a:
                item["normalized_match"] = bool(a["normalized_match"])
    except Exception as exc:
        print(json.dumps({"event": "adjudication_merge_error", "error": str(exc)},
                         ensure_ascii=False), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--url", default="http://127.0.0.1:2025")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--level", type=int, choices=[1, 2, 3])
    parser.add_argument("--connect-timeout", type=int, default=20)
    parser.add_argument("--read-timeout", type=int, default=600)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--auto-approve", action=argparse.BooleanOptionalAction, default=True,
                        help="在评测进程内自动表决并恢复本题审批（默认开启）")
    parser.add_argument("--approval-max-retries", type=int, default=3,
                        help="同一审批 ID 的最大尝试次数（默认 3）")
    parser.add_argument("--retry-from", type=Path)
    parser.add_argument("--retry-status", action="append", default=[])
    parser.add_argument("--resume-from", type=Path, help="从已有结果文件续跑，跳过其中已完成的 task_id")
    args = parser.parse_args()

    if args.metadata.suffix.lower() == ".csv":
        with args.metadata.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    elif pd is not None:
        rows = pd.read_parquet(args.metadata).to_dict("records")
    else:
        raise RuntimeError("读取 parquet 需要 pandas；请改用同目录的 metadata.parquet.csv")
    previous_results = []
    attempts = _load_attempts(args.output)
    # 判死跳过：技术失败（无答案）累计 >= 3 次的题不再进重试队列，
    # 避免反复空转（如 chess 深度搜索超时）。答完的题（finished，无论对错）不计数。
    MAX_DEAD_ATTEMPTS = 3
    if args.resume_from:
        previous = write_checkpoint(args.resume_from)
        previous_results = list(previous.get("results", []))
        # A task is complete once it produced a final answer, whether matched or not.
        # Records with final_status finished + an answer are not retried; only
        # genuine technical failures (no answer) remain in the retry queue.
        completed_ids = {
            item.get("task_id")
            for item in previous_results
            if item.get("final_status") == "finished"
            and (not item.get("error") or item.get("answer"))
        }
        # 判死跳过（与 --retry-from 分支同规则）：无答案的技术失败累计 >= 3 次的题
        # 不再进重试队列。watchdog 实际走 --resume-from，所以这里也必须过滤。
        dead_ids = {
            item.get("task_id")
            for item in previous_results
            if item.get("final_status") != "finished"
            and int(attempts.get(str(item.get("task_id")), 0)) >= MAX_DEAD_ATTEMPTS
        }
        for dead_id in sorted(dead_ids):
            print(json.dumps({"event": "retry_dead", "task_id": dead_id,
                              "attempts": attempts.get(dead_id), "skip": True},
                             ensure_ascii=False), flush=True)
        rows = [row for row in rows if str(row["task_id"]) not in completed_ids
                and str(row["task_id"]) not in dead_ids]
    if args.retry_from:
        previous = json.loads(args.retry_from.read_text(encoding="utf-8"))
        statuses = set(args.retry_status or ["interrupted"])
        retry_ids = {
            item["task_id"]
            for item in previous.get("results", [])
            if (item.get("final_status") in statuses or item.get("error"))
            and int(attempts.get(str(item["task_id"]), 0)) < MAX_DEAD_ATTEMPTS
        }
        rows = [row for row in rows if str(row["task_id"]) in retry_ids]
        dead_ids = {
            item["task_id"]
            for item in previous.get("results", [])
            if (item.get("final_status") in statuses or item.get("error"))
            and int(attempts.get(str(item["task_id"]), 0)) >= MAX_DEAD_ATTEMPTS
        }
        for dead_id in sorted(dead_ids):
            print(json.dumps({"event": "retry_dead", "task_id": dead_id,
                              "attempts": attempts.get(dead_id), "skip": True},
                             ensure_ascii=False), flush=True)
    if args.level:
        rows = [row for row in rows if int(row["Level"]) == args.level]
    # 队列按级别 L1→L2→L3 排序（同级别保持 metadata 原顺序）
    rows.sort(key=lambda r: int(r["Level"]))
    if args.limit:
        rows = rows[: args.limit]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    results = previous_results
    # 中断题的续跑索引：task_id -> 原 thread_id（仅 error 且带有效 thread 的记录）。
    # 余额/配额中断后 resume 复用原 thread，从 LangGraph checkpoint 接着跑，
    # 而不是新建 thread 整题重跑。
    resume_threads = {}
    for item in previous_results:
        tid = item.get("task_id")
        old_thread = item.get("thread_id")
        if (item.get("final_status") != "finished"
                and old_thread and item.get("final_status") in {None, "running", "interrupted", "error"}):
            resume_threads[str(tid)] = old_thread
    jobs = {}
    print(json.dumps({"event": "evaluation_started", "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                      "retry_tasks": len(rows), "preserved_records": len(results),
                      "resume_threads": len(resume_threads),
                      "workers": max(1, args.workers), "auto_approve": args.auto_approve},
                     ensure_ascii=False), flush=True)
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        for index, row in enumerate(rows, 1):
            jobs[executor.submit(
                run_task, args.url, args.workdir, row,
                (args.connect_timeout, args.read_timeout),
                args.auto_approve, args.approval_max_retries,
                resume_threads.get(str(row["task_id"])), args.output,
            )] = index
        for completed in as_completed(jobs):
            index = jobs[completed]
            try:
                result = completed.result()
            except Exception as exc:
                row = rows[index - 1]
                result = {
                    "task_id": str(row["task_id"]),
                    "level": int(row["Level"]),
                    "file_name": str(row.get("file_name") or ""),
                    "question": str(row["Question"]),
                    "expected": str(row.get("Final answer") or ""),
                    "answer": "",
                    "normalized_match": False,
                    "thread_id": None,
                    "final_status": None,
                    "changed_files": None,
                    "first_text_s": None,
                    "total_s": None,
                    "event_count": 0,
                    "tool_events": 0,
                    "error": f"worker: {type(exc).__name__}: {exc}",
                    "resumed": False,
                }
            existing_index = next(
                (i for i, item in enumerate(results) if item.get("task_id") == result["task_id"]),
                None,
            )
            if existing_index is None:
                results.append(result)
            else:
                results[existing_index] = result
            # 判死计数：真实失败（无答案、非网络层原因）累计；
            # 瞬时技术错误（HTTP 500/429/连接/流挂起等）不判死——它们是暂时性
            # 故障，恢复后重试完全可能成功，累计判死会误杀（如限流 3 次后
            # 永久跳过）。finished（无论命中与否）视为跑完，重置计数。
            tid = str(result["task_id"])
            _temporary = _is_temporary_error(result.get("error"))
            if result.get("final_status") in {None, "error", "interrupted", "running"} and not _temporary:
                attempts[tid] = int(attempts.get(tid, 0)) + 1
                if attempts[tid] >= MAX_DEAD_ATTEMPTS:
                    print(json.dumps({"event": "retry_dead", "task_id": tid,
                                      "attempts": attempts[tid]}, ensure_ascii=False), flush=True)
            elif result.get("final_status") == "finished":
                attempts[tid] = 0
            _save_attempts(args.output, attempts)
            print(
                json.dumps(
                    {
                        "index": index,
                        "total": len(rows),
                        "task_id": result["task_id"],
                        "level": result["level"],
                        "match": result["normalized_match"],
                        "status": result["final_status"],
                        "total_s": result["total_s"],
                        "error": result["error"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

            _apply_manual_adjudications(results)
            checkpoint = {
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "dataset": str(args.metadata),
                "n_completed": len(results),
                "results": results,
            }
            checkpoint = write_checkpoint(args.output, checkpoint)
            results = checkpoint["results"]

    _apply_manual_adjudications(results)
    summary = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "dataset": str(args.metadata),
        "n": len(results),
        "matched": sum(item["normalized_match"] for item in results),
        "by_level": {
            str(level): {
                "n": sum(item["level"] == level for item in results),
                "matched": sum(item["level"] == level and item["normalized_match"] for item in results),
            }
            for level in (1, 2, 3)
        },
        "results": results,
    }
    summary = write_checkpoint(args.output, summary)
    print(json.dumps({key: summary[key] for key in ("n", "matched", "by_level")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
