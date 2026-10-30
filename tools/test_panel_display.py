#!/usr/bin/env python3
"""v1.2.2 panel display layer: evidence moves, it is never removed.

The card/detail rework (one salary chip, tier merged into the score badge,
score adjustments only in the detail's collapsed breakdown, progress text,
Display menu, update confirmation, separate "Eligibility & rules" settings
section) is presentation only. ``render.tags_for`` still feeds the Markdown
report, the CSV and the search text, so this test pins its output on a
synthetic row and checks that every signal hidden from the card is still
searchable and still shown in the detail.

All data is hand-written and fictional; databases live in temp directories.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jobs import db as database                   # noqa: E402
from jobs import i18n, panel, panel_data, panel_views, run_state  # noqa: E402
from jobs import labels as labels_mod              # noqa: E402
from jobs.render import tag_texts, tags_for       # noqa: E402

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


TODAY = dt.date.today().isoformat()
REASONS = ["tier:T1", "level:'Junior' +3", "experience:3y -2", "location.out_of_state:'Perth' -1"]


def synthetic_row(**extra) -> dict:
    row = {
        "uid": "seek:synthetic_x", "title": "Synthetic Junior Developer", "company": "Example Co",
        "location": "Melbourne VIC", "source": "seek", "listing_date": TODAY,
        "salary": "$28 – $30 per hour", "salary_annual_min": 55328, "salary_annual_max": 59280,
        "salary_currency": "AUD", "salary_basis": "annualized", "salary_origin": "description",
        "reasons": json.dumps(REASONS), "skills": ["Python", "Selenium", "AWS", "Docker", "Git"],
        "total_score": 6, "tier": "T1", "eligibility_cue": "驾照",
        "eligibility_snippet": "Must hold a driver's licence.",
        "description": "Fictional role. Must hold a driver's licence.",
        "url": "https://example.invalid/job/synthetic_x",
        "_label": {"action": "applied", "eligibility": "eligible"},
    }
    row.update(extra)
    return row


def visible(html: str) -> str:
    html = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S)
    return re.sub(r"<[^>]+>", " ", html)


def test_shared_tags_unchanged() -> None:
    print("\n== render.tags_for（报告 / CSV / 搜索共用）口径不变 ==")
    expected = [("tier", "T1"), ("skill", "Python"), ("skill", "Selenium"), ("skill", "AWS"),
                ("skill", "Docker"), ("skill", "Git"), ("level", "Junior +3"),
                ("level", "经验 3+ 年 -2"), ("adjust", "location.out_of_state -1"),
                ("salary", "≈A$55k–59k/年（正文）"), ("raw", "原文 $28 – $30 per hour")]
    check("tags_for 输出逐项一致", tags_for(synthetic_row()) == expected, str(tags_for(synthetic_row())))
    check("md 报告用的 tag_texts 一致", tag_texts(synthetic_row()) == [t for _, t in expected])
    text = panel_data._search_text(synthetic_row(), {"note": "优先投递；"})
    for token in ("junior +3", "location.out_of_state -1", "经验 3+ 年 -2", "原文 $28 – $30 per hour",
                  "≈a$55k–59k/年（正文）", "t1"):
        check(f"搜索文本仍含卡片上隐藏的「{token}」", token in text)


def test_card() -> None:
    print("\n== 卡片：三类强调 + 一枚薪资 + 进度文字 ==")
    for lang in ("zh", "en"):
        html = panel._card(synthetic_row(), None, lang)
        tags = re.findall(r'class="tag ([a-z ]+)"', html)
        check(f"{lang}：标签只剩状态/线索/薪资三类", set(tags) <= {"age", "bad", "warn", "salary", "raw"}, str(tags))
        check(f"{lang}：只有一枚薪资", tags.count("salary") + tags.count("raw") == 1, str(tags))
        check(f"{lang}：≈ 与（正文）来源标记保留",
              ("≈A$55k–59k/年（正文）" if lang == "zh" else "≈A$55k–59k/yr (from description)") in html)
        check(f"{lang}：原文在悬停里（完整）", "$28 – $30 per hour" in html and 'title="' in html)
        check(f"{lang}：卡片不显示加减分", "+3" not in visible(html) and "out_of_state" not in html
              and "-2" not in visible(html))
        check(f"{lang}：层级并入分数徽章", re.search(r'class="score"[^>]*><span class="tier">T1</span>.*?<b>6</b>', html) is not None)
        check(f"{lang}：全部技能交给单行宽度排版，没有固定数量省略",
              ">Python · Selenium · AWS · Docker · Git</span>" in html)
        check(f"{lang}：技能悬停列出全部", "Python, Selenium, AWS, Docker, Git" in html)
        prog = i18n.value_label(lang, "action", "applied")
        check(f"{lang}：进度文字来自 _label，未加星标时星标隐藏",
              f'data-prog-uid="seek:synthetic_x" title="{i18n.t(lang, "field.action")}' in html
              and f">{prog}</span>" in html and 'class="prog good"' in html
              and re.search(r'class="card-star"[^>]* hidden>', html) is not None)
    for action in (None, "saved", "applied", "skipped"):
        for since in (None, "2026-10-30T00:00:00"):
            html = panel._card(synthetic_row(_label={"action": action}), since, "zh")
            check(f"卡片亮度 action={action} since={bool(since)}",
                  ('class="card done"' in html) == bool(since and action in ("applied", "skipped")))
    for score in (7, -3, 12):
        lone = panel._card(synthetic_row(total_score=score, reasons=json.dumps(REASONS[1:])), None, "zh")
        badge = re.search(r'class="score"[^>]*>(.*?)</span>', lone)
        check(f"无层级分数 {score}：徽章只含分数", badge is not None and badge.group(1) == f"<b>{score}</b>",
              badge.group(1) if badge else lone)
    # The later .score rule switches the badge to inline-flex, so text-align no longer
    # centres a lone score; the flex rule itself must centre its content.
    css = (ROOT / "jobs" / "panel_assets" / "panel.css").read_text(encoding="utf-8")
    flex = [r for r in re.findall(r'(?m)^\.score\{([^}]*)\}', css) if "inline-flex" in r]
    check("无层级时分数居中：inline-flex 徽章声明 justify-content:center",
          bool(flex) and all("justify-content:center" in r for r in flex), str(flex))
    bare = panel._card(synthetic_row(_label={}, salary_annual_min=None, salary_annual_max=None), None, "zh")
    check("未标进度：进度标签隐藏", re.search(r'class="prog [^"]*"[^>]* hidden>', bare) is not None)
    check("解析不出折算值：原文本身就是那一枚薪资", bare.count('class="tag raw"') == 1
          and "原文 $28 – $30 per hour" in bare)
    long_raw = "$" + "9" * 60
    capped = panel._card(synthetic_row(salary=long_raw, salary_annual_min=None, salary_annual_max=None), None, "zh")
    check("卡片原文沿用报告的截断，悬停给完整原文", "…" in visible(capped) and long_raw in capped)


def test_layout_clearances() -> None:
    print("\n== 布局留白：芯片、设置焦点环、地点接缝 ==")
    css = (ROOT / "jobs" / "panel_assets" / "panel.css").read_text(encoding="utf-8")
    base = re.search(r'(?m)^\.chips\{[^}]*padding:0 20px 10px\}', css)
    selecting = re.search(r'(?m)^body\.selecting \.chips\{padding-top:(\d+)px;padding-bottom:(\d+)px\}', css)
    check("多选时芯片与操作栏分开，且芯片行总高不变（列表不跳）",
          base is not None and selecting is not None and int(selecting[1]) > 0
          and int(selecting[1]) + int(selecting[2]) == 10, str(selecting))
    ring = re.search(r'(?m)^:focus-visible\{outline:(\d+)px solid var\(--accent\);outline-offset:(\d+)px\}', css)
    pane = re.search(r'(?m)^\.set-pane\{[^}]*margin-left:-(\d+)px;padding:0 (\d+)px 20px\}', css)
    check("设置窗格左右留白容得下外扩焦点环，内容左缘不变",
          ring is not None and pane is not None and pane[1] == pane[2]
          and int(pane[2]) >= int(ring[1]) + int(ring[2]), str(pane))
    check("地点框无右边框：生效/报错时由应用按钮补同色接缝",
          ".geo-control .ctl{border-right-width:0}" in css
          and ".geo-control .ctl.on+.geo-apply{border-left-color:var(--accent)}" in css
          and ".geo-control.invalid .ctl+.geo-apply{border-left-color:var(--bad-line)}" in css
          and css.index(".geo-control .ctl.on+.geo-apply") < css.index(".geo-control.invalid .ctl+.geo-apply"))


def test_detail() -> None:
    print("\n== 详情：完整原文 + 「为什么是 …」折叠区 ==")
    for lang in ("zh", "en"):
        html = panel._detail_html(synthetic_row(), lang)
        why = html[html.index('<details class="why">'):]
        check(f"{lang}：计分明细默认收起", '<details class="why">' in html and "<details class=\"why\" open" not in html)
        check(f"{lang}：标题含层级与总分",
              i18n.t(lang, "detail.why", tier="T1", score=6) in why)
        for name, delta in (("Junior", "+3"), ("location.out_of_state", "-1")):
            check(f"{lang}：加减分移到折叠区 {name} {delta}",
                  f"<span>{name}</span><b>{delta}</b>" in why)
        exp = "经验 3+ 年" if lang == "zh" else "Experience 3+ yrs"
        check(f"{lang}：经验信号也在折叠区", f"<span>{exp}</span><b>-2</b>" in why)
        head = html[:html.index('<section class="decide"')]
        check(f"{lang}：标签行不再有加减分", "+3" not in visible(head) and "out_of_state" not in head)
        check(f"{lang}：完整原文与折算值并排一次", visible(head).count("$28 – $30 per hour") == 1)
    plain = panel._detail_html(synthetic_row(reasons="[]", tier=None), "zh")
    check("没有计分信号：折叠区说明没有加减分", i18n.t("zh", "detail.why_empty") in plain
          and i18n.t("zh", "detail.why_plain", score=6) in plain)
    excluded = panel._detail_html(synthetic_row(
        is_excluded=1, rule_id="synthetic.authorization,synthetic.location,<fake>",
        matched_text="Synthetic matching phrase"), "en")
    rule = excluded[excluded.index('<div class="rule">'):excluded.index('<section class="decide"')]
    check("排除规则保留逗号和完整单词",
          "synthetic.authorization,synthetic.location," in re.sub(r"<[^>]+>", "", rule))
    check("规则之间允许换行，规则内容仍转义", rule.count("<wbr>") == 2
          and "&lt;fake&gt;" in rule and "<fake>" not in rule)
    legacy = panel._detail_html(synthetic_row(
        _label={"interest": "maybe", "eligibility": "unsure"}), "zh")
    check("详情不渲染旧兴趣、旧待定或历史块",
          all(text not in legacy for text in
              ("旧版记录", "兴趣：也许", "资格：待定", "有旧记录", 'class="history"', 'class="q-hist"')))


def make_db(d: str):
    conn = database.connect(os.path.join(d, "display.db"))
    database.upsert_job(conn, {
        "source": "seek", "source_id": "a", "title": "Synthetic Data Engineer", "company": "Example Co",
        "location": "Melbourne VIC", "listing_date": TODAY, "description": "Python and AWS.",
        "url": "https://example.invalid/job/synthetic_a"})
    database.replace_decision(conn, "seek:a", 0)
    database.replace_score(conn, "seek:a", "T1", 3, 0, 0, 5, "[]", 5)
    conn.commit()
    return conn


def choice_order(html: str, attr: str) -> list[str]:
    return re.findall(attr + r'="([a-z]+)"', html)


def test_page_controls() -> None:
    print("\n== 顶栏：显示菜单、更新岗位确认、状态行 ==")
    with tempfile.TemporaryDirectory() as d:
        conn = make_db(d)
        labels_mod.set_label(conn, "seek:a", source="panel", action="saved")
        conn.commit()
        for lang in ("zh", "en"):
            page = panel.render_page(conn, {}, "all", 0, 40, None, {}, lang, None, "auto")
            menu = page[page.index('id="display-pop"'):page.index('class="display-more"')]
            drawer = page[page.index('id="drawer"'):]
            settings = panel.render_settings(conn, {}, lang, None, "display", "auto")
            for where, html in (("顶栏菜单", menu), ("手机抽屉", drawer), ("设置页", settings)):
                check(f"{lang}/{where}：语言顺序 跟随/中文/English",
                      choice_order(html, "data-lang-choice")[:3] == ["auto", "zh", "en"],
                      str(choice_order(html, "data-lang-choice")))
                check(f"{lang}/{where}：外观顺序 跟随/浅色/深色",
                      choice_order(html, "data-theme-choice")[:3] == ["auto", "light", "dark"],
                      str(choice_order(html, "data-theme-choice")))
            check(f"{lang}：跟随浏览器时「跟随」为选中",
                  'class="on" data-lang-choice="auto" aria-pressed="true"' in menu)
            check(f"{lang}：顶栏不再有独立语言/主题按钮",
                  "lang-btn" not in page and "theme-btn" not in page and 'id="display-btn"' in page)
            check(f"{lang}：设置·显示分区不在顶栏重复菜单", 'id="display-btn"' not in settings)
            check(f"{lang}：更新按钮带文字且只有一个",
                  page.count('id="update-start"') == 1
                  and f"<span>{i18n.t(lang, 'js.update_title')}</span></button>" in page)
            check(f"{lang}：主页有更新确认窗，默认隐藏",
                  '<div class="modal update-confirm" id="update-confirm" hidden>' in page
                  and i18n.t(lang, "update.confirm_ok") in page)
            check(f"{lang}：抽屉里有放更新按钮的位置", 'id="drawer-update-slot"' in page)
            state = re.search(r'<span class="card-state"><span class="card-star"[^>]*>★</span>'
                              r'<span class="prog want"[^>]*>([^<]+)</span></span>', page)
            check(f"{lang}：卡片星标在前，想投文字在后",
                  state is not None and state.group(1) == i18n.value_label(lang, "action", "saved"))
            fetch = panel.render_settings(conn, {}, lang, None, "fetch", lang)
            check(f"{lang}：设置·抓取也走同一个确认窗",
                  fetch.count('id="update-start"') == 1 and 'id="update-confirm"' in fetch
                  and 'id="update-task"' in fetch)
            check(f"{lang}：「标注状态」默认文字与其他筛选一致",
                  i18n.t(lang, "labeled.any") == i18n.t(lang, "filter.any"))
        conn.close()

    print("\n== 状态行：结果未知保持未知 ==")
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    for lang in ("zh", "en"):
        fact = {"state": "unknown", "finished_at": now, "task_id": "legacy-2", "trigger": "unknown"}
        with patch.object(run_state, "latest", return_value=fact):
            note = panel_views._last_run_note(None, lang, {})
        check(f"{lang}：旧记录显示「结果未记录」", i18n.t(lang, "run.state_unknown") in note)
        check(f"{lang}：不写成成功、不露任务 ID",
              i18n.t(lang, "js.update_success") not in note and "legacy-2" not in note)
        check(f"{lang}：分隔符完整", " · " + i18n.t(lang, "run.state_unknown") in note)
    check("未知状态的完整说明也不是成功",
          all(w not in i18n.t(lang, "js.update_unknown").lower()
              for lang in ("zh", "en") for w in ("成功", "完成", "success", "complete")))


def test_settings_sections() -> None:
    print("\n== 设置：资格与规则独立分区 ==")
    with tempfile.TemporaryDirectory() as d:
        conn = make_db(d)
        for lang in ("zh", "en"):
            rules = panel.render_settings(conn, {}, lang, None, "rules", lang)
            fetch = panel.render_settings(conn, {}, lang, None, "fetch", lang)
            check(f"{lang}：分区导航含「资格与规则」且排第一",
                  re.findall(r'href="/settings\?section=([a-z]+)"', rules)[:4] == ["rules", "fetch", "skills", "display"])
            check(f"{lang}：资格与规则只显示规则组", 'id="config-editor" data-config-groups="rules"' in rules)
            check(f"{lang}：说明保存不会重算", i18n.t(lang, "set.rules_hint") in rules)
            groups = re.search(r'id="config-editor" data-config-groups="([^"]+)"', fetch).group(1).split()
            check(f"{lang}：抓取与更新不再含规则组", "rules" not in groups and "queries" in groups, str(groups))
        for section in (None, "unknown", ""):
            page = (panel.render_settings(conn, {}, "zh") if section is None else
                    panel.render_settings(conn, {}, "zh", None, section, "zh"))
            check(f"未指定/未知分区 {section!r}：进入第一分页规则 Profile",
                  'id="config-editor" data-config-groups="rules"' in page)
        check("显式技术栈链接仍进入技术栈", 'aria-current="page">' + i18n.t("zh", "set.skills")
              in panel.render_settings(conn, {}, "zh", None, "skills", "zh"))
        conn.close()


def main() -> int:
    test_shared_tags_unchanged()
    test_card()
    test_layout_clearances()
    test_detail()
    test_page_controls()
    test_settings_sections()
    if FAILS:
        print(f"\n失败 {len(FAILS)} 项：" + "；".join(FAILS))
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
