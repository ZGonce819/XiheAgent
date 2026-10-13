"""Review and resume approvals for a GAIA run without restarting its API."""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests

from config.app_config import get_settings
from resume_eval.gaia_state import iter_sse, write_checkpoint
from services.approval_vote import _pending_commands, vote_on_approval


# ── 评测安全硬拦截 ─────────────────────────────────────────────
# 模型在 GAIA 评测里可能尝试从外部下载基准答案数据集（WebVoyager 等已知
# 答案库）来作弊。这类命令**不走投票**（投票可能放行，如 2/3 票），直接拒绝，
# 让模型换一条合规路径继续，也避免外部下载命令网络挂起导致线程卡死。
_GAIA_CHEAT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("gaia_web.jsonl", "命令尝试下载 GAIA 基准答案数据集（GAIA_web.jsonl）"),
    ("webvoyager", "命令尝试访问 WebVoyager 项目（含 GAIA 答案数据集）"),
    ("raw.githubusercontent.com", "命令尝试从外部代码库下载数据文件"),
    ("cdn.jsdelivr.net", "命令尝试从外部 CDN 下载数据文件"),
    ("ghproxy", "命令尝试通过代理镜像下载外部数据文件"),
    ("githubusercontent", "命令尝试从 GitHub 原始内容域下载文件"),
)


def _cheat_block(commands) -> str | None:
    """命中作弊特征返回拦截原因；下载动作 + GAIA/答案特征同时出现才拦截。"""
    low_text = "\n".join(c.lower() for c in commands)
    dl_hint = any(k in low_text for k in (
        "invoke-webrequest", "iwr ", "curl", "wget", "download", "bitsadmin", "requests.get", "urllib"
    ))
    gaia_hint = any(k in low_text for k in ("gaia", "webvoyager", "答案", "answer"))
    if not (dl_hint and gaia_hint):
        return None
    for pattern, reason in _GAIA_CHEAT_PATTERNS:
        if pattern in low_text:
            return f"评测安全拦截：{reason}，直接拒绝，请换用本地已有材料或合规途径作答"
    if "answer" in low_text or "答案" in low_text or "jsonl" in low_text:
        return "评测安全拦截：命令疑似下载答案/数据文件，直接拒绝，请换用合规途径作答"
    return None


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def emit(**record):
    print(json.dumps({"time": datetime.now(UTC).isoformat(), **record}, ensure_ascii=True), flush=True)


def _recovery_state(url: str, pending: dict) -> str:
    """Read this thread's current state before retrying an exact approval."""
    response = requests.get(f"{url}/api/v1/threads/{pending['threadId']}/status", timeout=20)
    response.raise_for_status()
    thread = response.json()
    status = thread.get("status")
    if status in {"error", "failed", "cancelled"}:
        raise RuntimeError("审批恢复后服务端任务失败")
    if status in {"running", "finished"}:
        return status
    current = thread.get("pendingApproval")
    if current and current.get("threadId") == pending["threadId"]:
        if current.get("id") != pending["id"]:
            return "changed"
        return "claimed" if current.get("claimed") else "pending"
    raise RuntimeError("无法确认当前线程的待审批操作，未重复提交")


