"""命令行入口。

  python -m jobs.cli run        日常全流程：抓取 + 补详情 + 分析 + 出报告（唯一需要联网的组合）
  python -m jobs.cli fetch      仅抓列表（联网）
  python -m jobs.cli details    仅补详情队列（联网）
  python -m jobs.cli analyze    仅过滤 + 打分 + 去重（**不联网**，调规则用这个）
  python -m jobs.cli report     仅输出报告（**不联网**）
  python -m jobs.cli audit      查看被排除的 JD（**不联网**，用于发现误杀）
  python -m jobs.cli status     查看库内统计

抓取与打分是解耦的：analyze / report / audit 永远不会联网，
所以改 rules.yaml 后重跑是秒级的，不需要也不应该重新抓取。
"""

from __future__ import annotations

import argparse
from contextlib import closing
import copy
import uuid
import json
import sqlite3
import os
import signal
import sys
import time
import threading
from pathlib import Path

import yaml

from . import RunTimeout
from . import db as database
from . import legacy_rule_profiles
from .config import (db_path, get_eligibility, load_config, load_rules,
                     read_config_root, resolve_filtering)
from .dedupe import assign_groups, dedupe_key
from .filters import build_job_text, evaluate
from .scoring import score_job
from . import skills as skills_mod
from . import run_state

# 供 cmd_skills 复用；模块级 import 避免每个分支再 import 一次

# 哪些来源支持「事后补详情」。jobspy 的正文随列表一起返回，不进队列。
DETAIL_FETCHERS = {"seek": "jobs.sources.seek:fetch_detail"}


def _resolve(path: str):
    module, _, attr = path.partition(":")
    import importlib
    return getattr(importlib.import_module(module), attr)


def _open(args):
    config = copy.deepcopy(getattr(args, "_config_snapshot", None))
    if config is None:
        config = load_config(getattr(args, "config", None))
    return config, database.connect(db_path(config))


# --------------------------------------------------------------------------
# 联网命令
# --------------------------------------------------------------------------

def _cmd_fetch(args) -> int:
    from .sources import iter_sources
    from . import batches

    config, conn = _open(args)
    # Closing also rolls back any uncommitted batch before outer finalization.
    with closing(conn):
        queries = config.get("queries") or []
        config["_run_source_failures"] = {}
        if not queries:
            print("config.yaml 里没有 queries", file=sys.stderr)
            return 1

        only = getattr(args, "only", None)
        selected = [(name, fn) for name, fn in iter_sources(config) if not only or name == only]
        if not selected:
            return 1

        started = database.utcnow()
        stats, failed_sources = {}, set()
        batch_id = None
        try:
            batch_id = batches.start_batch(
                conn, pipeline_task_id=getattr(args, "_task_id", None),
                trigger=getattr(args, "trigger", None) or "cli")
            conn.commit()
            for name, fetch_fn in selected:
                print(f"[fetch] {name} ...", flush=True)
                try:
                    jobs = fetch_fn(config, queries)
                except RunTimeout:
                    raise            # 全流程超时必须向上传播
                except Exception as exc:                   # noqa: BLE001
                    print(f"  [{name}] 失败: {type(exc).__name__}: {exc}")
                    stats[name] = {"error": str(exc)}
                    failed_sources.add(name)
                    if hasattr(args, "_run_failed_sources"):
                        args._run_failed_sources.add(name)
                    continue
                inserted = updated = 0
                for job in jobs:
                    result = database.upsert_job(conn, job)
                    batches.record_member(conn, batch_id,
                                          f"{job['source']}:{job['source_id']}", result == "inserted")
                    inserted += result == "inserted"
                    updated += result == "updated"
                # The source's jobs, locations and exact members publish together.
                conn.commit()
                stats[name] = {"seen": len(jobs), "inserted": inserted, "updated": updated}
                if hasattr(args, "_run_counts"):
                    for field in ("seen", "inserted", "updated"):
                        args._run_counts[field] += stats[name][field]
                print(f"  {name}: 见到 {len(jobs)}，新增 {inserted}，更新 {updated}")

            failed_sources.update(config.get("_run_source_failures", {}))
            if hasattr(args, "_run_failed_sources"):
                args._run_failed_sources.update(failed_sources)
            database.log_run(conn, "fetch", started, stats)
            run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            batches.finish_batch(conn, batch_id, "partial" if failed_sources else "success",
                                 error="source_failed" if failed_sources else None,
                                 fetch_run_id=run_id)
            conn.commit()
        except BaseException as exc:
            # Preserve already committed sources and discard this source's jobs
            # and members before recording a terminal collection fact. Never turn
            # RunTimeout, SIGTERM/SystemExit or KeyboardInterrupt into success.
            conn.rollback()
            if batch_id is not None:
                state, error = "error", "collection_failed"
                if isinstance(exc, RunTimeout):
                    state, error = "timeout", "run_timeout"
                elif isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    state, error = "interrupted", "collection_interrupted"
                try:
                    batches.finish_batch(conn, batch_id, state, error=error)
                    conn.commit()
                except Exception:
                    conn.rollback()
                    print("[fetch] 批次收尾失败，保留未完成状态。", file=sys.stderr)
            raise
        print(f"[fetch] 完成 -> {database.counts(conn)}")
        return 0


