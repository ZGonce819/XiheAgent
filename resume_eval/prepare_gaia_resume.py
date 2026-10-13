"""Build a clean GAIA checkpoint, excluding unfinished/error records."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from gaia_eval import normalized_match


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    source = json.loads(args.source.read_text(encoding="utf-8"))
    recovered_path = args.source.with_suffix(".recovered.json")
    recovered = json.loads(recovered_path.read_text(encoding="utf-8")) if recovered_path.exists() else {}
    results = []
    for raw_item in source.get("results", []):
        item = dict(raw_item)
        update = recovered.get(item.get("thread_id"))
        if update and update.get("final_status") == "finished":
            item.update(update)
            item["normalized_match"] = normalized_match(item.get("answer"), item.get("expected"))
        if item.get("final_status") == "finished" and not item.get("error"):
            results.append(item)
    summary = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "dataset": source.get("dataset", ""),
        "n_completed": len(results),
        "results": results,
    }
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"kept": len(results), "excluded": len(source.get("results", [])) - len(results)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
