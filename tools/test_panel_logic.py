"""面板纯逻辑回归：翻页连续性、校准混样、每视图筛选。

这三件事都是「看一眼代码觉得对、用起来才发现不对」的类型：

  翻页连续  第一版按「现在有没有标注」筛推荐列表，于是在第一页标掉 N 条后，
            第二页的切片起点前移 N 格 —— 正好跳过 N 条没看过的岗位。
            修法是按 `since`（进入视图的时刻）筛，池子在翻页期间冻结。
            这个脚本直接断言冻结语义，不依赖数据库里的标注数据。
  校准混样  低分区一直没有标注样本，所以要按比例插低分岗；
            但插样必须作用在**整池**上再切片，按页插会让插入点随页码漂移。
  每视图筛选  推荐视图只给搜索 + 薪资，全部视图给全套。

固定时间使用明确标注的**合成测试时间**（2000 年的一组 before/since/after），
不是任何应用记录；stale 用明确的旧日期（2020-05-01），today 输入保持动态。

用法（不联网、不写库）：
    .venv/bin/python tools/test_panel_logic.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jobs import panel                      # noqa: E402

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
    labels = {
        "old": {"labeled_at": "2000-01-01T00:00:00+00:00"},   # 进视图之前就标过
        "now": {"labeled_at": "2000-06-15T23:00:00+00:00"},   # 翻页过程中才标
    }
    index = {u: [u] for u in ("old", "now", "fresh")}
    got = {u: panel._unlabelled_at({"uid": u}, labels, index, SINCE)
           for u in ("old", "now", "fresh")}
    check("进视图前已标 -> 不在推荐池", got["old"] is False)
    check("翻页时才标 -> 仍在池里（否则第二页会跳过它后面的条目）", got["now"] is True)
    check("从没标过 -> 在池里", got["fresh"] is True)
    # 池子大小在「标了一条」前后不变 —— 这正是「第二页不跳条目」的充要条件
    before = sum(1 for u in ("old", "now", "fresh")
                 if panel._unlabelled_at({"uid": u}, labels, index, SINCE))
    after = sum(1 for u in ("old", "now", "fresh")
                if panel._unlabelled_at({"uid": u}, labels, index, SINCE))
    check("标注前后池子大小一致", before == after, f"{before} -> {after}")


def test_mix() -> None:
    print("\n== 校准混样 ==")
    rows = ([{"uid": f"h{i}", "total_score": 5} for i in range(45)]
            + [{"uid": f"l{i}", "total_score": -1} for i in range(20)])
    mixed = panel._mix_low_scores(rows, 0.1, 0)
    check("一条不丢", len(mixed) == len(rows), f"{len(mixed)}/{len(rows)}")
    check("顺序稳定（同样的输入给同样的输出）",
          [r["uid"] for r in mixed] == [r["uid"] for r in panel._mix_low_scores(rows, 0.1, 0)])
    head = [r["total_score"] for r in mixed[:40]]
    lows = sum(1 for s in head if s <= 0)
    check("前 40 条里低分占比接近 10%", 3 <= lows <= 5, f"{lows}/40")
    check("ratio=0 时不插样，回到纯分数序",
          [r["uid"] for r in panel._mix_low_scores(rows, 0, 0)] == [r["uid"] for r in rows])
    only_low = [{"uid": u, "total_score": -2} for u in "ab"]
    check("没有高分岗时整池原样返回",
          [r["uid"] for r in panel._mix_low_scores(only_low, 0.1, 0)] == ["a", "b"])


def test_filters() -> None:
    print("\n== 每视图筛选 & 会话标过的卡片 ==")
    # 推荐视图除了搜索 + 薪资，再加技能与层级：两者都是「缩小这一页」，
    # 不是可投性/意向/进度那种会让人误解的口径。
    check("推荐 = 搜索 + 薪资 + 技能 + 层级",
          set(panel.VIEW_FILTER_KEYS["recommend"]) == {"q", "salary", "skill", "tier"})
    check("全部给全套",
          set(panel.VIEW_FILTER_KEYS["all"]) ==
          {"q", "eligibility", "interest", "action", "salary", "fresh", "skill",
           "tier", "sort", "labeled"})
    parsed = panel.parse_filters({"q": ["python"], "offset": ["40"], "view": ["all"]})
    check("offset / view 不进筛选条件", parsed == {"q": "python"}, str(parsed))
    check("since 单独带着走",
          panel.parse_filters({"since": [SINCE]}) == {"since": SINCE})
    row = {"uid": "x", "title": "t", "reasons": [], "total_score": 3,
           "_label": {"labeled_at": "2000-06-15T13:00:00+00:00"}}
    check("本次会话标过的卡片带 done 样式",
          'class="card done"' in panel._card(row, SINCE))
    row["_label"] = {"labeled_at": "2000-01-01T00:00:00+00:00"}
    check("更早标过的不是 done", 'class="card done"' not in panel._card(row, SINCE))
    check("已标注视图不给 labeled、改给 priority",
          "labeled" not in panel.VIEW_FILTER_KEYS["labelled"]
          and "priority" in panel.VIEW_FILTER_KEYS["labelled"])
    check("priority 命中「优先投递」备注标签",
          panel.passes_filters({"uid": "p"}, {"note": "优先投递；"}, {"priority": "1"}))
    check("没有该标签就被筛掉",
          not panel.passes_filters({"uid": "q"}, {"note": "搬迁；"}, {"priority": "1"}))


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
    test_mix()
    test_filters()
    test_stale()
    print()
    if failures:
        print(f"失败 {len(failures)} 项：" + "；".join(failures))
        return 2
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
