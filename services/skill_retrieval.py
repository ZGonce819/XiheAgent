"""技能检索：按当前任务从候选技能里挑出要写进提示词的那些。

技能注入的瓶颈不是「技能很多」，而是「技能目录会被拼进每一次模型调用的
system prompt」。本模块负责从全部技能里挑出最相关的若干条，让注入成本不再随
技能总数线性上涨。

## 打分用的文本和写进提示词的文本

这是本模块最重要的一条边界：参与打分的文本，和最终写进提示词的文本，
**不是同一份**：

**写进提示词的技能列表**（由 DeepAgents ``_format_skills_list`` 渲染进 system prompt）::

    - **{name}**: {description}
      -> Read `{path}` for full instructions

**打分用的文本**（见 :func:`retrieval_document`）
    ``name`` + ``description`` + ``path`` + ``metadata`` 的全部键值。

两者刻意**不一样**：打分用的文本更宽。

- ``metadata`` 里常放 ``keywords`` / ``when_to_use``，``path`` 带作用域信息
  （如 ``.project-set/Xihe``），把它们也算进打分能提高命中率；
- 但写进提示词的永远只有那两行摘要，**技能的 ``SKILL.md`` 正文自始至终不参与
  打分，也不会被写进提示词**。正文由模型在命中之后用 ``read_file`` 按需读取
  （DeepAgents 的 progressive disclosure），这是控制上下文成本的关键。

因此技能作者应当把「做什么 + 什么时候用 + 关键词」写进 ``description``
（技能规范要求 description 同时描述用途与触发条件，上限 1024 字符），
更细的检索提示可以放进 ``metadata``。

## 设计取舍

**检索层刻意做成依赖无关的词法检索**（BM25-lite），不引入向量库与嵌入模型：

- 技能数量在可预见的规模内（几十到几百条），词法检索的召回足够；
- 技能名与 ``description`` 本身就是为「被匹配」而写的，关键词密度高；
- 不需要额外的基础设施与网络调用，也不会因为嵌入服务不可用而让智能体不可用。

将来若要换成向量检索，只需替换 :func:`select_skills` 的实现，调用方
（``agent/middleware/skill_retrieval.py``）不需要改动；届时嵌入的输入同样应当是
:func:`retrieval_document` 的输出，而不是 ``SKILL.md`` 全文。

## 两条安全性质

1. **fail-open**：查询为空、没有任何技能得分大于 0、或 ``top_k`` 非法时，
   一律返回**全部技能**。宁可多列几个，也不能因为检索失效而让模型看不到
   本来能用的技能。
2. **保持原始顺序**：返回值沿用技能列表的原有次序，而不是按相关度重排。
   这样同一份技能目录在多次调用间渲染出的文本是稳定的，不会平白打断
   prompt 缓存的前缀。
"""

from __future__ import annotations

import math
import re
from collections.abc import Collection, Mapping, Sequence
from typing import Any

TOKEN_PATTERN = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]+")
"""分词用的字符类：拉丁字母数字串，或连续的 CJK 字符。"""

ALWAYS_ON_SKILLS: tuple[str, ...] = (
    "repo-bootstrap-analysis",
    "ai-coding-implementation",
    "code-review",
)
"""必须始终出现在提示词里的技能。

``prompt.py`` 在系统提示词里按名字直接引用了这三个内置技能（例如「优先使用
``repo-bootstrap-analysis`` skill 的工作方法」）。如果检索把它们挤掉，提示词
就会指向一个模型看不到的技能，因此这三个必须常驻，且不受 ``top_k`` 约束。
"""

_K1 = 1.2
"""BM25 的词频饱和系数。"""

_B = 0.75
"""BM25 的文档长度归一化强度。"""

_NAME_WEIGHT = 3.0
"""技能名命中的加权倍数。

技能名是作者精心起的标识（``db-migration-check``），命中它比命中描述里的
同一个词更能说明相关性。
"""


def tokenize(text: str) -> list[str]:
    """把一段文本切成可比较的词元。

    拉丁字母与数字按连续串切词；中文没有词边界，退化为**二元组**近似词元
    （``退款逻辑`` → ``退款`` / ``款逻`` / ``辑``… 中的相邻对），这与
    PostgreSQL ``pg_trgm`` 的思路一致，对中文语料有效，且不需要分词器。

    Args:
        text: 待切分的文本。

    Returns:
        词元列表，按出现顺序排列，可能含重复项。
    """
    tokens: list[str] = []
    for match in TOKEN_PATTERN.finditer((text or "").lower()):
        chunk = match.group(0)
        if chunk[0].isascii():
            if len(chunk) > 1:
                tokens.append(chunk)
            continue
        if len(chunk) == 1:
            tokens.append(chunk)
            continue
        tokens.extend(chunk[index : index + 2] for index in range(len(chunk) - 1))
    return tokens


