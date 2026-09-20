"""将项目文件移入工作区回收站。

提供安全的文件删除能力：把待删除文件移动到带时间戳的回收目录，
而不是直接删除。支持从回收站恢复，并防止误删回收站内的文件。
"""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta
from pathlib import Path


def move_to_recycle_bin(
    source: Path | str,
    *,
    project_root: Path | str,
    workspace_root: Path | str,
    now: datetime | None = None,
) -> Path:
    """将指定项目路径移入带时间戳的工作区回收目录。

    移动前会校验路径合法性：路径必须存在、必须在项目目录内、不能是项目根目录本身、
    不能已经在回收站内。目标目录按秒级时间戳分桶，同名文件自动顺延一秒避免覆盖。

    Args:
        source: 待移动的文件或目录路径。
        project_root: 项目根目录，用于校验 source 是否在合法范围内。
        workspace_root: 工作区根目录，回收站创建在该目录下的 ``recyclebin`` 子目录。
        now: 可选的当前时间，用于生成时间戳目录名；不传时取 ``datetime.now()``。

    Returns:
        文件移动后的目标路径。

    Raises:
        FileNotFoundError: 源路径不存在时抛出。
        PermissionError: 源路径在项目外、是项目根目录、或已在回收站内时抛出。
    """

    source_path = Path(source).expanduser().resolve()
    project_path = Path(project_root).expanduser().resolve()
    workspace_path = Path(workspace_root).expanduser().resolve()
    recycle_root = (workspace_path / "recyclebin").resolve()

    if not source_path.exists() and not source_path.is_symlink():
        raise FileNotFoundError(f"path does not exist: {source}")
    try:
        relative = source_path.relative_to(project_path)
    except ValueError as exc:
        raise PermissionError(f"path is outside the project: {source}") from exc
    if relative == Path("."):
        raise PermissionError("deleting the project root is not allowed")
    try:
        source_path.relative_to(recycle_root)
    except ValueError:
        pass
    else:
        raise PermissionError("deleting files from recyclebin is not allowed")

    timestamp = now or datetime.now()
    while True:
        bucket = recycle_root / timestamp.strftime("%Y-%m-%d-%H-%M-%S")
        destination = bucket / project_path.name / relative
        if not destination.exists() and not destination.is_symlink():
            break
        timestamp += timedelta(seconds=1)

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source_path), str(destination))
    return destination
