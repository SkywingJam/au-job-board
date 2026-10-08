"""打分：T1/T2/T3 方向分层 + 级别 + 方向调整项。

纯函数，只读 DB 里已有的字段，永不联网。
因此改 rules.yaml 后重跑打分的成本是秒级。

设计取舍：
  - 级别信号只取 **标题**（可预测、不会被正文里的
    "you will work alongside senior engineers" 误伤），
    外加 LinkedIn 的 job_level 字段。
  - **经验年限是唯一的例外，它必须读正文** —— 该模式几乎只出现在正文的
    Requirements 段，不在标题里。为了不把「我们团队有 10 年经验的工程师」
    当成岗位要求，正文命中必须带**要求语境**，
    见 `score_experience()` 与 rules.yaml 的 `experience` 段。
  - 方向信号优先用 SEEK 的 classification 字段，标题正则作兜底。
"""

from __future__ import annotations

import re
from functools import lru_cache

from .filters import normalize_text


@lru_cache(maxsize=4096)
def _compiled(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.IGNORECASE)


def _matches(text: str, patterns) -> str | None:
    for pattern in patterns or []:
        m = _compiled(pattern).search(text)
        if m:
            return m.group(0)
    return None


def classify_tier(title: str, classification: str, rules: dict) -> tuple[str, int, str]:
    """返回 (tier, weight, reason)。无 pattern 的层级作为兜底。

    分**两轮**扫全部层级：先只用 classification，再退回标题。

    为什么不能像原来那样「每个层级内部先 classification 再 title」：
    classification 是平台给的字段，比标题正则可靠，但它只在一个层级内部
    被优先。于是一个 classification='Help Desk & IT Support' 的岗位，
    只要标题里出现 "Software" 就被 T1 先抢走了，即使它本质是 T2。
    """
    tiers = rules.get("tiers") or {}
    fallback = ("T3", 0, "默认层级")

    for name, spec in tiers.items():
        if not (spec.get("title") or spec.get("classification")):
            fallback = (name, int(spec.get("weight", 0)), "无方向关键词")

    for field, haystack in (("classification", classification), ("title", title)):
        if not haystack:
            continue
        for name, spec in tiers.items():
            if (hit := _matches(haystack, spec.get(field) or [])):
                return name, int(spec.get("weight", 0)), f"{field}≈{hit!r}"

    return fallback


def score_level(title: str, job_level: str, rules: dict) -> tuple[int, list[str]]:
    """级别打分。分数自带正负号，直接相加。"""
    level_cfg = rules.get("level") or {}
    total, reasons = 0, []

    for bucket in ("positive", "negative"):
        for rule in level_cfg.get(bucket) or []:
            pattern = rule.get("pattern")
            if not pattern:
                continue
            if (hit := _matches(title, [pattern])):
                delta = int(rule.get("score", 0))
                total += delta
                reasons.append(f"level:{hit!r} {delta:+d}")

    # LinkedIn 的 job_level 字段比标题正则可靠
    mapping = rules.get("job_level") or {}
    if job_level:
        key = job_level.strip().lower()
        if key in mapping:
            delta = int(mapping[key])
            total += delta
            reasons.append(f"job_level:{key!r} {delta:+d}")

    return total, reasons


_EXP_NUM = re.compile(
    # 区间写法要一起认：`3-5 years` / `3 – 5 years` / `3 to 5 years`，
    # 以及 Indeed 转义过的 `3 \- 5 years`。年数一律取**下界**（见下方）。
    r"\b(\d{1,2})(?:\s*\\?\s*(?:[\u2013\u2014-]|to)\s*(\d{1,2}))?\s*\+?\s*(?:years?|yrs?)\b",
    re.IGNORECASE)
_EXP_WORD = re.compile(r"\b(experience|exp)\b", re.IGNORECASE)
# 「2 years (Required)」是 Indeed 的常见写法 —— 括号里的词在数字**后面**
_EXP_REQ_AFTER = re.compile(r"^\s*[^.\n]{0,18}\b(required|essential|mandatory)\b",
                            re.IGNORECASE)