def recover(url: str, pending: dict, instruction: str):
    state = _recovery_state(url, pending)
    if state != "pending":
        return state
    commands = _pending_commands(pending)
    block = _cheat_block(commands)
    if block:
        decision = "reject"
        outcome_state = {"commands": list(commands), "approve": 0, "reject": 1,
                         "decision": decision, "reason": block}
    else:
        outcome = vote_on_approval(pending, instruction=instruction)
        if not outcome.decided:
            raise RuntimeError("审批评审未形成决定，稍后重试")
        decision = "approve" if outcome.approved else "reject"
        outcome_state = outcome.as_state_dict()
    emit(event="vote", thread_id=pending["threadId"], approval_id=pending["id"],
         action=decision, outcome=outcome_state)
    # Voting can take minutes. Do not submit a stale decision after another
    # worker has claimed this approval or resumed the thread.
    state = _recovery_state(url, pending)
    if state != "pending":
        return state
    errors = []
    try:
        with requests.post(
            f"{url}/api/v1/threads/{pending['threadId']}/approval",
            json={"decision": decision, "approval_id": pending["id"]},
            stream=True, timeout=(20, 120),
        ) as response:
            response.raise_for_status()
            done = False
            for event, data in iter_sse(response):
                if event == "error":
                    errors.append(str(data.get("message") or data.get("detail") or data))
                if event == "done":
                    done = True
                    break
            if errors:
                raise RuntimeError("approval_sse_error: " + "; ".join(errors))
            if not done:
                raise RuntimeError("审批恢复流提前结束，未确认恢复结果")
    except requests.RequestException:
        # A read timeout does not cancel the API worker. Confirm acceptance
        # before allowing the caller to retry; never POST again here.
        state = _recovery_state(url, pending)
        if errors or state == "pending":
            raise
        emit(event="recovery_pending", thread_id=pending["threadId"], status=state)
        return state
    state = _recovery_state(url, pending)
    if state == "pending":
        raise RuntimeError("审批恢复流结束但审批仍未恢复")
    emit(event="resumed" if state in {"finished", "changed"} else "recovery_pending",
         thread_id=pending["threadId"], status=state)
    return state


class RecoveryAttempts:
    """Retry an exact approval with backoff, without permanently skipping failures."""
    def __init__(self, maximum=3):
        self.maximum = maximum
        self.attempts = {}
        self.next_try = {}

    def eligible(self, approval_id, now=None):
        now = time.monotonic() if now is None else now
        return (self.attempts.get(approval_id, 0) < self.maximum
                and now >= self.next_try.get(approval_id, 0))

    def start(self, approval_id, now=None):
        now = time.monotonic() if now is None else now
        count = self.attempts.get(approval_id, 0) + 1
        self.attempts[approval_id] = count
        self.next_try[approval_id] = now + min(300, 30 * 2 ** (count - 1))

    def finish(self, approval_id, now=None):
        now = time.monotonic() if now is None else now
        count = self.attempts.get(approval_id, 1)
        self.next_try[approval_id] = now + min(300, 30 * 2 ** (count - 1))


