"""方案生成路由。

负责为编码需求生成技术方案。方案作为普通 Agent 回答直接展示在前端，
不再保存到独立的 thread_plans 表或 Markdown 文件。

核心流程：
1. 解析仓库地址（Gitee / GitHub / Codeup）并创建/更新 thread 记录。
2. 构建 task_kind=planning 的 DeepAgent（只读模式）。
3. 通过 streaming_runtime 消费事件流，前端实时展示方案正文。
4. 方案输出后等待用户确认，确认后才进入 coding 流程。
5. 支持方案修订：用户提出补充要求时，基于上一版方案输出完整新版。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from agent.graph.graph import get_store
from agent.runtime._helpers import (
    build_agent_for_runtime,
    build_plan_user_content,
    extract_best_plan_text,
    plan_source_prompt,
    revision_source_prompt,
)
from agent.runtime.events import record_event
from agent.runtime.streaming_runtime import run_agent_with_event_stream
from utils.codeup_utils import parse_codeup_repo_url
from utils.gitee_utils import mask_token, parse_gitee_repo_url
from utils.github_utils import parse_github_repo_url

logger = logging.getLogger(__name__)

RuntimeEventSink = Callable[[str, dict[str, Any]], None]


def _parse_repo_url(repo_url: str):
    """根据 URL 域名自动识别 SCM 平台并解析仓库信息。"""
    parsed = urlparse(repo_url.strip())
    hostname = (parsed.hostname or "").lower()
    if hostname in {"codeup.aliyun.com", "www.codeup.aliyun.com"}:
        return parse_codeup_repo_url(repo_url)
    if hostname in {"github.com", "www.github.com"}:
        return parse_github_repo_url(repo_url)
    return parse_gitee_repo_url(repo_url)


def run_plan_response_task(
    *,
    repo_url: str,
    prompt: str,
    thread_id: str | None = None,
    previous_plan_message: dict[str, Any] | None = None,
    revision_prompt: str | None = None,
    event_sink: RuntimeEventSink | None = None,
) -> dict[str, Any]:
    """为编码需求生成技术方案，并把方案作为普通回答直接展示。

    方案正文由 DeepAgents 写入 checkpoint，前端通过事件流实时展示，刷新后也从
    checkpoint 恢复历史。用户后续输入"确认"时，同样从 checkpoint 读取上一条方案。
    如果传入 previous_plan_message，则表示用户在等待确认阶段提出了补充要求；
    此时会基于上一版方案重新输出完整新版方案，而不是只输出差异。

    这个函数始终以 planning task_kind 运行。即使用户原始需求看起来像 coding，
    只要还没有确认方案，就只能生成方案，不能改文件、不能提交、不能创建 PR。
    """

    thread_id = thread_id or str(uuid.uuid4())
    store = get_store()
    store.clear_run_events(thread_id)
    logger.info("开始生成技术方案：thread_id=%s repo_url=%s", thread_id, repo_url)
    record_event(thread_id, "created", "任务已创建", status="completed")
    record_event(thread_id, "repo", "解析仓库", status="in_progress")
    repo = _parse_repo_url(repo_url)
    record_event(thread_id, "repo", "解析仓库", status="completed")

    plan_source = prompt
    previous_plan_text: str | None = None
    if previous_plan_message is not None:
        # 方案修订场景：保留上一版方案正文，同时合并用户本轮补充要求。
        # 这样模型能输出"完整新版方案"，而不是只输出增量描述。
        previous_plan_text = str(previous_plan_message.get("content") or "").strip()
        plan_source = revision_source_prompt(
            previous_source_prompt=plan_source_prompt(previous_plan_message, prompt),
            revision_prompt=revision_prompt or prompt,
        )
    store.upsert_thread(
        thread_id=thread_id,
        title=(revision_prompt or prompt)[:80] or f"{repo.owner}/{repo.repo}",
        # user_prompt 只保存本轮用户真实输入，用于 Dashboard user_message 展示。
        # plan_source 是方案生成/后续实施的完整需求，不能混用为前端展示文本。
        user_prompt=revision_prompt or prompt,
        repo_url=repo.clone_url,
        repo_owner=repo.owner,
        repo_name=repo.repo,
        latest_run_status="running",
    )
    run_id = str(uuid.uuid4())
    store.record_run(run_id=run_id, thread_id=thread_id, status="running")
    record_event(thread_id, "agent", "构建方案生成 Agent", status="in_progress")
    # 方案任务也使用 DeepAgent，是因为它需要读取仓库结构、测试方式、关键文件等上下文。
    # 但 task_kind 固定为 planning，中间件和工具权限会保持只读。
    agent = build_agent_for_runtime(thread_id=thread_id, task_kind="planning", repo_url=repo.clone_url)
    record_event(thread_id, "agent", "构建方案生成 Agent", status="completed")

    try:
        # 事件流消费由 streaming_runtime.py 负责。runtime 只关心最终是否成功、
        # 以及最终 messages 里是否能提取到一份可确认的技术方案。
        result = run_agent_with_event_stream(
            agent=agent,
            thread_id=thread_id,
            run_id=run_id,
            content=build_plan_user_content(
                repo_url=repo.clone_url,
                prompt=plan_source_prompt(previous_plan_message, prompt)
                if previous_plan_message is not None
                else prompt,
                previous_plan=previous_plan_text,
                revision_prompt=revision_prompt,
            ),
            task_kind="planning",
            event_sink=event_sink,
        )
        store.finish_open_run_events(thread_id, status="completed")
        messages = result.get("messages", [])
        if not extract_best_plan_text(messages):
            raise RuntimeError("技术方案生成失败：模型没有返回可用方案")
        store.update_thread_status(thread_id, "completed")
        store.record_run(run_id=run_id, thread_id=thread_id, status="completed", finished=True)
        record_event(thread_id, "plan", "技术方案已输出，等待确认", kind="other", status="completed")
        logger.info("技术方案输出完成：thread_id=%s", thread_id)
        return {
            "thread_id": thread_id,
            "run_id": run_id,
            "status": "completed",
        }
    except Exception as exc:
        store.finish_open_run_events(thread_id, status="error")
        store.update_thread_status(thread_id, "failed")
        record_event(thread_id, "failed", "技术方案生成失败", status="error", detail=mask_token(str(exc)))
        store.record_run(
            run_id=run_id,
            thread_id=thread_id,
            status="failed",
            error=mask_token(str(exc)),
            finished=True,
        )
        logger.exception("技术方案生成失败：thread_id=%s run_id=%s error=%s", thread_id, run_id, mask_token(str(exc)))
        raise
