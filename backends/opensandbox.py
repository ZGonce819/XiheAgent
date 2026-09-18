"""OpenSandbox 远程沙盒后端实现。

基于 OpenSandbox 的内核级隔离沙盒，提供比 LocalShellBackend 更强的安全边界。
适用于企业多租户场景，每个 Agent 任务运行在独立的容器化沙盒中。

主要功能：
- 命令执行：通过 OpenSandbox API 在远程容器中执行 shell 命令；
- 文件操作：上传/下载文件到沙盒容器；
- 沙盒管理：创建、连接、列出运行中的沙盒实例。

部署前提：
    需要在 Linux 环境下部署 OpenSandbox Server，详见项目 README。
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from datetime import timedelta
from pathlib import PurePosixPath
from typing import cast

import httpx
from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox
from opensandbox import SandboxSync
from opensandbox.config import ConnectionConfigSync
from opensandbox.models import WriteEntry

from backends.command_policy import evaluate_command

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)

if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

SyncPollingInterval = float | Callable[[float], float]
PollingStrategy = Callable[[float], float]

# 默认沙盒镜像
DEFAULT_SANDBOX_IMAGE = (
    "sandbox-registry.cn-zhangjiakou.cr.aliyuncs.com/opensandbox/code-interpreter:v1.0.2"
)


def _to_text(content: bytes | str) -> str:
    """把待写入沙盒的内容统一转成字符串。

    OpenSandbox 的文件写入接口按文本编码传输，非 UTF-8 字节无法还原时退化为
    字节字面量，宁可写入不可读内容也不要让整批上传失败。

    Args:
        content: 待写入内容。

    Returns:
        UTF-8 文本。
    """
    if not isinstance(content, bytes):
        return str(content)
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return str(content)


class OpenSandboxBackend(BaseSandbox):
    """符合 SandboxBackendProtocol 协议的 OpenSandbox 实现。

    该实现继承了 BaseSandbox 的所有文件操作方法，
    并仅使用 OpenSandbox 的 API 实现了 execute()、download_files() 和 upload_files() 方法。
    """

    def __init__(
        self,
        *,
        sandbox: SandboxSync,
        timeout: int = 30 * 60,
        sync_polling_interval: SyncPollingInterval = 0.1,
    ) -> None:
        """创建一个包装已有 OpenSandbox 沙盒的后端实例。

        Args:
            sandbox: 要包装的现有 OpenSandbox 沙盒实例。
            timeout: 调用 ``execute()`` 且未显式指定 ``timeout`` 时使用的默认命令超时时间（秒）。
            sync_polling_interval: 在同步执行路径上，轮询 OpenSandbox 命令完成状态的间隔时间（秒）；
                也可以是一个可调用对象，接收已执行的秒数并返回下一次轮询的延迟时间。
        """
        logger.info("正在初始化 OpenSandbox，沙盒 ID: %s", sandbox.id)
        self._sandbox = sandbox
        self._default_timeout = timeout

        if callable(sync_polling_interval):
            polling_strategy = cast("PollingStrategy", sync_polling_interval)
        else:

            def polling_strategy(_elapsed: float) -> float:
                return sync_polling_interval

        self._sync_polling_interval = polling_strategy
        logger.debug("OpenSandbox 初始化完成，默认超时时间=%d秒", timeout)

    @property
    def id(self) -> str:
        """返回 OpenSandbox 沙盒的 ID。"""
        sandbox_id = self._sandbox.id
        logger.debug("获取沙盒 ID: %s", sandbox_id)
        return sandbox_id

    @property
    def sandbox_id(self) -> str:
        """返回底层 OpenSandbox 沙盒的 ID，供注册表记录使用。"""
        return self._sandbox.id

    def destroy(self) -> None:
        """终止远程沙盒并释放本地资源。

        调用后此 backend 实例不可再使用。
        失败时仅记录警告，不抛出异常，以免阻断上层清理流程。
        """
        sandbox_id = self._sandbox.id
        try:
            self._sandbox.destroy()
            logger.info("已销毁沙盒：%s", sandbox_id)
        except Exception:
            logger.warning("销毁沙盒失败：%s", sandbox_id, exc_info=True)

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """在沙盒内部执行一条 Shell 命令。

        Args:
            command: 要执行的 Shell 命令字符串。
            timeout: 等待命令完成的最大时间（秒）；为 None 时使用实例默认超时。

        Returns:
            包含命令输出和退出码的结构化响应。
        """
        policy = evaluate_command(command, "sandbox")
        if policy.action == "deny":
            return ExecuteResponse(
                output=f"命令被拒绝：{policy.reason}", exit_code=126, truncated=False
            )
        effective_timeout = timeout if timeout is not None else self._default_timeout
        logger.debug(
            "准备执行命令：%s...（超时时间=%d秒）", command[:100], effective_timeout
        )
        return self._execute_command(command, timeout=effective_timeout)

    def _execute_command(
        self,
        command: str,
        *,
        timeout: int,
    ) -> ExecuteResponse:
        """使用 OpenSandbox 的 API 执行命令。

        Args:
            command: 要执行的 Shell 命令字符串。
            timeout: 命令超时时间（秒）。

        Returns:
            包含命令输出和退出码的结构化响应。
        """
        try:
            logger.debug("通过 OpenSandbox API 执行命令：%s", command)
            result = self._sandbox.commands.run(command)
            logger.debug("命令执行完成，退出码：%s", result.exit_code)

            stdout = ""
            stderr = ""

            if result.logs.stdout:
                stdout = "\n".join([log.text for log in result.logs.stdout])
                logger.debug("命令标准输出长度：%d 字符", len(stdout))

            if result.logs.stderr:
                stderr = "\n".join([log.text for log in result.logs.stderr])
                logger.debug("命令标准错误长度：%d 字符", len(stderr))

            output = stdout
            if stderr and stderr.strip():
                output += f"\n<stderr>{stderr.strip()}</stderr>"

            logger.info("命令执行成功，退出码：%s", result.exit_code or 0)
            return ExecuteResponse(
                output=output,
                exit_code=result.exit_code or 0,
                truncated=False,
            )

        except Exception as e:
            error_msg = str(e)
            logger.error("执行命令时发生错误：%s", error_msg, exc_info=True)

            if "timeout" in error_msg.lower():
                logger.warning("命令在 %d 秒后超时", timeout)
                return ExecuteResponse(
                    output=f"命令在 {timeout} 秒后超时",
                    exit_code=124,
                    truncated=False,
                )

            return ExecuteResponse(
                output=f"执行命令时出错：{error_msg}",
                exit_code=1,
                truncated=False,
            )

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """从沙盒中下载文件。

        Args:
            paths: 沙盒内绝对路径列表。

        Returns:
            每个路径对应的下载结果，包含文件内容或错误信息。
        """
        logger.info("开始下载 %d 个文件：%s", len(paths), paths)
        responses: list[FileDownloadResponse] = []

        for i, path in enumerate(paths):
            logger.debug("正在处理第 %d/%d 个文件：%s", i + 1, len(paths), path)

            if not path.startswith("/"):
                logger.error("非法路径（必须是绝对路径）：%s", path)
                responses.append(
                    FileDownloadResponse(path=path, content=None, error="invalid_path")
                )
                continue

            try:
                logger.debug("正在从沙盒读取文件：%s", path)
                content = self._sandbox.files.read_file(path)
                content_bytes = content.encode("utf-8") if isinstance(content, str) else content

                logger.debug("文件读取成功，大小：%d 字节", len(content_bytes))
                responses.append(
                    FileDownloadResponse(
                        path=path,
                        content=content_bytes,
                        error=None,
                    )
                )

            except Exception as e:
                logger.error("读取文件 %s 时出错：%s", path, str(e), exc_info=True)

                try:
                    logger.debug("检查文件是否存在：%s", path)
                    safe_path = path.replace("'", "'\\''")
                    result = self._sandbox.commands.run(f"test -f '{safe_path}' && echo 'exists'")

                    if not result.logs.stdout or "exists" not in result.logs.stdout[0].text:
                        logger.error("文件不存在：%s", path)
                        responses.append(
                            FileDownloadResponse(
                                path=path,
                                content=None,
                                error="file_not_found",
                            )
                        )
                    else:
                        logger.error("文件存在但读取失败：%s，错误：%s", path, str(e))
                        responses.append(
                            FileDownloadResponse(
                                path=path,
                                content=None,
                                error=f"read_error: {str(e)}",
                            )
                        )
                except Exception as check_error:
                    logger.error("检查文件是否存在时出错：%s", check_error, exc_info=True)
                    responses.append(
                        FileDownloadResponse(
                            path=path,
                            content=None,
                            error=f"check_error: {str(check_error)}",
                        )
                    )

        success_count = sum(1 for r in responses if r.error is None)
        logger.info("文件下载完成，成功 %d/%d 个", success_count, len(paths))
        return responses

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """将文件上传到沙盒中。

        Args:
            files: ``(路径, 字节内容)`` 元组列表，路径必须是沙盒内绝对路径。

        Returns:
            每个文件的上传结果，成功时 error 为 None。
        """
        logger.info("准备上传 %d 个文件", len(files))
        responses: list[FileUploadResponse] = []
        upload_entries = []

        for i, (path, content) in enumerate(files):
            logger.debug(
                "正在处理第 %d/%d 个待上传文件：%s，大小：%d 字节",
                i + 1,
                len(files),
                path,
                len(content),
            )

            if not path.startswith("/"):
                logger.error("非法路径（必须是绝对路径）：%s", path)
                responses.append(FileUploadResponse(path=path, error="invalid_path"))
                continue

            try:
                content_str = _to_text(content)
                upload_entries.append(WriteEntry(path=path, data=content_str, mode=0o644))
                responses.append(FileUploadResponse(path=path, error=None))
                logger.debug("文件已加入上传队列：%s", path)

            except Exception as e:
                logger.error("准备上传文件 %s 时出错：%s", path, str(e), exc_info=True)
                responses.append(FileUploadResponse(path=path, error=str(e)))

        if upload_entries:
            logger.info("正在向沙盒写入 %d 个文件", len(upload_entries))
            try:
                self._sandbox.files.write_files(upload_entries)
                logger.info("成功上传 %d 个文件", len(upload_entries))

            except Exception as e:
                logger.error("上传文件时出错：%s", str(e), exc_info=True)
                for i, resp in enumerate(responses):
                    if resp.error is None:
                        responses[i] = FileUploadResponse(
                            path=resp.path,
                            error=f"upload_failed: {str(e)}",
                        )
        else:
            logger.warning("没有有效的文件需要上传")

        success_count = sum(1 for r in responses if r.error is None)
        error_count = len(responses) - success_count
        logger.info("文件上传完成，成功 %d 个，失败 %d 个", success_count, error_count)

        return responses

    def sync_skills(self, files: list[tuple[str, bytes]], *, roots: list[str]) -> int:
        """把技能同步进沙箱：先清空技能根目录，再写入最新内容。

        与 :meth:`upload_files` 的关键区别是这是一次**幂等同步**而非增量追加：
        主机上删掉的技能必须从容器里消失，否则沙箱会话会继续加载已经不存在的
        技能。清空与建目录都走 OpenSandbox 的文件系统接口，不经过命令策略——
        技能同步是运行时基础设施行为，不是 Agent 发起的命令，不应被审批规则
        拦下（``rm`` 在沙箱下是需要审批的类别）。

        Args:
            files: ``(沙箱内绝对路径, 字节内容)`` 列表，可以为空。
            roots: 需要先清空再重建的技能根目录，即使 ``files`` 为空也会建出来，
                避免 ``SkillsMiddleware`` 对着不存在的目录刷 ``path_not_found``。

        Returns:
            实际写入的文件数。

        Raises:
            Exception: 建目录或写入失败时向上抛出，由调用方决定如何降级。
        """
        for root in roots:
            try:
                self._sandbox.files.delete_directories([root])
            except Exception as exc:
                # 首次同步时目录不存在属于正常情况。这里不中断：后续写入会覆盖
                # 同名文件，最坏结果是主机上已删除的旧技能残留在容器里。
                logger.debug("清空技能目录跳过：%s（%s）", root, exc)

        directories = set(roots)
        directories.update(str(PurePosixPath(path).parent) for path, _ in files)
        self._sandbox.files.create_directories(
            [WriteEntry(path=path, mode=0o755) for path in sorted(directories)]
        )

        entries = [
            WriteEntry(path=path, data=_to_text(content), mode=0o644)
            for path, content in files
        ]
        if entries:
            self._sandbox.files.write_files(entries)
        logger.info("技能同步写入 %d 个文件，根目录：%s", len(entries), roots)
        return len(entries)


# ── 沙盒管理工具函数 ────────────────────────────────────────


def get_connection_config(
    domain: str | None = None,
    use_server_proxy: bool = True,
    timeout_seconds: int = 60,
    max_connections: int = 20,
) -> ConnectionConfigSync:
    """创建 OpenSandbox 连接配置。

    Args:
        domain: OpenSandbox Server 地址，默认从环境变量 SANDBOX_HOST 读取。
        use_server_proxy: 是否使用服务端代理模式。
        timeout_seconds: 请求超时时间（秒）。
        max_connections: 最大连接数。

    Returns:
        ConnectionConfigSync 实例。
    """
    host = domain or os.environ.get("SANDBOX_HOST", "http://192.168.23.3:8080")
    api_key = os.environ.get("SANDBOX_API_KEY")
    return ConnectionConfigSync(
        domain=host,
        api_key=api_key,
        use_server_proxy=use_server_proxy,
        request_timeout=timedelta(seconds=timeout_seconds),
        transport=httpx.HTTPTransport(limits=httpx.Limits(max_connections=max_connections)),
    )


def get_or_create_sandbox(
    config: ConnectionConfigSync,
    *,
    thread_id: str | None = None,
    sandbox_id: str | None = None,
    image: str | None = None,
) -> SandboxSync:
    """获取或创建沙箱。

    当提供 ``thread_id`` 时，先查注册表尝试复用存活的沙箱；
    复用失败或无记录时创建新沙箱并注册。

    Args:
        config: ConnectionConfigSync 配置。
        thread_id: 可选的会话 ID，用于沙箱复用和注册。
        sandbox_id: 可选的，直接连接的现有沙箱 ID（优先级高于 thread_id 查找）。
        image: 可选的，创建新沙箱时使用的镜像。

    Returns:
        SandboxSync 实例。
    """
    # 直接指定 sandbox_id 时跳过注册表查找
    if sandbox_id:
        print(f"[INFO] 正在连接到现有沙箱: {sandbox_id}")
        try:
            sandbox = SandboxSync.connect(sandbox_id, connection_config=config)
            print(f"[INFO] 成功连接到沙箱: {sandbox_id}")
            return sandbox
        except Exception as e:
            print(f"[WARNING] 连接沙箱失败: {e}，将创建新沙箱")

    # 通过注册表尝试复用 thread 的存活沙箱
    if thread_id:
        reused = _try_reuse_sandbox(config, thread_id)
        if reused is not None:
            return reused

    if not image:
        image = DEFAULT_SANDBOX_IMAGE

    print(f"[INFO] 正在创建新沙箱，使用镜像: {image}")
    sandbox = SandboxSync.create(
        image,
        entrypoint=["/opt/opensandbox/code-interpreter.sh"],
        env={"PYTHON_VERSION": "3.11"},
        resource={"cpu": "4", "memory": "4Gi"},
        timeout=timedelta(minutes=30),
        connection_config=config,
    )
    print(f"[INFO] 成功创建新沙箱，ID: {sandbox.id}")

    # 注册新沙箱到数据库
    if thread_id:
        _register_sandbox(thread_id, sandbox.id)

    return sandbox


def _try_reuse_sandbox(
    config: ConnectionConfigSync,
    thread_id: str,
) -> SandboxSync | None:
    """尝试从注册表复用 thread 的存活沙箱。

    Args:
        config: 连接配置。
        thread_id: 会话 ID。

    Returns:
        复用的 SandboxSync 实例；无法复用时返回 ``None``。
    """
    try:
        from store.sandbox_repo import SandboxRepo

        repo = SandboxRepo()
        record = repo.get_active_sandbox(thread_id)
        if record is None:
            logger.debug("注册表无 active 沙箱：thread_id=%s", thread_id)
            return None

        old_sandbox_id = record["sandbox_id"]
        logger.info("尝试复用沙箱：%s（thread_id=%s）", old_sandbox_id, thread_id)

        sandbox = SandboxSync.connect(old_sandbox_id, connection_config=config)
        if sandbox.is_healthy():
            repo.update_last_used(old_sandbox_id)
            logger.info("沙箱复用成功：%s（thread_id=%s）", old_sandbox_id, thread_id)
            return sandbox

        # 沙箱已死亡，标记为 killed
        logger.info("沙箱已过期，标记为 killed：%s", old_sandbox_id)
        repo.mark_killed(old_sandbox_id)

    except Exception:
        logger.warning("复用沙箱失败，将创建新沙箱", exc_info=True)
        try:
            repo.mark_killed(old_sandbox_id)
        except Exception:
            pass

    return None


def _register_sandbox(thread_id: str, sandbox_id: str) -> None:
    """注册新沙箱并执行清理策略。

    Args:
        thread_id: 会话 ID。
        sandbox_id: 新创建的沙箱 ID。
    """
    try:
        from config.app_config import get_settings
        from store.sandbox_repo import SandboxRepo

        settings = get_settings()
        repo = SandboxRepo()
        repo.register(sandbox_id, thread_id)

        # 执行清理策略
        idle_removed = repo.cleanup_idle(settings.sandbox_idle_timeout_days)
        if idle_removed:
            logger.info("空闲沙箱清理：%d 条", idle_removed)

        per_thread_removed = repo.cleanup_per_thread(settings.sandbox_max_per_thread)
        if per_thread_removed:
            logger.info("超限沙箱清理：%d 条", per_thread_removed)

    except Exception:
        logger.warning("沙箱注册或清理失败（不影响沙箱使用）", exc_info=True)


def sync_sandbox_skills(
    backend: OpenSandboxBackend, project_name: str | None = None
) -> int:
    """把主机上的技能同步进沙箱容器。

    同步范围由会话所属项目决定：属于某个项目集合时同步「全局层 + 该项目集合
    层」，否则只同步全局层。落点与 ``services.skill_registry.sandbox_skill_sources``
    给出的挂载路径完全一致，保证「同步了什么就挂载什么」。

    Args:
        backend: 目标 OpenSandboxBackend 实例。
        project_name: 会话所属项目集合名；为空时只同步全局层。

    Returns:
        实际写入沙箱的文件数。

    Raises:
        Exception: 读取技能或写入沙箱失败时向上抛出。这里刻意不吞异常——调用方
            有的是「不能阻断沙盒使用」（构建 backend），有的是「必须让运维看得见」
            （技能变更后的主动同步），降级策略应由调用方决定。

    Note:
        技能目录为空时也会建出技能根目录，避免加载器报 ``path_not_found``。
    """
    from services.skill_installer import get_default_skills
    from services.skill_registry import sandbox_skill_roots

    upload_files = get_default_skills(project_name)
    roots = sandbox_skill_roots(project_name)
    written = backend.sync_skills(upload_files, roots=roots)
    logger.info(
        "沙盒技能同步完成：%s project=%s files=%d roots=%s",
        backend.id,
        project_name or "-",
        written,
        roots,
    )
    return written


def list_running_sandboxes(config: ConnectionConfigSync) -> list:
    """列出所有正在运行的沙箱。

    Args:
        config: ConnectionConfigSync 配置。

    Returns:
        正在运行的沙箱列表。
    """
    try:
        print("[INFO] 正在列出运行中的沙箱...")

        try:
            sandboxes = SandboxSync.list(connection_config=config)
            running_sandboxes = []

            paginator = sandboxes
            while True:
                items = paginator.next_items()
                if not items:
                    break
                for sandbox in items:
                    if hasattr(sandbox, "state") and sandbox.state == "running":
                        running_sandboxes.append(sandbox)
                    elif not hasattr(sandbox, "state"):
                        running_sandboxes.append(sandbox)

            print(f"[INFO] 找到 {len(running_sandboxes)} 个运行中的沙箱")
            return running_sandboxes

        except AttributeError:
            print("[WARNING] SandboxSync.list() 方法不存在，无法列出沙箱")
            return []

    except Exception as e:
        print(f"[ERROR] 列出沙箱时出错: {e}")
        return []


def create_opensandbox_backend(
    *,
    thread_id: str | None = None,
    sandbox_id: str | None = None,
    image: str | None = None,
    project_name: str | None = None,
) -> OpenSandboxBackend:
    """DeepAgents 工厂函数：创建 OpenSandbox backend 实例。

    Args:
        thread_id: 可选的会话 ID，用于沙箱复用和注册。
        sandbox_id: 可选的，直接连接的现有沙箱 ID。
        image: 可选的，创建新沙箱时使用的镜像。
        project_name: 可选的会话所属项目集合名，决定同步哪几层技能到容器。

    Returns:
        OpenSandboxBackend 实例。
    """
    config = get_connection_config()
    sandbox = get_or_create_sandbox(
        config, thread_id=thread_id, sandbox_id=sandbox_id, image=image
    )
    backend = OpenSandboxBackend(sandbox=sandbox)

    # 同步技能到容器（全局层 + 会话所属项目集合层）。同步失败不影响沙盒使用，
    # 但绝不能把异常抛给上层——create_backend 捕获异常后会误判成「沙箱创建失败」
    # 并静默回退到本地 Shell。
    try:
        sync_sandbox_skills(backend, project_name)
    except Exception:
        logger.warning("沙盒技能同步失败（不影响沙箱使用）", exc_info=True)

    return backend
