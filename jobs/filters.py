"""硬过滤层（含 provenance）。

只做一件事：判断一条 JD 是否「铁定不可能」。
不做任何排序或模糊判断 —— 那属于 scoring。

为什么规则必须窄：误杀是永久且不可见的（没有反馈信号告诉你漏了什么），
误放只是花 2 秒扫一眼标题。所以这里只删铁定不行的。

每条判定都记录 provenance（哪条规则、匹配到哪个片段），否则无法调试。
"""

from __future__ import annotations

import re
from functools import lru_cache

# 匹配前先把空白折叠，让 [^.\n]{0,40} 这类窗口按「同一句」工作
_WS = re.compile(r"\s+")


def normalize_text(text: str | None) -> str:
    return _WS.sub(" ", text or "").strip()


@lru_cache(maxsize=4096)
def _compiled(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.IGNORECASE)


def _first_match(text: str, patterns) -> str | None:
    for pattern in patterns or []:
        m = _compiled(pattern).search(text)
        if m:
            return m.group(0)[:200]
    return None


def _first_match_avoiding(text: str, conditions, noise) -> str | None:
    """同 `_first_match`，但跳过与噪音区间**重叠**的那些命中。

    这是 `none_scope: overlap` 的实现。合成说明：

      噪音词只描述它自己覆盖的那一小段。比如
      「police clearance **and** obtain a security clearance」里，police
      一词只该否决它覆盖的 "police clearance"，不该把同一句里另一个真正
      的 security clearance 也一起救走。EEO 样板语同理：只有某次门槛命中
      本身跨越了 EEO 词句（宽窗口误命中欢迎语）时，那次才作废；同句里独立
      的 must / required / hold / have 要求不受影响。

    重叠判定把护栏收回它本来的意思：**只有当噪音与被判定的那次命中有字符
    重叠时，这次命中才算被否决**。
    """
    noise_re = [_compiled(p) for p in noise]
    for pattern in conditions:
        for m in _compiled(pattern).finditer(text):
            if any(nm.start() < m.end() and m.start() < nm.end()
                   for nr in noise_re for nm in nr.finditer(text)):
                continue
            return m.group(0)[:200]
    return None


# 分句边界：句号 / 分号 / 叹号 / 问号。normalize_text 已把换行折叠成空格，
# 所以这里**不依赖换行**，只按标点切分。
_SEGMENT_SPLIT = re.compile(r"[.;!?]")


def _segment_around(text: str, start: int, end: int) -> str:
    """取包含 [start, end) 的那个分句（按 . ; ! ? 切分）。"""
    left = 0
    for m in _SEGMENT_SPLIT.finditer(text, 0, start):
        left = m.end()
    nxt = _SEGMENT_SPLIT.search(text, end)
    right = nxt.start() if nxt else len(text)
    return text[left:right]


def _first_match_in_context(text: str, conditions, noise) -> str | None:
    """逐条检查候选命中，只否决**噪音与它同分句**的那些。

    这是 `none_scope: sentence` 的实现：噪音只否决与它同分句的候选命中。

    注意它比 `overlap` 宽 —— 同一句里独立、明确的资格要求也会被一起否决，
    所以资格门槛规则应优先用 `overlap`（只否决噪音实际覆盖的那次命中）。
    本函数保留为通用粒度，当前规则集未使用。

    **遍历全部候选命中**：前一个候选落在噪音分句里，不表示后面的分句没有
    真实要求。
    """
    noise_re = [_compiled(p) for p in noise]
    for pattern in conditions:
        for m in _compiled(pattern).finditer(text):
            if any(nr.search(_segment_around(text, m.start(), m.end()))
                   for nr in noise_re):
                continue
            return m.group(0)[:200]
    return None


def _match_candidates(text: str, conditions, noise, scope: str) -> str | None:
    """按 scope 语义在候选模式上找第一个没被噪音否决的命中。

    conditions 为空表示「无条件规则」，沿用原语义。
    """
    if noise and scope == "overlap":
        if not conditions:
            return "无条件"
        return _first_match_avoiding(text, conditions, noise)
    if noise and scope == "sentence":
        if not conditions:
            return None if _first_match(text, noise) else "无条件"
        return _first_match_in_context(text, conditions, noise)
    if _first_match(text, noise):
        return None
    if not conditions:
        return "无条件"
    return _first_match(text, conditions)


