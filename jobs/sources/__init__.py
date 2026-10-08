"""来源适配器。每个适配器只负责「把原始数据取回来并归一化」，不做任何过滤或打分。"""

from __future__ import annotations

from functools import partial
from typing import Callable, Iterable

# 归一化后的职位记录字段（各适配器统一输出这个形状）
NORMALIZED_FIELDS = (
    "source", "source_id", "title", "company", "location", "url",
    "salary", "work_type", "classification", "job_level", "listing_date",
    "teaser", "description", "raw",
)


def blank_job(source: str, source_id: str) -> dict:
    job = {f: None for f in NORMALIZED_FIELDS}
    job["source"] = source
    job["source_id"] = str(source_id)
    job["raw"] = {}
    return job


def iter_sources(config: dict) -> Iterable[tuple[str, Callable]]:
    """产出 (名字, 抓取函数)。抓取函数签名 fn(config, queries) -> list[dict]。"""
    from .jobspy_source import fetch as jobspy_fetch
    from .seek import fetch as seek_fetch

    if (config.get("seek") or {}).get("enabled", True):
        yield "seek", seek_fetch

    js = config.get("jobspy") or {}
    for site in ("indeed", "linkedin"):
        if (js.get(site) or {}).get("enabled", True):
            yield site, partial(jobspy_fetch, site=site)
