"""共用渲染：资格段落摘录、派生 tag、行输出。

md 报告与未来的面板共用这里的函数，避免两边各写一套、慢慢走样。
全部只读，不联网。

为什么要摘「资格段落」：硬过滤管的是**误杀**，
但**漏杀**（该排除的没排除）没有任何自动出口 —— 它只能靠人扫。
把资格句抽成单独一列，人扫一列就能发现，不用逐条点开 JD。
"""

from __future__ import annotations

import html
import json
import re

_WS = re.compile(r"\s+")

# 资格线索，按可信度排序。命中越靠前越可能是硬门槛。
#
# 每项是 (线索名, 正则, 硬过滤是否该管这一类)：
#
#   第三个字段很关键。`label --export-fixtures` 把标了「不可投」的岗位
#   回流成「必须排除」用例 —— 但那只有当**该类别本来就该由硬过滤负责**时才成立。
#   驾照、时间冲突是个人条件（驾照还能补），刻意不做硬排除；若也回流成用例，
#   测试会一直红，逼着我们写一条会误杀一整片 T2 支持岗的规则。
#   所以这类只标记、只降权，不进 fixtures。
#
# 这份线索表同时还是「漏杀出口」的闸门：export_fixtures 靠
# eligibility_snippet() 摘证据句，**摘不到就整条跳过**。同一个证据提取闸门
# 要覆盖不同类别的资格线索：既有国籍表达（citizen），也有原籍/居留表达
# （permanent residency）；认不出任一类，写法刁钻的漏杀就变不成回归用例。
ELIGIBILITY_CUES: list[tuple[str, str, bool]] = [
    ("保密等级", r"\b(NV1|NV2|TSPV|AGSVA)\b|negative\s+vetting|baseline\s+(security\s+)?clearance", True),
    ("保密审查", r"(security|government|defence|defense)\s+clearance", True),
    ("公民/PR", r"australian\s*(/|and|&|or)?\s*(new\s+zealand\s*)?citizen|permanent\s+residen|citizenship", True),
    # 工作权写的是「满足条件」，不是门槛 —— 它是 override 的触发句，不是排除依据
    ("工作权", r"full\s+(unrestricted\s+)?working\s+rights|valid\s+work\s+(permit|visa)|work\s+rights", False),
    # 警察检查在澳洲是犯罪记录检查，工作签证持有人可以满足，明确不是门槛
    ("警察检查", r"(national\s+)?police\s+(clearance|check|record)", False),
    ("原住民专属", r"identified\s+position|aboriginal\s+and[/\s]*or\s+torres\s+strait", True),
    # 非资格门槛、但确实会让人标「不可投」的个人条件。放在最后：
    # 它们不该盖过真正的资格句。
    ("驾照", r"(driver['\u2019]?s?|driving)\s+licen[cs]e", False),
]
_COMPILED_CUES = [(label, re.compile(pattern, re.IGNORECASE), owned)
                  for label, pattern, owned in ELIGIBILITY_CUES]


def cue_is_rule_owned(cue: str | None) -> bool:
    """这条线索对应的类别，硬过滤是否该负责（决定它能否变成「必须排除」用例）。"""
    return any(label == cue and owned for label, _, owned in ELIGIBILITY_CUES)

# 句子切分：句号/分号/换行/项目符号都算边界
_SENT_SPLIT = re.compile(r"(?<=[.;!?])\s+|\s+[•·*]\s+|\n+")


def normalize(text: str | None) -> str:
    return _WS.sub(" ", text or "").strip()


def eligibility_snippet(text: str | None, max_len: int = 240) -> tuple[str | None, str | None]:
    """从 JD 正文里摘出最相关的一句资格表述。

    返回 (摘录, 线索名)。找不到返回 (None, None)。
    按线索优先级扫描，尽量保证摘到的是真正的门槛句而非无关提及。
    """
    body = normalize(text)
    if not body:
        return None, None

    for label, pattern, _owned in _COMPILED_CUES:
        match = pattern.search(body)
        if not match:
            continue
        snippet = _sentence_around(body, match.start(), match.end(), max_len)
        return snippet, label
    return None, None


