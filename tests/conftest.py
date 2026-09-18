"""后端测试的公共夹具。

测试集中在「技能分层 → 沙箱落点」这条链路上，因此统一把
``Settings.skills_dir`` 指向临时目录，避免读写开发机上真实的技能目录。
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

import pytest

# 本项目的顶层包直接位于仓库根目录（而非 src/ 布局），pytest 从 tests/ 收集时
# 需要显式把根目录补进 sys.path，否则 import services.* 会失败。
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def write_skill(directory: Path, name: str, *, description: str | None = None) -> Path:
    """在指定目录下写一个最小可用的技能。

    Args:
        directory: 技能目录（会自动创建）。
        name: 技能名，需与目录名一致。
        description: frontmatter 里的说明；为空时由函数生成。

    Returns:
        写入的技能目录。
    """
    directory.mkdir(parents=True, exist_ok=True)
    summary = description if description is not None else f"{name} 的说明"
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {summary}\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    return directory


@pytest.fixture
def skills_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把全局技能目录指向临时目录，并返回该目录。

    Returns:
        临时的全局技能根目录。
    """
    from config.app_config import get_settings

    root = tmp_path / "skills"
    root.mkdir()
    monkeypatch.setattr(get_settings(), "_skills_dir", root)
    return root


@pytest.fixture
def make_skill() -> Callable[..., Path]:
    """返回「在任意目录写技能」的工厂函数。

    Returns:
        :func:`write_skill` 本身，供测试直接调用。
    """
    return write_skill