def cmd_details(args) -> int:
    config, conn = _open(args)
    # Closing also rolls back any uncommitted batch before outer finalization.
    with closing(conn):
        started = database.utcnow()
        stats = {}

        for source, path in DETAIL_FETCHERS.items():
            # 用 getattr：cmd_run 会复用本函数，而 `run` 子命令并没有 --limit 参数。
            # 直接取 args.limit 会让每天定时的 run 在 fetch 之后崩掉。
            pending = database.jobs_missing_detail(conn, source, getattr(args, "limit", None))
            if not pending:
                continue
            fetch_detail = _resolve(path)
            sleep = float((config.get("seek") or {}).get("detail_sleep", 0.25))
            ok = 0
            print(f"[details] {source}: 队列 {len(pending)} 条", flush=True)
            for row in pending:
                text = fetch_detail(row["source_id"])
                if text:
                    database.set_description(conn, row["uid"], text)
                    ok += 1
                time.sleep(sleep)
                if ok and ok % 25 == 0:
                    conn.commit()
                    print(f"  ... 已补 {ok}/{len(pending)}", flush=True)
            conn.commit()
            stats[source] = {"queued": len(pending), "fetched": ok}
            if hasattr(args, "_run_counts"):
                args._run_counts["details_fetched"] += ok
                args._run_details_incomplete |= ok < len(pending)
            print(f"  {source}: 成功 {ok}/{len(pending)}")

        # jobspy 来源没有独立详情步骤，提示一下未落正文的数量
        for source in ("indeed", "linkedin"):
            n = len(database.jobs_missing_detail(conn, source))
            if n:
                print(f"  [提示] {source} 有 {n} 条无正文"
                      f"（正文随列表返回；若需正文请开启对应 fetch_description 后重抓）")

        database.log_run(conn, "details", started, stats)
        conn.commit()
        return 0


# --------------------------------------------------------------------------
# 离线命令（永不联网）
# --------------------------------------------------------------------------

def _sync_skills_transactional(conn, cfg: dict) -> dict:
    """与 skills.sync 相同的抽取与重建，但**不提交**。

    analyze 需要把 skills 与 decisions 放进同一个事务，不能在中途
    留下半套结果。这里复用 skills.Matcher（纯抽取函数）与 db 的批量写入，
    语义和 skills.sync 完全一致，只是把 commit 交给调用方。
    """
    match = skills_mod.Matcher(cfg)
    rows = conn.execute("SELECT uid, title, teaser, description FROM jobs").fetchall()
    database.clear_skills(conn)
    pairs: list = []
    counts: dict = {}
    for row in rows:
        text = " \n ".join(str(p) for p in
                           (row["title"], row["teaser"], row["description"]) if p)
        for skill in match.extract(text):
            pairs.append((row["uid"], skill))
            counts[skill] = counts.get(skill, 0) + 1
    database.add_skills(conn, pairs)
    return counts


def _derive_scores_and_dedupe(conn, rules: dict, jobs) -> tuple:
    """薪资解析 -> 打分 -> 去重。不提交；由调用方与决策同事务提交。"""
    from . import salary as salary_mod

    sal_cfg = rules.get("salary") or {}
    sal_bands = sal_cfg.get("bands") or []
    sal_shift = int(sal_cfg.get("contract_shift", 0))
    sal_enabled = bool(sal_cfg.get("enabled", True))
    parsed_salaries = {}
    for job in jobs:
        found = (salary_mod.extract(job["salary"], job["description"], sal_bands, sal_shift)
                 if sal_enabled else None)
        parsed_salaries[job["uid"]] = found
        database.replace_salary(conn, job["uid"], found)

    # 注意：所有职位都会被打分，包括被硬排除的。这样可以看到"如果没有被过滤，
    # 它会得多少分"，便于判断规则是否过严。因此单独查 scores 会看到已被排除的
    # 职位，必须 join decisions 并过滤 is_excluded 才是实际入选的集合。
    scored = 0
    for job in jobs:
        result = score_job(job, rules, parsed_salaries.get(job["uid"]))
        database.replace_score(
            conn, job["uid"], result["tier"], result["tier_score"],
            result["level_score"], result["adjust_score"], result["total_score"],
            result["reasons"], result["rules_version"])
        scored += 1

    rows = [{"uid": j["uid"], "company": j["company"],
             "title": j["title"], "location": j["location"]} for j in jobs]
    dedupe_cfg = rules.get("dedupe") or {}
    groups = assign_groups(
        rows,
        include_city=bool(dedupe_cfg.get("include_city", False)),
        title_similarity=float(dedupe_cfg.get("title_similarity", 0.90)),
    )
    by_uid = {j["uid"]: j for j in jobs}
    for uid, group in groups.items():
        database.set_dedupe(conn, uid, dedupe_key(by_uid[uid]), group)
    return parsed_salaries, scored, groups


def _rule_profile_stats(resolution: dict) -> dict:
    """runs.stats 用的规则开关摘要：profile / 来源 / 有效开关 / 显式覆盖。

    只含规则 id 与 bool，不含 regex、desc 或 JD 原文。
    """
    return {
        "profile": resolution["profile"],
        "config_source": resolution["config_source"],
        "exclude": dict(resolution["exclude"]),
        "overrides": dict(resolution["overrides"]),
        "explicit_exclude": dict(resolution["explicit_exclude"]),
        "explicit_overrides": dict(resolution["explicit_overrides"]),
    }


