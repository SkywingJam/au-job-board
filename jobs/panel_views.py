"""面板的 HTML 渲染：列表卡片、详情、筛选栏、主页面与设置页。

样式与脚本放在 panel_assets/ 下的静态文件里，渲染时**内联**进页面 ——
仍然是单个响应、零外链、离线可用。所有界面文案走 i18n.py（中文 / English）；
岗位正文、公司、备注、技能名等数据原样输出，不翻译。

布局约定：
  - 顶栏一行：名称 · 视图 · 语言 · 外观 · 设置；第二行是搜索 + 常驻筛选，
    其余筛选收进「更多筛选」，收起时把生效的条件列成可移除的小标签。
  - 视图说明条：一句话说明当前视图口径 + 相关统计 + 上次抓取。
  - 卡片固定三个圆点（可投性 · 兴趣 · 投递进度），未标注是空心。
  - 详情按步骤：资格线索（原文）→ 做标记（带 ? 说明）→ 备注。
  - 竖屏（右栏放不下，见 panel.js 的 fitLayout）：汉堡菜单 + 底部筛选抽屉。
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from . import db as database
from . import i18n
from . import labels as labels_mod
from . import report as report_mod
from . import skills as skills_mod
from .panel_data import (
    FILTER_KEYS, FRESH_KEY, NONE_TOKEN, PRIORITY_KEY,
    QUERY_KEY, SINCE_KEY, SKILL_KEY, SKILL_PICK_TOP, SORT_KEYS, TIER_KEY, TIER_LABELS,
    ALL_FILTER_KEYS, VIEW_FILTER_KEYS, VIEWS,
    _link, _query, _resolve_since, age_days, AGE_WARN_DAYS, skill_tree,
)
from .render import render_jd_html, tags_for

ASSET_DIR = Path(__file__).with_name("panel_assets")
CURRENT = ' aria-current="page"'
MARK = "\x00"   # 占位：先转义文案再把它换成 <b> 计数

# 标注值的语义色（只用于显示）
TONE = {"eligible": "good", "ineligible": "bad", "want": "good", "no": "bad",
        "applied": "good", "skipped": "bad"}

# 每个视图哪些筛选常驻在工具栏，其余进「更多筛选」。**允许哪些筛选**仍以
# panel_data.VIEW_FILTER_KEYS 为准，这里只决定摆放位置（见 filter_layout）。
PRIMARY_FILTERS = {
    "recommend": (SKILL_KEY, TIER_KEY, "salary"),
    "labelled": (PRIORITY_KEY, SKILL_KEY),
}
DEFAULT_PRIMARY = (SKILL_KEY, TIER_KEY)
LABEL_GROUP_KEYS = ("eligibility", "interest", "action", "labeled")
TOOLBAR_FIXED = (QUERY_KEY, "sort")          # 搜索框与排序有自己的固定位置

ICON_SEARCH = ('<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
               'stroke-width="1.8" stroke-linecap="round" aria-hidden="true">'
               '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/></svg>')
ICON_FILTER = ('<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
               'stroke-width="1.9" stroke-linecap="round" aria-hidden="true">'
               '<path d="M4 6h16M7 12h10M10 18h4"/></svg>')
ICON_MENU = ('<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
             'stroke-width="2" stroke-linecap="round" aria-hidden="true">'
             '<path d="M4 7h16M4 12h16M4 17h16"/></svg>')
ICON_CLOSE = ('<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
              'stroke-width="2" stroke-linecap="round" aria-hidden="true">'
              '<path d="M6 6l12 12M18 6L6 18"/></svg>')
ICON_GLOBE = ('<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
              'stroke-width="1.7" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/>'
              '<path d="M3 12h18M12 3c2.6 2.8 2.6 15.2 0 18M12 3c-2.6 2.8-2.6 15.2 0 18"/></svg>')
ICON_INFO = ('<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
             'stroke-width="1.8" stroke-linecap="round" aria-hidden="true">'
             '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/></svg>')
# 主题按钮同时放太阳和月亮，CSS 按当前生效的主题只显示一个（见 panel.css）
ICON_THEME = ('<svg class="i-moon" width="17" height="17" viewBox="0 0 24 24" fill="none" '
              'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
              'stroke-linejoin="round" aria-hidden="true"><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/></svg>'
              '<svg class="i-sun" width="17" height="17" viewBox="0 0 24 24" fill="none" '
              'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true">'
              '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4'
              'M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4"/></svg>')
# 齿轮（Feather 风格），和主题按钮的太阳刻意画得不一样
ICON_GEAR = ('<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
             'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
             '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 '
             '2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 '
             '0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 '
             '1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06'
             'a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09'
             'a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 '
             '0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>')
ICON_BACK = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
             'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
             '<path d="M19 12H5"/><path d="M12 19l-7-7 7-7"/></svg>')


def asset(name: str) -> str:
    """读 panel_assets/ 下的静态文件。每次请求重读：文件很小，换来改样式刷新即生效。"""
    return (ASSET_DIR / name).read_text(encoding="utf-8")


def _e(value) -> str:
    return html.escape(str(value if value is not None else ""))


def _js(value) -> str:
    """嵌进 <script> 的 JSON。转义 </ 以免提前结束 script 标签。"""
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


def _tag_html(kind: str, text: str, title: str | None = None) -> str:
    """kind 决定配色。薪资两种刻意拉开：
    `salary` 是我们折算出来的数字，`raw` 是雇主的原话（虚线框 + 斜体）。
    """
    tip = f' title="{_e(title)}"' if title else ""
    return f'<span class="tag {kind}"{tip}>{_e(text)}</span>'


_RAW_PREFIX = "原文 "                          # render.salary_raw_tag 的前缀
_EXPERIENCE = re.compile(r"^经验 (\d+)\+ 年 ([+-]\d+)$")   # render.tags_for 的经验 tag


def _tag_text(kind: str, text: str, lang: str) -> str:
    """render.tags_for 产出的是中文短语（md 报告与搜索也用它，所以不改源头），
    这里只把两种固定句式换成当前界面语言；规则名、技能名、薪资数字照原样。"""
    if kind == "raw" and text.startswith(_RAW_PREFIX):
        return i18n.t(lang, "tag.raw", text=text[len(_RAW_PREFIX):])
    if kind == "salary" and lang != "zh":
        # render.salary_tag 的固定后缀：「/年」与「（正文）」（数字、⚠、≈ 照原样）
        year, from_text = i18n.t(lang, "tag.salary_year"), i18n.t(lang, "tag.salary_from_text")
        return text.replace("/年", year).replace("（正文）", from_text)
    if kind == "level" and (m := _EXPERIENCE.match(text)):
        return i18n.t(lang, "tag.experience", years=m.group(1), delta=m.group(2))
    return text


def _tags(pairs, lang: str) -> str:
    return "".join(_tag_html(kind, _tag_text(kind, text, lang)) for kind, text in pairs)


def _label_state(label: dict) -> dict:
    return {k: label.get(k) for k in ("eligibility", "interest", "action", "note")}


def _age_tag(row: dict, lang: str) -> tuple[str, str] | None:
    """面板专用的「挂出 N 天」（不进 tags_for，见 panel_data.age_tag 的说明）。"""
    age = age_days(row)
    if age is None or age <= AGE_WARN_DAYS:
        return None
    return ("age", i18n.t(lang, "age.tag", days=age))


# ---------------------------------------------------------------------------
# 标注：分段按钮与三个圆点
# ---------------------------------------------------------------------------

def _btn(uid, field, value, on, cls="", lang: str = "zh") -> str:
    """标注按钮。显示名走 i18n，存储值仍是英文枚举 —— 改文案不用迁移数据。"""
    text = i18n.value_label(lang, field, value) or value
    klass = "seg" + (" on" if on else "") + (f" {cls}" if cls else "")
    return (f'<button type="button" class="{klass}" aria-pressed="{"true" if on else "false"}" '
            f'data-uid="{_e(uid)}" data-field="{field}" data-value="{value}" '
            f'onclick="setLabel(\'{_e(uid)}\',\'{field}\',\'{value}\',this)">{_e(text)}</button>')


def _dot_class(field: str, value: str | None) -> str:
    if not value:
        return "dot unset"
    return "dot " + TONE.get(value, "mid")


def _dots_title(label: dict, lang: str) -> str:
    sep = i18n.t(lang, "js.dots_sep")
    colon = i18n.t(lang, "js.colon")
    return sep.join(
        i18n.t(lang, f"field.{k}") + colon
        + (i18n.value_label(lang, k, label.get(k)) if label.get(k) else i18n.t(lang, "dots.unset"))
        for k in FILTER_KEYS)


def _dots(uid: str, label: dict, lang: str) -> str:
    """固定三个圆点：可投性 · 兴趣 · 投递进度。空心 = 未标注。
    之前只在标了某几项时出一个文字徽标，看不出标的是哪一项。"""
    title = _dots_title(label, lang)
    dots = "".join(f'<span class="{_dot_class(k, label.get(k))}" data-axis="{k}"></span>'
                   for k in FILTER_KEYS)
    return (f'<span class="dots" data-uid="{_e(uid)}" title="{_e(title)}" '
            f'role="img" aria-label="{_e(title)}">{dots}</span>')


# ---------------------------------------------------------------------------
# 列表卡片与详情
# ---------------------------------------------------------------------------

def _card(row: dict, since: str | None = None, lang: str = "zh") -> str:
    uid = row["uid"]
    label = row.get("_label") or {}
    # 本次会话（since 之后）标过的：在推荐视图里仍然留在列表里（保证翻页不跳），
    # 但要一眼看出「已经处理过了」，否则会以为标注没生效。
    t = label.get("labeled_at") or ""
    done = bool(since and t and t > since)

    tags = _tags(tags_for(row), lang)
    if row.get("is_excluded"):
        # 「全部」和「新增」都会同时列出保留区与排除区，不标一下分不清
        tags = _tag_html("bad", i18n.t(lang, "card.excluded"),
                         i18n.t(lang, "card.rule", rule=row.get("rule_id") or "")) + tags
    # 挂出 >30 天的 tag **置顶于所有 tag**（包括「已排除」）：一眼看到就能跳过
    if (age := _age_tag(row, lang)):
        tags = _tag_html(*age) + tags
    if row.get("eligibility_cue"):
        tags += _tag_html("warn", i18n.cue_label(lang, row["eligibility_cue"]))

    sub = " · ".join(str(x) for x in (
        row.get("company"), row.get("location"), row.get("source")) if x)
    # 搜索已挪到服务端（见 _search_text），这里不再拼 data-text
    return "".join([
        f'<div class="card{" done" if done else ""}" data-uid="{_e(uid)}" role="button" tabindex="0" '
        f'onclick="openJob(\'{_e(uid)}\')" onkeydown="cardKeys(event,\'{_e(uid)}\')">',
        '<div class="card-top">',
        f'<p class="t">{_e(row.get("title"))}</p>',
        f'<span class="score" title="{_e(i18n.t(lang, "card.score"))}">{_e(row.get("total_score"))}</span>',
        "</div>",
        f'<div class="sub">{_e(sub)}</div>',
        # 状态点放在 tags 行内部（不是绝对定位），避免压在标签上
        f'<div class="tags">{tags}{_dots(uid, label, lang)}</div>',
        "</div>",
    ])


def _detail_html(row: dict, lang: str = "zh") -> str:
    """右侧详情。与 md 报告同口径（都走 render.py），只在样式上更贴合面板。"""
    T = lambda key, **kw: i18n.t(lang, key, **kw)   # noqa: E731
    uid = row["uid"]
    label = row.get("_label") or {}

    meta_parts = [row.get("company"), row.get("location"), row.get("source"),
                  T("detail.posted", date=row["listing_date"]) if row.get("listing_date") else None,
                  row.get("salary"), row.get("work_type")]
    meta = " · ".join(str(x) for x in meta_parts if x)
    if row.get("duplicate_count"):
        meta += " · " + T("detail.dupes", n=row["duplicate_count"],
                          sources=row.get("duplicate_sources") or "")

    # 详情页空间够，技能 tag 全部展开，不折成 +N
    tag_pairs = tags_for(row, skill_limit=None)
    if (age := _age_tag(row, lang)):
        tag_pairs = [age] + tag_pairs
    tags = _tags(tag_pairs, lang)
    url = _e(row.get("url"))

    out = [
        f'<button type="button" id="back" onclick="closeDetail()">{_e(T("detail.back"))}</button>',
        '<div class="d-head"><div class="d-title">',
        f'<h2>{_e(row.get("title"))}</h2>',
        f'<div class="sub">{_e(meta)}</div></div>',
        f'<a class="apply" href="{url}" target="_blank" rel="noopener">{_e(T("detail.open"))} ↗</a>',
        "</div>",
        f'<div class="tags d-tags">{tags}'
        f'<button type="button" class="linkbtn" aria-expanded="false" aria-controls="skill-edit" '
        f'onclick="toggleSkillEdit(this)">{_e(T("detail.skill_edit"))}</button></div>',
        # 这里不再重复列「本条命中」：上面的 tag 行已经全展开了
        '<div class="skill-edit" id="skill-edit" hidden>'
        f'<div class="skill-edit-head">{_e(T("detail.skill_head"))}'
        '<span class="skill-status" id="skill-status"></span></div>'
        f'<input id="skill-q" placeholder="{_e(T("detail.skill_placeholder"))}" '
        'autocomplete="off" spellcheck="false" '
        'onfocus="skillRender()" oninput="skillRender()" onblur="skillBlur()" '
        'oncompositionstart="imeStart(this)" oncompositionend="imeEnd(this)" '
        'onkeydown="skillKeys(event)">'
        '<div class="note-menu" id="skill-menu"></div>'
        f'<div class="hint">{_e(T("detail.skill_hint"))}</div>'
        '</div>',
    ]

    if row.get("is_excluded"):
        out.append(f'<div class="rule">{_e(T("detail.rule"))}<b>{_e(row.get("rule_id"))}</b>'
                   f'　{_e(T("detail.matched"))}{_e(row.get("matched_text"))}</div>')

    out.append(f'<section class="decide" aria-label="{_e(T("detail.decide"))}">')
    step = 1
    if row.get("eligibility_snippet"):
        # 原文证据不翻译：它是人快速判断用的事实依据
        out.append(
            '<div class="step-block">'
            f'<div class="step-title"><span class="step">{step}</span>{_e(T("detail.step1"))}'
            f'{_tag_html("warn", i18n.cue_label(lang, row.get("eligibility_cue")))}'
            f'<span class="spacer"></span><span class="step-aside">{_e(T("detail.original"))}</span></div>'
            f'<blockquote class="snippet" lang="en">{_e(row["eligibility_snippet"])}</blockquote>'
            f'<div class="evidence-note">{_e(T("detail.evidence_note"))}</div>'
            '</div>')
        step += 1

    axes = []
    for field, values in (("eligibility", ("eligible", "ineligible", "unsure")),
                          ("interest", ("want", "maybe", "no")),
                          ("action", ("saved", "applied", "skipped"))):
        buttons = "".join(_btn(uid, field, v, label.get(field) == v, TONE.get(v, ""), lang)
                          for v in values)
        axes.append(
            f'<div class="axis" role="group" aria-label="{_e(T("field." + field))}">'
            f'<div class="axis-label"><span class="{_dot_class(field, label.get(field))}" '
            f'data-axis-dot="{field}"></span>{_e(T("field." + field))}</div>'
            f'<div class="segs">{buttons}</div></div>')
    help_items = "".join(
        f'<div><b>{_e(T("field." + k))}</b><span>{_e(T("help." + k))}</span></div>'
        for k in FILTER_KEYS)
    out.append(
        '<div class="step-block">'
        f'<div class="step-title"><span class="step">{step}</span>{_e(T("detail.step2"))}</div>'
        f'<div class="axes">{"".join(axes)}'
        f'<button type="button" class="help" aria-expanded="false" aria-controls="help-panel" '
        f'aria-label="{_e(T("help.button"))}" title="{_e(T("help.button"))}" '
        f'onclick="toggleHelp(this)">?</button></div>'
        f'<div class="help-panel" id="help-panel" role="note" hidden>'
        f'<div class="help-grid">{help_items}</div>'
        f'<div class="help-foot">{_e(T("help.foot"))}</div></div>'
        '</div>')
    step += 1

    out.append(
        '<div class="step-block">'
        f'<div class="step-title"><span class="step">{step}</span>{_e(T("detail.step3"))}'
        f'<span class="step-aside">{_e(T("detail.optional"))}</span></div>'
        f'<div class="note">'
        f'<input data-uid="{_e(uid)}" data-note-stored="{_e(label.get("note"))}" '
        f'placeholder="{_e(T("detail.note_placeholder"))}" '
        f'aria-label="{_e(T("field.note"))}" '
        f'value="{_e(i18n.display_note(lang, label.get("note")))}" autocomplete="off" spellcheck="false" '
        f'onfocus="noteRender(this,false)" oninput="noteTyping(event)" '
        f'oncompositionend="noteComposed(this)" '
        f'onkeydown="noteKeys(event,this)" onblur="noteBlur(this)">'
        f'<div class="hint">{_e(T("detail.note_hint"))}</div>'
        f'</div></div>')
    out.append("</section>")

    out.append(f'<h3 class="jd-head">{_e(T("detail.jd"))} <span>· {_e(T("detail.jd_note"))}</span></h3>')
    out.append(f'<div class="jd">{render_jd_html(row.get("description"), T("detail.jd_empty"))}</div>')
    # 竖屏时固定在底部的操作栏（桌面隐藏；顶部那个按钮在竖屏隐藏）
    out.append(f'<div class="apply-bar"><a class="apply" href="{url}" target="_blank" '
               f'rel="noopener">{_e(T("detail.open"))} ↗</a></div>')
    return "".join(out)


# ---------------------------------------------------------------------------
# 顶栏、筛选
# ---------------------------------------------------------------------------

STALE_AFTER_DAYS = 2


def _last_run_note(conn, lang: str = "zh") -> str:
    """「上次抓取」提示。

    存在的意义：定时任务**静默死掉是没有任何反馈的** —— 页面照常打开、
    数据照常存在，只是不再更新。把时间摆出来，超过 STALE_AFTER_DAYS 变红。
    """
    T = lambda key, **kw: i18n.t(lang, key, **kw)   # noqa: E731
    stamp = database.last_run_at(conn, "run") or database.last_run_at(conn)
    if not stamp:
        return f'<span class="run">{_e(T("run.never"))}</span>'
    try:
        then = datetime.fromisoformat(stamp)
    except ValueError:
        return f'<span class="run">{_e(T("run.at", stamp=stamp))}</span>'
    seconds = (datetime.now(timezone.utc) - then).total_seconds()
    if seconds < 3600:
        ago = T("ago.minutes", n=max(1, int(seconds // 60)))
    elif seconds < 86400:
        ago = T("ago.hours", n=int(seconds // 3600))
    else:
        ago = T("ago.days", n=int(seconds // 86400))
    stale = seconds > STALE_AFTER_DAYS * 86400
    return (f'<span class="run{" stale" if stale else ""}"><span class="dot {"bad" if stale else "good"}"></span>'
            f'{_e(T("run.ago", ago=ago))}{_e(T("run.stale")) if stale else ""}</span>')


def filter_layout(view: str) -> tuple[tuple[str, ...], dict[str, list[str]]]:
    """(常驻筛选, {"label": [...], "job": [...]})。两者之和 + 搜索 + 排序
    恰好等于该视图允许的筛选（VIEW_FILTER_KEYS），只是摆放不同。"""
    allowed = VIEW_FILTER_KEYS.get(view, ALL_FILTER_KEYS)
    primary = tuple(k for k in PRIMARY_FILTERS.get(view, DEFAULT_PRIMARY) if k in allowed)
    rest = [k for k in allowed if k not in primary and k not in TOOLBAR_FIXED]
    return primary, {"label": [k for k in rest if k in LABEL_GROUP_KEYS],
                     "job": [k for k in rest if k not in LABEL_GROUP_KEYS]}


def _is_active(key: str, filters: dict) -> bool:
    value = filters.get(key)
    if key == "labeled":
        return value in ("yes", "no")
    return bool(value)


def _select(key: str, label: str, options: list[tuple[str, str]], current: str) -> str:
    opts = "".join(f'<option value="{_e(v)}"{" selected" if current == v else ""}>{_e(t)}</option>'
                   for v, t in options)
    on = " on" if current else ""
    return (f'<label class="ctl{on}"><span class="k">{_e(label)}</span>'
            f'<select data-fkey="{key}" onchange="applyFilter()">{opts}</select></label>')


def _toggle(key: str, label: str, checked: bool) -> str:
    return (f'<label class="ctl tog{" on" if checked else ""}">'
            f'<input type="checkbox" data-fkey="{key}"{" checked" if checked else ""} '
            f'onchange="applyFilter()"> {_e(label)}</label>')


def _skill_picker(filters: dict, counts: dict, lang: str = "zh") -> str:
    """技能多选。**默认一个都不勾、面板收起** —— 相当一部分岗位一个技术词
    都没有，默认开启会静默藏掉它们。按实际命中数降序，只露头部
    （SKILL_PICK_TOP），长尾交给搜索框。已选中的一定出现，否则取消不掉。"""
    if not counts:
        return ""
    selected = list(filters.get(SKILL_KEY) or [])
    ordered = [name for name, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    shown = [s for s in ordered if s in selected]
    shown += [s for s in ordered if s not in selected][:max(0, SKILL_PICK_TOP - len(shown))]
    shown += [s for s in selected if s not in shown]
    opts = []
    for name in shown:
        checked = " checked" if name in selected else ""
        opts.append(
            f'<label class="skillopt">'
            f'<input type="checkbox" data-fkey="{SKILL_KEY}" value="{_e(name)}"{checked} '
            f'onchange="applyFilter()">'
            f'<span>{_e(name)}</span>'
            f'<span class="skc">{counts.get(name, 0)}</span></label>')
    title = i18n.t(lang, "filter.skill")
    value = ", ".join(selected) if selected else i18n.t(lang, "filter.any")
    return (f'<div class="skillpick" id="skillpick">'
            f'<button type="button" class="ctl{" on" if selected else ""}" aria-haspopup="true" '
            f'title="{_e(i18n.t(lang, "filter.skill_hint"))}" onclick="skillToggle(event)">'
            f'<span class="k">{_e(title)}</span> <span class="v">{_e(value)}</span> ▾</button>'
            f'<div class="skillpanel" id="skill-panel">{"".join(opts)}</div></div>')


def _tier_select(filters: dict, lang: str = "zh") -> str:
    """层级下拉。层级本来就已算好，但此前只能靠搜 t1 —— 那是拿 T1 的唯一入口。
    补一个真正的下拉之后，t1 退化成快捷方式。"""
    options = [("", i18n.t(lang, "filter.any"))] + [(v, v) for v in TIER_LABELS]
    return _select(TIER_KEY, i18n.t(lang, "filter.tier"), options, filters.get(TIER_KEY, ""))


def _control(key: str, filters: dict, skill_counts: dict, lang: str) -> str:
    T = lambda k, **kw: i18n.t(lang, k, **kw)   # noqa: E731
    if key in FILTER_KEYS:
        options = ([("", T("filter.any"))]
                   + [(v, i18n.value_label(lang, key, v)) for v in labels_mod.VALID[key]]
                   + [(NONE_TOKEN, T("filter.none"))])
        return _select(key, T(f"field.{key}"), options, filters.get(key, ""))
    if key == "salary":
        return _toggle("salary", T("filter.salary"), bool(filters.get("salary")))
    if key == FRESH_KEY:
        return _toggle(FRESH_KEY, T("filter.fresh"), bool(filters.get(FRESH_KEY)))
    if key == PRIORITY_KEY:
        return _toggle(PRIORITY_KEY, T("filter.priority"), bool(filters.get(PRIORITY_KEY)))
    if key == SKILL_KEY:
        return _skill_picker(filters, skill_counts, lang)
    if key == TIER_KEY:
        return _tier_select(filters, lang)
    if key == "labeled":
        current = filters.get("labeled") if filters.get("labeled") in ("yes", "no") else ""
        options = [("", T("labeled.any")), ("no", T("labeled.no")), ("yes", T("labeled.yes"))]
        return _select("labeled", T("filter.labeled"), options, current)
    return ""


def _chip_text(key: str, filters: dict, lang: str) -> str:
    T = lambda k, **kw: i18n.t(lang, k, **kw)   # noqa: E731
    colon = T("js.colon")
    value = filters.get(key)
    if key in FILTER_KEYS:
        shown = T("filter.none") if value == NONE_TOKEN else i18n.value_label(lang, key, value)
        return T(f"field.{key}") + colon + shown
    if key == "labeled":
        return T("filter.labeled") + colon + T(f"labeled.{value}")
    if key == "salary":
        return T("filter.salary_chip")
    if key == FRESH_KEY:
        return T("filter.fresh")
    if key == PRIORITY_KEY:
        return T("filter.priority")
    if key == TIER_KEY:
        return T("filter.tier") + colon + str(value)
    if key == SKILL_KEY:
        return T("filter.skill") + colon + ", ".join(value or [])
    return key


def _filter_bar(view: str, filters: dict, skill_counts: dict, lang: str = "zh") -> str:
    """工具栏 + 「更多筛选」面板 + 生效条件。

    **搜索和筛选是同一套机制**：同一个 `data-fkey` + `applyFilter()`，
    同一个 URL 参数，服务端一起过滤。
    摆放见 filter_layout：常驻的放工具栏，其余收进「更多筛选」；
    竖屏时 panel.js 把常驻筛选和排序挪进同一个面板（底部抽屉），
    所以每个控件在 DOM 里只有一份，不会重复进 URL。
    """
    T = lambda k, **kw: i18n.t(lang, k, **kw)   # noqa: E731
    allowed = VIEW_FILTER_KEYS.get(view, ALL_FILTER_KEYS)
    primary, groups = filter_layout(view)
    hidden_keys = groups["label"] + groups["job"]
    hidden_active = [k for k in hidden_keys if _is_active(k, filters)]
    counted = [k for k in allowed if k not in TOOLBAR_FIXED]
    total_active = sum(1 for k in counted if _is_active(k, filters))

    out = ['<div class="toolbar">']
    if QUERY_KEY in allowed:
        out.append(
            f'<label class="search">{ICON_SEARCH}'
            f'<input type="search" class="q" data-fkey="{QUERY_KEY}" '
            f'aria-label="{_e(T("search.label"))}" '
            f'value="{_e(filters.get(QUERY_KEY, ""))}" '
            f'placeholder="{_e(T("search.placeholder"))}" '
            f'oninput="searchTyping(event)" onkeydown="searchKeys(event)" '
            f'oncompositionstart="searchStart(event)" '
            f'oncompositionend="searchEnd(event)" '
            f'autocomplete="off" spellcheck="false"></label>')
    out.append('<span id="primary-home" hidden></span><div class="filter-group" id="primary-filters">')
    out.extend(_control(k, filters, skill_counts, lang) for k in primary)
    out.append("</div>")
    if hidden_keys:
        badge = f'<span class="badge">{len(hidden_active)}</span>' if hidden_active else ""
        out.append(f'<button type="button" class="fbtn more-btn" id="more-btn" aria-expanded="false" '
                   f'aria-controls="more-panel" onclick="toggleMore()">{ICON_FILTER}'
                   f'{_e(T("filter.more"))}{badge}</button>')
    badge = f'<span class="badge">{total_active}</span>' if total_active else ""
    out.append(f'<button type="button" class="fbtn sheet-btn" aria-expanded="false" '
               f'aria-controls="more-panel" onclick="toggleMore()">{ICON_FILTER}'
               f'{_e(T("filter.button"))}{badge}</button>')
    out.append('<span class="spacer"></span>')
    out.append('<span id="tail-home" hidden></span><div class="filter-group" id="tail-filters">')
    if "sort" in allowed:
        sort = filters.get("sort") or "score"
        out.append(_select("sort", T("filter.sort"),
                           [(v, T(f"sort.{v}")) for v in SORT_KEYS], sort)
                   .replace('class="ctl on"', 'class="ctl"'))
    out.append("</div>")
    if any(k != SINCE_KEY for k in filters):
        out.append(f'<a class="clear" href="#" onclick="clearFilter();return false;">'
                   f'{_e(T("filter.clear_all"))}</a>')
    out.append("</div>")

    # 「更多筛选」面板：桌面是工具栏下方的展开区，竖屏是底部抽屉
    out.append(f'<div class="more-panel" id="more-panel" data-keys="{" ".join(hidden_keys)}" hidden>'
               f'<div class="sheet-grab" aria-hidden="true"></div>'
               f'<div class="sheet-head"><b>{_e(T("filter.button"))} · {_e(T("view." + view))}</b>'
               f'<button type="button" class="linkbtn" onclick="clearFilter()">'
               f'{_e(T("filter.clear_all"))}</button></div>'
               '<div class="sheet-slot" id="sheet-slot"></div>')
    for group in ("label", "job"):
        keys = groups[group]
        if not keys:
            continue
        out.append(f'<fieldset class="fgroup"><legend>{_e(T("filter.group." + group))} '
                   f'<span>· {_e(T("filter.group." + group + "_hint"))}</span></legend>'
                   f'<div class="filter-group">'
                   + "".join(_control(k, filters, skill_counts, lang) for k in keys)
                   + "</div></fieldset>")
    clear_hidden = (f'<button type="button" class="linkbtn" onclick="clearHidden()">'
                    f'{_e(T("filter.clear_hidden"))}</button>') if hidden_keys else ""
    out.append(f'<div class="more-foot">{clear_hidden}'
               f'<button type="button" class="primary-btn" onclick="toggleMore(false)">'
               f'{_e(T("filter.done"))}</button></div></div>')

    if hidden_active:
        chips = "".join(
            f'<span class="achip">{_e(_chip_text(k, filters, lang))}'
            f'<button type="button" aria-label="{_e(T("filter.remove"))}: {_e(_chip_text(k, filters, lang))}" '
            f'onclick="removeFilter(\'{k}\')">×</button></span>'
            for k in hidden_active)
        out.append(f'<div class="chips" id="active-chips"><span class="chips-label">'
                   f'{_e(T("filter.active"))}</span>{chips}'
                   f'<button type="button" class="linkbtn" onclick="clearHidden()">'
                   f'{_e(T("filter.clear_hidden"))}</button></div>')
    return "".join(out)


def _search_explanation(filters: dict, cfg: dict, total: int, lang: str = "zh") -> str:
    """把「搜索怎么读的」摆在结果上方。和项目里「决策要可追溯」同一条原则：
    用户要能看出这个词被当成技术词 / 前缀 / 整词，而不是对着空结果猜。"""
    query = filters.get(QUERY_KEY)
    if not query:
        return ""
    terms = skills_mod.search_terms(query, cfg)
    if not terms:
        return ""
    kinds = {"skill": "term.skill", "prefix": "term.prefix"}
    joined = " + ".join(_e(i18n.t(lang, kinds.get(kind, "term.word"), label=label))
                        for _rx, kind, label in terms)
    note = i18n.t(lang, "search.all_match") if len(terms) > 1 else ""
    return ('<div class="qexplain">'
            + i18n.t(lang, "search.explain", query=_e(query), terms=joined, all=_e(note), total=total)
            + '</div>')


# ---------------------------------------------------------------------------
# 页面骨架
# ---------------------------------------------------------------------------

def _head(lang: str, theme: str | None, title: str, body_class: str = "") -> str:
    theme_attr = f' data-theme="{theme}"' if theme in i18n.THEMES else ""
    cls = f' class="{body_class}"' if body_class else ""
    return "".join([
        f'<!doctype html><html lang="{i18n.HTML_LANG[lang]}"{theme_attr}><head><meta charset=utf-8>',
        '<meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover">',
        '<meta name="color-scheme" content="light dark">',
        f"<title>{_e(title)}</title>",
        f"<style>{asset('panel.css')}</style></head><body{cls}>",
    ])


_DEMO_BANNER = {
    "en": ("Demo · Synthetic data",
           "Hand-written fictional jobs. Nothing here is real; "
           "edits are saved only to the demo database."),
    "zh": ("演示 · 合成数据",
           "全部为手写的虚构岗位，并非真实信息；所有改动只保存在 demo 数据库。"),
}


def _demo_suffix(config: dict, lang: str) -> str:
    return (" · 演示" if lang == "zh" else " · Demo") if config.get("demo") else ""


def _demo_banner(config: dict, lang: str = "en") -> str:
    """独立 demo 的常驻标识，随界面语言切换。仅当 config 带 `demo` 段时出现；
    生产配置没有该段，渲染结果与原来逐字节一致。文案放在这里而不是 i18n.py，
    是为了不扩大共享翻译表的改动面。"""
    if not config.get("demo"):
        return ""
    title, text = _DEMO_BANNER.get(lang) or _DEMO_BANNER["en"]
    return (f'<div class="demo-banner" role="note"><b>{_e(title)}</b>'
            f'<span>{_e(text)}</span></div>')


def _theme_button(lang: str) -> str:
    # aria-label 由 panel.js 按当前生效主题改写（切到深色 / 切到浅色）
    return (f'<button type="button" class="iconbtn theme-btn" onclick="toggleTheme()" '
            f'aria-label="{_e(i18n.t(lang, "theme.to_dark"))}" '
            f'title="{_e(i18n.t(lang, "theme.to_dark"))}">{ICON_THEME}</button>')


def _lang_button(lang: str) -> str:
    other = "en" if lang == "zh" else "zh"
    return (f'<button type="button" class="iconbtn lang-btn" onclick="setLang(\'{other}\')" '
            f'aria-label="{_e(i18n.t(lang, "lang.switch"))}" title="{_e(i18n.t(lang, "lang.switch"))}">'
            f'{ICON_GLOBE}<span>{_e(i18n.t(lang, "lang.current"))}</span>'
            f'<span class="muted">/ {_e(i18n.t(lang, "lang.other"))}</span></button>')


def _scripts(lang: str, extra: dict | None = None) -> str:
    data = {"__I18N__": i18n.js_strings(lang), "__LANG__": lang}
    data.update(extra or {})
    return "".join(f"<script>window.{k}={_js(v)};</script>" for k, v in data.items())


def _stats(view: str, stats: dict, total: int, lang: str) -> list[tuple[str, object]]:
    T = lambda k: i18n.t(lang, k)   # noqa: E731
    common = [(T("stat.labelled"), stats["已标注"]), (T("stat.saved"), stats["待投"]),
              (T("stat.applied"), stats["已投递"])]
    if view == "new":
        return [(T("stat.batch"), total)] + common
    if view == "excluded":
        return [(T("stat.miss"), stats["漏杀候选"]), (T("stat.false_excl"), stats["误杀候选"])]
    if view == "labelled":
        return common[1:] + [(T("stat.miss"), stats["漏杀候选"])]
    return common


def render_page(conn, config: dict, view: str, offset: int, limit: int,
                started: float | None = None, filters: dict | None = None,
                lang: str = "zh", theme: str | None = None) -> str:
    """渲染整页。

    `started` 现在**不在这里用**（页脚已删），但保留参数：它和
    `Handler._send(started=…)` 是同一个起点，Server-Timing 响应头与
    data/panel.log 的耗时都靠它。删掉参数容易让人顺手把计时链路一起删了。
    """
    T = lambda k, **kw: i18n.t(lang, k, **kw)   # noqa: E731
    filters = filters or {}
    since = _resolve_since(conn, view, filters)
    # 每次请求重读词表：词表文件很小，开销可忽略。换来的是「改了词表刷新即生效」，
    # 不用重启常驻面板（这也是把词表放数据里而不是代码里的意义）。
    skills_cfg = skills_mod.load_skills(config.get("skills_path"))
    page, total = _query(conn, config, view, offset, limit, filters, since, skills_cfg)
    stats = labels_mod.summary(conn)
    skill_counts = database.skill_counts(conn)

    # nav 用不带 since 的链接：换视图 = 重新冻结（推荐视图会重新取未标注）。
    # 点击由 gotoView() 接管 —— 它会补上该视图自己记住的筛选条件；
    # href 保持干净是为了修饰键/中键还能正常开新标签。
    nav = "".join(
        f'<a class="tab{" on" if key == view else ""}" href="{_link(key, 0, filters)}"'
        f'{CURRENT if key == view else ""}'
        f' onclick="return gotoView(\'{key}\',event)">{_e(T("view." + key))}</a>'
        for key in VIEWS)
    drawer_views = "".join(
        f'<a class="dv{" on" if key == view else ""}" href="{_link(key, 0, filters)}"'
        f'{CURRENT if key == view else ""}'
        f' onclick="return gotoView(\'{key}\',event)"><b>{_e(T("view." + key))}</b>'
        f'<small>{_e(T("viewhint." + key))}</small></a>'
        for key in VIEWS)

    embed = {r["uid"]: _label_state(r.get("_label") or {}) for r in page}
    note_tags = labels_mod.note_tag_candidates(conn)
    # 按字母排：详情页的候选不再按输入过滤，排序是唯一的"怎么找"。
    skill_embed = [{"name": e.get("name"), "aliases": e.get("aliases") or []}
                   for e in sorted(skills_cfg.get("skills") or [],
                                   key=lambda e: str(e.get("name") or "").lower())]

    pager = []
    if total > limit:
        if offset > 0:
            pager.append(f'<a href="{_link(view, max(0, offset - limit), filters, since)}">'
                         f'{_e(T("list.prev"))}</a>')
        pager.append(f'<span>{_e(T("list.page", page=offset // limit + 1, pages=(total - 1) // limit + 1))}</span>')
        if offset + limit < total:
            pager.append(f'<a href="{_link(view, offset + limit, filters, since)}">{_e(T("list.next"))}</a>')

    stat_items = _stats(view, stats, total, lang)
    stats_html = "".join(f'<span>{_e(name)} <b>{_e(n)}</b></span>' for name, n in stat_items)
    run_note = _last_run_note(conn, lang)
    has_filters = any(k != SINCE_KEY for k in filters)
    if page:
        cards = "".join(_card(r, since, lang) for r in page)
    elif has_filters:
        cards = (f'<div class="empty"><b>{_e(T("list.empty_filtered"))}</b>'
                 f'<span>{_e(T("list.empty_hint"))}</span>'
                 f'<button type="button" class="btn" onclick="clearFilter()">'
                 f'{_e(T("filter.clear_all"))}</button></div>')
    else:
        cards = f'<div class="placeholder">{_e(T("list.empty"))}</div>'

    drawer_stats = "".join(f'<div><b>{_e(n)}</b>{_e(name)}</div>'
                           for name, n in [(T("stat.labelled"), stats["已标注"]),
                                           (T("stat.saved"), stats["待投"]),
                                           (T("stat.applied"), stats["已投递"])])
    lang_seg = "".join(
        f'<button type="button" class="{"on" if code == lang else ""}" '
        f'aria-pressed="{"true" if code == lang else "false"}" onclick="setLang(\'{code}\')">{label}</button>'
        for code, label in (("zh", "中文"), ("en", "English")))
    theme_seg = "".join(
        f'<button type="button" data-theme-choice="{code}" onclick="setTheme(\'{code}\')">'
        f'{_e(T("set.theme_" + code))}</button>'
        for code in ("light", "dark", "auto"))

    return "".join([
        _head(lang, theme, T("app.title") + _demo_suffix(config, lang)),
        _demo_banner(config, lang),
        '<header class="main-header">',
        '<div class="topbar">',
        f'<button type="button" class="iconbtn menu-btn" aria-label="{_e(T("nav.menu"))}" '
        f'aria-expanded="false" aria-controls="drawer" onclick="openDrawer()">{ICON_MENU}</button>',
        f'<h1>{_e(T("app.title"))}</h1>',
        f'<span class="mobile-view"><b>{_e(T("view." + view))}</b> <span>{total}</span></span>',
        f'<nav class="tabs" aria-label="{_e(T("nav.views"))}">{nav}</nav>',
        '<span class="spacer"></span>',
        '<div class="top-actions">',
        _lang_button(lang),
        _theme_button(lang),
        f'<a class="iconbtn setlink" href="/settings" title="{_e(T("nav.settings"))}">'
        f'{ICON_GEAR}<span>{_e(T("nav.settings"))}</span></a>',
        "</div></div>",
        _filter_bar(view, filters, skill_counts, lang),
        "</header>",
        '<div class="ctxbar">',
        f'<div class="ctx-desc">{ICON_INFO}<div><span class="ctx-text">'
        f'<b>{_e(T("view." + view))}</b>{_e(T("js.colon"))}{_e(T("viewdesc." + view))}</span>'
        f'{_search_explanation(filters, skills_cfg, total, lang)}</div></div>',
        f'<div class="ctx-stats">{stats_html}'
        f'<span>{_e(T("list.count", n=MARK, total=total)).replace(MARK, f"<b id=count>{len(page)}</b>")}</span>'
        f'{run_note}</div>',
        "</div>",
        f'<main><div class="layout"><div class="list" aria-label="{_e(T("list.label"))}">',
        cards,
        (f'<div class="pager">{"".join(pager)}</div>' if pager else ""),
        "</div>",
        # 拖动条。role=separator + tabindex 是「可调分栏」的标准无障碍写法，
        # 键盘左右箭头 / Home / End 都能调（见 gutterKeys）。
        '<div class="gutter" id="gutter" role="separator" aria-orientation="vertical" '
        'aria-label="↔" tabindex="0" title="↔" '
        'onpointerdown="startDrag(event)" onkeydown="gutterKeys(event)" '
        'ondblclick="resetSplit()"></div>',
        f'<div class="detail" id="detail" aria-label="{_e(T("detail.label"))}">',
        f'<div class="placeholder">{_e(T("detail.placeholder"))}</div>',
        "</div></div>",
        "</main>",
        # 竖屏的汉堡菜单
        '<div class="drawer" id="drawer" hidden>',
        f'<nav class="drawer-panel" aria-label="{_e(T("nav.views"))}">',
        f'<div class="drawer-head"><b>{_e(T("app.title"))}</b>'
        f'<button type="button" class="iconbtn" aria-label="{_e(T("nav.menu_close"))}" '
        f'onclick="closeDrawer()">{ICON_CLOSE}</button></div>',
        f'<div class="drawer-views">{drawer_views}</div>',
        f'<div class="drawer-stats">{drawer_stats}</div>',
        f'<div class="drawer-sec"><span>{_e(T("set.lang_title"))}</span><div class="segrow">{lang_seg}</div></div>',
        f'<div class="drawer-sec"><span>{_e(T("set.theme_title"))}</span><div class="segrow">{theme_seg}</div></div>',
        f'<a class="drawer-link" href="/settings">{ICON_GEAR}<span>{_e(T("nav.settings"))}</span>'
        f'<small>{_e(T("set.skills"))} ›</small></a>',
        f'<div class="drawer-foot">{run_note}</div>',
        "</nav>",
        f'<button type="button" class="scrim" aria-label="{_e(T("nav.menu_close"))}" onclick="closeDrawer()"></button>',
        "</div>",
        '<div class="sheet-scrim" id="sheet-scrim" onclick="toggleMore(false)"></div>',
        '<div class="toast" id="toast" role="status" aria-live="polite"></div>',
        '<div class="note-menu" id="note-menu"></div>',
        _scripts(lang, {"__LABELS__": embed, "__VIEW__": view, "__NOTE_TAGS__": note_tags,
                        "__NOTE_TAG_LABELS__": i18n.note_tag_labels(lang),
                        "__SKILLS__": skill_embed}),
        f"<script>{asset('panel.js')}</script>",
        "</body></html>",
    ])


# ---------------------------------------------------------------------------
# 设置页
# ---------------------------------------------------------------------------

SETTINGS_SECTIONS = ("skills", "display")

_PROFILE_NAMES = ("citizen", "permanent_resident", "485", "student_visa", "custom")


def _profile_display(conn, T) -> str:
    """最近一次成功 analyze 记录的 profile 显示名；只读库，不读配置。"""
    info = report_mod.latest_rule_profile(conn)
    if info.get("status") != "recorded":
        return T("set.profile_unrecorded")
    profile = info.get("profile")
    if profile is None:
        return T("set.profile_default")
    if profile in _PROFILE_NAMES:
        return T("set.profile_" + profile)
    return T("set.profile_unknown")


def render_settings(conn, config: dict, lang: str = "zh", theme: str | None = None,
                    section: str = "skills", lang_pref: str = "auto") -> str:
    """设置页：左分类、右详情，页面居中留白（LinkedIn 设置那种）。

    技术栈列表交给前端用 GET /api/skills 的数据渲染，服务端不重复写一套。
    """
    T = lambda k, **kw: i18n.t(lang, k, **kw)   # noqa: E731
    if section not in SETTINGS_SECTIONS:
        section = "skills"
    nav = "".join(
        f'<a class="set-nav-item{" on" if key == section else ""}" '
        f'href="/settings?section={key}"{CURRENT if key == section else ""}>'
        f'{_e(T("set." + key))}</a>'
        for key in SETTINGS_SECTIONS)

    if section == "skills":
        tree = skill_tree(conn, config.get("skills_path"))
        pane = "".join([
            f'<h2>{_e(T("set.skills_title"))}</h2>',
            f'<p class="set-hint">{_e(T("set.skills_hint"))}</p>',
            '<div class="set-add">'
            f'<input id="new-skill" placeholder="{_e(T("set.add_placeholder"))}" '
            f'aria-label="{_e(T("set.add_placeholder"))}" autocomplete="off" spellcheck="false">'
            f'<button id="new-skill-btn" class="primary-btn">+ {_e(T("set.add"))}</button></div>',
            '<div class="set-tools">'
            f'<input id="skill-filter" class="set-filter" placeholder="{_e(T("set.filter_placeholder"))}" '
            f'aria-label="{_e(T("set.filter_placeholder"))}" autocomplete="off" spellcheck="false">'
            f'<div class="legend"><span>{_e(T("set.legend"))}</span>'
            f'<span class="bub seed" title="{_e(T("set.tip_seed"))}">{_e(T("set.seed"))}</span>'
            f'<span class="bub local" title="{_e(T("set.tip_local"))}">{_e(T("set.local"))}</span>'
            f'<small>{_e(T("set.legend_hint"))}</small></div></div>',
            '<div class="sk-headrow" aria-hidden="true">'
            f'<span>{_e(T("set.col_name"))}</span><span>{_e(T("set.col_alias"))}</span>'
            f'<span class="num">{_e(T("set.col_hits"))}</span><span>{_e(T("set.col_source"))}</span><span></span></div>',
            '<div id="skill-tree" class="skill-tree"></div>',
            f'<p class="set-foot">{_e(T("set.footnote"))}</p>',
        ])
        extra = {"__SKILL_TREE__": tree}
    else:
        def radios(name: str, current: str, options: list[tuple[str, str, str]], fn: str) -> str:
            return '<div class="opts">' + "".join(
                f'<button type="button" class="opt{" on" if code == current else ""}" '
                f'aria-pressed="{"true" if code == current else "false"}" data-{name}-choice="{code}" '
                f'onclick="{fn}(\'{code}\')"><span class="radio"></span>'
                f'<span><b>{_e(label)}</b><small>{_e(desc)}</small></span></button>'
                for code, label, desc in options) + "</div>"
        pane = "".join([
            f'<h2>{_e(T("set.lang_title"))}</h2>',
            f'<p class="set-hint">{_e(T("set.lang_desc"))}</p>',
            radios("lang", lang_pref, [("auto", T("set.lang_auto"), T("set.lang_auto_desc")),
                                       ("zh", "中文", T("set.lang_zh_desc")),
                                       ("en", "English", T("set.lang_en_desc"))], "setLang"),
            f'<h2 class="h2-gap">{_e(T("set.theme_title"))}</h2>',
            radios("theme", theme or "auto", [("auto", T("set.theme_auto"), "prefers-color-scheme"),
                                              ("light", T("set.theme_light"), ""),
                                              ("dark", T("set.theme_dark"), "")], "setTheme"),
            f'<h2 class="h2-gap">{_e(T("set.profile_title"))}</h2>',
            f'<p class="set-hint"><b>{_e(T("set.profile_label", name=_profile_display(conn, T)))}</b>'
            f'<br>{_e(T("set.profile_used"))}</p>',
            f'<div class="keep"><b>{_e(T("set.keep_title"))}</b><ul>'
            f'<li>{_e(T("set.keep1"))}</li><li>{_e(T("set.keep2"))}</li><li>{_e(T("set.keep3"))}</li></ul></div>',
        ])
        extra = {}

    return "".join([
        _head(lang, theme, f'{T("settings.title")} · {T("app.title")}'
              + _demo_suffix(config, lang), "settings"),
        _demo_banner(config, lang),
        '<header class="set-header">'
        f'<a class="backlink" href="/" title="{_e(T("nav.back"))}" aria-label="{_e(T("nav.back"))}">'
        f'{ICON_BACK}</a>'
        f'<h1>{_e(T("settings.title"))}</h1><span class="spacer"></span>',
        _lang_button(lang), _theme_button(lang),
        '</header>',
        '<main class="set-wrap">',
        f'<nav class="set-nav" aria-label="{_e(T("set.sections"))}">{nav}</nav>',
        f'<section class="set-pane{" set-pane-scroll" if section != "skills" else ""}">{pane}</section>',
        "</main>",
        '<div class="modal" id="sk-modal" hidden></div>',
        '<div class="toast" id="toast" role="status" aria-live="polite"></div>',
        _scripts(lang, extra),
        f"<script>{asset('panel.js')}</script>",
        f"<script>{asset('settings.js')}</script>",
        "</body></html>",
    ])
