"""面板纯逻辑回归：翻页连续性、推荐顺序、每视图筛选。

这三件事都是「看一眼代码觉得对、用起来才发现不对」的类型：

  翻页连续  第一版按「现在有没有标注」筛推荐列表，于是在第一页标掉 N 条后，
            第二页的切片起点前移 N 格 —— 正好跳过 N 条没看过的岗位。
            修法是按 `since`（进入视图的时刻）筛，池子在翻页期间冻结。
            这个脚本用临时合成数据库断言 action 事件冻结语义。
  推荐顺序  推荐视图与其他视图同为分数优先；旧配置里的低分混样键被忽略。
  每视图筛选  推荐视图只给搜索 + 薪资，全部视图给全套。

固定时间使用明确标注的**合成测试时间**（2000 年的一组 before/since/after），
不是任何应用记录；stale 用明确的旧日期（2020-05-01），today 输入保持动态。

用法（不联网，只写临时合成数据库）：
    .venv/bin/python tools/test_panel_logic.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jobs import panel, panel_data, labels as labels_mod, db as database, cli  # noqa: E402
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from unittest.mock import patch
import tempfile

# 合成测试时间，不是应用记录。
SINCE = "2000-06-15T12:00:00+00:00"
failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{('  ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


def test_freeze() -> None:
    """推荐池子按「进入视图那一刻」判定，翻页期间不因标注而变。"""
    print("== 翻页连续性（推荐视图的池子冻结）==")
    with tempfile.TemporaryDirectory() as directory:
        conn = database.connect(Path(directory) / "synthetic.db")
        names = ("old", "fresh", "note", "priority", "interest", "eligibility", "peer")
        for i, name in enumerate(names):
            uid = f"synthetic:{name}"
            database.upsert_job(conn, {
                "source": "synthetic", "source_id": name, "title": name,
                "company": "Synthetic Example", "dedupe_group": "old" if name == "peer" else name})
            conn.execute("UPDATE jobs SET dedupe_group = ? WHERE uid = ?",
                         ("old" if name == "peer" else name, uid))
            database.replace_decision(conn, uid, 0)
            database.replace_score(conn, uid, "T1", 3, 0, 0, 20 - i, "[]", 20 - i)
        conn.commit()
        with patch.object(database, "utcnow", return_value=SINCE):
            labels_mod.set_label(conn, "synthetic:old", action="saved")
            labels_mod.set_label(conn, "synthetic:note", note="优先投递；")
            labels_mod.set_label(conn, "synthetic:priority", priority=True)
            labels_mod.set_label(conn, "synthetic:interest", interest="want")
            labels_mod.set_label(conn, "synthetic:eligibility", eligibility="eligible")
            marker = panel_data._resolve_since(conn, "recommend", {})
            check("水位 token 在 URL 解析后完整保留",
                  panel.parse_filters({"since": [marker]}) == {"since": marker})
            before, total = panel._query(conn, {}, "recommend", 0, 40, {}, marker)
            expected = [row["uid"] for row in before]
            check("只有 action 算已处理，备注等仍在推荐", set(expected) == {
                "synthetic:fresh", "synthetic:note", "synthetic:priority",
                "synthetic:interest", "synthetic:eligibility"}, str(expected))
            check("人工记录视图与推荐可以重叠",
                  {row["uid"] for row in panel._query(conn, {}, "labelled", 0, 40)[0]}
                  & set(expected) == {"synthetic:note", "synthetic:priority",
                                      "synthetic:interest", "synthetic:eligibility"})
            labels_mod.set_label(conn, "synthetic:fresh", action="saved")
            labels_mod.set_label(conn, "synthetic:fresh", action="applied")
            labels_mod.set_label(conn, "synthetic:fresh", action=None)
            labels_mod.set_label(conn, "synthetic:fresh", action="skipped")
            labels_mod.set_label(conn, "synthetic:old", action="applied")
            labels_mod.set_label(conn, "synthetic:old", action=None)
            labels_mod.set_label(conn, "synthetic:note", note="搬迁；")
            after, total_after = panel._query(conn, {}, "recommend", 0, 40, {}, marker)
            check("同秒 action 切换/取消、备注修改不改变冻结成员与顺序",
                  [row["uid"] for row in after] == expected and total_after == total)
            pages = [row["uid"] for offset in range(0, total, 2)
                     for row in panel._query(conn, {}, "recommend", offset, 2, {}, marker)[0]]
            check("跨页无跳项或重复", pages == expected, str(pages))
            snapshot = labels_mod.action_states_at(conn, marker)
            check("兼容入口消费冻结快照", not panel._unlabelled_at(
                {"uid": "synthetic:old", "dedupe_group": "old"}, snapshot,
                {"old": ["synthetic:old", "synthetic:peer"]}, marker))
            new_marker = labels_mod.freeze_marker(conn)
            current, _ = panel._query(conn, {}, "recommend", 0, 40, {}, new_marker)
            current_uids = {row["uid"] for row in current}
            check("新会话采用清空/新 action 的当前状态",
                  "synthetic:old" in current_uids and "synthetic:fresh" not in current_uids)
            decorated = {row["uid"]: row for row in after}
            check("装饰暴露独立 priority 与 action 处理状态",
                  decorated["synthetic:priority"]["priority"] == 1
                  and decorated["synthetic:priority"]["label_priority"] == 1
                  and decorated["synthetic:priority"]["processed"] is False
                  and decorated["synthetic:fresh"]["processed"] is True)
            check("装饰保留机器决策时间及资格解释",
                  "decided_at" in after[0] and after[0]["qualification"] ==
                  labels_mod.qualification(after[0], after[0]["_label"]))
        conn.close()


def test_recommend_order() -> None:
    """推荐视图按分数排序，不再混入低分岗；旧配置里的混样键被忽略。"""
    print("\n== 推荐顺序（不混低分）==")
    old_keys = {"panel": {"recommend": {"low_ratio": 0.1, "low_max_score": 0}}}
    with tempfile.TemporaryDirectory() as directory:
        conn = database.connect(Path(directory) / "synthetic.db")
        scores = [5 + i % 7 for i in range(45)] + [-1 - i % 3 for i in range(20)]
        for i, score in enumerate(scores):
            name = f"job{i:02d}"
            database.upsert_job(conn, {"source": "synthetic", "source_id": name,
                                       "title": name, "company": "Synthetic Example"})
            database.replace_decision(conn, f"synthetic:{name}", 0)
            database.replace_score(conn, f"synthetic:{name}", "T1", 3, 0, 0, score, "[]", score)
        conn.commit()
        whole, total = panel._query(conn, old_keys, "recommend", 0, 100, {}, SINCE)
        values = [row["total_score"] for row in whole]
        check("推荐整池即分数降序", total == 65 and values == sorted(values, reverse=True),
              f"{total} {values[:12]}")
        check("第 10、20 条不是低分岗", values[9] > 0 and values[19] > 0)
        check("旧混样键与无配置结果相同",
              [r["uid"] for r in whole] ==
              [r["uid"] for r in panel._query(conn, {}, "recommend", 0, 100, {}, SINCE)[0]])
        pages = [row["uid"] for offset in range(0, 65, 10)
                 for row in panel._query(conn, old_keys, "recommend", offset, 10, {}, SINCE)[0]]
        check("分页连续：无重复、无遗漏", pages == [r["uid"] for r in whole])
        conn.close()


def test_filters() -> None:
    print("\n== 每视图筛选 & 会话标过的卡片 ==")
    # 推荐视图除了搜索 + 薪资，再加技能与层级：两者都是「缩小这一页」，
    # 不是可投性/意向/进度那种会让人误解的口径。
    check("推荐 = 搜索 + 薪资 + 技能 + 层级，不含地点搜索",
          set(panel.VIEW_FILTER_KEYS["recommend"]) == {"q", "salary", "skill", "tier"})
    check("全部给全套",
          set(panel.VIEW_FILTER_KEYS["all"]) ==
          {"q", "eligibility", "interest", "action", "salary", "fresh", "skill",
           "tier", "sort", "labeled", "geo"})
    parsed = panel.parse_filters({"q": ["python"], "offset": ["40"], "view": ["all"]})
    check("offset / view 不进筛选条件", parsed == {"q": "python"}, str(parsed))
    check("since 单独带着走",
          panel.parse_filters({"since": [SINCE]}) == {"since": SINCE})
    row = {"uid": "synthetic:card", "title": "Synthetic", "reasons": [],
           "total_score": 3, "processed": True,
           "_label": {"action": "saved", "labeled_at": "2000-01-01T00:00:00+00:00"}}
    check("已有 action 的卡片算已处理，不依赖最近修改时间",
          'class="card done"' in panel._card(row, SINCE))
    row["processed"] = False
    row["_label"] = {"note": "优先投递；", "priority": True,
                     "labeled_at": "2000-06-15T13:00:00+00:00"}
    check("会话内只改备注/priority 的卡片不算已处理",
          'class="card done"' not in panel._card(row, SINCE))
    check("已标注视图不给 labeled、改给 priority",
          "labeled" not in panel.VIEW_FILTER_KEYS["labelled"]
          and "priority" in panel.VIEW_FILTER_KEYS["labelled"])
    for label, expected in (
        ({"action": "saved", "priority": True}, True),
        ({"action": "saved", "priority": 1, "note": "搬迁；"}, True),
        ({"action": "saved", "priority": False}, False),
        ({"action": "saved", "priority": None, "note": "优先投递；"}, False),
        ({"action": "applied", "priority": True}, False),
        ({"action": "skipped", "priority": True}, False),
        ({"priority": True, "interest": "want"}, False),
    ):
        check(f"优先待投筛选 {label}",
              panel.passes_filters({"uid": "synthetic:p"}, label, {"priority": "1"}) is expected)
    for eligibility, expected in ((None, True), ("unsure", True),
                                  ("eligible", False), ("ineligible", False)):
        check(f"未纠正筛选与当前人工覆盖一致：{eligibility}",
              panel.passes_filters({}, {"eligibility": eligibility},
                                   {"eligibility": "none"}) is expected)
    check("旧待定链接仍可查历史值",
          panel.passes_filters({}, {"eligibility": "unsure"}, {"eligibility": "unsure"}))
    check("主状态与旧兴趣的 none 仍只匹配空值",
          not panel.passes_filters({}, {"action": "saved"}, {"action": "none"})
          and not panel.passes_filters({}, {"interest": "maybe"}, {"interest": "none"})
          and panel.passes_filters({}, {}, {"action": "none", "interest": "none"}))


def test_cli() -> None:
    print("\n== CLI 独立 priority 与局部清空 ==")
    with tempfile.TemporaryDirectory() as directory:
        conn = database.connect(Path(directory) / "synthetic-cli.db")
        uid = "synthetic:cli"
        database.upsert_job(conn, {"source": "synthetic", "source_id": "cli",
                                  "title": "Synthetic CLI", "company": "Example"})
        conn.commit()
        output = StringIO()
        with patch.object(cli, "_open", return_value=({}, conn)), \
             patch.object(cli, "load_rules", return_value={"version": 1}), \
             redirect_stdout(output), redirect_stderr(output):
            check_result = cli.main(["label", uid, "--action", "saved", "--priority",
                                     "--note", "优先投递；", "--eligibility", "eligible"])
            initial = labels_mod.get_labels(conn)[uid]
            check_result2 = cli.main(["label", uid, "--no-priority", "--clear-action",
                                      "--clear-eligibility"])
            cleared = labels_mod.get_labels(conn)[uid]
        check("CLI saved + priority 写入", check_result == 0 and
              initial["action"] == "saved" and initial["priority"] == 1)
        check("CLI 取消只清指定字段并保留备注", check_result2 == 0 and
              cleared["action"] is None and cleared["eligibility"] is None and
              cleared["priority"] == 0 and cleared["note"] == "优先投递；")
        check("CLI 打印包含想投、priority 和进度", "想投" in output.getvalue() and
              "priority =" in output.getvalue() and "processed = False" in output.getvalue())
        for arguments in (["--priority", "--no-priority"],
                          ["--action", "saved", "--clear-action"],
                          ["--eligibility", "eligible", "--clear-eligibility"],
                          ["--eligibility", "unsure"]):
            with redirect_stderr(StringIO()):
                try:
                    cli.main(["label", uid, *arguments])
                except SystemExit as exc:
                    rejected = exc.code == 2
                else:
                    rejected = False
            check(f"CLI 拒绝冲突或旧 unsure 写入 {arguments}", rejected)
        conn.close()


def test_stale() -> None:
    """挂出 >30 天：不进推荐、有置顶标红 tag、可被「只看 30 天内」筛掉。"""
    print("\n== 过期岗位（挂出 > 30 天）==")
    import datetime as dt
    import os
    import tempfile

    from jobs import db as database

    old = {"uid": "old", "listing_date": "2020-05-01"}
    new = {"uid": "new", "listing_date": dt.date.today().isoformat()}
    check("老岗判为过期", panel.is_stale(old))
    check("今天不算过期", not panel.is_stale(new))
    check("无日期不算过期", not panel.is_stale({"listing_date": None}))
    tag = panel.age_tag(old)
    check("过期卡片有「挂出 N 天」tag", tag is not None and "挂出" in tag[1])
    check("tag 形状是 (kind, text)", tag is not None and tag[0] == "age")
    check("新卡片没有该 tag", panel.age_tag(new) is None)

    with tempfile.TemporaryDirectory() as d:
        conn = database.connect(os.path.join(d, "t.db"))
        for sid, ld in (("old", "2020-05-01"), ("new", dt.date.today().isoformat())):
            uid = f"t:{sid}"
            database.upsert_job(conn, {"source": "t", "source_id": sid,
                                       "title": f"T {sid}", "listing_date": ld})
            database.replace_decision(conn, uid, 0)
            database.replace_score(conn, uid, "T1", 3, 0, 0, 5, "[]", 5)
        conn.commit()

        page, _ = panel._query(conn, {}, "recommend", 0, 40, {},
                               since=SINCE)
        uids = {r["uid"] for r in page}
        check("过期岗位不进推荐", "t:old" not in uids and "t:new" in uids, str(uids))

        page2, _ = panel._query(conn, {}, "all", 0, 40, {"fresh": "1"}, None)
        uids2 = {r["uid"] for r in page2}
        check("「只看 30 天内」把过期岗挡掉",
              "t:old" not in uids2 and "t:new" in uids2, str(uids2))
        conn.close()


def main() -> int:
    test_freeze()
    test_recommend_order()
    test_filters()
    test_cli()
    test_stale()
    print()
    if failures:
        print(f"失败 {len(failures)} 项：" + "；".join(failures))
        return 2
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