def _analyze_legacy(args, config: dict, rules: dict, selected_rules: dict,
                    resolution: dict, conn, started: str) -> int:
    """既有路径：折叠文本 + filters.evaluate（按 profile 选出的规则）。

    资格判定用 selected_rules；评分 / 薪资 / 去重 / skills 继续用完整原
    rules，不因资格开关改变其它业务。决策字段与评分结果不变。
    """
    jobs = database.all_jobs(conn)
    if not jobs:
        print("库内无数据，先跑 fetch", file=sys.stderr)
        return 1

    database.clear_decisions_and_scores(conn)

    excluded = 0
    for job in jobs:
        text = build_job_text(job)
        decision = evaluate(text, selected_rules)
        database.replace_decision(
            conn, job["uid"], decision["is_excluded"], decision["rule_id"],
            decision["rule_kind"], decision["matched_text"], decision["rules_version"])
        excluded += decision["is_excluded"]

    _, scored, groups = _derive_scores_and_dedupe(conn, rules, jobs)
    skill_counts = _sync_skills_transactional(conn, skills_mod.load_skills(args.skills))
    from .config_edit import public_config_snapshot

    database.log_run(conn, "analyze", started,
                     {"jobs": len(jobs), "excluded": excluded, "scored": scored,
                      "skills": len(skill_counts), "filtering_mode": "legacy",
                      "rule_profile": _rule_profile_stats(resolution),
                      "config_snapshot": public_config_snapshot(config, rules)})
    conn.commit()

    disabled_ex = sum(1 for on in resolution["exclude"].values() if not on)
    disabled_ov = sum(1 for on in resolution["overrides"].values() if not on)
    print(f"[analyze] 规则版本 {rules.get('version')} ｜ 模式 legacy ｜ "
          f"profile {resolution['profile'] or '缺省'}（关闭 exclude {disabled_ex} / "
          f"override {disabled_ov}）｜ 共 {len(jobs)} 条，排除 {excluded} 条，"
          f"打分 {scored} 条，去重组 {len(set(groups.values()))} 个，"
          f"技能词 {len(skill_counts)} 个 ｜ 软件规则开关，非法律资格判断")
    return 0


def cmd_analyze(args) -> int:
    # Full runs already own the database lock through report and finalization.
    if getattr(args, "_pipeline_active", False):
        return _cmd_analyze(args)
    raw, error = _read_config_root_checked(getattr(args, "config", None))
    if error is not None:
        print(f"[analyze] {error}", file=sys.stderr)
        return 1
    config = raw if isinstance(raw, dict) else {}
    try:
        resolve_filtering(config)
    except ValueError as exc:
        print(f"[analyze] filtering 配置无效：{exc}", file=sys.stderr)
        return 1
    lock = run_state.acquire_lock(config)
    if lock is None:
        print("[analyze] another update is running", file=sys.stderr)
        return 4
    previous_snapshot = getattr(args, "_config_snapshot", None)
    try:
        # Re-read under ownership: a settings save may have completed since preflight.
        raw, error = _read_config_root_checked(getattr(args, "config", None))
        if error is not None:
            print(f"[analyze] {error}", file=sys.stderr)
            return 1
        fresh = raw if isinstance(raw, dict) else {}
        if db_path(config).resolve() != db_path(fresh).resolve():
            print("[analyze] database changed; retry with the current configuration", file=sys.stderr)
            return 1
        args._config_snapshot = fresh
        return _cmd_analyze(args)
    finally:
        args._config_snapshot = previous_snapshot
        lock.close()


def _cmd_analyze(args) -> int:
    """过滤 + 打分 + 去重（不联网）。

    过滤模式只允许 legacy：既有折叠文本 + filters.evaluate，行为不变。
    缺省（没有 filtering 段）严格为 legacy；显式 profile_v1 或任何非法
    mode 在 connect 之前安全失败，绝不静默回退。

    规则开关（legacy_rule_profile）在这里解析一次：legacy 缺省全开；
    citizen / PR 等 preset 只按开关表关闭现有规则；非法 profile / 开关 /
    未知 id / null 映射在 connect 之前安全失败。资格判定用选出的规则，
    评分 / 薪资 / 去重 / skills 仍用完整原规则。

    配置 / 规则预检在任何 connect 之前完成；逐岗位评估或之后的
    score / skills / 持久化失败都会回滚本轮派生结果。labels / label_events
    永不触碰。
    """
    # ---- 预检（不碰 DB）----
    if getattr(args, "_config_snapshot", None) is not None:
        raw, error = copy.deepcopy(args._config_snapshot), None
    else:
        raw, error = _read_config_root_checked(getattr(args, "config", None))
    if error is not None:
        print(f"[analyze] {error}", file=sys.stderr)
        return 1
    config = raw if isinstance(raw, dict) else {}
    try:
        resolve_filtering(config)
    except ValueError as exc:
        print(f"[analyze] filtering 配置无效：{exc}", file=sys.stderr)
        return 1
    try:
        rules = load_rules(getattr(args, "rules", None))
    except FileNotFoundError:
        print("[analyze] 规则文件不存在。", file=sys.stderr)
        return 1
    except yaml.YAMLError:
        print("[analyze] 规则文件不是有效 YAML。", file=sys.stderr)
        return 1
    except (OSError, UnicodeDecodeError):
        print("[analyze] 规则文件读取失败。", file=sys.stderr)
        return 1

    # 规则开关只在每次分析预备一次（不逐岗位 deepcopy / 读文件）；非法配置
    # 在 connect 之前安全失败。
    try:
        resolution = legacy_rule_profiles.resolve_rule_profile(config, rules)
        selected_rules = legacy_rule_profiles.select_profile_rules(config, rules)
    except legacy_rule_profiles.RuleProfileConfigError as exc:
        print(f"[analyze] legacy_rule_profile 配置无效：{exc}", file=sys.stderr)
        return 1

    started = database.utcnow()
    conn = database.connect(db_path(config))
    try:
        return _analyze_legacy(args, config, rules, selected_rules, resolution,
                               conn, started)
    except RunTimeout:
        # 全流程超时必须向上传播（见 jobs.RunTimeout 的说明），不能被回滚层吞掉。
        conn.rollback()
        raise
    except Exception as exc:            # noqa: BLE001
        conn.rollback()
        print(f"[analyze] 分析失败，本轮派生结果已回滚（{type(exc).__name__}）。",
              file=sys.stderr)
        return 1
    finally:
        conn.close()


