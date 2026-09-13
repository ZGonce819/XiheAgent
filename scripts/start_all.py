"""Xihe 项目一键启动脚本。

同时启动后端（Uvicorn）和前端（Vite），并在退出时统一清理子进程与端口占用。
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from scripts.stop_all import stop_ports


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def start_process(
    name: str,
    command: list[str],
    cwd: Path,
    env: dict[str, str] | None = None,
) -> subprocess.Popen:
    """启动一个子进程并返回 Popen 对象。

    Args:
        name: 进程名称，用于日志输出。
        command: 启动命令及参数列表。
        cwd: 进程工作目录。
        env: 可选的额外环境变量，会合并到当前环境变量中。

    Returns:
        已启动的 ``subprocess.Popen`` 对象。
    """
    print(f"启动 {name}: {' '.join(command)}")
    process_env = os.environ.copy()
    if env:
        process_env.update(env)
    return subprocess.Popen(command, cwd=str(cwd), env=process_env)


def wait_for_port(host: str, port: int, timeout: float = 60) -> bool:
    """等待指定端口可连接。

    Args:
        host: 主机地址。
        port: 端口号。
        timeout: 最大等待秒数。

    Returns:
        ``True`` 表示端口已就绪，``False`` 表示超时。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def main() -> None:
    """同时启动 Xihe 后端和前端。

    后端运行在 http://127.0.0.1:2024。
    前端运行在 http://127.0.0.1:3000。
    等待后端就绪后再启动前端，避免代理连接被拒绝。
    按 Ctrl+C 会同时停止两个进程。
    """
    backend = start_process(
        "backend",
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app:app",
            "--host",
            "127.0.0.1",
            "--port",
            "2024",
        ],
        PROJECT_ROOT,
    )

    print("等待后端就绪...")
    if not wait_for_port("127.0.0.1", 2024):
        backend.terminate()
        raise SystemExit("后端未在规定时间内就绪，已退出")
    print("后端已就绪，启动前端...")

    frontend = start_process(
        "frontend",
        [
            "node",
            ".\\node_modules\\vite\\bin\\vite.js",
            "dev",
            "--host",
            "127.0.0.1",
            "--port",
            "3000",
        ],
        PROJECT_ROOT / "frontend",
        env={"VITE_DASHBOARD_API_BASE_URL": "http://127.0.0.1:2024"},
    )

    try:
        while True:
            backend_code = backend.poll()
            frontend_code = frontend.poll()
            if backend_code is not None:
                raise SystemExit(f"backend exited: {backend_code}")
            if frontend_code is not None:
                raise SystemExit(f"frontend exited: {frontend_code}")
            time.sleep(1)
    except KeyboardInterrupt:
        print("正在停止服务...")
    finally:
        # 先停止 start_all.py 直接创建的子进程
        for process in (frontend, backend):
            if process.poll() is None:
                process.terminate()
        for process in (frontend, backend):
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        # 再按端口兜底清理，处理 IDE 停止按钮或 Windows 进程树残留
        stopped = stop_ports((2024, 3000))
        if stopped:
            print(f"已清理残留服务进程：{stopped}")
        sys.exit(0)


if __name__ == "__main__":
    main()
