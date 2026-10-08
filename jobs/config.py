"""配置与规则加载。

config.yaml -> 抓什么
rules.yaml  -> 怎么筛（唯一的调参面）
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str) -> dict:
    path = ROOT / name
    if not path.exists():
        raise FileNotFoundError(f"缺少配置文件: {path}")
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_config(path: str | Path | None = None) -> dict:
    if path is None:
        return _load("config.yaml")
    with Path(path).open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_rules(path: str | Path | None = None) -> dict:
    if path is None:
        return _load("rules.yaml")
    with Path(path).open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def read_config_root(path: str | Path | None = None):
    """读取配置的**原始根值**，不做 `or {}` 兜底。

    与 load_config 用相同的默认路径（ROOT/config.yaml）与 --config 路径语义，
    但保留 yaml.safe_load 的原始结果：空文档返回 None，非 dict 根原样返回；
    文件缺失抛 FileNotFoundError，解析错误抛 yaml.YAMLError。

    供需要严格校验根类型的入口（如 eligibility profile）使用。
    load_config 的既有返回语义不变（仍把空文档/非 dict 兜底成 {}）。
    """
    if path is None:
        target = ROOT / "config.yaml"
        if not target.exists():
            raise FileNotFoundError(f"缺少配置文件: {target}")
    else:
        target = Path(path)
    with target.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def db_path(config: dict) -> Path:
    p = Path(config.get("db_path", "data/jobs.db"))
    return p if p.is_absolute() else ROOT / p


def get_eligibility(config: dict) -> dict:
    """解析 config 的 eligibility 段落（委托 jobs.eligibility.resolve_eligibility）。

    只做解析与校验：不读 DB / 网络 / 全局配置，也不把结果写回 config。
    既有过滤尚未消费它（返回里 filtering_mode="legacy"）。
    """
    from .eligibility import resolve_eligibility

    return resolve_eligibility(config)


# --------------------------------------------------------------------------
# filtering 段：仅 legacy
#
# 与 eligibility 段**分开**：eligibility 段只允许 profile / custom，
# 过滤模式放在独立的 filtering 段。缺省（没有 filtering 段）严格为 legacy；
# 一旦出现就必须完整合法，未知 mode / 空对象 / 非法键 / 错误类型一律失败，
# 绝不静默回退到 legacy。
# --------------------------------------------------------------------------

FILTERING_SECTION = "filtering"
FILTERING_KEYS = ("mode",)
FILTERING_MODES = ("legacy",)


class FilteringConfigError(ValueError):
    """filtering 段的配置错误。

    错误信息只给安全的字段路径与允许取值，不回显配置值、路径或 YAML 原文。
    """


def resolve_filtering(config) -> dict:
    """严格解析 filtering 段，返回 {"mode": ...}。

    - config 不是映射 -> 失败（由调用方决定兜底口径，这里不猜）；
    - 没有 filtering 段 -> legacy；
    - 有 filtering 段 -> 必须是恰好含 mode 的对象，mode 必须是 legacy；
      显式 profile_v1 或任何其它值都在这里安全拒绝（连接 DB 之前失败）。
    """
    if not isinstance(config, dict):
        raise FilteringConfigError("config: 必须是对象")
    if FILTERING_SECTION not in config:
        return {"mode": "legacy"}
    section = config[FILTERING_SECTION]
    if not isinstance(section, dict):
        raise FilteringConfigError("filtering: 必须是对象")
    if not section:
        raise FilteringConfigError("filtering: 不能为空对象（缺省请删除该段）")
    for key in section:
        if not isinstance(key, str) or key not in FILTERING_KEYS:
            raise FilteringConfigError("filtering: 含未知字段")
    if "mode" not in section:
        raise FilteringConfigError("filtering: 缺少 mode")
    mode = section["mode"]
    if isinstance(mode, bool) or not isinstance(mode, str):
        raise FilteringConfigError("filtering.mode: 必须是字符串")
    if mode not in FILTERING_MODES:
        raise FilteringConfigError(
            "filtering.mode: 未知 mode（仅支持 legacy）")
    return {"mode": mode}


def get_filtering(config) -> dict:
    """解析 filtering 段；缺省严格为 legacy（见 resolve_filtering）。"""
    return resolve_filtering(config)
