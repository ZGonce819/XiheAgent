"""Read-only audit of interrupted/running GAIA records and persisted evidence."""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    snapshot = json.loads((ROOT / 'gaia_mimo_full_20261007.json').read_text(encoding='utf-8'))
    recovered = json.loads((ROOT / 'gaia_mimo_full_20261007.recovered.json').read_text(encoding='utf-8'))
    pending = json.loads((ROOT / 'data/pending_command_approvals.json').read_text(encoding='utf-8'))
    monitor = []
    for line in (ROOT / 'gaia_approval_monitor_20261007.log').read_text(encoding='utf-8').splitlines():
        try:
            monitor.append(json.loads(line))
        except ValueError:
            pass
    log = (ROOT / 'logs/agent-runs.log').read_text(encoding='utf-8').splitlines()
    db = None
    try:
        from config.env_config import load_environment
        from database.mysql import ConnectionManager
        load_environment()
        db = ConnectionManager()
    except Exception as exc:
        print('DB unavailable:', type(exc).__name__)
    report = []
    for item in snapshot['results']:
        if item['final_status'] not in ('interrupted', 'running'):
            continue
        tid = item['thread_id']
        record = {k: item.get(k) for k in ('task_id', 'thread_id', 'question', 'final_status')}
        record['recovered'] = recovered.get(tid)
        p = pending.get(tid)
        record['pending'] = p
        record['monitor'] = [m for m in monitor if m.get('thread_id') == tid]
        record['log'] = [line for line in log if tid in line]
        if db:
            record['thread'] = db.fetch_one('SELECT thread_id, latest_run_status, updated_at FROM threads WHERE thread_id=%s', (tid,))
            record['runs'] = db.fetch_all('SELECT status, started_at, finished_at, error FROM runs WHERE thread_id=%s ORDER BY started_at', (tid,))
            record['events'] = db.fetch_all('SELECT title, status, detail, updated_at FROM run_events WHERE thread_id=%s ORDER BY updated_at DESC LIMIT 5', (tid,))
        report.append(record)
        print(json.dumps({'task':item['task_id'], 'saved':item['final_status'], 'recovered':tid in recovered, 'pending':bool(p), 'monitor':dict(Counter(m['event'] for m in record['monitor'])), 'db':record.get('thread'), 'last_log':record['log'][-1:]}, ensure_ascii=False))
    path = ROOT / 'resume_eval/gaia_state_diagnosis_20261007.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    if db:
        db.close()
    print('report:', path)


if __name__ == '__main__':
    main()
