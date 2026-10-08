"""面板的数据与筛选口径：视图、筛选参数、排序、搜索、分页切片、设置页数据。

从 panel.py 拆出（纯搬移）。这里**不产出 HTML**：HTML 在 panel_views.py，
HTTP 在 panel.py。业务含义（池子、排序、混样、过期判定）一行未改。
"""

from __future__ import annotations

import datetime as dt
from urllib.parse import urlencode

from . import db as database
from . import labels as labels_mod
from . import report as report_mod
from . import skills as skills_mod
from .render import tags_for

# 「推荐」是默认入口：未标注 + 分数优先 + 混 10% 低分岗（校准）。
# 「全部」是全库（含被规则排除的），完整筛选都在那里。
VIEWS = {"recommend": "推荐", "all": "全部", "new": "新增",
         "excluded": "被排除", "labelled": "已标注"}
VIEW_ALIASES = {"kept": "recommend"}     # 旧链接还能用


FILTER_KEYS = ("eligibility", "interest", "action")
NONE_TOKEN = "none"          # 筛「未标注这一项」
LABELED_ANY = "any"          # 「已标注 + 未标注都要」。**显式**选择，
                             # 用来区分「用户要看全部」和「没传参数」
SORT_KEYS = {"score": "分数", "date": "发布时间"}
LABELED_LABELS = {LABELED_ANY: "全部", "no": "未标注", "yes": "已标注"}
QUERY_KEY = "q"
FRESH_KEY = "fresh"          # 「只看 30 天内的」筛选
AGE_WARN_DAYS = 30           # 挂出超过这么久：不进推荐 + 标红（见 age_tag）
PRIORITY_KEY = "priority"    # 「优先投递」筛选（备注里带这个标签）
PRIORITY_TAG = "优先投递"     # 与 labels.NOTE_TAG_DEFAULTS 里的词一致
SKILL_KEY = "skill"          # 技能多选。值为规范名，可在 URL 里重复出现
TIER_KEY = "tier"            # 层级下拉（T1/T2/T3）—— 把 t1 搜索从唯一入口降级
TIER_LABELS = ("T1", "T2", "T3")
SKILL_PICK_TOP = 18          # 技能筛选器展示多少个（按实际命中数降序）
SINCE_KEY = "since"          # 视图的「冻结时间」，见 _resolve_since

# 每个视图给哪些筛选控件。
# 推荐视图只留搜索 + 薪资：它默认就是「未标注、分数优先」，
# 把可投性/意向/进度摆出来只会让人以为还有别的口径；排序和「显示未标注」
# 对它同样没有意义。要看完整筛选去「全部」。
ALL_FILTER_KEYS = (QUERY_KEY, "eligibility", "interest", "action", "salary",
                   FRESH_KEY, SKILL_KEY, TIER_KEY, "sort", "labeled")
# 推荐视图 = 搜索 + 薪资 + 技能 + 层级。原来只有前两个，是因为
# 可投性/意向/进度会让人以为还有别的口径；技能与层级不是口径，
# 是「把这一页缩到我想读的」，正是推荐视图最需要的。层级下拉同时把
# t1 搜索（此前是拿 T1 的唯一入口）降级成快捷方式。
# 「已标注」里再给「是否显示已标注」毫无意义（整页都标过），换成「优先投递」：
# 筛出备注里带「优先投递」的那批，这才是标注攒下来之后真正要用的队列。
LABELLED_FILTER_KEYS = (tuple(k for k in ALL_FILTER_KEYS if k != "labeled")
                        + (PRIORITY_KEY,))
VIEW_FILTER_KEYS = {
    "recommend": (QUERY_KEY, "salary", SKILL_KEY, TIER_KEY),
    "all": ALL_FILTER_KEYS,
    "new": ALL_FILTER_KEYS,
    "excluded": ALL_FILTER_KEYS,
    "labelled": LABELLED_FILTER_KEYS,
}


def parse_filters(qs) -> dict:
    """从 query string 解析筛选条件。空值 = 不筛。"""
    out = {}
    for key in FILTER_KEYS:
        value = (qs.get(key) or [""])[0]
        if value:
            out[key] = value
    if (qs.get("salary") or [""])[0]:
        out["salary"] = "1"
    if (qs.get(FRESH_KEY) or [""])[0]:
        out[FRESH_KEY] = "1"
    if (qs.get(PRIORITY_KEY) or [""])[0]:
        out[PRIORITY_KEY] = "1"
    skills = [v for v in (qs.get(SKILL_KEY) or []) if v]
    if skills:
        out[SKILL_KEY] = skills[:20]        # 多选也要兜一下超长 URL
    tier = (qs.get(TIER_KEY) or [""])[0]
    if tier in TIER_LABELS:
        out[TIER_KEY] = tier
    sort = (qs.get("sort") or [""])[0]
    if sort in SORT_KEYS:
        out["sort"] = sort
    labeled = (qs.get("labeled") or [""])[0]
    if labeled in ("yes", "no", LABELED_ANY):
        out["labeled"] = labeled
    q = (qs.get(QUERY_KEY) or [""])[0].strip()
    if q:
        out[QUERY_KEY] = q[:120]          # 长度兜一下，避免超长 URL
    since = (qs.get(SINCE_KEY) or [""])[0]
    if since:
        out[SINCE_KEY] = since[:40]
    return out


