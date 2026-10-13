"""联网搜索工具。

该工具用于在本地仓库上下文不足时，补充公开网络资料，例如框架文档、第三方库变更说明、
错误信息背景和公开技术资料。工具本身只返回搜索摘要，不替代本地代码分析。

外部搜索优先依赖智谱 Web Search API。SDK 采用懒加载方式初始化，避免后端启动阶段因为可选依赖
或 API Key 缺失而直接失败。**智谱不可用（缺 Key、SDK 未装、接口报错）时自动降级到
Bing 搜索的 RSS 接口**，保证搜索能力不因单一供应商失效而整体不可用。
"""

from __future__ import annotations

import json
import logging
import re
import threading
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from functools import lru_cache
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from agent.runtime.events import record_event
from config.env_config import get_env, require_env
from utils.context_utils import get_runtime_thread_id
from utils.gitee_utils import mask_token

logger = logging.getLogger("agent.run.web_search")

_BING_RSS_URL = "https://www.bing.com/search"

# 智谱 search_std 单条结果可能极长，逐条截断，防止撑爆模型上下文。
# 800 字符/条（count=3，单次搜索约 2.4K 字符）：保留关键信息同时控制上下文体积
# （此前 1500 字符/条，长任务多轮搜索累积会显著放大每轮模型请求）。
MAX_RESULT_CHARS = 800

# 每线程（单任务）搜索预算：超过后不再执行真实搜索，强制基于已有信息作答，
# 防止 agent 陷入"搜索-思考"死循环（长任务实测曾单题空转 56 分钟搜索 70+ 次）。
MAX_SEARCH_PER_TASK = 8

_SEARCH_COUNT: dict[str, int] = {}
_SEARCH_COUNT_LOCK = threading.Lock()


def _consume_search_budget(thread_id: str) -> int:
    """为指定线程的搜索计数自增，返回当前累计次数（1 起）。

    Args:
        thread_id: 当前线程 ID；为空时使用固定占位键，仍限制总量。

    Returns:
        累计搜索次数。
    """
    key = thread_id or "__no_thread__"
    with _SEARCH_COUNT_LOCK:
        _SEARCH_COUNT[key] = _SEARCH_COUNT.get(key, 0) + 1
        return _SEARCH_COUNT[key]


# 修复轮补充的搜索额度：最终答案校验失败进入修复轮时，
# 允许 agent 再做最多 REPAIR_SEARCH_REFILL 次针对性搜索补齐缺口。
REPAIR_SEARCH_REFILL = 3


def refill_search_budget(thread_id: str, amount: int = REPAIR_SEARCH_REFILL) -> None:
    """为指定线程补充搜索额度（供最终答案修复轮使用）。

    计数值回退 amount（即允许该线程再执行 amount 次真实搜索）。
    补充额度不会让计数为负，也不影响其他线程的预算。

    Args:
        thread_id: 当前线程 ID；为空时使用固定占位键。
        amount: 补充的搜索次数，默认 REPAIR_SEARCH_REFILL。
    """
    key = thread_id or "__no_thread__"
    with _SEARCH_COUNT_LOCK:
        _SEARCH_COUNT[key] = max(0, _SEARCH_COUNT.get(key, 0) - amount)


