"""薪资解析：把自由文本的薪资描述折成可比的年化区间。

**定位：难度信号，不是筛选维度。**

薪资字段可能缺失：缺失只表示源数据没写，不代表这条岗位是低薪。
存在缺失时，只比较已知薪资（排序或过滤）可能产生选择偏差。
但反过来，**有值的时候它就是一个额外信息**：
一个标题没写 Senior、却标着 $160k 的岗位，基本可以断定不是入门级。

因此这里只求 best effort：
  - 明确的数字 + 单位 -> 解析
  - "Competitive" / "attractive hourly rate" 这类没数字的 -> 直接放弃，不算错

年化的两个假设（at least 保守取值）：
  - 日 -> × 260 工作日
  - 时 -> × 1976 小时（38h × 52 周，澳洲全职标准）

**注意**：时薪/日薪通常是**合同岗**报价，结构性高于同级永久岗。
所以年化后只用于「难度」判断，不能当作永久岗薪资来比较 ——
返回值里用 basis 区分，展示时也分开标。
"""

from __future__ import annotations

import re

# 货币：显式代码优先，其次是符号
_CURRENCY = re.compile(
    r'\b(AUD|USD|NZD|GBP|EUR|SGD)\b|(?P<sym>AU\$|US\$|NZ\$|A\$|\$)',
    re.IGNORECASE)

# 周期。**顺序有意义**：先判时/日/月，最后才是年。
# 单位写法在真实数据里非常杂：p.a. / p.h. / phr / ph / p.d. / pa / pcm / "a day"。
# 用后置 (?![a-z]) 而不是前置 \b —— "160kpa" 里 k 和 p 都是词字符，
# 前置 \b 会失配，导致只取到前半段。
_PERIODS = [
    ("hour", re.compile(
        r'per\s*hour|hourly|an?\s+hour|/\s*hr|p/?hr|p\.?\s?h\.?r?(?![a-z])', re.I)),
    ("day", re.compile(
        r'per\s*day|daily|an?\s+day|/\s*day|p\.?\s?/?d\.?(?![a-z])', re.I)),
    ("month", re.compile(
        r'per\s*month|monthly|an?\s+month|/\s*month|pcm|p\.?\s?m\.?(?![a-z])', re.I)),
    ("year", re.compile(
        r'per\s*year|per\s*annum|per\s*yr|annually|yearly|annual|/\s*year|'
        r'p\.?\s?a\.?(?![a-z])', re.I)),
]

# 一个「像钱的数字」：可选货币前缀 + 数字 + 可选 k 后缀。
# 两个负向断言：
#   (?![0-9])  避免从 "160" 里截出 "1"
#   (?!\s*%)   排除 "12% Super" 里的 12
_TOKEN = re.compile(
    r'(?P<pre>AU\$|US\$|NZ\$|A\$|\$|\bAUD\b|\bUSD\b|\bNZD\b|\bGBP\b|\bEUR\b|\bSGD\b)?\s*'
    r'(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(?P<k>[kK])?'
    r'(?![0-9])(?!\s*%)',
    re.IGNORECASE)

_RANGE_SEP = re.compile(r'\s*(?:-|–|—|to|up\s+to|and\s+above)\s*', re.I)
_MAX_ONLY = re.compile(r'\bup\s*to\b', re.I)
_MIN_ONLY = re.compile(r'\b(and\s+above|\+|or\s+more|plus)\b', re.I)

DEFAULT_CURRENCY = "AUD"
HOURS_PER_YEAR = 1976
DAYS_PER_YEAR = 260

# 年化系数
_MULTIPLIER = {"year": 1, "month": 12, "day": DAYS_PER_YEAR, "hour": HOURS_PER_YEAR}

# 没写单位时的心智模型：澳洲年薪不会低于 2 万，也不会高到 200 万
_BARE_ANNUAL_FLOOR = 20_000

# 合理性上限。超过就当成源数据错误丢掉而不是猜：
# 源数据偶尔会把数量级标错（多一个数量级后缀），不能顺着错误值外推。
MAX_RAW_AMOUNT = 500_000
MAX_ANNUAL = 1_500_000


def _to_number(num: str, k: str | None) -> float:
    value = float(num.replace(",", ""))
    if k:
        value *= 1000
    return value