def cmd_report(args) -> int:
    from .report import write_audit_summary, write_report

    config, conn = _open(args)
    try:
        # CLI analysis uses --skills (or the default vocabulary), so report labels
        # must use that same vocabulary rather than a panel-only config path.
        config["skills_path"] = args.skills
        config["rules_path"] = (
            getattr(args, "rules", None)
            or str(Path(__file__).resolve().parent.parent / "rules.yaml"))
        if not (config.get("report")):
            config["report"] = {}
        info = write_report(conn, config)
        summary = write_audit_summary(conn, Path(info["out_dir"]))
        print(f"[report] 精选 {info['top']} 条 / 通过过滤 {info['kept']} 条 / 排除 {info['excluded']} 条")
        print(f"         输出目录 {info['out_dir']}")
        print(f"         排除汇总 {summary}")
        return 0
    finally:
        conn.close()


def cmd_audit(args) -> int:
    config, conn = _open(args)
    limit = args.limit or 25
    rows = conn.execute(
        """SELECT d.rule_id, d.matched_text, j.title, j.company, j.source, j.url
           FROM decisions d JOIN jobs j ON j.uid = d.uid
           WHERE d.is_excluded = 1
           ORDER BY d.rule_id, j.title LIMIT ?""", (limit,)).fetchall()

    print("被硬排除的 JD（抽样，用于人工确认有没有误杀）：\n")
    for row in rows:
        print(f"  [{row['rule_id']}] {row['title']} — {row['company']} ({row['source']})")
        print(f"      命中: {row['matched_text']}")
        print(f"      {row['url']}")
    print(f"\n共排除 {database.counts(conn)['excluded']} 条。完整清单见 out/*-excluded.csv")
    return 0


def cmd_status(args) -> int:
    _, conn = _open(args)
    stats = database.counts(conn)
    print("库内统计：")
    for key, value in stats.items():
        print(f"  {key:<18} {value}")
    print("\n按来源：")
    for row in conn.execute("SELECT source, COUNT(*) n, "
                            "SUM(CASE WHEN description IS NOT NULL AND TRIM(description)<>'' "
                            "THEN 1 ELSE 0 END) with_desc "
                            "FROM jobs GROUP BY source ORDER BY n DESC"):
        print(f"  {row['source']:<10} {row['n']:>5} 条，其中 {row['with_desc']} 条有正文")
    print("\n按层级：")
    for row in conn.execute("SELECT tier, COUNT(*) n, MAX(total_score) best "
                            "FROM scores GROUP BY tier ORDER BY tier"):
        print(f"  {row['tier']:<4} {row['n']:>5} 条，最高分 {row['best']}")
    return 0


def cmd_eligibility_profile(args) -> int:
    """只读查看 eligibility profile 的解析结果。不联网、不碰 DB。

    严格校验原始 YAML 根类型（空文档 / 非 dict 根都拒绝，不回退 485）。
    只调用 read_config_root + get_eligibility；不调用 _open /
    database.connect，不创建或读取数据库，也不写配置。成功时 stdout
    只输出一段 JSON；错误时输出安全 stderr 并返回非零，不回显原配置值。
    """
    try:
        raw = read_config_root(getattr(args, "config", None))
    except FileNotFoundError:
        print("配置文件不存在。", file=sys.stderr)
        return 1
    except PermissionError:
        print("配置文件不可读。", file=sys.stderr)
        return 1
    except IsADirectoryError:
        print("配置路径不是文件。", file=sys.stderr)
        return 1
    except UnicodeDecodeError:
        print("配置文件不是 UTF-8 文本。", file=sys.stderr)
        return 1
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = ""
        if mark is not None:
            where = f"（第 {mark.line + 1} 行，第 {mark.column + 1} 列）"
        print(f"配置文件不是有效 YAML{where}。", file=sys.stderr)
        return 1
    except OSError:
        print("配置文件读取失败。", file=sys.stderr)
        return 1
    try:
        profile = get_eligibility(raw)
    except ValueError as exc:
        print(f"eligibility 配置无效：{exc}", file=sys.stderr)
        return 1
    print(json.dumps(profile, ensure_ascii=False, indent=2))
    return 0


def _read_config_root_checked(path: str):
    """读取严格配置根；失败返回 (None, 安全错误信息)，成功返回 (raw, None)。

    与 eligibility-profile 使用相同的 PROFILE-006 错误分类：根类型 / schema
    由调用方的 get_eligibility 判定。错误信息只给类别（可选行列号），
    不回显路径、配置值、未知键或 YAML 原文。
    """
    try:
        return read_config_root(path), None
    except FileNotFoundError:
        return None, "配置文件不存在。"
    except PermissionError:
        return None, "配置文件不可读。"
    except IsADirectoryError:
        return None, "配置路径不是文件。"
    except UnicodeDecodeError:
        return None, "配置文件不是 UTF-8 文本。"
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = ""
        if mark is not None:
            where = f"（第 {mark.line + 1} 行，第 {mark.column + 1} 列）"
        return None, f"配置文件不是有效 YAML{where}。"
    except OSError:
        return None, "配置文件读取失败。"


