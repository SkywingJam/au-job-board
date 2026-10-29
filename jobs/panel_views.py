"""面板的 HTML 渲染：列表卡片、详情、筛选栏、主页面与设置页。

样式与脚本放在 panel_assets/ 下的静态文件里，渲染时**内联**进页面 ——
仍然是单个响应、零外链、离线可用。所有界面文案走 i18n.py（中文 / English）；
岗位正文、公司、备注、技能名等数据原样输出，不翻译。

布局约定：
  - 顶栏一行：名称 · 视图 · 上次抓取 + 更新岗位 · 显示（语言/外观）· 设置；
    第二行是搜索 + 常驻筛选，其余筛选收进「更多筛选」，收起时把生效的条件列成
    可移除的小标签。
  - 视图说明条：一句话说明当前视图口径 + 相关统计。
  - 卡片只有三类强调（状态、资格线索、薪资）；右侧是处理状态（想投 / 已投 / 跳过）
    与独立星标，未处理时不占位。
  - 详情按步骤：资格线索（原文）→ 处理（三状态 + 星标）→ 备注；资格纠错
    折叠在标注区底部；计分明细在页面底部折叠。
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
from . import skills as skills_mod
from .panel_data import (
    FILTER_KEYS, FRESH_KEY, INELIGIBLE_LAST_KEY, NONE_TOKEN, PRIORITY_KEY,
    QUERY_KEY, SINCE_KEY, SKILL_KEY, SKILL_PICK_TOP, SORT_KEYS, TIER_KEY, TIER_LABELS,
    ALL_FILTER_KEYS, VIEW_FILTER_KEYS, VIEWS,
    _link, _query, _resolve_since, age_days, AGE_WARN_DAYS, skill_tree,
)
from .render import render_jd_html, tags_for

ASSET_DIR = Path(__file__).with_name("panel_assets")
CURRENT = ' aria-current="page"'
MARK = "\x00"   # 占位：先转义文案再把它换成 <b> 计数

# 标注值的语义色（只用于显示）。处理状态：想投 = 强调色、已投 = 绿、跳过 = 中性 ——
# 跳过只是「这条不处理了」，不是资格不符，不用红色。
TONE = {"eligible": "good", "ineligible": "bad", "want": "good", "no": "bad",
        "saved": "want", "applied": "good", "skipped": "mid"}

# 每个视图哪些筛选常驻在工具栏，其余进「更多筛选」。**允许哪些筛选**仍以
# panel_data.VIEW_FILTER_KEYS 为准，这里只决定摆放位置（见 filter_layout）。
PRIMARY_FILTERS = {
    "recommend": (SKILL_KEY, TIER_KEY, "salary"),
    "labelled": ("geo", "action", PRIORITY_KEY, SKILL_KEY),
}
DEFAULT_PRIMARY = ("geo", SKILL_KEY, TIER_KEY)
LABEL_GROUP_KEYS = ("action", "eligibility", "labeled")
INTERFACE_HIDDEN_KEYS = ("interest",)        # 保留后端兼容，当前界面不再宣传或展示
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
ICON_INFO = ('<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
             'stroke-width="1.8" stroke-linecap="round" aria-hidden="true">'
             '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/></svg>')
ICON_REFRESH = ('<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
                '<path d="M20 4v6h-6"/><path d="M20 10a8 8 0 1 0-.7 7"/></svg>')
# 齿轮（Feather 风格）
ICON_GEAR = ('<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
             'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
             '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 '
             '2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 '
             '0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 '
             '1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06'
             'a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09'
             'a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 '
             '0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>')
ICON_STAR = ('<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
             'stroke-width="1.8" stroke-linejoin="round" aria-hidden="true">'
             '<path d="M12 3.6l2.6 5.3 5.8.8-4.2 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.2-4.1 5.8-.8z"/></svg>')
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


def _copy_dialog(lang: str) -> str:
    """「复制岗位」自动复制失败时的手动退路：只读文本框里是同一份完整文本。"""
    T = lambda key: i18n.t(lang, key)   # noqa: E731
    return (
        '<div class="modal copy-modal" id="copy-modal" hidden>'
        '<div class="dialog" role="dialog" aria-modal="true" '
        'aria-labelledby="copy-modal-title" aria-describedby="copy-modal-body">'
        f'<div class="dialog-head"><h3 id="copy-modal-title">{_e(T("copy.manual_title"))}</h3></div>'
        f'<div class="dialog-body"><p id="copy-modal-body">{_e(T("copy.manual_body"))}</p>'
        f'<textarea id="copy-modal-text" readonly spellcheck="false" aria-label="{_e(T("detail.copy"))}"></textarea></div>'
        '<div class="dialog-foot"><span class="spacer"></span>'
        f'<button type="button" class="btn" data-copy-select>{_e(T("copy.select"))}</button>'
        f'<button type="button" class="primary-btn" data-copy-close>{_e(T("copy.close"))}</button>'
        '</div></div></div>')


def _update_widget(lang: str, *, compact: bool = False) -> str:
    """「更新岗位」：带文字的按钮，点了先确认（见 _update_confirm），因为它会联网。
    按钮只有一个 #update-start；竖屏时 panel.js 把整个小部件挪进汉堡抽屉。"""
    T = lambda key: i18n.t(lang, key)
    icon = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M20 7v5h-5M4 17v-5h5"/><path d="M6.1 7a7 7 0 0 1 11.6-1L20 9M4 15l2.3 3A7 7 0 0 0 18 17"/></svg>'
    return ''.join([
        '<span class="update-widget update-compact" id="update-widget">' if compact else '<section class="update-widget">',
        f'<button type="button" class="btn update-btn" id="update-start" '
        f'title="{_e(T("set.update_hint"))}" aria-haspopup="dialog" disabled>',
        icon, f'<span>{_e(T("js.update_title"))}</span></button>',
        '<span class="update-feedback"><span id="update-status" tabindex="-1" role="status" aria-live="polite" aria-atomic="true"></span>',
        '' if compact else ('<span id="update-meta" class="muted"></span>'
                            f'<details class="update-details"><summary>{_e(T("update.details"))}</summary>'
                            '<span id="update-task" class="muted"></span></details>'),
        '</span>', '</span>' if compact else '</section>',
    ])


def _update_confirm(lang: str) -> str:
    """更新前的确认窗。只拦一下「会联网、要几分钟」，状态机与禁用逻辑仍在 panel.js 原处。"""
    T = lambda key: i18n.t(lang, key)
    return (
        '<div class="modal update-confirm" id="update-confirm" hidden>'
        '<div class="dialog" role="dialog" aria-modal="true" '
        'aria-labelledby="update-confirm-title" aria-describedby="update-confirm-body">'
        f'<div class="dialog-head"><h3 id="update-confirm-title">{_e(T("update.confirm_title"))}</h3></div>'
        f'<div class="dialog-body"><p id="update-confirm-body">{_e(T("update.confirm_body"))}</p></div>'
        '<div class="dialog-foot"><span class="spacer"></span>'
        f'<button type="button" class="btn" data-update-cancel>{_e(T("update.cancel"))}</button>'
        f'<button type="button" class="primary-btn" data-update-ok>{_e(T("update.confirm_ok"))}</button>'
        '</div></div></div>')


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
    return {k: label.get(k) for k in ("eligibility", "interest", "action", "note", "priority")}


