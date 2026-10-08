"""测试数据边界与导出契约（离线，不依赖 pytest 或新依赖）。

验收三件事：

  默认测试数据   资格回归默认只读合成 fixture；旧的真实标注 fixture
                 已从源码树删除；默认导出落到 ignored 的 out/fixtures/，
                 不会默认覆盖随 Git 分发的合成 fixture。
  导出契约       export_fixtures 在临时目录验证 eligible/ineligible 的
                 导出、unsure / 个人条件 / 无资格句的跳过语义、返回键、
                 排序，以及 note 不导出。
  失败语义       合成 fixture 与显式私人输入的缺失 / 损坏 / 空 / schema
                 无效 / expected 非法都必须非零退出，不能假通过。

全部使用内存或临时 SQLite 与自主合成文本；不写项目真实 out/，不读取
生产 DB、真实 corpus、标签导出或网络。中间用模块属性 mock 与临时文件
制造失败路径，不移动或破坏仓库文件。

运行：
    PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tools/test_fixture_boundary.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jobs import db as database          # noqa: E402
from jobs import labels as labels_mod    # noqa: E402

FIXTURE_DIR = ROOT / "tools" / "fixtures"
SYNTHETIC = FIXTURE_DIR / "synthetic_citizenship.json"
OLD_REAL = FIXTURE_DIR / "labeled_citizenship.json"
CITIZENSHIP_TEST = ROOT / "tools" / "test_citizenship_rules.py"

failures: list = []


def check(label: str, ok: bool, detail: str = "") -> None:
    line = f"  [{'PASS' if ok else 'FAIL'}] {label}"
    if detail:
        line += f"  {detail}"
    print(line)
    if not ok:
        failures.append(label)


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def git_ignored(rel: str) -> bool:
    """rel 相对仓库根。git 可用时问 git，否则退回解析 .gitignore。"""
    git = shutil.which("git")
    if git:
        proc = subprocess.run([git, "-C", str(ROOT), "check-ignore", "-q", rel],
                              capture_output=True)
        if proc.returncode in (0, 1):
            return proc.returncode == 0
    for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines():
        if line.strip() == "out/":
            return rel.startswith("out/")
    return False


def run_script(args: list) -> tuple:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run([sys.executable] + args, cwd=str(ROOT), env=env,
                          capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(database.SCHEMA)
    return conn


def add_job(conn, uid: str, title: str, company: str, description: str) -> None:
    source, source_id = uid.split(":", 1)
    database.upsert_job(conn, {"source": source, "source_id": source_id,
                               "title": title, "company": company,
                               "description": description})
    conn.commit()


def seed_label(conn, uid: str, eligibility: str, reason=None, note=None,
               labeled_at="2026-01-01T00:00:00+00:00") -> None:
    conn.execute(
        "INSERT INTO labels (uid, eligibility, ineligible_reason, note, labeled_at) "
        "VALUES (?,?,?,?,?)", (uid, eligibility, reason, note, labeled_at))
    conn.commit()


def test_default_export_path() -> None:
    print("== 默认导出路径的边界 ==")
    target = labels_mod.FIXTURE_PATH
    expected = ROOT / "out" / "fixtures" / "labeled_citizenship.json"
    check("默认导出路径是 ROOT/out/fixtures/labeled_citizenship.json",
          target == expected, str(target))
    check("默认导出路径不等于 tracked 合成 fixture", target != SYNTHETIC)
    check("默认导出路径不在 tools/ 下", "tools" not in target.parts)
    check("旧真实 fixture 已从源码树删除", not OLD_REAL.exists(), str(OLD_REAL))
    check("新合成 fixture 存在于源码树", SYNTHETIC.exists(), str(SYNTHETIC))

    ignore_rel = expected.relative_to(ROOT).as_posix()
    check(f"默认导出路径被 Git 忽略（{ignore_rel}）",
          git_ignored(ignore_rel) is True)
    synthetic_rel = SYNTHETIC.relative_to(ROOT).as_posix()
    check(f"合成 fixture 未被 Git 忽略（{synthetic_rel}）",
          git_ignored(synthetic_rel) is False)


def test_synthetic_schema() -> None:
    print("== 合成 fixture 的 schema 与结构 ==")
    data = json.loads(SYNTHETIC.read_text(encoding="utf-8"))
    check("是非空 JSON 数组", isinstance(data, list) and len(data) > 0,
          f"{len(data)} 条")
    allowed = {"id", "expected", "text", "category"}
    bad_keys = [i for i, d in enumerate(data) if set(d) - allowed]
    check("字段只在 id/expected/text/category（无 uid/title/company/reason/labeled_at）",
          not bad_keys, str(bad_keys[:3]))

    prefix = "synthetic_citizenship_"
    ids = [d.get("id") for d in data]
    check("id 唯一", len(ids) == len(set(ids)))
    bad_ids = [i for i in ids
               if not (isinstance(i, str) and i.startswith(prefix)
                       and i[len(prefix):].isdigit()
                       and len(i[len(prefix):]) == 3)]
    check("id 为 synthetic_citizenship_NNN 形式", not bad_ids, str(bad_ids[:3]))
    check("expected 只有 exclude/keep",
          all(d.get("expected") in ("exclude", "keep") for d in data))
    check("text 均为非空字符串",
          all(isinstance(d.get("text"), str) and d["text"].strip() for d in data))
    check("category 均为非空字符串",
          all(isinstance(d.get("category"), str) and d["category"].strip()
              for d in data))

    cats = {d["category"] for d in data}
    required = {"citizen_only", "citizen_or_pr", "pr_standalone", "visa_open",
                "eeo_noise", "clearance_and_police", "security_clearance",
                "police_clearance", "diversity", "pr_narrative"}
    check("必覆盖类别齐全", required <= cats, str(sorted(required - cats)))
    check("exclude 与 keep 两个方向都有",
          {d["expected"] for d in data} == {"exclude", "keep"})

    # 结构性的文本扫描只是补充护栏，不是完整隐私验收；真正的人工核对
    # 见 docs/test-data-policy.md（所有句子为手写最小合成句）。
    markers = ("http", "www.", "linkedin:", "seek:", "indeed:")
    offenders = [d["id"] for d in data
                 if any(m in d["text"].lower() for m in markers)]
    check("文本无 URL / 平台 ID 标记（补充扫描）", not offenders,
          str(offenders[:3]))


def test_export_contract() -> None:
    print("== export_fixtures 的导出契约 ==")
    conn = make_conn()
    add_job(conn, "synth:keep", "Synthetic Keep Role", "Synthetic Labs",
            "You must be an Australian Citizen, Permanent Resident or hold a "
            "valid work permit or visa with full working rights.")
    add_job(conn, "synth:exclude", "Synthetic Exclude Role", "Synthetic Labs",
            "Applicants must be an Australian Citizen or Permanent Resident.")
    add_job(conn, "synth:unsure", "Synthetic Unsure Role", "Synthetic Labs",
            "Candidates must have Australian citizenship or permanent residency.")
    add_job(conn, "synth:licence", "Synthetic Licence Role", "Synthetic Labs",
            "A current driver's licence is required for site visits.")
    add_job(conn, "synth:plain", "Synthetic Plain Role", "Synthetic Labs",
            "Join our friendly team and grow your career.")
    seed_label(conn, "synth:keep", "eligible", note="私人备注不该导出；",
               labeled_at="2026-01-01T00:00:00+00:00")
    seed_label(conn, "synth:exclude", "ineligible", reason="合成排除原因",
               labeled_at="2026-01-02T00:00:00+00:00")
    seed_label(conn, "synth:unsure", "unsure",
               labeled_at="2026-01-03T00:00:00+00:00")
    seed_label(conn, "synth:licence", "ineligible", reason="需驾照",
               labeled_at="2026-01-04T00:00:00+00:00")
    seed_label(conn, "synth:plain", "ineligible", reason="合成原因",
               labeled_at="2026-01-05T00:00:00+00:00")

    real_out = labels_mod.FIXTURE_PATH
    existed_before = real_out.exists()

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "labeled_citizenship.json"
        info = labels_mod.export_fixtures(conn, target)

        check("返回键为 written/skipped/no_snippet/unowned/path",
              set(info) == {"written", "skipped", "no_snippet", "unowned", "path"},
              str(sorted(info)))
        check("written == 2（eligible keep + ineligible exclude）",
              info["written"] == 2, str(info["written"]))
        check("skipped == 2（个人条件 + 无资格句）",
              info["skipped"] == 2, str(info["skipped"]))
        check("path 指向临时目录", info["path"] == str(target), info["path"])
        check("写入位于临时目录、不在项目路径下",
              target.exists() and ROOT not in target.resolve().parents,
              str(target))
        check("无资格句岗位进入 no_snippet",
              info["no_snippet"] == ["Synthetic Plain Role"],
              str(info["no_snippet"]))
        check("个人条件岗位进入 unowned（驾照）",
              ("Synthetic Licence Role", "驾照") in info["unowned"],
              str(info["unowned"]))

        exported = json.loads(target.read_text(encoding="utf-8"))
        check("导出条目数为 2", len(exported) == 2, str(len(exported)))
        check("排序按 labeled_at：keep 在前、exclude 在后",
              [d["uid"] for d in exported] == ["synth:keep", "synth:exclude"],
              str([d["uid"] for d in exported]))
        check("expected 为 keep/exclude",
              sorted(d["expected"] for d in exported) == ["exclude", "keep"])
        check("exclude 条目保留 reason",
              exported[1]["reason"] == "合成排除原因", str(exported[1]["reason"]))
        check("每条的 cue 已记录",
              all(d.get("cue") for d in exported),
              str([d.get("cue") for d in exported]))
        check("导出不含 note 键", all("note" not in d for d in exported))
        raw = target.read_text(encoding="utf-8")
        check("导出文件文本不含备注内容", "私人备注不该导出" not in raw)
        check("导出文件文本不含 note 字段名", '"note"' not in raw)
        check("unsure 未导出",
              all(d["uid"] != "synth:unsure" for d in exported))

    check("测试未创建或改动项目真实 out/ 导出文件",
          real_out.exists() == existed_before, str(real_out))


def test_entry_points() -> None:
    print("== 默认入口与显式私人入口 ==")
    rc, out, err = run_script([str(CITIZENSHIP_TEST)])
    check("默认入口退出 0（只读合成 fixture）", rc == 0,
          f"exit={rc} {err.strip()[:120]}")
    check("默认入口不加载私人导出", "私人导出回归" not in out)
    check("默认入口打印合成 fixture 路径",
          "synthetic_citizenship.json" in out)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        priv = tmp_path / "private_export.json"
        priv.write_text(json.dumps([
            {"uid": "synth:p1", "expected": "keep",
             "text": "You may hold a valid work visa with full working rights."},
            {"uid": "synth:p2", "expected": "exclude",
             "text": "You must be an Australian citizen."},
        ], ensure_ascii=False), encoding="utf-8")
        rc, out, err = run_script([str(CITIZENSHIP_TEST),
                                   "--private-fixtures", str(priv)])
        check("显式私人入口成功退出 0", rc == 0, f"exit={rc}")
        check("显式私人入口确实运行了私人用例", "私人导出回归" in out)

        missing = tmp_path / "missing.json"
        rc, _o, _e = run_script([str(CITIZENSHIP_TEST),
                                 "--private-fixtures", str(missing)])
        check("私人 fixture 缺失 -> 非零", rc != 0, f"exit={rc}")

        corrupt = tmp_path / "corrupt.json"
        corrupt.write_text("{ not valid json", encoding="utf-8")
        rc, _o, _e = run_script([str(CITIZENSHIP_TEST),
                                 "--private-fixtures", str(corrupt)])
        check("私人 fixture 损坏 -> 非零", rc != 0, f"exit={rc}")

        empty = tmp_path / "empty.json"
        empty.write_text("[]", encoding="utf-8")
        rc, _o, _e = run_script([str(CITIZENSHIP_TEST),
                                 "--private-fixtures", str(empty)])
        check("私人 fixture 为空 -> 非零", rc != 0, f"exit={rc}")

        bad_schema = tmp_path / "bad_schema.json"
        bad_schema.write_text(json.dumps([{"expected": "keep"}]),
                              encoding="utf-8")
        rc, _o, _e = run_script([str(CITIZENSHIP_TEST),
                                 "--private-fixtures", str(bad_schema)])
        check("私人 fixture schema 无效 -> 非零", rc != 0, f"exit={rc}")

        bad_expected = tmp_path / "bad_expected.json"
        bad_expected.write_text(
            json.dumps([{"expected": "maybe", "text": "synthetic text"}]),
            encoding="utf-8")
        rc, _o, _e = run_script([str(CITIZENSHIP_TEST),
                                 "--private-fixtures", str(bad_expected)])
        check("私人 fixture expected 非法 -> 非零", rc != 0, f"exit={rc}")


def test_fixture_error_paths(citizenship_mod) -> None:
    print("== 合成 fixture 的失败语义（mock 临时文件）==")
    FixtureError = citizenship_mod.FixtureError

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        def expect_error(name, content, synthetic=True):
            path = tmp_path / name
            if content is not None:
                path.write_text(content, encoding="utf-8")
            try:
                citizenship_mod.load_fixture(path, synthetic=synthetic)
            except FixtureError:
                return True
            except Exception:
                return False
            return False

        check("缺失合成 fixture 报错",
              expect_error("missing.json", None))
        check("损坏 JSON 报错",
              expect_error("corrupt.json", "{ not json"))
        check("空数组报错",
              expect_error("empty.json", "[]"))
        check("非数组报错",
              expect_error("object.json", "{}"))
        check("未知字段报错",
              expect_error("extra.json", json.dumps([
                  {"id": "synthetic_citizenship_001", "expected": "keep",
                   "text": "synthetic", "uid": "real:1"}])))
        check("expected 非法报错",
              expect_error("bad_expected.json", json.dumps([
                  {"id": "synthetic_citizenship_001", "expected": "maybe",
                   "text": "synthetic"}])))
        check("text 为空报错",
              expect_error("blank_text.json", json.dumps([
                  {"id": "synthetic_citizenship_001", "expected": "keep",
                   "text": "  "}])))
        check("id 重复报错",
              expect_error("dup_id.json", json.dumps([
                  {"id": "synthetic_citizenship_001", "expected": "keep",
                   "text": "synthetic"},
                  {"id": "synthetic_citizenship_001", "expected": "exclude",
                   "text": "synthetic"}])))

        # 私人 schema 允许额外字段，但同样拒绝缺失 / 非法 expected
        ok_private = tmp_path / "private_ok.json"
        ok_private.write_text(json.dumps([{
            "uid": "real:1", "expected": "keep", "cue": "公民/PR",
            "text": "synthetic private text", "title": "T", "company": "C",
            "reason": None, "labeled_at": "2026-01-01T00:00:00+00:00"}]),
            encoding="utf-8")
        try:
            loaded = citizenship_mod.load_fixture(ok_private, synthetic=False)
            private_ok = len(loaded) == 1
        except Exception:
            private_ok = False
        check("私人入口兼容导出 schema", private_ok)
        check("私人 fixture 缺失 expected 报错",
              expect_error("private_missing.json",
                           json.dumps([{"text": "x"}]), synthetic=False))
        check("私人 fixture expected 非法报错",
              expect_error("private_bad.json",
                           json.dumps([{"expected": "unsure", "text": "x"}]),
                           synthetic=False))

        # 断言失败必须返回 2，而不是假通过
        wrong = tmp_path / "wrong.json"
        wrong.write_text(json.dumps([{
            "id": "synthetic_citizenship_999", "expected": "exclude",
            "text": "This role is open to all candidates with full working rights.",
            "category": "visa_open"}]), encoding="utf-8")
        original = citizenship_mod.SYNTHETIC_FIXTURE
        citizenship_mod.SYNTHETIC_FIXTURE = wrong
        try:
            rc = citizenship_mod.main([])
        finally:
            citizenship_mod.SYNTHETIC_FIXTURE = original
        check("合成案例断言失败返回 2（不假通过）", rc == 2, f"exit={rc}")

        original = citizenship_mod.SYNTHETIC_FIXTURE
        citizenship_mod.SYNTHETIC_FIXTURE = tmp_path / "synthetic_missing.json"
        try:
            rc = citizenship_mod.main([])
        finally:
            citizenship_mod.SYNTHETIC_FIXTURE = original
        check("合成 fixture 缺失时 main 非零", rc == 3, f"exit={rc}")


def main() -> int:
    print(f"ROOT = {ROOT}")
    citizenship_mod = load_module("citizenship_rules_under_test", CITIZENSHIP_TEST)

    test_default_export_path()
    test_synthetic_schema()
    test_export_contract()
    test_entry_points()
    test_fixture_error_paths(citizenship_mod)

    print()
    if failures:
        print(f"失败 {len(failures)} 项：")
        for item in failures:
            print(f"  - {item}")
        return 2
    print("数据边界与导出契约测试全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
