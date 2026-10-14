"""GAIA 重试 watchdog：检测执行器/API 未运行则自动拉起（--resume-from 续跑）。

由 Windows 计划任务每分钟调用一次。幂等：运行中则什么都不做。
中断（kill 进程、关机会话等）后，本脚本会在下一分钟自动恢复执行，
直到 92 题队列全部跑完。
"""

from __future__ import annotations

import datetime
import subprocess
import sys
import os

ROOT = r"D:\Bemyself\Python\xihe"
PYTHON = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
LOG_DIR = os.path.join(ROOT, "logs")
LOG = os.path.join(LOG_DIR, "gaia_watchdog.log")

EVAL_ARGS = [
    ".artifacts\\gaia-key-rotation\\evaluate.py",
    "--metadata", "gaia_data\\2023\\validation\\metadata.parquet.csv",
    "--workdir", "gaia_workspaces\\delegation_v4_retry_20261007",
    "--output", "gaia_mimo_delegation_v4_retry_20261007.json",
    "--resume-from", "gaia_mimo_delegation_v4_retry_20261007.json",
    "--url", "http://127.0.0.1:2026",
    "--workers", "1",
    "--auto-approve",
]


def log_msg(msg: str) -> None:
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")


def count_eval_processes() -> int:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'gaia-key-rotation\\\\evaluate.py' }).Count"],
        capture_output=True, text=True, timeout=30,
    )
    try:
        return int(out.stdout.strip() or "0")
    except ValueError:
        return 0


def api_alive() -> bool:
    import urllib.request
    try:
        urllib.request.urlopen("http://127.0.0.1:2026/health/ready", timeout=4)
        return True
    except Exception:
        return False


def start_api() -> None:
    out = open(os.path.join(LOG_DIR, "gaia_api_rotation_20261007.log"), "ab")
    err = open(os.path.join(LOG_DIR, "gaia_api_rotation_20261007.err"), "ab")
    subprocess.Popen(
        [PYTHON, ".artifacts\\gaia-key-rotation\\serve.py"],
        cwd=ROOT, stdout=out, stderr=err,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    log_msg("API 未运行，已拉起 serve.py")


def start_eval() -> None:
    out = open(os.path.join(LOG_DIR, "gaia_retry_rotation_20261007.log"), "ab")
    err = open(os.path.join(LOG_DIR, "gaia_retry_rotation_20261007.err"), "ab")
    subprocess.Popen(
        [PYTHON] + EVAL_ARGS,
        cwd=ROOT, stdout=out, stderr=err,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    log_msg("执行器未运行，已拉起 evaluate.py（--resume-from）")


def main() -> None:
    eval_count = count_eval_processes()
    if eval_count > 0 and api_alive():
        log_msg(f"执行器运行中（{eval_count} 个）且 API 正常，无需动作")
        return
    if not api_alive():
        start_api()
        import time
        time.sleep(10)
    if count_eval_processes() == 0:
        start_eval()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        log_msg(f"watchdog 异常: {type(exc).__name__}: {exc}")
