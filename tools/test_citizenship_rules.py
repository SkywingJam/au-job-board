"""合成资格回归测试（不联网）。

背景：初版 citizenship 规则是照着语料里见过的原句写的，结果
"an Australian / New Zealand Citizen or Australian Permanent Resident"
这类变体没被抓住，让不能申请的岗位排到第 1 名。

这个脚本默认**只读合成 fixture**：
    tools/fixtures/synthetic_citizenship.json

里面的每一条都是手写的最小合成句，覆盖 citizen-only、citizen-or-PR、
独立 PR、有效工作签证的开放析取、EEO 噪音、police clearance 与
security clearance 的区分与并列，以及 ANZ / 标点 / 缩写 / 复数 /
residency 等语法变体。**不含真实公司、平台 ID、URL、时间线、标注原因
或个人记录** —— 这些属于私人数据，不进随 Git 分发的测试。

真实标注导出默认写到 out/fixtures/labeled_citizenship.json（out/
已被 gitignore，不随仓库分发）。要用它做一次额外回归，必须**显式**传
--private-fixtures PATH；默认运行不会探测、不会自动加载 out/、旧
fixture、DB、私人 corpus 或任何标签导出。

fixture 缺失、JSON 损坏、为空、schema 无效或 expected 取值非法时，
脚本会明确报错并返回非零，不会静默跳过再报「全部通过」。

用法：
    .venv/bin/python tools/test_citizenship_rules.py
    .venv/bin/python tools/test_citizenship_rules.py --private-fixtures out/fixtures/labeled_citizenship.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jobs.config import load_rules          # noqa: E402
from jobs.filters import evaluate, normalize_text  # noqa: E402

# 默认（公共）测试数据：纯合成，随 Git 分发。
SYNTHETIC_FIXTURE = ROOT / "tools" / "fixtures" / "synthetic_citizenship.json"

# 合成 fixture 的字段白名单。刻意排除 uid/title/company/reason/labeled_at：
# 这些字段一出现，就说明真实标注记录被搬进了公共测试数据。
SYNTHETIC_FIELDS = {"id", "expected", "text", "category"}

# 私人导出由 jobs.labels.export_fixtures() 生成，字段更多；这里只要求
# 兼容的 expected/text，其余字段原样接受，不因新增字段而报错。
PRIVATE_REQUIRED_FIELDS = {"expected", "text"}

VALID_EXPECTED = ("exclude", "keep")


class FixtureError(ValueError):
    """fixture 缺失 / JSON 损坏 / 为空 / schema 无效 / expected 非法。"""


def _is_nonempty_str(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def load_fixture(path: Path, *, synthetic: bool) -> list:
    """加载并**严格校验**一个 fixture 文件。

    synthetic=True  —— 默认公共数据：字段只能来自 SYNTHETIC_FIELDS，
                       必须带唯一 id，expected 只能是 exclude/keep。
    synthetic=False —— 显式私人导出：只要求 expected/text，兼容导出的
                       完整 schema。

    任何缺失、损坏或不合规都抛 FixtureError，绝不放行成「空测试」。
    """
    if not path.exists():
        raise FixtureError(f"fixture 不存在：{path}")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FixtureError(f"fixture 无法读取：{path}: {exc}") from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise FixtureError(f"fixture 不是有效 JSON：{path}: {exc}") from exc
    if not isinstance(data, list):
        raise FixtureError(f"fixture 必须是 JSON 数组：{path}")
    if not data:
        raise FixtureError(f"fixture 为空，没有可运行的案例：{path}")

    seen = set()
    cases = []
    for index, item in enumerate(data):
        where = f"{path} 第 {index + 1} 项"
        if not isinstance(item, dict):
            raise FixtureError(f"{where} 不是对象")
        keys = set(item)
        if synthetic:
            unknown = keys - SYNTHETIC_FIELDS
            if unknown:
                raise FixtureError(
                    f"{where} 含有不允许的字段 {sorted(unknown)}；"
                    f"合成 fixture 只允许 {sorted(SYNTHETIC_FIELDS)}")
            if not _is_nonempty_str(item.get("id")):
                raise FixtureError(f"{where} 缺少非空字符串 id")
            if item["id"] in seen:
                raise FixtureError(f"{where} 的 id 重复：{item['id']}")
            seen.add(item["id"])
            if "category" in item and not _is_nonempty_str(item["category"]):
                raise FixtureError(f"{where} 的 category 必须是非空字符串")
        else:
            missing = PRIVATE_REQUIRED_FIELDS - keys
            if missing:
                raise FixtureError(
                    f"{where} 缺少字段 {sorted(missing)}；"
                    "私人导出至少要有 expected/text")
            ident = item.get("id") or item.get("uid")
            if ident is not None:
                if not _is_nonempty_str(ident):
                    raise FixtureError(f"{where} 的 id/uid 必须是非空字符串")
                if ident in seen:
                    raise FixtureError(f"{where} 的 id/uid 重复：{ident}")
                seen.add(ident)

        if item.get("expected") not in VALID_EXPECTED:
            raise FixtureError(
                f"{where} 的 expected 必须是 {VALID_EXPECTED} 之一，"
                f"收到 {item.get('expected')!r}")
        if not _is_nonempty_str(item.get("text")):
            raise FixtureError(f"{where} 缺少非空字符串 text")
        cases.append(item)
    return cases


def run_cases(cases, rules, title):
    """对一组案例做双向断言，返回失败描述列表。"""
    failures = []
    print(f"\n== {title}（{len(cases)} 条）==")
    for index, fx in enumerate(cases):
        name = str(fx.get("id") or fx.get("uid") or f"case_{index + 1}")
        category = f"[{fx['category']}] " if fx.get("category") else ""
        decision = evaluate(normalize_text(fx["text"]), rules)
        want_exclude = fx["expected"] == "exclude"
        ok = decision["is_excluded"] == want_exclude
        print(f"  [{'PASS' if ok else 'FAIL'}] {category}{name} "
              f"期望{'排除' if want_exclude else '保留'}")
        if ok:
            if decision["rule_id"]:
                print(f"          -> {decision['rule_id']}")
        else:
            failures.append(
                f"{name}: 期望 {'exclude' if want_exclude else 'keep'}，"
                f"实际 is_excluded={decision['is_excluded']}"
                f"（规则 {decision['rule_id']}）")
            print(f"          实际 is_excluded={decision['is_excluded']}"
                  f"  规则 {decision['rule_id']}")
    return failures


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="资格规则回归测试。默认只读合成 fixture，不联网。")
    parser.add_argument(
        "--private-fixtures", metavar="PATH", default=None,
        help="额外读取一份**显式指定**的私人标注导出（如 "
             "out/fixtures/labeled_citizenship.json）。不传则完全忽略。")
    args = parser.parse_args(argv)

    rules = load_rules()
    print(f"规则版本 {rules.get('version')}")
    try:
        fixture_display = SYNTHETIC_FIXTURE.relative_to(ROOT)
    except ValueError:          # 测试 mock 的临时路径可能在仓库外
        fixture_display = SYNTHETIC_FIXTURE
    print(f"默认合成 fixture：{fixture_display}")

    try:
        synthetic = load_fixture(SYNTHETIC_FIXTURE, synthetic=True)
        private = (load_fixture(Path(args.private_fixtures), synthetic=False)
                   if args.private_fixtures else None)
    except FixtureError as exc:
        print(f"fixture 错误：{exc}", file=sys.stderr)
        return 3

    failures = run_cases(synthetic, rules, "合成回归")
    if private is not None:
        failures += run_cases(private, rules, "私人导出回归（显式传入）")
    else:
        print("\n（未传 --private-fixtures：本次只跑合成数据，"
              "不读取任何私人导出）")

    print()
    if failures:
        print(f"失败 {len(failures)} 项：")
        for item in failures:
            print(f"  - {item}")
        return 2
    total = len(synthetic) + (len(private) if private else 0)
    if private is None:
        print(f"全部通过（{total} 条），全部为合成数据")
    else:
        print(f"全部通过（{total} 条：合成 + 显式私人导出）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
