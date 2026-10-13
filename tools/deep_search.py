"""多轮联网搜索与来源交叉验证工具。"""

from __future__ import annotations

import json
import re
import threading
from collections import Counter, defaultdict
from urllib.parse import urlparse

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from config.constants import DEEP_SEARCH_DEFAULT_ROUNDS
from utils.context_utils import get_runtime_thread_id
from .web_search import _web_search_impl

_URL_RE = re.compile(r"https?://[^\s)\]}>]+")

# 每线程（单题目）deep_search 调用预算：防止模型把搜索循环转移到 deep_search
# （deep_search 每次内部默认 3 轮搜索，3 次即 9 轮，足以完成交叉验证）。
MAX_DEEP_SEARCH_PER_TASK = 3

_DEEP_SEARCH_COUNT: dict[str, int] = {}
_DEEP_SEARCH_COUNT_LOCK = threading.Lock()


def _consume_deep_search_budget(thread_id: str) -> int:
    """为指定线程的 deep_search 调用计数自增，返回当前累计次数（1 起）。

    Args:
        thread_id: 当前线程 ID；为空时使用固定占位键。

    Returns:
        累计调用次数。
    """
    key = thread_id or "__no_thread__"
    with _DEEP_SEARCH_COUNT_LOCK:
        _DEEP_SEARCH_COUNT[key] = _DEEP_SEARCH_COUNT.get(key, 0) + 1
        return _DEEP_SEARCH_COUNT[key]


# 修复轮补充的 deep_search 额度：进入最终答案修复轮时允许再做 1 次深度检索。
REPAIR_DEEP_SEARCH_REFILL = 1


def refill_deep_search_budget(thread_id: str, amount: int = REPAIR_DEEP_SEARCH_REFILL) -> None:
    """为指定线程补充 deep_search 调用额度（供最终答案修复轮使用）。

    Args:
        thread_id: 当前线程 ID；为空时使用固定占位键。
        amount: 补充的调用次数，默认 REPAIR_DEEP_SEARCH_REFILL。
    """
    key = thread_id or "__no_thread__"
    with _DEEP_SEARCH_COUNT_LOCK:
        _DEEP_SEARCH_COUNT[key] = max(0, _DEEP_SEARCH_COUNT.get(key, 0) - amount)


def _extract_sources(text: str) -> list[dict[str, str]]:
    """按 URL 邻近文本切分来源，避免把整页摘要复制给每个 URL。

    摘要截断到 600 字符/条，控制子代理报告回填主上下文的体积
    （此前 1200 字符/条；deep_search 每轮 3 次搜索，12 条记录全量回填会显著
    放大后续每轮模型请求）。
    """
    matches = list(_URL_RE.finditer(text))
    if not matches:
        return [{"url": "", "snippet": text[:600]}] if text else []
    sources: list[dict[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sources.append({"url": match.group(0).rstrip(".,;:)").strip(), "snippet": text[match.start():end].strip()[:600]})
    return sources


class DeepSearchSchema(BaseModel):
    query: str = Field(description="需要深入研究的问题")
    rounds: int = Field(default=DEEP_SEARCH_DEFAULT_ROUNDS, ge=2, le=5, description="搜索轮数，默认 3 轮")


def _next_query(base: str, previous: list[dict[str, str]], round_index: int) -> str:
    """根据上一轮来源内容生成下一轮查询，形成真正的轮间依赖。"""
    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}|[\u4e00-\u9fff]{2,}", " ".join(
        item.get("snippet", "") for item in previous
    ).lower())
    stop = {"this", "that", "with", "from", "which", "使用", "相关", "一个", "可以"}
    terms = [word for word, _ in Counter(words).most_common(12) if word not in stop]
    focus = " ".join(terms[:4])
    suffix = (" official primary source" if round_index == 1 else
              " limitations conflicting evidence" if round_index == 2 else
              " latest verification")
    return " ".join(part for part in (base, focus, suffix) if part).strip()


@tool("deep_search", args_schema=DeepSearchSchema)
def deep_search(query: str, rounds: int = 3) -> str:
    """执行多轮搜索、去重并检查来源多样性。"""
    normalized = " ".join((query or "").split())
    if not normalized:
        return json.dumps({"ok": False, "error": "query 不能为空"}, ensure_ascii=False)

    # deep_search 调用预算：超过 MAX_DEEP_SEARCH_PER_TASK 次后拒绝执行，
    # 防止模型用 deep_search 绕开 web_search 的 8 次预算形成新循环。
    thread_id = get_runtime_thread_id()
    used = _consume_deep_search_budget(thread_id)
    if used > MAX_DEEP_SEARCH_PER_TASK:
        return json.dumps(
            {
                "ok": False,
                "error": (
                    f"deep_search 调用预算已用尽（本任务最多调用 {MAX_DEEP_SEARCH_PER_TASK} 次，"
                    f"当前为第 {used} 次请求）。禁止继续调用。请基于已获取的信息给出最可能的答案，"
                    "并说明判断依据与不确定点；也可以直接调用 submit_final_answer 工具提交本次任务的"
                    "最终结论（把结论填进 answer 参数），调用后任务立即结束。"
                ),
            },
            ensure_ascii=False,
        )

    records: list[dict[str, str]] = []
    seen: set[str] = set()
    failures: list[str] = []
    queries: list[str] = []
    previous: list[dict[str, str]] = []
    for round_index in range(rounds):
        search_query = normalized if round_index == 0 else _next_query(normalized, previous, round_index)
        queries.append(search_query)
        try:
            # 走内部实现，不计入单题 8 次搜索预算，避免 deep_search 被预算误伤。
            result = _web_search_impl(search_query, thread_id=None)
            text = str(result or "")
            sources = _extract_sources(text)
            for source in sources:
                clean = source["url"]
                if clean in seen:
                    continue
                if clean:
                    seen.add(clean)
                records.append({"url": clean, "query": search_query, "snippet": source["snippet"]})
            previous = records[-8:]
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{search_query}: {exc}")

    hosts = defaultdict(int)
    for item in records:
        if item["url"]:
            hosts[urlparse(item["url"]).netloc.lower()] += 1
    independent_hosts = sorted(hosts)
    # 交叉验证要求：同一组关键短语至少被两个独立域名的摘要支持。
    claim_hosts: dict[str, set[str]] = defaultdict(set)
    for item in records:
        host = urlparse(item["url"]).netloc.lower() if item["url"] else "unknown"
        sentences = re.split(r"[。.!?\n]", item["snippet"])
        for sentence in sentences:
            tokens = re.findall(r"[a-z0-9\u4e00-\u9fff]{3,}", sentence.lower())
            if len(tokens) >= 4:
                claim_hosts[" ".join(tokens[:6])].add(host)
    corroborated = [claim for claim, source_set in claim_hosts.items() if len(source_set - {"unknown"}) >= 2]
    verified = bool(corroborated)
    return json.dumps(
        {
            "ok": bool(records),
            "query": normalized,
            "rounds": len(queries),
            "queries": queries,
            "sources": records[:8],
            "source_hosts": independent_hosts,
            "corroborated_claims": corroborated[:10],
            "cross_source_verified": verified,
            "verification_note": "同一主张获得至少两个独立来源支持" if verified else "没有发现被多个独立来源支持的共同主张",
            "failures": failures,
        },
        ensure_ascii=False,
    )
