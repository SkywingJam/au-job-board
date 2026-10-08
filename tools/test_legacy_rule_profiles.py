"""legacy_rule_profiles 回归：开关表、旧引擎一致性与安全边界。

全部使用手写合成的 rules / text / config / expected，不读生产 DB、真实 JD 或
私人档案。唯一的生产读取是 `rules.yaml`，且只核对 exclude / override 的 id
表与数量，不打印或比较任何真实 pattern / desc。

覆盖：

  1. 缺段 / 485 / custom / 显式全开启与旧 `filters.evaluate` 逐字段一致，
     顺序、`version`、override 返回保持。
  2. 五 profile 预设表与合成命中，含混合规则仍排除的限制；custom 与显式覆盖优先。
  3. 逐条关闭 exclude、多命中只禁一个、全关 exclude；override 开 / 关 / 顺序；
     无命中时 override 不能伪造命中。
  4. 非资格规则与 any / none / weak / none_scope 仍走旧引擎；text 原样传入、
     输入不被修改、返回隔离、非法配置 / 规则安全报错。
  5. 不调用候选解析器 / 不访问生产资源的接口证据。

用法：
    PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tools/test_legacy_rule_profiles.py
"""

from __future__ import annotations

import ast
import copy
import importlib.util
import json
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml                                          # noqa: E402
from jobs import db as database                      # noqa: E402
from jobs import filters as filters_mod              # noqa: E402
from jobs import legacy_rule_profiles as profiles    # noqa: E402

failures: list = []
checks = 0

REMOVED_MODULES = (
    "jobs.eligibility_runtime",
    "jobs.eligibility_extraction",
    "jobs.clearance_extraction",
    "jobs.eligibility_assessment",
    "jobs.handover_orchestration",
)