def cmd_label(args) -> int:
    """人工标注：能不能投 / 想不想投 / 投没投。不联网。

    标注是项目里唯一不可再生的数据，因此 analyze 永不清它。
    `--export-fixtures` 会把标注回流成回归用例 —— 这是「漏杀没有出口」的出口。
    """
    from . import labels as labels_mod

    config, conn = _open(args)
    out_dir = Path((config.get("report") or {}).get("out_dir", "out"))
    if not out_dir.is_absolute():
        out_dir = Path(__file__).resolve().parent.parent / out_dir

    if args.find:
        rows = labels_mod.find_jobs(conn, args.find, args.limit or 40)
        if not rows:
            print(f"没有匹配 {args.find!r} 的岗位")
            return 1
        print(f"匹配 {len(rows)} 条：\n")
        for row in rows:
            state = "已排除" if row["is_excluded"] else f"分数 {row['total_score']}"
            mark = "  [已标注]" if row["labeled"] else ""
            print(f"  {row['uid']:<24} {str(row['title'])[:46]:<46} "
                  f"{str(row['company'])[:20]:<20} {state}{mark}")
        print("\n下一步：label <uid> --eligibility eligible|ineligible "
              "--reason \"原因\" [--action saved] [--priority]")
        return 0

    if args.list:
        rows = conn.execute(
            """SELECT l.*, j.title, j.company FROM labels l
               LEFT JOIN jobs j ON j.uid = l.uid
               ORDER BY l.labeled_at DESC LIMIT ?""", (args.limit or 25,)).fetchall()
        if not rows:
            print("还没有任何标注")
            return 0
        print(f"最近 {len(rows)} 条标注：\n")
        for row in rows:
            bits = [f"{k}={labels_mod.display(k, row[k])}"
                    for k in ("eligibility", "interest", "action") if row[k]]
            bits += [f"priority={row['priority']}",
                     f"processed={bool(row['action'])}"]
            print(f"  {str(row['title'])[:44]:<44} {str(row['company'])[:18]:<18} "
                  f"{' '.join(bits)}")
        print("\n" + "　".join(f"{k} {v}" for k, v in labels_mod.summary(conn).items()))
        return 0

    if args.tidy_notes:
        info = labels_mod.tidy_notes(conn, apply=args.apply)
        print("[label] 备注整理 —— 预演（不写库）" if not args.apply
              else "[label] 备注整理 —— 已写入")
        for uid, title, old, new in info["planned"]:
            print(f"  {str(title)[:38]:<38} {old!r}")
            print(f"  {'':<38}   -> {new!r}")
        if info["freeform"]:
            print(f"\n  词表覆盖不了、原样保留 {len(info['freeform'])} 条"
                  f"（要不要补词表由你定）：")
            for old in dict.fromkeys(info["freeform"]):
                print(f"    {old!r}")
        print(f"\n  合计 可整理 {len(info['planned'])} 条"
              f"／已是规范标签 {len(info['clean'])} 条"
              f"（去重后 {len(set(info['clean']))} 种）"
              f"／保留原样 {len(info['freeform'])} 条")
        if not args.apply and info["planned"]:
            print("  确认无误后加 --apply 写入（每次改写都会进 label_events，原文不会丢）")
        return 0

    if args.export:
        info = labels_mod.export(conn, out_dir)
        print(f"[label] 导出 {info['count']} 条标注 -> {info['csv']} / {info['md']}")
        return 0

    if args.export_fixtures:
        info = labels_mod.export_fixtures(conn)
        print(f"[label] 回流回归用例 {info['written']} 条 -> {info['path']}")
        # 跳过的原因要分开报 —— 「正文里找不到资格句」意味着这类漏杀
        # 永远进不了测试，是这个闭环里最需要被看见的一种失败。
        if info["no_snippet"]:
            print(f"         {len(info['no_snippet'])} 条正文里找不到资格句，无法生成用例"
                  f"（这些漏杀不会进回归测试）：")
            for title in info["no_snippet"][:10]:
                print(f"           - {title}")
        if info["unowned"]:
            print(f"         {len(info['unowned'])} 条标了不可投、但原因不是硬过滤该管的类别"
                  f"（已按降权处理，故意不生成「必须排除」用例）：")
            for title, cue in info["unowned"][:10]:
                print(f"           - [{cue}] {title}")
        print("         跑 `.venv/bin/python tools/test_citizenship_rules.py` 验证")
        return 0

    if args.history:
        rows = labels_mod.history(conn, args.uid, args.limit or 60)
        if not rows:
            print("还没有任何标注变更记录")
            return 0
        print(f"最近 {len(rows)} 条标注变更：\n")
        for row in rows:
            old = row["old_value"] or "—"
            new = row["new_value"] if row["new_value"] is not None else "（清空）"
            flag = "   <<< 清空" if row["old_value"] and row["new_value"] is None else ""
            print(f"  {str(row['at'])[:19]}  [{row['source']}] "
                  f"{str(row['title'])[:32]:<32} {row['field']}: {old} → {new}{flag}")
        return 0

    if not args.uid:
        print("需要 uid，或用 --find / --list / --export / --export-fixtures", file=sys.stderr)
        return 1

    if args.clear:
        print(f"已清除 {args.uid} 的标注" if labels_mod.clear_label(conn, args.uid)
              else f"{args.uid} 本来就没有标注")
        return 0

    fields = {"eligibility": args.eligibility, "ineligible_reason": args.reason,
              "interest": args.interest, "action": args.action, "note": args.note,
              "priority": getattr(args, "priority", None)}
    fields = {k: v for k, v in fields.items() if v is not None}
    if getattr(args, "clear_action", False):
        fields["action"] = None
    if getattr(args, "clear_eligibility", False):
        fields["eligibility"] = None
        fields["ineligible_reason"] = None
    if not fields:
        print("没有要写入的字段。可用 --eligibility/--reason/--interest/--action/--note/--priority/--no-priority/--clear-action/--clear-eligibility",
              file=sys.stderr)
        return 1

    job = conn.execute("SELECT title, company FROM jobs WHERE uid = ?", (args.uid,)).fetchone()
    if job is None:
        print(f"库里没有 uid={args.uid}", file=sys.stderr)
        return 1

    rules = load_rules(getattr(args, "rules", None))
    try:
        result = labels_mod.set_label(conn, args.uid, rules_version=rules.get("version"),
                                      source="cli", **fields)
    except ValueError as exc:
        print(f"标注失败：{exc}", file=sys.stderr)
        return 1

    print(f"已标注 {job['title']} — {job['company']}")
    for key in ("eligibility", "ineligible_reason", "interest", "action", "priority", "note"):
        if result.get(key) is not None:
            value = result[key] if key == "priority" else labels_mod.display(key, result[key])
            print(f"    {key} = {value}")
    print(f"    processed = {bool(result.get('action'))}")

    # 最有价值的信号：人工判断"不能投"，但规则并没排除它 —— 这是漏杀
    decision = conn.execute("SELECT is_excluded FROM decisions WHERE uid = ?",
                            (args.uid,)).fetchone()
    if result.get("eligibility") == "ineligible" and decision and not decision["is_excluded"]:
        print("\n  ⚠ 这条**并没有**被硬过滤排除 —— 即漏杀。")
        print("    建议：label --export-fixtures 把它变成回归用例，")
        print("    然后改 rules.yaml 让同类写法命中。")
    return 0


