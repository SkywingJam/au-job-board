"""独立合成 demo 的回归：数据、隔离与面板交互。

不联网、不碰生产 config / rules / 词表 / DB / out，只写临时目录。核心不是「路径不同」，
而是**运行时守卫**：用 sys.addaudithook 在 demo 生成、面板读取与写交互期间拦截
  - 打开 / 写入 / 删除 / 建目录：只允许 demo 根（读另允许 demo/ 源文件与 jobs/ 代码），
    仓库里其余文件（config.yaml、rules.yaml、skills*.yaml、data/、out/ 其余部分 …）一律拒绝；
  - sqlite3.connect：只允许 demo 根内的库；
  - 套接字：只允许回环地址的连接 / 绑定 / 解析；
  - 子进程 / fork：一律拒绝（不启动后台服务）。
守卫自己也有反向自测，证明它真会拒绝（否则「没被拦住」什么都证明不了）。

用法：
    .venv/bin/python tools/test_demo.py
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import http.client
import io
import json
import os
import re
import shutil
import socket
import sqlite3
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jobs import demo, panel                       # noqa: E402
from jobs import skills as skills_mod              # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{('  ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


# ---------------------------------------------------------------------------
# 运行时守卫
# ---------------------------------------------------------------------------

class GuardViolation(Exception):
    pass


_ACTIVE: "Guard | None" = None
_INSTALLED = False
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
_LOOPBACK = {"127.0.0.1", "localhost", None, ""}
_NO_PROCESS = {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn",
               "os.fork", "os.forkpty"}


def _runtime_roots() -> list[Path]:
    """Python 运行时自己要读的位置（解释器、标准库、已装依赖、系统库）。
    只放行这些；其余位置（含任何外部临时目录、用户目录）的读都算越界。"""
    import site
    import sysconfig
    raw = {sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix,
           *sysconfig.get_paths().values(), *site.getsitepackages(),
           "/usr", "/System", "/Library", "/etc", "/private/etc", "/dev", "/opt/homebrew",
           "/private/var/db"}
    return [Path(os.path.realpath(r)) for r in raw if r]


def _real(path) -> Path | None:
    try:
        return Path(os.path.realpath(os.fsdecode(path)))
    except (TypeError, ValueError):
        return None


def _under(path: Path, roots) -> bool:
    return any(path == r or r in path.parents for r in roots)


class Guard:
    """生效期间，任何越界的文件 / 网络 / 进程访问都会被记录并拒绝。"""

    def __init__(self, demo_roots):
        self.demo_roots = [Path(os.path.realpath(r)) for r in demo_roots]
        self.read_roots = self.demo_roots + [Path(os.path.realpath(ROOT / "demo")),
                                             Path(os.path.realpath(ROOT / "jobs")),
                                             Path(os.path.realpath(ROOT / "tools"))]   # 回溯栈要读测试源码
        self.repo = Path(os.path.realpath(ROOT))
        self.runtime_roots = _runtime_roots()
        self.violations: list[str] = []
        self.paused = False

    def deny(self, what: str):
        self.violations.append(what)
        raise GuardViolation(what)

    def __enter__(self):
        global _ACTIVE, _INSTALLED
        if not _INSTALLED:
            sys.addaudithook(_hook)
            _INSTALLED = True
        _ACTIVE = self
        return self

    def __exit__(self, *exc):
        global _ACTIVE
        _ACTIVE = None
        return False

    @contextlib.contextmanager
    def pause(self):
        """测试代码自己读生产文件做对照时用；被测代码不会走这里。"""
        global _ACTIVE
        previous, _ACTIVE = _ACTIVE, None
        try:
            yield
        finally:
            _ACTIVE = previous              # 恢复进入前的状态，而不是无条件重新启用

    # -- 检查 --
    def check_path(self, event: str, path, write: bool):
        p = _real(path)
        if p is None or str(p) in ("/dev/null", "/dev/urandom", "/dev/random"):
            return
        if write:
            if _under(p, self.demo_roots) or "__pycache__" in p.parts:
                return
            self.deny(f"{event} write outside demo root: {p}")
        if _under(p, self.runtime_roots) or _under(p, self.read_roots):
            return
        self.deny(f"{event} read outside demo root / demo sources / python runtime: {p}")

    def check_host(self, event: str, host):
        if isinstance(host, bytes):
            host = host.decode("ascii", "replace")
        if host not in _LOOPBACK:
            self.deny(f"{event} non-loopback host {host!r}")


def _hook(event: str, args):
    g = _ACTIVE
    if g is None:
        return
    if event == "open":
        path, mode, flags = args
        if isinstance(path, int):
            return
        if isinstance(flags, int) and flags & _WRITE_FLAGS:
            write = True
        else:
            write = any(c in (mode or "") for c in "wax+")
        g.check_path("open", path, write)
    elif event in ("os.mkdir", "os.remove", "os.rmdir", "os.truncate", "os.chmod",
                   "os.utime", "shutil.rmtree"):
        g.check_path(event, args[0], True)
    elif event in ("os.rename", "os.link", "os.symlink", "shutil.copyfile", "shutil.move"):
        g.check_path(event, args[0], event in ("os.rename", "shutil.move"))
        g.check_path(event, args[1], True)
    elif event == "sqlite3.connect":
        if str(args[0]) != ":memory:":
            g.check_path("sqlite3.connect", args[0], True)
    elif event in ("socket.connect", "socket.bind"):
        address = args[1]
        if isinstance(address, tuple):
            g.check_host(event, address[0])
        else:
            g.deny(f"{event} non-inet address {address!r}")
    elif event == "socket.getaddrinfo":
        g.check_host(event, args[0])
    elif event in ("socket.gethostbyname", "socket.gethostbyaddr"):
        g.check_host(event, args[0])
    elif event in _NO_PROCESS or event.startswith("os.exec"):
        g.deny(f"{event} (process creation is not allowed)")


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

PROTECTED = ["config.yaml", "rules.yaml", "skills.yaml", "skills.local.yaml",
             "skills.local.example.yaml", "README.md"]


SENTINEL = "EXTERNAL-SYNTHETIC-SENTINEL-DO-NOT-LEAK"


def snapshot_dir(path: Path) -> dict:
    """目录里每个文件的 (内容哈希, mtime, 大小, 链接数)。"""
    out = {}
    for p in sorted(path.iterdir()):
        st = p.lstat()
        out[p.name] = (digest(p) if p.is_file() and not p.is_symlink() else None,
                       st.st_mtime_ns, st.st_size, st.st_nlink)
    return out


def digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def repo_snapshot() -> dict:
    """仓库里所有文件的 (大小, mtime)。.venv / .git / __pycache__ 不计。"""
    snap = {}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in (".venv", ".git", "__pycache__")]
        for name in filenames:
            p = Path(dirpath) / name
            if name == ".DS_Store":
                continue
            st = p.stat()
            snap[str(p.relative_to(ROOT))] = (st.st_size, st.st_mtime_ns)
    return snap


def http_get(port: int, path: str, headers: dict | None = None) -> tuple[int, str]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("GET", path, headers=headers or {})
        resp = conn.getresponse()
        return resp.status, resp.read().decode("utf-8")
    finally:
        conn.close()


def http_post(port: int, path: str, payload: dict) -> tuple[int, str]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        body = json.dumps(payload).encode("utf-8")
        conn.request("POST", path, body=body, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        return resp.status, resp.read().decode("utf-8")
    finally:
        conn.close()


def cards(html: str) -> int:
    return len(re.findall(r'<div class="card[ "]', html))


def db_rows(root: Path, sql: str, *args) -> list:
    conn = sqlite3.connect(root / "jobs.db")
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


EXPECTED_UIDS = (
    [f"seek:DEMO-{n:03d}" for n in (1, 4, 6, 9, 10, 12, 14, 15, 16, 18)]
    + [f"linkedin:DEMO-{n:03d}" for n in (2, 5, 8, 13, 19)]
    + [f"indeed:DEMO-{n:03d}" for n in (3, 7, 11, 17)])


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------

def test_sources() -> None:
    print("== 合成输入文件（只读检查，不生成）==")
    fixture = json.loads((ROOT / "demo/demo.jobs.json").read_text(encoding="utf-8"))
    jobs = fixture["jobs"]
    check("岗位数在 10～20", 10 <= len(jobs) <= 20, f"{len(jobs)} 条")
    check("source_id 全是 DEMO-nnn 固定标识", all(re.fullmatch(r"DEMO-\d{3}", j["source_id"]) for j in jobs))
    check("uid 与期望清单一致（稳定 id）",
          sorted(f"{j['source']}:{j['source_id']}" for j in jobs) == sorted(EXPECTED_UIDS))
    blob = json.dumps(fixture, ensure_ascii=False)
    check("fixture 里没有任何 URL / 邮箱（链接由生成器统一写成 example.invalid）",
          not re.search(r"https?://|www\.|@\w+\.\w+", blob))
    check("每条标注都指向存在的岗位", all(lb["uid"] in EXPECTED_UIDS for lb in fixture["labels"]))
    check("demo_id 固定", fixture["demo_id"] == demo.DEMO_ID)
    check("来源只用通用枚举", {j["source"] for j in jobs} == {"seek", "linkedin", "indeed"})
    cfg = (ROOT / "demo/demo.config.yaml").read_text(encoding="utf-8")
    check("demo 配置没有抓取段（无 seek / jobspy / queries）",
          not re.search(r"^(seek|jobspy|queries):", cfg, re.M))
    check("demo 配置默认回环 + 8091（不是生产 8090）",
          '"127.0.0.1"' in cfg and "port: 8091" in cfg)


def test_generate(base: Path, guard_factory) -> Path:
    print("== 生成：数量 / 稳定 id / 去重 / 标注（守卫生效）==")
    root = base / "demo-a"
    today = dt.date.today()
    with guard_factory([root]) as g:
        summary = demo.generate(root, today)
    check("生成期间零守卫违规", g.violations == [], str(g.violations[:3]))
    check("返回摘要数量", (summary["jobs"], summary["groups"], summary["excluded"],
                           summary["labelled"]) == (19, 16, 2, 6), str(summary))
    check("demo 根内文件完整",
          sorted(p.name for p in root.iterdir() if not p.name.startswith("jobs.db-")) ==   # SQLite WAL 旁路文件也在根内
          sorted(["config.yaml", "demo-manifest.json", "jobs.db", "reports", "rules.yaml", "skills.yaml"]))

    rows = db_rows(root, "SELECT uid, source, url, dedupe_group FROM jobs")
    check("uid 稳定且与 fixture 一致", sorted(r["uid"] for r in rows) == sorted(EXPECTED_UIDS))
    check("所有链接都是 example.invalid", all(r["url"].startswith("https://example.invalid/demo/") for r in rows))
    groups: dict[str, list[str]] = {}
    for r in rows:
        groups.setdefault(r["dedupe_group"], []).append(r["uid"])
    sizes = sorted(len(v) for v in groups.values())
    check("重复案例：一组 3 条（跨三平台、公司写法不同）+ 一组 2 条（标题带级别后缀）",
          sizes.count(3) == 1 and sizes.count(2) == 1 and len(groups) == 16, str(sizes))
    triple = next(v for v in groups.values() if len(v) == 3)
    check("三平台重复组就是 Quillfeather 那三条",
          sorted(triple) == ["indeed:DEMO-003", "linkedin:DEMO-002", "seek:DEMO-001"])
    pair = next(v for v in groups.values() if len(v) == 2)
    check("模糊标题重复组是 Kestrel 两条", sorted(pair) == ["linkedin:DEMO-013", "seek:DEMO-014"])

    dec = {r["uid"]: r for r in db_rows(root, "SELECT * FROM decisions")}
    excluded = sorted(u for u, r in dec.items() if r["is_excluded"])
    check("被规则排除的恰为 citizenship-only 与 NV1 两条",
          excluded == ["linkedin:DEMO-005", "seek:DEMO-006"], str(excluded))
    check("visa 开放写法被 override 救回（不被误排除）",
          all(dec[u]["rule_kind"] == "override" and not dec[u]["is_excluded"]
              for u in ("seek:DEMO-004", "seek:DEMO-015")))

    labels = {r["uid"]: r for r in db_rows(root, "SELECT * FROM labels")}
    actions = sorted(r["action"] for r in labels.values())
    check("已标注 6 条，覆盖 saved / applied / skipped 三种进度",
          len(labels) == 6 and set(actions) == {"saved", "applied", "skipped"}, str(actions))
    check("存在未标注岗位", len(rows) - len(labels) > 0)
    check("默认标注文本是英文（备注 / 不可投原因里没有汉字）",
          not re.search(r"[\u4e00-\u9fff]", " ".join(f"{r['note'] or ''} {r['ineligible_reason'] or ''}"
                                                   for r in labels.values())), str([r["note"] for r in labels.values()]))
    check("英文备注已规范化为「标签；」形态", labels["seek:DEMO-001"]["note"] == "Stack match；Apply first；")
    check("清单记录 lang=en", json.loads((root / demo.MANIFEST).read_text(encoding="utf-8"))["lang"] == "en")
    check("已投递带时间戳、跳过没有",
          all(r["applied_at"] for r in labels.values() if r["action"] == "applied")
          and not any(r["applied_at"] for r in labels.values() if r["action"] != "applied"))

    sal = {r["uid"]: r for r in db_rows(root, "SELECT * FROM salary")}
    jobs = {r["uid"]: r for r in db_rows(root, "SELECT * FROM jobs")}
    check("薪资：平台字段（年薪 / 时薪 / 日薪折算）",
          sal["seek:DEMO-001"]["origin"] == "field" and sal["indeed:DEMO-007"]["period"] == "hour"
          and sal["indeed:DEMO-011"]["period"] == "day")
    check("薪资：字段为空、从正文挖出", sal["seek:DEMO-012"]["origin"] == "description")
    check("薪资：缺失值与无法解析的原文都没有解析结果",
          "seek:DEMO-009" not in sal and "seek:DEMO-010" not in sal
          and jobs["seek:DEMO-010"]["salary"] == "Competitive package")
    check("缺描述的岗位也入库（待补详情样例）",
          jobs["linkedin:DEMO-019"]["description"] is None)

    tiers = {r["uid"]: r["tier"] for r in db_rows(root, "SELECT * FROM scores")}
    check("方向分层覆盖 T1 / T2 / T3", {"T1", "T2", "T3"} <= set(tiers.values()))

    gen_cfg = demo.load_demo(root)
    check("生成的配置全是 demo 根内绝对路径",
          all(str(root.resolve()) in str(v) for v in
              (gen_cfg["db_path"], gen_cfg["skills_path"], gen_cfg["report"]["out_dir"])))
    check("生成的配置带 demo 标识、无抓取段",
          gen_cfg["demo"]["id"] == demo.DEMO_ID and "seek" not in gen_cfg and "jobspy" not in gen_cfg)
    return root


def test_deterministic(base: Path, guard_factory) -> None:
    print("== 稳定性：同一天重复生成得到同样的逻辑数据 ==")
    fixed = dt.date(2030, 1, 15)
    snaps = []
    for name in ("det-1", "det-2"):
        root = base / name
        with guard_factory([root]):
            demo.generate(root, fixed)
        snaps.append((
            db_rows(root, "SELECT uid, title, company, location, listing_date, dedupe_group FROM jobs ORDER BY uid"),
            db_rows(root, "SELECT uid, tier, total_score FROM scores ORDER BY uid"),
            db_rows(root, "SELECT uid, eligibility, interest, action, note FROM labels ORDER BY uid"),
            db_rows(root, "SELECT uid, is_excluded, rule_id FROM decisions ORDER BY uid")))
    check("两次生成的 jobs / scores / labels / decisions 完全一致", snaps[0] == snaps[1])
    dates = {r["uid"]: r["listing_date"] for r in snaps[0][0]}
    check("相对日期按 --today 锚定", dates["seek:DEMO-001"] == "2030-01-12"
          and dates["linkedin:DEMO-019"].startswith("2030-01-14"))


def test_lang_flag(base: Path, guard_factory) -> None:
    print("== --lang zh：中文版标注；其余数据不变 ==")
    en, zh = base / "lang-en", base / "lang-zh"
    fixed = dt.date(2030, 1, 15)
    with guard_factory([en, zh]) as g:
        demo.generate(en, fixed)
        demo.generate(zh, fixed, lang="zh")
        try:
            demo.generate(base / "lang-bad", fixed, lang="fr")
            bad = False
        except demo.DemoError:
            bad = True
    check("不支持的 lang 被拒绝且不建目录", bad and not (base / "lang-bad").exists())
    check("lang 生成零守卫违规", g.violations == [], str(g.violations))
    zl = {r["uid"]: r for r in db_rows(zh, "SELECT * FROM labels")}
    check("中文版备注与原因是中文", zl["seek:DEMO-001"]["note"] == "技术栈匹配；优先投递；"
          and "公民" in zl["linkedin:DEMO-005"]["ineligible_reason"])
    check("中文版清单 lang=zh", json.loads((zh / demo.MANIFEST).read_text(encoding="utf-8"))["lang"] == "zh")
    q = ("SELECT uid, eligibility, interest, action FROM labels ORDER BY uid",
         "SELECT uid, title, company, description FROM jobs ORDER BY uid",
         "SELECT uid, tier, total_score FROM scores ORDER BY uid")
    check("除标注文本外，岗位 / 分数 / 标注取值两版完全一致",
          all(db_rows(en, s) == db_rows(zh, s) for s in q))
    rc_err = io.StringIO()
    with contextlib.redirect_stderr(rc_err):
        try:
            demo.main(["generate", "--root", str(base / "lang-cli"), "--lang", "fr"])
            ok = False
        except SystemExit as exc:
            ok = exc.code == 2
    check("CLI --lang 只接受 en / zh", ok)


def test_refuse_overwrite(base: Path, root: Path, guard_factory) -> None:
    print("== 拒绝覆盖 / 失败不留成功假象 ==")
    before = {p.name: digest(p) for p in root.iterdir() if p.is_file() and p.suffix != ""}
    with guard_factory([root]) as g:
        try:
            demo.generate(root)
            refused = False
        except demo.DemoError as exc:
            refused = "拒绝覆盖" in str(exc)
    check("已存在的 demo 根被拒绝", refused)
    check("拒绝后旧目录逐字节不变", before == {p.name: digest(p) for p in root.iterdir() if p.is_file() and p.suffix != ""})
    check("拒绝路径零守卫违规", g.violations == [])

    empty = base / "empty-existing"
    empty.mkdir()
    try:
        demo.generate(empty)
        ok = False
    except demo.DemoError:
        ok = True
    check("连空的已有目录也拒绝（不做「合并写入」）", ok and list(empty.iterdir()) == [])

    # A deployed checkout may already contain data/. Compare the pre-existing
    # directory state instead of assuming this is a fresh clone.
    protected_dirs = (ROOT / "data", ROOT / "data" / "demo", ROOT / "jobs" / "demo-x")
    before_dirs = {p: (p.exists(), p.is_symlink()) for p in protected_dirs}
    for bad in (ROOT, ROOT.parent, ROOT / "data" / "demo", ROOT / "jobs" / "demo-x"):
        try:
            demo.generate(bad)
            ok = False
        except demo.DemoError:
            ok = True
        check(f"拒绝落在仓库代码 / 生产数据区的 demo 根：{bad.relative_to(ROOT.parent)}", ok)
    check("拒绝操作保持既有目录状态，不新建 data/demo 或 jobs/demo-x",
          before_dirs == {p: (p.exists(), p.is_symlink()) for p in protected_dirs})

    import jobs.cli as cli
    original = cli.cmd_analyze
    failed = base / "fails"
    try:
        cli.cmd_analyze = lambda args: 1
        try:
            demo.generate(failed)
            raised = False
        except demo.DemoError:
            raised = True
        check("analyze 失败 → 抛错且 demo 根被清掉", raised and not failed.exists())

        def boom(args):
            raise RuntimeError("synthetic failure")
        cli.cmd_analyze = boom
        failed2 = base / "fails-2"
        try:
            demo.generate(failed2)
            raised = False
        except RuntimeError:
            raised = True
        check("analyze 抛异常 → 异常原样上抛且 demo 根被清掉", raised and not failed2.exists())
    finally:
        cli.cmd_analyze = original
    half = base / "half"
    half.mkdir()
    (half / "jobs.db").write_bytes(b"")
    try:
        demo.load_demo(half)
        ok = False
    except demo.DemoError:
        ok = True
    check("没有清单的半成品目录不能被启动", ok)


def test_guard_selftest(base: Path, root: Path) -> None:
    print("== 守卫自测：证明它真的会拒绝 ==")
    ext = base / "guard-ext"
    ext.mkdir()
    (ext / "sentinel.txt").write_text(SENTINEL, encoding="utf-8")
    sqlite3.connect(ext / "ext.db").close()
    link = base / "guard-link-root"
    shutil.copytree(root, link, ignore=shutil.ignore_patterns("jobs.db*"))
    os.symlink(ext / "sentinel.txt", link / "skills.local.yaml")
    ext_before = snapshot_dir(ext)
    cases = [
        ("读外部合成 sentinel", lambda: open(ext / "sentinel.txt", "rb").close()),
        ("写外部合成 sentinel", lambda: open(ext / "sentinel.txt", "w").close()),
        ("追加外部合成 sentinel", lambda: open(ext / "sentinel.txt", "a").close()),
        ("在外部目录新建文件", lambda: open(ext / "created.txt", "w").close()),
        ("连外部合成 SQLite", lambda: sqlite3.connect(ext / "ext.db")),
        ("经 demo 根内符号链接读外部", lambda: open(link / "skills.local.yaml", "rb").close()),
        ("经 demo 根内符号链接写外部", lambda: open(link / "skills.local.yaml", "w").close()),
        ("读生产 config.yaml", lambda: open(ROOT / "config.yaml", "rb").close()),
        ("读生产 rules.yaml", lambda: open(ROOT / "rules.yaml", "rb").close()),
        ("读生产 skills.yaml", lambda: open(ROOT / "skills.yaml", "rb").close()),
        ("读生产 skills.local.yaml", lambda: open(ROOT / "skills.local.yaml", "rb").close()),
        ("连生产 data/jobs.db", lambda: sqlite3.connect(ROOT / "data" / "jobs.db")),
        ("写 demo 根之外", lambda: open(base / "outside.txt", "w").close()),
        ("写仓库 out/（demo 根之外）", lambda: open(ROOT / "out" / "x.txt", "w").close()),
        ("连外网 IP", lambda: socket.create_connection(("93.184.216.34", 80), timeout=1)),
        ("解析外网域名", lambda: socket.getaddrinfo("example.com", 80)),
        ("绑定通配地址", lambda: socket.socket().bind(("0.0.0.0", 0))),
        ("绑定局域网地址", lambda: socket.socket().bind(("192.0.2.1", 0))),
        ("启动子进程", lambda: __import__("subprocess").run(["true"])),
    ]
    for label, fn in cases:
        with Guard([root]) as g:
            try:
                fn()
                denied = False
            except GuardViolation:
                denied = True
            except Exception:                               # noqa: BLE001
                denied = False
        check(f"拒绝：{label}", denied and len(g.violations) == 1, str(g.violations))
    check("守卫自测没有改动外部合成目录", snapshot_dir(ext) == ext_before)
    with Guard([root]) as g:
        try:
            open(os.__file__, "rb").close()                  # 运行时标准库仍可读
            open(root / "config.yaml", "rb").close()
            sqlite3.connect(root / "jobs.db").close()
            s = socket.socket()
            s.bind(("127.0.0.1", 0))
            s.close()
            allowed = True
        except GuardViolation:
            allowed = False
    check("允许：标准库、demo 根内读 / 连库、回环绑定", allowed and g.violations == [])
    check("守卫自测没有写出 outside.txt", not (base / "outside.txt").exists()
          and not (ROOT / "out" / "x.txt").exists())


def test_panel_regression() -> None:
    print("== 生产默认行为：没有 demo 字段时与原来一致 ==")
    from jobs import panel_views
    root_cfg = {"panel": {"page_size": 10}}
    check("无 demo 段 → 不渲染横幅", panel_views._demo_banner(root_cfg) == "")
    check("有 demo 段 → 英文横幅（默认）",
          "Demo · Synthetic data" in panel_views._demo_banner({"demo": {"id": "x"}}, "en"))
    check("有 demo 段 → 中文横幅随语言切换",
          "演示 · 合成数据" in panel_views._demo_banner({"demo": {"id": "x"}}, "zh")
          and "Synthetic" not in panel_views._demo_banner({"demo": {"id": "x"}}, "zh"))

    calls = []
    real = (skills_mod.load_skills, skills_mod.load_local, skills_mod.load_base)
    skills_mod.load_skills = lambda path=None: calls.append(("skills", path)) or {"skills": []}
    skills_mod.load_local = lambda path=None: calls.append(("local", path)) or {"add": [], "alias": {}, "remove": []}
    skills_mod.load_base = lambda path=None: calls.append(("base", path)) or {"skills": []}
    try:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        from jobs import db as database
        conn.executescript(database.SCHEMA)
        html = panel_views.render_page(conn, root_cfg, "recommend", 0, 10)
        settings = panel_views.render_settings(conn, root_cfg)
        conn.close()
    finally:
        skills_mod.load_skills, skills_mod.load_local, skills_mod.load_base = real
    check("生产配置的页面 / 设置页没有 demo 横幅", "demo-banner" not in html.split("<style>")[0] + html.split("</style>")[1]
          and "Synthetic" not in html and "Synthetic" not in settings)
    check("没有 skills_path 时仍走仓库默认词表（path=None）", calls and all(p is None for _, p in calls),
          str(calls))


def test_server(base: Path, root: Path, guard_factory) -> None:
    print("== 面板：读取、筛选、详情、设置、标注写入（守卫生效）==")
    snapshot_before = repo_snapshot()
    prod_before = {n: digest(ROOT / n) for n in PROTECTED}
    prod_dirs_before = ((ROOT / "data").exists(), (ROOT / "out").exists())
    skills_local_before = digest(ROOT / "skills.local.yaml")

    out = io.StringIO()
    with guard_factory([root]) as g, contextlib.redirect_stdout(out):
        httpd, url = demo.make_server(root, "127.0.0.1", 0)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            run_server_checks(root, port, g)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)
    check("URL 用 localhost 名称展示且绑定回环", url.startswith("http://localhost:"))
    check("服务期间零守卫违规（无越界读写 / 外网 / 子进程）", g.violations == [], str(g.violations[:5]))

    check("生产配置 / 规则 / 词表 / README 逐字节不变", prod_before == {n: digest(ROOT / n) for n in PROTECTED})
    check("生产 skills.local.yaml 不变", skills_local_before == digest(ROOT / "skills.local.yaml"))
    check("没有创建生产 data/ 或 out/", (ROOT / "data").exists() == prod_dirs_before[0]
          and (ROOT / "out").exists() == prod_dirs_before[1])
    after = repo_snapshot()
    check("仓库内没有任何文件被新增 / 修改 / 删除", snapshot_before == after,
          str(sorted(set(snapshot_before.items()) ^ set(after.items()))[:4]))


def run_server_checks(root: Path, port: int, g: Guard) -> None:
    status, page = http_get(port, "/", {"Accept-Language": "en"})
    check("推荐视图 200 + 显著 Demo 横幅", status == 200 and "Demo · Synthetic data" in page)
    check("页面标题带 Demo", "· Demo</title>" in page)
    check("推荐视图 = 未标注 + 非过期 + 未排除的 8 组", cards(page) == 8, f"{cards(page)}")
    check("页面不引用任何外部资源（无 CDN / 外链样式脚本）",
          not re.search(r'(src|href)="https?://(?!example\.invalid)', page))
    check("卡片里是合成公司",
          all(name in page for name in ("Tidewater", "Larkspur", "Wattlebird", "Saltbush")))

    counts = {}
    for view in ("all", "new", "excluded", "labelled"):
        s, html = http_get(port, f"/?view={view}", {"Accept-Language": "en"})
        counts[view] = (s, cards(html))
    check("全部视图首页 10 张（page_size=10，共 16 组，分页）", counts["all"] == (200, 10), str(counts))
    check("被排除视图 2 张", counts["excluded"] == (200, 2), str(counts))
    check("已标注视图 6 张", counts["labelled"] == (200, 6), str(counts))
    check("新增视图（合成 fetch 批次）首页 10 张", counts["new"] == (200, 10), str(counts))
    _, page2 = http_get(port, "/?view=all&offset=10", {"Accept-Language": "en"})
    check("全部视图第二页 6 张", cards(page2) == 6)

    _, f1 = http_get(port, "/?view=all&action=skipped", {"Accept-Language": "en"})
    check("筛选：进度=跳过 → 2 张", cards(f1) == 2, f"{cards(f1)}")
    check("跳过卡片沿用既有红色 tag 语义（action 标记存在）", "skipped" in f1 or "跳过" in f1 or "Skipped" in f1)
    _, f2 = http_get(port, "/?view=all&skill=Python&skill=Docker", {"Accept-Language": "en"})
    check("筛选：技能 Python/Docker（任一命中，去重后）", 0 < cards(f2) < 16, f"{cards(f2)}")
    _, f3 = http_get(port, "/?view=all&q=graduate", {"Accept-Language": "en"})
    check("搜索 graduate 命中至少 4 组", cards(f3) >= 4, f"{cards(f3)}")
    _, f4 = http_get(port, "/?view=all&salary=1", {"Accept-Language": "en"})
    check("只看有薪资 → 少于全部", 0 < cards(f4) < 16, f"{cards(f4)}")

    s, body = http_get(port, "/api/job?uid=linkedin:DEMO-002")
    detail = json.loads(body)["html"] if s == 200 else ""
    check("详情接口返回合成岗位正文", s == 200 and "Quillfeather" in detail and "example.invalid" in detail)
    s, body = http_get(port, "/api/job?uid=linkedin:DEMO-019")
    check("缺描述岗位的详情也能渲染", s == 200 and "QA Intern" in body)
    s, _ = http_get(port, "/api/job?uid=seek:NOPE")
    check("不存在的 uid → 404", s == 404)

    s, settings = http_get(port, "/settings", {"Accept-Language": "en"})
    check("设置页 200 + Demo 横幅", s == 200 and "Demo · Synthetic data" in settings)
    _, zh_page = http_get(port, "/?view=all", {"Accept-Language": "zh-CN"})
    _, zh_settings = http_get(port, "/settings", {"Accept-Language": "zh-CN"})
    check("中文界面：列表页与设置页横幅都是中文",
          "演示 · 合成数据" in zh_page and "演示 · 合成数据" in zh_settings
          and "Synthetic data" not in zh_page and "· 演示</title>" in zh_page)
    s, body = http_get(port, "/api/skills")
    tree = json.loads(body)
    names = {e["name"] for e in tree["skills"]}
    check("词表只有合成词表（22 个通用技术名）", s == 200 and len(names) == 22 and "Python" in names, str(len(names)))
    s, _ = http_get(port, "/settings?section=display", {"Accept-Language": "en"})
    check("设置页的外观分区 200", s == 200)

    # ---- 写交互：标注 / 投递 ----
    uid = "seek:DEMO-010"
    s, body = http_post(port, "/api/label", {"uid": uid, "field": "action", "value": "applied"})
    check("标注：标记已投递 → 200", s == 200 and json.loads(body)["value"] == "applied", body)
    s, body = http_post(port, "/api/label", {"uid": uid, "field": "interest", "value": "want"})
    s2, _ = http_post(port, "/api/label", {"uid": uid, "field": "note", "value": "技术栈匹配"})
    check("标注：意向 / 备注 → 200", s == 200 and s2 == 200)
    s, _ = http_post(port, "/api/label", {"uid": uid, "field": "action", "value": "bogus"})
    check("非法取值 → 400，不落库", s == 400)
    with g.pause():
        row = db_rows(root, "SELECT * FROM labels WHERE uid=?", uid)
        events = db_rows(root, "SELECT source FROM label_events WHERE uid=?", uid)
    check("写入落在 demo 库：action / interest / note",
          bool(row) and (row[0]["action"], row[0]["interest"], row[0]["note"]) == ("applied", "want", "技术栈匹配；"))
    check("标注留痕来源 panel", {e["source"] for e in events} == {"panel"})
    _, labelled = http_get(port, "/?view=labelled", {"Accept-Language": "en"})
    check("刷新后已标注视图 7 张", cards(labelled) == 7, f"{cards(labelled)}")

    # ---- 写交互：设置（加技能 / 别名）----
    s, body = http_post(port, "/api/skills", {"token": "Rust", "action": "add"})
    check("设置：新增技能 Rust → 200", s == 200, body)
    s, body = http_post(port, "/api/skills", {"token": "Postgres SQL", "action": "alias", "target": "PostgreSQL"})
    check("设置：给已有技能加别名 → 200（或已存在的幂等错误）", s in (200, 400), body)
    local = root / "skills.local.yaml"
    with g.pause():
        wrote = local.exists() and "Rust" in local.read_text(encoding="utf-8")
    check("技能编辑写进 demo 根的 skills.local.yaml", wrote)
    s, body = http_get(port, "/api/skills")
    check("新增技能立刻出现在词表接口", "Rust" in {e["name"] for e in json.loads(body)["skills"]})
    s, _ = http_post(port, "/api/nope", {})
    check("未知 POST 路径 → 404", s == 404)


def _ext_dir(base: Path, name: str, root: Path) -> Path:
    """外部合成目录：一份会泄露的词表补充层 + 一份带哨兵岗位的 SQLite。"""
    ext = base / f"ext-{name}"
    ext.mkdir()
    (ext / "local.yaml").write_text(
        f"version: 1\nadd:\n- name: {SENTINEL}\n  aliases: []\n  not: []\nalias: {{}}\nremove: []\n",
        encoding="utf-8")
    shutil.copyfile(root / "config.yaml", ext / "config.yaml")
    shutil.copyfile(root / DEMO_MANIFEST, ext / DEMO_MANIFEST)
    src = sqlite3.connect(root / "jobs.db")
    dst = sqlite3.connect(ext / "other.db")
    src.backup(dst)
    dst.execute("UPDATE jobs SET title = 'EXTERNAL-SENTINEL-JOB'")
    dst.commit()
    dst.close()
    src.close()
    (ext / "other.db-wal").unlink(missing_ok=True)
    return ext


DEMO_MANIFEST = demo.MANIFEST


def _fresh_root(base: Path, name: str, guard_factory) -> tuple[Path, Path]:
    root = base / f"sym-{name}"
    with guard_factory([root]):
        demo.generate(root)
    return root, _ext_dir(base, name, root)


def test_link_boundaries(base: Path, guard_factory) -> None:
    print("== 链接边界：符号链接 / 悬空链接 / 硬链接不得让 demo 读写根外 ==")

    def swap(path: Path, target: Path) -> None:
        if path.exists() or path.is_symlink():
            path.unlink()
        os.symlink(target, path)

    def hard(path: Path, target: Path) -> None:
        path.unlink(missing_ok=True)
        os.link(target, path)

    start_cases = [
        ("skills.local.yaml 链到外部已有文件", lambda r, e: swap(r / "skills.local.yaml", e / "local.yaml")),
        ("skills.local.yaml 是悬空链接", lambda r, e: swap(r / "skills.local.yaml", e / "missing.yaml")),
        ("skills.local.yaml 与外部文件是硬链接", lambda r, e: hard(r / "skills.local.yaml", e / "local.yaml")),
        ("config.yaml 是符号链接", lambda r, e: swap(r / "config.yaml", e / "config.yaml")),
        ("demo-manifest.json 是符号链接", lambda r, e: swap(r / DEMO_MANIFEST, e / DEMO_MANIFEST)),
        ("jobs.db 链到外部 SQLite", lambda r, e: (os.rename(r / "jobs.db", r / "jobs.db.aside"),
                                                  swap(r / "jobs.db", e / "other.db"))),
        ("jobs.db-wal 是悬空链接", lambda r, e: swap(r / "jobs.db-wal", e / "missing.wal")),
        ("jobs.db-shm 链到外部文件", lambda r, e: swap(r / "jobs.db-shm", e / "local.yaml")),
    ]
    for i, (label, setup) in enumerate(start_cases):
        root, ext = _fresh_root(base, f"s{i}", guard_factory)
        for side in ("jobs.db-wal", "jobs.db-shm"):          # 干净起点：先去掉旁路文件
            (root / side).unlink(missing_ok=True)
        setup(root, ext)
        before = snapshot_dir(ext)
        with guard_factory([root]) as g:
            try:
                demo.make_server(root, "127.0.0.1", 0)
                refused, msg = False, ""
            except demo.DemoError as exc:
                refused, msg = True, str(exc)
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                rc = demo.main(["serve", "--root", str(root), "--port", "0"])
        check(f"启动前：{label} → make_server 与 CLI 都拒绝（rc=2）",
              refused and rc == 2 and "检查失败" in err.getvalue(), msg)
        check(f"启动前：{label} → 外部不变、不创建目标、输出不泄露",
              snapshot_dir(ext) == before and not (ext / "missing.yaml").exists()
              and not (ext / "missing.wal").exists() and SENTINEL not in err.getvalue() + msg
              and g.violations == [], str(g.violations[:2]))

    # ---- 启动后、POST 前才出现链接 ----
    def served(root: Path):
        out = io.StringIO()
        ctx = contextlib.redirect_stdout(out)
        ctx.__enter__()
        httpd, _ = demo.make_server(root, "127.0.0.1", 0)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        return httpd, thread, ctx

    runtime_cases = [
        ("skills.local.yaml 换成指向外部文件的符号链接",
         lambda r, e: swap(r / "skills.local.yaml", e / "local.yaml")),
        ("skills.local.yaml 换成悬空符号链接",
         lambda r, e: swap(r / "skills.local.yaml", e / "missing.yaml")),
        ("skills.local.yaml 换成与外部文件的硬链接",
         lambda r, e: hard(r / "skills.local.yaml", e / "local.yaml")),
        ("jobs.db 换成指向外部 SQLite 的符号链接",
         lambda r, e: (os.rename(r / "jobs.db", r / "jobs.db.aside"), swap(r / "jobs.db", e / "other.db"))),
        ("jobs.db-wal 换成悬空符号链接",
         lambda r, e: swap(r / "jobs.db-wal", e / "missing.wal")),
    ]
    for i, (label, setup) in enumerate(runtime_cases):
        root, ext = _fresh_root(base, f"r{i}", guard_factory)
        with guard_factory([root]) as g:
            httpd, thread, ctx = served(root)
            port = httpd.server_address[1]
            try:
                s0, _ = http_get(port, "/api/skills")          # 起点正常
                with g.pause():
                    for side in ("jobs.db-wal", "jobs.db-shm"):
                        if "wal" not in label:
                            continue
                        (root / side).unlink(missing_ok=True)
                    setup(root, ext)
                    before = snapshot_dir(ext)
                s1, b1 = http_get(port, "/api/skills")
                s2, b2 = http_get(port, "/?view=all")
                s3, b3 = http_post(port, "/api/skills", {"token": "Rust", "action": "add"})
                s4, b4 = http_post(port, "/api/label",
                                   {"uid": "seek:DEMO-010", "field": "action", "value": "applied"})
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=5)
                ctx.__exit__(None, None, None)
        bodies = b1 + b2 + b3 + b4
        check(f"启动后：{label} → GET / POST 全部 403，响应写明检查失败",
              (s0, s1, s2, s3, s4) == (200, 403, 403, 403, 403)
              and all("demo data check failed" in b for b in (b1, b2, b3, b4)), str((s0, s1, s2, s3, s4)))
        check(f"启动后：{label} → 外部字节 / mtime / 链接数不变、不泄露、不创建目标",
              snapshot_dir(ext) == before and SENTINEL not in bodies and "EXTERNAL-SENTINEL-JOB" not in bodies
              and not (ext / "missing.yaml").exists() and not (ext / "missing.wal").exists(),
              str(sorted(set(snapshot_dir(ext).items()) ^ set(before.items()))[:3]))
        check(f"启动后：{label} → 服务端没有触碰外部路径（守卫零违规）", g.violations == [], str(g.violations[:2]))

    # ---- 恢复：移除链接后正常使用，说明预检不是「一次失败永久失败」----
    root, ext = _fresh_root(base, "recover", guard_factory)
    with guard_factory([root]) as g:
        httpd, thread, ctx = served(root)
        port = httpd.server_address[1]
        try:
            with g.pause():
                swap(root / "skills.local.yaml", ext / "local.yaml")
                before = snapshot_dir(ext)
            bad, _ = http_post(port, "/api/skills", {"token": "Rust", "action": "add"})
            with g.pause():
                (root / "skills.local.yaml").unlink()
            ok, body = http_post(port, "/api/skills", {"token": "Rust", "action": "add"})
            lab, _ = http_post(port, "/api/label", {"uid": "seek:DEMO-010", "field": "action", "value": "saved"})
            _, page = http_get(port, "/?view=all", {"Accept-Language": "en"})
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)
            ctx.__exit__(None, None, None)
    wrote = "Rust" in (root / "skills.local.yaml").read_text(encoding="utf-8")
    check("移除链接后：词表写入 / 标注 / 页面恢复正常，且只写 demo 根", (bad, ok, lab) == (403, 200, 200)
          and wrote and cards(page) == 10 and snapshot_dir(ext) == before and g.violations == [])

    # ---- 普通路径：已有 demo 复用（含 SQLite 旁路文件）仍可启动 ----
    root, _ = _fresh_root(base, "plain", guard_factory)
    with guard_factory([root]) as g:
        httpd, _ = demo.make_server(root, "127.0.0.1", 0)
        httpd.server_close()
    check("普通的新生成 demo（含 SQLite 旁路文件）仍可启动", g.violations == [])
    check_paths_ok = demo.check_paths(root.resolve()) == []
    check("check_paths 对普通 demo 根返回空", check_paths_ok)


def test_server_refusals(root: Path, guard_factory) -> None:
    print("== 启动边界：只回环、不占生产端口、占用不抢 ==")
    for host in ("0.0.0.0", "", "::", "::1", "192.0.2.1", "example.com"):
        try:
            demo.make_server(root, host, 0)
            ok = False
        except demo.DemoError:
            ok = True
        check(f"拒绝监听 host={host!r}", ok)
    try:
        demo.make_server(root, "127.0.0.1", demo.PRODUCTION_PORT)
        ok = False
    except demo.DemoError as exc:
        ok = "8090" in str(exc)
    check("拒绝占用生产端口 8090", ok)

    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    busy = holder.getsockname()[1]
    try:
        demo.make_server(root, "127.0.0.1", busy)
        ok = False
    except demo.DemoError as exc:
        ok = "不会去停止" in str(exc)
    still = socket.create_connection(("127.0.0.1", busy), timeout=2)
    still.close()
    holder.close()
    check("端口被占用 → 报错，原监听者不受影响", ok)

    tampered = Path(tempfile.mkdtemp(prefix="demo-tamper-"))
    try:
        shutil.copytree(root, tampered / "d")
        cfg = tampered / "d" / "config.yaml"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace(str(root), str(ROOT / "data")), encoding="utf-8")
        try:
            demo.load_demo(tampered / "d")
            ok = False
        except demo.DemoError as exc:
            ok = "demo 根之外" in str(exc)
        check("配置里的路径指向 demo 根之外 → 拒绝启动（不会回退生产 DB）", ok)
        cfg.write_text(cfg.read_text(encoding="utf-8").replace("skills_path:", "x_skills_path:"), encoding="utf-8")
        try:
            demo.load_demo(tampered / "d")
            ok = False
        except demo.DemoError:
            ok = True
        check("缺少 skills_path → 拒绝启动（不静默回退 skills.yaml）", ok)
    finally:
        shutil.rmtree(tampered, ignore_errors=True)


def test_cli(base: Path, guard_factory) -> None:
    print("== 一键入口 run / generate ==")
    root = base / "cli-demo"
    calls = []
    real_serve = demo.serve
    demo.serve = lambda r, host=None, port=None: calls.append((Path(r), host, port))
    try:
        with guard_factory([root]) as g, contextlib.redirect_stdout(io.StringIO()):
            rc1 = demo.main(["run", "--root", str(root), "--port", "18091"])
        check("run：根不存在 → 生成并启动", rc1 == 0 and (root / demo.MANIFEST).is_file()
              and calls == [(root.resolve(), None, 18091)], str(calls))
        before = digest(root / "jobs.db")
        with contextlib.redirect_stdout(io.StringIO()):
            rc2 = demo.main(["run", "--root", str(root)])
        check("run：已有 demo → 复用并启动，不覆盖数据库", rc2 == 0 and digest(root / "jobs.db") == before
              and len(calls) == 2)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc3 = demo.main(["generate", "--root", str(root)])
        check("generate：目标已存在 → 退出码 2 + 拒绝覆盖提示", rc3 == 2 and "拒绝覆盖" in err.getvalue())
        demo.serve = real_serve                 # serve 用真实实现：它应在启动前就失败
        with contextlib.redirect_stderr(io.StringIO()):
            rc4 = demo.main(["serve", "--root", str(base / "missing")])
        check("serve：没有生成过的目录 → 退出码 2，不创建目录", rc4 == 2 and not (base / "missing").exists())
        check("CLI 全程零守卫违规", g.violations == [], str(g.violations))
    finally:
        demo.serve = real_serve


def main() -> int:
    base = Path(tempfile.mkdtemp(prefix="demo-test-"))
    try:
        test_sources()
        root = test_generate(base, Guard)
        test_deterministic(base, Guard)
        test_lang_flag(base, Guard)
        test_refuse_overwrite(base, root, Guard)
        test_guard_selftest(base, root)
        test_panel_regression()
        test_server(base, root, Guard)
        test_server_refusals(root, Guard)
        test_link_boundaries(base, Guard)
        test_cli(base, Guard)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print()
    if failures:
        print(f"{len(failures)} 项失败：")
        for label in failures:
            print(f"  - {label}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
