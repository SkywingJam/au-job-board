"""SEEK 适配器。

列表数据走内部 JSON 搜索接口，完整正文走 GraphQL jobDetails 查询。这两个接口
都是 unofficial / best-effort：平台随时可能改动或关闭，其中硬编码的 GraphQL
字段集是最容易失效的一环。

搜索响应**不返回正文**，所以列表级抓取与详情级抓取是分开的两步。详情补抓走
db 的「详情队列」，已抓到正文的职位不会重复请求。
"""

from __future__ import annotations

import json
import re
import time
from html import unescape
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import blank_job

SOURCE = "seek"
SEARCH_API = "https://www.seek.com.au/api/jobsearch/v5/search"
GRAPHQL = "https://www.seek.com.au/graphql"
JOB_URL = "https://www.seek.com.au/job/"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/604.1")

# 硬编码的字段集；SEEK 改 schema 时这里会先断。
DETAIL_QUERY = (
    "query jobDetails($jobId: ID!) { jobDetails(id: $jobId) { job { "
    "id title abstract content(platform: WEB) status isExpired "
    "salary { label } workTypes { label } "
    "advertiser { id name isVerified } location { label } "
    'classifications { label(languageCode: "en") } '
    "} } }"
)


def _get_json(url: str, timeout: int = 30) -> dict:
    req = Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def html_to_text(html: str) -> str:
    """SEEK 正文是 HTML，转成可读纯文本（只用标准库）。"""
    if not html:
        return ""
    text = re.sub(r"(?i)<\s*(br|/p|/li|/div|/h[1-6])\s*>", "\n", html)
    text = re.sub(r"(?i)<\s*li[^>]*>", "\n- ", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()


def _flatten_classifications(raw) -> str:
    """搜索结果的 classifications 是嵌套结构，取 subclassification 描述。"""
    out = []
    for item in raw or []:
        if isinstance(item, dict):
            sub = item.get("subclassification") or {}
            main = item.get("classification") or {}
            label = (sub.get("description") if isinstance(sub, dict) else None) \
                or (main.get("description") if isinstance(main, dict) else None)
            if label:
                out.append(label)
        elif isinstance(item, str):
            out.append(item)
    return "; ".join(dict.fromkeys(out))


def _first_label(raw) -> str:
    """workTypes / workArrangements 在不同版本里可能是 str 或 dict。"""
    if not raw:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        return raw.get("label") or raw.get("displayText") or ""
    if isinstance(raw, list):
        for item in raw:
            label = _first_label(item)
            if label:
                return label
    return ""


def _normalize(rec: dict, query: str) -> dict:
    job = blank_job(SOURCE, rec.get("id", ""))
    locations = rec.get("locations") or [{}]
    loc = locations[0] if isinstance(locations[0], dict) else {}
    job.update({
        "title": rec.get("title"),
        "company": rec.get("companyName")
                   or (rec.get("advertiser") or {}).get("description"),
        "location": loc.get("label"),
        "url": JOB_URL + str(rec.get("id", "")),
        "salary": rec.get("salaryLabel"),
        "work_type": _first_label(rec.get("workTypes")),
        "classification": _flatten_classifications(rec.get("classifications")),
        "job_level": None,   # SEEK 无此字段
        "listing_date": rec.get("listingDate"),
        "teaser": (rec.get("teaser") or "").strip() or None,
        "raw": {"query": query, "workArrangements": rec.get("workArrangements")},
    })
    return job


def search_page(keywords: str, where: str, page: int, daterange: int | None = None) -> dict:
    params = {
        "siteKey": "AU-Main",
        "sourcesystem": "houston",
        "where": where,
        "page": page,
        "keywords": keywords,
        "locale": "en-AU",
        "include": "seodata",
    }
    if daterange:
        params["daterange"] = daterange
    return _get_json(f"{SEARCH_API}?{urlencode(params)}")


def fetch(config: dict, queries: list[str]) -> list[dict]:
    """列表级抓取。返回不含正文的职位记录。"""
    cfg = config.get("seek") or {}
    pages = int(cfg.get("pages_per_query", 2))
    daterange = cfg.get("daterange")
    locations = cfg.get("locations") or ["All Australia"]

    out: dict[str, dict] = {}
    for query in queries:
        for where in locations:
            for page in range(1, pages + 1):
                try:
                    data = search_page(query, where, page, daterange)
                except HTTPError as exc:
                    print(f"  [seek] {query!r} p{page} HTTP {exc.code}")
                    break
                except (URLError, TimeoutError) as exc:
                    print(f"  [seek] {query!r} p{page} 网络错误: {exc}")
                    break
                records = data.get("data") or []
                if not records:
                    break
                for rec in records:
                    if not rec.get("id"):
                        continue
                    job = _normalize(rec, query)
                    out.setdefault(job["source_id"], job)
                total = data.get("totalCount") or 0
                if page * 20 >= total:
                    break
                time.sleep(0.35)
    return list(out.values())


def fetch_detail(source_id: str) -> str | None:
    """详情级抓取：取回完整正文。失败返回 None（留在队列里下次再试）。"""
    payload = json.dumps({
        "operationName": "jobDetails",
        "variables": {"jobId": str(source_id)},
        "query": DETAIL_QUERY,
    }).encode("utf-8")
    req = Request(GRAPHQL, data=payload, headers={
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": UA,
    })
    try:
        with urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError):
        return None
    # 限流可能以 HTTP 200 + body errors 的形式返回
    if body.get("errors"):
        return None
    job = ((body.get("data") or {}).get("jobDetails") or {}).get("job")
    if not job:
        return None
    content = html_to_text(job.get("content", ""))
    abstract = job.get("abstract") or ""
    return f"{content}\n\n{abstract}".strip() or None
