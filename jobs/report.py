"""报告与审计输出。全部只读 DB，不联网。

产出：
  - 精选清单（CSV + Markdown）：通过硬过滤、按分数排序、已去重
  - 排除清单（CSV）：**审计用**。误杀是沉默的，唯一能发现它的办法就是人工翻这份
  - 标注清单：见 labels.export

本文件的渲染统一走 `render.py`，与未来的面板共用同一套口径，
避免 md 和面板两边各写一份、慢慢走样。
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import sqlite3
from pathlib import Path

from . import db as database
from . import labels as labels_mod
from .render import eligibility_snippet, render_job_lines, tag_texts

COLUMNS = ["score", "tier", "tags", "eligibility_cue", "eligibility_snippet",
           "title", "company", "location", "source", "listing_date", "salary",
           "work_type", "url", "reasons", "rule_profile", "rule_disabled",
           "label_eligibility", "label_interest", "label_action", "label_note"]


def _rows(conn) -> list[dict]:
    sql = """
        SELECT j.uid, j.source, j.title, j.teaser, j.company, j.location, j.url, j.salary,
               j.work_type, j.listing_date, j.classification, j.job_level,
               j.description, j.dedupe_group, j.first_seen_at, j.last_seen_at,
               d.is_excluded, d.rule_id, d.rule_kind, d.matched_text,
               d.rules_version AS decision_rules_version,
               s.tier, s.total_score, s.tier_score, s.level_score, s.adjust_score, s.reasons,
               sal.band AS salary_band, sal.hint AS salary_hint, sal.basis AS salary_basis,
               sal.period AS salary_period, sal.origin AS salary_origin,
               sal.annual_min AS salary_annual_min, sal.annual_max AS salary_annual_max,
               sal.currency AS salary_currency
        FROM jobs j
        LEFT JOIN decisions d   ON d.uid = j.uid
        LEFT JOIN scores    s   ON s.uid = j.uid
        LEFT JOIN salary    sal ON sal.uid = j.uid
    """
    return [dict(r) for r in conn.execute(sql)]


def _group_index(all_rows: list[dict]) -> dict[str, list[str]]:
    """去重组 -> 组内全部 uid。标注挂在单条 uid 上，
    但展示是按去重组，而组代表会随规则变化换人，所以要能查到整组的标注。"""
    index: dict[str, list[str]] = {}
    for row in all_rows:
        index.setdefault(row.get("dedupe_group") or row["uid"], []).append(row["uid"])
    return index


def label_of(row: dict, labels: dict[str, dict], group_index: dict[str, list[str]]) -> dict:
    """取该行所属去重组的人工标注（合并后）。

    比 decorate() 便宜得多 —— 只查 labels 表，不抽资格句、不算 tag。
    筛选必须用它，否则为了筛选就得先把整个池子装饰一遍。
    """
    group = row.get("dedupe_group") or row["uid"]
    in_group = [labels[u] for u in group_index.get(group, [row["uid"]]) if u in labels]
    return labels_mod.merge_labels(in_group) or {}


def _decorate(rows: list[dict], labels: dict[str, dict],
              group_index: dict[str, list[str]]) -> list[dict]:
    """把资格摘录、tag、标注写进行里，供 CSV / md 共用。"""
    for row in rows:
        snippet, cue = eligibility_snippet(row.get("description"))
        row["eligibility_snippet"] = snippet or ""
        row["eligibility_cue"] = cue or ""
        row["tags"] = " ".join(tag_texts(row))

        merged = label_of(row, labels, group_index)
        row["_label"] = merged
        for key in ("eligibility", "interest", "action", "note"):
            row[f"label_{key}"] = merged.get(key) or ""
    return rows


def _sort_key(row: dict):
    return (-(row.get("total_score") or -999), row.get("title") or "")


def _best_per_group(rows: list[dict]) -> list[dict]:
    """每个去重组只保留分数最高的一条，并记录被合并掉的来源与城市。"""
    best: dict[str, dict] = {}
    dupes: dict[str, list[str]] = {}
    for row in rows:
        group = row.get("dedupe_group") or row["uid"]
        tag = f"{row['source']}@{row.get('location') or '-'}"
        dupes.setdefault(group, []).append(tag)
        if group not in best or _sort_key(row) < _sort_key(best[group]):
            best[group] = row
    for group, row in best.items():
        own = f"{row['source']}@{row.get('location') or '-'}"
        others = [t for t in dupes[group] if t != own]
        row["duplicate_count"] = len(others)
        row["duplicate_sources"] = "; ".join(dict.fromkeys(others))
    return list(best.values())


def best_per_group(rows: list[dict]) -> list[dict]:
    """给「全部」视图用：kept 已经去过重，excluded 没有（审计要看全部），
    两边合起来必须再补一次，否则同一岗位的三个平台副本会并排出现。"""
    return _best_per_group(rows)


def load(conn, config: dict):
    """取数 + 去重 + 排序。**刻意不做装饰。**

    装饰（抽资格句、算 tag）是最耗时的一步。面板一次只显示一页，所以
    调用方应先用 load() 拿到顺序，**切片之后**再对那几十条调用
    decorate()，避免每个请求都装饰整库。
    """
    rows = _rows(conn)
    index = _group_index(rows)
    labels = labels_mod.get_labels(conn)
    # 技能命中结果直接挂在行上（一次查询，避免逐行 N+1）。它是 tag、
    # 筛选和搜索共用的输入，所以要在装饰之前就能读到 —— 筛选不跑 decorate。
    skill_hits = database.skills_map(conn)
    for row in rows:
        row["skills"] = skill_hits.get(row["uid"], [])

    kept = _best_per_group([r for r in rows if not r["is_excluded"]])
    kept.sort(key=_sort_key)
    # 排除清单不去重 —— 审计要看全部
    excluded = sorted([r for r in rows if r["is_excluded"]],
                      key=lambda r: (r.get("rule_id") or "", r.get("title") or ""))
    return kept, excluded, labels, index


def decorate(rows, labels, index) -> list[dict]:
    """给指定的行补上资格句 / tag / 标注。入参应为已切片的小集合。"""
    return _decorate(list(rows), labels, index)


def has_labels(row: dict, labels: dict[str, dict], index: dict[str, list[str]]) -> bool:
    """该行所属去重组里是否有人工标注。

    判断「有没有标过」不该先做装饰（装饰才是耗时的那步），
    所以这里只看 labels 表 + 组索引。
    """
    group = row.get("dedupe_group") or row["uid"]
    return any(uid in labels for uid in index.get(group, [row["uid"]]))


def build(conn, config: dict):
    """全量装饰版本，给「一次要拿到全部」的调用方（md 报告、脚本、测试）。"""
    kept, excluded, labels, index = load(conn, config)
    return _decorate(kept, labels, index), _decorate(excluded, labels, index)


def _write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def latest_rule_profile(conn) -> dict:
    """最近一次成功 analyze 记录的规则开关（runs.stats.rule_profile）。

    只读 runs 一次；记录缺失、旧版本或损坏时返回 `status="unavailable"`，
    界面据此显示「旧分析未记录 profile」。不读文件、不重算、不写库。
    """
    unavailable = {
        "status": "unavailable",
        "profile": None,
        "config_source": None,
        "exclude": {},
        "overrides": {},
        "explicit_exclude": {},
        "explicit_overrides": {},
    }
    try:
        row = conn.execute(
            "SELECT stats FROM runs WHERE kind = 'analyze' "
            "ORDER BY run_id DESC LIMIT 1").fetchone()
    except sqlite3.Error:
        return unavailable
    if row is None:
        return unavailable
    try:
        stats = json.loads(row[0] or "{}")
    except (TypeError, ValueError):
        return unavailable
    info = stats.get("rule_profile") if isinstance(stats, dict) else None
    if not isinstance(info, dict):
        return unavailable

    def mapping(key):
        value = info.get(key)
        return dict(value) if isinstance(value, dict) else {}

    return {
        "status": "recorded",
        "profile": info.get("profile") if isinstance(info.get("profile"), str) else None,
        "config_source": (info.get("config_source")
                          if isinstance(info.get("config_source"), str) else None),
        "exclude": mapping("exclude"),
        "overrides": mapping("overrides"),
        "explicit_exclude": mapping("explicit_exclude"),
        "explicit_overrides": mapping("explicit_overrides"),
    }


def _disabled_switches(info: dict) -> list:
    disabled = [rid for rid, on in (info.get("exclude") or {}).items() if not on]
    disabled += [rid for rid, on in (info.get("overrides") or {}).items() if not on]
    return disabled


def _explicit_switches(info: dict) -> list:
    pairs = list((info.get("explicit_exclude") or {}).items())
    pairs += list((info.get("explicit_overrides") or {}).items())
    return [f"{rid}={'true' if value else 'false'}" for rid, value in pairs]


def _rule_csv_fields(info: dict) -> dict:
    """CSV 用的紧凑规则开关列；旧记录缺信息时留空。"""
    if info.get("status") != "recorded":
        return {"rule_profile": "", "rule_disabled": ""}
    return {
        "rule_profile": info.get("profile") or "legacy_default",
        "rule_disabled": ";".join(_disabled_switches(info)),
    }


def rule_profile_text(info: dict) -> str:
    """一行中文摘要，供 md 报告与日志展示。只含 id / bool，不含 regex。"""
    if info.get("status") != "recorded":
        return "旧分析未记录 profile（展示沿用旧决策；重算后生效）"
    profile = info.get("profile") or "缺省（全部规则开启）"
    disabled = _disabled_switches(info)
    explicit = _explicit_switches(info)
    return (f"profile {profile} ｜ 来源 {info.get('config_source') or '-'} ｜ "
            f"关闭 exclude/override {('、'.join(disabled)) if disabled else '无'} ｜ "
            f"显式覆盖 {('、'.join(explicit)) if explicit else '无'}")


def write_report(conn, config: dict) -> dict:
    cfg = config.get("report") or {}
    top_n = int(cfg.get("top_n", 60))
    out_dir = Path(cfg.get("out_dir", "out"))
    if not out_dir.is_absolute():
        from .config import ROOT
        out_dir = ROOT / out_dir

    stamp = dt.date.today().isoformat()
    kept, excluded, labels, index = load(conn, config)
    # 只装饰真正要写出去的行 —— 没必要为整库全部抽资格句
    top = decorate(kept[:top_n], labels, index)
    excluded = decorate(excluded, labels, index)

    # 规则开关是「最近一次成功 analyze 使用的配置」，每次报告只读一次 runs；
    # 不把当前尚未生效的配置挂到已存决策上。
    rule_info = latest_rule_profile(conn)
    rule_fields = _rule_csv_fields(rule_info)
    for row in top:
        row.update(rule_fields)
    for row in excluded:
        row.update(rule_fields)

    # --- 精选 CSV ---
    _write_csv(out_dir / f"{stamp}-scored.csv", top,
               COLUMNS + ["duplicate_count", "duplicate_sources"])

    # --- 精选 Markdown ---
    stats = labels_mod.summary(conn)
    lines = [
        f"# 高分岗位（{stamp}）", "",
        f"入库 {len(_rows(conn))} 条，通过硬过滤 {len(kept)} 条（去重后），列出前 {len(top)} 条。", "",
        f"规则开关：{rule_profile_text(rule_info)}", "",
        f"标注进度：" + "　".join(f"{k} {v}" for k, v in stats.items()), "",
        "> 扫「资格句」那一列即可发现**漏杀**（该排除却没被排除的岗位）。",
        "> 发现后请标注：`python -m jobs.cli label --find \"公司名\"` 拿到 uid，"
        "再 `label <uid> --eligibility ineligible --reason \"...\"`。", "",
    ]
    for i, row in enumerate(top, 1):
        lines += render_job_lines(row, index=i, label=row.get("_label"))
        lines.append("")
    (out_dir / f"{stamp}-top.md").write_text("\n".join(lines), encoding="utf-8")

    # --- 排除清单（审计用）---
    audit_cols = ["rule_id", "rule_kind", "matched_text", "eligibility_cue",
                  "eligibility_snippet", "rule_profile", "rule_disabled",
                  "title", "company", "location", "source",
                  "label_eligibility", "url"]
    _write_csv(out_dir / f"{stamp}-excluded.csv",
               sorted(excluded, key=lambda r: (r.get("rule_id") or "", r.get("title") or "")),
               audit_cols)

    return {
        "kept": len(kept), "excluded": len(excluded), "top": len(top),
        "out_dir": str(out_dir), "stamp": stamp,
    }


def write_audit_summary(conn, out_dir: Path) -> Path:
    """按规则汇总排除量，方便一眼看出哪条规则在大量误杀。"""
    sql = """
        SELECT d.rule_id, COUNT(*) AS n
        FROM decisions d WHERE d.is_excluded = 1
        GROUP BY d.rule_id ORDER BY n DESC
    """
    rows = [dict(r) for r in conn.execute(sql)]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "exclusion-summary.json"
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
