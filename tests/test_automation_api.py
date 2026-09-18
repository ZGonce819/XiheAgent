"""自动化任务的循环状态接口与手动触发接口的测试。

覆盖 ``GET /dashboard/api/schedules/{id}/state``、
``POST /dashboard/api/schedules/{id}/run``、
``POST /dashboard/api/schedules/{id}/resume`` 与
``PATCH /dashboard/api/schedules/{id}/budget``。

Note:
    「能不能跑」的判断要调模型（任务类型判定），所以这里**一律打桩**，测的是接口
    拿到判断结果之后的处置：状态码对不对、理由给没给、活有没有派下去。
    ``run_cycle`` 也换成记录器 —— 真跑一轮要模型和数据库，几分钟起步。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from config.automation_config import create_automation, get_automation
from config.automation_state import (
    clear_stopped,
    mark_stopped,
    record_run,
    set_budget,
    state_path,
)

REPO = "https://example.com/a/b.git"


@pytest.fixture
def dashboard(monkeypatch: pytest.MonkeyPatch, data_root) -> Iterator[TestClient]:
    """一个只挂了仪表盘路由的测试客户端，任务类型判定固定为「项目分析」。"""
    import api.dashboard as dashboard_module

    monkeypatch.setattr(
        "utils.intent_utils.classify_task_kind", lambda instruction: "analysis"
    )
    app = FastAPI()
    app.include_router(dashboard_module.dashboard_router)
    yield TestClient(app)


@pytest.fixture
def dispatched(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """拦下整轮执行，返回每次被派发的参数。"""
    import api.dashboard as dashboard_module

    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(
        dashboard_module,
        "run_cycle",
        lambda automation, runner, **kwargs: seen.append(
            {"automation": automation, "runner": runner, **kwargs}
        ),
    )
    return seen


def make_automation(**overrides: Any) -> dict[str, Any]:
    """建一个能跑通的自动化定义，按需覆盖字段。"""
    payload: dict[str, Any] = {
        "name": "每日分诊",
        "prompt": "分析这个仓库的目录结构，列出主要模块",
        "schedule": "0 9 * * *",
        "repo": REPO,
        "checks": [],
    }
    payload.update(overrides)
    return create_automation(payload)


def test_state_returns_404_for_unknown_automation(dashboard: TestClient) -> None:
    """状态接口对不存在的任务返回 404。"""
    assert dashboard.get("/dashboard/api/schedules/nope/state").status_code == 404


def test_run_returns_404_for_unknown_automation(dashboard: TestClient) -> None:
    """触发接口对不存在的任务返回 404。"""
    assert dashboard.post("/dashboard/api/schedules/nope/run").status_code == 404


def test_run_refuses_write_tasks(
    dashboard: TestClient, monkeypatch: pytest.MonkeyPatch, dispatched: list[dict[str, Any]]
) -> None:
    """超出只读范围的任务当场拒掉，不能先回 202 再在后台失败。"""
    monkeypatch.setattr(
        "utils.intent_utils.classify_task_kind", lambda instruction: "coding"
    )
    automation = make_automation(prompt="实现一个登录页并提交代码")
    response = dashboard.post(f"/dashboard/api/schedules/{automation['id']}/run")
    assert response.status_code == 400
    assert "只读任务" in response.json()["detail"]
    assert dispatched == []


def test_run_refuses_without_repo(
    dashboard: TestClient, dispatched: list[dict[str, Any]]
) -> None:
    """没配代码仓库时拒掉，理由要说清。"""
    automation = make_automation(repo=None)
    response = dashboard.post(f"/dashboard/api/schedules/{automation['id']}/run")
    assert response.status_code == 400
    assert "代码仓库" in response.json()["detail"]
    assert dispatched == []


def test_run_refuses_broken_checks(
    dashboard: TestClient, dispatched: list[dict[str, Any]]
) -> None:
    """检查项配置有误时拒掉 —— 否则每一轮都会以「检查项配错」白白结束。"""
    automation = make_automation(checks=[{"command": "   "}])
    response = dashboard.post(f"/dashboard/api/schedules/{automation['id']}/run")
    assert response.status_code == 400
    assert "检查项配置有误" in response.json()["detail"]
    assert dispatched == []


def test_run_accepts_and_dispatches(
    dashboard: TestClient, dispatched: list[dict[str, Any]]
) -> None:
    """能跑时返回 202，把整轮派下去，并把这次触发记进定义。"""
    automation = make_automation()
    response = dashboard.post(f"/dashboard/api/schedules/{automation['id']}/run")

    assert response.status_code == 202
    body = response.json()
    assert body["accepted"] is True
    assert body["roundNumber"] == 1
    assert body["threadId"]

    assert [item["automation"]["id"] for item in dispatched] == [automation["id"]]
    saved = get_automation(automation["id"]) or {}
    assert saved["lastThreadId"] == body["threadId"]
    assert saved["lastTriggeredAt"]
    assert saved["lastError"] is None


def test_run_passes_the_execution_environment_to_the_cycle(
    dashboard: TestClient, dispatched: list[dict[str, Any]]
) -> None:
    """执行环境要显式传给循环 —— 验证门的审批策略按它判断。

    不传的话 ``run_cycle`` 会用缺省值 ``local``，在开了沙箱的部署里会用错一套策略。
    """
    automation = make_automation()
    dashboard.post(f"/dashboard/api/schedules/{automation['id']}/run")
    assert dispatched[0]["backend_type"] in {"local", "sandbox"}


def test_state_does_not_create_the_state_file(dashboard: TestClient) -> None:
    """读状态是只读操作，不能顺手把状态文件建出来。"""
    automation = make_automation()
    response = dashboard.get(f"/dashboard/api/schedules/{automation['id']}/state")
    assert response.status_code == 200
    assert response.json()["stopped"] is False
    assert isinstance(response.json()["text"], str)
    assert not state_path(automation["id"]).exists()


def test_run_refuses_when_the_loop_is_stopped(
    dashboard: TestClient, dispatched: list[dict[str, Any]]
) -> None:
    """循环已经停了就不该再被触发，理由要带上为什么停的。"""
    automation = make_automation()
    mark_stopped(automation["id"], "连续 3 轮没通过验证")

    response = dashboard.post(f"/dashboard/api/schedules/{automation['id']}/run")
    assert response.status_code == 409
    assert "连续 3 轮没通过验证" in response.json()["detail"]
    assert dispatched == []

    state = dashboard.get(f"/dashboard/api/schedules/{automation['id']}/state")
    assert state.json()["stopped"] is True


def test_resume_returns_404_for_unknown_automation(dashboard: TestClient) -> None:
    """恢复接口对不存在的任务返回 404。"""
    assert dashboard.post("/dashboard/api/schedules/nope/resume").status_code == 404


def test_resume_clears_the_stop_and_makes_the_loop_runnable_again(
    dashboard: TestClient, dispatched: list[dict[str, Any]]
) -> None:
    """手动停下的循环，恢复之后要真的能再触发 —— 不能只是状态变好看。"""
    automation = make_automation()
    mark_stopped(automation["id"], "先停一下看看")

    assert (
        dashboard.post(f"/dashboard/api/schedules/{automation['id']}/run").status_code
        == 409
    )

    resumed = dashboard.post(f"/dashboard/api/schedules/{automation['id']}/resume")
    assert resumed.status_code == 200
    assert resumed.json()["stopped"] is False
    assert resumed.json()["stopSource"] is None

    assert (
        dashboard.post(f"/dashboard/api/schedules/{automation['id']}/run").status_code
        == 202
    )
    assert len(dispatched) == 1


def test_resume_on_a_running_loop_is_harmless(dashboard: TestClient) -> None:
    """没停过的循环调恢复接口不该报错 —— 按钮和状态之间本来就有窗口期。"""
    automation = make_automation()
    response = dashboard.post(f"/dashboard/api/schedules/{automation['id']}/resume")
    assert response.status_code == 200
    assert response.json()["stopped"] is False


def test_resume_refuses_when_the_stop_came_from_the_budget(
    dashboard: TestClient,
) -> None:
    """轮数用完而停下的，清标记没有用 —— 要说清该改哪里，不能假装恢复成功。"""
    automation = make_automation()
    set_budget(automation["id"], max_runs=1)
    record_run(automation["id"], outcome="succeeded")

    response = dashboard.post(f"/dashboard/api/schedules/{automation['id']}/resume")
    assert response.status_code == 409
    assert "预算上限" in response.json()["detail"]

    # 状态没被动过，仍然是停着的 —— 不能因为恢复失败就把它清成没停。
    body = dashboard.get(f"/dashboard/api/schedules/{automation['id']}/state").json()
    assert body["stopped"] is True
    assert body["stopSource"] == "max_runs"


def test_state_reports_where_the_stop_came_from(dashboard: TestClient) -> None:
    """四种停止来源的恢复方式不同，所以来源要一起给出来。"""
    automation = make_automation()

    mark_stopped(automation["id"], "手动停的")
    body = dashboard.get(f"/dashboard/api/schedules/{automation['id']}/state").json()
    assert body["stopSource"] == "marked"

    clear_stopped(automation["id"])
    set_budget(automation["id"], max_consecutive_failures=1)
    record_run(automation["id"], outcome="failed")
    body = dashboard.get(f"/dashboard/api/schedules/{automation['id']}/state").json()
    assert body["stopSource"] == "failures"


def test_budget_returns_404_for_unknown_automation(dashboard: TestClient) -> None:
    """预算接口对不存在的任务返回 404。"""
    response = dashboard.patch(
        "/dashboard/api/schedules/nope/budget", json={"maxRuns": 1}
    )
    assert response.status_code == 404


def test_budget_updates_only_what_was_given(dashboard: TestClient) -> None:
    """只改给出来的那一个上限，其余保持原值。"""
    automation = make_automation()
    set_budget(automation["id"], max_runs=5, max_consecutive_failures=3)

    response = dashboard.patch(
        f"/dashboard/api/schedules/{automation['id']}/budget",
        json={"maxTokens": 1000},
    )
    assert response.status_code == 200
    budget = response.json()["state"]["budget"]
    assert budget["maxTokens"] == 1000
    assert budget["maxRuns"] == 5
    assert budget["maxConsecutiveFailures"] == 3


def test_budget_accepts_a_numeric_string(dashboard: TestClient) -> None:
    """表单里的数字是以字符串提交的，要收。"""
    automation = make_automation()
    response = dashboard.patch(
        f"/dashboard/api/schedules/{automation['id']}/budget",
        json={"maxRuns": "7"},
    )
    assert response.status_code == 200
    assert response.json()["state"]["budget"]["maxRuns"] == 7


def test_budget_refuses_an_empty_body(dashboard: TestClient) -> None:
    """一个上限都没给就拒掉 —— 否则调用方以为改了，其实什么都没发生。"""
    automation = make_automation()
    response = dashboard.patch(
        f"/dashboard/api/schedules/{automation['id']}/budget", json={}
    )
    assert response.status_code == 400
    assert "至少要给一个上限" in response.json()["detail"]


@pytest.mark.parametrize("value", [-1, True, 1.5, "abc", None])
def test_budget_refuses_values_that_are_not_whole_numbers(
    dashboard: TestClient, value: Any
) -> None:
    """负数、布尔、小数、非数字字符串一律拒掉，不静默取整、也不把 true 当成 1。"""
    automation = make_automation()
    response = dashboard.patch(
        f"/dashboard/api/schedules/{automation['id']}/budget",
        json={"maxRuns": value},
    )
    assert response.status_code == 400
    # 报错要点名是哪个字段，否则表单上三个输入框，用户不知道该改哪个。
    assert "maxRuns" in response.json()["detail"]


def test_raising_the_limit_is_the_way_back_from_a_budget_stop(
    dashboard: TestClient, dispatched: list[dict[str, Any]]
) -> None:
    """因轮数用完而停下的循环，调高上限之后要真的能再跑。

    这是「继续运行」按钮救不了的那一类 —— 界面必须给得出正确的下一步，
    而不是让用户去点一个只会返回 409 的按钮。
    """
    automation = make_automation()
    set_budget(automation["id"], max_runs=1)
    record_run(automation["id"], outcome="succeeded")

    assert (
        dashboard.post(f"/dashboard/api/schedules/{automation['id']}/resume").status_code
        == 409
    )

    raised = dashboard.patch(
        f"/dashboard/api/schedules/{automation['id']}/budget",
        json={"maxRuns": 5},
    )
    assert raised.status_code == 200
    assert raised.json()["stopped"] is False
    assert raised.json()["stopSource"] is None

    assert (
        dashboard.post(f"/dashboard/api/schedules/{automation['id']}/run").status_code
        == 202
    )
    assert len(dispatched) == 1
