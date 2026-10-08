"""独立合成演示模式：生成一份全合成的 demo 数据目录，并用现有面板在本地回环地址展示。

这个模块**不是**生产流水线的一部分，也不改它：
  - 不抓取、不联网、不 import 任何来源适配器，不启动后台服务；
  - 只写一个**全新的** demo 根目录（默认 out/demo/），已存在就拒绝，绝不覆盖；
  - 读的配置 / 规则 / 词表只有仓库 demo/ 下显式命名的合成文件，
    生成出的 config.yaml 里 db_path / report.out_dir / skills_path 全是 demo 根下的绝对路径。
    不回退 config.yaml / rules.yaml / skills*.yaml，也不碰生产 data/jobs.db；
  - 面板复用现有实现，demo 上下文只靠 config 里两个显式字段进入：
    `skills_path`（词表读写位置）与 `demo`（顶栏标识）。

用法：
    python -m jobs.demo generate   # 只生成，不启动，可直接检查 out/demo/
    python -m jobs.demo serve      # 启动已生成的 demo（默认 http://localhost:8091）
    python -m jobs.demo run        # 一键：没有就生成，然后启动；已有就复用，不覆盖
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import io
import json
import os
import shutil
import stat
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path

import yaml

from . import db as database
from . import labels as labels_mod
from .config import ROOT, load_config

DEMO_ID = "jd-board-demo-001"
SOURCE_DIR = ROOT / "demo"
DEFAULT_ROOT = ROOT / "out" / "demo"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8091
PRODUCTION_PORT = 8090                  # 生产常用端口：demo 不占用
LOOPBACK_HOSTS = ("127.0.0.1", "localhost")   # 面板服务是 AF_INET，不支持 ::1
MANIFEST = "demo-manifest.json"         # 最后写：没有它的目录不算一份完整 demo
MIN_JOBS, MAX_JOBS = 10, 20
LANGS = ("en", "zh")                    # 标注文本语言：默认英文，--lang zh 生成中文版

# 复制进 demo 根的合成输入（仓库内文件名 -> demo 根内文件名）
SOURCE_FILES = {"demo.rules.yaml": "rules.yaml", "demo.skills.yaml": "skills.yaml"}


class DemoError(Exception):
    """demo 生成 / 启动的可预期失败。"""


# ---------------------------------------------------------------------------
# 生成
# ---------------------------------------------------------------------------

def _check_root(root: Path) -> None:
    """demo 根必须是全新的路径，且不能落在仓库的代码 / 生产数据区。"""
    if root.exists():
        raise DemoError(f"拒绝覆盖：{root} 已存在。请换一个 --root，或自行移走旧目录。")
    if root == ROOT or root in ROOT.parents:
        raise DemoError(f"拒绝：demo 根不能是仓库目录或它的上级：{root}")
    if ROOT in root.parents and (ROOT / "out") not in root.parents:
        raise DemoError(f"拒绝：仓库内的 demo 根必须在 {ROOT / 'out'} 之下：{root}")


def _load_fixture() -> dict:
    path = SOURCE_DIR / "demo.jobs.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    if fixture.get("demo_id") != DEMO_ID:
        raise DemoError(f"{path.name} 的 demo_id 不是 {DEMO_ID}")
    jobs = fixture.get("jobs") or []
    if not MIN_JOBS <= len(jobs) <= MAX_JOBS:
        raise DemoError(f"合成岗位应为 {MIN_JOBS}～{MAX_JOBS} 条，实际 {len(jobs)}")
    uids = [f"{j['source']}:{j['source_id']}" for j in jobs]
    if len(set(uids)) != len(uids):
        raise DemoError("合成岗位 uid 重复")
    unknown = [lb["uid"] for lb in fixture.get("labels") or [] if lb["uid"] not in uids]
    if unknown:
        raise DemoError(f"标注指向不存在的岗位：{unknown}")
    return fixture


def _label_fields(label: dict, lang: str) -> dict:
    """标注里的文本字段是 {en, zh}，按 lang 取一个；其余字段原样。"""
    out = {}
    for key, value in label.items():
        if key == "uid":
            continue
        out[key] = value[lang] if isinstance(value, dict) else value
    return out


def _job_record(raw: dict, today: dt.date) -> dict:
    job = {k: v for k, v in raw.items() if k != "days_ago"}
    listed = today - dt.timedelta(days=int(raw["days_ago"]))
    # 平台日期格式不一致是真实存在的情况，demo 里让 linkedin 带时间、其余纯日期
    job["listing_date"] = (f"{listed.isoformat()}T09:00:00" if raw["source"] == "linkedin"
                           else listed.isoformat())
    # example.invalid 是保留域名，永远解析不到
    job["url"] = f"https://example.invalid/demo/{raw['source']}/{raw['source_id']}"
    job["raw"] = {"demo": DEMO_ID}
    return job


def generate(root: str | Path, today: dt.date | None = None, lang: str = "en") -> dict:
    """生成一份新的 demo 数据目录。返回摘要 dict；失败时不留下半成品。

    lang 只决定合成标注（备注 / 不可投原因）的语言；岗位正文始终是英文。"""
    if lang not in LANGS:
        raise DemoError(f"lang 必须是 {LANGS} 之一，收到 {lang!r}")
    root = Path(root).expanduser().resolve()
    _check_root(root)
    today = today or dt.date.today()
    fixture = _load_fixture()
    template = yaml.safe_load((SOURCE_DIR / "demo.config.yaml").read_text(encoding="utf-8"))

    if not root.parent.exists():        # 默认 out/ 可能还没有；已有的上级目录不碰
        root.parent.mkdir(parents=True)
    try:
        root.mkdir()                    # 不带 exist_ok：和 _check_root 之间被抢先创建也会失败
    except FileExistsError as exc:
        raise DemoError(f"拒绝覆盖：{root} 已存在") from exc

    try:
        return _build(root, today, fixture, template, lang)
    except BaseException:
        # 失败不得留下看起来像成功的目录；root 是上面刚创建的，只清理它
        shutil.rmtree(root, ignore_errors=True)
        raise


def _build(root: Path, today: dt.date, fixture: dict, template: dict, lang: str) -> dict:
    # 1. 词表 / 规则：复制合成输入进 demo 根。词表的本地补充层
    #    （面板里加技能 / 别名）固定写在这份副本旁边，不会回写仓库。
    for src, dst in SOURCE_FILES.items():
        shutil.copyfile(SOURCE_DIR / src, root / dst)

    # 2. 配置：相对 DB 路径会锚到项目 ROOT 而不是配置目录，所以一律写绝对路径
    config = dict(template)
    config["db_path"] = str(root / "jobs.db")
    config["report"] = {**(template.get("report") or {}), "out_dir": str(root / "reports")}
    config["skills_path"] = str(root / "skills.yaml")
    config_path = root / "config.yaml"
    config_path.write_text(
        "# Generated by jobs/demo.py. Synthetic demo only; every path is inside the demo root.\n"
        + yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8")
    (root / "reports").mkdir()
    rules_version = (yaml.safe_load((root / "rules.yaml").read_text(encoding="utf-8")) or {}).get("version")

    # 3. 合成岗位入库。「新增」视图按最近一次 fetch 批次取数，所以补一条合成的
    #    fetch 记录（不是真的抓取，stats 里标明 synthetic）。
    conn = database.connect(config["db_path"])
    try:
        started = database.utcnow()
        for raw in fixture["jobs"]:
            database.upsert_job(conn, _job_record(raw, today))
        database.log_run(conn, "fetch", started,
                         {"synthetic": True, "demo_id": DEMO_ID, "jobs": len(fixture["jobs"])})
        conn.commit()
    finally:
        conn.close()

    # 4. 复用现有 analyze（过滤 + 打分 + 薪资 + 去重 + 技能），所有路径显式传入
    from .cli import cmd_analyze
    args = argparse.Namespace(config=str(config_path), rules=str(root / "rules.yaml"),
                              skills=str(root / "skills.yaml"))
    with contextlib.redirect_stdout(io.StringIO()):
        if cmd_analyze(args) != 0:
            raise DemoError("demo 分析失败")

    # 5. 合成标注 / 投递状态
    conn = database.connect(config["db_path"])
    try:
        for label in fixture.get("labels") or []:
            fields = _label_fields(label, lang)
            labels_mod.set_label(conn, label["uid"], rules_version=rules_version,
                                 source="demo", **fields)
        summary = {
            "demo_id": DEMO_ID,
            "generated_on": today.isoformat(),
            "lang": lang,
            "jobs": conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],
            "groups": conn.execute("SELECT COUNT(DISTINCT dedupe_group) FROM jobs").fetchone()[0],
            "excluded": conn.execute("SELECT COUNT(*) FROM decisions WHERE is_excluded=1").fetchone()[0],
            "labelled": conn.execute("SELECT COUNT(*) FROM labels").fetchone()[0],
        }
    finally:
        conn.close()
    if summary["jobs"] != len(fixture["jobs"]):
        raise DemoError(f"入库 {summary['jobs']} 条，与合成数据 {len(fixture['jobs'])} 条不一致")

    summary["root"] = str(root)
    # 清单最后写：它存在就表示上面每一步都成功了
    (root / MANIFEST).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------

# demo 根里会被读 / 写的固定文件名。SQLite 的附属文件和词表补充层是**派生**路径，
# 配置里没写，但同样会被面板读写，所以也要检查。
_REQUIRED_FILES = (MANIFEST, "config.yaml", "rules.yaml", "skills.yaml", "jobs.db")
_OPTIONAL_FILES = ("skills.local.yaml", "jobs.db-wal", "jobs.db-shm", "jobs.db-journal")


def check_paths(root: Path) -> list[str]:
    """demo 根里的读写路径是否都是**本根内的普通单链接文件**。返回问题列表（空 = 通过）。

    拒绝：符号链接（含悬空链接）、非普通文件、多个硬链接（写它会改到根外共享的 inode）。
    skills.local.yaml 与 SQLite 附属文件允许不存在。提示里只写文件名，不写链接目标，
    也不读目标内容。这是对正常用户误链接 / 请求前已存在的替换的安全失败，
    不声称防御并发的恶意 TOCTOU。
    """
    problems = []
    try:
        if os.path.realpath(root) != str(root) or not root.is_dir():
            return ["the demo root is not a plain directory"]
    except OSError:
        return ["the demo root cannot be checked"]
    for names, required in ((_REQUIRED_FILES, True), (_OPTIONAL_FILES, False)):
        for name in names:
            try:
                st = os.lstat(root / name)
            except FileNotFoundError:
                if required:
                    problems.append(f"{name} is missing")
                continue
            except OSError:
                problems.append(f"{name} cannot be checked")
                continue
            if stat.S_ISLNK(st.st_mode):
                problems.append(f"{name} is a symbolic link")
            elif not stat.S_ISREG(st.st_mode):
                problems.append(f"{name} is not a regular file")
            elif st.st_nlink > 1:
                problems.append(f"{name} has more than one hard link")
    return problems


def load_demo(root: str | Path) -> dict:
    """读取并校验一份已生成的 demo，返回面板用的 config。

    先检查 manifest / config 等入口是 demo 根内的普通文件，再读它们；
    清单合法并不授权根外文件。配置里的路径必须恰好是根内的 jobs.db / skills.yaml。
    """
    root = Path(root).expanduser().resolve()
    if not (root / MANIFEST).exists() and not (root / MANIFEST).is_symlink():
        raise DemoError(f"{root} 不是完整的 demo 目录（缺少 {MANIFEST}）。先运行 generate。")
    problems = check_paths(root)
    if problems:
        raise DemoError(f"demo 数据目录检查失败：{'; '.join(problems)}。没有读取或写入任何文件。")
    if json.loads((root / MANIFEST).read_text(encoding="utf-8")).get("demo_id") != DEMO_ID:
        raise DemoError(f"{root} 不是本 demo（demo_id 不符）")
    config = load_config(root / "config.yaml")
    if (config.get("demo") or {}).get("id") != DEMO_ID:
        raise DemoError("config.yaml 缺少 demo 标识，拒绝启动")
    paths = {"db_path": (config.get("db_path"), "jobs.db"),
             "skills_path": (config.get("skills_path"), "skills.yaml"),
             "report.out_dir": ((config.get("report") or {}).get("out_dir"), None)}
    for name, (value, fixed) in paths.items():
        if not value or not Path(value).is_absolute():
            raise DemoError(f"config.{name} 必须是绝对路径：{value!r}")
        resolved = Path(os.path.realpath(value))
        if root != resolved and root not in resolved.parents:
            raise DemoError(f"config.{name} 指向 demo 根之外：{value}")
        if fixed and str(Path(value)) != str(root / fixed):
            raise DemoError(f"config.{name} 必须恰好是 {root / fixed}：{value}")
    return config


def _demo_handler(base, config: dict, root: Path):
    """demo 专用 handler：每个请求在处理前先复查 demo 根内的读写路径。

    启动时的检查挡不住「启动后才出现的链接」，所以放在请求入口。检查不过就直接回 403，
    不进入面板逻辑：不读、不写任何外部内容。不改全局 Handler。"""

    class DemoHandler(base):
        def _path_ok(self) -> bool:
            problems = check_paths(root)
            if not problems:
                return True
            body = ("demo data check failed: " + "; ".join(problems)
                    + ". Nothing was read or written.").encode()
            self.close_connection = True      # 请求体没读，不能复用这条连接
            self._send(body, 403, "text/plain; charset=utf-8")
            return False

        def do_GET(self):                                   # noqa: N802
            if self._path_ok():
                super().do_GET()

        def do_POST(self):                                  # noqa: N802
            if self._path_ok():
                super().do_POST()

    DemoHandler.config = config
    return DemoHandler


def make_server(root: str | Path, host: str | None = None, port: int | None = None):
    """创建（但不运行）demo 面板服务。返回 (httpd, url)。

    只允许回环地址；占用中的端口直接报错，不会去停任何已在运行的服务。
    """
    from .panel import Handler

    config = load_demo(root)
    panel = config.get("panel") or {}
    host = str(panel.get("host") or DEFAULT_HOST) if host is None else host   # 空串要被拒绝，不能回退默认
    port = int(panel.get("port") or DEFAULT_PORT) if port is None else int(port)
    if host not in LOOPBACK_HOSTS:
        raise DemoError(f"demo 只允许监听回环地址 {LOOPBACK_HOSTS}，收到 {host!r}")
    if port == PRODUCTION_PORT:
        raise DemoError(f"端口 {PRODUCTION_PORT} 留给生产面板，demo 不占用；请用其它端口")
    root = Path(root).expanduser().resolve()
    # 绑定在子类上，不改全局 Handler.config，避免影响同进程里的其它面板用法
    handler = _demo_handler(Handler, config, root)
    try:
        httpd = ThreadingHTTPServer((host, port), handler)
    except OSError as exc:
        raise DemoError(f"无法监听 {host}:{port}（{exc}）。端口被占用时请换 --port；"
                        "demo 不会去停止已有服务。") from exc
    shown = "localhost" if host == "127.0.0.1" else host
    return httpd, f"http://{shown}:{httpd.server_address[1]}"


def serve(root: str | Path, host: str | None = None, port: int | None = None) -> None:
    httpd, url = make_server(root, host, port)
    print(f"Demo · Synthetic data  →  {url}")
    print(f"  数据目录 {Path(root).expanduser().resolve()}（标注 / 设置只写这里）")
    print("  Ctrl-C 停止")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------

def _print_summary(summary: dict) -> None:
    print(f"已生成合成 demo：{summary['root']}")
    print(f"  岗位 {summary['jobs']} 条 ｜ 去重后 {summary['groups']} 组 ｜ "
          f"被规则排除 {summary['excluded']} 条 ｜ 已标注 {summary['labelled']} 条")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jobs.demo", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, text in (("generate", "只生成 demo 数据，不启动服务"),
                       ("serve", "启动已生成的 demo"),
                       ("run", "一键：没有就生成，然后启动；已有就复用")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--root", default=str(DEFAULT_ROOT), help="demo 数据目录（默认 out/demo）")
        if name in ("generate", "run"):
            p.add_argument("--lang", choices=LANGS, default="en",
                           help="合成标注（备注等）的语言，默认 en；仅在生成时生效")
        if name == "generate":
            p.add_argument("--today", help="把相对日期锚到这一天（YYYY-MM-DD），默认今天")
        else:
            p.add_argument("--host", default=None, help=f"回环地址，默认 {DEFAULT_HOST}")
            p.add_argument("--port", type=int, default=None, help=f"默认 {DEFAULT_PORT}")
        if name == "run":
            p.add_argument("--today", help="首次生成时把相对日期锚到这一天")
    args = parser.parse_args(argv)

    try:
        today = dt.date.fromisoformat(args.today) if getattr(args, "today", None) else None
        root = Path(args.root).expanduser().resolve()
        if args.cmd == "generate":
            _print_summary(generate(root, today, args.lang))
            return 0
        if args.cmd == "run":
            if root.exists():
                print(f"复用已有 demo（不覆盖）：{root}")
            else:
                _print_summary(generate(root, today, args.lang))
        serve(root, args.host, args.port)
        return 0
    except DemoError as exc:
        print(f"[demo] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
