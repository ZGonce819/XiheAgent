"""Run the user-approved recovery queue with three workers, preserving existing hits."""
from __future__ import annotations

import csv
import json
import sys
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / '.artifacts' / 'gaia-key-rotation'))

ORDER = '''42d4198c 4d0aa727 da52d699 e142056d 0bb3b44a b2c257e0
9f41b083 2dfc4c37 73c1b9fe 1dcc160f e2d69698 a56f1527 708b99c5
65638e28 72c06643 8d46b8d6 0a3cd321 d5141ca5 5f982798 3da89939
f2feb6a4 5b2a14e8 853c8244 6359a0b1 cca530fc 0bdb7c40 872bfbb1
bec74516 384d0dd8 0512426f 50f58759 7a4a336d 0b260a57 ded28325'''.split()


def run_parallel(items, fn, workers=3, should_stop=lambda: False):
    items = iter(items)
    workers = max(1, workers)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = set()
        exhausted = False
        while futures or not exhausted:
            while not exhausted and len(futures) < workers and not should_stop():
                try:
                    item = next(items)
                except StopIteration:
                    exhausted = True
                    break
                futures.add(pool.submit(fn, item))
            if not futures:
                break
            completed, futures = wait(futures, return_when=FIRST_COMPLETED)
            for future in completed:
                yield future.result()
            if should_stop():
                exhausted = True


def main():
    import evaluate
    import rotation
    from resume_eval.gaia_eval import run_task
    from resume_eval.gaia_state import atomic_json, checkpoint_lock

    evaluate._acquire_single_instance_lock()
    rotation.install(rotation.load_local_keys())
    output = ROOT / 'gaia_mimo_delegation_v4_retry_20261007.json'
    workdir = ROOT / 'gaia_workspaces' / 'delegation_v4_retry_20261007'
    with (ROOT / 'gaia_data/2023/validation/metadata.parquet.csv').open(encoding='utf-8-sig', newline='') as handle:
        metadata = list(csv.DictReader(handle))
    rows = []
    for prefix in ORDER:
        matches = [r for r in metadata if r['task_id'].startswith(prefix)]
        if len(matches) != 1:
            raise RuntimeError(f'Ambiguous or missing task: {prefix}')
        rows.append(matches[0])
    with checkpoint_lock(output):
        initial = json.loads(output.read_text(encoding='utf-8'))
        backup = output.with_name(output.name + '.before_ordered_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '.bak')
        atomic_json(backup, initial)
    print(json.dumps({'event': 'ordered_queue', 'tasks': [r['task_id'] for r in rows], 'workers': 3}, ensure_ascii=False), flush=True)
    current = json.loads(output.read_text(encoding='utf-8'))
    pending = []
    for index, row in enumerate(rows, 1):
        existing = next((r for r in current['results'] if r['task_id'] == row['task_id']), {})
        if existing.get('normalized_match'):
            print(json.dumps({'event': 'skip_existing_hit', 'task_id': row['task_id'], 'index': index}), flush=True)
        else:
            pending.append((index, row))

    def solve(item):
        index, row = item
        # Never inject the expected answer or old guesses into the solving prompt.
        return index, run_task('http://127.0.0.1:2026', str(workdir), row, (20, 1200), output_path=output)

    pause_file = ROOT / 'logs' / 'ordered_evaluate.pause'
    for index, result in run_parallel(pending, solve, workers=3, should_stop=pause_file.exists):
        with checkpoint_lock(output):
            current = json.loads(output.read_text(encoding='utf-8'))
            current['results'] = [result if r['task_id'] == result['task_id'] else r for r in current['results']]
            current['timestamp_utc'] = datetime.now(timezone.utc).isoformat()
            current['matched'] = sum(bool(r.get('normalized_match')) for r in current['results'])
            current['n_finished'] = sum(r.get('final_status') == 'finished' and not r.get('error') for r in current['results'])
            current['status_counts'] = {s: sum(r.get('final_status') == s for r in current['results']) for s in ('finished', 'running', 'error', 'interrupted', 'idle')}
            current['by_level'] = {str(l): {'n': sum(r.get('level') == l for r in current['results']), 'matched': sum(r.get('level') == l and bool(r.get('normalized_match')) for r in current['results'])} for l in (1, 2, 3)}
            atomic_json(output, current)
        print(json.dumps({'index': index, 'total': len(rows), 'task_id': result['task_id'], 'level': result['level'], 'match': result['normalized_match'], 'status': result['final_status'], 'total_s': result['total_s'], 'error': result['error']}, ensure_ascii=False), flush=True)
    print(json.dumps({'event': 'ordered_queue_finished'}), flush=True)


if __name__ == '__main__':
    main()
