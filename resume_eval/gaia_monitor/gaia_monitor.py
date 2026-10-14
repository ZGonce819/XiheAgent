# -*- coding: utf-8 -*-
"""GAIA 2023 validation 重试队列监控服务（只读，标准库，零依赖）。
提供：
  GET /                监控面板 HTML
  GET /api/status      json 顶层统计 + running 题详情 + 最近完成
  GET /api/activity    retry log / API err 日志尾部（实时活动流）
"""
import io
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = r"D:\Bemyself\Python\xihe"
JSON_PATH = os.path.join(BASE, "gaia_mimo_delegation_v4_retry_20261007.json")
LOG_RETRY = os.path.join(BASE, "logs", "ordered_evaluate.log")
# serve 的 app 日志（httpx/agent 中间件）统一落 backend.log；旧 serve 曾把 stderr
# 重定向进 gaia_api_rotation_20261007.err，新 serve 不再写该文件，故改读 backend.log。
LOG_API = os.path.join(BASE, "logs", "backend.log")
HERE = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(HERE, "gaia_monitor.html")
STATE_PATH = os.path.join(HERE, "gaia_monitor_state.json")
TOTAL = 165

RETRY_TAIL = 22
API_TAIL = 50
API_LINE_MAX = 260
QUESTION_MAX = 420
ACTIVE_WINDOW_MS = 300000   # 保留：updatedAt 超 15 分钟视为异常卡住提示（不再直接判暂停）

_EVAL_ALIVE = {"ts": 0.0, "val": True}


def evaluate_alive():
    """evaluate 执行器进程是否存活（30s 缓存，PowerShell 查询）。"""
    now = time.time()
    if now - _EVAL_ALIVE["ts"] < 30:
        return _EVAL_ALIVE["val"]
    try:
        import subprocess
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'evaluate\\.py' }).Count"],
            capture_output=True, text=True, timeout=15,
            creationflags=0x08000000)  # CREATE_NO_WINDOW
        n = int((out.stdout or "0").strip() or "0")
        _EVAL_ALIVE.update(ts=now, val=n > 0)
    except Exception:
        _EVAL_ALIVE.update(ts=now)  # 查询失败保持上次值
    return _EVAL_ALIVE["val"]
_STATE_LOCK = threading.Lock()


def current_task_id():
    """读 evaluate 的 retry log 尾部，取最近一次 task_started 的 task_id。

    evaluate 为 workers=1 串行：最近被拉起的题就是当前真正在执行的题，
    用于把"运行中"记录里的假活跃（排队/待续跑的 running 残留）区分出来。
    """
    try:
        lines = tail_lines(LOG_RETRY, 200)
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("event") == "task_started" and obj.get("task_id"):
                return obj["task_id"]
    except Exception:
        pass
    return None


def tail_lines(path, n):
    """二进制尾部读取最后 n 行（处理大日志，避免整读）。"""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                return []
            pos = size
            chunk = 8192
            buf = b""
            guard = 0
            while pos > 0 and buf.count(b"\n") <= n and guard < 40:
                pos = max(0, pos - chunk)
                f.seek(pos)
                buf = f.read(chunk) + buf
                guard += 1
            lines = buf.decode("utf-8", errors="replace").splitlines()
            return lines[-n:]
    except Exception as exc:
        return ["[读取失败: %s]" % exc]


