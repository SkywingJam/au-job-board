"""跨平台去重。

同一份工作会同时出现在 SEEK / LinkedIn / Indeed 上（Indeed 本身也是聚合器，
大量职位是从别处转载的）。不去重的话高分列表会被同一份工作的多个副本淹没。

策略（两段）：
  1. 规范化键：公司名 + 标题 + 城市，做精确分组
  2. 在有界块内做模糊合并：同城 + 同公司 + 标题相似度 >= 阈值

模糊只在这个场景是对的 —— 这里要处理的是「写法差异」（Pty Ltd / Pty. Ltd.）。
eligibility 判断则绝不能用模糊（见 filters.py）。
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

_PUNCT = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")

# 澳洲公司名常见后缀，不参与匹配
_COMPANY_NOISE = re.compile(
    r"\b(pty\.?\s*ltd\.?|pty\.?\s*limited|ltd\.?|limited|inc\.?|llc|group|holdings|"
    r"australia|aust|au|the)\b", re.IGNORECASE)

TITLE_SIMILARITY_THRESHOLD = 0.90


def normalize_company(name: str | None) -> str:
    text = (name or "").lower()
    text = _COMPANY_NOISE.sub(" ", text)
    text = _PUNCT.sub(" ", text)
    return _WS.sub(" ", text).strip()


def normalize_title(title: str | None) -> str:
    text = (title or "").lower()
    text = _PUNCT.sub(" ", text)
    return _WS.sub(" ", text).strip()


def city_of(location: str | None) -> str:
    """取地点第一段做城市。'Melbourne, Victoria, Australia' -> 'melbourne'"""
    first = (location or "").split(",")[0]
    return _WS.sub(" ", _PUNCT.sub(" ", first.lower())).strip()


def dedupe_key(job) -> str:
    return f"{normalize_company(job['company'])}|{normalize_title(job['title'])}|{city_of(job['location'])}"


def assign_groups(jobs: list, include_city: bool = False,
                  title_similarity: float = TITLE_SIMILARITY_THRESHOLD) -> dict[str, str]:
    """给每条职位分配一个去重组 ID。返回 {uid: group_id}。

    jobs 每项需有 uid / company / title / location。
    group_id 取组内代表 uid（调用方通常会再按分数选代表）。

    include_city=False（默认）时，同一公司 + 同一标题的不同城市会被合并 ——
    全国性毕业生项目在 6 个城市各发一条，对求职者来说只投一次。
    """
    groups: dict[str, str] = {}

    # 块 = 公司（可选含城市）。只在块内比标题相似度，避免 O(n^2) 全量比较
    fuzzy_blocks: dict[tuple, list] = {}
    for job in jobs:
        key = (normalize_company(job["company"]),)
        if include_city:
            key = key + (city_of(job["location"]),)
        fuzzy_blocks.setdefault(key, []).append(job)

    parent: dict[str, str] = {j["uid"]: j["uid"] for j in jobs}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for members in fuzzy_blocks.values():
        if len(members) < 2:
            continue
        for i, left in enumerate(members):
            for right in members[i + 1:]:
                lt, rt = normalize_title(left["title"]), normalize_title(right["title"])
                if not lt or not rt:
                    continue
                if SequenceMatcher(None, lt, rt).ratio() >= title_similarity:
                    union(left["uid"], right["uid"])

    for job in jobs:
        groups[job["uid"]] = find(job["uid"])

    return groups
