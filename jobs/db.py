"""SQLite 落库层。

解耦设计的核心：
  - `fetch` 只写 `jobs` 表（唯一联网的环节）
  - `filter` / `score` 只读 `jobs`，写 `decisions` / `scores`（永不联网）

因此调整 rules.yaml 后重跑打分是秒级的，不需要也不应该重新抓取。
原始 JD 正文永久保留在 `jobs.description`，这是「不重抓」的前提。
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    uid                     TEXT PRIMARY KEY,   -- "{source}:{source_id}"
    source                  TEXT NOT NULL,
    source_id               TEXT NOT NULL,
    title                   TEXT,
    company                 TEXT,
    location                TEXT,
    url                     TEXT,
    salary                  TEXT,
    work_type               TEXT,
    classification          TEXT,
    job_level               TEXT,
    listing_date            TEXT,
    teaser                  TEXT,
    description             TEXT,               -- 原始正文，永久保留
    description_fetched_at  TEXT,               -- NULL = 待补详情（详情队列）
    dedupe_key              TEXT,               -- 规范化后的匹配键
    dedupe_group            TEXT,               -- 去重组代表 uid
    first_seen_at           TEXT NOT NULL,
    last_seen_at            TEXT NOT NULL,
    raw_json                TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_detail_queue ON jobs(description_fetched_at);
CREATE INDEX IF NOT EXISTS idx_jobs_source       ON jobs(source);
CREATE INDEX IF NOT EXISTS idx_jobs_dedupe       ON jobs(dedupe_key);

CREATE TABLE IF NOT EXISTS decisions (
    uid          TEXT PRIMARY KEY,
    is_excluded  INTEGER NOT NULL,   -- 列名不用 "excluded"：那是 upsert 的保留别名
    rule_id      TEXT,
    rule_kind    TEXT,      -- exclude | override | none
    matched_text TEXT,      -- provenance：命中的具体片段
    rules_version INTEGER,
    decided_at   TEXT
);

CREATE TABLE IF NOT EXISTS scores (
    uid          TEXT PRIMARY KEY,
    tier         TEXT,
    tier_score   INTEGER,
    level_score  INTEGER,
    adjust_score INTEGER,
    total_score  INTEGER,
    reasons      TEXT,      -- JSON: 各项加/减分的原因
    rules_version INTEGER,
    scored_at    TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    run_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT,
    started_at TEXT,
    finished_at TEXT,
    stats      TEXT
);

-- 人工标注。**这是项目里唯一不可再生的数据** —— 岗位可以重抓，人的判断不能。
-- 因此它必须与 analyze 完全隔离：analyze 会清 decisions/scores，
-- 但永远不得触碰 labels（见 clear_decisions_and_scores）。
CREATE TABLE IF NOT EXISTS labels (
    uid               TEXT PRIMARY KEY,
    eligibility       TEXT,      -- eligible | ineligible | unsure
    ineligible_reason TEXT,      -- 尤其用于记录「漏杀」：为什么这条不该出现
    interest          TEXT,      -- want | maybe | no
    action            TEXT,      -- saved | applied | skipped
    applied_at        TEXT,
    note              TEXT,
    rules_version     INTEGER,   -- 标注时生效的规则版本，回看时避免误读
    labeled_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_labels_eligibility ON labels(eligibility);

-- 标注变更流水。**每一次写入都留痕** —— 标注是唯一不可再生的数据，
-- 而面板上的按钮是开关（再点一次会清空），误点就会静默丢数据。
-- 有了这张表，任何一次丢失都能回放出来并恢复。
CREATE TABLE IF NOT EXISTS label_events (
    event_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    uid          TEXT NOT NULL,
    field        TEXT,
    old_value    TEXT,
    new_value    TEXT,
    source       TEXT,      -- cli | panel
    rules_version INTEGER,
    at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_label_events_uid ON label_events(uid);

-- 解析出来的薪资。**定位是难度信号，不是筛选维度。**
-- 薪资字段允许为空：缺失只表示源数据没写，不代表这条岗位是低薪。
-- 存在缺失时，只比较已知薪资（如排序或过滤出「高薪榜」）
-- 可能产生选择偏差。它只回答一个问题：这条岗位看起来有多资深。
CREATE TABLE IF NOT EXISTS salary (
    uid         TEXT PRIMARY KEY,
    currency    TEXT,
    period      TEXT,      -- year | month | day | hour
    basis       TEXT,      -- annual（本来就是年薪）| annualized（由时/日/月折算）
    min_amount  REAL,      -- 原文数字
    max_amount  REAL,
    annual_min  REAL,
    annual_max  REAL,
    band        TEXT,      -- 难度档位文案
    hint        INTEGER,   -- 对打分的调整
    origin      TEXT,      -- field（平台字段）| description（从正文挖的）
    raw         TEXT,
    parsed_at   TEXT
);

-- 从 JD 抽出的技术栈。**不是判断，是词表命中结果** —— 可随 skills.yaml
-- 重算（见 jobs/skills.py），analyze 与面板编辑词表后都会重建这张表。
-- 单独一张表而不是 jobs 的列：一个岗位命中多个技能，且按技能筛选要建索引。
CREATE TABLE IF NOT EXISTS job_skills (
    uid   TEXT NOT NULL,
    skill TEXT NOT NULL,
    PRIMARY KEY (uid, skill)
);
CREATE INDEX IF NOT EXISTS idx_job_skills_skill ON job_skills(skill);

-- 面板等界面用的零散状态（如「上次访问时间」，支撑"新增"视图）
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# 元数据字段：抓取时若已有非空值，不要用空值覆盖
META_FIELDS = (
    "title", "company", "location", "url", "salary",
    "work_type", "classification", "job_level", "listing_date", "teaser",
)


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def connect(path: str | Path) -> sqlite3.Connection:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    # WAL：未来面板会一边读、fetch 一边写。默认 rollback journal 下
    # 写事务会阻塞读，容易出现 "database is locked"。
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def upsert_job(conn: sqlite3.Connection, job: dict) -> str:
    """插入或更新一条职位。

    关键行为：
      - description 只在新值非空、且旧值为空时才写入（不覆盖已抓到的正文）
      - 已有正文的职位不会被重新排队
    返回 "inserted" 或 "updated"。
    """
    uid = f"{job['source']}:{job['source_id']}"
    now = utcnow()
    row = conn.execute("SELECT * FROM jobs WHERE uid = ?", (uid,)).fetchone()

    desc = (job.get("description") or "").strip()
    raw = json.dumps(job.get("raw") or {}, ensure_ascii=False, default=str)

    if row is None:
        conn.execute(
            """INSERT INTO jobs (uid, source, source_id, title, company, location, url,
                   salary, work_type, classification, job_level, listing_date, teaser,
                   description, description_fetched_at, first_seen_at, last_seen_at, raw_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                uid, job["source"], str(job["source_id"]),
                job.get("title"), job.get("company"), job.get("location"), job.get("url"),
                job.get("salary"), job.get("work_type"), job.get("classification"),
                job.get("job_level"), job.get("listing_date"), job.get("teaser"),
                desc or None, now if desc else None, now, now, raw,
            ),
        )
        return "inserted"

    # 更新：元数据只在有新值时覆盖
    sets, vals = [], []
    for f in META_FIELDS:
        v = job.get(f)
        if v not in (None, ""):
            sets.append(f"{f} = ?")
            vals.append(v)
    if desc and not (row["description"] or "").strip():
        sets.append("description = ?")
        vals.append(desc)
        sets.append("description_fetched_at = ?")
        vals.append(now)
    if raw and raw != "{}":
        sets.append("raw_json = ?")
        vals.append(raw)
    sets.append("last_seen_at = ?")
    vals.append(now)
    vals.append(uid)
    conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE uid = ?", vals)
    return "updated"


