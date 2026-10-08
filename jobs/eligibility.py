"""Eligibility profile：纯解析与校验，不读 DB / 网络 / 全局配置。

本模块只把 config 里的 `eligibility` 段落解析成一个稳定的**能力快照**，
**不接入现有过滤**（返回的 filtering_mode 恒为 "legacy"）。这里的 preset
是**本项目自己的软件配置模板**，不是对任何个人法律资格的确认或推断：
  - None 表示「未知 / 未设置」，不是 false；
  - max_weekly_hours=None 不表示「无限制工作权」；
  - 不从公民身份推断可持某级保密许可，也不内置学生签证的法律小时数。

输入 config 必须是 dict；缺失 eligibility 时按既有迁移口径返回 485 兼容模板
（config_source="legacy_default"）。显式 eligibility 必须严格合规，否则抛
带字段路径的 ValueError，绝不静默退回 485。

纯函数：不修改输入，每次返回独立对象，可 JSON 序列化。
"""

from __future__ import annotations

import copy
import math

# 能力字段顺序固定，返回结构按此顺序输出。
CAPABILITY_FIELDS = (
    "australian_citizen",
    "permanent_resident",
    "unrestricted_work_rights",
    "requires_sponsorship",
    "security_clearance_eligible",
    "max_weekly_hours",
)

# 前五个是布尔/None，最后一个是小时数/None。
BOOL_FIELDS = CAPABILITY_FIELDS[:-1]
HOURS_FIELD = CAPABILITY_FIELDS[-1]

# eligibility 段落只允许这两个键。
ALLOWED_ELIGIBILITY_KEYS = ("profile", "custom")

# 固定 preset。custom 单独处理，不在这里。
PRESETS: dict[str, dict] = {
    "citizen": {
        "australian_citizen": True,
        "permanent_resident": False,
        "unrestricted_work_rights": True,
        "requires_sponsorship": False,
        "security_clearance_eligible": None,
        "max_weekly_hours": None,
    },
    "permanent_resident": {
        "australian_citizen": False,
        "permanent_resident": True,
        "unrestricted_work_rights": True,
        "requires_sponsorship": False,
        "security_clearance_eligible": None,
        "max_weekly_hours": None,
    },
    "485": {
        "australian_citizen": False,
        "permanent_resident": False,
        "unrestricted_work_rights": True,
        "requires_sponsorship": False,
        "security_clearance_eligible": False,
        "max_weekly_hours": None,
    },
    "student_visa": {
        "australian_citizen": False,
        "permanent_resident": False,
        "unrestricted_work_rights": None,
        "requires_sponsorship": None,
        "security_clearance_eligible": False,
        "max_weekly_hours": None,
    },
}

PRESET_NAMES = tuple(PRESETS)
PROFILE_NAMES = PRESET_NAMES + ("custom",)


def _fail(path: str, message: str) -> ValueError:
    return ValueError(f"{path}: {message}")


def _validate_bool_or_none(path: str, value):
    if value is None:
        return None
    # 必须原生 bool：字符串 "false"/"true"、数字 0/1 都拒绝，不做 bool() 转换。
    if type(value) is bool:
        return value
    raise _fail(path, f"只接受布尔值或 null，收到类型 {type(value).__name__}"
                      "（不把字符串/数字转 bool）")


def _validate_hours(path: str, value):
    if value is None:
        return None
    if type(value) is bool:
        raise _fail(path, "只接受正的有限数字或 null，不接受布尔")
    if not isinstance(value, (int, float)):
        raise _fail(path, f"只接受正的有限数字或 null，收到类型 {type(value).__name__}")
    if not math.isfinite(value) or value <= 0:
        raise _fail(path, "只接受正的有限数字或 null（不接受 0、负数、NaN、Infinity）")
    return value


def _resolve_custom(custom) -> dict:
    if not isinstance(custom, dict):
        raise _fail("eligibility.custom",
                    f"必须是对象，收到 {type(custom).__name__}")
    if not custom:
        raise _fail("eligibility.custom", "不能为空对象")
    if any(not isinstance(k, str) for k in custom):
        raise _fail("eligibility.custom", "键必须是字符串（能力字段名）")
    missing = [f for f in CAPABILITY_FIELDS if f not in custom]
    if missing:
        raise _fail("eligibility.custom", f"缺少字段 {missing}")
    if any(k not in CAPABILITY_FIELDS for k in custom):
        raise _fail("eligibility.custom",
                    f"含未知字段；只允许 {list(CAPABILITY_FIELDS)}")
    caps: dict = {}
    for field in CAPABILITY_FIELDS:
        path = f"eligibility.custom.{field}"
        if field == HOURS_FIELD:
            caps[field] = _validate_hours(path, custom[field])
        else:
            caps[field] = _validate_bool_or_none(path, custom[field])
    return caps


def _result(profile: str, capabilities: dict, source: str) -> dict:
    return {
        "profile": profile,
        "capabilities": capabilities,
        "config_source": source,
        "filtering_mode": "legacy",
    }


def resolve_eligibility(config: dict) -> dict:
    """把 config 解析成 eligibility 快照。纯函数，不读 DB / 网络 / 全局配置。"""
    if not isinstance(config, dict):
        raise _fail("config", f"必须是 dict，收到类型 {type(config).__name__}")

    if "eligibility" not in config:
        # 既有项目默认口径的迁移模板；不是对个人签证权利的确认。
        return _result("485", copy.deepcopy(PRESETS["485"]), "legacy_default")

    eligibility = config["eligibility"]
    if not isinstance(eligibility, dict):
        raise _fail("eligibility",
                    f"必须是对象，收到类型 {type(eligibility).__name__}")
    if not eligibility:
        raise _fail("eligibility", "不能为空对象（缺省请删除 eligibility 段落）")
    if any(not isinstance(k, str) for k in eligibility):
        raise _fail("eligibility", "键必须是字符串；只允许 profile / custom")
    if any(k not in ALLOWED_ELIGIBILITY_KEYS for k in eligibility):
        raise _fail("eligibility",
                    f"含未知字段；只允许 {list(ALLOWED_ELIGIBILITY_KEYS)}")
    if "profile" not in eligibility:
        raise _fail("eligibility", "缺少 profile")

    raw = eligibility["profile"]
    if isinstance(raw, bool):
        raise _fail("eligibility.profile", "必须是字符串，收到类型 bool")
    if isinstance(raw, int):
        # YAML 裸整数 485 兼容并归一为字符串；其他整数不是合法 profile。
        if raw == 485:
            profile = "485"
        else:
            raise _fail("eligibility.profile",
                        "未知 profile（非字符串只兼容 485）")
    elif isinstance(raw, str):
        profile = raw
    else:
        raise _fail("eligibility.profile",
                    f"必须是字符串，收到类型 {type(raw).__name__}")
    if not profile:
        raise _fail("eligibility.profile", "不能是空字符串")

    if profile == "custom":
        if "custom" not in eligibility:
            raise _fail("eligibility.custom",
                        "profile=custom 时必须提供 custom 对象")
        return _result("custom", _resolve_custom(eligibility["custom"]), "explicit")

    if profile not in PRESETS:
        raise _fail("eligibility.profile",
                    f"未知 profile（可选 {list(PROFILE_NAMES)}）")
    if "custom" in eligibility:
        raise _fail("eligibility.custom", "preset profile 不允许携带 custom")
    return _result(profile, copy.deepcopy(PRESETS[profile]), "explicit")
