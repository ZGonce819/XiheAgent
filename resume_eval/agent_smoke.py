"""Run one read-only Agent task against the real SSE endpoint."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:2025")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fixture", help="Workspace fixture path as seen by the Agent backend.")
    args = parser.parse_args()

    fixture = args.fixture or str((Path(__file__).parent / "fixtures" / "agent_smoke").resolve())
    prompt = "Read facts.txt in the current workspace. What is the release code? Reply with the code only. Do not change files."
    payload = {
        "content": prompt,
        "backend_type": "local",
        "local_work_dir": fixture,
    }
    started = time.perf_counter()
    events: list[dict] = []
    current_event = "message"
    thread_id = None
    first_text_s = None
    error = None
    try:
        with requests.post(
            f"{args.url}/api/v1/threads/stream-message",
            json=payload,
            stream=True,
            timeout=(10, 180),
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines(decode_unicode=True):
                if not line:
                    continue
                if line.startswith("event: "):
                    current_event = line[7:]
                elif line.startswith("data: "):
                    elapsed = round(time.perf_counter() - started, 3)
                    data = json.loads(line[6:])
                    thread_id = thread_id or data.get("thread_id")
                    if current_event == "text_delta" and first_text_s is None:
                        first_text_s = elapsed
                    if current_event == "error":
                        error = data.get("message")
                    events.append({"event": current_event, "elapsed_s": elapsed, "data": data})
                    if current_event == "done":
                        break
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    streamed_text = "".join(str(item["data"].get("content") or "") for item in events if item["event"] == "text_delta")
    final_thread = None
    if thread_id:
        try:
            detail = requests.get(f"{args.url}/api/v1/threads/{thread_id}", timeout=10)
            detail.raise_for_status()
            final_thread = detail.json()
        except Exception as exc:
            error = error or f"Could not fetch final thread: {type(exc).__name__}: {exc}"
    answers = [
        str(chunk.get("text") or "")
        for message in (final_thread or {}).get("messages", [])
        if message.get("author") == "agent"
        for chunk in message.get("chunks", [])
        if chunk.get("kind") == "text" and chunk.get("text") != "任务计划"
    ]
    answer = answers[-1] if answers else ""
    changed_files = (final_thread or {}).get("changedFiles")
    result = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "fixture": str(fixture),
        "prompt": prompt,
        "expected": "CEDAR-27",
        "thread_id": thread_id,
        "first_text_s": first_text_s,
        "total_s": round(time.perf_counter() - started, 3),
        "event_count": len(events),
        "event_types": sorted(set(item["event"] for item in events)),
        "streamed_text": streamed_text,
        "answer": answer,
        "final_status": (final_thread or {}).get("status"),
        "changed_files": changed_files,
        "correct": answer.strip() == "CEDAR-27" and (final_thread or {}).get("status") == "finished" and changed_files == [] and error is None,
        "error": error,
        "events": events,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "events"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