def jobs_missing_detail(conn: sqlite3.Connection, source: str, limit: int | None = None):
    """详情队列：本体尚未抓到的职位。"""
    sql = ("SELECT uid, source_id FROM jobs "
           "WHERE source = ? AND (description IS NULL OR TRIM(description) = '') "
           "ORDER BY first_seen_at DESC")
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, (source,)).fetchall()


def all_jobs(conn: sqlite3.Connection):
    return conn.execute("SELECT * FROM jobs").fetchall()


def set_description(conn: sqlite3.Connection, uid: str, description: str) -> None:
    conn.execute(
        "UPDATE jobs SET description = ?, description_fetched_at = ? WHERE uid = ?",
        (description, utcnow(), uid),
    )


def set_dedupe(conn: sqlite3.Connection, uid: str, key: str, group: str) -> None:
    conn.execute("UPDATE jobs SET dedupe_key = ?, dedupe_group = ? WHERE uid = ?",
                 (key, group, uid))


def replace_decision(conn, uid, is_excluded, rule_id=None, rule_kind=None,
                     matched_text=None, rules_version=None) -> None:
    # INSERT OR REPLACE：重跑打分时整体覆盖，不做增量合并
    conn.execute(
        """INSERT OR REPLACE INTO decisions
               (uid, is_excluded, rule_id, rule_kind, matched_text, rules_version, decided_at)
           VALUES (?,?,?,?,?,?,?)""",
        (uid, int(is_excluded), rule_id, rule_kind, matched_text,
         rules_version, utcnow()),
    )


def replace_score(conn, uid, tier, tier_score, level_score, adjust_score,
                  total_score, reasons, rules_version) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO scores
               (uid, tier, tier_score, level_score, adjust_score, total_score,
                reasons, rules_version, scored_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (uid, tier, tier_score, level_score, adjust_score, total_score,
         json.dumps(reasons, ensure_ascii=False), rules_version, utcnow()),
    )