def passes_filters(row: dict, label: dict, filters: dict,
                   has_label: bool | None = None) -> bool:
    if not filters:
        return True
    labeled = filters.get("labeled")
    if labeled in ("yes", "no"):
        # 「有没有标过」看的是整个去重组（report.has_labels），不是这一条 uid
        if ("yes" if has_label else "no") != labeled:
            return False
    for key in FILTER_KEYS:
        want = filters.get(key)
        if not want:
            continue
        have = label.get(key)
        if want == NONE_TOKEN:
            if have:
                return False
        elif have != want:
            return False
    if filters.get("salary") and not (row.get("salary") or "").strip():
        return False
    if filters.get(FRESH_KEY) and is_stale(row):
        return False
    if filters.get(PRIORITY_KEY):
        # 「优先投递」是备注里的标签，不是独立字段（见 labels.note_tags）
        if PRIORITY_TAG not in labels_mod.note_tags(label.get("note")):
            return False
    if filters.get(TIER_KEY) and (row.get("tier") or "") != filters[TIER_KEY]:
        return False
    wanted = filters.get(SKILL_KEY)
    if wanted:
        # 多选 = 任一命中（OR）。加一个 chip 只会扩大结果，不会像 AND 那样
        # 悄悄把岗位藏掉 —— 要和「只排序不排除」保持同一种手感。
        have = row.get("skills") or []
        if not any(skill in have for skill in wanted):
            return False
    return True


def _date_key(row: dict) -> float:
    """发布时间排序键。取负的序数，于是「越新越小」，可直接升序排。

    平台给的日期格式不完全一致（带时间的 ISO 8601 与纯日期都可能出现），
    有些行没有日期。**统一截到日期粒度**再比 —— 混着比字符串会让同一
    天的纯日期条目排到带时间的前面，那是精度差异，不是真实先后。
    缺失的用 inf 排在最后。
    """
    raw = (row.get("listing_date") or "")[:10]
    try:
        return -dt.date.fromisoformat(raw).toordinal()
    except ValueError:
        return float("inf")


def age_days(row: dict, today: dt.date | None = None) -> int | None:
    """挂出多少天。日期缺失/解析失败返回 None（当作「不算过期」）。"""
    raw = (row.get("listing_date") or "")[:10]
    if not raw:
        return None
    try:
        return ((today or dt.date.today()) - dt.date.fromisoformat(raw)).days
    except ValueError:
        return None


def is_stale(row: dict, today: dt.date | None = None) -> bool:
    age = age_days(row, today)
    return age is not None and age > AGE_WARN_DAYS


def age_tag(row: dict) -> tuple[str, str] | None:
    """过期岗位的「挂出 N 天」。

    **只有面板会画它，刻意不进 render.tags_for。** 原因有二：
      1. tags_for 的输出会进搜索范围、CSV 和 md 报告 —— 那就会变成统计口径；
      2. 它是随「今天」天天变的信号，拿它当特征/权重没有意义。所以它是
         纯展示，且置顶，方便一眼跳过。
    """
    age = age_days(row)
    if age is None or age <= AGE_WARN_DAYS:
        return None
    return ("age", f"挂出 {age} 天")


def _ordered(rows: list[dict], view: str, filters: dict, config: dict) -> list[dict]:
    """排序。

    默认口径：**分数优先、时间次优先**。
    被排除视图保留 rule_id 顺序 —— 那是审计时要的（同一条规则的误杀连在一起看）。
    推荐视图在分数序列里再插低分岗（校准样本，见 _mix_low_scores）。
    """
    sort = filters.get("sort")
    if view == "excluded" and not sort:
        return sorted(rows, key=lambda r: (r.get("rule_id") or "", r.get("title") or ""))
    if sort == "date":
        return sorted(rows, key=lambda r: (_date_key(r),
                                           -(r.get("total_score") or -999),
                                           r.get("uid") or ""))
    rows = sorted(rows, key=lambda r: (-(r.get("total_score") or -999),
                                       _date_key(r), r.get("uid") or ""))
    if view == "recommend":
        cfg = (config.get("panel") or {}).get("recommend") or {}
        rows = _mix_low_scores(rows, float(cfg.get("low_ratio", 0.1)),
                               int(cfg.get("low_max_score", 0)))
    return rows


