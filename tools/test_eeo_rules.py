"""EEO 护栏回归（合成数据，离线、不写库）。

citizenship.requirement 的 EEO / 多元包容护栏使用 none_scope: sentence：
样板语只否决与它**同分句**的候选命中，不再因为广告里存在样板语就抹掉
另一段独立、明确的门槛句。本脚本用自主合成文本、手写固定 expected
验证这一点，并回归非目标规则的 text / overlap 语义没有被改动。

用法（不联网、不写库）：
    .venv/bin/python tools/test_eeo_rules.py
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jobs.config import load_rules                    # noqa: E402
from jobs.filters import evaluate, normalize_text     # noqa: E402

RULES = load_rules()
failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{('  ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


def decide(text: str, rules: dict | None = None) -> dict:
    return evaluate(normalize_text(text), rules or RULES)


def _rules_without_eeo_guard() -> dict:
    """只在本进程内存里复制规则，摘掉 citizenship.requirement 的 EEO 与
    welcome 护栏，用来证明合成案例确实会被无护栏规则命中（防止「未触发任何
    模式」的假护栏）。"""
    clone = copy.deepcopy(RULES)
    for rule in clone["exclude"]:
        if rule["id"] == "citizenship.requirement":
            rule.pop("none", None)
            rule.pop("none_scope", None)
            rule.pop("weak_none", None)
            rule.pop("weak_none_scope", None)
    return clone


def test_pure_noise_keeps() -> None:
    print("== 1. 纯 EEO / 多元噪音必须保留 ==")
    wide = ("Eligibility: we are an equal employment opportunity employer and welcome "
            "all Australian citizens and visa holders to apply.")
    cases = [
        ("equal employment opportunity（宽窗口可能命中）", wide),
        ("regardless of citizenship",
         "We recruit on merit regardless of citizenship, national origin or disability."),
        ("citizenship, marital status",
         "All qualified applicants are considered without regard to religion, "
         "citizenship, marital status, disability or age."),
        ("citizenship, national origin",
         "We are committed to diversity and do not discriminate on the basis of "
         "citizenship, national origin, gender or age."),
        # 独立验收反例：宽资格标题窗口误命中欢迎语（无明确要求）必须保留
        ("welcome + regardless（宽窗口未覆盖噪音）",
         "Eligibility: Australian citizens and visa holders are welcome regardless "
         "of citizenship, national origin or disability."),
        ("welcome + 后接独立 EEO 句",
         "Eligibility: Australian citizens and visa holders are welcome. We are an "
         "equal employment opportunity employer."),
        ("独立 EEO 句 + welcome",
         "We are an equal employment opportunity employer. Eligibility: Australian "
         "citizens and visa holders are welcome."),
    ]
    for label, text in cases:
        d = decide(text)
        check(label + " -> 保留", d["is_excluded"] is False, str(d["rule_id"]))

    # 区分力证明：这段宽窗口文本在摘掉护栏后确实会被 citizenship.requirement 命中
    unguarded = decide(wide, _rules_without_eeo_guard())
    check("宽窗口案例在无护栏时会命中（证明护栏测试有区分力）",
          unguarded["is_excluded"] is True, str(unguarded["rule_id"]))


def test_requirement_not_cancelled() -> None:
    print("== 2. 独立要求不被 EEO 抵消 ==")
    cases = [
        ("要求后接 EEO 句",
         "To be eligible you must be an Australian citizen. We are an equal employment "
         "opportunity employer."),
        ("EEO 在要求之前",
         "We are an equal employment opportunity employer. You must be an Australian "
         "citizen."),
        ("不同段落（换行被折叠）",
         "We are an equal employment opportunity employer.\n\nAll staff must be "
         "Australian citizens."),
        ("较长通用无关文本分隔",
         "We are an equal employment opportunity employer. Our teams work across many "
         "offices and time zones to support customers around the clock, with structured "
         "onboarding and mentoring for every new starter. Candidates must hold "
         "Australian citizenship to be appointed."),
        ("PR 独立要求 + EEO",
         "We are an equal employment opportunity employer. Candidates must have "
         "permanent residency for this role."),
        # 独立验收反例：明确要求与 EEO 同句 / 无标点段落，不能因同句有 EEO 就放行
        ("EEO 与要求同句（and 连接）",
         "We are an equal employment opportunity employer and applicants must be "
         "Australian citizens."),
        ("EEO 与 PR 要求同句（and 连接）",
         "Candidates must have permanent residency for this role and we are an equal "
         "employment opportunity employer."),
        ("要求在前、EEO 在后（换行折叠）",
         "You must be an Australian citizen\n\nWe are an equal employment opportunity "
         "employer."),
        ("无标点段落：EEO 在前、要求在后",
         "We are an equal employment opportunity employer applicants must be Australian "
         "citizens"),
        ("无标点段落：要求在前、EEO 在后",
         "Applicants must be Australian citizens and we are an equal employment "
         "opportunity employer"),
    ]
    for label, text in cases:
        d = decide(text)
        check(label + " -> 排除", d["is_excluded"] is True,
              f"rule={d['rule_id']} matched={d['matched_text']!r}")

    # 换行必须是真换行：源码里是 \n（不是字面量），normalize_text 折叠后不含换行。
    nl_req_first = ("You must be an Australian citizen\n\nWe are an equal employment "
                    "opportunity employer.")
    check("换行输入（要求在前）确有真实换行", "\n" in nl_req_first)
    check("normalize_text 折叠后无换行（要求在前）",
          "\n" not in normalize_text(nl_req_first))
    check("要求在前、EEO 在后（换行）-> 排除",
          decide(nl_req_first)["is_excluded"] is True)

    nl_eeo_first = ("We are an equal employment opportunity employer\n\nYou must be an "
                    "Australian citizen.")
    check("换行输入（EEO 在前）确有真实换行", "\n" in nl_eeo_first)
    check("normalize_text 折叠后无换行（EEO 在前）",
          "\n" not in normalize_text(nl_eeo_first))
    check("EEO 在前、要求在后（换行）-> 排除",
          decide(nl_eeo_first)["is_excluded"] is True)


def test_provenance_multiple_hits() -> None:
    print("== 3. 多候选命中：噪音在前/在后，matched_text 指真实要求 ==")
    # 只让 citizenship.requirement 自己的模式命中：第一分句是 EEO 欢迎语
    # （宽窗口候选），第二分句是真实要求。matched_text 若含 "Eligibility"
    # 就说明它取的是被护栏否决的那个候选。
    text = ("Eligibility: we are an equal employment opportunity employer and welcome "
            "all Australian citizens to apply. Candidates must hold Australian "
            "citizenship.")
    d = decide(text)
    check("噪音在前仍识别后面的真实要求", d["is_excluded"] is True, str(d["rule_id"]))
    mt = d["matched_text"] or ""
    check("matched_text 指向真实要求而非 EEO 候选",
          bool(mt) and "australian" in mt.lower() and "equal employment" not in mt.lower()
          and "eligibility" not in mt.lower(), repr(mt))

    text2 = ("Candidates must hold Australian citizenship. We are an equal employment "
             "opportunity employer.")
    d2 = decide(text2)
    check("噪音在后仍排除", d2["is_excluded"] is True, str(d2["rule_id"]))
    mt2 = d2["matched_text"] or ""
    check("matched_text 仍指向要求",
          bool(mt2) and "australian" in mt2.lower()
          and "equal employment" not in mt2.lower(), repr(mt2))

    # 欢迎语（宽窗口候选）与独立要求并存：欢迎候选被 welcome 护栏否决，
    # 真实要求仍被识别，matched_text 指向要求。
    w1 = ("Eligibility: Australian citizens and visa holders are welcome. Candidates "
          "must hold Australian citizenship.")
    d_w1 = decide(w1)
    check("欢迎语在前 + 独立要求 -> 排除", d_w1["is_excluded"] is True,
          str(d_w1["rule_id"]))
    mt_w1 = d_w1["matched_text"] or ""
    check("matched_text 指向要求而非欢迎语",
          "hold australian citizenship" in mt_w1.lower()
          and "welcome" not in mt_w1.lower(), repr(mt_w1))

    w2 = ("Candidates must hold Australian citizenship. Eligibility: Australian "
          "citizens and visa holders are welcome.")
    d_w2 = decide(w2)
    check("独立要求在前 + 欢迎语 -> 排除", d_w2["is_excluded"] is True,
          str(d_w2["rule_id"]))
    mt_w2 = d_w2["matched_text"] or ""
    check("matched_text 仍指向要求（要求在前）",
          "hold australian citizenship" in mt_w2.lower()
          and "welcome" not in mt_w2.lower(), repr(mt_w2))


def test_semicolon_and_normalization() -> None:
    print("== 4. 同一句分号 / 大小写 / 换行 ==")
    semi = ("To be eligible you must be an Australian citizen; we are an equal "
            "employment opportunity employer.")
    d = decide(semi)
    check("同一句里分号隔开互不抵消", d["is_excluded"] is True,
          f"matched={d['matched_text']!r}")

    upper = ("TO BE ELIGIBLE YOU MUST BE AN AUSTRALIAN CITIZEN.\n\n"
             "WE ARE AN EQUAL EMPLOYMENT OPPORTUNITY EMPLOYER.")
    d2 = decide(upper)
    check("大小写与换行经 normalize_text 后稳定", d2["is_excluded"] is True,
          str(d2["rule_id"]))
    check("normalize_text 确实折叠了换行", normalize_text(upper).count("\n") == 0)


def test_other_keeps() -> None:
    print("== 5. 多元欢迎语 / PR 叙述 / visa 开放析取 ==")
    cases = [
        ("多元包容欢迎语",
         "We encourage applications from Aboriginal and Torres Strait Islander peoples, "
         "people with disability and people from culturally diverse backgrounds."),
        ("仅 PR 叙述",
         "International graduates may start on a temporary contract and transition to "
         "an ongoing role once they obtain permanent residency."),
        ("visa 开放析取",
         "You must be an Australian Citizen, Permanent Resident or hold a valid work "
         "permit or visa with full unrestricted working rights."),
    ]
    for label, text in cases:
        d = decide(text)
        check(label + " -> 保留", d["is_excluded"] is False, str(d["rule_id"]))


def test_scope_semantics() -> None:
    print("== 6. text / overlap / sentence 语义与非目标规则回归 ==")
    text_rule = {"version": 0, "overrides": [], "exclude": [
        {"id": "t.text", "any": ["agile"], "none": ["waterfall"]}]}
    check("text：无噪音触发",
          evaluate(normalize_text("agile delivery"), text_rule)["is_excluded"] is True)
    check("text：远处出现噪音也整条否决",
          evaluate(normalize_text("agile delivery. we also discuss waterfall methods"),
                   text_rule)["is_excluded"] is False)

    ov_rule = {"version": 0, "overrides": [], "exclude": [
        {"id": "t.overlap", "any": ["clearance"], "none": ["police clearance"],
         "none_scope": "overlap"}]}
    check("overlap：重叠的命中被否决",
          evaluate(normalize_text("police clearance"), ov_rule)["is_excluded"] is False)
    d_ov = evaluate(normalize_text("security clearance"), ov_rule)
    check("overlap：不重叠的命中仍触发", d_ov["is_excluded"] is True,
          str(d_ov["matched_text"]))

    sent_rule = {"version": 0, "overrides": [], "exclude": [
        {"id": "t.sentence", "any": ["must"], "none": ["equal employment opportunity"],
         "none_scope": "sentence"}]}
    check("sentence：同分句噪音否决",
          evaluate(normalize_text("we are an equal employment opportunity employer "
                                  "and must comply"), sent_rule)["is_excluded"] is False)
    check("sentence：其他分句的要求不受影响",
          evaluate(normalize_text("must comply. we are an equal employment opportunity "
                                  "employer"), sent_rule)["is_excluded"] is True)

    d_pc = decide("This role requires a current National Police Clearance and an "
                  "Australian Defence Security Clearance.")
    check("police + security clearance 并列仍排除", d_pc["is_excluded"] is True,
          str(d_pc["rule_id"]))
    d_pc_only = decide("The successful applicant must obtain a National Police "
                       "Clearance before starting.")
    check("只有 police clearance 仍保留", d_pc_only["is_excluded"] is False,
          str(d_pc_only["rule_id"]))


def test_provenance_shape() -> None:
    print("== 7. provenance 字段形状与框架自检 ==")
    d = decide("To be eligible you must be an Australian citizen. We are an equal "
               "employment opportunity employer.")
    check("命中：is_excluded True", d["is_excluded"] is True)
    check("命中：rule_kind=exclude", d["rule_kind"] == "exclude", str(d["rule_kind"]))
    check("命中：rule_id 可追溯到 citizenship.requirement",
          "citizenship.requirement" in (d["rule_id"] or ""), str(d["rule_id"]))
    check("命中：matched_text 非空且不是 EEO 噪音",
          bool(d["matched_text"]) and "equal employment" not in d["matched_text"].lower(),
          repr(d["matched_text"]))
    check("命中：rules_version == 8", d["rules_version"] == 8, str(d["rules_version"]))

    none = decide("We build reliable software for our customers.")
    check("未命中：字段形状保留",
          none == {"is_excluded": False, "rule_id": None, "rule_kind": None,
                   "matched_text": None, "rules_version": 8}, str(none))

    ov = decide("You must be an Australian Citizen, Permanent Resident or hold a valid "
                "work permit or visa with full unrestricted working rights.")
    check("override：字段形状保留",
          ov["is_excluded"] is False and ov["rule_kind"] == "override"
          and ov["rule_id"] == "visa.or", str(ov))

    # 框架自检：同一判定若把 expected 故意写反，必须被判为失败（只在内存）
    probe = decide("To be eligible you must be an Australian citizen.")
    deliberately_wrong = (probe["is_excluded"] == False)   # noqa: E712
    check("框架自检：故意写反的 expected 会被判失败", deliberately_wrong is False,
          f"实际 is_excluded={probe['is_excluded']}")


def main() -> int:
    print(f"规则版本 {RULES.get('version')}")
    test_pure_noise_keeps()
    test_requirement_not_cancelled()
    test_provenance_multiple_hits()
    test_semicolon_and_normalization()
    test_other_keeps()
    test_scope_semantics()
    test_provenance_shape()
    print()
    if failures:
        print(f"失败 {len(failures)} 项：")
        for item in failures:
            print(f"  - {item}")
        return 2
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
