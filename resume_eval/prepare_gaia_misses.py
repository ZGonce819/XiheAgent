"""Seed a separate retry run with existing hits and an attachment-only workspace."""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from resume_eval.gaia_eval import normalized_match
from resume_eval.gaia_state import atomic_json, validate_record, write_checkpoint


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    args = parser.parse_args()
    manifest_path = args.output.with_suffix(".retry-manifest.json")
    if args.output.exists() or args.workdir.exists() or manifest_path.exists():
        parser.error("Use new output and workspace paths to preserve previous runs")
    source = json.loads(args.source.read_text(encoding="utf-8"))
    sidecar = args.source.with_suffix(".recovered.json")
    updates = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.exists() else {}
    hits = {}
    for raw in source["results"]:
        record = validate_record({**raw, **updates.get(raw.get("thread_id"), {})})
        if (record.get("final_status") == "finished" and not record.get("error")
                and normalized_match(record.get("answer"), record.get("expected"))):
            hits[record["task_id"]] = record
    with args.metadata.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    ids = {r["task_id"] for r in rows}
    if len(ids) != len(rows) or not set(hits) <= ids:
        raise ValueError("Metadata IDs are duplicated or do not match existing results")
    misses = [r for r in rows if r["task_id"] not in hits]
    attachments = {}
    for row in misses:
        name = (row.get("file_name") or "").strip()
        if not name:
            continue
        if Path(name).name != name:
            raise ValueError("Attachment must be a plain filename")
        attachment = args.metadata.parent / name
        if not attachment.is_file() or attachment.is_symlink():
            raise FileNotFoundError(attachment)
        attachments[name] = attachment
    args.workdir.mkdir(parents=True)
    for name, attachment in attachments.items():
        shutil.copy2(attachment, args.workdir / name)
    manifest = {
        "created_at": datetime.now(UTC).isoformat(), "source": str(args.source.resolve()),
        "output": str(args.output.resolve()), "workdir": str(args.workdir.resolve()),
        "preserved_hits": len(hits), "retry_count": len(misses),
        "task_ids": [r["task_id"] for r in misses], "attachments": sorted(attachments),
    }
    atomic_json(manifest_path, manifest)
    write_checkpoint(args.output, {
        "timestamp_utc": manifest["created_at"], "dataset": str(args.metadata),
        "n": len(hits), "results": list(hits.values()),
    })
    print(json.dumps({"preserved_hits": len(hits), "retry_count": len(misses),
                      "attachments": len(attachments)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