def _age_tag(row: dict, lang: str) -> tuple[str, str] | None:
    """面板专用的「挂出 N 天」（不进 tags_for，见 panel_data.age_tag 的说明）。"""
    age = age_days(row)
    if age is None or age <= AGE_WARN_DAYS:
        return None
    return ("age", i18n.t(lang, "age.tag", days=age))


# ---------------------------------------------------------------------------
# 标注：处理状态、星标与资格纠错
# ---------------------------------------------------------------------------

def _btn(uid, field, value, on, cls="", lang: str = "zh") -> str:
    """标注按钮。显示名走 i18n，存储值仍是英文枚举 —— 改文案不用迁移数据。"""
    text = i18n.value_label(lang, field, value) or value
    klass = "seg" + (" on" if on else "") + (f" {cls}" if cls else "")
    return (f'<button type="button" class="{klass}" aria-pressed="{"true" if on else "false"}" '
            f'data-uid="{_e(uid)}" data-field="{field}" data-value="{value}" '
            f'onclick="setLabel(\'{_e(uid)}\',\'{field}\',\'{value}\',this)">{_e(text)}</button>')


def _star_btn(uid: str, on: bool, lang: str) -> str:
    """独立星标：与三状态并排但更轻。只切换 priority，不改处理状态。"""
    T = lambda key: i18n.t(lang, key)   # noqa: E731
    return (f'<button type="button" class="star-btn{" on" if on else ""}" '
            f'aria-pressed="{"true" if on else "false"}" title="{_e(T("help.priority"))}" '
            f'data-uid="{_e(uid)}" data-field="priority" data-value="1" '
            f'onclick="setLabel(\'{_e(uid)}\',\'priority\',\'1\',this)">'
            f'{ICON_STAR}<span>{_e(T("field.priority"))}</span></button>')


def _state_text(label: dict, lang: str) -> str:
    """一句话说明当前处理状态，详情与竖屏底栏共用（panel.js stateText 同口径）。"""
    T = lambda key, **kw: i18n.t(lang, key, **kw)   # noqa: E731
    action = label.get("action")
    text = (T("state.done", status=i18n.value_label(lang, "action", action)) if action
            else T("state.open"))
    if label.get("priority"):
        text += " · " + T("state.star")
    return text


def _card_state(uid: str, label: dict, lang: str) -> str:
    """卡片右下：星标 + 处理状态文字。未处理、没星标时都隐藏，不占位。
    读的是去重组合并后的标注（row['_label']）；panel.js 标注后就地更新。"""
    T = lambda key: i18n.t(lang, key)   # noqa: E731
    value = label.get("action")
    text = i18n.value_label(lang, "action", value) if value else ""
    title = T("field.action") + T("js.colon") + text if value else ""
    tone = TONE.get(value, "mid") if value else ""
    star = bool(label.get("priority"))
    return (f'<span class="card-state">'
            f'<span class="card-star" data-star-uid="{_e(uid)}" role="img" '
            f'aria-label="{_e(T("field.priority"))}" title="{_e(T("field.priority"))}"'
            f'{"" if star else " hidden"}>★</span>'
            f'<span class="prog {tone}" data-prog-uid="{_e(uid)}" title="{_e(title)}"'
            f'{"" if value else " hidden"}>{_e(text)}</span></span>')


def _correction_tag(row: dict, label: dict, lang: str) -> str:
    """卡片只提示与分析结果**相反**的人工纠正（误杀 / 漏杀线索）。与分析一致的旧人工
    可投、机器默认、旧待定都不占卡片位置 —— 它们在详情的资格纠错里照样可查。"""
    q = labels_mod.qualification(row, label)
    if not q["corrected"] or not q["analysis"] or q["effective"] == q["analysis"]:
        return ""
    value = q["effective"]
    return _tag_html("corr", i18n.t(lang, "card.corrected",
                                    value=i18n.value_label(lang, "eligibility", value)),
                     i18n.t(lang, "qualification.manual"))


# ---------------------------------------------------------------------------
# 列表卡片与详情
# ---------------------------------------------------------------------------

SCORE_KINDS = ("tier", "level", "adjust")    # 计分信号：卡片不显示，详情收进「为什么是 …」


def _salary_tip(row: dict, lang: str) -> str:
    """薪资标签的悬停说明：来源标记的含义 + 雇主原文（完整，不截断）。"""
    T = lambda key, **kw: i18n.t(lang, key, **kw)   # noqa: E731
    tips = []
    if row.get("salary_basis") == "annualized":
        tips.append(T("salary.tip_annualized"))
    if row.get("salary_origin") == "description":
        tips.append(T("salary.tip_text"))
    if (raw := " ".join(str(row.get("salary") or "").split())):
        tips.append(T("salary.tip_raw", text=raw))
    return "\n".join(tips)


def _salary_chip(pairs, row: dict, lang: str) -> str:
    """卡片只留**一枚**薪资：有折算值就显示折算值（≈ / （正文）来源标记原样保留），
    原文放进悬停；解析不出来时，原文本身就是那一枚（它是唯一的线索）。"""
    for kind in ("salary", "raw"):
        text = next((t for k, t in pairs if k == kind), None)
        if text:
            return _tag_html(kind, _tag_text(kind, text, lang), _salary_tip(row, lang))
    return ""


def _card(row: dict, since: str | None = None, lang: str = "zh") -> str:
    uid = row["uid"]
    label = row.get("_label") or {}
    # 推荐视图（有 since）里本次会话处理过的仍然留在列表里（保证翻页不跳），
    # 变淡表示「已经处理过了」。其他视图本来就混着各种状态，不变淡。
    done = bool(since and label.get("action"))

    # 卡片只用三类强调：状态（挂出 N 天 / 已排除）、资格线索、薪资。
    # 层级并进分数徽章；加减分移到详情的「为什么是 …」；技能是中性文字。
    # 这里只挑选 render.tags_for 的输出来显示，报告与搜索用的 tags_for 本身不变。
    pairs = tags_for(row, skill_limit=None)
    tags = ""
    # 挂出 >30 天的 tag **置顶于所有 tag**（包括「已排除」）：一眼看到就能跳过
    if (age := _age_tag(row, lang)):
        tags += _tag_html(*age)
    if row.get("is_excluded"):
        # 「全部」和「新增」都会同时列出保留区与排除区，不标一下分不清
        tags += _tag_html("bad", i18n.t(lang, "card.excluded"),
                          i18n.t(lang, "card.rule", rule=row.get("rule_id") or ""))
    if row.get("eligibility_cue"):
        tags += _tag_html("warn", i18n.cue_label(lang, row["eligibility_cue"]))
    tags += _correction_tag(row, label, lang)
    tags += _salary_chip(pairs, row, lang)
    skills = [text for kind, text in pairs if kind == "skill"]
    skills_html = ""
    if skills:
        shown = " · ".join(skills)  # 由单行剩余宽度决定省略，不按技能个数截断
        skills_html = (f'<span class="skills" title="{_e(i18n.t(lang, "card.skills", list=", ".join(skills)))}">'
                 f'{_e(shown)}</span>')

    tier = next((text for kind, text in pairs if kind == "tier"), None)
    score = row.get("total_score")
    badge_title = (i18n.t(lang, "card.tier_score", tier=tier, score=score) if tier
                   else i18n.t(lang, "card.score"))
    badge = (f'<span class="tier">{_e(tier)}</span><span class="sep">·</span>' if tier else "") + f'<b>{_e(score)}</b>'

    sub = " · ".join(str(x) for x in (
        row.get("company"), row.get("location"), row.get("source")) if x)
    # 技能与进度共用一行：技能按剩余宽度省略，进度保持完整；空间不足时整组换行。
    state = _card_state(uid, label, lang)
    tail = f'<span class="card-tail">{skills_html}{state}</span>' if skills else state
    # 搜索已挪到服务端（见 _search_text），这里不再拼 data-text
    return "".join([
        f'<div class="card{" done" if done else ""}" data-uid="{_e(uid)}" '
        f'role="button" tabindex="0" '
        f'onclick="openJob(\'{_e(uid)}\')" onkeydown="cardKeys(event,\'{_e(uid)}\')">',
        '<div class="card-top">',
        f'<p class="t">{_e(row.get("title"))}</p>',
        f'<span class="score" title="{_e(badge_title)}">{badge}</span>',
        "</div>",
        f'<div class="sub">{_e(sub)}</div>',
        # 进度文字与状态点放在 tags 行内部（不是绝对定位），避免压在标签上
        # 进度文字与圆点是一组，换行时一起走，不会被拆到两行
        f'<div class="tags">{tags}{tail}</div>',
        "</div>",
    ])