def _mix_low_scores(rows: list[dict], low_ratio: float, low_max_score: int) -> list[dict]:
    """在推荐列表里按比例插入低分岗，作为**校准样本**。

    为什么需要：低分区如果一直不进列表，就永远拿不到标注，权重也调不出来。
    所以每 10 条里硬插 1 条低分的。

    **必须作用在整池上、再切片**：如果按页插，插入点会随页码漂移，
    翻页时同一岗位可能重复或消失。整池插完再切片，分页才是连续的。
    """
    if low_ratio <= 0:
        return rows
    step = max(2, round(1.0 / low_ratio))
    high = [r for r in rows if (r.get("total_score") or -999) > low_max_score]
    low = [r for r in rows if (r.get("total_score") or -999) <= low_max_score]
    if not low or not high:
        return rows
    out: list[dict] = []
    i = j = 0
    while i < len(high) or j < len(low):
        if j < len(low) and (len(out) + 1) % step == 0:
            out.append(low[j]); j += 1
        elif i < len(high):
            out.append(high[i]); i += 1
        else:
            out.append(low[j]); j += 1
    return out


def _label_time(row: dict, labels: dict, index: dict) -> str:
    return (report_mod.label_of(row, labels, index) or {}).get("labeled_at") or ""


def _unlabelled_at(row: dict, labels: dict, index: dict, since: str) -> bool:
    """在 `since` 那一刻还没被标过 —— 推荐视图的池子就是它。

    **为什么要用时间而不是「现在有没有标注」**：翻页时会在第一页标注。
    如果按「现在」判，标一条池子就少一条，第二页的切片起点前移一格，
    于是**正好跳过一条没看过的岗位**。
    按 `since` 判，本次会话里标的仍算「在 since 时未标注」，池子在翻页期间不变。
    代价是标过的卡片仍留在列表里 —— 前端给它们加 .done 变灰，一眼能看出已处理。
    """
    t = _label_time(row, labels, index)
    return not t or not since or t > since


def _search_text(row: dict, label: dict) -> str:
    """搜索范围：标题 / 公司 / 地点 / 薪资原文 / 备注 / 所有 tag / **正文全文**。

    正文比资格摘录更全（摘录就是从正文里截的），而且是纯子串匹配、
    不跑正则，比 decorate() 便宜得多。
    """
    parts = [row.get("title"), row.get("company"), row.get("location"),
             row.get("salary"), (label or {}).get("note"), row.get("description")]
    parts += [text for _, text in tags_for(row)]
    return " ".join(str(p) for p in parts if p).lower()


def _search(rows: list[dict], query: str, labels: dict, index: dict,
            skills_cfg: dict) -> list[dict]:
    """术语化搜索：查询按空白切成术语，术语之间 **AND**。

    每个术语按长度/词表分三档（见 skills.term_matcher）：

      词表里的词    整词   java 只匹配 Java（不是 javascript）、c 只匹配 C
      非词表词(>=3) 前缀   cyber 仍命中 cybersecurity
      短词(<=2)     整词   t1 保留；3 的 "+3" 被 + 挡住

    **词表那一层是承重的**：没有它，java 会走前缀档照样吃掉 javascript。
    _search_text（搜索范围：标题/公司/地点/薪资/备注/tag/正文）一行没动。
    """
    terms = skills_mod.search_terms(query, skills_cfg)
    if not terms:
        return rows
    out = []
    for row in rows:
        text = _search_text(row, report_mod.label_of(row, labels, index))
        if all(rx.search(text) for rx, _kind, _label in terms):
            out.append(row)
    return out


def _resolve_since(conn, view: str, filters: dict) -> str | None:
    """推荐视图的「冻结时间」：进入视图时定下来，翻页必须带着走。

    只推荐视图需要它 —— 池子判据是「在 since 时还没被标过」（见 _unlabelled_at），
    这样翻页期间标注不会改变池子，第一页标掉的不会让第二页跳过项目。
    其余视图的池子由规则 / 抓取批次决定，翻页时本来就不变。
    """
    if view != "recommend":
        return None
    return filters.get(SINCE_KEY) or database.utcnow()


