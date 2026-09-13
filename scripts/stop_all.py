"""按端口清理残留服务进程的辅助工具。

提供基于 Windows PowerShell / netstat 的端口占用查询与进程终止能力，
供 start_all.py 和独立命令行调用使用。
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Iterable

DEFAULT_PORTS = (2024, 3000)


def _run_powershell(command: str) -> subprocess.CompletedProcess[str]:
    """执行一段只用于进程查询/停止的 PowerShell 命令。

    不使用 shell=True，避免命令被额外解释；所有动态值只来自固定端口数字。

    Args:
        command: 要执行的 PowerShell 命令字符串。

    Returns:
        ``subprocess.CompletedProcess`` 对象，包含 stdout 和 stderr。
    """
    return subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
    )


def find_pids_by_ports(ports: Iterable[int] = DEFAULT_PORTS) -> list[int]:
    """根据端口查找监听进程 PID。

    start_all.py 默认占用：
    - 2024：FastAPI/Uvicorn 后端
    - 3000：Vite 前端

    Args:
        ports: 要查询的端口集合，默认为项目使用的端口。

    Returns:
        去重并排序后的 PID 列表。
    """
    port_list = ",".join(str(port) for port in ports)
    command = (
        f"Get-NetTCPConnection -LocalPort {port_list} -ErrorAction SilentlyContinue "
        "| Where-Object { $_.State -eq 'Listen' } "
        "| Select-Object -ExpandProperty OwningProcess -Unique"
    )
    result = _run_powershell(command)

    pids: list[int] = []
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                pids.append(int(line))
            except ValueError:
                continue
    if pids:
        return sorted(set(pids))

    # 某些 Windows/IDE 组合下 Get-NetTCPConnection 可能查不到，
    # 但 netstat 仍然能看到 LISTENING 记录，所以这里做一次兜底解析。
    netstat = subprocess.run(
        ["netstat", "-ano"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
    )
    if netstat.returncode != 0:
        return []
    port_set = {int(port) for port in ports}
    for line in netstat.stdout.splitlines():
        if "LISTENING" not in line:
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        local_address = parts[1]
        pid_text = parts[-1]
        match = re.search(r":(\d+)$", local_address)
        if not match:
            continue
        if int(match.group(1)) not in port_set:
            continue
        try:
            pids.append(int(pid_text))
        except ValueError:
            continue
    return sorted(set(pids))


def stop_pids(pids: Iterable[int]) -> list[int]:
    """停止指定 PID，并返回实际发出停止命令的 PID 列表。
    
    Args:
        pids: 待停止的进程 ID 集合。
    
    Returns:
        实际发出停止命令的 PID 列表（已去重排序）。
    """
    stopped: list[int] = []
    for pid in sorted(set(pids)):
        if pid <= 0:
            continue
        command = f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue"
        _run_powershell(command)
        stopped.append(pid)
    return stopped


def stop_ports(ports: Iterable[int] = DEFAULT_PORTS) -> list[int]:
    """停止指定端口上的监听进程。
    
    Args:
        ports: 要清理的端口集合，默认为项目使用的端口。
    
    Returns:
        实际停止的 PID 列表。
    """
    pids = find_pids_by_ports(ports)
    return stop_pids(pids)


def main() -> None:
    """清理 xihe-agent 项目后端和前端服务端口。"""
    ports = DEFAULT_PORTS
    stopped = stop_ports(ports)
    if stopped:
        print(f"已停止端口 {ports} 对应进程：{stopped}")
    else:
        print(f"端口 {ports} 当前没有监听进程。")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