def read_json():
    try:
        with io.open(JSON_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        return {"error": str(exc), "results": [], "n_finished": 0,
                "matched": 0, "status_counts": {}, "by_level": {}}


def thread_detail(thread_full):
    """轮询轻量状态；仅终态读取消息摘要，失败返回 None。"""
    try:
        import urllib.request
        url = "http://127.0.0.1:2026/api/v1/threads/%s" % thread_full
        with urllib.request.urlopen(url + "/status", timeout=6) as resp:
            t = json.load(resp)
        detail = {}
        if t.get("status") in {"finished", "error", "failed", "cancelled"}:
            try:
                with urllib.request.urlopen(url, timeout=6) as resp:
                    detail = json.load(resp)
            except Exception:
                pass  # Preserve status even if terminal history times out.
        msgs = detail.get("messages") or []
        recent = []
        for m in msgs[-6:]:
            texts = []
            for c in (m.get("chunks") or []):
                k = c.get("kind")
                if k == "text":
                    texts.append(str(c.get("text") or ""))
                elif k in ("tool_call", "tool_use", "tool_result"):
                    texts.append("[工具] " + str(c.get("name") or c.get("title") or c.get("tool_name") or ""))
            body = " ".join(t for t in texts if t)
            recent.append({
                "author": m.get("author") or "?",
                "ts": str(m.get("timestamp") or "")[:19],
                "text": body[:400],
                "full": body[:30000],  # 弹窗查看完整用；30k 上限防面板轮询传输过载
            })
        return {
            "created_at_ms": detail.get("createdAt"),
            "updated_at_ms": t.get("updatedAt"),
            "status": t.get("status"),
            "activity_fingerprint": t.get("activityFingerprint"),
            "pending_approval": t.get("pendingApproval"),
            "messages_count": len(msgs) if detail else None,
            "model": detail.get("model"),
            "recent_messages": recent,
        }
    except Exception:
        return None


def fmt_elapsed(ms):
    s = max(0, int(ms / 1000))
    return "%dm%02ds" % (s // 60, s % 60) if s >= 60 else "%ds" % s


def _load_state():
    try:
        with io.open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state):
    try:
        with io.open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
    except Exception:
        pass


IDLE_TIMEOUT_MS = 90000   # API updatedAt 超 90s 未刷新 → 强制判 inactive，兜底绿点卡死/已结束未翻状态


def update_timers(running):
    """计时状态机（v2，墙钟口径修正）：
    - started_at_ms：这题第一次被面板见到 active=True 的时刻（不再用 thread.created_at_ms，
      线程可能几小时前就建好等着，用它会把排队/空置时间全算进 elapsed）。
      排队中（active=False）时 started_at_ms=None，前端显示 '--'。
    - accumulated_ms：活跃段累计时长；每轮 active 时 += 轮询间隔，inactive 时冻结。
    - elapsed_ms：一律返回 accumulated_ms，不再走 now - started_at_ms 墙钟分支。
    - 兜底：API updatedAt 超 IDLE_TIMEOUT_MS 未刷新，即使 evaluate 还活着也强制 active=False，
      避免模型已结束/卡住但绿点还亮着、elapsed 空涨。
    - 题不在 running（完成/失败/消失）：标记 ended_ts 并保留 3 分钟用于显示。
    返回 {task_id: detail}。
    """
    with _STATE_LOCK:
        state = _load_state()
        now = int(time.time() * 1000)
        seen = set()
        out = {}
        for r in running:
            tid = r.get("task_id", "")
            if not tid:
                continue
            seen.add(tid)
            thread_full = r.get("thread_id") or ""
            td = thread_detail(thread_full) or {}
            upd = td.get("updated_at_ms")
            api_status = td.get("status")
            # active 判定：evaluate 执行器活着 且 该题是当前正在执行的题（最近 task_started）。
            # 串行 evaluate 同一时刻只处理一题；其余 running 记录是中断残留
            # （排队/待续跑），标 queued 且不计时，避免面板显示"多题同时在跑"。
            eval_ok = evaluate_alive()
            cur = current_task_id()
            # current_task_id 依赖 evaluate 的 stdout 重定向进 retry log；手动
            # Start-Process 未重定向时日志断更取不到 task_started。fallback 用
            # 线程 API status=running 判当前活跃题，避免 running 记录被误标"中断/暂停"。
            # 额外：log 里最近 task_started 可能已结束（evaluate 已开始下一题但新
            # task_started 还没写进 log），此时 cur 不在 running 列表里，也要 fallback。
            running_ids = {r.get("task_id", "") for r in running}
            if eval_ok and (cur is None or cur not in running_ids):
                for r2 in running:
                    td2 = thread_detail(r2.get("thread_id") or "") or {}
                    if td2.get("status") == "running":
                        cur = r2.get("task_id")
                        break
            active = bool(eval_ok and api_status == "running")
            queued = bool(eval_ok and not active and api_status in {"idle", "interrupted"})

            # 兜底：API 90s 没刷新 → 强制 inactive。但 thread status=running 时
            # serve 明确认为它还在跑（可能模型正在长推理/工具调用，SSE 不推 chunk），
            # 此时不应因 updatedAt 静止就判停；只有 status 不是 running 才兜底。
            if active and api_status != "running" and upd is not None and (now - upd) > IDLE_TIMEOUT_MS:
                active = False
                queued = False

            suspicious = bool(eval_ok and upd is not None
                              and (now - upd) > ACTIVE_WINDOW_MS)
            st = state.get(tid)
            if st is None or not isinstance(st, dict):
                st = {
                    "started_at_ms": now if active else None,
                    "accumulated_ms": 0,
                    "active_since_ms": now if active else None,
                    "last_seen_ms": now,
                }
            else:
                st.setdefault("accumulated_ms", 0)
                prev_active = bool(st.get("active"))
                prev_last_seen = st.get("last_seen_ms") or now
                if active:
                    if prev_active:
                        # 连续活跃：累计本轮轮询间隔
                        st["accumulated_ms"] += now - prev_last_seen
                    else:
                        # 从 inactive 切到 active：开始新的活跃段
                        # started_at_ms 只在第一次真正开跑时记一次；之后切回 active 不重置
                        if st.get("started_at_ms") is None:
                            st["started_at_ms"] = now
                        st["active_since_ms"] = now
                else:
                    st["active_since_ms"] = None
                st["last_seen_ms"] = now
            st["active"] = active
            st["queued"] = queued
            st["last_updated_ms"] = upd
            state[tid] = st
            out[tid] = {
                "started_at_ms": st.get("started_at_ms"),
                "accumulated_ms": st["accumulated_ms"],
                # 一律走活跃段累计；active 时 accumulated 已含到 now 的轮询间隔
                "elapsed_ms": st["accumulated_ms"],
                "active": active,
                "queued": queued,
                "active_since_ms": st.get("active_since_ms"),
                "last_seen_ms": st["last_seen_ms"],
                "suspicious": suspicious,
            }
        # 清理已结束的任务（保留 3 分钟记录用于显示最近结束）
        stale = [t for t in state if t not in seen and state[t].get("ended_ts", 0) + 180000 < now]
        for t in stale:
            del state[t]
        for t in state:
            if t not in seen:
                state[t]["ended_ts"] = state[t].get("ended_ts", now)
                state[t]["active"] = False
        _save_state(state)
        return out


def build_recent(results, n=8):
    """从 retry log 尾部按文件顺序提取最近完成记录（行序=时间序）。
    完成记录行形如 {"index":..,"task_id":..,"level":..,"match":..,"status":..,"total_s":..}。
    再用 json 记录补 question。
    evaluate 重启若覆盖主 log，主 log 暂无完成行时回退读最近备份，避免"最近结果"为空。
    """
    q_by_id = {}
    err_by_id = {}
    match_by_id = {}
    status_by_id = {}
    total_s_by_id = {}
    for r in results:
        tid = r.get("task_id", "")
        q_by_id[tid] = r.get("question") or ""
        err_by_id[tid] = r.get("error") or r.get("stream_error") or ""
        # 用 JSON 现算 match/status/total_s 覆盖 log 行快照（log 行是完成时写盘前的值，
        # 人工裁定/重算/重跑后以 JSON 为准，保证 recent 与 stats 口径一致）
        match_by_id[tid] = bool(r.get("normalized_match"))
        status_by_id[tid] = r.get("final_status") or ""
        total_s_by_id[tid] = r.get("total_s") or 0

    def _parse(lines):
        out = []
        seen = set()
        for line in reversed(lines):
            if '"task_id"' not in line or '"status"' not in line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            if not isinstance(o, dict) or not o.get("task_id"):
                continue
            if o.get("status") not in ("finished", "error", "success"):
                continue
            tid = o["task_id"]
            if tid in seen:
                continue
            seen.add(tid)
            _match = match_by_id.get(tid, bool(o.get("match")))
            _status = status_by_id.get(tid) or o.get("status")
            _total_s = total_s_by_id.get(tid) or o.get("total_s")
            out.append({
                "task_id": tid[:8],
                "level": o.get("level"),
                "match": _match,
                "status": _status,
                "total_s": _total_s,
                "question": (q_by_id.get(tid) or "")[:90],
                # miss 分流：技术失败（error 非空，可重跑）vs 答错（能力边界）
                "miss_type": ("technical" if err_by_id.get(tid)
                              else "capability") if not _match else None,
            })
            if len(out) >= n:
                break
        out.reverse()  # 旧 → 新
        return out

    recs = _parse(tail_lines(LOG_RETRY, 1500))
    if not recs:
        import glob as _glob
        baks = sorted(_glob.glob(os.path.join(os.path.dirname(LOG_RETRY),
                                              os.path.basename(LOG_RETRY) + ".bak_*")),
                      reverse=True)
        if baks:
            recs = _parse(tail_lines(baks[0], 1500))
    return recs


def build_status():
    d = read_json()
    results = d.get("results") or []
    # 全部从 results 现算，避免读 json 顶层过时快照（n_finished/status_counts
    # 由历史脚本回填，不随评测推进刷新，会造成 81+11≠88 之类口径漂移）。
    finished = [r for r in results if r.get("final_status") == "finished"]
    matched = [r for r in results if r.get("normalized_match")]
    missed = [r for r in finished if not r.get("normalized_match")]
    # miss 分流统计：技术失败（error/stream_error 非空 → 可重跑）vs 答错（能力边界）
    missed_technical = [r for r in missed if (r.get("error") or r.get("stream_error"))]
    missed_capability = [r for r in missed if not (r.get("error") or r.get("stream_error"))]
    running = [r for r in results if r.get("final_status") == "running"]
    error = [r for r in results if r.get("final_status") == "error"]
    interrupted = [r for r in results if r.get("final_status") == "interrupted"]
    remaining = len(error) + len(interrupted) + len(running)

    by_level = {}
    for lvl, info in (d.get("by_level") or {}).items():
        by_level[int(lvl)] = {
            "n": info.get("n", 0),
            "matched": info.get("matched", 0),
        }

    run_detail = []
    timers = update_timers(running)
    for r in running:
        q = r.get("question") or ""
        td = thread_detail(r.get("thread_id") or "") or {}
        te = r.get("tool_events")
        tool_events = te if isinstance(te, list) else []
        tm = timers.get(r.get("task_id", ""), {})
        item = {
            "task_id": r.get("task_id", ""),
            "level": r.get("level"),
            "thread_id": (r.get("thread_id") or "")[:8],
            "thread_full": r.get("thread_id") or "",
            "question": q[:QUESTION_MAX] + ("…" if len(q) > QUESTION_MAX else ""),
            "question_full": q,  # 弹窗查看完整问题用
            "created_at_ms": td.get("created_at_ms"),
            "updated_at_ms": td.get("updated_at_ms"),
            "status": td.get("status"),
            "messages_count": td.get("messages_count"),
            "model": td.get("model"),
            "timer": {
                "started_at_ms": tm.get("started_at_ms"),
                "accumulated_ms": tm.get("accumulated_ms", 0),
                "elapsed_ms": tm.get("elapsed_ms", 0),
                "active": bool(tm.get("active")),
                "queued": bool(tm.get("queued")),
                "active_since_ms": tm.get("active_since_ms"),
                "last_seen_ms": tm.get("last_seen_ms"),
            },
            "total_s": r.get("total_s"),
            "event_count": r.get("event_count"),
            "tool_events": tool_events[-3:],
            "recent_messages": (td or {}).get("recent_messages") or [],
        }
        run_detail.append(item)

    recent = build_recent(results)

    return {
        "stats": {
            "total": TOTAL,
            "finished": len(finished),
            "matched": len(matched),
            "missed": len(missed),
            "missed_technical": len(missed_technical),
            "missed_capability": len(missed_capability),
            "remaining": remaining,
            "error": len(error),
            "running": len(running),
            "interrupted": len(interrupted),
        },
        "by_level": by_level,
        "running": run_detail,
        "recent": recent,
        "json_error": d.get("error"),
    }


def build_activity():
    """按线程聚合活动流：每个线程只显示其起始（第一条）关键事件。

    - eval 日志（LOG_RETRY）：线程级事件（task_started/vote/finished/recovery），
      每线程保留最早一条（task_started 优先）。
    - api 日志（LOG_API）：只提取线程关键事件（server 创建/意图分类/技术方案/
      final_response/提交答案/委派/错误），过滤 httpx 请求与消息清洗等噪音，
      每线程保留最早一条。
    - 最终按时间升序（最新在底部）。
    """
    import datetime
    thread_rows: dict[str, dict] = {}

    def _eval_thread_id(ln: str) -> str | None:
        m = re.search(r'"task_id":\s*"([0-9a-f-]{36})"', ln)
        if m:
            return f"task:{m.group(1)}"
        m = re.search(r'"thread_id":\s*"([0-9a-f-]{36})"', ln)
        return f"thr:{m.group(1)}" if m else None

    def _api_thread_id(ln: str) -> str | None:
        m = re.search(r"xihe-agent-stream-([0-9a-f-]{36})", ln)
        if m:
            return m.group(1)
        m = re.search(r"thread_id=([0-9a-f-]{36})", ln)
        return m.group(1) if m else None

    for ln in tail_lines(LOG_RETRY, RETRY_TAIL):
        # 活动流只展示"线程开始"：task_started 与 evaluation_started。
        # finished/error/vote/recovery 等结果与过程记录不刷屏（结果见"最近结果"区）。
        if '"event": "task_started"' not in ln and '"event": "evaluation_started"' not in ln:
            continue
        ts = None
        try:
            obj = json.loads(ln)
            t = obj.get("time") or ""
            if t:
                dt = datetime.datetime.fromisoformat(t.replace("Z", "+00:00"))
                ts = dt.timestamp() * 1000
        except Exception:
            pass
        tid = _eval_thread_id(ln)
        if tid:
            prev = thread_rows.get(tid)
            if prev is None or (prev.get("t") is None or (ts is not None and ts < prev["t"])):
                thread_rows[tid] = {"t": ts, "src": "r", "text": ln[:API_LINE_MAX], "impt": 2}
        else:
            thread_rows[f"eval:{hash(ln) & 0xffff}"] = {"t": ts, "src": "r", "text": ln[:API_LINE_MAX], "impt": 2}

    _NOISE = ("httpx", "HTTP Request", "message_sanitize", "tool_sanitize", "record_event")
    for ln in tail_lines(LOG_API, API_TAIL):
        if any(k in ln for k in _NOISE):
            continue
        ts = None
        m = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", ln)
        if m:
            try:
                dt = datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                ts = dt.timestamp() * 1000
            except Exception:
                pass
        tid = _api_thread_id(ln)
        if not tid:
            continue
        impt = 3 if any(k in ln for k in ("ERROR", "WARNING", "Traceback", "final_response", "quota exhausted")) else 2
        prev = thread_rows.get(tid)
        if prev is None or (prev.get("t") is None or (ts is not None and ts < prev["t"])):
            thread_rows[tid] = {"t": ts, "src": "a", "text": ln[:API_LINE_MAX], "impt": impt}

    rows = list(thread_rows.values())
    # 升序（时间从旧到新，最新在底部），与聊天流阅读习惯一致。
    rows.sort(key=lambda x: x["t"] if x["t"] is not None else 0)
    return {"lines": rows}


def build_api_log(limit: int = 40):
    """返回 backend.log 最近的 API 实时活动行（模型调用/搜索/重试/清洗/错误）。

    与活动流（每线程一条起始）互补：这里展示后端实时在做的具体动作。
    升序（最新在底部）。
    """
    import datetime
    rows = []
    for ln in tail_lines(LOG_API, limit):
        ts = None
        m = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", ln)
        if m:
            try:
                dt = datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                ts = dt.timestamp() * 1000
            except Exception:
                pass
        impt = 3 if any(k in ln for k in ("ERROR", "WARNING", "Traceback", "500 ", "429", "quota exhausted", "final_response")) else 1
        if "[server]" in ln or "[backends]" in ln or "task_router" in ln or "task_intent" in ln:
            impt = 2
        rows.append({"t": ts, "text": ln[:API_LINE_MAX], "impt": impt})
    rows.sort(key=lambda x: x["t"] if x["t"] is not None else 0)
    return {"lines": rows}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            try:
                with io.open(HTML_PATH, encoding="utf-8") as f:
                    html = f.read()
                self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            except Exception as exc:
                self._send(500, ("监控页读取失败: %s" % exc).encode("utf-8"), "text/plain; charset=utf-8")
        elif path == "/api/status":
            body = json.dumps(build_status(), ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
        elif path == "/api/activity":
            body = json.dumps(build_activity(), ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
        elif path == "/api/apilog":
            body = json.dumps(build_api_log(), ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def log_message(self, *args):
        pass


def main():
    port = 8787
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("GAIA monitor: http://127.0.0.1:%d  (Ctrl+C 停止)" % port, flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