def _why_section(pairs, row: dict, lang: str) -> str:
    """「为什么是 T1 · 6 分」：卡片上拿掉的计分信号（层级、级别、经验、规则加减分）
    原样移到这里，默认收起。只换位置，不删证据；文字与报告同源（render.tags_for）。"""
    T = lambda key, **kw: i18n.t(lang, key, **kw)   # noqa: E731
    score = row.get("total_score")
    tier = next((text for kind, text in pairs if kind == "tier"), None)
    items = []
    if tier:
        items.append((T("detail.why_tier"), tier))
    for kind, text in pairs:
        if kind in ("level", "adjust"):
            shown = _tag_text(kind, text, lang)
            name, _, delta = shown.rpartition(" ")
            items.append((name or shown, delta if name else ""))
    rows = "".join(f'<li><span>{_e(name)}</span><b>{_e(value)}</b></li>' for name, value in items)
    if not any(kind in ("level", "adjust") for kind, _ in pairs):
        rows += f'<li class="muted">{_e(T("detail.why_empty"))}</li>'
    title = T("detail.why", tier=tier, score=score) if tier else T("detail.why_plain", score=score)
    return (f'<details class="why"><summary>{_e(title)}</summary>'
            f'<div class="why-body"><p class="hint">{_e(T("detail.why_hint"))}</p>'
            f'<ul class="why-list">{rows}</ul></div></details>')


def _correction_details(row: dict, uid: str, label: dict, lang: str) -> str:
    """低频：资格纠错，默认折叠。摘要行直接给出当前有效结果与来源，
    不展开也知道是「分析结果」还是「人工纠正」。展开只是读，不写任何东西；
    机器默认选中项是虚线描边（CSS 按 data-corrected 区分），人工纠正是实底。"""
    T = lambda key, **kw: i18n.t(lang, key, **kw)   # noqa: E731
    q = row.get("qualification") or labels_mod.qualification(row, label)
    buttons = "".join(_btn(uid, "eligibility", v, q["effective"] == v, TONE.get(v, ""), lang)
                      for v in ("eligible", "ineligible"))
    # 只有人工纠正时才单独列出原分析结果；否则摘要与来源说明已经讲清楚了。
    analysis = (T("qualification.analysis_was",
                  value=i18n.value_label(lang, "eligibility", q["analysis"]))
                if q["corrected"] and q["analysis"] else "")
    return (f'<details class="qualification" data-qualification-uid="{_e(uid)}" '
            f'data-analysis="{_e(q["analysis"] or "")}" '
            f'data-corrected="{str(q["corrected"]).lower()}">'
            f'<summary><span class="q-title">{_e(T("qualification.title"))}</span>'
            f'<span class="q-sum" data-qualification-summary>{_e(_qualification_summary(q, lang))}</span>'
            '</summary>'
            '<div class="q-body">'
            f'<div class="q-row"><div class="segs q-segs" role="group" '
            f'aria-label="{_e(T("qualification.title"))}">{buttons}</div>'
            f'<span class="q-analysis" data-qualification-analysis>{_e(analysis)}</span></div>'
            f'<p class="hint" data-qualification-source>{_e(_qualification_source(q, lang))}</p>'
            '</div></details>')


def _qualification_summary(q: dict, lang: str) -> str:
    """摘要行：当前有效结果 · 来源（panel.js qualificationSummary 同口径）。"""
    T = lambda key, **kw: i18n.t(lang, key, **kw)   # noqa: E731
    if not q["effective"]:
        return T("qualification.sum_unknown")
    value = i18n.value_label(lang, "eligibility", q["effective"])
    return T("qualification.sum_manual" if q["corrected"] else "qualification.sum_machine", value=value)


def _qualification_source(q: dict, lang: str) -> str:
    return i18n.t(lang, _qualification_source_key(q))


def _qualification_source_key(q: dict) -> str:
    """来源说明（panel.js qualificationSourceKey 同口径）。"""
    if q["corrected"]:
        return "qualification.manual"
    if q["analysis"] == "ineligible":
        return "qualification.machine_excluded"
    return "qualification.machine" if q["analysis"] else "qualification.unknown"


def _geo_suggestions(lang: str = "zh") -> str:
    """Custom suggestions for the supported coarse targets: states and capitals."""
    from . import locations
    meta = locations._reference()[0]
    values = ["Australia"] + (["澳大利亚"] if lang == "zh" else []) + list(meta["states"]) + [
        f"{city} {state}" for city, state in meta["cities"].items()]
    opts = "".join(
        f'<button type="button" class="geoopt" role="option" tabindex="-1" '
        f'data-geo-value="{_e(v)}" onmousedown="geoOptionPick(event,this)" '
        f'onkeydown="geoOptionKeys(event,this)">{_e(v)}</button>'
        for v in values
    )
    label = _e(i18n.t(lang, "location.region"))
    return (f'<div class="geo-suggest-panel" id="geo-suggest-panel" role="listbox" '
            f'aria-label="{label}" hidden>{opts}</div>')


def _geo_error(text: str) -> str | None:
    """Translate the parser's rejection into plain guidance; None when valid."""
    from . import locations
    try:
        locations.parse_target(text)
    except ValueError as exc:
        message = str(exc)
        if ("one Australian place" in message or "one Australian postcode" in message
                or "work-mode" in message):
            return "location.geo_error.multiple"
        return "location.geo_error.unsupported"
    return None


