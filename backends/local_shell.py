"""Windows 本地文件后端实现。

这个模块是当前项目 Agent 运行时最关键的工程化组件之一：

1. 它把 DeepAgents 的文件后端协议落到本机 Windows 文件系统上；
2. 它把模型看到的虚拟路径（例如 ``/projects``、``/skills``）映射到真实工作区目录；
3. 它为命令执行增加安全守卫、超时控制、Token 脱敏和 Gitee 非交互认证；
4. 它保留了旧版 Gitee/Git 工具需要的兼容方法，降低业务代码迁移成本。

讲课时可以把它理解为"Agent 与真实电脑之间的工程边界"：
Prompt 负责告诉模型应该怎么做，LocalShellBackend 负责决定模型最终能不能读、写、执行。
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from deepagents.backends.protocol import (
    DeleteResult,
    EditResult,
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)
from deepagents.backends.sandbox import BaseSandbox

from .command_policy import evaluate_command
from .permissions import normalize_safe_command
from .workspace import Workspace
from utils.recycle_bin import move_to_recycle_bin

logger = logging.getLogger("agent.run.shell")


# ── 环境变量辅助函数 ──────────────────────────────────────────


def _get_env(name: str, default: str = "") -> str:
    """从环境变量读取值，不存在时返回默认值。

    Args:
        name: 环境变量名称。
        default: 环境变量不存在时的默认返回值，默认为空字符串。

    Returns:
        环境变量的值，或 ``default``。
    """
    return os.environ.get(name, default)


# 默认工作区根目录
_DEFAULT_WORKSPACE_ROOT = Path(
    _get_env("AI_WORKSPACE_ROOT", r"E:\ai_workspace")
).resolve()


# ── 虚拟路径常量 ──────────────────────────────────────────────
# Agent 内部通过 /projects、/skills 等虚拟路径访问文件，
# 这里定义所有虚拟路径的映射关系。
VIRTUAL_PROJECTS = "/projects"
VIRTUAL_SKILLS = "/skills"
# 全局技能目录的专用虚拟路径。本地区「本地文件夹模式」下 /skills 指向项目内的
# .xihe-agent/skills，全局技能需要一个独立路径才能同时挂载，让项目层与全局层
# 并存并按顺序覆盖。
VIRTUAL_GLOBAL_SKILLS = "/skills-global"
# 项目集合技能目录的虚拟路径。项目集合存放在全局技能目录下的 .project-set
# 容器里，与全局技能目录同级但需要在模型视角区分开，故单列一个前缀。
VIRTUAL_PROJECT_SET_SKILLS = "/skills-set"
VIRTUAL_POLICIES = "/policies"
VIRTUAL_REVIEWS = "/reviews"
VIRTUAL_RUNTIMES = "/runtimes"
VIRTUAL_TMP = "/tmp"
VIRTUAL_LOGS = "/logs"

# 项目集合在全局技能目录下的容器目录名，与 services.skill_registry 保持一致。
PROJECT_SET_DIR_NAME = ".project-set"

# 匹配命令中的 Windows 绝对路径（如 D:\xxx）
_DRIVE_PATH_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]:[\\/][^\s\"';&|<>]*)")
# 匹配命令中的虚拟路径（如 /projects/my-repo），用于自动转换为真实路径
_VIRTUAL_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9])/(skills-global|skills-set|projects|skills|policies|reviews|runtimes|tmp|logs)(/[^\s\"';&|<>]*)?"
)
_RELATIVE_PROJECTS_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_./\\-])projects[\\/](?=[^\s\"';&|<>]+)"
)
_RELATIVE_PROJECTS_DIR_RE = re.compile(
    r"(?<![A-Za-z0-9_./\\-])projects(?=\s|$)"
)
_NESTED_PROJECTS_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_./\\-])projects[\\/]projects(?:[\\/]|(?=\s|$))"
)
# 危险命令黑名单 —— 匹配到的命令直接拒绝执行
_DANGEROUS_PATTERNS = (
    r"\bformat\b",
    r"\bshutdown\b",
    r"\brestart-computer\b",
    r"\bstop-computer\b",
    r"\bset-executionpolicy\b",
    r"\breg\s+(delete|add|import|save|restore)\b",
    r"\bnet\s+user\b",
    r"\bnet\s+localgroup\b",
    r"\bdiskpart\b",
    r"\bbcdedit\b",
)

def _mask_token(text: str) -> str:
    """把文本中的 Gitee Token 替换为 ***，防止泄露到日志和返回值里。

    Agent 后端会把命令 stdout/stderr、异常信息、Git 输出写入日志或返回给模型。
    如果 Git 认证失败、远端 URL 展开或调试信息里包含 token，就可能造成泄露。
    因此所有准备进入日志和模型上下文的文本，都应先经过这个函数脱敏。

    Args:
        text: 待脱敏的原始文本。

    Returns:
        脱敏后的文本，所有已知 Token 已被替换为 ``***``。
    """
    masked = text
    for token_name in ("GITEE_TOKEN", "SCM_GITEE_TOKEN"):
        token = _get_env(token_name).strip()
        if token:
            masked = masked.replace(token, "***")
    return masked


@dataclass
class CommandResult:
    """兼容旧版 Gitee/Git 工具的命令执行结果。

    DeepAgents 原生 ``execute()`` 返回 ``ExecuteResponse``，但项目里部分历史工具
    仍然依赖 ``command/cwd/exit_code/stdout/stderr`` 这种更接近 subprocess 的结构。
    保留这个 dataclass 可以让旧工具逐步迁移，而不需要一次性改完整个调用链。
    """

    command: str
    cwd: str
    exit_code: int
    stdout: str
    stderr: str


class LocalShellBackend(BaseSandbox):
    """Windows 本地 DeepAgents backend。

    核心职责：
    1. 实现 DeepAgents 原生文件协议：``ls/read/write/edit/glob/grep/upload/download``；
    2. 实现命令执行协议：``execute()``，负责 Windows 适配、路径转换、超时和脱敏；
    3. 实现本地工作区布局：projects、skills、policies、reviews、runtimes、tmp、logs；
    4. 实现 Gitee Git 非交互认证：通过 ``GIT_ASKPASS`` 注入 token，避免后台进程弹窗；
    5. 保留旧版兼容接口：``run/read_file/write_file/list_files``，避免一次性重构业务工具。

    这个类不是"完全可信的 shell 代理"，而是课程项目里的受控本地执行层。
    真正企业生产环境还应叠加容器隔离、系统用户隔离、审计日志和更完整的权限策略。
    """

    def __init__(
        self,
        workspace: Workspace | str | os.PathLike[str] | None = None,
        *,
        timeout: int = 3600,
        local_folder_mode: bool = False,
        global_workspace_root: Path | str | None = None,
    ) -> None:
        """初始化本地 backend 实例。

        Args:
            workspace: 工作区对象或根目录路径；为空时从 ``LOCAL_SHELL_WORKSPACE``
                环境变量或默认值推导。
            timeout: ``execute()`` 未显式指定超时时的默认值（秒），默认 3600。
            local_folder_mode: 是否启用「本地文件夹模式」。启用后 ``root`` 即用户
                选定的项目文件夹（文件夹即项目根），只在其中维护
                ``.xihe-agent/{skills,memories,reviews}`` 布局；后端基础设施
                （``.secrets`` / ``runtimes`` 等）指向全局工作区，且写操作被限制在
                ``root`` 内、读操作可访问整个全局工作区（只读）。
            global_workspace_root: 本地文件夹模式下使用的只读全局工作区根目录，
                为空时回退到 ``_DEFAULT_WORKSPACE_ROOT``。
        """
        # 确定工作区根目录：传入的 workspace > 环境变量 > 默认值
        if isinstance(workspace, Workspace):
            self.workspace = workspace
            configured_root = workspace.root
        else:
            configured_root = (
                workspace
                or os.environ.get("LOCAL_SHELL_WORKSPACE")
                or str(_DEFAULT_WORKSPACE_ROOT)
            )
            self.workspace = Workspace(Path(configured_root))

        self.root = Path(configured_root).expanduser().resolve()
        self.local_folder_mode = local_folder_mode

        if local_folder_mode:
            # 本地文件夹模式：root 就是项目根，基础设施复用全局工作区（只读）。
            self.global_workspace_root = Path(
                global_workspace_root or _DEFAULT_WORKSPACE_ROOT
            ).expanduser().resolve()
            self.agent_dir: Path | None = self.root / ".xihe-agent"
            self.projects_dir = self.root
            self.skills_dir = self.agent_dir / "skills"
            # 全局技能目录：本地文件夹模式下 /skills 被项目目录占用，全局技能
            # 通过 /skills-global 暴露，使两层技能可以同时挂载。
            self.global_skills_dir = self.global_workspace_root / "skills"
            # 项目集合：全局技能目录下的 .project-set/<集合名>，通过 /skills-set
            # 暴露，让「随项目走但不落在项目文件夹里」的技能也有归属。
            self.project_set_skills_dir = self.global_skills_dir / PROJECT_SET_DIR_NAME
            self.reviews_dir = self.agent_dir / "reviews"
            self.policies_dir = self.global_workspace_root / "policies"
            self.runtimes_dir = self.global_workspace_root / "runtimes"
            self.tmp_dir = self.global_workspace_root / "tmp"
            self.logs_dir = self.global_workspace_root / "logs"
            self.secrets_dir = self.global_workspace_root / ".secrets"
            self.shared_python_venv = self.global_workspace_root / os.environ.get(
                "LOCAL_SHELL_SHARED_PYTHON_VENV",
                r"runtimes\python\default\.venv",
            )
        else:
            self.global_workspace_root = self.root
            self.agent_dir = None
            # 工作区子目录规划
            self.projects_dir = self.root / os.environ.get(
                "LOCAL_SHELL_PROJECTS_DIR", "projects"
            )
            self.skills_dir = self.root / "skills"
            # 非本地文件夹模式下 /skills 本身就是全局技能目录，别无第二层。
            self.global_skills_dir = self.skills_dir
            # 项目集合仍挂在全局技能目录下，两种模式保持一致。
            self.project_set_skills_dir = self.global_skills_dir / PROJECT_SET_DIR_NAME
            self.policies_dir = self.root / "policies"
            self.reviews_dir = self.root / "reviews"
            self.runtimes_dir = self.root / "runtimes"
            self.tmp_dir = self.root / "tmp"
            self.logs_dir = self.root / "logs"
            self.secrets_dir = self.root / ".secrets"
            self.shared_python_venv = self.root / os.environ.get(
                "LOCAL_SHELL_SHARED_PYTHON_VENV",
                r"runtimes\python\default\.venv",
            )
        self.default_timeout = timeout
        self.command_guard_enabled = (
            os.environ.get("LOCAL_SHELL_ENABLE_COMMAND_GUARD", "true").lower()
            not in {"0", "false", "no"}
        )
        self._venv_error: str | None = None
        self._ensure_layout()

    @property
    def id(self) -> str:
        """返回 backend 实例的唯一标识。

        Returns:
            格式为 ``local-windows:<root>`` 的标识字符串。
        """
        return f"local-windows:{self.root}"

    def get_work_dir(self) -> str:
        """DeepAgents 协议：返回默认工作目录的虚拟路径。

        Returns:
            固定返回 ``/projects`` 虚拟路径。
        """
        return VIRTUAL_PROJECTS

    def get_workspace_root(self) -> str:
        """DeepAgents 协议：返回虚拟根路径。

        Returns:
            固定返回 ``/``。
        """
        return "/"

    def health(self) -> dict[str, Any]:
        """健康检查：检测目录、Git、Python、Node 等是否可用。

        Returns:
            包含健康状态、根目录和各项检查结果的字典。
        """
        checks = {
            "root_exists": self.root.exists(),
            "root_writable": self._is_writable(self.root),
            "projects_exists": self.projects_dir.exists(),
            "skills_exists": self.skills_dir.exists(),
            "python_venv_exists": self.shared_python_venv.exists(),
            "git_available": shutil.which("git") is not None,
            "python_available": shutil.which("python") is not None,
            "node_available": shutil.which("node") is not None,
            "command_guard_enabled": self.command_guard_enabled,
        }
        return {
            "type": "local_shell",
            "root": str(self.root),
            "healthy": bool(
                checks["root_exists"]
                and checks["root_writable"]
                and checks["projects_exists"]
            ),
            "checks": checks,
            "errors": {"python_venv": self._venv_error} if self._venv_error else {},
        }

    # ── DeepAgents 原生协议实现 ─────────────────────────────

    def execute(
        self, command: str, *, timeout: int | None = None
    ) -> ExecuteResponse:
        """执行 DeepAgents 原生命令工具请求。

        这里是模型调用 ``execute`` 工具时最终进入的入口，处理顺序是：
        1. 先做安全拒绝判断，拦截危险命令和工作区外路径；
        2. 再做命令预处理，把虚拟路径和部分 Unix 习惯命令转换成 Windows 可执行形式；
        3. 最后通过 subprocess 执行，并把 stdout/stderr 合并成模型容易阅读的文本。

        注意：这个方法不向上抛异常，而是按 DeepAgents 协议返回结构化结果。

        Args:
            command: 要执行的命令字符串。
            timeout: 超时时间（秒）；为 None 时使用实例默认超时。

        Returns:
            包含命令输出和退出码的结构化响应。
        """
        if self.command_guard_enabled:
            denied = self._deny_reason(command)
            policy = evaluate_command(command, "local")
            if denied or policy.action == "deny":
                return ExecuteResponse(
                    output=f"命令被拒绝：{denied or policy.reason}",
                    exit_code=126,
                    truncated=False,
                )

        prepared_command = self._prepare_command(command, cwd_path=self.projects_dir)
        effective_timeout = timeout if timeout is not None else self.default_timeout
        try:
            completed = subprocess.run(
                prepared_command,
                cwd=self.projects_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=effective_timeout,
                env=self._execution_env(),
                check=False,
                shell=True,
            )
            output = completed.stdout
            if completed.stderr.strip():
                stderr = completed.stderr.strip()
                output = f"{output}\n<stderr>{stderr}</stderr>" if output else stderr
            return ExecuteResponse(
                output=_mask_token(output.rstrip()),
                exit_code=completed.returncode,
                truncated=False,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            return ExecuteResponse(
                output=_mask_token(
                    f"命令执行超时：{effective_timeout} 秒\n{stdout}{stderr}".strip()
                ),
                exit_code=124,
                truncated=False,
            )
        except Exception as exc:
            return ExecuteResponse(
                output=f"命令执行失败：{_mask_token(str(exc))}",
                exit_code=1,
                truncated=False,
            )

    def ls(self, path: str) -> LsResult:
        """列出虚拟路径下的文件和目录。

        Args:
            path: 虚拟路径字符串。

        Returns:
            包含目录条目列表或错误信息的结果。
        """
        try:
            resolved = self._resolve_virtual_path(path)
            if not resolved.exists():
                return LsResult(entries=None, error=f"路径不存在：{path}")
            if not resolved.is_dir():
                return LsResult(entries=None, error=f"当前路径不是目录：{path}")
            return LsResult(
                entries=[
                    {
                        "path": self._to_virtual_path(child),
                        "is_dir": child.is_dir(),
                        "size": child.stat().st_size if child.is_file() else 0,
                        "modified_at": datetime.fromtimestamp(
                            child.stat().st_mtime, UTC
                        ).isoformat(),
                    }
                    for child in sorted(resolved.iterdir(), key=lambda p: p.name.lower())
                ]
            )
        except PermissionError as exc:
            return LsResult(entries=None, error=str(exc))

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        """读取文件内容（支持偏移和行数限制）。

        默认先按 UTF-8 解码，失败后回退到 Latin-1。
        这个回退策略保证模型在遇到非 UTF-8 文件时仍能看到内容，
        避免因为一次编码异常中断整个 Agent 任务。

        Args:
            file_path: 虚拟路径。
            offset: 起始行偏移量。
            limit: 最大读取行数。

        Returns:
            包含文件内容和元数据的结果，或错误信息。
        """
        try:
            resolved = self._resolve_virtual_path(file_path)
            if not resolved.exists():
                return ReadResult(error=f"文件不存在：{file_path}")
            if resolved.is_dir():
                return ReadResult(error=f"当前路径是目录，不能读取为文件：{file_path}")
            raw = resolved.read_bytes()
            try:
                text = raw.decode("utf-8")
                encoding = "utf-8"
            except UnicodeDecodeError:
                text = raw.decode("latin-1")
                encoding = "latin-1"
            lines = text.splitlines()
            if offset or limit:
                text = "\n".join(lines[int(offset) : int(offset) + int(limit)])
            stat = resolved.stat()
            return ReadResult(
                file_data={
                    "content": text,
                    "encoding": encoding,
                    "created_at": datetime.fromtimestamp(stat.st_ctime, UTC).isoformat(),
                    "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
                }
            )
        except PermissionError as exc:
            return ReadResult(error=str(exc))

    def write(self, file_path: str, content: str) -> WriteResult:
        """新建文件。如果文件已存在则报错（修改请用 edit）。

        Args:
            file_path: 虚拟路径。
            content: 文件内容。

        Returns:
            写入成功时包含路径，失败时包含错误信息。
        """
        try:
            resolved = self._resolve_virtual_path(file_path)
            denied = self._write_deny_reason(resolved)
            if denied:
                return WriteResult(error=denied)
            if resolved.exists():
                return WriteResult(error=f"文件已存在，修改文件请使用 edit_file：{file_path}")
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content, encoding="utf-8", newline="")
            return WriteResult(path=file_path)
        except PermissionError as exc:
            return WriteResult(error=str(exc))

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        """替换文件中的文本 —— 相当于精细化的 find & replace。

        Args:
            file_path: 虚拟路径。
            old_string: 要被替换的原始文本。
            new_string: 替换后的新文本。
            replace_all: 是否替换所有匹配项，默认只替换第一次出现。

        Returns:
            替换结果，包含匹配次数或错误信息。
        """
        try:
            resolved = self._resolve_virtual_path(file_path)
            if not resolved.exists():
                return EditResult(error=f"文件不存在：{file_path}")
            if resolved.is_dir():
                return EditResult(error=f"当前路径是目录，不能修改为文件：{file_path}")
            denied = self._write_deny_reason(resolved)
            if denied:
                return EditResult(error=denied)
            text = resolved.read_text(encoding="utf-8")
            count = text.count(old_string)
            if count == 0:
                return EditResult(error=f"没有找到要替换的内容：{old_string}")
            if count > 1 and not replace_all:
                return EditResult(
                    error=f"要替换的内容出现 {count} 次，请设置 replace_all=True 或提供更精确片段。"
                )
            updated = text.replace(old_string, new_string, -1 if replace_all else 1)
            resolved.write_text(updated, encoding="utf-8", newline="")
            return EditResult(path=file_path, occurrences=count if replace_all else 1)
        except PermissionError as exc:
            return EditResult(error=str(exc))

    def delete(self, file_path: str) -> DeleteResult:
        """Move a project file or directory into the global workspace recycle bin."""

        try:
            resolved = self._resolve_virtual_path(file_path)
            denied = self._write_deny_reason(resolved)
            if denied:
                return DeleteResult(error=denied)
            if not resolved.exists() and not resolved.is_symlink():
                return DeleteResult(error=f"文件不存在：{file_path}")

            if self.local_folder_mode:
                project_root = self.root
            else:
                try:
                    project_relative = resolved.relative_to(self.projects_dir.resolve())
                except ValueError:
                    return DeleteResult(error="只能删除项目目录中的文件")
                if not project_relative.parts:
                    return DeleteResult(error="不能删除项目目录根节点")
                project_root = self.projects_dir / project_relative.parts[0]

            move_to_recycle_bin(
                resolved,
                project_root=project_root,
                workspace_root=self.global_workspace_root,
            )
            return DeleteResult(path=file_path)
        except (FileNotFoundError, PermissionError, OSError) as exc:
            return DeleteResult(error=str(exc))

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        """递归搜索匹配模式的文件路径（支持 fnmatch 通配符）。

        Args:
            pattern: fnmatch 风格的通配符模式。
            path: 搜索起始的虚拟路径，默认从根目录开始。

        Returns:
            包含匹配文件列表的结果。
        """
        try:
            base = self._resolve_virtual_path(path or "/")
            matches = []
            for child in base.rglob("*"):
                virtual = self._to_virtual_path(child)
                if fnmatch.fnmatch(virtual, pattern) or fnmatch.fnmatch(
                    child.name, pattern
                ):
                    matches.append(
                        {
                            "path": virtual,
                            "is_dir": child.is_dir(),
                            "size": child.stat().st_size if child.is_file() else 0,
                            "modified_at": datetime.fromtimestamp(
                                child.stat().st_mtime, UTC
                            ).isoformat(),
                        }
                    )
            return GlobResult(matches=matches)
        except PermissionError as exc:
            return GlobResult(matches=None, error=str(exc))

    def grep(
        self, pattern: str, path: str | None = None, glob: str | None = None
    ) -> GrepResult:
        """在文件中搜索关键词，返回匹配的行号和内容。

        Args:
            pattern: 要搜索的关键词。
            path: 搜索起始的虚拟路径。
            glob: 可选的文件名过滤模式。

        Returns:
            包含匹配结果（路径、行号、文本）的列表。
        """
        try:
            base = self._resolve_virtual_path(path or VIRTUAL_PROJECTS)
            files = [base] if base.is_file() else [p for p in base.rglob("*") if p.is_file()]
            matches = []
            for file in files:
                if glob and not fnmatch.fnmatch(file.name, glob):
                    continue
                try:
                    for line_no, line in enumerate(
                        file.read_text(encoding="utf-8", errors="ignore").splitlines(), 1
                    ):
                        if pattern in line:
                            matches.append(
                                {
                                    "path": self._to_virtual_path(file),
                                    "line": line_no,
                                    "text": line,
                                }
                            )
                except OSError:
                    continue
            return GrepResult(matches=matches)
        except PermissionError as exc:
            return GrepResult(matches=None, error=str(exc))

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """批量上传文件（二进制内容直接写入）。

        Args:
            files: ``(虚拟路径, 字节内容)`` 元组列表。

        Returns:
            每个文件的上传结果列表。
        """
        responses: list[FileUploadResponse] = []
        for path, content in files:
            try:
                resolved = self._resolve_virtual_path(path)
                denied = self._write_deny_reason(resolved)
                if denied:
                    responses.append(FileUploadResponse(path=path, error=denied))
                    continue
                resolved.parent.mkdir(parents=True, exist_ok=True)
                resolved.write_bytes(content)
                responses.append(FileUploadResponse(path=path, error=None))
            except PermissionError:
                responses.append(FileUploadResponse(path=path, error="permission_denied"))
            except IsADirectoryError:
                responses.append(FileUploadResponse(path=path, error="is_directory"))
            except Exception:
                responses.append(FileUploadResponse(path=path, error="invalid_path"))
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """批量下载文件，返回二进制内容。

        Args:
            paths: 虚拟路径列表。

        Returns:
            每个路径对应的下载结果，包含文件内容或错误信息。
        """
        responses: list[FileDownloadResponse] = []
        for path in paths:
            try:
                resolved = self._resolve_virtual_path(path)
                if not resolved.exists():
                    responses.append(
                        FileDownloadResponse(path=path, content=None, error="file_not_found")
                    )
                elif resolved.is_dir():
                    responses.append(
                        FileDownloadResponse(path=path, content=None, error="is_directory")
                    )
                else:
                    responses.append(
                        FileDownloadResponse(path=path, content=resolved.read_bytes(), error=None)
                    )
            except PermissionError:
                responses.append(
                    FileDownloadResponse(path=path, content=None, error="permission_denied")
                )
            except Exception:
                responses.append(
                    FileDownloadResponse(path=path, content=None, error="invalid_path")
                )
        return responses

    # ── 旧兼容接口 ──────────────────────────────────────────
    # 下面的 read_file / write_file / list_files / run
    # 是为旧版 Gitee/Git 工具保留的入口，内部调上面原生方法。

    def read_file(self, path: str) -> str:
        """读取文本文件（兼容旧工具）。

        Args:
            path: 文件路径，支持相对路径和虚拟路径。

        Returns:
            文件文本内容。

        Raises:
            FileNotFoundError: 文件不存在。
            IsADirectoryError: 路径指向目录。
        """
        result = self.read(self._normalize_compat_path(path), offset=0, limit=200_000)
        if result.error:
            if "目录" in result.error or "is_directory" in result.error:
                raise IsADirectoryError(result.error)
            raise FileNotFoundError(result.error)
        return str(result.file_data["content"]) if result.file_data else ""

    def write_file(self, path: str, content: str) -> str:
        """写入文本文件（兼容旧工具，可新建也可覆盖）。

        Args:
            path: 文件路径。
            content: 要写入的文本内容。

        Returns:
            写入文件的真实绝对路径字符串。
        """
        virtual_path = self._normalize_compat_path(path)
        resolved = self._resolve_virtual_path(virtual_path)
        denied = self._write_deny_reason(resolved)
        if denied:
            raise PermissionError(denied)
        if resolved.exists() and resolved.is_dir():
            raise IsADirectoryError(f"write_file 必须写入具体文件，当前路径是目录: {path}")
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8", newline="")
        return str(resolved)

    def list_files(self, path: str = ".") -> list[str]:
        """列出目录下一层内容（兼容旧工具，返回相对路径）。

        Args:
            path: 目录路径，默认当前目录。

        Returns:
            相对路径字符串列表。
        """
        virtual_path = self._normalize_compat_path(path)
        resolved = self._resolve_virtual_path(virtual_path)
        if not resolved.exists():
            return []
        if resolved.is_file():
            return [str(resolved.relative_to(self.root)).replace("\\", "/")]
        return [
            str(child.relative_to(self.root)).replace("\\", "/")
            for child in sorted(resolved.iterdir())
        ]

    def run(self, command: str, cwd: str = ".", timeout: int = 300) -> CommandResult:
        """受控执行命令（给旧 Gitee/Git 工具用，带日志和 Token 屏蔽）。

        与 ``execute()`` 的区别：
        - ``execute()`` 面向 DeepAgents 原生协议，返回 ``ExecuteResponse``；
        - ``run()`` 面向项目里的历史工具，返回 ``CommandResult``；
        - ``run()`` 允许指定 cwd，并兼容旧工具常见的 ``cd xxx && git ...`` 写法。

        Args:
            command: 要执行的命令字符串。
            cwd: 执行目录，支持虚拟路径。
            timeout: 命令超时时间（秒）。

        Returns:
            包含命令、工作目录、退出码和标准输出的结构化结果。
        """
        cwd_path, command_text = self._prepare_run_command(command, cwd)
        logger.info(
            "执行命令：cwd=%s command=%s timeout=%s",
            cwd_path,
            _mask_token(command_text),
            timeout,
        )
        completed = subprocess.run(
            command_text,
            cwd=str(cwd_path),
            shell=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            env=self._execution_env(),
        )
        logger.info(
            "命令结束：exit_code=%s command=%s stdout_bytes=%s stderr_bytes=%s",
            completed.returncode,
            _mask_token(command_text),
            len(completed.stdout.encode("utf-8", errors="replace")),
            len(completed.stderr.encode("utf-8", errors="replace")),
        )
        return CommandResult(
            command=command_text,
            cwd=str(cwd_path),
            exit_code=completed.returncode,
            stdout=_mask_token(completed.stdout),
            stderr=_mask_token(completed.stderr),
        )

    # ── 初始化相关 ───────────────────────────────────────────

    def _ensure_layout(self) -> None:
        """创建工作区目录结构，写入 workspace 标记文件。

        后端启动时会确保这些目录存在，而不是要求运维或用户手工创建。
        这样 Agent 第一次启动就具备稳定的文件系统语义。

        本地文件夹模式下只创建 ``.xihe-agent/{skills,reviews}``，
        不在用户项目文件夹里生成任何其它目录；Git askpass 等基础设施写到全局工作区。
        """
        if self.local_folder_mode:
            assert self.agent_dir is not None
            for directory in (self.root, self.skills_dir, self.reviews_dir):
                directory.mkdir(parents=True, exist_ok=True)
            # askpass 脚本必须存在才能非交互认证，写到全局 .secrets（只读复用）。
            self.secrets_dir.mkdir(parents=True, exist_ok=True)
            if os.environ.get("LOCAL_SHELL_CREATE_PYTHON_VENV", "false").lower() not in {
                "0",
                "false",
                "no",
            }:
                self._ensure_shared_python_venv()
            self._ensure_gitee_askpass_files()
            return

        for directory in (
            self.root,
            self.projects_dir,
            self.skills_dir,
            self.policies_dir,
            self.reviews_dir,
            self.runtimes_dir,
            self.tmp_dir,
            self.logs_dir,
            self.secrets_dir,
            self.shared_python_venv.parent,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        if os.environ.get("LOCAL_SHELL_CREATE_PYTHON_VENV", "false").lower() not in {
            "0",
            "false",
            "no",
        }:
            self._ensure_shared_python_venv()
        self._ensure_policy_files()
        self._ensure_gitee_askpass_files()
        state = {
            "backend": "local_shell",
            "root": str(self.root),
            "updated_at": datetime.now(UTC).isoformat(),
            "virtual_dirs": [
                VIRTUAL_PROJECTS,
                VIRTUAL_SKILLS,
                VIRTUAL_GLOBAL_SKILLS,
                VIRTUAL_PROJECT_SET_SKILLS,
                VIRTUAL_POLICIES,
                VIRTUAL_REVIEWS,
                VIRTUAL_RUNTIMES,
                VIRTUAL_TMP,
                VIRTUAL_LOGS,
            ],
        }
        (self.root / ".ai_coding_workspace.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _ensure_policy_files(self) -> None:
        """补全默认的 policy 文件（工作区说明、Git 规范、安全规范）。

        采用"文件不存在才创建"的策略，避免覆盖用户后续维护过的规则内容。
        """
        defaults = {
            "workspace.md": (
                "# 工作区目录说明\n\n"
                "- /projects：Gitee 项目源码目录。\n"
                "- /skills：当前项目专属的技能目录。\n"
                "- /skills-global：跨项目共享的全局技能目录（只读）。\n"
                "- /skills-set：当前项目集合的技能目录（只读）。\n"
                "- /runtimes：本机运行环境目录。\n"
                "- /reviews：审查结果目录。\n"
                "- /tmp：临时文件目录。\n"
                "- /logs：运行日志目录。\n"
            ),
            "git.md": (
                "# Git 规范\n\n"
                "- 第一版只处理 Gitee 仓库。\n"
                "- 修改代码前先确认仓库目录和当前分支。\n"
                "- 提交前必须运行必要测试。\n"
            ),
            "security.md": (
                "# 安全规范\n\n"
                "- 不读取或输出 .secrets 目录内容。\n"
                "- 不提交密钥、Token、私钥或 .env 文件。\n"
            ),
        }
        for name, content in defaults.items():
            path = self.policies_dir / name
            if not path.exists():
                path.write_text(content, encoding="utf-8")

    def _ensure_shared_python_venv(self) -> None:
        """创建共享 Python 虚拟环境（供所有项目复用）。

        如果 ``shared_python_venv`` 路径下已存在可用的 Python 可执行文件，
        则直接跳过创建。创建失败时把错误信息写入 ``self._venv_error``，
        不会抛出异常，以免阻断后端初始化流程。
        """
        if (self.shared_python_venv / "Scripts" / "python.exe").exists():
            return
        python = shutil.which("python")
        if not python:
            self._venv_error = "python executable not found on PATH"
            return
        try:
            completed = subprocess.run(
                [python, "-m", "venv", str(self.shared_python_venv)],
                cwd=self.root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
                check=False,
            )
        except Exception as exc:
            self._venv_error = str(exc)
            return
        if completed.returncode != 0:
            self._venv_error = (
                completed.stderr or completed.stdout or "venv creation failed"
            ).strip()
            return
        self._venv_error = None

    def _ensure_gitee_askpass_files(self) -> None:
        """生成 Git 非交互认证脚本。

        因为 Agent 后台不能弹窗让用户输密码，所以预先创建 askpass 脚本，
        Git 需要凭据时会自动调用它，从环境变量读取 token。
        """
        ps1 = self.secrets_dir / "gitee_askpass.ps1"
        cmd = self.secrets_dir / "gitee_askpass.cmd"
        if not ps1.exists():
            ps1.write_text(
                "\n".join(
                    [
                        "param([string]$Prompt)",
                        "if ($Prompt -match '(?i)username') {",
                        "  [Console]::Out.Write($env:GITEE_ASKPASS_USERNAME)",
                        "} else {",
                        "  [Console]::Out.Write($env:GITEE_ASKPASS_TOKEN)",
                        "}",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
        if not cmd.exists():
            cmd.write_text(
                f'@echo off\r\npowershell -NoProfile -ExecutionPolicy Bypass -File "{ps1}" %*\r\n',
                encoding="utf-8",
            )

    # ── 路径处理 ─────────────────────────────────────────────

    def _normalize_compat_path(self, path: str | os.PathLike[str]) -> str:
        """把旧工具传过来的路径统一成虚拟路径格式（/ 开头）。

        Args:
            path: 原始路径，可以是相对路径或绝对路径。

        Returns:
            归一化后的虚拟路径字符串。
        """
        raw = str(path).strip().replace("\\", "/")
        if raw in {"", "."}:
            return "/"
        if raw.startswith("/"):
            return raw
        return "/" + raw

    def _resolve_virtual_path(self, path: str | os.PathLike[str]) -> Path:
        """把虚拟路径（如 ``/projects/my-repo``）转成真实文件系统路径。

        无论调用方传入虚拟路径还是 Windows 绝对路径，最终都必须解析成真实路径，
        并确认它仍然位于 ``self.root`` 工作区内。

        本地文件夹模式下额外允许解析到只读全局工作区，以支持读取全局资源；
        写操作是否越界由 ``_write_deny_reason`` 和各写方法的守卫负责。

        Args:
            path: 虚拟路径或 Windows 绝对路径。

        Returns:
            解析后的真实 ``Path`` 对象。

        Raises:
            PermissionError: 路径逃逸出工作区。
        """
        raw = str(path).strip() or "/"
        if self.local_folder_mode:
            return self._resolve_local_folder_path(raw)
        if re.match(r"^[A-Za-z]:[\\/]", raw):
            resolved = Path(raw).resolve()
        else:
            normalized = raw.replace("\\", "/").strip("/")
            if not normalized:
                return self.root
            first, _, rest = normalized.partition("/")
            roots = {
                "projects": self.projects_dir,
                "skills": self.skills_dir,
                "skills-global": self.global_skills_dir,
                "skills-set": self.project_set_skills_dir,
                "policies": self.policies_dir,
                "reviews": self.reviews_dir,
                "runtimes": self.runtimes_dir,
                "tmp": self.tmp_dir,
                "logs": self.logs_dir,
            }
            base = roots.get(first)
            if base is not None:
                resolved = (base / rest).resolve() if rest else base.resolve()
            else:
                resolved = (self.root / normalized).resolve()
        if not self._is_under_root(resolved):
            raise PermissionError(f"path outside local workspace is denied: {path}")
        return resolved

    def _local_virtual_roots(self) -> dict[str, Path]:
        """本地文件夹模式下虚拟前缀到真实目录的映射。

        ``projects`` 指向 root 本身（文件夹即项目根），``skills/memories/reviews``
        指向 ``.xihe-agent`` 下的对应目录，其余基础设施指向全局工作区。

        Returns:
            虚拟前缀名到真实目录的字典。
        """
        roots = {
            "projects": self.root,
            "skills": self.skills_dir,
            "skills-global": self.global_skills_dir,
            "skills-set": self.project_set_skills_dir,
            "reviews": self.reviews_dir,
            "policies": self.policies_dir,
            "runtimes": self.runtimes_dir,
            "tmp": self.tmp_dir,
            "logs": self.logs_dir,
        }
        return roots

    def _resolve_local_folder_path(self, raw: str) -> Path:
        """本地文件夹模式下的路径解析。

        放行 root（读写）与全局工作区（只读）两类路径，其余一律拒绝。

        Args:
            raw: 已 strip 的原始路径字符串。

        Returns:
            解析后的真实路径。

        Raises:
            PermissionError: 路径既不在 root 也不在全局工作区内。
        """
        if re.match(r"^[A-Za-z]:[\\/]", raw):
            resolved = Path(raw).resolve()
        else:
            normalized = raw.replace("\\", "/").strip("/")
            if not normalized:
                return self.root
            first, _, rest = normalized.partition("/")
            roots = self._local_virtual_roots()
            if first in roots:
                base = roots[first]
                resolved = (base / rest).resolve() if rest else base.resolve()
            else:
                resolved = (self.root / normalized).resolve()
        if self._is_under_root(resolved) or self._is_under_global(resolved):
            return resolved
        raise PermissionError(f"path outside local workspace is denied: {raw}")

    def _to_virtual_path(self, path: Path) -> str:
        """把真实路径转回虚拟路径（/ 开头）。

        DeepAgents 和模型不应该直接感知宿主机真实磁盘路径。

        Args:
            path: 真实文件系统路径。

        Returns:
            对应的虚拟路径字符串。

        Raises:
            PermissionError: 路径逃逸出工作区。
        """
        resolved = path.resolve()
        if self.local_folder_mode:
            return self._to_virtual_path_local(resolved)
        if not self._is_under_root(resolved):
            raise PermissionError(f"path outside local workspace is denied: {path}")
        # 非本地文件夹模式下 .project-set 就在 skills 目录内，若不先拦下来会被
        # 报成 /skills/.project-set/...，与挂载用的 /skills-set 前缀不一致。
        if self._is_under(resolved, self.project_set_skills_dir):
            rel = resolved.relative_to(self.project_set_skills_dir).as_posix()
            return f"/skills-set/{rel}" if rel else "/skills-set"
        rel = resolved.relative_to(self.root).as_posix()
        return "/" + rel if rel else "/"

    def _to_virtual_path_local(self, resolved: Path) -> str:
        """本地文件夹模式下把真实路径转回虚拟路径。

        匹配顺序有意设计为「项目本地技能 → 项目集合 → 全局技能 → 项目文件
        → 全局工作区」：

        * ``.xihe-agent/skills`` 映射为 ``/skills``；
        * ``.project-set`` 映射为 ``/skills-set``；
        * 全局技能目录映射为 ``/skills-global``；
        * root 下其余路径映射为 ``/projects/...``；
        * 全局工作区其余路径按其相对全局根的形式返回（如 ``/policies/...``）。

        顺序不能随意调整，两处都是「子目录必须先于父目录」：

        * 项目本地技能先于全局技能匹配，否则当全局工作区恰好是项目文件夹的
          父目录时，项目内技能会被误判为全局技能；
        * 项目集合先于全局技能匹配，因为 ``.project-set`` 就在全局技能目录
          内部，否则集合技能会被误判为全局技能。

        Args:
            resolved: 已解析的真实路径。

        Returns:
            虚拟路径字符串。

        Raises:
            PermissionError: 路径既不在 root 也不在全局工作区内。
        """
        if self._is_under(resolved, self.skills_dir):
            rel = resolved.relative_to(self.skills_dir).as_posix()
            return f"/skills/{rel}" if rel else "/skills"
        if self._is_under(resolved, self.project_set_skills_dir):
            rel = resolved.relative_to(self.project_set_skills_dir).as_posix()
            return f"/skills-set/{rel}" if rel else "/skills-set"
        if self._is_under(resolved, self.global_skills_dir):
            rel = resolved.relative_to(self.global_skills_dir).as_posix()
            return f"/skills-global/{rel}" if rel else "/skills-global"
        if self._is_under(resolved, self.reviews_dir):
            rel = resolved.relative_to(self.reviews_dir).as_posix()
            return f"/reviews/{rel}" if rel else "/reviews"
        if self._is_under_root(resolved):
            rel = resolved.relative_to(self.root).as_posix()
            return f"/projects/{rel}" if rel else "/projects"
        if self._is_under_global(resolved):
            rel = resolved.relative_to(self.global_workspace_root).as_posix()
            return "/" + rel if rel else "/"
        raise PermissionError(f"path outside local workspace is denied: {resolved}")

    def _is_under_root(self, path: Path) -> bool:
        """判断路径是否在工作区根目录之下。

        Args:
            path: 待检查的路径。

        Returns:
            路径在 root 下返回 True，否则返回 False。
        """
        try:
            path.relative_to(self.root)
            return True
        except ValueError:
            return False

    def _is_under_global(self, path: Path) -> bool:
        """判断路径是否在只读全局工作区之下（仅本地文件夹模式有意义）。

        Args:
            path: 待检查的路径。

        Returns:
            本地文件夹模式且路径在全局工作区下返回 True，否则返回 False。
        """
        if not self.local_folder_mode:
            return False
        return self._is_under(path, self.global_workspace_root)

    def _is_under(self, path: Path, parent: Path) -> bool:
        """判断 path 是否在 parent 目录之下。

        Args:
            path: 待检查的路径。
            parent: 父目录。

        Returns:
            path 在 parent 下返回 True，否则返回 False。
        """
        try:
            path.relative_to(parent)
            return True
        except ValueError:
            return False

    # ── 安全控制 ─────────────────────────────────────────────

    def _write_deny_reason(self, path: Path) -> str | None:
        """检查目录是否禁止写入。

        课程项目只允许模型主要修改 ``/projects`` 下的业务仓库。
        ``skills/policies/runtimes/logs/.secrets`` 都属于后端运行基础设施或规则资产，
        不允许在普通任务中被模型写入。

        本地文件夹模式下：写操作必须落在项目文件夹 ``root`` 内，全局工作区一律只读；
        ``.xihe-agent/skills`` 视为只读技能资产，``memories``/``reviews`` 与项目文件可写。

        Args:
            path: 待检查的目标路径。

        Returns:
            禁止写入时返回原因字符串，允许写入时返回 None。
        """
        if self.local_folder_mode:
            if not self._is_under(path, self.root):
                return "write denied: outside project folder"
            if self._is_under(path, self.skills_dir):
                return "write denied: skills are read-only"
            return None
        if self._is_under(path, self.policies_dir):
            return "write denied: policies are read-only"
        if self._is_under(path, self.skills_dir):
            return "write denied: skills are read-only"
        if self._is_under(path, self.global_skills_dir):
            return "write denied: skills are read-only"
        if self._is_under(path, self.project_set_skills_dir):
            return "write denied: skills are read-only"
        if self._is_under(path, self.runtimes_dir):
            return "write denied: runtimes are read-only"
        if self._is_under(path, self.logs_dir):
            return "write denied: logs are read-only"
        if self._is_under(path, self.secrets_dir):
            return "write denied: secrets are protected"
        return None

    def _is_writable(self, path: Path) -> bool:
        """测写入权限：尝试写一个临时文件，成功就表示可写。

        Args:
            path: 待测试的目录路径。

        Returns:
            可写返回 True，否则返回 False。
        """
        try:
            probe = path / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return True
        except OSError:
            return False

    def _deny_reason(self, command: str) -> str | None:
        """安全守卫：检测命令是否越界或使用了危险操作。

        这里是 ``normalize_safe_command()`` 之前的第一层粗粒度拒绝：
        - 拦截 ``..`` 路径穿越；
        - 拦截明显危险的系统命令；
        - 拦截指向工作区外的 Windows 绝对路径。

        Args:
            command: 待检查的原始命令字符串。

        Returns:
            命中拒绝规则时返回原因字符串，全部通过时返回 None。
        """
        lowered = command.lower()
        if _NESTED_PROJECTS_PATH_RE.search(command.replace("\\", "/")):
            return (
                "nested projects path is denied; "
                "use /projects/<repo> for file tools or <repo> in execute commands"
            )
        if "../" in command or "..\\" in command:
            return "path traversal outside workspace is denied"
        for pattern in _DANGEROUS_PATTERNS:
            if re.search(pattern, lowered):
                return f"dangerous command pattern matched: {pattern}"
        for match in _DRIVE_PATH_RE.finditer(command):
            candidate = Path(match.group(1)).resolve()
            # 本地文件夹模式允许命令引用只读全局工作区路径（如 type E:\ai_workspace\x）。
            if not self._is_under_root(candidate) and not self._is_under_global(candidate):
                return f"absolute path outside workspace: {candidate}"
        return None

    # ── 命令预处理 ───────────────────────────────────────────

    def _prepare_command(self, command: str, *, cwd_path: Path | None = None) -> str:
        """执行前的命令预处理：适配 Windows、替换虚拟路径。

        LLM 常按 Linux/Unix 习惯生成命令，例如 ``python3``、``pwd``、``test -d``。
        本方法负责把这些常见表达转换成 Windows 可执行形式，
        同时把 ``/projects/...`` 这样的虚拟路径替换为带引号的真实路径。

        Args:
            command: 原始命令字符串。
            cwd_path: 可选的命令执行目录。

        Returns:
            预处理后可在 Windows 上执行的命令字符串。
        """
        if cwd_path is not None and cwd_path.resolve() == self.projects_dir.resolve():
            command = self._normalize_command_for_projects_cwd(command)
        prepared = command.replace("python3 ", "python ")
        prepared = _VIRTUAL_PATH_RE.sub(self._virtual_command_path_replacement, prepared)
        if prepared.strip() == "pwd":
            return "cd"
        if prepared.startswith('printf %s "$HOME"'):
            return "echo %USERPROFILE%"
        if prepared.startswith("test -d "):
            return "echo ok"
        return self._prepare_git_command(prepared)

    def _normalize_command_for_projects_cwd(self, command: str) -> str:
        """修正默认在 /projects 目录执行时容易产生的重复相对路径。

        当命令默认在 ``E:\\ai_workspace\\projects`` 下执行时，
        如果命令中再写 ``projects/<repo>``，实际路径会变成重复的。
        这里把相对的 ``projects/<repo>`` 收敛成 ``<repo>``。

        Args:
            command: 原始命令字符串。

        Returns:
            修正重复路径后的命令字符串。
        """
        normalized = _RELATIVE_PROJECTS_PATH_RE.sub("", command)
        normalized = _RELATIVE_PROJECTS_DIR_RE.sub(".", normalized)
        if normalized != command:
            logger.info(
                "已修正 projects 工作目录下的重复相对路径：%s -> %s",
                _mask_token(command),
                _mask_token(normalized),
            )
        return normalized

    def _prepare_git_command(self, command: str) -> str:
        """给 git 命令注入 askpass 配置，避免弹窗或使用过期的凭据管理器。

        Args:
            command: 原始命令字符串。

        Returns:
            注入 askpass 配置后的命令字符串；非 git 命令原样返回。
        """
        stripped = command.lstrip()
        leading = command[: len(command) - len(stripped)]
        if stripped == "git":
            rest = ""
        elif stripped.lower().startswith("git "):
            rest = stripped[4:]
        else:
            return command
        askpass = str(self.secrets_dir / "gitee_askpass.cmd")
        return f'{leading}git -c credential.helper= -c core.askPass="{askpass}" {rest}'.rstrip()

    def _prepare_run_command(self, command: str, cwd: str) -> tuple[Path, str]:
        """准备 ``run()`` 的命令：解析 cwd、兼容 ``cd``、安全检查、规范化。

        Args:
            command: 原始命令字符串。
            cwd: 执行目录，支持虚拟路径。

        Returns:
            ``(执行目录 Path, 预处理后的命令)`` 元组。
        """
        cwd_path = self._resolve_virtual_path(self._normalize_compat_path(cwd))
        command = re.sub(r"\s+2>&1(?=\s*(?:&&|\|\||$))", "", command.strip())
        first_part = command.split("&&", 1)[0].strip()
        cd_match = re.fullmatch(r"cd\s+(.+)", first_part, flags=re.IGNORECASE)
        if cd_match:
            cwd_path = self._resolve_virtual_path(
                self._normalize_compat_path(cd_match.group(1).strip().strip('"').strip("'"))
            )
            command = command.split("&&", 1)[1].strip() if "&&" in command else ""
        if not command:
            raise ValueError("Command cannot be empty")
        if self.command_guard_enabled:
            denied = self._deny_reason(command)
            if denied:
                raise PermissionError(f"命令被拒绝：{denied}")
        safe_command = normalize_safe_command(command)
        return cwd_path, self._prepare_command(safe_command, cwd_path=cwd_path)

    def _virtual_command_path_replacement(self, match: re.Match[str]) -> str:
        """把命令中的 ``/projects/xxx`` 等虚拟路径替换为真实路径（带引号防空格）。

        Args:
            match: 正则匹配对象，包含虚拟路径及其后缀。

        Returns:
            替换后的带引号真实路径字符串。
        """
        roots = self._local_virtual_roots() if self.local_folder_mode else {
            "projects": self.projects_dir,
            "skills": self.skills_dir,
            "skills-global": self.global_skills_dir,
            "skills-set": self.project_set_skills_dir,
            "policies": self.policies_dir,
            "reviews": self.reviews_dir,
            "runtimes": self.runtimes_dir,
            "tmp": self.tmp_dir,
            "logs": self.logs_dir,
        }
        root = roots[match.group(1)]
        suffix = (match.group(2) or "").lstrip("/")
        path = (root / Path(*suffix.split("/"))).resolve() if suffix else root
        if not self._is_under_root(path) and not self._is_under_global(path):
            raise PermissionError(f"virtual path escapes workspace: {match.group(0)}")
        return f'"{path}"'

    def _execution_env(self) -> dict[str, str]:
        """构造子进程环境变量：注入 venv、Git 安全配置、Gitee 认证信息。

        Returns:
            包含完整环境变量配置的字典。
        """
        env = os.environ.copy()
        scripts = self.shared_python_venv / "Scripts"
        if scripts.exists():
            env["PATH"] = f"{scripts}{os.pathsep}{env.get('PATH', '')}"
            env["VIRTUAL_ENV"] = str(self.shared_python_venv)
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GCM_INTERACTIVE"] = "Never"
        gitee_token = _get_env("GITEE_TOKEN").strip() or _get_env("SCM_GITEE_TOKEN").strip()
        if gitee_token:
            env["GIT_ASKPASS"] = str(self.secrets_dir / "gitee_askpass.cmd")
            env["GITEE_ASKPASS_USERNAME"] = "oauth2"
            env["GITEE_ASKPASS_TOKEN"] = gitee_token
        return env


# ── 模块级单例 ────────────────────────────────────────────

_BACKEND: LocalShellBackend | None = None


def get_local_shell_backend() -> LocalShellBackend:
    """获取全局唯一的 LocalShellBackend 实例（单例）。

    工作区可在设置页运行时修改。缓存的 backend 若仍指向旧目录，就必须重建，
    否则新会话仍会把仓库创建到应用启动时的默认工作区。
    
    Returns:
        全局 ``LocalShellBackend`` 实例。
    """
    global _BACKEND
    from config.app_config import get_settings

    configured_root = get_settings().workspace_root.expanduser().resolve()
    if _BACKEND is None or _BACKEND.root != configured_root:
        _BACKEND = LocalShellBackend(workspace=configured_root)
    return _BACKEND


def create_local_shell_backend(_sandbox_id: str | None = None) -> LocalShellBackend:
    """DeepAgents 工厂函数：创建（或复用）backend 实例。
    
    Args:
        _sandbox_id: 保留参数，未使用。
    
    Returns:
        全局 ``LocalShellBackend`` 实例。
    """
    return get_local_shell_backend()


def validate_local_shell_startup_config() -> None:
    """启动时验证：确保 backend 可以正常创建。

    通过尝试获取全局单例来触发初始化流程，
    如果工作区目录结构或环境变量配置有误，此方法会在启动阶段提前暴露问题。
    """
    get_local_shell_backend()
