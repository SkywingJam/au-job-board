"""原始规则＋启用开关＋五 profile 预设（纯内存，未接入 analyze / 面板）。

本模块只做一件事：按一个独立的 `legacy_rule_profile` 配置段，选择现有
`rules.yaml` 里哪些 `exclude` / `overrides` 条目参与旧 `filters.evaluate`
匹配，并把判定原样委托给旧引擎。它不新增提取器、不新增判定语义、不改 regex，
也不读取或消费旧的 `eligibility` 能力模板。

- `resolve_rule_profile(config, rules)`：解析配置段，返回 profile、来源、两个
  命名空间按原顺序的有效 bool 映射、显式覆盖与粗粒度限制。不含 regex / desc。
- `select_profile_rules(config, rules)`：返回独立的 deepcopy 规则对象，只移除
  被关闭的条目；其余顶层字段、`version`、规则内容与顺序逐字义不变。
- `evaluate_profile_rules(text, config, rules)`：直接调用
  `filters.evaluate(text, selected_rules)`，返回其原有 dict。

语义要点：

- 缺少 `legacy_rule_profile` 段：两个命名空间全部开启，无条件复现旧规则；
  旧 `eligibility` 段不会被读取，也不会偷偷启用预设。
- 预设关闭表只作用于输入 rules 中实际存在的 id：缺失的预设 id 不报错，也不
  凭空添加条目；未列出的新 id 默认开启。
- 显式 `exclude` / `overrides` 映射按规则 id 覆盖预设，值必须是原生 bool。
- 所有开关都是**软件规则选择**，不是个人法律资格判断。

纯函数：不读文件 / DB / 网络，不修改输入或全局状态，返回值相互隔离且可
JSON 序列化。不 import 候选提取器、三态判定或编排层。
"""

from __future__ import annotations

import copy

from .filters import evaluate

SECTION = "legacy_rule_profile"
PROFILES = ("citizen", "permanent_resident", "485", "student_visa", "custom")
SECTION_KEYS = ("profile", "exclude", "overrides")
NAMESPACES = ("exclude", "overrides")

# 预设默认关闭表：只列出「实际存在时会被关闭」的 id。所有其它 id 默认开启。
# 这是尽快恢复实用原始基线的保守开关表，不是身份资格模型。
_DEFAULT_DISABLED = {
    "citizen": {"exclude": ("citizenship.exclusive", "citizenship.or_pr"),
                "overrides": ()},
    "permanent_resident": {"exclude": ("citizenship.or_pr",), "overrides": ()},
    "485": {"exclude": (), "overrides": ()},
    "student_visa": {"exclude": (), "overrides": ()},
    "custom": {"exclude": (), "overrides": ()},
}

LIMITS = (
    "这些开关只选择运行哪些既有规则，不是个人法律资格判断。",
    "citizenship.requirement 是混合规则，所有 preset 默认仍开启；"
    "citizen / permanent_resident 仍可能被其中重叠条件排除。",
    "关闭 citizenship.or_pr 不保证 PR 岗位全部放行；visa.or 是全局 override，"
    "默认保留，关闭它只是去掉一条救回路径，不会更精确。",
    "student_visa 与 485 的默认开关相同，不表示工作权相同；"
    "不推断小时上限、也不从身份推断已持有许可。",
)


class RuleProfileConfigError(ValueError):
    """`legacy_rule_profile` 段或 rules 的形状错误。

    错误信息只给安全字段路径，不回显配置值、规则 id 之外的内容或敏感原文。
    """


def _validate_rules(rules):
    """校验 rules 根与两个命名空间的形状，返回 id 顺序表。

    缺 `exclude` / `overrides`（或显式 null）按旧语义视为空列表；
    显式的非列表值、非对象条目、缺失 / 非字符串 / 重复 id 拒绝。
    同一 id 允许同时出现在两个命名空间。
    """
    if not isinstance(rules, dict):
        raise RuleProfileConfigError("rules: 必须是对象")
    ids = {}
    for namespace in NAMESPACES:
        raw = rules.get(namespace)
        if raw is None:
            ids[namespace] = []
            continue
        if not isinstance(raw, list):
            raise RuleProfileConfigError(f"rules.{namespace}: 必须是列表")
        ordered = []
        seen = set()
        for entry in raw:
            if not isinstance(entry, dict):
                raise RuleProfileConfigError(f"rules.{namespace}: 条目必须是对象")
            rid = entry.get("id")
            if not isinstance(rid, str) or not rid:
                raise RuleProfileConfigError(
                    f"rules.{namespace}: 条目缺少非空字符串 id")
            if rid in seen:
                raise RuleProfileConfigError(f"rules.{namespace}: 含重复 id")
            seen.add(rid)
            ordered.append(rid)
        ids[namespace] = ordered
    return ids


