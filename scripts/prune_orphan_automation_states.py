"""清理 ``data/automation_state/`` 下没有对应定义的孤儿状态文件。

删除自动化任务时会连同状态文件一起删（见 ``api/dashboard.py``），但历史上留下的
孤儿文件不会自动消失：界面上一个任务都没有，磁盘上却堆着一批点不进去的历史。
这个脚本一次性把它们收走。

**默认归档不删除**：这些文件里可能有真实跑过的记录，删掉不可恢复。归档目录程序
不读它，纯手工逃生口。确认不需要了再加 ``--delete``。

用法::

    python scripts/prune_orphan_automation_states.py
    python scripts/prune_orphan_automation_states.py --delete
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.automation_config import list_automations  # noqa: E402
from config.automation_state import (  # noqa: E402
    list_state_ids,
    orphan_state_ids,
    prune_orphan_states,
    state_dir,
)

_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    """按命令行参数收走孤儿状态文件。

    Returns:
        进程退出码；没有孤儿时返回 ``0``。
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--delete",
        action="store_true",
        help="直接删除而不是归档。不可恢复，确认过再传。",
    )
    parser.add_argument(
        "--archive-dir",
        default=None,
        help="归档目录；默认 data/_orphaned_state_<今天>。",
    )
    args = parser.parse_args()

    known = {str(item.get("id") or "") for item in list_automations()}
    known.discard("")
    before = list_state_ids()
    orphans = orphan_state_ids(known)

    print(f"状态目录：{state_dir()}")
    print(
        f"仍然存在的任务 {len(known)} 个；状态文件 {len(before)} 个；"
        f"其中没有对应定义的 {len(orphans)} 个"
    )
    if not orphans:
        print("没有需要清理的，什么都没做。")
        return 0

    archive: Path | str | None = None
    if not args.delete:
        archive = args.archive_dir or (_ROOT / "data" / f"_orphaned_state_{date.today()}")

    moved = prune_orphan_states(known, archive_dir=archive)
    if archive is None:
        print(f"已删除 {len(moved)} 个。")
    else:
        print(f"已归档 {len(moved)} 个到 {archive}（确认不需要了自己删掉那个目录）")
    print(f"状态目录里还剩 {len(list_state_ids())} 个状态文件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