def score_experience(title: str, body: str, rules: dict) -> tuple[int, list[str]]:
    """经验年限减分。**只降权，不排除。**

    背景：级别规则原来有一条 `\\d+\\+?\\s*years?\\s+(of\\s+)?experience`，
    但 `score_level` 只把标题传进去，而该模式几乎只出现在正文的
    Requirements 段。于是这条规则等于没生效。

    为什么不能直接把正文拼给 `score_level`：正文里大量出现的是
    **公司叙述**（"has more than 30 years of experience"、
    "with over 40 years of experience"），拼进去就成了误判。所以正文
    命中必须同时满足：

      1. 数字后面紧跟 experience/exp（"3-5 years' experience"），或
         数字前面/窗口里有要求用词（minimum / at least / you have …），或
         括号里跟着 (Required)；
      2. 窗口内**没有**叙述用词（more than N / over N / 我们团队 / 毕业年限 …）。

    年数取区间**下界**（"3-5 years" = 3 年），多段要求取**最大**值
    （"10 年手工测试 + 2 年 UFT" 就是 10 年岗）。档位见 rules.yaml
    `experience.buckets`。标题里写了年限就直接算要求 —— 标题没有正文
    那种叙述句的歧义。
    """
    cfg = rules.get("experience") or {}
    if not cfg:
        return 0, []
    buckets = sorted(((int(b.get("min_years", 0)), int(b.get("score", 0)))
                      for b in cfg.get("buckets") or []), reverse=True)
    if not buckets:
        return 0, []
    cue = _compiled(cfg["require_cue"]) if cfg.get("require_cue") else None
    deny = _compiled(cfg["deny_cue"]) if cfg.get("deny_cue") else None
    lookback = int(cfg.get("lookback", 70))
    lookahead = int(cfg.get("lookahead", 26))

    def lower_bound(m):
        """区间取**下界**（"3-5 years" 要求的是至少 3 年）。"""
        return int(m.group(1))

    years = 0
    for m in _EXP_NUM.finditer(title or ""):
        years = max(years, lower_bound(m))

    if not years:
        for m in _EXP_NUM.finditer(body or ""):
            start, end = m.start(), m.end()
            before = body[max(0, start - lookback):start]
            after = body[end:end + lookahead]
            is_requirement = (_EXP_WORD.search(after) or _EXP_REQ_AFTER.match(after)
                              or _EXP_WORD.search(before)
                              or (cue and cue.search(before)))
            if not is_requirement:
                continue
            window = body[max(0, start - lookback):end + lookahead]
            if deny and deny.search(window):
                continue
            years = max(years, lower_bound(m))

    for min_years, delta in buckets:
        if years >= min_years:
            return delta, [f"experience:{years}y {delta:+d}"]
    return 0, []


def score_adjust(title: str, body: str, rules: dict) -> tuple[int, list[str]]:
    total, reasons = 0, []
    for rule in rules.get("adjust") or []:
        pattern = rule.get("pattern")
        if not pattern:
            continue
        scope = rule.get("scope", "any")
        haystack = title if scope == "title" else f"{title} \n {body}"
        if (hit := _matches(haystack, [pattern])):
            delta = int(rule.get("score", 0))
            total += delta
            reasons.append(f"{rule.get('id', 'adjust')}:{hit!r} {delta:+d}")
    return total, reasons


def score_location(location: str, rules: dict) -> tuple[int, list[str]]:
    """地点减分。

    **不是硬门槛**，只是把外州岗位排在后面 —— 抓取范围是澳洲全国，
    这些岗位照样要能看见，只是排后面。

    location 字段是平台给的自由文本（"Sydney NSW" / "Bayswater North,
    Victoria, Australia"），所以只能用关键词判断，做不到精确到区。
    判断不出来（字段为空、或写了 remote）就不减分 —— 宁可漏判不可误判。
    """
    cfg = rules.get("location") or {}
    penalty = int(cfg.get("out_of_state_penalty", 0) or 0)
    if not penalty or not location:
        return 0, []
    if _matches(location, cfg.get("local")):
        return 0, []
    if _matches(location, cfg.get("flexible")):
        return 0, []
    return penalty, [f"location.out_of_state:{location!r} {penalty:+d}"]


def score_job(job, rules: dict, salary: dict | None = None) -> dict:
    """打分。salary 是 salary.extract() 的结果（可为 None）。

    薪资在这里**不是**评价岗位好坏，而是补一个标题给不出的信号：
    标题没写 Senior、却标着 $160k 的岗位，多半不是入门级。
    """
    title = normalize_text(job["title"])
    classification = normalize_text(job["classification"])
    body = normalize_text(job["description"])

    tier, tier_score, tier_reason = classify_tier(title, classification, rules)
    level_score, level_reasons = score_level(title, job["job_level"], rules)
    # 经验年限算「级别」信号：它说的就是资历，只是写在正文里。
    # 单列一条 reason（experience:…），面板上单独成一个 tag。
    exp_score, exp_reasons = score_experience(title, body, rules)
    level_score += exp_score
    adjust_score, adjust_reasons = score_adjust(title, body, rules)
    loc_score, loc_reasons = score_location(normalize_text(job["location"]), rules)
    adjust_score += loc_score
    adjust_reasons += loc_reasons

    mismatch = False
    if salary and salary.get("hint"):
        hint = int(salary["hint"])
        adjust_score += hint
        # 标题没给出任何资历信息、但薪资明显偏高 —— 最值得提示的情形
        mismatch = hint < 0 and level_score >= 0
        note = " ⚠标题未标高资历" if mismatch else ""
        adjust_reasons.append(f"salary:{salary.get('band')} {hint:+d}{note}")

    reasons = ([f"tier:{tier} {tier_score:+d} ({tier_reason})"]
               + level_reasons + exp_reasons + adjust_reasons)
    return {
        "tier": tier,
        "tier_score": tier_score,
        "level_score": level_score,
        "adjust_score": adjust_score,
        "total_score": tier_score + level_score + adjust_score,
        "reasons": reasons,
        "rules_version": rules.get("version"),
        "salary_mismatch": mismatch,
    }