def _rule_fires(text: str, rule: dict) -> str | None:
    """返回命中的片段，未命中返回 None。

    语义：
      any        —— 明确要求候选，命中任一即触发（缺省表示无条件满足）
      none       —— 命中任一则本条不触发（用于剔除噪音）
      none_scope —— "text"（默认，全篇生效）
                    | "overlap"（只否决与之字符重叠的那次命中）
                    | "sentence"（只否决与之同分句的那次命中）
      weak_any   —— 性质更弱、更容易误命中的第二组候选（可选）
      weak_none / weak_none_scope —— 只作用于 weak_any 候选的护栏

    三种 scope 对应三种护栏意图：

      text     全篇出现噪音就整条不触发。适用于「噪音一出现，整篇判断都
               不再可靠」的场景。
      overlap  噪音只否决与它**字符区间重叠**的那次命中。适用于噪音是另一
               个词组/短句的场景（如 police clearance，或跨越 EEO 欢迎语的
               宽窗口命中）。
      sentence 噪音只否决与它**同分句**的那次命中。粒度比 overlap 宽：同一句
               里独立、明确的资格要求也会被一起否决，所以只用于 weak_any。
               分句按 . ; ! ? 切分，不依赖换行（换行已被 normalize_text 折叠）。

    一个规则可以把候选分成性质不同的两组，互不影响：

      any        明确要求候选（must / required / hold / have …）。它们只受
                 none / none_scope 约束；资格门槛规则用 overlap，同句里独立的
                 明确要求不会被 EEO 样板语抹掉。
      weak_any   宽资格标题窗口（"About you / Eligibility … Australian Citizen"）。
                 它只是「标题附近出现公民字眼」，会把 welcome / 多元包容欢迎语
                 误命中成门槛，所以单独用 weak_none（welcome / encourage /
                 regardless / EEO）在同分句内否决它。

    先判 any；any 没有有效命中时再判 weak_any。
    """
    conditions = rule.get("any")
    noise = rule.get("none")
    scope = rule.get("none_scope") or "text"

    hit = _match_candidates(text, conditions, noise, scope)
    if hit:
        return hit
    weak_conditions = rule.get("weak_any")
    if weak_conditions:
        weak_noise = rule.get("weak_none") or noise
        weak_scope = rule.get("weak_none_scope") or scope
        return _match_candidates(text, weak_conditions, weak_noise, weak_scope)
    return None


def build_job_text(job) -> str:
    """用于过滤的文本。标题 + teaser + 正文。"""
    parts = [job["title"], job["teaser"], job["description"]]
    return normalize_text(" \n ".join(p for p in parts if p))


def evaluate(text: str, rules: dict) -> dict:
    """判定一条 JD。

    返回 dict: is_excluded / rule_id / rule_kind / matched_text
    """
    version = rules.get("version")

    hits: list[tuple[str, str]] = []
    for rule in rules.get("exclude") or []:
        if (matched := _rule_fires(text, rule)):
            hits.append((rule["id"], matched))

    if not hits:
        return {"is_excluded": False, "rule_id": None,
                "rule_kind": None, "matched_text": None, "rules_version": version}

    # 覆盖规则优先：持 full working rights 的工作签证无需雇主担保，
    # "Citizen, PR or hold a valid work permit/visa" 这类是开放的
    for rule in rules.get("overrides") or []:
        if (matched := _first_match(text, rule.get("any"))):
            return {"is_excluded": False, "rule_id": rule["id"],
                    "rule_kind": "override", "matched_text": matched,
                    "rules_version": version}

    return {
        "is_excluded": True,
        "rule_id": ",".join(rule_id for rule_id, _ in hits),
        "rule_kind": "exclude",
        "matched_text": hits[0][1],
        "rules_version": version,
    }
