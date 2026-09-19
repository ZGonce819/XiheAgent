"""Move project files into the workspace recycle bin."""

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
    """Move one project path into a timestamped workspace recycle directory."""

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