def _sentence_around(body: str, start: int, end: int, max_len: int) -> str:
    """取包含 [start, end) 的那一句，并做长度约束。"""
    for sentence in _SENT_SPLIT.split(body):
        if not sentence:
            continue
        idx = sentence.lower().find(body[start:end].lower())
        if idx >= 0:
            if len(sentence) <= max_len:
                return sentence.strip()
            # 句子太长（SEEK 正文常有一长串项目符号）：围绕命中处开窗
            left = max(0, idx - max_len // 3)
            return ("…" if left else "") + sentence[left:left + max_len].strip() + "…"
    # 兜底：直接开窗
    left = max(0, start - max_len // 3)
    return "…" + body[left:left + max_len].strip() + "…"


# --------------------------------------------------------------------------
# 正文渲染：把抓到的 JD 转成可读 HTML
#
# 三个来源存下来的正文是不同风格的「类 markdown 纯文本」：
#   Indeed  **Date:**  + 转义连字符（market\-leading）+ 大量 "  \n  \n\n" 空白行
#   LinkedIn **The Company** + AI\-native
#   SEEK    纯文本 + "- " 项目符号（抓取时已由 HTML 转来）
# 所以需要一个小的 markdown 子集渲染器，而不是直接 <pre> 丢出去。
#
# 安全：**先转义 HTML，再做 markdown 变换**。正文来自招聘网站，属不可信输入；
# 变换只会引入我们已知的标签，因此不会产生注入。
# --------------------------------------------------------------------------

_MD_ESCAPES = re.compile(r"\\([\\`*_{}\[\]()#+\-.!>~|])")
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*", re.S)
_MD_ITALIC = re.compile(r"(?<![*\w])\*([^*\n]+)\*(?!\*)")
_MD_CODE = re.compile(r"`([^`\n]+)`")
_MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_MD_BARE_URL = re.compile(r"(?<![\"'=>(])(https?://[^\s<>\"')\]]+)")
_MD_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*$")
_MD_BULLET = re.compile(r"^\s{0,4}[-*•·]\s+(.+)$")
_MD_NUMBERED = re.compile(r"^\s{0,4}\d{1,3}[.)]\s+(.+)$")
_BLANK_RUN = re.compile(r"[ \t]*\n[ \t]*(?:\n[ \t]*)+")
_TRAILING_WS = re.compile(r"[ \t]+$", re.M)


def _inline(text: str) -> str:
    """行内标记。输入必须是**已转义**的文本。"""
    text = _MD_ESCAPES.sub(r"\1", text)
    text = _MD_CODE.sub(r"<code>\1</code>", text)
    text = _MD_BOLD.sub(r"<strong>\1</strong>", text)
    text = _MD_ITALIC.sub(r"<em>\1</em>", text)
    text = _MD_LINK.sub(r'<a href="\2" target="_blank" rel="noopener">\1</a>', text)
    text = _MD_BARE_URL.sub(r'<a href="\1" target="_blank" rel="noopener">\1</a>', text)
    return text


def render_jd_html(text: str | None, empty: str = "（这份 JD 没有抓到正文）") -> str:
    """把 JD 正文渲染成 HTML。空正文返回占位提示（面板按界面语言传 empty）。"""
    if not text or not text.strip():
        return f'<p class="muted">{html.escape(empty)}</p>'

    body = text.replace("\r\n", "\n").replace("\r", "\n")
    # 招聘网站转出来的正文常有成片的 "  \n  \n\n"，先压成单个空行
    body = _TRAILING_WS.sub("", body)
    body = _BLANK_RUN.sub("\n\n", body)
    body = html.escape(body, quote=False)

    out: list[str] = []
    para: list[str] = []
    list_tag: str | None = None

    def close_list():
        nonlocal list_tag
        if list_tag:
            out.append(f"</{list_tag}>")
            list_tag = None

    def flush_para():
        if not para:
            return
        raw = "\n".join(para).strip()
        para.clear()
        if not raw:
            return
        # 整段就是一个粗体 -> 当成小标题，正文可读性提高很多
        if re.fullmatch(r"\*\*[^*]+\*\*:?", raw):
            out.append(f"<h4>{_inline(raw.strip('*').rstrip(':'))}</h4>")
            return
        out.append("<p>" + _inline(raw).replace("\n", "<br>") + "</p>")

    def open_list(tag: str):
        nonlocal list_tag
        if list_tag != tag:
            close_list()
            out.append(f"<{tag}>")
            list_tag = tag

    for line in body.split("\n"):
        if not line.strip():
            flush_para()
            # 刻意**不**在这里 close_list()：招聘网站的正文常在项目符号之间
            # 夹空行，若在空行处收列表，每个 bullet 都会变成独立的 <ul>。
            # open_list() 只在标签类型变化时才真正切换，所以同一列表会延续下去；
            # 遇到普通段落或标题时由下面的分支关闭。
            continue
        if (m := _MD_HEADING.match(line)):
            flush_para(); close_list()
            out.append(f"<h4>{_inline(m.group(2))}</h4>")
        elif (m := _MD_BULLET.match(line)):
            flush_para()
            open_list("ul")
            out.append(f"<li>{_inline(m.group(1))}</li>")
        elif (m := _MD_NUMBERED.match(line)):
            flush_para()
            open_list("ol")
            out.append(f"<li>{_inline(m.group(1))}</li>")
        else:
            close_list()
            para.append(line)

    flush_para()
    close_list()
    return "".join(out)


# --------------------------------------------------------------------------
# 派生 tag：能从 scores.reasons 算出来的，就不要让人手填
# --------------------------------------------------------------------------

_RE_TIER = re.compile(r"^tier:(\w+)")
_RE_LEVEL = re.compile(r"^level:'([^']+)'\s*([+-]\d+)")
_RE_JOBLEVEL = re.compile(r"^job_level:'([^']+)'\s*([+-]\d+)")
# 经验的形状："experience:{年数}y {delta:+d}"。**必须排在 _RE_ADJUST 之前判**，
# 否则它会被当成一条普通 adjust 规则，年数会丢掉（tags 只剩 "experience -2"）。
_RE_EXPERIENCE = re.compile(r"^experience:(\d+)y\s*([+-]\d+)")
# adjust 的形状："{rule_id}:{命中片段!r} {delta:+d}"
_RE_ADJUST = re.compile(r"^([\w.]+):.*?\s*([+-]\d+)$")


def parse_reasons(reasons) -> dict:
    """把 scores.reasons（JSON 数组或已是 list）拆成结构化成分。"""
    if isinstance(reasons, str):
        try:
            reasons = json.loads(reasons or "[]")
        except (ValueError, TypeError):
            reasons = []
    reasons = reasons or []

    out = {"tier": None, "levels": [], "job_level": None, "experience": None,
           "adjusts": [], "raw": list(reasons)}
    for reason in reasons:
        if not isinstance(reason, str):
            continue
        if (m := _RE_TIER.match(reason)):
            out["tier"] = m.group(1)
            continue
        if (m := _RE_LEVEL.match(reason)):
            out["levels"].append((m.group(1), int(m.group(2))))
            continue
        if (m := _RE_JOBLEVEL.match(reason)):
            out["job_level"] = (m.group(1), int(m.group(2)))
            continue
        if (m := _RE_EXPERIENCE.match(reason)):
            out["experience"] = (int(m.group(1)), int(m.group(2)))
            continue
        if (m := _RE_ADJUST.match(reason)):
            out["adjusts"].append((m.group(1), int(m.group(2))))
    return out


# 原始薪资串挂成 tag 时的长度上限（不含 "原文 " 前缀）。
# 雇主写的格式五花八门，但人能直接看懂，而且常带归一化丢掉的细节
# （"+ super"、"Band 5"、"negotiable"）。截断只是为了不让它撑爆标签行。
SALARY_RAW_MAX = 36


def salary_raw_tag(row: dict, max_len: int = SALARY_RAW_MAX) -> str:
    """把雇主写的薪资原文原样挂成 tag。

    归一化出来的（`A$160k–170k/年 · 资深`）是**解释**；这一条是**原始证据**。
    两个都给：既能一眼比大小，也能核对解析对不对 ——
    解析失败时（例如源数据的数量级标注有误）原文就是唯一的线索。
    """
    raw = normalize(row.get("salary"))
    if not raw:
        return ""
    if len(raw) > max_len:
        raw = raw[:max_len].rstrip() + "…"
    return f"原文 {raw}"


def salary_tag(row: dict) -> str:
    """归一化后的薪资区间。**只呈现数字，不评价高低。**

    档位判断（偏低/中级/资深…）已停用：那需要 4 个阈值加一个合同岗修正量，
    全是拍的，误判风险大于收益。等标注数据攒够直接用推荐算法更靠谱。
    需要时把 `rules.yaml` 的 `salary.bands` 加回来即可恢复。

    解析不出来就不显示 —— best effort，缺失是正常的，
    不该用「薪资未知」占位制造噪音。
    """
    low = row.get("salary_annual_min")
    high = row.get("salary_annual_max")
    if low is None and high is None:
        return ""

    def money(value):
        if value is None:
            return "?"
        return f"{value/1000:.0f}k" if value >= 1000 else f"{value:.0f}"

    symbol = {"AUD": "A$", "USD": "US$", "NZD": "NZ$", "GBP": "£",
              "EUR": "€", "SGD": "S$"}.get(row.get("salary_currency"), "")

    if low is not None and high is not None and low != high:
        amount = f"{symbol}{money(low)}–{money(high)}"
    elif low is not None:
        amount = f"{symbol}{money(low)}+"
    else:
        amount = f"≤{symbol}{money(high)}"

    # ≈ 表示这是折算值（时薪/日薪按 1976 小时 / 260 天折成年薪），不是原始数字
    prefix = "≈" if row.get("salary_basis") == "annualized" else ""
    band = row.get("salary_band")             # 停用中，配置恢复后自动带上
    suffix = "（正文）" if row.get("salary_origin") == "description" else ""
    return f"{prefix}{amount}/年" + (f" · {band}" if band else "") + suffix


def salary_mismatch(row: dict) -> bool:
    """标题没给资历信息、但薪资明显偏高。

    依赖 `salary.hint`，因此**在 bands 停用期间恒为 False**。
    配置恢复后这条提示会自动回来。"""
    if not row.get("salary_band"):
        return False
    parsed = parse_reasons(row.get("reasons"))
    if parsed["levels"] or parsed["job_level"] or parsed["experience"]:
        return False                       # 标题/正文年限已说了资历，无需提示
    return int(row.get("salary_hint") or 0) < 0


# 一张卡片最多展示几个技能 tag。头部技术词命中率很高，一条 JD 常命中多个；
# 不封顶的话 tag 行会盖过岗位本身。
SKILL_TAG_MAX = 8


def skill_tags(row: dict,
               limit: int | None = SKILL_TAG_MAX) -> list[tuple[str, str]]:
    """技能词表的命中结果（analyze 时抽好，存在 job_skills 表）。

    **第一版不进分数** —— 它是筛选器，不是判断：二值信号（命中/没命中）
    在低分区又没有标注的情况下没法验证权重。所以只呈现、只筛选。

    limit 为 None 表示不封顶：列表卡片要短（超过就收成 +N），但**详情页
    全部展开**，那里有的是空间。
    """
    found = [str(s) for s in (row.get("skills") or []) if s]
    if not found:
        return []
    if limit is None or len(found) <= limit:
        return [("skill", s) for s in found]
    return ([("skill", s) for s in found[:limit]]
            + [("skill", f"+{len(found) - limit}")])


def tags_for(row: dict,
             skill_limit: int | None = SKILL_TAG_MAX) -> list[tuple[str, str]]:
    """给人看的短标签，返回 (kind, text)。

    kind 只用于面板上色（tier / skill / level / adjust / salary / raw），
    md 报告只要 text。全部来自已算好的分数，不引入新判断。
    """
    parsed = parse_reasons(row.get("reasons"))
    tags: list[tuple[str, str]] = []

    if parsed["tier"]:
        tags.append(("tier", parsed["tier"]))
    tags.extend(skill_tags(row, skill_limit))
    tags.extend(("level", f"{text} {delta:+d}") for text, delta in parsed["levels"])
    if parsed["job_level"]:
        text, delta = parsed["job_level"]
        tags.append(("level", f"{text} {delta:+d}"))
    if parsed["experience"]:
        years, delta = parsed["experience"]
        tags.append(("level", f"经验 {years}+ 年 {delta:+d}"))
    tags.extend(("adjust", f"{rule} {delta:+d}") for rule, delta in parsed["adjusts"]
                if rule != "salary")   # 薪资调整已由档位 tag 表达，不重复

    if (money := salary_tag(row)):
        tags.append(("salary", ("⚠ " if salary_mismatch(row) else "") + money))
    if (raw := salary_raw_tag(row)):
        # 解析失败时也照样挂 —— 原文是唯一能看出「源数据坏了」的线索
        tags.append(("raw", raw))
    return tags


def tag_texts(row: dict) -> list[str]:
    """只要文字，给 md 报告用。"""
    return [text for _, text in tags_for(row)]


def score_badge(row: dict) -> str:
    """一行式的分数说明：总分 + 分项。"""
    return (f"分数 {row.get('total_score')}"
            f"（{row.get('tier') or '-'} / 级别 {row.get('level_score', 0):+d}"
            f" / 调整 {row.get('adjust_score', 0):+d}）")


def render_job_lines(row: dict, index: int | None = None,
                     label: dict | None = None, include_snippet: bool = True) -> list[str]:
    """渲染一条岗位为 Markdown 片段。md 报告与面板共用这个口径。"""
    heading = f"{index}. " if index is not None else ""
    lines = [f"### {heading}{row.get('title') or '(无标题)'} — {row.get('company') or '-'}"]

    tags = tag_texts(row)
    if tags:
        lines.append("- " + " ｜ ".join(f"`{t}`" for t in tags))

    dup = ""
    if row.get("duplicate_count"):
        dup = f"  · 另有 {row['duplicate_count']} 条重复（{row.get('duplicate_sources')}）"
    lines.append(f"- **{score_badge(row)}**{dup}")
    lines.append(
        f"- **地点** {row.get('location') or '-'} ｜ **来源** {row.get('source')}"
        f" ｜ **发布** {row.get('listing_date') or '-'}")

    if row.get("salary"):
        lines.append(f"- **薪资** {row['salary']}")

    if include_snippet:
        snippet, cue = eligibility_snippet(row.get("description"))
        if snippet:
            lines.append(f"- **资格句**（{cue}）{snippet}")
        else:
            lines.append("- **资格句** 正文未见明确的资格表述")

    if label and any(label.get(k) for k in ("eligibility", "interest", "action", "note")):
        bits = []
        if label.get("eligibility"):
            extra = f"（{label['ineligible_reason']}）" if label.get("ineligible_reason") else ""
            bits.append(f"可投性={label['eligibility']}{extra}")
        if label.get("interest"):
            bits.append(f"意向={label['interest']}")
        if label.get("action"):
            bits.append(f"进度={label['action']}")
        if label.get("note"):
            bits.append(f"备注={label['note']}")
        lines.append("- **标注** " + " ｜ ".join(bits))

    lines.append(f"- **链接** {row.get('url') or '-'}")
    return lines
