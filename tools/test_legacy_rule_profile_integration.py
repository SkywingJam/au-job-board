"""PROD-047 integration: legacy_rule_profile wired into analyze, report, panel.

全部手写合成：synthetic rules / text / config / expected，只用
`tempfile.TemporaryDirectory()`。不读生产 JD、生产 DB、私人导出或真实词表。
真实 `cmd_analyze` 在临时库上运行，随后消费报告与五个面板视图 / 详情。

覆盖：

  1. 缺段 / 显式 485 / custom 全开与旧直接 `filters.evaluate` 决策全字段一致；
     评分 / 薪资 / 去重 / skills 逐行一致；labels / label_events 不变。
  2. citizen / permanent_resident / 逐规则关闭 / 显式覆盖 / override 关闭的
     实际消费者结果与手写 expected 一致；混合规则仍排除。
  3. 非法配置在 `database.connect` 之前拒绝；分析失败 rollback / RunTimeout
     传播；成功写 runs.stats，失败不写成功记录。
  4. 展示绑定「最近一次成功 analyze」的配置：当前配置改了但未重算时不误标，
     旧 runs 有可读 fallback，恶意字段转义，中英齐全，原文不翻译。

用法：
    PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tools/test_legacy_rule_profile_integration.py
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml                                              # noqa: E402
from jobs import RunTimeout, cli                         # noqa: E402
from jobs import db as database                          # noqa: E402
from jobs import filters as filters_mod                  # noqa: E402
from jobs import labels as labels_mod                    # noqa: E402
from jobs import panel                                   # noqa: E402
from jobs import report                                  # noqa: E402

failures: list = []
checks = 0

TIMESTAMPS = {"decided_at", "scored_at", "parsed_at", "first_seen_at",
              "last_seen_at", "labeled_at", "finished_at", "started_at"}

SYNTHETIC_RULES = {
    "version": 4242,
    "exclude": [
        {"id": "clearance.named", "any": ["nv1"], "desc": "synthetic cue"},
        {"id": "citizenship.exclusive",
         "any": ["must be an australian citizen"], "desc": "synthetic cue"},
        {"id": "citizenship.or_pr",
         "any": ["citizen or permanent resident"], "desc": "synthetic cue"},
        {"id": "citizenship.requirement",
         "any": ["australian citizenship"],
         "none": ["welcome"], "none_scope": "overlap",
         "weak_any": ["eligibility: australian citizen"],
         "weak_none": ["welcome"], "weak_none_scope": "sentence",
         "desc": "synthetic cue"},
        {"id": "indigenous.identified",
         "any": ["identified position"], "desc": "synthetic cue"},
        {"id": "adf.enlistment", "any": ["enlist in the adf"],
         "desc": "synthetic cue"},
    ],
    "overrides": [
        {"id": "visa.or", "any": ["citizen or hold a valid work visa"],
         "desc": "synthetic cue"},
        {"id": "synthetic.second_override", "any": ["open to all applicants"],
         "desc": "synthetic cue"},
    ],
    "tiers": {"T1": {"title": ["engineer"], "weight": 3}, "T3": {"weight": 0}},
    "level": {"positive": [], "negative": []},
    "job_level": {},
    "experience": {},
    "adjust": [],
    "location": {},
    "dedupe": {"include_city": False, "title_similarity": 0.9},
    "salary": {"enabled": False},
}

JOBS = (
    ("keep", "Synthetic Engineer", "Great role with Python."),
    ("excl_exclusive", "Synthetic Citizen Role",
     "You must be an Australian citizen to apply."),
    ("excl_orp", "Synthetic Resident Role",
     "Must be a citizen or permanent resident."),
    ("mixed", "Synthetic Mixed Role",
     "Applicants must have Australian citizenship."),
    ("over", "Synthetic Open Role",
     "Applicants must have Australian citizenship, or citizen or hold a valid work visa."),
    ("nv_only", "Synthetic Clearance Role",
     "This role requires NV1 clearance."),
)
JOB_UIDS = [f"seek:{sid}" for sid, _, _ in JOBS]


def check(label: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{('  ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


def make_fixture(root: Path, profile_section=None) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    rules_path = root / "rules.yaml"
    rules_path.write_text(
        yaml.safe_dump(SYNTHETIC_RULES, allow_unicode=True, sort_keys=False),
        encoding="utf-8")
    skills_path = root / "skills.yaml"
    skills_path.write_text(
        yaml.safe_dump({"version": 1, "skills": [{"name": "Python",
                                                  "aliases": [], "not": []}]},
                       allow_unicode=True, sort_keys=False),
        encoding="utf-8")
    db_path = root / "jobs.db"
    config = {
        "db_path": str(db_path),
        "filtering": {"mode": "legacy"},
        "report": {"out_dir": str(root / "out"), "top_n": 50},
        "panel": {"page_size": 40},
    }
    if profile_section is not None:
        config["legacy_rule_profile"] = profile_section
    config_path = root / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8")
    return {"root": root, "rules": rules_path, "skills": skills_path,
            "db": db_path, "config": config_path, "config_dict": config}


def write_config(fx: dict, profile_section) -> None:
    config = dict(fx["config_dict"])
    config.pop("legacy_rule_profile", None)
    if profile_section is not None:
        config["legacy_rule_profile"] = profile_section
    fx["config"].write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8")


def seed(db_path: Path) -> None:
    conn = database.connect(db_path)
    try:
        database.log_run(conn, "fetch", database.utcnow(), {})
        for sid, title, desc in JOBS:
            database.upsert_job(conn, {
                "source": "seek", "source_id": sid, "title": title,
                "company": "Example Co", "location": "Melbourne VIC",
                "classification": "Help Desk & IT Support", "description": desc,
                "url": f"https://example.invalid/job/{sid}"})
        labels_mod.set_label(conn, "seek:keep", source="test",
                             eligibility="eligible", interest="want",
                             note="优先投递")
        conn.commit()
    finally:
        conn.close()


def analyze(fx: dict) -> int:
    return cli.cmd_analyze(argparse.Namespace(
        config=str(fx["config"]), rules=str(fx["rules"]), skills=str(fx["skills"])))


def rows_by(conn, sql: str, key) -> dict:
    out = {}
    for row in conn.execute(sql):
        d = dict(row)
        out[key(d)] = d
    return out


def compare_business(label: str, ca, cb, sql: str, key, timestamps) -> None:
    a = rows_by(ca, sql, key)
    b = rows_by(cb, sql, key)
    if set(a) != set(b):
        check(label, False, f"keys differ {sorted(set(a) ^ set(b))}")
        return
    bad = ""
    for k in a:
        for col in set(a[k]) | set(b[k]):
            if col in timestamps:
                continue
            if a[k].get(col) != b[k].get(col):
                bad = f"{k}.{col}: {a[k].get(col)!r} != {b[k].get(col)!r}"
                break
        if bad:
            break
    check(label, not bad, bad)


def snapshot(conn, tables) -> dict:
    return {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t}")]
            for t in tables}


def decision_map(db_path: Path) -> dict:
    conn = database.connect(db_path)
    try:
        return {r["uid"]: {k: r[k] for k in
                           ("is_excluded", "rule_id", "rule_kind",
                            "matched_text", "rules_version")}
                for r in conn.execute("SELECT * FROM decisions")}
    finally:
        conn.close()


def expected_map(overrides) -> dict:
    base = {uid: (0, None) for uid in JOB_UIDS}
    base["seek:excl_exclusive"] = (1, "citizenship.exclusive")
    base["seek:excl_orp"] = (1, "citizenship.or_pr")
    base["seek:mixed"] = (1, "citizenship.requirement")
    base["seek:over"] = (0, "visa.or")
    base["seek:nv_only"] = (1, "clearance.named")
    base.update(overrides)
    return base


def test_default_equivalence() -> None:
    print("== 1. 缺段 / 显式 485 / custom 全开与旧 evaluate 一致 ==")
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        fx_default = make_fixture(root / "default", None)
        fx_485 = make_fixture(root / "p485", {"profile": "485"})
        fx_custom = make_fixture(root / "custom", {"profile": "custom"})
        for fx in (fx_default, fx_485, fx_custom):
            seed(fx["db"])

        conn = database.connect(fx_default["db"])
        try:
            before = snapshot(conn, ("labels", "label_events"))
        finally:
            conn.close()

        check("缺段 analyze rc=0", analyze(fx_default) == 0)
        check("显式 485 analyze rc=0", analyze(fx_485) == 0)
        check("custom analyze rc=0", analyze(fx_custom) == 0)

        ca = database.connect(fx_default["db"])
        cb = database.connect(fx_485["db"])
        cc = database.connect(fx_custom["db"])
        try:
            compare_business("decisions：缺段=485（业务列）", ca, cb,
                             "SELECT * FROM decisions", lambda r: r["uid"],
                             {"decided_at"})
            compare_business("scores：缺段=485", ca, cb,
                             "SELECT * FROM scores", lambda r: r["uid"],
                             {"scored_at"})
            compare_business("salary：缺段=485", ca, cb,
                             "SELECT * FROM salary", lambda r: r["uid"],
                             {"parsed_at"})
            compare_business("job_skills：缺段=485", ca, cb,
                             "SELECT * FROM job_skills",
                             lambda r: r["uid"] + "|" + r["skill"], set())
            compare_business("dedupe：缺段=485", ca, cb,
                             "SELECT uid, dedupe_key, dedupe_group FROM jobs",
                             lambda r: r["uid"], set())
            compare_business("decisions：custom=缺段", ca, cc,
                             "SELECT * FROM decisions", lambda r: r["uid"],
                             {"decided_at"})
            compare_business("scores：custom=缺段", ca, cc,
                             "SELECT * FROM scores", lambda r: r["uid"],
                             {"scored_at"})

            rules = SYNTHETIC_RULES
            all_match = True
            detail = ""
            for sid, _, _ in JOBS:
                uid = f"seek:{sid}"
                job = dict(ca.execute("SELECT * FROM jobs WHERE uid = ?",
                                      (uid,)).fetchone())
                want = filters_mod.evaluate(filters_mod.build_job_text(job), rules)
                row = dict(ca.execute(
                    "SELECT is_excluded, rule_id, rule_kind, matched_text, "
                    "rules_version FROM decisions WHERE uid = ?", (uid,)).fetchone())
                for field in ("is_excluded", "rule_id", "rule_kind",
                              "matched_text", "rules_version"):
                    if row[field] != want[field]:
                        all_match = False
                        detail = f"{uid}.{field}: {row[field]!r} != {want[field]!r}"
                        break
                if not all_match:
                    break
            check("缺段决策逐字段等于旧 filters.evaluate", all_match, detail)

            after = snapshot(ca, ("labels", "label_events"))
            check("labels / label_events 不变", before == after)
        finally:
            ca.close(); cb.close(); cc.close()

        info = report.latest_rule_profile(database.connect(fx_default["db"]))
        check("缺段 runs.stats：config_source=legacy_default",
              info["status"] == "recorded"
              and info["config_source"] == "legacy_default"
              and info["profile"] is None, str(info))
        info485 = report.latest_rule_profile(database.connect(fx_485["db"]))
        check("显式 485 runs.stats：profile=485 且开关全开",
              info485["status"] == "recorded" and info485["profile"] == "485"
              and all(info485["exclude"].values())
              and all(info485["overrides"].values()), str(info485["exclude"]))


def test_preset_consumer_results() -> None:
    print("== 2. 五 preset / 覆盖 / override 的消费者结果 ==")
    cases = (
        ("citizen", {"profile": "citizen"},
         {"seek:excl_exclusive": (0, None), "seek:excl_orp": (0, None),
          "seek:mixed": (1, "citizenship.requirement")}),
        ("permanent_resident", {"profile": "permanent_resident"},
         {"seek:excl_exclusive": (1, "citizenship.exclusive"),
          "seek:excl_orp": (0, None),
          "seek:mixed": (1, "citizenship.requirement")}),
        ("485", {"profile": "485"},
         expected_map({})),
        ("custom 全开", {"profile": "custom"}, expected_map({})),
        ("citizen 显式开 exclusive",
         {"profile": "citizen", "exclude": {"citizenship.exclusive": True}},
         {"seek:excl_exclusive": (1, "citizenship.exclusive"),
          "seek:excl_orp": (0, None),
          "seek:mixed": (1, "citizenship.requirement")}),
        ("485 关 or_pr",
         {"profile": "485", "exclude": {"citizenship.or_pr": False}},
         {"seek:excl_orp": (0, None)}),
        ("485 关 clearance.named",
         {"profile": "485", "exclude": {"clearance.named": False}},
         {"seek:nv_only": (0, None)}),
        ("485 关 visa.or override",
         {"profile": "485", "overrides": {"visa.or": False}},
         {"seek:over": (1, "citizenship.requirement")}),
        ("custom 关混合规则",
         {"profile": "custom", "exclude": {"citizenship.requirement": False}},
         {"seek:mixed": (0, None), "seek:over": (0, None)}),
    )
    with tempfile.TemporaryDirectory() as d:
        for index, (label, section, overrides) in enumerate(cases):
            fx = make_fixture(Path(d) / f"case{index}", section)
            seed(fx["db"])
            rc = analyze(fx)
            expected = expected_map(overrides)
            got = decision_map(fx["db"])
            bad = ""
            if rc != 0:
                bad = f"rc={rc}"
            else:
                for uid, (is_excluded, rule_id) in expected.items():
                    actual = (got[uid]["is_excluded"], got[uid]["rule_id"])
                    if actual != (is_excluded, rule_id):
                        bad = f"{uid}: {actual} != {(is_excluded, rule_id)}"
                        break
            check(f"{label}：消费者决策符合手写 expected", bad == "", bad)


def test_gate_rollback_stats() -> None:
    print("== 3. 配置门 / rollback / 超时 / runs.stats ==")
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        bad_sections = (
            {"profile": "sentinel-secret"},
            {"profile": "485", "exclude": {"sentinel-secret": True}},
            {"profile": "485", "exclude": None},
            {"profile": "485", "extra": 1},
            {"profile": "485", "exclude": {"clearance.named": 1}},
        )
        original_connect = cli.database.connect
        for index, section in enumerate(bad_sections):
            fx = make_fixture(root / f"bad{index}", section)
            calls = {"n": 0}

            def spy(*_a, **_k):
                calls["n"] += 1
                raise AssertionError("connect must not run")

            cli.database.connect = spy
            try:
                rc = analyze(fx)
            finally:
                cli.database.connect = original_connect
            check(f"非法配置 #{index + 1} connect 前拒绝且零 DB 访问",
                  rc != 0 and calls["n"] == 0, f"rc={rc} calls={calls['n']}")

        fx = make_fixture(root / "flow", {"profile": "485"})
        seed(fx["db"])
        check("首次 analyze 成功", analyze(fx) == 0)
        conn = database.connect(fx["db"])
        try:
            before = {r["uid"]: r["is_excluded"]
                      for r in conn.execute("SELECT uid, is_excluded FROM decisions")}
            runs_before = conn.execute(
                "SELECT COUNT(*) FROM runs WHERE kind = 'analyze'").fetchone()[0]
            stats = json.loads(conn.execute(
                "SELECT stats FROM runs WHERE kind = 'analyze' "
                "ORDER BY run_id DESC LIMIT 1").fetchone()[0])
        finally:
            conn.close()
        check("成功写入 runs.stats.rule_profile",
              isinstance(stats.get("rule_profile"), dict)
              and stats["rule_profile"]["profile"] == "485"
              and stats["rule_profile"]["config_source"] == "explicit",
              str(stats.get("rule_profile")))

        conn = database.connect(fx["db"])
        database.upsert_job(conn, {
            "source": "seek", "source_id": "late", "title": "Synthetic Late",
            "company": "Example Co", "location": "Melbourne VIC",
            "classification": "Data Analysts", "description": "No requirement.",
            "url": "https://example.invalid/job/late"})
        conn.commit()
        conn.close()

        original_sync = cli._sync_skills_transactional

        def boom(*_a, **_k):
            raise ValueError("synthetic failure")

        cli._sync_skills_transactional = boom
        try:
            rc = analyze(fx)
        finally:
            cli._sync_skills_transactional = original_sync
        conn = database.connect(fx["db"])
        try:
            after = {r["uid"]: r["is_excluded"]
                     for r in conn.execute("SELECT uid, is_excluded FROM decisions")}
            runs_after = conn.execute(
                "SELECT COUNT(*) FROM runs WHERE kind = 'analyze'").fetchone()[0]
        finally:
            conn.close()
        check("失败 rollback 到上一提交决策集", rc != 0 and after == before,
              f"rc={rc}")
        check("失败不写成功 runs 记录", runs_after == runs_before,
              f"{runs_before} -> {runs_after}")

        def timed_out(*_a, **_k):
            raise RunTimeout()

        cli._sync_skills_transactional = timed_out
        try:
            try:
                analyze(fx)
                outcome = "returned"
            except RunTimeout:
                outcome = "propagated"
            except Exception as exc:                     # noqa: BLE001
                outcome = f"wrong {type(exc).__name__}"
        finally:
            cli._sync_skills_transactional = original_sync
        check("RunTimeout 向上传播", outcome == "propagated", outcome)


def test_display_binding() -> None:
    print("== 4. 面板不显示规则摘要 / 后台记录与报告保留 ==")
    with tempfile.TemporaryDirectory() as d:
        fx = make_fixture(Path(d) / "panel", None)
        seed(fx["db"])
        check("面板用库 analyze 成功", analyze(fx) == 0)
        conn = database.connect(fx["db"])
        try:
            info = report.latest_rule_profile(conn)
            check("后台记录 legacy_default",
                  info["status"] == "recorded" and info["profile"] is None
                  and info["config_source"] == "legacy_default", str(info))

            # 当前配置改成 citizen，但不重算：后台记录仍是结果配置。
            write_config(fx, {"profile": "citizen"})
            after = report.latest_rule_profile(conn)
            check("改当前配置未重算时后台记录不误标",
                  after["status"] == "recorded" and after["profile"] is None,
                  str(after))

            # 旧 runs 无 rule_profile：可读 fallback。
            database.log_run(conn, "analyze", database.utcnow(), {})
            conn.commit()
            check("旧记录 fallback = unavailable",
                  report.latest_rule_profile(conn)["status"] == "unavailable")

            # 面板五视图（中 / 英）都不再含规则状态摘要。
            views = ("recommend", "all", "new", "excluded", "labelled")
            bad = ""
            for lang in ("zh", "en"):
                for view in views:
                    html = panel.render_page(conn, fx["config_dict"], view, 0, 40,
                                             None, {}, lang, None)
                    if "规则开关" in html or "Rule switches" in html:
                        bad = f"{lang}/{view}"
                        break
                if bad:
                    break
            check("五个面板视图（中英）都不含规则摘要", bad == "", bad)

            row = panel._find(conn, fx["config_dict"], "seek:keep")
            detail = panel._detail_html(row, "zh")
            check("详情保留原文、不翻译", "Great role with Python." in detail)
            row_mixed = panel._find(conn, fx["config_dict"], "seek:mixed")
            detail_mixed = panel._detail_html(row_mixed, "zh")
            check("详情保留人工标注 / 原文摘录",
                  "Applicants must have Australian citizenship." in detail_mixed)

            info_report = report.write_report(conn, fx["config_dict"])
            out = Path(info_report["out_dir"])
            csv_text = (out / f"{info_report['stamp']}-scored.csv").read_text(
                encoding="utf-8-sig")
            md_text = (out / f"{info_report['stamp']}-top.md").read_text(
                encoding="utf-8")
            check("CSV 含 rule_profile / rule_disabled 列",
                  "rule_profile" in csv_text and "rule_disabled" in csv_text)
            check("md 报告含规则开关摘要", "规则开关：" in md_text)
        finally:
            conn.close()



def main() -> int:
    test_default_equivalence()
    test_preset_consumer_results()
    test_gate_rollback_stats()
    test_display_binding()
    print()
    if failures:
        print(f"失败 {len(failures)} 项：")
        for item in failures:
            print(f"  - {item}")
        return 2
    print(f"全部通过（{checks} 项检查）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