def _fallback_search(query: str) -> str:
    """智谱搜索不可用时，用 Bing 搜索的 RSS 接口兜底。

    Bing ``format=rss`` 返回标准 RSS 2.0 XML，只依赖标准库
    （urllib + xml.etree），不引入新依赖；结果格式与智谱一致的摘要文本。
    网络或解析失败时抛异常，由调用方统一降级。

    Args:
        query: 搜索关键词。

    Returns:
        搜索结果摘要文本，格式为「标题\nURL\n摘要」条目序列。

    Raises:
        OSError: 网络请求失败。
    """

    url = f"{_BING_RSS_URL}?q={urllib.parse.quote(query)}&format=rss&count=5"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        raw = response.read().decode("utf-8", "ignore")

    root = ET.fromstring(raw)
    items = list(root.iter("item"))[:5]
    if not items:
        raise RuntimeError("Bing RSS 没有返回搜索结果")

    entries = []
    for item in items:
        title = "".join(item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        snippet = " ".join((item.findtext("description") or "").split()).strip()
        entries.append(f"{title}\n{link}\n{snippet}")
    return "\n\n".join(entries) if entries else "没有搜索到任何内容。"


@lru_cache(maxsize=1)
def _get_zhipu_client() -> Any:
    """懒加载智谱 SDK 客户端。

    Web 搜索不是后端启动的必要能力，所以不能在模块导入时直接初始化 SDK。
    这样即使本地暂时没有安装 `zai`，FastAPI 也能正常启动；只有 Agent 真正调用
    web_search 时，才返回明确的依赖或密钥错误。

    Returns:
        智谱 SDK 客户端实例。

    Raises:
        RuntimeError: SDK 未安装时抛出，包含安装指引。
    """

    # API Key 在真正搜索时才读取，避免导入模块阶段对环境变量产生强依赖。
    api_key = require_env("ZHIPU_API_KEY")
    try:
        # 优先兼容新版 `zai` SDK。
        from zai import ZhipuAiClient

        return ZhipuAiClient(api_key=api_key)
    except (ImportError, AttributeError):
        pass

    try:
        # 回退兼容旧版 `zhipuai` SDK，降低部署环境升级成本。
        from zhipuai import ZhipuAI
    except ImportError as exc:
        raise RuntimeError("缺少智谱 SDK，请先安装依赖：pip install zhipuai") from exc
    return ZhipuAI(api_key=api_key)


class WebSearchSchema(BaseModel):
    """联网搜索工具的输入参数定义。

    Attributes:
        query: 需要搜索的关键词或问题。
    """

    query: str = Field(description="需要搜索的关键词或问题。")


def _try_academic(query: str) -> str | None:
    """尝试用 OpenAlex 学术库检索文献/论文类查询。

    对论文标题+年份查询召回质量远高于通用搜索引擎（典型：GAIA 等文献题里
    搜“论文标题”时通用引擎返回无关内容）。带相关性过滤（score 双规则），
    普通查询（天气/文档等）不会污染搜索结果。无命中返回 None 由调用方走普通搜索。

    Args:
        query: 规范化后的搜索词。

    Returns:
        格式化后的学术命中文本；无命中或网络失败时返回 None。
    """
    # OpenAlex 学术检索
    try:
        # 净化 query：去掉问号/引号/逗号等标点，OpenAlex 对含标点或部分中文
        # 查询会返回 400；文献标题本身仍能精确命中。
        clean = re.sub(r'[?,.!""\'\u201c\u201d\u2018\u2019]', ' ', query).strip()
        if not clean:
            return None
        url = "https://api.openalex.org/works?" + urllib.parse.urlencode(
            {"search": clean, "per-page": 5}
        )
        req = urllib.request.Request(url, headers={"User-Agent": "xihe-agent/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        results = data.get("results") or []
        results = [it for it in results if (it.get("relevance_score") or 0) >= 30]
        if not results:
            return None
        # 相关性判别：精确文献标题命中 score 通常 >300；普通查询按词片段匹配
        # score 多为 30-70 且 top 与次位接近。双规则只放行"真正命中"：
        #   1) top1 绝对高分（>=100）；或
        #   2) top1 >= 60 且明显突出于次位（>=1.5x）。
        top = results[0].get("relevance_score") or 0
        second = (results[1].get("relevance_score") or 0) if len(results) > 1 else 0
        if top < 60:
            return None
        if len(results) == 1 and top < 100:
            return None
        if len(results) > 1 and second > 0 and top / second < 1.5:
            return None
        lines = []
        for it in results[:5]:
            title = str(it.get("display_name") or "").strip()
            if not title:
                continue
            authors = ", ".join(
                str((a.get("author") or {}).get("display_name") or "")
                for a in (it.get("authorships") or [])[:8]
            )
            year = it.get("publication_year") or ""
            venue = ((it.get("primary_location") or {}).get("source") or {}).get("display_name") or ""
            lines.append(f"[学术检索] {title} | 作者: {authors} | 年份: {year} | 出处: {venue}")
        if lines:
            return "\n\n".join(lines)
    except Exception:
        pass
    return None


def _web_search_impl(query: str, thread_id: str | None) -> str:
    """执行真实联网搜索（不计入搜索预算）。

    供 web_search 工具（受预算限制）与 deep_search 内部（不受预算限制）共用。

    Args:
        query: 需要搜索的关键词或问题。
        thread_id: 当前线程 ID；为 None 时不写入前端事件。

    Returns:
        搜索结果摘要文本；失败时返回明确错误信息。
    """
    # 压缩空白，避免模型生成多行 query 或带大量空格影响搜索质量。
    normalized_query = " ".join((query or "").split())
    if not normalized_query:
        return "搜索失败：query 不能为空"

    if thread_id:
        # 写入工具调用事件，前端可以展示"正在联网搜索"的运行过程。
        record_event(
            thread_id,
            f"web_search:{normalized_query[:80]}",
            "联网搜索资料",
            kind="fetch",
            status="in_progress",
            detail=json.dumps({"query": normalized_query}, ensure_ascii=False),
        )
    # 文献/论文类查询优先走学术数据库（OpenAlex/Crossref，召回质量远高于
    # 通用搜索引擎）；无命中时无缝回落到普通搜索，不影响其他类型的查询。
    academic_hit = _try_academic(normalized_query)
    if academic_hit:
        if thread_id:
            record_event(
                thread_id,
                f"web_search:{normalized_query[:80]}",
                "联网搜索资料",
                kind="fetch",
                status="completed",
                detail=json.dumps(
                    {"query": normalized_query, "source": "academic", "result_preview": academic_hit[:1200]},
                    ensure_ascii=False,
                ),
            )
        return academic_hit
    try:
        client = _get_zhipu_client()
        # 使用 search_std 并限制 count=3，控制外部结果数量和模型上下文体积。
        response = client.web_search.web_search(
            search_engine="search_std",
            search_query=normalized_query,
            count=3,
            search_recency_filter="noLimit",
        )
        results = getattr(response, "search_result", None) or []
        if not results:
            output = "没有搜索到任何内容。"
        else:
            # 智谱 search_std 的单条 content 可能非常长，
            # 直接塞给模型会撑爆上下文。逐条截断到 MAX_RESULT_CHARS，
            # 只保留摘要级别信息。
            snippets = []
            for item in results:
                text = str(getattr(item, "content", "") or "").strip()
                if not text:
                    continue
                if len(text) > MAX_RESULT_CHARS:
                    text = text[:MAX_RESULT_CHARS] + "…"
                snippets.append(text)
            output = "\n\n".join(snippets)
            if not output:
                output = "搜索结果为空。"
        if thread_id:
            # 事件详情只记录预览片段，避免把大量搜索结果重复写入事件表。
            record_event(
                thread_id,
                f"web_search:{normalized_query[:80]}",
                "联网搜索资料",
                kind="fetch",
                status="completed",
                detail=json.dumps(
                    {"query": normalized_query, "result_preview": output[:1200]},
                    ensure_ascii=False,
                ),
            )
        return output
    except Exception as exc:
        # 外部 API 错误、网络异常、SDK 异常都先尝试 DDG 兜底；
        # 兜底也失败时返回可读文本，让模型可以继续降级处理。
        primary_error = mask_token(str(exc))
        logger.warning(
            "联网搜索主通道失败，尝试 DuckDuckGo 兜底：query=%s error=%s",
            normalized_query,
            primary_error,
        )
        try:
            output = _fallback_search(normalized_query)
        except Exception as fallback_exc:
            error = f"{primary_error}；DDG 兜底失败：{mask_token(str(fallback_exc))}"
            logger.warning("联网搜索失败：query=%s error=%s", normalized_query, error)
            if thread_id:
                record_event(
                    thread_id,
                    f"web_search:{normalized_query[:80]}",
                    "联网搜索资料",
                    kind="fetch",
                    status="error",
                    detail=json.dumps({"query": normalized_query, "error": error}, ensure_ascii=False),
                )
            return f"搜索失败: {error}"
        if thread_id:
            record_event(
                thread_id,
                f"web_search:{normalized_query[:80]}",
                "联网搜索资料",
                kind="fetch",
                status="completed",
                detail=json.dumps(
                    {"query": normalized_query, "source": "duckduckgo", "result_preview": output[:1200]},
                    ensure_ascii=False,
                ),
            )
        return output


@tool("web_search", args_schema=WebSearchSchema)
def web_search(query: str) -> str:
    """使用智谱搜狗 Web Search API 进行联网搜索（文献/论文/作者类查询自动优先走 OpenAlex 学术库）。

    适用于需要外部资料支撑的任务，例如查询最新框架文档、第三方库用法、
    API 变更说明、行业资料或错误信息背景。不要搜索密钥、token、私有仓库内容。
    论文标题、作者、会议/期刊等学术类查询：工具会自动先查 OpenAlex 学术数据库（返回
    “作者/年份/出处”结构化信息），无命中才走通用搜索引擎，无需手动改查询词。

    Args:
        query: 需要搜索的关键词或问题。

    Returns:
        搜索结果摘要文本；失败时返回明确错误信息。
    """

    thread_id = get_runtime_thread_id()
    # 搜索预算硬性拦截：超过 MAX_SEARCH_PER_TASK 次后拒绝真实搜索，
    # 并明确要求基于已有信息作答，阻断无信息增量的搜索循环。
    # 注意：deep_search 内部走 _web_search_impl，不计入本预算。
    used = _consume_search_budget(thread_id)
    if used > MAX_SEARCH_PER_TASK:
        return (
            f"搜索预算已用尽（本任务最多搜索 {MAX_SEARCH_PER_TASK} 次，当前为第 {used} 次请求）。"
            "禁止继续搜索。请基于已获取的信息给出最可能的答案，并说明判断依据与不确定点；"
            "也可以直接调用 submit_final_answer 工具提交本次任务的最终结论（把结论填进 answer 参数），"
            "调用后任务立即结束；或直接输出最终答复，不再继续搜索。"
        )
    return _web_search_impl(query, thread_id)
