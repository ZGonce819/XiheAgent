"""Reconcile saved GAIA answers; optionally retire runs from a stopped API instance."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.env_config import load_environment
from database.mysql import ConnectionManager
from resume_eval.gaia_state import read_json, write_checkpoint


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--retire-orphans-before", help="仅在旧 API 已退出时使用；ISO 时间之前残留 running 记为失败，不重跑")
    args = parser.parse_args()
    backup = args.results.with_suffix(".before-repair.json")
    if not backup.exists():
        shutil.copy2(args.results, backup)
    snapshot = write_checkpoint(args.results)
    updates = {}
    if args.retire_orphans_before:
        cutoff = datetime.fromisoformat(args.retire_orphans_before)
        if cutoff.tzinfo is None:
            parser.error("截止时间必须带时区")
        pending = read_json(args.results.parent / "data/pending_command_approvals.json", {})
        load_environment()
        db = ConnectionManager()
        try:
            for row in snapshot.get("results", []):
                tid = row.get("thread_id")
                if row.get("final_status") != "running" or tid in pending:
                    continue
                saved = db.fetch_one("SELECT latest_run_status, updated_at FROM threads WHERE thread_id=%s", (tid,))
                if not saved or saved["latest_run_status"] != "running":
                    continue
                updated = datetime.fromisoformat(saved["updated_at"])
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=UTC)
                if updated >= cutoff:
                    continue
                error = "orphaned_run: 旧 API 进程已退出，遗留 running；需要重新执行，未计为成功"
                now = datetime.now(UTC).isoformat()
                # Keep original run history; mark its terminal state explicitly.
                db.execute("UPDATE threads SET latest_run_status='failed', updated_at=%s WHERE thread_id=%s AND latest_run_status='running'", (now, tid))
                db.execute("UPDATE runs SET status='failed', error=%s, finished_at=%s WHERE thread_id=%s AND status='running'", (error, now, tid))
                updates[tid] = {"final_status": "error", "error": error, "answer": row.get("answer", "")}
        finally:
            db.close()
    snapshot = write_checkpoint(args.results, updates=updates)
    print(json.dumps({"retired_orphans": len(updates), "finished": snapshot["n_finished"], "matched": snapshot["matched"], "status_counts": snapshot["status_counts"], "backup": str(backup)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
