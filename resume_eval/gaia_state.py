"""Shared, atomic GAIA checkpoints for the evaluator and approval monitor."""
from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from utils.message_utils import final_answer_error


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def iter_sse(response):
    """Parse SSE boundaries, comments and multiline data, including trailing EOF."""
    event, data = "message", []
    for raw in response.iter_lines(decode_unicode=True):
        line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        if not line:
            if data:
                yield event, json.loads("\n".join(data))
            event, data = "message", []
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        yield event, json.loads("\n".join(data))


def validate_record(record: dict) -> dict:
    record = dict(record)
    has_answer = bool(str(record.get("answer") or "").strip())
    # 有最终答案但带 error（如 validator 否决后修复耗尽）→ 视为跑完（未命中），
    # 不降级为 error；只有"没有答案"的技术失败才算 error。
    if record.get("final_status") == "finished" and record.get("error") and not has_answer:
        record["final_status"] = "error"
    if record.get("final_status") == "finished":
        error = final_answer_error(str(record.get("answer") or ""))
        if error:
            record.update(final_status="error", error=f"invalid_final_answer: {error}")
    validation_failed = any(marker in str(record.get("error") or "") for marker in (
        "最终答案校验失败", "最终答案校验器不可用", "invalid_final_answer", "缺少有效校验凭证",
    ))
    if record.get("final_status") != "finished" or validation_failed:
        record["normalized_match"] = False
    record["execution_succeeded"] = record.get("final_status") == "finished" and not record.get("error")
    return record


@contextmanager
def checkpoint_lock(path: Path):
    """Cross-process lock shared by checkpoint and recovery writers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(path.suffix + ".lock").open("a+b") as lock:
        lock.seek(0, os.SEEK_END)
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        deadline = time.monotonic() + 15
        while True:
            try:
                lock.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Checkpoint locked: {path.name}") from None
                time.sleep(0.1)
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def write_checkpoint(path: Path, snapshot: dict | None = None, updates: dict | None = None):
    """Merge recoveries on every write, regardless of other pending approvals."""
    from resume_eval.gaia_eval import normalized_match

    with checkpoint_lock(path):
        recovered_path = path.with_suffix(".recovered.json")
        recovered = read_json(recovered_path, {})
        if updates:
            recovered.update(updates)
            atomic_json(recovered_path, recovered)
        disk = read_json(path, {})
        current = dict(disk if snapshot is None else snapshot)
        if not current:
            return current
        rows = []
        # 人工裁决最高优先级：manual_adjudications.json 与 gaia_state.py 同目录，
        # 存在时其 normalized_match 覆盖自动重算结果（防止写盘重算把用户裁定打回）。
        manual_adj = {}
        _man_path = Path(__file__).with_name("manual_adjudications.json")
        if _man_path.exists():
            try:
                manual_adj = json.loads(_man_path.read_text(encoding="utf-8"))
            except Exception:
                manual_adj = {}
        for source in current.get("results", []):
            row = dict(source)
            row.update(recovered.get(row.get("thread_id"), {}))
            row = validate_record(row)
            row["normalized_match"] = normalized_match(row.get("answer"), row.get("expected"))
            row = validate_record(row)
            _tid = str(row.get("task_id") or "")
            _adj = manual_adj.get(_tid)
            if _adj and "normalized_match" in _adj:
                row["normalized_match"] = bool(_adj["normalized_match"])
            rows.append(row)
        current["results"] = rows
        current["n_recorded"] = len(rows)
        current["n_finished"] = sum(r.get("final_status") == "finished" and not r.get("error") for r in rows)
        current["matched"] = sum(r["normalized_match"] for r in rows)
        current["status_counts"] = {
            status: sum(r.get("final_status") == status for r in rows)
            for status in ("finished", "running", "interrupted", "error", "idle")
        }
        current["by_level"] = {
            str(level): {
                "n": sum(r.get("level") == level for r in rows),
                "matched": sum(r.get("level") == level and r["normalized_match"] for r in rows),
            } for level in (1, 2, 3)
        }
        if current != disk:
            atomic_json(path, current)
        return current
