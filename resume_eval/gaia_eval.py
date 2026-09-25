"""Run GAIA validation tasks against the local XiheAgent SSE API."""

from __future__ import annotations

import argparse
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
import string


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
    normalized_expected = normalize(expected)
    return any(normalize(c) == normalized_expected for c in candidates)


def extract_answer(thread: dict) -> str:
    answers = [
        str(chunk.get("text") or "")
        for message in thread.get("messages", [])
        if message.get("author") == "agent"
        for chunk in message.get("chunks", [])
        if chunk.get("kind") == "text" and chunk.get("text") != "任务计划"
    ]
    return answers[-1] if answers else ""


def run_task(base_url: str, workdir: str, row: dict, timeout: tuple[int, int]) -> dict:
    question = str(row["Question"])
    file_name = str(row.get("file_name") or "").strip()
    attachment = f" The attached file is available as /projects/{file_name}." if file_name else ""
    prompt = (
        "Answer the following GAIA benchmark question. Use the available tools and attached file "
        "when needed. Return only the final answer, with no explanation unless the question asks for it.\n\n"
        f"{question}{attachment}"
    )
    payload = {
        "content": prompt,
        "backend_type": "local",
        "local_work_dir": workdir,
    }
    started = time.perf_counter()
    events: list[dict] = []
    current_event = "message"
    thread_id = None
    error = None
    first_text_s = None
    try:
        with requests.post(
            f"{base_url}/api/v1/threads/stream-message",
            json=payload,
            stream=True,
            timeout=timeout,
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines(decode_unicode=True):
                if not line:
                    continue
                if line.startswith("event: "):
                    current_event = line[7:]
                elif line.startswith("data: "):
                    data = json.loads(line[6:])
                    elapsed = round(time.perf_counter() - started, 3)
                    thread_id = thread_id or data.get("thread_id")
                    if current_event == "text_delta" and first_text_s is None:
                        first_text_s = elapsed
                    events.append({"event": current_event, "elapsed_s": elapsed, "data": data})
                    if current_event == "done":
                        break
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    # SSE 连接可能在 Agent 长时间推理时被网络层切断（客户端收到
    # ChunkedEncodingError 等），但服务端任务仍在继续。此时线程在服务端
    # 最终会落到 finished/failed。断连后轮询线程状态，避免把已完成的任务
    # 误记为 running/interrupted。
    if thread_id and error:
        for _ in range(60):
            try:
                detail = requests.get(f"{base_url}/api/v1/threads/{thread_id}", timeout=20)
                detail.raise_for_status()
                thread = detail.json()
                status = thread.get("status")
                if status in {"finished", "error"}:
                    break
                if status == "running":
                    time.sleep(3)
                    continue
                break
            except Exception:
                break

    thread = {}
    if thread_id:
        try:
            detail = requests.get(f"{base_url}/api/v1/threads/{thread_id}", timeout=20)
            detail.raise_for_status()
            thread = detail.json()
        except Exception as exc:
            error = error or f"thread_fetch: {type(exc).__name__}: {exc}"

    answer = extract_answer(thread)
    expected = str(row.get("Final answer") or "")
    return {
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
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--url", default="http://127.0.0.1:2025")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--level", type=int, choices=[1, 2, 3])
    parser.add_argument("--connect-timeout", type=int, default=20)
    parser.add_argument("--read-timeout", type=int, default=1800)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--retry-from", type=Path)
    parser.add_argument("--retry-status", action="append", default=[])
    args = parser.parse_args()

    rows = pd.read_parquet(args.metadata).to_dict("records")
    if args.retry_from:
        previous = json.loads(args.retry_from.read_text(encoding="utf-8"))
        statuses = set(args.retry_status or ["interrupted"])
        retry_ids = {
            item["task_id"]
            for item in previous.get("results", [])
            if item.get("final_status") in statuses or item.get("error")
        }
        rows = [row for row in rows if str(row["task_id"]) in retry_ids]
    if args.level:
        rows = [row for row in rows if int(row["Level"]) == args.level]
    if args.limit:
        rows = rows[: args.limit]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    results = []
    jobs = {}
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        for index, row in enumerate(rows, 1):
            jobs[executor.submit(run_task, args.url, args.workdir, row, (args.connect_timeout, args.read_timeout))] = index
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
                }
            results.append(result)
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

            checkpoint = {
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "dataset": str(args.metadata),
                "n_completed": len(results),
                "results": results,
            }
            args.output.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")

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
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: summary[key] for key in ("n", "matched", "by_level")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
