"""eligibility profile 的解析、校验与只读 CLI 回归（离线，无新依赖）。

覆盖：
  - 五个 profile（citizen / permanent_resident / 485 / student_visa / custom）
    的能力表、YAML 引号与裸整数 485、缺配置的 legacy 来源、None 保留；
  - 关键非法 schema / 未知字段 / 缺字段 / 类型与小时值，错误带字段路径；
  - 纯函数：输入不变、返回对象与 preset 跨调用隔离、可 JSON 序列化；
  - config.get_eligibility 委托，load_config / load_rules 行为不变；
  - CLI `eligibility-profile` 只读：严格校验原始 YAML 根类型、成功 stdout
    仅 JSON、不建 DB、错误非零且不回显原值；并用 mock database.connect
    证明成功/错误路径都不走 DB。

用法（不联网、不碰生产 DB）：
    .venv/bin/python tools/test_eligibility_profile.py
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jobs import cli                                        # noqa: E402
from jobs import db as database                             # noqa: E402
from jobs.config import (db_path, get_eligibility, load_config,  # noqa: E402
                         load_rules, read_config_root)
from jobs.eligibility import (CAPABILITY_FIELDS, PRESETS,   # noqa: E402
                              resolve_eligibility)

failures: list = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{('  ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


def expect_error(label: str, config, path_fragment: str) -> None:
    try:
        resolve_eligibility(config)
    except ValueError as exc:
        check(label, path_fragment in str(exc), repr(str(exc)))
        return
    check(label, False, "未抛出 ValueError")


def good_custom(**over) -> dict:
    base = {
        "australian_citizen": False,
        "permanent_resident": False,
        "unrestricted_work_rights": True,
        "requires_sponsorship": False,
        "security_clearance_eligible": False,
        "max_weekly_hours": None,
    }
    base.update(over)
    return base


def caps(result: dict) -> tuple:
    return tuple(result["capabilities"][f] for f in CAPABILITY_FIELDS)


def run_cli(cfg_path: Path):
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "jobs.cli", "--config", str(cfg_path),
         "eligibility-profile"],
        cwd=str(ROOT), env=env, capture_output=True, text=True)


def write_cfg(tmp: Path, name: str, text: str) -> Path:
    p = tmp / name
    p.write_text(text, encoding="utf-8")
    return p


def test_presets_and_legacy() -> None:
    print("== 1. 五个 profile / legacy 默认 / None 保留 ==")
    expected = {
        "citizen": (True, False, True, False, None, None),
        "permanent_resident": (False, True, True, False, None, None),
        "485": (False, False, True, False, False, None),
        "student_visa": (False, False, None, None, False, None),
    }
    for name, tup in expected.items():
        r = resolve_eligibility({"eligibility": {"profile": name}})
        check(f"{name} 能力表", caps(r) == tup, str(caps(r)))
        check(f"{name} config_source=explicit", r["config_source"] == "explicit")
        check(f"{name} filtering_mode=legacy", r["filtering_mode"] == "legacy")

    r = resolve_eligibility({"eligibility": {"profile": "485"}})
    check("引号 485 归一", r["profile"] == "485" and r["config_source"] == "explicit")
    r = resolve_eligibility({"eligibility": {"profile": 485}})
    check("裸整数 485 归一为字符串",
          r["profile"] == "485" and r["capabilities"] == PRESETS["485"], str(r["profile"]))

    r = resolve_eligibility({})
    check("缺 eligibility -> 485 兼容模板",
          r["profile"] == "485" and r["capabilities"] == PRESETS["485"])
    check("legacy config_source", r["config_source"] == "legacy_default")
    check("max_weekly_hours=None 保留", r["capabilities"]["max_weekly_hours"] is None)
    sv = resolve_eligibility({"eligibility": {"profile": "student_visa"}})
    check("student_visa None 保留",
          sv["capabilities"]["unrestricted_work_rights"] is None
          and sv["capabilities"]["requires_sponsorship"] is None)


def test_custom_and_validation() -> None:
    print("== 2. custom 解析与非法配置报错 ==")
    r = resolve_eligibility({"eligibility": {
        "profile": "custom",
        "custom": good_custom(australian_citizen=True, requires_sponsorship=None,
                              security_clearance_eligible=None, max_weekly_hours=20)}})
    check("custom 混合 bool/None + 小时数",
          caps(r) == (True, False, True, None, None, 20), str(caps(r)))
    check("custom config_source=explicit", r["config_source"] == "explicit")
    r = resolve_eligibility({"eligibility": {"profile": "custom", "custom": good_custom(
        australian_citizen=None, permanent_resident=None,
        unrestricted_work_rights=None, requires_sponsorship=None,
        security_clearance_eligible=None)}})
    check("custom 全 None 保留", all(v is None for v in caps(r)), str(caps(r)))

    expect_error("根 list 拒绝", [], "config")
    expect_error("根 None 拒绝", None, "config")
    expect_error("根 str 拒绝", "x", "config")
    expect_error("eligibility=null", {"eligibility": None}, "eligibility")
    expect_error("eligibility=空对象", {"eligibility": {}}, "eligibility")
    expect_error("eligibility=list", {"eligibility": []}, "eligibility")
    expect_error("eligibility=str", {"eligibility": "485"}, "eligibility")
    expect_error("缺 profile", {"eligibility": {"custom": good_custom()}}, "eligibility")
    expect_error("未知字段 extra", {"eligibility": {"profile": "485", "extra": 1}}, "eligibility")
    expect_error("拼写错 prof", {"eligibility": {"prof": "485"}}, "eligibility")
    expect_error("未知 profile", {"eligibility": {"profile": "nope"}}, "eligibility.profile")
    expect_error("大小写不自动猜", {"eligibility": {"profile": "Citizen"}}, "eligibility.profile")
    expect_error("空 profile", {"eligibility": {"profile": ""}}, "eligibility.profile")
    expect_error("非字符串 482", {"eligibility": {"profile": 482}}, "eligibility.profile")
    expect_error("float 485.0", {"eligibility": {"profile": 485.0}}, "eligibility.profile")
    expect_error("bool True profile", {"eligibility": {"profile": True}}, "eligibility.profile")
    expect_error("preset 带 custom",
                 {"eligibility": {"profile": "485", "custom": good_custom()}},
                 "eligibility.custom")
    expect_error("custom 缺 custom", {"eligibility": {"profile": "custom"}}, "eligibility.custom")
    expect_error("custom 空对象", {"eligibility": {"profile": "custom", "custom": {}}},
                 "eligibility.custom")
    missing = good_custom()
    del missing["requires_sponsorship"]
    expect_error("custom 缺字段", {"eligibility": {"profile": "custom", "custom": missing}},
                 "requires_sponsorship")
    extra = good_custom()
    extra["work_rights"] = True
    expect_error("custom 多字段", {"eligibility": {"profile": "custom", "custom": extra}},
                 "eligibility.custom")
    expect_error("bool 字符串 true",
                 {"eligibility": {"profile": "custom",
                                  "custom": good_custom(australian_citizen="true")}},
                 "australian_citizen")
    expect_error("bool 数字 1",
                 {"eligibility": {"profile": "custom",
                                  "custom": good_custom(permanent_resident=1)}},
                 "permanent_resident")
    expect_error("bool 数字 0",
                 {"eligibility": {"profile": "custom",
                                  "custom": good_custom(unrestricted_work_rights=0)}},
                 "unrestricted_work_rights")
    for bad in (0, -1, True, "20", float("nan"), float("inf"), float("-inf")):
        expect_error(f"max_weekly_hours 非法 {bad!r}",
                     {"eligibility": {"profile": "custom",
                                      "custom": good_custom(max_weekly_hours=bad)}},
                     "max_weekly_hours")
    for good in (1, 20, 37.5):
        r = resolve_eligibility({"eligibility": {
            "profile": "custom", "custom": good_custom(max_weekly_hours=good)}})
        check(f"max_weekly_hours 合法 {good!r}",
              r["capabilities"]["max_weekly_hours"] == good)


def test_purity_and_isolation() -> None:
    print("== 3. 纯函数：输入不变 / 返回隔离 / 可序列化 ==")
    cfg = {"eligibility": {"profile": "485"}, "other": ["keep"]}
    before = copy.deepcopy(cfg)
    r1 = resolve_eligibility(cfg)
    check("输入对象不被修改", cfg == before, str(cfg))
    r1["capabilities"]["australian_citizen"] = True
    r1["profile"] = "tampered"
    r2 = resolve_eligibility(cfg)
    check("修改返回值不污染 preset/下次结果",
          r2["capabilities"]["australian_citizen"] is False and r2["profile"] == "485",
          str(r2["profile"]))
    r3 = resolve_eligibility(cfg)
    check("每次返回独立对象",
          r2 is not r3 and r2["capabilities"] is not r3["capabilities"])

    cfgc = {"eligibility": {"profile": "custom",
                            "custom": good_custom(max_weekly_hours=20)}}
    beforec = copy.deepcopy(cfgc)
    resolve_eligibility(cfgc)
    check("custom 输入不被修改", cfgc == beforec)

    try:
        json.dumps(r2)
        serializable = True
    except TypeError:
        serializable = False
    check("返回可 JSON 序列化", serializable)


def test_config_module() -> None:
    print("== 4. config 模块：不插入字段 / 兼容既有读取 ==")
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        # Explicit hand-written legacy config: no eligibility section. This
        # replaces the old default load_config() read of the repository
        # config.yaml, so the test no longer depends on any private file.
        legacy = tmp / "legacy.yaml"
        legacy.write_text("db_path: data/jobs.db\n", encoding="utf-8")
        base = load_config(legacy)
        check("显式 legacy 配置不含 eligibility", "eligibility" not in base)
        check("get_eligibility(显式 legacy 配置) 为 legacy",
              get_eligibility(base)["config_source"] == "legacy_default")

        rules = load_rules()
        check("load_rules 原样（含 version/exclude）", "version" in rules and "exclude" in rules)
        check("load_rules 不受 get_eligibility 影响", load_rules()["version"] == rules["version"])

        p = tmp / "cfg.yaml"
        p.write_text("eligibility:\n  profile: citizen\ndb_path: nowhere.db\n",
                     encoding="utf-8")
        cfg = load_config(p)
        check("自定义路径读取兼容", cfg["eligibility"]["profile"] == "citizen")
        check("自定义路径解析", get_eligibility(cfg)["profile"] == "citizen")
        check("load_config 不注入额外字段", set(cfg) == {"eligibility", "db_path"},
              str(sorted(cfg)))

    # read_config_root 保留原始根值；load_config 的 or {} 兜底不变
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        check("read_config_root 保留 list 根",
              read_config_root(write_cfg(tmp, "raw_list.yaml", "[]\n")) == [])
        check("read_config_root 空文档返回 None",
              read_config_root(write_cfg(tmp, "raw_empty.yaml", "")) is None)
        check("read_config_root dict 原样",
              read_config_root(write_cfg(tmp, "raw_dict.yaml",
                                         "eligibility:\n  profile: citizen\n"))[
                  "eligibility"]["profile"] == "citizen")
        check("load_config 仍把 list 根兜底为 {}",
              load_config(write_cfg(tmp, "list.yaml", "[]\n")) == {})
        check("db_path 默认不变", db_path({}).name == "jobs.db")
        check("db_path 绝对路径不变",
              db_path({"db_path": "/tmp/abs.db"}) == Path("/tmp/abs.db"))


def test_cli_readonly() -> None:
    print("== 5. CLI 只读：成功 JSON / 不建 DB / 错误非零 ==")
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        db = tmp / "should_not_exist.db"
        cfg = tmp / "cfg.yaml"
        cfg.write_text("db_path: " + str(db) + "\neligibility:\n  profile: \"485\"\n",
                       encoding="utf-8")
        proc = run_cli(cfg)
        check("成功 exit 0", proc.returncode == 0, f"rc={proc.returncode}")
        try:
            parsed = json.loads(proc.stdout)
        except ValueError:
            parsed = None
        check("成功 stdout 是 JSON", isinstance(parsed, dict), proc.stdout[:80])
        check("JSON profile=485", bool(parsed) and parsed.get("profile") == "485")
        check("JSON filtering_mode=legacy",
              bool(parsed) and parsed.get("filtering_mode") == "legacy")
        check("成功不创建 DB", not db.exists(), str(db))

        legacy = tmp / "legacy.yaml"
        legacy.write_text("db_path: " + str(db) + "\n", encoding="utf-8")
        proc_legacy = run_cli(legacy)
        p_legacy = json.loads(proc_legacy.stdout)
        check("CLI 缺 eligibility -> legacy_default",
              p_legacy["config_source"] == "legacy_default")

        bad = tmp / "bad.yaml"
        bad.write_text("db_path: " + str(db) +
                       "\nsecret_token: DO_NOT_LEAK_123\n"
                       "eligibility:\n  profile: nonsense\n", encoding="utf-8")
        proc2 = run_cli(bad)
        check("错误 exit 非零", proc2.returncode != 0, f"rc={proc2.returncode}")
        check("错误不输出成功 JSON", "capabilities" not in proc2.stdout)
        check("错误有 stderr", bool(proc2.stderr.strip()))
        check("错误不泄漏配置/秘密值",
              "DO_NOT_LEAK_123" not in (proc2.stdout + proc2.stderr),
              (proc2.stdout + proc2.stderr)[:120])
        check("错误不创建 DB", not db.exists(), str(db))


def test_cli_does_not_connect() -> None:
    print("== 6. CLI 只读：mock database.connect 立即失败 ==")
    with tempfile.TemporaryDirectory() as d:
        good = write_cfg(Path(d), "good.yaml", "eligibility:\n  profile: 485\n")
        bad = write_cfg(Path(d), "bad.yaml", "eligibility:\n  profile: nonsense\n")
        original = database.connect

        def boom(*_a, **_k):
            raise AssertionError("database.connect 不应被只读命令调用")

        database.connect = boom
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cli.cmd_eligibility_profile(argparse.Namespace(config=str(good)))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc_bad = cli.cmd_eligibility_profile(argparse.Namespace(config=str(bad)))
        except AssertionError:
            rc = rc_bad = -1
        finally:
            database.connect = original
        check("mock connect 成功路径不走 DB", rc == 0, f"rc={rc}")
        check("mock 场景仍输出 JSON", '"filtering_mode": "legacy"' in buf.getvalue())
        check("mock connect 错误路径不走 DB 且非零", rc_bad == 1, f"rc={rc_bad}")


def test_selfcheck() -> None:
    print("== 7. 框架自检：故意错误 expected 必须被检出 ==")
    r = resolve_eligibility({"eligibility": {"profile": "citizen"}})
    deliberately_wrong = (r["capabilities"]["australian_citizen"] is False)
    check("故意写反的 expected 会被判失败", deliberately_wrong is False,
          str(r["capabilities"]["australian_citizen"]))


def test_root_type_strict() -> None:
    print("== 8. CLI 严格根类型：非 dict 拒绝 / 合法 dict legacy ==")
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        bad_roots = [
            ("list", "[]\n"),
            ("false", "false\n"),
            ("zero", "0\n"),
            ("empty string", "\"\"\n"),
            ("null", "null\n"),
            ("scalar", "x\n"),
            ("non-empty list", "[1, 2]\n"),
            ("empty document", ""),
        ]
        for label, text in bad_roots:
            proc = run_cli(write_cfg(tmp, "root.yaml", text))
            check(f"根 {label} -> 非零", proc.returncode != 0, f"rc={proc.returncode}")
            check(f"根 {label} -> 无成功 JSON", "capabilities" not in proc.stdout)
            check(f"根 {label} -> 无 traceback", "Traceback" not in proc.stderr)

        p_empty = run_cli(write_cfg(tmp, "empty.yaml", "{}\n"))
        pj_empty = json.loads(p_empty.stdout)
        check("根 {} -> legacy_default",
              p_empty.returncode == 0 and pj_empty["config_source"] == "legacy_default")
        p_dict = run_cli(write_cfg(tmp, "dict.yaml", "db_path: never.db\n"))
        pj_dict = json.loads(p_dict.stdout)
        check("dict 缺 eligibility -> legacy_default",
              p_dict.returncode == 0 and pj_dict["config_source"] == "legacy_default")


def test_cli_error_safety() -> None:
    print("== 9. CLI 错误安全：缺失 / 损坏 YAML / 解码 ==")
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        proc = run_cli(tmp / "missing.yaml")
        check("缺失配置 -> 非零", proc.returncode != 0)
        check("缺失配置 -> 无 traceback", "Traceback" not in proc.stderr)
        check("缺失配置 -> 无成功 JSON", "capabilities" not in proc.stdout)
        check("缺失配置 -> 安全 stderr", "不存在" in proc.stderr, proc.stderr.strip()[:60])

        proc = run_cli(write_cfg(tmp, "corrupt.yaml", "eligibility: [\n  profile: 485\n"))
        check("损坏 YAML -> 非零", proc.returncode != 0)
        check("损坏 YAML -> 无 traceback", "Traceback" not in proc.stderr)
        check("损坏 YAML -> 无成功 JSON", "capabilities" not in proc.stdout)
        check("损坏 YAML -> 安全 stderr（含行列号）",
              "有效 YAML" in proc.stderr and "行" in proc.stderr, proc.stderr.strip()[:80])

        bad = tmp / "bad_utf8.bin"
        bad.write_bytes(b"\xff\xfe\x00bad")
        proc = run_cli(bad)
        check("非 UTF-8 -> 非零", proc.returncode != 0)
        check("非 UTF-8 -> 无 traceback", "Traceback" not in proc.stderr)
        check("非 UTF-8 -> 安全 stderr", "UTF-8" in proc.stderr, proc.stderr.strip()[:60])

        proc = run_cli(tmp)
        check("目录路径 -> 非零", proc.returncode != 0)
        check("目录路径 -> 无 traceback", "Traceback" not in proc.stderr)


def test_sentinel_not_echoed() -> None:
    print("== 10. 合成哨兵不回显（profile / bool / hours / YAML）==")
    sent = "SYNTHETIC_PRIVATE_SENTINEL"
    native = [
        ("非法 profile", {"eligibility": {"profile": sent}}),
        ("非法 bool", {"eligibility": {"profile": "custom",
                                       "custom": good_custom(australian_citizen=sent)}}),
        ("非法 hours", {"eligibility": {"profile": "custom",
                                        "custom": good_custom(max_weekly_hours=sent)}}),
    ]
    for label, cfg in native:
        try:
            resolve_eligibility(cfg)
            raised, msg = False, ""
        except ValueError as exc:
            raised, msg = True, str(exc)
        check(f"{label} 报错且不回显哨兵", raised and sent not in msg, repr(msg[:70]))

    with tempfile.TemporaryDirectory() as d:
        proc = run_cli(write_cfg(Path(d), "sentinel.yaml",
                                 "eligibility: [" + sent + "\n"))
        combined = proc.stdout + proc.stderr
        check("损坏 YAML 不回显哨兵", sent not in combined, combined[:80])
        check("损坏 YAML 无 traceback", "Traceback" not in proc.stderr)


def test_mixed_keys() -> None:
    print("== 11. 未知键混合类型：稳定 ValueError，不 TypeError ==")
    custom_mixed = dict(good_custom())
    custom_mixed[12] = 3
    cases = [
        ("eligibility 混合键", {"eligibility": {"profile": "citizen", "bad": 1, 12: 2}},
         "eligibility"),
        ("custom 混合键", {"eligibility": {"profile": "custom", "custom": custom_mixed}},
         "eligibility.custom"),
    ]
    for label, cfg, frag in cases:
        try:
            resolve_eligibility(cfg)
            outcome = "no-error"
        except TypeError as exc:
            outcome = f"TypeError: {exc}"
        except ValueError as exc:
            outcome = "ok" if frag in str(exc) else f"path? {exc}"
        check(f"{label} ValueError 带路径", outcome == "ok", str(outcome))

    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        proc = run_cli(write_cfg(tmp, "mixed.yaml",
                                 "eligibility:\n  profile: citizen\n  12: 2\n"))
        check("YAML eligibility 混合键 -> 非零", proc.returncode != 0, f"rc={proc.returncode}")
        check("YAML eligibility 混合键 -> 无 traceback", "Traceback" not in proc.stderr)
        check("YAML eligibility 混合键 -> 无成功 JSON", "capabilities" not in proc.stdout)

        proc2 = run_cli(write_cfg(
            tmp, "mixed_custom.yaml",
            "eligibility:\n  profile: custom\n  custom:\n"
            "    australian_citizen: false\n    permanent_resident: false\n"
            "    unrestricted_work_rights: true\n    requires_sponsorship: false\n"
            "    security_clearance_eligible: false\n    max_weekly_hours: null\n"
            "    12: 3\n"))
        check("YAML custom 混合键 -> 非零", proc2.returncode != 0, f"rc={proc2.returncode}")
        check("YAML custom 混合键 -> 无 traceback", "Traceback" not in proc2.stderr)


def main() -> int:
    test_presets_and_legacy()
    test_custom_and_validation()
    test_purity_and_isolation()
    test_config_module()
    test_cli_readonly()
    test_cli_does_not_connect()
    test_root_type_strict()
    test_cli_error_safety()
    test_sentinel_not_echoed()
    test_mixed_keys()
    test_selfcheck()
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