def _query(conn, config, view, offset, limit, filters=None, since=None,
           skills_cfg=None):
    """返回 (页内行[已装饰], 总数)。只装饰当前页。"""
    filters = filters or {}
    kept, excluded, labels, index = report_mod.load(conn, config)

    if view == "excluded":
        rows = excluded
    elif view == "all":
        # 全库。excluded 侧本来没去重（审计要看全部），这里补一次，
        # 否则同一岗位的三个平台副本会在「全部」里并排出现
        rows = report_mod.best_per_group(kept + excluded)
    elif view == "labelled":
        # 已标注视图要连被排除的一起看 —— 被排除的那一侧才是漏杀的出口
        rows = [r for r in kept + excluded
                if report_mod.has_labels(r, labels, index)]
    elif view == "new":
        # 最近一批抓取。**不看访问时间** —— 那套会被每次页面加载推走，
        # 基线永远晚于最近一次抓取，结果恒为空。
        # 这一批连被规则排除的一起列出：这一栏回答的是「刚才那批抓到了什么」，
        # 藏掉一部分会让人以为抓少了；被排除的卡片带「已排除」标记。
        batch = database.last_batch_started_at(conn)
        rows = report_mod.best_per_group(
            [r for r in kept + excluded
             if batch and (r.get("first_seen_at") or "") >= batch])
    else:                                   # recommend
        # 挂出 >30 天的不进推荐：多半没有真实 headcount，或排在几个月积压后面。
        # **只影响推荐视图**，库、全部、被排除、已标注都不动它。
        rows = [r for r in kept
                if _unlabelled_at(r, labels, index, since) and not is_stale(r)]

    rows = _ordered(rows, view, filters, config)

    if filters.get(QUERY_KEY):
        rows = _search(rows, filters[QUERY_KEY], labels, index, skills_cfg or {})

    # **先过滤、再切片** —— 「一页要填满」的关键。客户端隐藏做不到这件事。
    if filters:
        rows = [r for r in rows
                if passes_filters(r, report_mod.label_of(r, labels, index), filters,
                                  report_mod.has_labels(r, labels, index))]

    total = len(rows)
    page = report_mod.decorate(rows[offset:offset + limit], labels, index)
    return page, total


def _link(view: str, offset: int = 0, filters: dict | None = None,
          since: str | None = None) -> str:
    # 一律 urlencode：搜索词里会有 + / & / 非 ASCII（旧版直接拼字符串，
    # 搜「c++」会被 parse_qs 当成空格）。多值（技能多选）逐个 append。
    pairs: list[tuple[str, str]] = [("view", view)]
    if offset:
        pairs.append(("offset", str(offset)))
    for key, value in (filters or {}).items():
        if key == SINCE_KEY:
            continue                        # 由 since 参数单独管，避免出现两份
        if isinstance(value, (list, tuple)):
            pairs.extend((key, str(v)) for v in value)
        else:
            pairs.append((key, str(value)))
    if since:
        pairs.append((SINCE_KEY, since))
    return "/?" + urlencode(pairs)


def skill_tree(conn, skills_path: str | None = None) -> dict:
    """设置页要的数据：合并词表 + 命中数 + 来源 + 标记。

    首屏和「改一条后重新取数」共用这一份，所以只写一套数据形状。
    """
    merged = skills_mod.load_skills(skills_path)
    local = skills_mod.load_local(skills_path)
    # 种子条目 -> 它在 skills.yaml 里自带的别名（小写），用来判断每个别名的来源
    seed_aliases = {
        str(e.get("name")): {str(a).lower() for a in (e.get("aliases") or [])}
        for e in (skills_mod.load_base(skills_path).get("skills") or []) if e.get("name")}
    removed = {str(r).strip().lower() for r in (local.get("remove") or [])}
    counts = database.skill_counts(conn)
    out = []
    for entry in merged.get("skills") or []:
        name = str(entry.get("name"))
        aliases = [str(a) for a in (entry.get("aliases") or [])]
        is_seed = name in seed_aliases
        own = seed_aliases.get(name, set())
        alias_origins = {a: ("seed" if is_seed and a.lower() in own else "local")
                         for a in aliases}
        # 种子条目在补充层里被改过（加了本地别名，或抑制了种子别名）= 种子 + 本地
        edited = is_seed and (any(v == "local" for v in alias_origins.values())
                              or bool(own & removed))
        out.append({
            "name": name,
            "hits": counts.get(name, 0),
            "origin": "seed" if is_seed else "local",
            "source": ("mixed" if edited else "seed") if is_seed else "local",
            "ambiguous": bool(entry.get("ambiguous")),
            "has_not": bool(entry.get("not")),
            "aliases": aliases,
            "alias_origins": alias_origins,
        })
    # 设置页按字母排（方便查找）；命中数照样展示在行里
    out.sort(key=lambda s: (s["name"].lower(), s["name"]))
    return {"skills": out,
            "remove": [str(r) for r in (local.get("remove") or [])]}
