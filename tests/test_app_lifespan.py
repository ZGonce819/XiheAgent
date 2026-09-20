"""应用生命周期的测试。

只覆盖一件事：常驻调度有没有被正确地拉起、什么条件下不拉起、拉不起来时会不会
把整个服务带下去。真实的调度器会去扫真实的自动化定义并真跑一轮，所以这里一律
换成记录器。

Note:
    ``app`` 模块在导入时会装默认技能、配置日志。测试不该动开发机上真实的技能目录，
    也不该把测试日志写进 ``logs/backend.log``，因此夹具会先把这两件事换掉 ——
    这一步**必须在导入 app 之前**。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest


def drive_lifespan() -> None:
    """把应用生命周期完整走一遍：启动 → 关闭。"""
    import app as app_module

    async def run() -> None:
        async with app_module.lifespan(app_module.app):
            pass

    asyncio.run(run())


@pytest.fixture
def scheduler_calls(
    monkeypatch: pytest.MonkeyPatch, data_root: Any
) -> list[dict[str, Any]]:
    """把全局调度器换成记录器。

    Returns:
        依次记下启动参数与停止动作的容器。
    """
    calls: list[dict[str, Any]] = []

    class FakeScheduler:
        def stop(self) -> None:
            calls.append({"stopped": True})

    def fake_start(**kwargs: Any) -> FakeScheduler:
        calls.append(kwargs)
        return FakeScheduler()

    monkeypatch.setattr("config.logging_config.configure_logging", lambda: None)
    monkeypatch.setattr("services.skill_installer.ensure_default_skills", lambda: [])
    monkeypatch.setattr("services.automation_scheduler.start_scheduler", fake_start)
    return calls


def test_lifespan_starts_and_stops_the_scheduler(
    scheduler_calls: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """默认开着：启动时拉起，关闭时停掉。"""
    from config.app_config import get_settings

    monkeypatch.setattr(get_settings(), "automation_scheduler_enabled", True)
    monkeypatch.setattr(get_settings(), "automation_tick_seconds", 7)

    drive_lifespan()

    assert scheduler_calls[0] == {"tick_seconds": 7}
    assert scheduler_calls[1] == {"stopped": True}


def test_lifespan_skips_the_scheduler_when_disabled(
    scheduler_calls: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """关掉开关就不拉起，也不会去停一个没起来的调度器。"""
    from config.app_config import get_settings

    monkeypatch.setattr(get_settings(), "automation_scheduler_enabled", False)

    drive_lifespan()

    assert scheduler_calls == []


def test_a_broken_scheduler_does_not_take_the_service_down(
    scheduler_calls: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """调度起不来不能让整个服务起不来 —— 手动触发和别的功能都不依赖它。"""
    from config.app_config import get_settings

    monkeypatch.setattr(get_settings(), "automation_scheduler_enabled", True)

    def boom(**kwargs: Any) -> Any:
        raise RuntimeError("调度起不来")

    monkeypatch.setattr("services.automation_scheduler.start_scheduler", boom)

    drive_lifespan()

    assert scheduler_calls == []