def clear_decisions_and_scores(conn) -> None:
    """重跑 analyze 时清空派生的判定与分数。

    **刻意不清 labels** —— 人工标注是项目里唯一不可再生的数据
    （岗位可以重抓，人的判断不能）。将来若要加清理逻辑，看这一行注释就够。
    """
    conn.execute("DELETE FROM decisions")
    conn.execute("DELETE FROM scores")


def get_meta(conn, key: str, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
    conn.commit()


def replace_salary(conn, uid: str, parsed: dict | None) -> None:
    if parsed is None:
        conn.execute("DELETE FROM salary WHERE uid = ?", (uid,))
        return
    conn.execute(
        """INSERT OR REPLACE INTO salary
               (uid, currency, period, basis, min_amount, max_amount, annual_min,
                annual_max, band, hint, origin, raw, parsed_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (uid, parsed.get("currency"), parsed.get("period"), parsed.get("basis"),
         parsed.get("min"), parsed.get("max"), parsed.get("annual_min"),
         parsed.get("annual_max"), parsed.get("band"), parsed.get("hint", 0),
         parsed.get("origin"), parsed.get("raw"), utcnow()),
    )


def salaries(conn) -> dict:
    return {r["uid"]: dict(r) for r in conn.execute("SELECT * FROM salary")}


def clear_skills(conn) -> None:
    """重算技能前清空。抽取是纯函数（见 skills.sync），这张表随时可重建。"""
    conn.execute("DELETE FROM job_skills")


def replace_skills(conn, uid: str, skills) -> None:
    conn.execute("DELETE FROM job_skills WHERE uid = ?", (uid,))
    conn.executemany("INSERT OR IGNORE INTO job_skills (uid, skill) VALUES (?, ?)",
                     [(uid, skill) for skill in skills or []])


def add_skills(conn, pairs) -> None:
    """批量写入 job_skills。调用方负责先 clear_skills（见 skills.sync）。"""
    conn.executemany("INSERT OR IGNORE INTO job_skills (uid, skill) VALUES (?, ?)",
                     list(pairs))


def skills_map(conn) -> dict:
    """uid -> [技能名]。列表/详情/筛选都读它，避免一次一查的 N+1。"""
    out: dict[str, list[str]] = {}
    for row in conn.execute("SELECT uid, skill FROM job_skills ORDER BY uid, skill"):
        out.setdefault(row["uid"], []).append(row["skill"])
    return out


def skill_counts(conn) -> dict:
    """技能 -> 命中篇数，按命中降序。面板的筛选器按它决定展示哪批词。"""
    return {row["skill"]: row["n"] for row in conn.execute(
        "SELECT skill, COUNT(*) AS n FROM job_skills "
        "GROUP BY skill ORDER BY n DESC, skill")}


def last_run_at(conn, kind: str | None = None) -> str | None:
    """最近一次完成时间。面板用它显示「上次抓取」，用来发现定时任务已经悄悄停了。"""
    if kind:
        row = conn.execute("SELECT MAX(finished_at) AS t FROM runs WHERE kind = ?",
                           (kind,)).fetchone()
    else:
        row = conn.execute("SELECT MAX(finished_at) AS t FROM runs").fetchone()
    return row["t"] if row else None


def last_batch_started_at(conn) -> str | None:
    """最近一批抓取的开始时间。

    「新增」视图按它取数：一次 fetch 里插入的岗位，first_seen_at 都落在
    [started_at, finished_at] 之内，所以 `first_seen_at >= started_at`
    就是「最近这一批新抓到的」。

    早先是拿「上次访问面板的时间」当基线，而前端每次加载页面都会把它推到现在 ——
    基线永远晚于最近一次抓取，于是「新增」恒为空。改成按抓取批次算就不会了，
    语义也更直白：点开就是最近一批 JD，不用先访问一次再等下一批。
    """
    row = conn.execute(
        "SELECT started_at FROM runs WHERE kind = 'fetch' "
        "ORDER BY run_id DESC LIMIT 1").fetchone()
    return row["started_at"] if row else None


def log_run(conn, kind: str, started_at: str, stats: dict) -> None:
    conn.execute(
        "INSERT INTO runs (kind, started_at, finished_at, stats) VALUES (?,?,?,?)",
        (kind, started_at, utcnow(), json.dumps(stats, ensure_ascii=False)),
    )


def counts(conn) -> dict:
    def one(sql, *a):
        return conn.execute(sql, a).fetchone()[0]
    return {
        "jobs": one("SELECT COUNT(*) FROM jobs"),
        "with_description": one(
            "SELECT COUNT(*) FROM jobs WHERE description IS NOT NULL AND TRIM(description) <> ''"),
        "decisions": one("SELECT COUNT(*) FROM decisions"),
        "excluded": one("SELECT COUNT(*) FROM decisions WHERE is_excluded = 1"),
        "scores": one("SELECT COUNT(*) FROM scores"),
    }