def _copy_text(row: dict, lang: str) -> str:
    """「复制岗位」的纯文本：给外部 AI 用，所以取存储的完整 JD，不取渲染后的 DOM。"""
    T = lambda key: i18n.t(lang, key)   # noqa: E731
    lines = [f'{T("copy.uid")}: {row.get("uid") or ""}',
             f'{T("copy.title")}: {row.get("title") or ""}',
             f'{T("copy.company")}: {row.get("company") or ""}',
             f'{T("copy.location")}: {row.get("location") or ""}']
    lines.append(f'{T("copy.url")}: {row.get("url") or ""}')
    body = str(row.get("description") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    lines += ["", f'{T("copy.jd")}:', body or T("detail.jd_empty")]
    return "\n".join(lines) + "\n"


def _detail_html(row: dict, lang: str = "zh", keys: str = i18n.DEFAULT_KEYS) -> str:
    """右侧详情。与 md 报告同口径（都走 render.py），只在样式上更贴合面板。"""
    T = lambda key, **kw: i18n.t(lang, key, **kw)   # noqa: E731
    prev_key, next_key = i18n.KEY_PROFILES.get(keys, i18n.KEY_PROFILES[i18n.DEFAULT_KEYS])
    uid = row["uid"]
    label = row.get("_label") or {}

    # 薪资原文不再进元信息行：它和折算值并排显示在下面的标签行（同一信息只出现一次）
    meta_parts = [row.get("company"), row.get("location"), row.get("source"),
                  T("detail.posted", date=row["listing_date"]) if row.get("listing_date") else None,
                  row.get("work_type")]
    meta = " · ".join(str(x) for x in meta_parts if x)
    if row.get("duplicate_count"):
        meta += " · " + T("detail.dupes", n=row["duplicate_count"],
                          sources=row.get("duplicate_sources") or "")

    # 详情页空间够，技能 tag 全部展开，不折成 +N。计分信号收进底部「为什么是 …」。
    all_pairs = tags_for(row, skill_limit=None)
    tags = ""
    if (age := _age_tag(row, lang)):
        tags += _tag_html(*age)
    salary = next((text for kind, text in all_pairs if kind == "salary"), None)
    if salary:
        tags += _tag_html("salary", _tag_text("salary", salary, lang), _salary_tip(row, lang))
    if (raw := " ".join(str(row.get("salary") or "").split())):
        # 详情给**完整**原文（卡片与报告里的原文标签会截断）
        tags += _tag_html("raw full", i18n.t(lang, "tag.raw", text=raw))
    tags += _tags([(kind, text) for kind, text in all_pairs if kind == "skill"], lang)
    url = _e(row.get("url"))

    # 上一条 / 下一条：列表顺序只有前端知道（页面载入时冻结的卡片顺序），
    # 这里只给按钮，位置与可用状态由 panel.js 的 syncDetailNav 填。
    # 按键提示跟随快捷键方案（cookie jobs_keys），与 panel.js 实际响应的键一致。
    def step_btn(delta: int, text: str) -> str:
        (glyph, aria), name = (next_key, T("detail.next")) if delta > 0 else (prev_key, T("detail.prev"))
        return (f'<button type="button" class="stepbtn" data-step="{delta}" onclick="stepDetail({delta})" '
                f'aria-label="{_e(name)}" aria-keyshortcuts="{aria}" title="{_e(name)} ({_e(glyph)})" disabled>'
                f'{text}</button>')
    out = [
        f'<div data-label-scope data-uid="{_e(uid)}" '
        f'data-scope-label="{_e(json.dumps(_label_state(label), ensure_ascii=False))}">',
        '<div class="d-nav">',
        f'<button type="button" id="back" onclick="closeDetail()">{_e(T("detail.back"))}</button>',
        '<span class="d-steps">',
        step_btn(-1, f'‹ {_e(T("detail.prev"))} <kbd>{_e(prev_key[0])}</kbd>'),
        '<span class="d-pos"></span>',
        step_btn(1, f'{_e(T("detail.next"))} <kbd>{_e(next_key[0])}</kbd> ›'),
        '</span></div>',
        '<div class="d-head"><div class="d-title">',
        f'<h2>{_e(row.get("title"))}</h2>',
        f'<div class="sub">{_e(meta)}</div></div>',
        '<div class="d-actions">'
        # 复制用的完整文本在转义后的属性里：按钮读它，不读渲染后的正文 DOM
        f'<button type="button" class="btn copy-btn" onclick="copyJob(this)" '
        f'title="{_e(T("detail.copy_title"))}" data-copy-text="{_e(_copy_text(row, lang))}">'
        f'{_e(T("detail.copy"))}</button>'
        f'<a class="apply" href="{url}" target="_blank" rel="noopener">{_e(T("detail.open"))} ↗</a>'
        '</div>',
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
        rule_ids = ',<wbr>'.join(
            f'<span class="rule-token">{_e(token)}</span>'
            for token in str(row.get("rule_id") or "").split(",")
        )
        out.append(f'<div class="rule">{_e(T("detail.rule"))}<b>{rule_ids}</b>'
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

    # 标注与备注：桌面原位不动；竖屏（panel.js 的 body.portrait）这一块变成底部抽屉，
    # 正文前置。是同一组节点，不复制 —— 备注输入框只有一个，保存队列与未保存保护照旧。
    out.append(
        f'<button type="button" class="label-scrim" tabindex="-1" aria-label="{_e(T("detail.sheet_close"))}" '
        'onclick="toggleLabelSheet(false)"></button>'
        f'<div class="label-sheet" id="label-sheet" tabindex="-1" aria-label="{_e(T("detail.decide"))}">'
        '<div class="sheet-grab" aria-hidden="true"></div>'
        f'<div class="label-sheet-head"><b>{_e(T("detail.decide"))}</b></div>')
    # 高频：三状态 + 星标同一行，星标更轻；说明只留一句常驻提示。
    buttons = "".join(_btn(uid, "action", v, label.get("action") == v, TONE.get(v, ""), lang)
                      for v in ("saved", "applied", "skipped"))
    out.append(
        '<div class="step-block">'
        f'<div class="step-title"><span class="step">{step}</span>{_e(T("detail.step2"))}'
        f'<span class="spacer"></span><span class="decide-state" data-state-uid="{_e(uid)}" '
        f'role="status" aria-live="polite">{_e(_state_text(label, lang))}</span></div>'
        '<div class="status-row">'
        f'<div class="segs status-segs" role="group" aria-label="{_e(T("field.action"))}">{buttons}</div>'
        f'{_star_btn(uid, bool(label.get("priority")), lang)}</div>'
        f'<p class="hint decide-hint">{_e(T("help.action"))}</p></div>')
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
    out.append(_correction_details(row, uid, label, lang))
    out.append(f'<div class="label-sheet-foot"><button type="button" class="primary-btn" '
               f'onclick="toggleLabelSheet(false)">{_e(T("detail.sheet_done"))}</button></div>'
               '</div>')
    out.append("</section>")

    out.append(f'<h3 class="jd-head">{_e(T("detail.jd"))} <span>· {_e(T("detail.jd_note"))}</span></h3>')
    out.append(f'<div class="jd">{render_jd_html(row.get("description"), T("detail.jd_empty"))}</div>')
    out.append(_why_section(all_pairs, row, lang))
    # 竖屏时固定在底部的操作栏（桌面隐藏；顶部那个按钮在竖屏隐藏）：
    # 上一条 · 标注抽屉（显示处理状态与星标）· 打开原文 · 下一条
    summary = _state_text(label, lang)
    out.append('<div class="apply-bar">'
               + step_btn(-1, "‹")
               + f'<button type="button" class="label-toggle" aria-expanded="false" '
                 f'aria-controls="label-sheet" onclick="toggleLabelSheet()">'
                 f'<b>{_e(T("detail.labels"))} ▴</b><span class="label-sum">{_e(summary)}</span></button>'
               + f'<a class="apply" href="{url}" target="_blank" rel="noopener">{_e(T("detail.open"))} ↗</a>'
               + step_btn(1, "›")
               + '</div>')
    out.append('</div>')
    return "".join(out)


# ---------------------------------------------------------------------------
# 顶栏、筛选
# ---------------------------------------------------------------------------

STALE_AFTER_DAYS = 2


def _last_run_note(conn, lang: str = "zh", config: dict | None = None) -> str:
    """Latest fetch/run fact; analyze and report never make data look fresh."""
    from . import run_state
    T = lambda key, **kw: i18n.t(lang, key, **kw)
    if config is None:
        config = {"db_path": conn.execute("PRAGMA database_list").fetchone()[2]}
    fact = run_state.latest(config, conn)
    stamp = fact.get("finished_at")
    state = fact.get("state", "idle")
    stale = False
    if state == "running":
        text = T("js.update_running", stage=T("js.update_stage_" + (fact.get("stage") or "wait")))
    elif not stamp:
        text = T("run.never")
    else:
        try:
            then = datetime.fromisoformat(stamp)
            if then.tzinfo is None:
                then = then.replace(tzinfo=timezone.utc)
            seconds = max(0, (datetime.now(timezone.utc) - then).total_seconds())
            unit, n = ("minutes", max(1, int(seconds // 60))) if seconds < 3600 else (("hours", int(seconds // 3600)) if seconds < 86400 else ("days", int(seconds // 86400)))
            text = T("run.ago", ago=T("ago." + unit, n=n))
            stale = seconds >= STALE_AFTER_DAYS * 86400
            if stale:
                text += T("run.stale")
        except ValueError:
            text = T("run.at", stamp=stamp)
        if state != "success":
            # 状态行只放简短结果；完整说明在设置页。「结果未记录」保持未知，不写成成功。
            short = "run.state_" + state
            text += " · " + T(short if short in i18n.STRINGS[i18n.DEFAULT_LANG] else "js.update_" + state)
    dot = '<span class="dot bad" aria-hidden="true"></span>' if stale else ""
    return (f'<span class="run{" stale" if stale else ""}" data-run-time="{_e(stamp or "")}" '
            f'data-run-state="{_e(state)}">{dot}<span>{_e(text)}</span></span>')


def filter_layout(view: str) -> tuple[tuple[str, ...], dict[str, list[str]]]:
    """(常驻筛选, {"label": [...], "job": [...]})。

    后端仍兼容旧兴趣筛选，但当前界面不宣传、不传播、不展示该轴。
    """
    allowed = tuple(k for k in VIEW_FILTER_KEYS.get(view, ALL_FILTER_KEYS)
                    if k not in INTERFACE_HIDDEN_KEYS)
    primary = tuple(k for k in PRIMARY_FILTERS.get(view, DEFAULT_PRIMARY) if k in allowed)
    rest = [k for k in allowed if k not in primary and k not in TOOLBAR_FIXED]
    label = [k for k in LABEL_GROUP_KEYS if k in rest]
    return primary, {"label": label, "job": [k for k in rest if k not in label]}


def _is_active(key: str, filters: dict) -> bool:
    value = filters.get(key)
    if key == "labeled":
        return value in ("yes", "no")
    return bool(value)


def _select(key: str, label: str, options: list[tuple[str, str]], current: str, *,
            active: bool | None = None) -> str:
    """A single-choice control with the same custom popover language as skills."""
    current_label = next((text for value, text in options if value == current), label)
    on = bool(current) if active is None else active
    option_html = "".join(
        f'<button type="button" class="choiceopt{" sel" if value == current else ""}" '
        f'role="option" tabindex="-1" data-choice-value="{_e(value)}" '
        f'aria-selected="{"true" if value == current else "false"}" '
        f'onclick="choicePick(this)" onkeydown="choiceOptionKeys(event,this)">'
        f'<span class="choice-label">{_e(text)}</span>'
        f'<span class="choice-check" aria-hidden="true">✓</span></button>'
        for value, text in options
    )
    panel_id = f"choice-panel-{key}"
    return (
        f'<div class="choicepick" data-choice-default="{_e(options[0][0])}">'
        f'<button type="button" class="ctl{" on" if on else ""}" data-fkey="{key}" '
        f'data-choice-trigger value="{_e(current)}" aria-haspopup="listbox" '
        f'aria-expanded="false" aria-controls="{panel_id}" aria-label="{_e(label)}" '
        f'onclick="choiceToggle(event,this)" onkeydown="choiceTriggerKeys(event,this)">'
        f'<span class="k">{_e(label)}</span><span class="v">{_e(current_label)}</span>'
        f'<span class="caret" aria-hidden="true">▾</span></button>'
        f'<div class="choicepanel" id="{panel_id}" role="listbox" aria-label="{_e(label)}" hidden>'
        f'{option_html}</div></div>'
    )


def _toggle(key: str, label: str, checked: bool, value: str | None = None) -> str:
    # 无 value 的复选框会以 "on" 进 URL；严格取值的开关（如资格不符置后）显式给 "1"。
    value_attr = f' value="{_e(value)}"' if value is not None else ""
    return (f'<label class="ctl tog{" on" if checked else ""}">'
            f'<input type="checkbox" data-fkey="{key}"{value_attr}{" checked" if checked else ""} '
            f'onchange="filterChanged()"> {_e(label)}</label>')


def _skill_picker(filters: dict, counts: dict, lang: str = "zh", skill_labels=None) -> str:
    """技能多选。**默认一个都不勾、面板收起** —— 相当一部分岗位一个技术词
    都没有，默认开启会静默藏掉它们。按实际命中数降序，只露头部
    （SKILL_PICK_TOP），长尾交给搜索框。已选中的一定出现，否则取消不掉。"""
    if not counts:
        return ""
    skill_labels = skill_labels or {}
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
            f'onchange="filterChanged()">'
            f'<span>{_e(skill_labels.get(name, name))}</span>'
            f'<span class="skc">{counts.get(name, 0)}</span></label>')
    title = i18n.t(lang, "filter.skill")
    value = ", ".join(skill_labels.get(name, name) for name in selected) if selected else i18n.t(lang, "filter.any")
    return (f'<div class="skillpick" id="skillpick">'
            f'<button type="button" class="ctl{" on" if selected else ""}" aria-haspopup="true" '
            f'title="{_e(i18n.t(lang, "filter.skill_hint"))}" onclick="skillToggle(event)">'
            f'<span class="k">{_e(title)}</span> <span class="v">{_e(value)}</span> <span class="caret" aria-hidden="true">▾</span></button>'
            f'<div class="skillpanel" id="skill-panel">{"".join(opts)}</div></div>')


def _tier_select(filters: dict, lang: str = "zh") -> str:
    """层级下拉。层级本来就已算好，但此前只能靠搜 t1 —— 那是拿 T1 的唯一入口。
    补一个真正的下拉之后，t1 退化成快捷方式。"""
    options = [("", i18n.t(lang, "filter.any"))] + [(v, v) for v in TIER_LABELS]
    return _select(TIER_KEY, i18n.t(lang, "filter.tier"), options, filters.get(TIER_KEY, ""))


def _control(key: str, filters: dict, skill_counts: dict, lang: str, skill_labels=None) -> str:
    T = lambda k, **kw: i18n.t(lang, k, **kw)   # noqa: E731
    if key == "geo":
        error = _geo_error(filters["geo"]) if filters.get("geo") else None
        invalid = ' aria-invalid="true" aria-describedby="geo-error"' if error else ""
        return (f'<div class="geo-control{" invalid" if error else ""}"><label class="ctl{" on" if filters.get("geo") else ""}" '
                f'title="{_e(T("location.region_title"))}">'
                f'<span class="k">{_e(T("location.region"))}</span>'
                f'<input data-fkey="geo" value="{_e(filters.get("geo", ""))}" '
                f'placeholder="{_e(T("location.region_hint"))}" aria-label="{_e(T("location.region"))}"{invalid} '
                'aria-autocomplete="list" aria-controls="geo-suggest-panel" aria-expanded="false" '
                'autocomplete="off" spellcheck="false" enterkeyhint="search" '
                'onfocus="geoFocus(this)" oninput="geoInput(this)" onkeydown="geoKeys(event)" '
                'oncompositionstart="imeStart(this)" oncompositionend="imeEnd(this)"></label>'
                f'<button type="button" class="btn geo-apply" onclick="applyGeo()" aria-label="{_e(T("location.apply"))}">'
                f'{_e(T("location.apply_short"))}</button>{_geo_suggestions(lang)}</div>')
    if key in FILTER_KEYS:
        options = ([("", T("filter.any"))]
                   + [(v, i18n.value_label(lang, key, v)) for v in labels_mod.VALID[key]]
                   + [(NONE_TOKEN, T(f"filter.none.{key}"))])
        return _select(key, T(f"filter.field.{key}"), options, filters.get(key, ""))
    if key == "salary":
        return _toggle("salary", T("filter.salary"), bool(filters.get("salary")))
    if key == FRESH_KEY:
        return _toggle(FRESH_KEY, T("filter.fresh"), bool(filters.get(FRESH_KEY)))
    if key == PRIORITY_KEY:
        return _toggle(PRIORITY_KEY, T("filter.priority"), bool(filters.get(PRIORITY_KEY)))
    if key == INELIGIBLE_LAST_KEY:
        return _toggle(INELIGIBLE_LAST_KEY, T("filter.ineligible_last"),
                       bool(filters.get(INELIGIBLE_LAST_KEY)), "1")
    if key == SKILL_KEY:
        return _skill_picker(filters, skill_counts, lang, skill_labels)
    if key == TIER_KEY:
        return _tier_select(filters, lang)
    if key == "labeled":
        current = filters.get("labeled") if filters.get("labeled") in ("yes", "no") else ""
        options = [("", T("labeled.any")), ("no", T("labeled.no")), ("yes", T("labeled.yes"))]
        return _select("labeled", T("filter.labeled"), options, current)
    return ""


def _chip_text(key: str, filters: dict, lang: str, skill_labels=None) -> str:
    T = lambda k, **kw: i18n.t(lang, k, **kw)   # noqa: E731
    colon = T("js.colon")
    value = filters.get(key)
    if key == "geo":
        return T("location.region") + colon + str(value)
    if key in FILTER_KEYS:
        shown = T(f"filter.none.{key}") if value == NONE_TOKEN else i18n.value_label(lang, key, value)
        return T(f"filter.field.{key}") + colon + shown
    if key == "labeled":
        return T("filter.labeled") + colon + T(f"labeled.{value}")
    if key == "salary":
        return T("filter.salary_chip")
    if key == FRESH_KEY:
        return T("filter.fresh")
    if key == PRIORITY_KEY:
        return T("filter.priority")
    if key == INELIGIBLE_LAST_KEY:
        return T("filter.ineligible_last")
    if key == TIER_KEY:
        return T("filter.tier") + colon + str(value)
    if key == SKILL_KEY:
        return T("filter.skill") + colon + ", ".join((skill_labels or {}).get(name, name) for name in value or [])
    return key


def _filter_bar(view: str, filters: dict, skill_counts: dict, lang: str = "zh", skill_labels=None) -> str:
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
    counted = [k for k in allowed
               if k not in TOOLBAR_FIXED and k not in INTERFACE_HIDDEN_KEYS]
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
    out.extend(_control(k, filters, skill_counts, lang, skill_labels) for k in primary)
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
                           [(v, T(f"sort.{v}")) for v in SORT_KEYS], sort, active=False))
    out.append("</div>")
    if any(k != SINCE_KEY for k in filters):
        out.append(f'<a class="clear" href="#" onclick="clearFilter();return false;">'
                   f'{_e(T("filter.clear_all"))}</a>')
    out.append("</div>")

    # 「更多筛选」面板：桌面是工具栏下方的展开区，竖屏是底部抽屉。
    # 底部的批量操作按钮只由 CSS 在竖屏显示。
    panel_class = "more-panel" + ("" if hidden_keys else " sheet-only")
    out.append(f'<div class="{panel_class}" id="more-panel" data-keys="{" ".join(hidden_keys)}" hidden>'
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
                   + "".join(_control(k, filters, skill_counts, lang, skill_labels) for k in keys)
                   + "</div></fieldset>")
    clear_hidden = (f'<button type="button" class="linkbtn" onclick="clearHidden()">'
                    f'{_e(T("filter.clear_hidden"))}</button>') if hidden_keys else ""
    out.append(f'<div class="more-foot">{clear_hidden}'
               f'<button type="button" class="primary-btn" id="more-done" onclick="moreDone()">'
               f'{_e(T("filter.done"))}</button></div></div>')

    if hidden_active:
        chips = "".join(
            f'<span class="achip">{_e(_chip_text(k, filters, lang, skill_labels))}'
            f'<button type="button" aria-label="{_e(T("filter.remove"))}: {_e(_chip_text(k, filters, lang, skill_labels))}" '
            f'onclick="removeFilter(\'{k}\')">×</button></span>'
            for k in hidden_active)
        out.append(f'<div class="chips" id="active-chips"><span class="chips-label">'
                   f'{_e(T("filter.active"))}</span>{chips}</div>')
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


# 语言与外观：一个「显示」小菜单（顶栏）、手机抽屉和设置页用**同一组选项、同一顺序**：
# 跟随 → 中文/浅色 → English/深色。「跟随」= 清掉 cookie，不写任何值（见 panel.js setPref）。
LANG_CHOICES = ("auto", "zh", "en")
THEME_CHOICES = ("auto", "light", "dark")


def _lang_choice_label(lang: str, code: str) -> str:
    # 语言名用各自的语言写（「中文」「English」），切过去的人才认得
    return {"zh": "中文", "en": "English"}.get(code) or i18n.t(lang, "set.lang_auto")


def _display_choices(lang: str, lang_pref: str, prefix: str) -> str:
    """语言 + 外观两组分段按钮。外观的选中态由 panel.js syncThemeUi 按 data-theme-choice 同步。"""
    T = lambda key: i18n.t(lang, key)   # noqa: E731
    langs = "".join(
        f'<button type="button" class="{"on" if code == lang_pref else ""}" data-lang-choice="{code}" '
        f'aria-pressed="{"true" if code == lang_pref else "false"}" onclick="setLang(\'{code}\')">'
        f'{_e(_lang_choice_label(lang, code))}</button>' for code in LANG_CHOICES)
    themes = "".join(
        f'<button type="button" data-theme-choice="{code}" onclick="setTheme(\'{code}\')">'
        f'{_e(T("set.theme_" + code))}</button>' for code in THEME_CHOICES)
    return (f'<div class="drawer-sec"><span id="{prefix}-lang">{_e(T("set.lang_title"))}</span>'
            f'<div class="segrow" role="group" aria-labelledby="{prefix}-lang">{langs}</div></div>'
            f'<div class="drawer-sec"><span id="{prefix}-theme">{_e(T("set.theme_title"))}</span>'
            f'<div class="segrow" role="group" aria-labelledby="{prefix}-theme">{themes}</div></div>')


def _display_menu(lang: str, lang_pref: str) -> str:
    """顶栏的「Aa 显示 ▾」：低频设置收成一个按钮，弹出层就是 _display_choices。"""
    T = lambda key: i18n.t(lang, key)   # noqa: E731
    return (f'<div class="display-menu" id="display-menu">'
            f'<button type="button" class="iconbtn display-btn" id="display-btn" aria-expanded="false" '
            f'aria-controls="display-pop" aria-label="{_e(T("display.label"))}" onclick="toggleDisplay()">'
            f'<span class="aa" aria-hidden="true">Aa</span><span>{_e(T("display.button"))}</span>'
            f'<span class="caret" aria-hidden="true">▾</span></button>'
            f'<div class="display-pop" id="display-pop" role="group" aria-label="{_e(T("display.label"))}" hidden>'
            f'{_display_choices(lang, lang_pref, "dm")}'
            f'<p class="display-more">{_e(T("display.more"))}</p></div></div>')


def _scripts(lang: str, extra: dict | None = None) -> str:
    data = {"__I18N__": i18n.js_strings(lang), "__LANG__": lang,
            "__STALE_AFTER_DAYS__": STALE_AFTER_DAYS}
    data.update(extra or {})
    return "".join(f"<script>window.{k}={_js(v)};</script>" for k, v in data.items())


# 顶部统计的稳定键（= i18n 的 stat.* 后缀）-> labels.summary 的口径。
# 页面渲染与只读接口 /api/stats 共用这一张表：标注后前端按键重拉，不自行加减。
STAT_KEYS = {"processed": "已处理", "saved": "待投", "priority": "优先待投",
             "applied": "已投递", "labelled": "已标注",
             "miss": "漏杀候选", "false_excl": "误杀候选"}
# 顶栏与抽屉共用的处理口径：只有三种主状态算处理；「已标注」是另一回事，
# 只在已标注视图里与「已处理」并列，避免把有备注 / 星标的未处理岗位当成做完了。
PROCESS_STATS = ("processed", "saved", "priority", "applied")


def _stats(view: str, stats: dict, total: int, lang: str) -> list[tuple[str, object, str | None]]:
    """(显示名, 数值, 统计键)。统计键为 None 的项（本批）不随标注变化。"""
    def stat(key):
        return (i18n.t(lang, "stat." + key), stats[STAT_KEYS[key]], key)
    common = [stat(k) for k in PROCESS_STATS]
    if view == "new":
        return [(i18n.t(lang, "stat.batch"), total, None)] + common
    if view == "excluded":
        return [stat("miss"), stat("false_excl")]
    if view == "labelled":
        return [stat("labelled")] + common
    return common


def _stat_attr(key: str | None) -> str:
    return f' data-stat="{key}"' if key else ""


def _filters_for_view(view: str, filters: dict) -> dict:
    allowed = set(VIEW_FILTER_KEYS.get(view, ALL_FILTER_KEYS))
    return {key: value for key, value in filters.items()
            if (key in allowed and key not in INTERFACE_HIDDEN_KEYS) or key == SINCE_KEY}


def render_page(conn, config: dict, view: str, offset: int, limit: int,
                started: float | None = None, filters: dict | None = None,
                lang: str = "zh", theme: str | None = None, lang_pref: str = "auto") -> str:
    """渲染整页。

    `started` 现在**不在这里用**（页脚已删），但保留参数：它和
    `Handler._send(started=…)` 是同一个起点，Server-Timing 响应头与
    data/panel.log 的耗时都靠它。删掉参数容易让人顺手把计时链路一起删了。
    """
    T = lambda k, **kw: i18n.t(lang, k, **kw)   # noqa: E731
    from . import run_state
    result_generation = run_state.generation(config)
    filters = filters or {}
    since = _resolve_since(conn, view, filters)
    # 每次请求重读词表：词表文件很小，开销可忽略。换来的是「改了词表刷新即生效」，
    # 不用重启常驻面板（这也是把词表放数据里而不是代码里的意义）。
    skills_cfg = skills_mod.load_skills(config.get("skills_path"))
    # An unreadable region shows guidance in the list instead of a bare error page;
    # the filter keeps its meaning (nothing is matched against an unknown target).
    geo_error = _geo_error(filters["geo"]) if filters.get("geo") else None
    page, total = (([], 0) if geo_error else
                   _query(conn, config, view, offset, limit, filters, since, skills_cfg))
    # A new batch can shrink the filtered result set. Stay in this view and
    # use its last available page instead of showing an empty stale offset.
    if total and offset >= total:
        offset = ((total - 1) // limit) * limit
        page, total = _query(conn, config, view, offset, limit, filters, since, skills_cfg)
    stats = labels_mod.summary(conn)
    skill_counts = database.skill_counts(conn)

    # nav 用不带 since 的链接：换视图 = 重新冻结（推荐视图会重新取未标注）。
    # 点击由 gotoView() 接管 —— 它会补上该视图自己记住的筛选条件；
    # href 保持干净是为了修饰键/中键还能正常开新标签。
    nav = "".join(
        f'<a class="tab{" on" if key == view else ""}" href="{_link(key, 0, _filters_for_view(key, filters))}"'
        f'{CURRENT if key == view else ""}'
        f' onclick="return gotoView(\'{key}\',event)">{_e(T("view." + key))}</a>'
        for key in VIEWS)
    drawer_views = "".join(
        f'<a class="dv{" on" if key == view else ""}" href="{_link(key, 0, _filters_for_view(key, filters))}"'
        f'{CURRENT if key == view else ""}'
        f' onclick="return gotoView(\'{key}\',event)"><b>{_e(T("view." + key))}</b>'
        f'<small>{_e(T("viewhint." + key))}</small></a>'
        for key in VIEWS)

    embed = {r["uid"]: _label_state(r.get("_label") or {}) for r in page}
    # Priority has its own control; retain legacy note translations for reading.
    note_tags = [tag for tag in labels_mod.note_tag_candidates(conn) if tag != "优先投递"]
    # 按字母排：详情页的候选不再按输入过滤，排序是唯一的"怎么找"。
    # 「已被占用」的预判直接用后端的 canonical_tokens（同一份小写口径）；
    # 用 [token, 规范名] 数组而不是对象，避免 __proto__ 之类的词撞原型。
    skill_embed = [{"name": e.get("name"), "display_name": skills_mod.display_name(e), "aliases": e.get("aliases") or []}
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
    stats_html = "".join(f'<span>{_e(name)} <b{_stat_attr(key)}>{_e(n)}</b></span>'
                         for name, n, key in stat_items)
    run_note = _last_run_note(conn, lang, config)
    has_filters = any(k != SINCE_KEY for k in filters)
    if geo_error:
        cards = (f'<div class="empty geo-empty" id="geo-error" role="alert">'
                 f'<b>{_e(T("location.geo_invalid", geo=filters["geo"]))}</b>'
                 f'<span>{_e(T(geo_error))}</span>'
                 f'<button type="button" class="btn" onclick="removeFilter(\'geo\')">'
                 f'{_e(T("location.clear_geo"))}</button></div>')
    elif page:
        # One divider wherever the displayed block changes: the location tier
        # and/or the "ineligible last" zone. Neither option: no dividers.
        parts, previous = [], None
        for row in page:
            block = (row.get("_ineligible_last"),
                     (row.get("_geo_match") or "unknown") if filters.get("geo") else None)
            if previous is not None and block != previous:
                parts.append('<hr class="geo-divider" aria-hidden="true">')
            parts.append(_card(row, since, lang))
            previous = block
        cards = "".join(parts)
    elif has_filters:
        cards = (f'<div class="empty"><b>{_e(T("list.empty_filtered"))}</b>'
                 f'<span>{_e(T("list.empty_hint"))}</span>'
                 f'<button type="button" class="btn" onclick="clearFilter()">'
                 f'{_e(T("filter.clear_all"))}</button></div>')
    else:
        cards = f'<div class="placeholder">{_e(T("list.empty"))}</div>'

    drawer_stats = "".join(f'<div><b{_stat_attr(key)}>{_e(stats[STAT_KEYS[key]])}</b>'
                           f'{_e(T("stat." + key))}</div>'
                           for key in PROCESS_STATS)

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
        # 上次抓取 + 「更新岗位」放在一起；竖屏时 panel.js 把更新按钮挪进抽屉
        f'<span class="run-controls">{run_note}<span id="update-home" hidden></span>'
        f'{_update_widget(lang, compact=True)}</span>',
        _display_menu(lang, lang_pref),
        f'<a class="iconbtn setlink" href="/settings" title="{_e(T("nav.settings"))}">'
        f'{ICON_GEAR}<span>{_e(T("nav.settings"))}</span></a>',
        "</div></div>",
        _filter_bar(view, filters, skill_counts, lang, skills_mod.display_names(skills_cfg)),
        "</header>",
        '<div class="ctxbar">',
        f'<div class="ctx-desc">{ICON_INFO}<div><span class="ctx-text">'
        f'<b>{_e(T("view." + view))}</b>{_e(T("js.colon"))}{_e(T("viewdesc." + view))}</span>'
        f'{_search_explanation(filters, skills_cfg, total, lang)}</div></div>',
        f'<div class="ctx-stats">{stats_html}'
        f'<span>{_e(T("list.count", n=MARK, total=total)).replace(MARK, f"<b id=count>{len(page)}</b>")}</span></div>',
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
        f'<div class="drawer-run">{run_note}<div id="drawer-update-slot"></div></div>',
        _display_choices(lang, lang_pref, "dw"),
        f'<a class="drawer-link" href="/settings">{ICON_GEAR}<span>{_e(T("nav.settings"))}</span>'
        f'<small>{_e(T("nav.settings_hint"))} ›</small></a>',
        "</nav>",
        f'<button type="button" class="scrim" aria-label="{_e(T("nav.menu_close"))}" onclick="closeDrawer()"></button>',
        "</div>",
        '<div class="sheet-scrim" id="sheet-scrim" onclick="toggleMore(false)"></div>',
        '<div class="toast" id="toast" role="status" aria-live="polite"></div>',
        '<div class="note-menu" id="note-menu"></div>',
        _update_confirm(lang),
        _copy_dialog(lang),
        _scripts(lang, {"__LABELS__": embed, "__VIEW__": view, "__NOTE_TAGS__": note_tags,
                        "__NOTE_TAG_LABELS__": i18n.note_tag_labels(lang),
                        "__SKILLS__": skill_embed,
                        "__SKILL_OWNERS__": sorted(skills_mod.canonical_tokens(skills_cfg).items()),
                        "__RESULT_GENERATION__": result_generation}),
        f"<script>{asset('panel.js')}</script>",
        "</body></html>",
    ])


# ---------------------------------------------------------------------------
# 设置页
# ---------------------------------------------------------------------------

# 「资格与规则」从「抓取与更新」里独立出来：它决定哪些岗位被排除，与抓取无关。
# 未指定或未知分区时进入第一个分区：资格与规则。
SETTINGS_SECTIONS = ("rules", "fetch", "skills", "display")
# 配置编辑器（settings.js）按分区只显示自己的那几组；写入与冲突检测是同一套。
CONFIG_GROUPS = {"rules": "rules", "fetch": "queries seek indeed linkedin"}

def render_settings(conn, config: dict, lang: str = "zh", theme: str | None = None,
                    section: str = SETTINGS_SECTIONS[0], lang_pref: str = "auto",
                    keys: str = i18n.DEFAULT_KEYS) -> str:
    """设置页：左分类、右详情，页面居中留白（LinkedIn 设置那种）。

    技术栈列表交给前端用 GET /api/skills 的数据渲染，服务端不重复写一套。
    """
    T = lambda k, **kw: i18n.t(lang, k, **kw)   # noqa: E731
    if section not in SETTINGS_SECTIONS:
        section = SETTINGS_SECTIONS[0]
    nav = "".join(
        f'<a class="set-nav-item{" on" if key == section else ""}" '
        f'href="/settings?section={key}"{CURRENT if key == section else ""}>'
        f'{_e(T("set.rules_tab" if key == "rules" else "set." + key))}</a>'
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
    elif section == "rules":
        pane = "".join([
            '<div class="config-title-line">'
            f'<h2>{_e(T("set.rules_title"))}</h2><span id="config-actions"></span></div>',
            f'<p class="set-hint">{_e(T("set.rules_hint"))}</p>',
            f'<div id="config-editor" data-config-groups="{CONFIG_GROUPS["rules"]}"></div>',
        ])
        extra = {}
    elif section == "fetch":
        pane = "".join([
            '<div class="config-title-line">'
            f'<h2>{_e(T("set.fetch_title"))}</h2><span id="config-actions"></span></div>',
            f'<div id="config-editor" data-config-groups="{CONFIG_GROUPS["fetch"]}"></div>',
            f'<h2 class="h2-gap">{_e(T("js.update_title"))}</h2>',
            f'<p class="set-hint">{_e(T("set.update_hint"))}</p>',
            _update_widget(lang),
            _update_confirm(lang),
        ])
        extra = {}
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
            radios("theme", theme or "auto", [("auto", T("set.theme_auto"), T("set.theme_auto_desc")),
                                              ("light", T("set.theme_light"), ""),
                                              ("dark", T("set.theme_dark"), "")], "setTheme"),
            f'<h2 class="h2-gap">{_e(T("set.keys_title"))}</h2>',
            f'<p class="set-hint">{_e(T("set.keys_desc"))}</p>',
            radios("keys", keys, [(code, T("set.keys_" + code), T(f"set.keys_{code}_desc"))
                                  for code in i18n.KEY_PROFILES], "setKeys"),
            f'<div class="keep"><b>{_e(T("set.keep_title"))}</b><ul>'
            f'<li>{_e(T("set.keep1"))}</li><li>{_e(T("set.keep2"))}</li><li>{_e(T("set.keep3"))}</li></ul></div>',
        ])
        extra = {}

    from . import run_state
    extra["__SETTINGS_GENERATION__"] = run_state.generation(config)
    return "".join([
        _head(lang, theme, f'{T("settings.title")} · {T("app.title")}'
              + _demo_suffix(config, lang), "settings"),
        _demo_banner(config, lang),
        '<header class="set-header">'
        f'<a class="backlink" href="/" title="{_e(T("nav.back"))}" aria-label="{_e(T("nav.back"))}">'
        f'{ICON_BACK}</a>'
        f'<h1>{_e(T("settings.title"))}</h1><span class="spacer"></span>',
        # 显示分区本身就是这些选项，顶栏不再重复一份
        "" if section == "display" else _display_menu(lang, lang_pref),
        '</header>',
        '<main class="set-wrap">',
        f'<nav class="set-nav" aria-label="{_e(T("set.sections"))}">{nav}</nav>',
        f'<section class="set-pane{" set-pane-scroll" if section != "skills" else ""}">{pane}</section>',
        "</main>",
        '<div class="modal" id="sk-modal" hidden></div>',
        '<div class="modal" id="config-modal" hidden></div>',
        '<div class="modal config-confirm" id="config-confirm" hidden></div>',
        '<div class="toast" id="toast" role="status" aria-live="polite"></div>',
        _scripts(lang, extra),
        f"<script>{asset('panel.js')}</script>",
        f"<script>{asset('settings.js')}</script>",
        "</body></html>",
    ])
