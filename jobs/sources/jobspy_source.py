"""Indeed / LinkedIn 适配器（基于 python-jobspy）。

- Indeed 通过 python-jobspy 抓取，它内部会适配平台的请求方式。**不要绕过
  jobspy 自己写 requests。**
- LinkedIn 是可选来源；开启 fetch_description 时逐条请求正文，速度较慢。
  硬过滤需要正文，所以示例配置对该来源默认开启。
- 两个来源都是 unofficial / best-effort：python-jobspy 或平台变化时都可能失效。
"""

from __future__ import annotations

import warnings

warnings.filterwarnings("ignore")

from .. import RunTimeout
from . import blank_job

SOURCE_INDEED = "indeed"
SOURCE_LINKEDIN = "linkedin"

# jobspy DataFrame -> 归一化字段
_COLUMN_MAP = {
    "job_url": "url",
    "date_posted": "listing_date",
    "job_type": "work_type",
    "job_level": "job_level",
    "description": "description",
    "company_industry": "classification",
}


def _clean(value):
    """把 pandas 的 NaN / NaT / 'nan' 统一成 None。"""
    if value is None:
        return None
    try:
        if value != value:      # NaN
            return None
    except Exception:
        pass
    text = str(value).strip()
    if text.lower() in ("", "nan", "nat", "none"):
        return None
    return text


def fetch(config: dict, queries: list[str], site: str = SOURCE_INDEED) -> list[dict]:
    """抓 Indeed 或 LinkedIn。site 由调用方通过 functools.partial 绑定。"""
    from jobspy import scrape_jobs

    cfg = (config.get("jobspy") or {}).get(site) or {}
    location = cfg.get("location") or "Melbourne VIC"
    results_wanted = int(cfg.get("results_wanted", 40))

    kwargs = {}
    if site == SOURCE_LINKEDIN:
        kwargs["linkedin_fetch_description"] = bool(cfg.get("fetch_description", True))
    else:
        kwargs["country_indeed"] = cfg.get("country_indeed", "australia")

    out: dict[str, dict] = {}
    for query in queries:
        try:
            df = scrape_jobs(
                site_name=[site],
                search_term=query,
                location=location,
                results_wanted=results_wanted,
                verbose=0,
                **kwargs,
            )
        except RunTimeout:
            raise            # 超时要整体中断，不能被当成「这个查询失败」吞掉
        except Exception as exc:                       # noqa: BLE001
            print(f"  [{site}] {query!r} 抓取失败: {type(exc).__name__}: {exc}")
            continue

        if df is None or len(df) == 0:
            continue

        for _, row in df.iterrows():
            source_id = _clean(row.get("id"))
            if not source_id:
                continue
            job = blank_job(site, source_id)
            for column, field in _COLUMN_MAP.items():
                job[field] = _clean(row.get(column))
            job["title"] = _clean(row.get("title"))
            job["company"] = _clean(row.get("company"))
            job["location"] = _clean(row.get("location"))
            # 薪资拼成可读字符串，便于人工核对
            lo, hi = _clean(row.get("min_amount")), _clean(row.get("max_amount"))
            cur, interval = _clean(row.get("currency")), _clean(row.get("interval"))
            if lo or hi:
                job["salary"] = " ".join(
                    str(x) for x in (lo, "-" if lo and hi else None, hi, cur, interval) if x)
            job["raw"] = {"query": query, "is_remote": _clean(row.get("is_remote"))}
            out.setdefault(job["source_id"], job)

    return list(out.values())