def cmd_skills(args) -> int:
    """技能词表的查看与结构性编辑。不联网。

    写入只动补充层 skills.local.yaml，手写带注释的 skills.yaml 保持只读。
    删除、改名、合并这类操作走这里；面板只负责「读到一半顺手加个词」。
    """
    config, conn = _open(args)
    path = getattr(args, "skills", None)
    action = getattr(args, "skills_command", None)

    if action in (None, "list"):
        merged = skills_mod.load_skills(path)
        local = skills_mod.load_local(path)
        seed_names = {str(e.get("name"))
                      for e in (skills_mod.load_base(path).get("skills") or [])}
        counts = database.skill_counts(conn)
        rows, dead = [], 0
        for entry in merged.get("skills") or []:
            name = str(entry.get("name"))
            n = counts.get(name, 0)
            if n == 0:
                dead += 1
            if getattr(args, "dead", False) and n:
                continue
            flags = []
            if entry.get("ambiguous"):
                flags.append("歧义")
            if entry.get("not"):
                flags.append("not")
            rows.append((n, "seed" if name in seed_names else "local", name,
                         "、".join(str(a) for a in (entry.get("aliases") or [])),
                         " ".join(flags)))
        rows.sort(key=lambda r: (-r[0], r[2]))
        limit = getattr(args, "limit", None)
        shown = rows[:limit] if limit else rows
        print(f"技能词表：{len(rows)} 条　0 命中 {dead} 条"
              + (f"　（只显示前 {len(shown)}）" if limit and len(shown) < len(rows) else "")
              + "\n")
        for n, origin, name, aliases, flags in shown:
            print(f"  {n:>4}  {origin:<5} {name:<22} {aliases}"
                  + (f"  [{flags}]" if flags else ""))
        if local.get("remove"):
            print(f"\n  抑制（补充层 remove）：{'、'.join(local['remove'])}")
        conn.close()
        return 0

    try:
        if action == "add":
            info = skills_mod.edit_and_sync(conn, path, args.name, "add")
        elif action == "alias":
            info = skills_mod.edit_and_sync(conn, path, args.token, "alias", args.to)
        elif action == "remove":
            info = skills_mod.edit_and_sync(conn, path, args.token, "remove")
        else:
            print("skills 的子命令：list / add / alias / remove", file=sys.stderr)
            return 1
        counts = info.pop("counts")
        note = (f"全量重算，技能 {len(counts)} 个" if counts is not None
                else f"增量重算，更新 {info['docs']} 篇")
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(f"[skills] 编辑失败：{exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    print(f"[skills] {action} {info} -> {note}")
    return 0


def cmd_panel(args) -> int:
    """启动本地网页面板。只监听内网地址，不联网抓取。"""
    from .panel import serve

    config, conn = _open(args)
    conn.close()
    panel = config.get("panel") or {}
    if args.host:
        panel["host"] = args.host
    if args.port:
        panel["port"] = args.port
    config["panel"] = panel
    panel_skills = args.skills or config.get("skills_path")
    if panel_skills:
        # Panel edits and the worker must target the same vocabulary even when
        # launched outside the repository (skills paths use launch cwd).
        config["skills_path"] = str(Path(panel_skills).resolve())
    previous_break = None
    if os.name == 'nt' and hasattr(signal, 'SIGBREAK'):
        def on_break(_signum, _frame):
            raise KeyboardInterrupt
        previous_break = signal.signal(signal.SIGBREAK, on_break)
    try:
        serve(config, config_path=args.config, rules_path=args.rules, skills_path=args.skills)
    finally:
        if previous_break is not None:
            signal.signal(signal.SIGBREAK, previous_break)
    return 0


def _on_alarm(signum, frame):        # noqa: ARG001
    raise RunTimeout()


def _acquire_run_lock(config=None):
    return run_state.acquire_lock(config or load_config())


def _write_run_result(args, state, stage=None, error=None, *, persist=True):
    """Durable bounded progress, including runs launched outside the panel."""
    result = {
        "task_id": args._task_id, "kind": args._task_kind,
        "trigger": getattr(args, "trigger", None) or "cli",
        "state": state, "stage": stage,
        "started_at": args._run_started,
        "finished_at": None if state == "running" else database.utcnow(),
        "counts": dict(args._run_counts),
        "failed_sources": sorted(args._run_failed_sources), "error": error,
        # A full partial run still completed analysis/report on existing jobs.
        "refresh_available": state == "success" or
                             (state == "partial" and (args._task_kind == "run" or
                              bool(args._run_counts.get("seen")))),
    }
    # Build the terminal summary without exposing it before DB logging succeeds.
    if not persist:
        return result
    # Rejected competing attempts must never replace the current execution.
    if state != "busy":
        run_state.publish(args._config_snapshot, result, apply_generation=False)
    target = getattr(args, "result_file", None)
    if target:
        run_state.atomic_json(target, result)
    return result


def _execute_pipeline(args, *, fetch_only=False):
    initial = load_config(getattr(args, "config", None))
    if os.name == 'nt' and not getattr(args, 'windows_worker', False):
        from .windows_pipeline import execute
        return execute(args, fetch_only=fetch_only)
    inherited = getattr(args, "run_lock_fd", None)
    raw_handle = getattr(args, 'run_lock_handle', None)
    if os.name == 'nt' and raw_handle is not None:
        from .windows_process import inherited_lock
        lock = inherited_lock(raw_handle)
        inherited = raw_handle
    else:
        lock = os.fdopen(os.dup(inherited), "a+") if inherited is not None else _acquire_run_lock(initial)
    previous_snapshot = getattr(args, "_config_snapshot", None)
    args._run_started = getattr(args, "result_started_at", None) or database.utcnow()
    args._run_counts = {"seen": 0, "inserted": 0, "updated": 0, "details_fetched": 0}
    args._run_failed_sources = set()
    args._run_details_incomplete = False
    args._task_id = getattr(args, "task_id", None) or uuid.uuid4().hex
    args._task_kind = "fetch" if fetch_only else "run"
    args._config_snapshot = initial
    if lock is None:
        _write_run_result(args, "busy", error="run_busy")
        args._config_snapshot = previous_snapshot
        print("[run] another update is running", file=sys.stderr)
        return 4
    args._pipeline_active = True
    limit = int(getattr(args, "timeout", None) or 0)
    stage = None
    state, error = "error", "run_failed"
    previous_alarm = None
    previous_term = None
    try:
        fresh_config = load_config(getattr(args, "config", None))
        if db_path(initial).resolve() != db_path(fresh_config).resolve():
            raise ValueError("database changed while waiting for configuration lock")
        args._config_snapshot = fresh_config
        if threading.current_thread() is threading.main_thread():
            previous_term = signal.signal(signal.SIGTERM, lambda signum, _frame: (_ for _ in ()).throw(SystemExit(128 + signum)))
        if limit and os.name != 'nt':
            previous_alarm = signal.signal(signal.SIGALRM, _on_alarm)
            signal.setitimer(signal.ITIMER_REAL, limit)
        steps = (("fetch", _cmd_fetch),) if fetch_only else (("fetch", cmd_fetch), ("details", cmd_details), ("analyze", cmd_analyze), ("report", cmd_report))
        for stage, step in steps:
            _write_run_result(args, "running", stage)
            code = step(args)
            if code:
                error = "no_sources" if stage == "fetch" else "run_failed"
                return code
        if args._run_failed_sources:
            state, error = "partial", "source_failed"
        elif args._run_details_incomplete:
            state, error = "partial", "details_incomplete"
        else:
            state, error = "success", None
        return 0
    except RunTimeout:
        state, error = "timeout", "run_timeout"
        return 3
    except (KeyboardInterrupt, SystemExit):
        state, error = "interrupted", "run_interrupted"
        raise
    finally:
        if limit and os.name != 'nt':
            signal.setitimer(signal.ITIMER_REAL, 0)
            if previous_alarm is not None:
                signal.signal(signal.SIGALRM, previous_alarm)
        if previous_term is not None:
            signal.signal(signal.SIGTERM, previous_term)
        try:
            if getattr(args, "_config_snapshot", None) is not None:
                result = _write_run_result(args, state, stage, error, persist=False)
                if not fetch_only:
                    _, conn = _open(args)
                    database.log_run(conn, "run", args._run_started, result)
                    conn.commit()
                    conn.close()
                # Only finalization after all DB logging can make this terminal
                # recoverable. Inherited workers defer generation to the panel
                # watcher, or to a later reader which proves the flock is free.
                result['completion_ready'] = True
                if getattr(args, "result_file", None):
                    run_state.atomic_json(args.result_file, result)
                run_state.publish(args._config_snapshot, result,
                                  apply_generation=inherited is None)
        except Exception:
            # A failed finalization must not leave a recoverable success marker.
            try:
                _write_run_result(args, "error", stage, "storage_unavailable")
            except Exception:
                pass
            raise
        finally:
            args._config_snapshot = previous_snapshot
            args._pipeline_active = False
            lock.close()


def cmd_fetch(args) -> int:
    if getattr(args, "_pipeline_active", False):
        return _cmd_fetch(args)
    return _execute_pipeline(args, fetch_only=True)


def cmd_run(args) -> int:
    return _execute_pipeline(args)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="jobs", description="澳洲求职聚合流水线")
    parser.add_argument("--config", help="config.yaml 路径")
    parser.add_argument("--rules", help="rules.yaml 路径")
    parser.add_argument("--skills", help="skills.yaml 路径（技能词表）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_fetch = sub.add_parser("fetch", help="抓列表（联网）")
    p_fetch.add_argument("--only", choices=["seek", "indeed", "linkedin"],
                         help="只抓某一个来源（LinkedIn 慢，便于单独重跑）")
    p_fetch.set_defaults(func=cmd_fetch)

    p_details = sub.add_parser("details", help="补详情队列（联网）")
    p_details.add_argument("--limit", type=int, default=None)
    p_details.set_defaults(func=cmd_details)

    sub.add_parser("analyze", help="过滤+打分+去重（不联网）").set_defaults(func=cmd_analyze)
    sub.add_parser("report", help="输出报告（不联网）").set_defaults(func=cmd_report)

    p_audit = sub.add_parser("audit", help="抽样查看被排除的 JD（不联网）")
    p_audit.add_argument("--limit", type=int, default=25)
    p_audit.set_defaults(func=cmd_audit)

    sub.add_parser("status", help="库内统计").set_defaults(func=cmd_status)

    sub.add_parser(
        "eligibility-profile",
        help="只读查看 eligibility profile 解析结果（不联网、不碰 DB）",
    ).set_defaults(func=cmd_eligibility_profile)

    p_label = sub.add_parser("label", help="人工标注（不联网）")
    p_label.add_argument("uid", nargs="?", help="要标注的 uid，占位符 seek:<job-id>；实际 uid 请用 --find 从自己的库查询")
    eligibility = p_label.add_mutually_exclusive_group()
    eligibility.add_argument("--clear-eligibility", action="store_true", help="清除人工资格纠正")
    eligibility.add_argument("--eligibility", choices=["eligible", "ineligible"],
                         help="能不能投（ineligible 会被回流成回归用例）")
    p_label.add_argument("--reason", help="不可投的原因，尤其用于记录漏杀")
    p_label.add_argument("--interest", choices=["want", "maybe", "no"], help="想不想投")
    action = p_label.add_mutually_exclusive_group()
    action.add_argument("--action", choices=["saved", "applied", "skipped"],
                        help="进度：saved=想投，applied=已投，skipped=跳过")
    action.add_argument("--clear-action", action="store_true", help="取消主状态，保留其他人工记录")
    priority = p_label.add_mutually_exclusive_group()
    priority.add_argument("--priority", dest="priority", action="store_true", default=None,
                          help="标记优先待投（筛选同时要求 saved）")
    priority.add_argument("--no-priority", dest="priority", action="store_false",
                          help="取消优先标记")
    p_label.add_argument("--note", help="自由备注")
    p_label.add_argument("--clear", action="store_true", help="兼容旧行为：清除整行人工标注")
    p_label.add_argument("--find", help="按标题/公司查找 uid")
    p_label.add_argument("--list", action="store_true", help="列出最近标注")
    p_label.add_argument("--history", action="store_true",
                         help="标注变更流水（含被清空的记录，可据此恢复）")
    p_label.add_argument("--export", action="store_true", help="导出标注清单到 out/")
    p_label.add_argument("--tidy-notes", action="store_true",
                         help="把历史备注整理成规范标签（默认只预演）")
    p_label.add_argument("--apply", action="store_true",
                         help="配合 --tidy-notes 真正写入")
    p_label.add_argument("--export-fixtures", action="store_true",
                         help="把标注回流成回归用例")
    p_label.add_argument("--limit", type=int, default=None)
    p_label.set_defaults(func=cmd_label)

    p_skills = sub.add_parser("skills", help="技能词表：查看 / 增删词条（不联网）")
    skills_sub = p_skills.add_subparsers(dest="skills_command")
    sk_list = skills_sub.add_parser("list", help="列出合并后的词表与命中数")
    sk_list.add_argument("--dead", action="store_true", help="只看 0 命中的词")
    sk_list.add_argument("--limit", type=int, default=None, help="只显示前 N 条")
    sk_add = skills_sub.add_parser("add", help="新增一个技能词")
    sk_add.add_argument("name")
    sk_alias = skills_sub.add_parser("alias", help="把一个词设为已有技能的别名")
    sk_alias.add_argument("token")
    sk_alias.add_argument("--to", required=True, help="目标技能名")
    sk_remove = skills_sub.add_parser("remove", help="删除一个技能或别名")
    sk_remove.add_argument("token")
    p_skills.set_defaults(func=cmd_skills)

    p_run = sub.add_parser("run", help="全流程（联网）")
    p_run.add_argument("--timeout", type=int, default=1800,
                       help="全局超时秒数，默认 1800（0 = 不限）。"
                            "无人值守时防卡死：launchd 不会重入，卡一次就永远不再抓")
    p_run.add_argument("--result-file", help=argparse.SUPPRESS)
    p_run.add_argument("--result-started-at", help=argparse.SUPPRESS)
    p_run.add_argument("--run-lock-fd", type=int, help=argparse.SUPPRESS)
    p_run.add_argument("--task-id", help=argparse.SUPPRESS)
    p_run.add_argument("--trigger", choices=("manual", "scheduled", "cli", "unknown"), default="cli", help="execution source")
    p_run.set_defaults(func=cmd_run)

    p_panel = sub.add_parser("panel", help="启动本地网页面板（不联网）")
    p_panel.add_argument("--host", help="覆盖 config.yaml 的 panel.host")
    p_panel.add_argument("--port", type=int, help="覆盖 config.yaml 的 panel.port")
    p_panel.set_defaults(func=cmd_panel)

    for pipeline_parser in (p_fetch, p_run):
        pipeline_parser.add_argument('--run-lock-handle', type=int, help=argparse.SUPPRESS)
        pipeline_parser.add_argument('--windows-worker', action='store_true', help=argparse.SUPPRESS)
    for option in ('result-file', 'result-started-at', 'task-id'):
        p_fetch.add_argument('--' + option, help=argparse.SUPPRESS)
    p_fetch.add_argument('--trigger', choices=('manual', 'scheduled', 'cli', 'unknown'),
                         default='cli', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