def retrieval_document(skill: Mapping[str, Any]) -> str:
    """拼出一个技能的**打分用的文本**（参与打分，但不写进提示词）。

    刻意比写进提示词的那份更宽：

    - ``name`` / ``description`` —— 提示词里也会出现，是主要的匹配依据；
    - ``path`` —— 带作用域信息。形如 ``/workspace/skills/.project-set/Xihe/db-check/SKILL.md``
      的路径会让「项目名 + 项目集合」成为可匹配信号，查询里提到项目名时能自然
      抬高该项目专属技能；路径里的 ``skills`` / ``SKILL.md`` 等词在每条技能里
      都出现，IDF 会自动把它们压到接近 0，不会造成干扰；
    - ``metadata`` —— 技能规范允许的任意键值映射，常用来放 ``keywords`` /
      ``when_to_use`` 这类额外检索提示，因此键名与值都算进去。

    **技能正文不在这里**：``SKILL.md`` 的 body 不参与打分，也不会写进提示词，
    由模型命中后用 ``read_file`` 按需读取。

    Args:
        skill: 一条技能记录（``deepagents`` 的 ``SkillMetadata`` 形状）。

    Returns:
        供 :func:`tokenize` 切分的文本。
    """
    parts = [
        str(skill.get("name") or ""),
        str(skill.get("description") or ""),
        str(skill.get("path") or ""),
    ]
    metadata = skill.get("metadata")
    if isinstance(metadata, dict):
        for key, value in metadata.items():
            parts.append(str(key))
            parts.append(str(value))
    return "\n".join(part for part in parts if part)


def _idf(token: str, document_frequency: Mapping[str, int], total: int) -> float:
    """计算词元的逆文档频率。

    Args:
        token: 词元。
        document_frequency: 词元到出现该词元的文档数的映射。
        total: 文档总数。

    Returns:
        BM25 常用的平滑 IDF；词元从未出现时返回一个较大的正数。
    """
    frequency = document_frequency.get(token, 0)
    return math.log(1 + (total - frequency + 0.5) / (frequency + 0.5))


def _bm25(
    query_tokens: Collection[str],
    counts: Mapping[str, int],
    idf: Mapping[str, float],
    length: int,
    average_length: float,
) -> float:
    """按 BM25 计算一份文档对查询的得分。

    Args:
        query_tokens: 查询词元集合。
        counts: 该文档的词元频次。
        idf: 预先算好的词元 IDF。
        length: 该文档的词元总数。
        average_length: 全部文档的平均词元数。

    Returns:
        得分；没有任何查询词元命中时为 ``0.0``。
    """
    score = 0.0
    for token in query_tokens:
        frequency = counts.get(token, 0)
        if not frequency:
            continue
        ratio = length / average_length if average_length else 1.0
        denominator = frequency + _K1 * (1 - _B + _B * ratio)
        score += idf[token] * (frequency * (_K1 + 1)) / denominator
    return score


def select_skills(
    skills: Sequence[Mapping[str, Any]],
    query: str,
    *,
    top_k: int,
    always_on: Collection[str] = ALWAYS_ON_SKILLS,
) -> list[Mapping[str, Any]]:
    """从全部技能里挑出要写进提示词的子集。

    Args:
        skills: 待筛选的技能列表，元素需含 ``name`` 与 ``description`` 键
            （``deepagents`` 的 ``SkillMetadata`` 满足该形状）。``path`` 与
            ``metadata`` 存在时会一并纳入打分，见 :func:`retrieval_document`。
        query: 当前任务的查询文本，通常是最后一条用户消息。
        top_k: 列出**总数**的上限。:data:`ALWAYS_ON_SKILLS` 里的技能优先占用
            名额，剩余名额再按相关度分给其他技能。若这些技能本身就多于
            ``top_k``，则只列出它们（优先级高于额度）。
        always_on: 必须保留的技能名，默认 :data:`ALWAYS_ON_SKILLS`。

    Returns:
        选中的技能列表，**保持 ``skills`` 的原始顺序**。以下任一情况直接返回
        全部技能（fail-open）：列表为空、``top_k`` 非正、查询切不出词元、
        或所有技能得分都为 0。
    """
    items = list(skills)
    if not items or top_k <= 0:
        return items

    query_tokens = set(tokenize(query))
    if not query_tokens:
        return items

    name_tokens = [tokenize(str(item.get("name") or "")) for item in items]
    # 参与打分的文本 = retrieval_document（name + description + path + metadata），
    # 比写进提示词的那份更宽，见模块文档「打分用的文本和写进提示词的文本」。
    document_tokens = [tokenize(retrieval_document(item)) for item in items]

    total = len(items)
    document_frequency: dict[str, int] = {}
    for tokens in document_tokens:
        for token in set(tokens):
            document_frequency[token] = document_frequency.get(token, 0) + 1

    lengths = [len(tokens) for tokens in document_tokens]
    average_length = sum(lengths) / total if total else 0.0
    # IDF 只跟查询词元与全部技能有关，与具体某个技能无关，循环外算一次即可。
    idf = {token: _idf(token, document_frequency, total) for token in query_tokens}

    scores: list[float] = []
    for index in range(total):
        counts: dict[str, int] = {}
        for token in document_tokens[index]:
            counts[token] = counts.get(token, 0) + 1
        score = _bm25(query_tokens, counts, idf, lengths[index], average_length)
        # 技能名命中额外加权：名字是作者精心起的标识，比描述里的同一个词更可信。
        name_hits = query_tokens.intersection(name_tokens[index])
        score += sum(idf[token] for token in name_hits) * (_NAME_WEIGHT - 1)
        scores.append(score)

    if not any(score > 0 for score in scores):
        return items

    always = {str(name).strip().lower() for name in always_on}
    keep = {
        index
        for index in range(total)
        if str(items[index].get("name") or "").strip().lower() in always
    }
    ranked = sorted(range(total), key=lambda index: (-scores[index], index))
    for index in ranked:
        if len(keep) >= top_k:
            break
        keep.add(index)

    return [item for index, item in enumerate(items) if index in keep]