def check(label: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{('  ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


def synthetic_rules() -> dict:
    return {
        "version": 4242,
        "exclude": [
            {"id": "clearance.named", "any": [r"nv1"], "desc": "synthetic cue"},
            {"id": "citizenship.exclusive",
             "any": [r"must be an australian citizen"], "desc": "synthetic cue"},
            {"id": "citizenship.or_pr",
             "any": [r"citizen or permanent resident"], "desc": "synthetic cue"},
            {"id": "citizenship.requirement",
             "any": [r"australian citizenship"],
             "none": [r"welcome"], "none_scope": "overlap",
             "weak_any": [r"eligibility: australian citizen"],
             "weak_none": [r"welcome"], "weak_none_scope": "sentence",
             "desc": "synthetic cue"},
            {"id": "indigenous.identified",
             "any": [r"identified position"], "desc": "synthetic cue"},
            {"id": "adf.enlistment",
             "any": [r"enlist in the adf"], "desc": "synthetic cue"},
            {"id": "synthetic.overlap",
             "any": [r"citizens are welcome", r"citizens only"],
             "none": [r"welcome"], "none_scope": "overlap",
             "desc": "synthetic cue"},
            {"id": "synthetic.text_none",
             "any": [r"night shift"], "none": [r"no night shift"],
             "desc": "synthetic cue"},
        ],
        "overrides": [
            {"id": "visa.or",
             "any": [r"citizen or hold a valid work visa"], "desc": "synthetic cue"},
            {"id": "synthetic.second_override",
             "any": [r"open to all applicants"], "desc": "synthetic cue"},
        ],
        "tiers": {"T3": {"weight": 0}},
    }


def exclude_ids(rules: dict) -> list:
    return [r["id"] for r in rules.get("exclude") or []]


def override_ids(rules: dict) -> list:
    return [r["id"] for r in rules.get("overrides") or []]


EXCLUSIVE = "You must be an Australian citizen to apply."
OR_PR = "Must be a citizen or permanent resident."
MIXED = "Applicants must have Australian citizenship."
CLEARANCE = "This role requires NV1 clearance."
FULL = ("This role requires NV1 clearance. Applicants must have Australian "
        "citizenship. citizen or permanent resident.")
OVERRIDE = "Applicants must have Australian citizenship, or citizen or hold a valid work visa."
TWO_OVERRIDES = ("Applicants must have Australian citizenship. citizen or hold a "
                 "valid work visa. open to all applicants.")
OVERRIDE_ONLY = "citizen or hold a valid work visa."
WEAK_OPEN = "Eligibility: Australian citizen and we welcome everyone."
WEAK_SPLIT = "Eligibility: Australian citizen. We welcome everyone."

ALL_TEXTS = (EXCLUSIVE, OR_PR, MIXED, CLEARANCE, FULL, OVERRIDE, TWO_OVERRIDES,
             OVERRIDE_ONLY, WEAK_OPEN, WEAK_SPLIT, "nothing relevant here.")


def raises(fn):
    try:
        fn()
    except profiles.RuleProfileConfigError as exc:
        return exc
    except Exception as exc:                         # noqa: BLE001
        return exc
    return None


def test_legacy_default_equivalence() -> None:
    print("== 1. 缺段 / 485 / custom / 显式全开启 与旧 evaluate 一致 ==")
    rules = synthetic_rules()
    snapshot = copy.deepcopy(rules)
    all_on = {
        "legacy_rule_profile": {
            "profile": "485",
            "exclude": {rid: True for rid in exclude_ids(rules)},
            "overrides": {rid: True for rid in override_ids(rules)},
        }
    }
    configs = (
        ("缺段", {}),
        ("485", {"legacy_rule_profile": {"profile": "485"}}),
        ("custom 无覆盖", {"legacy_rule_profile": {"profile": "custom"}}),
        ("显式全开启", all_on),
    )
    for label, config in configs:
        same = True
        detail = ""
        for text in ALL_TEXTS:
            got = profiles.evaluate_profile_rules(text, config, rules)
            want = filters_mod.evaluate(text, rules)
            if got != want:
                same = False
                detail = f"{text[:24]!r}: {got} != {want}"
                break
        check(f"{label}：逐字段等于旧 evaluate", same, detail)

    selected = profiles.select_profile_rules({}, rules)
    check("selector 不改 version", selected["version"] == rules["version"])
    check("selector 保持 exclude 顺序",
          exclude_ids(selected) == exclude_ids(rules))
    check("selector 保持 overrides 顺序",
          override_ids(selected) == override_ids(rules))
    check("selector 不插入 enabled 字段",
          all("enabled" not in entry and "enabled" not in rule
              for namespace in ("exclude", "overrides")
              for entry in [selected[namespace]] for rule in entry))
    check("selector/evaluator 不改输入 rules", rules == snapshot)
    check("selector 返回独立对象", selected is not rules)


def test_profile_presets() -> None:
    print("== 2. 五 profile 预设表与混合规则限制 ==")
    rules = synthetic_rules()
    cases = {
        "citizen": {"citizenship.exclusive", "citizenship.or_pr"},
        "permanent_resident": {"citizenship.or_pr"},
        "485": set(),
        "student_visa": set(),
        "custom": set(),
    }
    for profile, expected_off in cases.items():
        resolution = profiles.resolve_rule_profile(
            {"legacy_rule_profile": {"profile": profile}}, rules)
        off = {rid for rid, on in resolution["exclude"].items() if not on}
        check(f"{profile}：exclude 默认关闭集正确", off == expected_off, str(sorted(off)))
        check(f"{profile}：未列出的 id 保持开启",
              all(resolution["exclude"][rid] for rid in exclude_ids(rules)
                  if rid not in expected_off))
        check(f"{profile}：overrides 全部开启",
              all(resolution["overrides"].values()))
        check(f"{profile}：config_source=explicit",
              resolution["config_source"] == "explicit")
        check(f"{profile}：profile 回显", resolution["profile"] == profile)
        check(f"{profile}：带粗粒度限制", isinstance(resolution["limits"], list)
              and all(isinstance(x, str) for x in resolution["limits"]))

    check("缺段 profile 为 null",
          profiles.resolve_rule_profile({}, rules)["profile"] is None)

    check("citizen 关闭 exclusive：合成命中放行",
          profiles.evaluate_profile_rules(
              EXCLUSIVE, {"legacy_rule_profile": {"profile": "citizen"}}, rules
          )["is_excluded"] is False)
    check("485 不关闭 exclusive：合成命中排除",
          profiles.evaluate_profile_rules(
              EXCLUSIVE, {"legacy_rule_profile": {"profile": "485"}}, rules
          )["rule_id"] == "citizenship.exclusive")
    check("citizen / PR 关闭 or_pr：合成命中放行",
          profiles.evaluate_profile_rules(
              OR_PR, {"legacy_rule_profile": {"profile": "citizen"}}, rules
          )["is_excluded"] is False
          and profiles.evaluate_profile_rules(
              OR_PR, {"legacy_rule_profile": {"profile": "permanent_resident"}},
              rules)["is_excluded"] is False)
    check("485 不关闭 or_pr：合成命中排除",
          profiles.evaluate_profile_rules(
              OR_PR, {"legacy_rule_profile": {"profile": "485"}}, rules
          )["rule_id"] == "citizenship.or_pr")

    mixed_excluded = all(
        profiles.evaluate_profile_rules(
            MIXED, {"legacy_rule_profile": {"profile": p}}, rules
        )["rule_id"] == "citizenship.requirement"
        for p in cases)
    check("混合规则 citizenship.requirement 所有 preset 仍排除", mixed_excluded)

    check("custom 显式关闭混合规则 -> 放行",
          profiles.evaluate_profile_rules(
              MIXED,
              {"legacy_rule_profile": {"profile": "custom",
                                       "exclude": {"citizenship.requirement": False}}},
              rules)["is_excluded"] is False)
    check("citizen 显式重新开启 exclusive -> 排除",
          profiles.evaluate_profile_rules(
              EXCLUSIVE,
              {"legacy_rule_profile": {"profile": "citizen",
                                       "exclude": {"citizenship.exclusive": True}}},
              rules)["rule_id"] == "citizenship.exclusive")
    check("citizen 显式开启 or_pr 且关闭 exclusive -> 按显式覆盖",
          profiles.evaluate_profile_rules(
              OR_PR,
              {"legacy_rule_profile": {
                  "profile": "citizen",
                  "exclude": {"citizenship.or_pr": True,
                              "citizenship.exclusive": False}}},
              rules)["rule_id"] == "citizenship.or_pr")


def test_switch_control() -> None:
    print("== 3. 逐条关闭 / 多命中 / override 开关与顺序 ==")
    rules = synthetic_rules()
    disable_clearance = {"legacy_rule_profile": {
        "profile": "485", "exclude": {"clearance.named": False}}}
    got = profiles.evaluate_profile_rules(FULL, disable_clearance, rules)
    check("只关 clearance.named：其余命中仍排除",
          got["is_excluded"] is True
          and "clearance.named" not in got["rule_id"]
          and "citizenship.requirement" in got["rule_id"], str(got))

    all_off = {"legacy_rule_profile": {
        "profile": "485",
        "exclude": {rid: False for rid in exclude_ids(rules)}}}
    check("全部 exclude 关闭：多命中也不排除",
          profiles.evaluate_profile_rules(FULL, all_off, rules)["is_excluded"]
          is False)

    default = {"legacy_rule_profile": {"profile": "485"}}
    got = profiles.evaluate_profile_rules(OVERRIDE, default, rules)
    check("override 开启：原 override 返回形状",
          got == {"is_excluded": False, "rule_id": "visa.or",
                  "rule_kind": "override",
                  "matched_text": got["matched_text"],
                  "rules_version": 4242}
          and got["rule_kind"] == "override", str(got))
    off = {"legacy_rule_profile": {
        "profile": "485", "overrides": {"visa.or": False}}}
    check("关闭 visa.or：排除恢复",
          profiles.evaluate_profile_rules(OVERRIDE, off, rules)["is_excluded"]
          is True)

    check("多 override 按原顺序取第一个",
          profiles.evaluate_profile_rules(TWO_OVERRIDES, default, rules)["rule_id"]
          == "visa.or")
    first_off = {"legacy_rule_profile": {
        "profile": "485", "overrides": {"visa.or": False}}}
    check("第一个 override 关闭后取第二个",
          profiles.evaluate_profile_rules(TWO_OVERRIDES, first_off, rules)["rule_id"]
          == "synthetic.second_override")
    both_off = {"legacy_rule_profile": {
        "profile": "485",
        "overrides": {rid: False for rid in override_ids(rules)}}}
    check("全部 override 关闭：只看 exclude",
          profiles.evaluate_profile_rules(TWO_OVERRIDES, both_off, rules)["is_excluded"]
          is True)

    no_hit = profiles.evaluate_profile_rules(OVERRIDE_ONLY, default, rules)
    check("无 exclude 命中：override 不伪造命中",
          no_hit == {"is_excluded": False, "rule_id": None, "rule_kind": None,
                     "matched_text": None, "rules_version": 4242}, str(no_hit))
    no_hit_off = profiles.evaluate_profile_rules(OVERRIDE_ONLY, both_off, rules)
    check("无命中 + override 关闭：形状不变",
          no_hit_off == no_hit, str(no_hit_off))


def test_engine_passthrough() -> None:
    print("== 4. 旧引擎 passthrough / text 保真 / 隔离 / 安全错误 ==")
    rules = synthetic_rules()
    default = {"legacy_rule_profile": {"profile": "485"}}

    check("none_scope=overlap 跳过与噪音重叠的候选",
          profiles.evaluate_profile_rules("citizens are welcome", default, rules
          )["is_excluded"] is False
          and profiles.evaluate_profile_rules("citizens only", default, rules
          )["rule_id"] == "synthetic.overlap")
    check("none_scope=text 全篇噪音否决整条",
          profiles.evaluate_profile_rules("night shift", default, rules)["is_excluded"]
          is True
          and profiles.evaluate_profile_rules(
              "night shift and no night shift", default, rules)["is_excluded"]
          is False)
    check("weak_any + weak_none_scope=sentence 同分句否决",
          profiles.evaluate_profile_rules(WEAK_OPEN, default, rules)["is_excluded"]
          is False
          and profiles.evaluate_profile_rules(WEAK_SPLIT, default, rules)["rule_id"]
          == "citizenship.requirement")

    raw = "  Leading and trailing  \n newline  "
    check("text 原样传入，不额外 normalize",
          profiles.evaluate_profile_rules(raw, default, rules)
          == filters_mod.evaluate(raw, rules))

    snapshot = copy.deepcopy(rules)
    first = profiles.resolve_rule_profile(default, rules)
    second = profiles.resolve_rule_profile(default, rules)
    check("resolver 返回相互隔离", first is not second
          and first["exclude"] is not second["exclude"])
    first["exclude"]["clearance.named"] = False
    check("修改返回值不影响下一次调用",
          profiles.resolve_rule_profile(default, rules)["exclude"]["clearance.named"]
          is True)
    result_a = profiles.evaluate_profile_rules(FULL, default, rules)
    result_b = profiles.evaluate_profile_rules(FULL, default, rules)
    check("evaluator 返回相互隔离", result_a is not result_b)
    result_a["rule_id"] = "tampered"
    check("修改结果不影响下一次调用",
          profiles.evaluate_profile_rules(FULL, default, rules)["rule_id"]
          != "tampered")
    selected = profiles.select_profile_rules(default, rules)
    selected["exclude"][0]["any"][0] = "tampered"
    check("修改 selected 不影响输入 rules",
          rules["exclude"][0]["any"][0] == "nv1" and rules == snapshot)
    check("resolver 输出可 JSON 序列化",
          json.loads(json.dumps(profiles.resolve_rule_profile(default, rules)))
          == profiles.resolve_rule_profile(default, rules))

    bad_configs = [
        "not-a-dict",
        {"legacy_rule_profile": "not-a-dict"},
        {"legacy_rule_profile": {}},
        {"legacy_rule_profile": {"exclude": {}}},
        {"legacy_rule_profile": {"profile": True}},
        {"legacy_rule_profile": {"profile": 486}},
        {"legacy_rule_profile": {"profile": None}},
        {"legacy_rule_profile": {"profile": "sentinel-secret"}},
        {"legacy_rule_profile": {"profile": "485", "extra": 1}},
        {"legacy_rule_profile": {"profile": "485", "exclude": "not-a-dict"}},
        {"legacy_rule_profile": {"profile": "485", "exclude": {"clearance.named": 1}}},
        {"legacy_rule_profile": {"profile": "485", "exclude": {"clearance.named": "true"}}},
        {"legacy_rule_profile": {"profile": "485", "overrides": {"visa.or": None}}},
        {"legacy_rule_profile": {"profile": "485", "exclude": {"sentinel-secret": True}}},
        {"legacy_rule_profile": {"profile": "485", "overrides": {"sentinel-secret": True}}},
        {"legacy_rule_profile": {"profile": "485", "exclude": {1: True}}},
    ]
    for index, config in enumerate(bad_configs):
        exc = raises(lambda c=config: profiles.resolve_rule_profile(c, rules))
        check(f"非法 config #{index + 1} 安全拒绝",
              isinstance(exc, profiles.RuleProfileConfigError),
              type(exc).__name__ if exc else "no-error")
    leak = raises(lambda: profiles.resolve_rule_profile(
        {"legacy_rule_profile": {"profile": "sentinel-secret"}}, rules))
    check("错误不回显 profile 值",
          leak is not None and "sentinel-secret" not in str(leak), str(leak))
    leak = raises(lambda: profiles.resolve_rule_profile(
        {"legacy_rule_profile": {"profile": "485",
                                 "exclude": {"sentinel-secret": True}}}, rules))
    check("错误不回显未知规则 id",
          leak is not None and "sentinel-secret" not in str(leak), str(leak))

    bad_rules = [
        "not-a-dict",
        {"exclude": "not-a-list"},
        {"exclude": ["not-a-dict"]},
        {"exclude": [{"any": ["x"]}]},
        {"exclude": [{"id": 1, "any": ["x"]}]},
        {"exclude": [{"id": "dup", "any": ["x"]}, {"id": "dup", "any": ["y"]}]},
        {"overrides": [{"id": "dup", "any": ["x"]}, {"id": "dup", "any": ["y"]}]},
    ]
    for index, bad_rules_obj in enumerate(bad_rules):
        exc = raises(lambda r=bad_rules_obj: profiles.resolve_rule_profile({}, r))
        check(f"非法 rules #{index + 1} 安全拒绝",
              isinstance(exc, profiles.RuleProfileConfigError),
              type(exc).__name__ if exc else "no-error")
    cross = {"exclude": [{"id": "same", "any": ["x"]}],
             "overrides": [{"id": "same", "any": ["y"]}]}
    check("同一 id 跨命名空间允许",
          raises(lambda: profiles.resolve_rule_profile({}, cross)) is None)
    empty_rules = profiles.select_profile_rules({}, {"version": 1})
    check("缺 exclude/overrides 不凭空添加",
          "exclude" not in empty_rules and "overrides" not in empty_rules)


def test_interface_evidence() -> None:
    print("== 5. 接口证据：不接候选模块 / 不访问生产资源 / id 表 ==")
    module_path = Path(profiles.__file__)
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            imported.add("." * node.level + (node.module or ""))
    imported.discard("__future__")
    check("模块只 import copy 与旧引擎", imported == {"copy", ".filters"},
          str(sorted(imported)))

    found = [name for name in REMOVED_MODULES
             if importlib.util.find_spec(name) is not None]
    check("候选模块不可 import", not found, str(found))

    rules = synthetic_rules()
    config = {"legacy_rule_profile": {"profile": "485"}}
    original_connect = database.connect
    original_socket = socket.socket

    def blocked(*_args, **_kwargs):
        raise AssertionError("production resource access")

    database.connect = blocked
    socket.socket = blocked
    try:
        profiles.resolve_rule_profile(config, rules)
        profiles.select_profile_rules(config, rules)
        profiles.evaluate_profile_rules("text", config, rules)
        outcome = "no-access"
    except AssertionError as exc:
        outcome = str(exc)
    finally:
        database.connect = original_connect
        socket.socket = original_socket
    check("函数不访问 DB / 网络", outcome == "no-access", outcome)

    production = yaml.safe_load((ROOT / "rules.yaml").read_text(encoding="utf-8"))
    prod_exclude = [r["id"] for r in production.get("exclude") or []]
    prod_overrides = [r["id"] for r in production.get("overrides") or []]
    check("production rules.yaml：8 exclude / 1 override",
          len(prod_exclude) == 8 and prod_overrides == ["visa.or"],
          f"{len(prod_exclude)}/{len(prod_overrides)}")
    check("production 预设关闭集只落在存在的 id 上",
          set(prod_exclude) >= {"citizenship.exclusive", "citizenship.or_pr"})
    for profile, expected_off in (
            ("citizen", {"citizenship.exclusive", "citizenship.or_pr"}),
            ("permanent_resident", {"citizenship.or_pr"}),
            ("485", set())):
        resolution = profiles.resolve_rule_profile(
            {"legacy_rule_profile": {"profile": profile}}, production)
        off = {rid for rid, on in resolution["exclude"].items() if not on}
        check(f"production {profile} 有效关闭集",
              off == expected_off and list(resolution["exclude"]) == prod_exclude,
              str(sorted(off)))


def test_rules_none_namespaces() -> None:
    print("== 6. rules 命名空间为 None：selector / evaluator 兼容旧空列表语义 ==")
    variants = (
        ("exclude=None", {"version": 7, "exclude": None}),
        ("overrides=None", {"version": 7, "overrides": None}),
        ("both=None", {"version": 7, "exclude": None, "overrides": None}),
        ("空 rules", {}),
        ("仅 version", {"version": 7}),
    )
    configs = (
        ("缺段", {}),
        ("citizen", {"legacy_rule_profile": {"profile": "citizen"}}),
        ("permanent_resident",
         {"legacy_rule_profile": {"profile": "permanent_resident"}}),
        ("485", {"legacy_rule_profile": {"profile": "485"}}),
        ("student_visa", {"legacy_rule_profile": {"profile": "student_visa"}}),
        ("custom", {"legacy_rule_profile": {"profile": "custom"}}),
        ("显式空映射",
         {"legacy_rule_profile": {"profile": "485", "exclude": {},
                                  "overrides": {}}}),
    )

    for label, rules in variants:
        snapshot = copy.deepcopy(rules)
        selected = profiles.select_profile_rules({}, rules)
        check(f"{label}：selector 不抛错且选择完成", isinstance(selected, dict))
        check(f"{label}：输入 rules 未被修改", rules == snapshot)
        check(f"{label}：selected 保留原始 None / 缺省",
              selected.get("exclude") == rules.get("exclude")
              and selected.get("overrides") == rules.get("overrides"))
        resolution = profiles.resolve_rule_profile({}, rules)
        check(f"{label}：resolver 有效映射为空",
              resolution["exclude"] == {} and resolution["overrides"] == {})

        for clabel, config in configs:
            same = all(
                profiles.evaluate_profile_rules(text, config, rules)
                == filters_mod.evaluate(text, rules)
                for text in ALL_TEXTS)
            check(f"{label} + {clabel}：evaluator 逐字段等于旧 evaluate", same)

    mixed = {"version": 7, "exclude": None, "overrides": None}
    exc = raises(lambda: profiles.select_profile_rules(
        {"legacy_rule_profile": {"profile": "485",
                                 "exclude": {"clearance.named": True}}}, mixed))
    check("rules 命名空间为 None 时显式未知 id 仍拒绝",
          isinstance(exc, profiles.RuleProfileConfigError),
          type(exc).__name__ if exc else "no-error")


def test_config_mapping_null() -> None:
    print("== 7. 配置映射：省略 / {} / null / 非法对象 ==")
    rules = synthetic_rules()
    accepted = (
        ("省略", {"legacy_rule_profile": {"profile": "485"}}),
        ("exclude={}", {"legacy_rule_profile": {"profile": "485", "exclude": {}}}),
        ("overrides={}", {"legacy_rule_profile": {"profile": "485",
                                                  "overrides": {}}}),
        ("两映射都为 {}", {"legacy_rule_profile": {
            "profile": "485", "exclude": {}, "overrides": {}}}),
    )
    for label, config in accepted:
        exc = raises(lambda c=config: profiles.resolve_rule_profile(c, rules))
        check(f"{label} 接受", exc is None,
              type(exc).__name__ if exc else "")

    for namespace in ("exclude", "overrides"):
        config = {"legacy_rule_profile": {"profile": "485", namespace: None}}
        exc = raises(lambda c=config: profiles.resolve_rule_profile(c, rules))
        check(f"{namespace}=null 安全拒绝",
              isinstance(exc, profiles.RuleProfileConfigError),
              type(exc).__name__ if exc else "no-error")
        exc = raises(lambda c=config: profiles.select_profile_rules(c, rules))
        check(f"{namespace}=null 在 selector 也拒绝",
              isinstance(exc, profiles.RuleProfileConfigError),
              type(exc).__name__ if exc else "no-error")
        exc = raises(lambda c=config: profiles.evaluate_profile_rules("x", c, rules))
        check(f"{namespace}=null 在 evaluator 也拒绝",
              isinstance(exc, profiles.RuleProfileConfigError),
              type(exc).__name__ if exc else "no-error")

    for value in ("not-a-dict", 1, [], True):
        config = {"legacy_rule_profile": {"profile": "485", "exclude": value}}
        exc = raises(lambda c=config: profiles.resolve_rule_profile(c, rules))
        check(f"exclude 非对象（{type(value).__name__}）安全拒绝",
              isinstance(exc, profiles.RuleProfileConfigError),
              type(exc).__name__ if exc else "no-error")

    exc = raises(lambda: profiles.resolve_rule_profile(
        {"legacy_rule_profile": {"profile": "485", "exclude": None}}, rules))
    check("null 错误安全且不回显值",
          isinstance(exc, profiles.RuleProfileConfigError)
          and "None" not in str(exc), str(exc))


def main() -> int:
    test_legacy_default_equivalence()
    test_profile_presets()
    test_switch_control()
    test_engine_passthrough()
    test_interface_evidence()
    test_rules_none_namespaces()
    test_config_mapping_null()
    print()
    if failures:
        print(f"失败 {len(failures)} 项：")
        for item in failures:
            print(f"  - {item}")
        return 2
    print(f"全部通过（{checks} 项检查）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