def parse(text: str | None) -> dict | None:
    """解析一段薪资描述。无法可靠解析时返回 None（不算失败）。

    返回：
      currency, period, basis(annual|annualized), min, max,
      annual_min, annual_max, raw, confident
    """
    if not text or not text.strip():
        return None
    raw = " ".join(text.split())

    # 货币
    currency = DEFAULT_CURRENCY
    if (m := _CURRENCY.search(raw)):
        code = (m.group(1) or "").upper()
        if code:
            currency = code
        else:
            sym = m.group("sym") or "$"
            currency = "AUD" if sym in ("$", "A$", "AU$") else \
                       "USD" if sym == "US$" else \
                       "NZD" if sym == "NZ$" else DEFAULT_CURRENCY

    # 周期
    period = None
    for name, pattern in _PERIODS:
        if pattern.search(raw):
            period = name
            break

    # 数字提取。分「强」「弱」：
    #   强 = 带货币前缀或 k 后缀（"$120k"、"AUD 650"）
    #   弱 = 光秃秃的数字（"AUD 400 - 700 per day" 里的 700）
    # 只有当同一段里已有强数字、且弱数字与它量级相当（十倍以内）时才收。
    # 这样 "AUD 400 - 700 per day" 能取到两个数，
    # 而 "Band 5 $78,154.75" 里的等级序号 5 会被丢掉。
    tokens = []
    for m in _TOKEN.finditer(raw):
        value = _to_number(m.group("num"), m.group("k"))
        if value <= 0 or value > MAX_RAW_AMOUNT:
            # 超过上限的单个数字几乎一定是源数据错误（数量级标注有误），
            # 直接丢弃而不是顺着错误值猜测
            continue
        tokens.append((value, bool(m.group("pre") or m.group("k"))))
    if not tokens:
        return None

    strong = [v for v, is_strong in tokens if is_strong]
    if strong:
        low_anchor, high_anchor = min(strong), max(strong)
        amounts = [v for v, is_strong in tokens
                   if is_strong or low_anchor / 10 <= v <= high_anchor * 10]
    else:
        # 整段一个货币标记都没有（"90,000 - 125,000"）：只认足够大的数字
        amounts = [v for v, _ in tokens if v >= _BARE_ANNUAL_FLOOR]
    if not amounts:
        return None

    # 没写单位时：只有足够大才敢当年薪；小数字又没单位就不猜
    confident = True
    if period is None:
        if max(amounts) >= _BARE_ANNUAL_FLOOR:
            period = "year"
        else:
            return None

    low, high = min(amounts), max(amounts)
    single = len(amounts) == 1

    # "up to X" 只有上限；"X and above" 只有下限
    if _MAX_ONLY.search(raw):
        low, single = None, False
    elif single and _MIN_ONLY.search(raw):
        high = None

    if low is not None and high is not None and low > high:
        low, high = high, low

    mult = _MULTIPLIER[period]
    annual_low = low * mult if low is not None else None
    annual_high = high * mult if high is not None else None

    # 年化后仍不合理的，直接放弃（宁可不给，也不要给个荒谬的数字）
    for value in (annual_low, annual_high):
        if value is not None and value > MAX_ANNUAL:
            return None

    return {
        "currency": currency,
        "period": period,
        "basis": "annual" if period == "year" else "annualized",
        "min": low,
        "max": high,
        "annual_min": annual_low,
        "annual_max": annual_high,
        "confident": confident,
        "raw": raw[:120],
    }


def format_short(parsed: dict | None) -> str:
    """一行摘要，给面板和报告用。"""
    if not parsed:
        return ""
    symbol = {"AUD": "A$", "USD": "US$", "NZD": "NZ$", "GBP": "£", "EUR": "€",
              "SGD": "S$"}.get(parsed["currency"], "")

    def money(value):
        return f"{symbol}{value/1000:.0f}k" if value and value >= 1000 else f"{symbol}{value:.0f}"

    low, high = parsed["min"], parsed["max"]
    if low is not None and high is not None and low != high:
        body = f"{money(parsed['annual_min'])}–{money(parsed['annual_max'])}/年"
    elif low is not None:
        body = f"{money(parsed['annual_min'])}/年起"
    elif high is not None:
        body = f"最高 {money(parsed['annual_max'])}/年"
    else:
        return ""

    if parsed["basis"] == "annualized":
        unit = {"hour": "时薪", "day": "日薪", "month": "月薪"}[parsed["period"]]
        return f"≈{body}（原为{unit} {money(low if low is not None else high)}）"
    return body


def classify(parsed: dict | None, bands: list[dict], contract_shift: int = 0) -> tuple[str, int]:
    """按年化金额给难度档位与打分调整。

    contract_shift：合同岗（时薪/日薪折算来的）**档位门槛整体上移**。
    因为 $120/hr 的合同报价年化后是 $237k，但它对应的资历未必比
    $140k 的永久岗高 —— 直接比较会把所有合同岗都误判成资深。
    """
    if not parsed:
        return "", 0
    anchor = parsed.get("annual_max") or parsed.get("annual_min")
    if not anchor:
        return "", 0
    if parsed.get("basis") == "annualized":
        anchor -= contract_shift
    for band in bands:
        ceiling = band.get("max")
        if ceiling is None or anchor < ceiling:
            return band.get("label", ""), int(band.get("hint", 0))
    return "", 0


# 从正文里找薪资时用的线索词。必须有其一，避免把
# "$5 million revenue" 这类句子当成薪资。
_SALARY_CUE = re.compile(
    r'salary|remuneration|package|per\s+annum|p\.a\.|base\s+pay|daily\s+rate|'
    r'hourly\s+rate|rate\s+of\s+pay|compensation|salary\s+band|pay\s+range',
    re.IGNORECASE)
_MONEY_HINT = re.compile(r'\$|\bAUD\b|\bUSD\b', re.IGNORECASE)

# 年化后落在这个窗口外的一律丢掉 —— 宁可没有，也不要给个离谱的数字
_PLAUSIBLE = (40_000, 600_000)


def extract(field_text: str | None, description: str | None,
            bands: list[dict], contract_shift: int = 0) -> dict | None:
    """先看平台字段，没有再扫正文。

    正文扫描要求句子同时含「货币符号」和「薪资线索词」，
    再用金额窗口兜底 —— 三道过滤下来误报很少。
    """
    parsed = parse(field_text)
    origin = "field"

    if parsed is None and description:
        from .render import _SENT_SPLIT, normalize   # 局部导入，避免循环依赖
        for sentence in _SENT_SPLIT.split(normalize(description)):
            if not _MONEY_HINT.search(sentence) or not _SALARY_CUE.search(sentence):
                continue
            candidate = parse(sentence)
            if not candidate:
                continue
            anchor = candidate.get("annual_max") or candidate.get("annual_min") or 0
            if _PLAUSIBLE[0] <= anchor <= _PLAUSIBLE[1]:
                parsed, origin = candidate, "description"
                break

    if parsed is None:
        return None

    band, hint = classify(parsed, bands, contract_shift)
    parsed.update({"band": band, "hint": hint, "origin": origin})
    return parsed