def main():
    from resume_eval.gaia_eval import extract_answer

    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--url", default="http://127.0.0.1:2025")
    parser.add_argument("--started-after", type=int, default=0, help="Run start, Unix milliseconds")
    parser.add_argument("--merge-only", action="store_true", help="仅合并已有恢复结果，不调用 API 或模型")
    parser.add_argument("--max-retries", type=int, default=3)
    args = parser.parse_args()
    if args.merge_only:
        snapshot = write_checkpoint(args.results)
        emit(event="merged", finished=snapshot.get("n_finished"), matched=snapshot.get("matched"))
        return
    if args.started_after <= 0:
        parser.error("监控模式必须提供 --started-after，避免审批其他批次")
    pending_path = get_settings().data_dir / "pending_command_approvals.json"
    recovered_path = args.results.with_suffix(".recovered.json")
    recovered = read_json(recovered_path, {})
    attempts = RecoveryAttempts(max(1, args.max_retries))
    tracked = set()
    futures = {}
    inflight_ids = {}
    with ThreadPoolExecutor(max_workers=2) as executor:
        while True:
            snapshot = write_checkpoint(args.results)
            records = snapshot.get("results", [])
            tracked.update(item["thread_id"] for item in records if item.get("final_status") in {"interrupted", "running"} and item.get("thread_id"))
            for tid, future in list(futures.items()):
                if future.done():
                    try:
                        future.result()
                    except Exception as exc:
                        emit(event="recovery_error", thread_id=tid, error=f"{type(exc).__name__}: {exc}")
                    attempts.finish(inflight_ids.pop(tid))
                    del futures[tid]
            for tid, pending in read_json(pending_path, {}).items():
                try:
                    created = datetime.fromisoformat(pending.get("createdAt", "")).timestamp() * 1000
                except (ValueError, TypeError):
                    continue
                if created < args.started_after:
                    continue
                if len(futures) >= 2:
                    break
                if tid in futures or not attempts.eligible(pending.get("id")):
                    continue
                try:
                    response = requests.get(f"{args.url}/api/v1/threads/{tid}/status", timeout=10)
                    response.raise_for_status()
                    thread = response.json()
                    if thread.get("status") in {"running", "finished"}:
                        continue
                    current = thread.get("pendingApproval")
                    if not current or current.get("id") != pending.get("id") or current.get("threadId") != tid:
                        continue
                    if current.get("claimed"):
                        emit(event="needs_attention", thread_id=tid, reason="审批仍被占用，未重复执行")
                        continue
                    # Read the question only when an exact approval needs review.
                    response = requests.get(f"{args.url}/api/v1/threads/{tid}", timeout=10)
                    response.raise_for_status()
                    thread = response.json()
                    instruction = "\n".join(
                        str(chunk.get("text") or "")
                        for message in thread.get("messages", []) if message.get("author") == "user"
                        for chunk in message.get("chunks", [])
                    )
                    if thread.get("createdAt", 0) < args.started_after or not instruction.startswith("Answer the following GAIA benchmark question."):
                        continue
                    attempts.start(pending["id"])
                    tracked.add(tid)
                    futures[tid] = executor.submit(recover, args.url, pending, instruction)
                    inflight_ids[tid] = pending["id"]
                    emit(event="review_started", thread_id=tid, approval_id=pending["id"])
                except Exception as exc:
                    emit(event="scan_error", thread_id=tid, error=str(exc))
            changed = False
            for tid in tracked:
                if tid in recovered and recovered[tid].get("final_status") == "finished":
                    continue
                try:
                    response = requests.get(f"{args.url}/api/v1/threads/{tid}/status", timeout=10)
                    response.raise_for_status()
                    thread = response.json()
                    if thread.get("status") in {"finished", "error"} and not isinstance(thread.get("finalAnswer"), str):
                        response = requests.get(f"{args.url}/api/v1/threads/{tid}", timeout=10)
                        response.raise_for_status()
                        thread = response.json()
                    if thread.get("status") == "finished":
                        recovered[tid] = {"answer": extract_answer(thread), "final_status": "finished", "error": None}
                        changed = True
                        emit(event="recovered_answer", thread_id=tid)
                    elif thread.get("status") == "running":
                        update = {"final_status": "running", "error": None}
                        if recovered.get(tid) != update:
                            recovered[tid] = update
                            changed = True
                    elif thread.get("status") == "interrupted":
                        update = {"final_status": "interrupted", "error": None}
                        if recovered.get(tid) != update:
                            recovered[tid] = update
                            changed = True
                    elif thread.get("status") == "error" and tid not in read_json(pending_path, {}):
                        update = {"answer": extract_answer(thread), "final_status": "error", "error": "服务端任务失败，请检查对应运行日志"}
                        if recovered.get(tid) != update:
                            recovered[tid] = update
                            changed = True
                except requests.RequestException:
                    pass
            if changed:
                write_checkpoint(args.results, updates=recovered)
            # Recovered answers are already merged, even if other approvals fail.
            if snapshot.get("n") == 165 and not futures:
                pendings = read_json(pending_path, {})
                pending_ids = set(pendings) & tracked
                if not pending_ids:
                    if not any(r.get("final_status") == "running" for r in records):
                        snapshot = write_checkpoint(args.results)
                        emit(event="complete", n=165, matched=snapshot["matched"])
                        return
                elif all(attempts.attempts.get(pendings[tid]["id"], 0) >= attempts.maximum for tid in pending_ids):
                    emit(event="needs_attention", thread_ids=sorted(pending_ids), reason="审批恢复重试已耗尽，其他完成答案已保存")
                    return
            time.sleep(10)


if __name__ == "__main__":
    main()