def _normalize_profile(value):
    if isinstance(value, bool):
        raise RuleProfileConfigError(f"{SECTION}.profile: 必须是字符串 profile 名")
    if isinstance(value, int):
        if value == 485:
            return "485"
        raise RuleProfileConfigError(f"{SECTION}.profile: 未知 profile")
    if not isinstance(value, str):
        raise RuleProfileConfigError(f"{SECTION}.profile: 必须是字符串 profile 名")
    if value not in PROFILES:
        raise RuleProfileConfigError(f"{SECTION}.profile: 未知 profile")
    return value


def _parse_section(config):
    """返回 (profile | None, config_source, explicit_exclude, explicit_overrides)。"""
    if not isinstance(config, dict):
        raise RuleProfileConfigError("config: 必须是对象")
    if SECTION not in config:
        return None, "legacy_default", {}, {}
    section = config[SECTION]
    if not isinstance(section, dict):
        raise RuleProfileConfigError(f"{SECTION}: 必须是对象")
    if not section:
        raise RuleProfileConfigError(f"{SECTION}: 不能为空对象（缺省请删除该段）")
    for key in section:
        if not isinstance(key, str) or key not in SECTION_KEYS:
            raise RuleProfileConfigError(f"{SECTION}: 含未知字段")
    if "profile" not in section:
        raise RuleProfileConfigError(f"{SECTION}: 缺少 profile")
    profile = _normalize_profile(section["profile"])

    explicit = {}
    for namespace in NAMESPACES:
        # 只区分键是否存在：省略与 {} 等价；显式 null 不是省略，拒绝。
        if namespace not in section:
            explicit[namespace] = {}
            continue
        mapping = section[namespace]
        if not isinstance(mapping, dict):
            raise RuleProfileConfigError(f"{SECTION}.{namespace}: 必须是对象")
        for rid, value in mapping.items():
            if not isinstance(rid, str) or not rid:
                raise RuleProfileConfigError(
                    f"{SECTION}.{namespace}: 含非字符串规则 id")
            if not isinstance(value, bool):
                raise RuleProfileConfigError(
                    f"{SECTION}.{namespace}: 值必须是布尔")
        explicit[namespace] = dict(mapping)
    return profile, "explicit", explicit["exclude"], explicit["overrides"]


def _effective(ordered_ids, disabled, explicit):
    result = {}
    for rid in ordered_ids:
        state = rid not in disabled
        if rid in explicit:
            state = explicit[rid]
        result[rid] = state
    return result


def resolve_rule_profile(config: dict, rules: dict) -> dict:
    """解析 `legacy_rule_profile`，返回安全、隔离、可 JSON 序列化的描述。

    返回键：`profile`（缺段为 None）、`config_source`
    （`legacy_default` / `explicit`）、`exclude` / `overrides`
    （按 rules 原顺序的 id -> bool 有效映射）、`explicit_exclude` /
    `explicit_overrides`（只含显式给出的项）、`limits`（粗粒度限制）。
    不含 regex、真实 desc 或 JD 文本。
    """
    ids = _validate_rules(rules)
    profile, source, explicit_exclude, explicit_overrides = _parse_section(config)

    for namespace, explicit in (("exclude", explicit_exclude),
                                ("overrides", explicit_overrides)):
        for rid in explicit:
            if rid not in ids[namespace]:
                raise RuleProfileConfigError(
                    f"{SECTION}.{namespace}: 含未知规则 id")

    if profile is None:
        disabled_exclude = frozenset()
        disabled_overrides = frozenset()
    else:
        disabled_exclude = frozenset(_DEFAULT_DISABLED[profile]["exclude"])
        disabled_overrides = frozenset(_DEFAULT_DISABLED[profile]["overrides"])

    return {
        "profile": profile,
        "config_source": source,
        "exclude": _effective(ids["exclude"], disabled_exclude, explicit_exclude),
        "overrides": _effective(ids["overrides"], disabled_overrides,
                                explicit_overrides),
        "explicit_exclude": dict(explicit_exclude),
        "explicit_overrides": dict(explicit_overrides),
        "limits": list(LIMITS),
    }


def select_profile_rules(config: dict, rules: dict) -> dict:
    """返回独立的 deepcopy 规则对象，只移除被关闭的条目。

    不改规则内容、不加 `enabled` 字段、不重排、不改 `version` 与其它顶层
    字段；两个命名空间的列表按原顺序过滤。

    rules 的 `exclude` / `overrides` 为 `None` 时按旧引擎的空列表语义处理：
    跳过过滤并**保留返回对象中的原始 `None` 值**；缺该命名空间时同样保持缺省。
    """
    resolution = resolve_rule_profile(config, rules)
    selected = copy.deepcopy(rules)
    for namespace in NAMESPACES:
        if rules.get(namespace) is None:
            continue
        effective = resolution[namespace]
        selected[namespace] = [
            entry for entry in selected[namespace] if effective[entry["id"]]
        ]
    return selected


def evaluate_profile_rules(text: str, config: dict, rules: dict) -> dict:
    """按 profile 选择规则后，直接委托 `filters.evaluate`。

    text 与旧 evaluate 的输入完全一致，不额外 normalize、不增加判定语义。
    profile 元信息请单独调用 `resolve_rule_profile`。
    """
    return evaluate(text, select_profile_rules(config, rules))
