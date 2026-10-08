"""本地网页面板：左侧列表 + 右侧详情（类似 LinkedIn/SEEK 的横屏模式）。

只用标准库（http.server + gzip），不引入 web 框架 —— 本项目的依赖刻意保持在
jobspy + pyyaml 两个。页面不引用任何 CDN，没有外网也能用。

性能取舍：
  - 「装饰」（抽资格句 + 算 tag）只对当前页做，不做全库
  - 正文原文占用大，因此列表页**不带正文**，点哪条才用 /api/job 取那一条的
    正文并按需装饰，让列表 HTML 和服务端响应保持轻量。

安全约束：**拒绝通配绑定地址（如 0.0.0.0），必须显式给出具体的主机地址**，
否则拒绝启动。
"""

from __future__ import annotations

import gzip
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import db as database
from . import i18n
from . import labels as labels_mod
from . import skills as skills_mod
from . import report as report_mod

# 数据口径与 HTML 渲染已拆到两个模块；这里保留原有名字，调用方与测试不用改。
from .panel_data import (  # noqa: F401
    VIEWS, VIEW_ALIASES, FILTER_KEYS, NONE_TOKEN, LABELED_ANY, SORT_KEYS, LABELED_LABELS,
    QUERY_KEY, FRESH_KEY, AGE_WARN_DAYS, PRIORITY_KEY, PRIORITY_TAG, SKILL_KEY, TIER_KEY,
    TIER_LABELS, SKILL_PICK_TOP, SINCE_KEY, ALL_FILTER_KEYS, LABELLED_FILTER_KEYS,
    VIEW_FILTER_KEYS, parse_filters, passes_filters, _date_key, age_days, is_stale,
    age_tag, _ordered, _mix_low_scores, _label_time, _unlabelled_at, _search_text,
    _search, _resolve_since, _query, _link, skill_tree,
)
from .panel_views import (  # noqa: F401
    _e, _btn, _tag_html, _label_state, _card, _detail_html, STALE_AFTER_DAYS,
    _last_run_note, _skill_picker, _tier_select, _filter_bar, _search_explanation,
    SETTINGS_SECTIONS, filter_layout, render_settings, render_page,
)

FORBIDDEN_HOSTS = {"0.0.0.0", "::", ""}


class Handler(BaseHTTPRequestHandler):
    config: dict = {}
    server_version = "jobs-panel"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, body: bytes, status: int = 200, ctype: str = "text/html; charset=utf-8",
              started: float | None = None):
        raw_len = len(body)
        headers = [("Content-Type", ctype), ("Cache-Control", "no-store")]
        if raw_len > 1024 and "gzip" in (self.headers.get("Accept-Encoding") or ""):
            body = gzip.compress(body, 6)
            headers.append(("Content-Encoding", "gzip"))

        if started is not None:
            dur_ms = (time.perf_counter() - started) * 1000
            # 浏览器 devtools 的 Timing 面板会显示这一项，用来区分
            # 「服务端慢」和「网络/浏览器慢」
            headers.append(("Server-Timing", f"render;dur={dur_ms:.0f}"))
            print(f"[panel] {self.command} {self.path} -> {status} "
                  f"{raw_len}B (gzip {len(body)}B) {dur_ms:.0f}ms", flush=True)

        self.send_response(status)
        for key, value in headers:
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _prefs(self) -> tuple[str, str | None]:
        """界面语言与外观：cookie 优先，语言再看 Accept-Language，默认中文 / 跟随系统。"""
        cookie = self.headers.get("Cookie")
        return (i18n.resolve_lang(cookie, self.headers.get("Accept-Language")),
                i18n.resolve_theme(cookie))

    def do_GET(self):                                   # noqa: N802
        started = time.perf_counter()
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        lang, theme = self._prefs()
        conn = database.connect(_db_path(self.config))
        try:
            if parsed.path == "/api/job":
                uid = (qs.get("uid") or [""])[0]
                row = _find(conn, self.config, uid)
                if row is None:
                    self._send(f"uid={uid} not found".encode(), 404, "text/plain; charset=utf-8", started)
                    return
                payload = json.dumps({"html": _detail_html(row, lang)}, ensure_ascii=False)
                self._send(payload.encode("utf-8"), 200,
                           "application/json; charset=utf-8", started)
                return

            if parsed.path == "/api/skills":
                self._json(skill_tree(conn, self.config.get("skills_path")), 200, started)
                return

            if parsed.path == "/settings":
                section = (qs.get("section") or ["skills"])[0]
                page = render_settings(conn, self.config, lang, theme, section,
                                       i18n.lang_pref(self.headers.get("Cookie")))
                self._send(page.encode("utf-8"), 200,
                           "text/html; charset=utf-8", started)
                return

            if parsed.path not in ("/", "/index.html"):
                self._send(b"not found", 404, "text/plain", started)
                return

            view = (qs.get("view") or ["recommend"])[0]
            view = VIEW_ALIASES.get(view, view)      # 旧链接 view=kept 继续能用
            if view not in VIEWS:
                view = "recommend"
            limit = int((self.config.get("panel") or {}).get("page_size", 40))
            try:
                offset = max(0, int((qs.get("offset") or ["0"])[0]))
            except ValueError:
                offset = 0
            filters = parse_filters(qs)
            page = render_page(conn, self.config, view, offset, limit, started, filters,
                               lang, theme)
            self._send(page.encode("utf-8"), 200, "text/html; charset=utf-8", started)
        finally:
            conn.close()

    def _json(self, obj, status: int = 200, started: float | None = None):
        self._send(json.dumps(obj, ensure_ascii=False).encode("utf-8"), status,
                   "application/json; charset=utf-8", started)

    def do_POST(self):                                  # noqa: N802
        started = time.perf_counter()
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except ValueError:
            self._json({"error": "bad json"}, 400, started)
            return
        if self.path == "/api/skills":
            self._post_skills(payload, started)
            return
        if self.path != "/api/label":
            self._send(b"not found", 404, "text/plain", started)
            return
        conn = database.connect(_db_path(self.config))
        try:
            uid, field, value = payload.get("uid"), payload.get("field"), payload.get("value")
            if not uid or field not in labels_mod.WRITABLE:
                self._send("bad request".encode(), 400, "text/plain", started)
                return
            labels_mod.set_label(conn, uid, source="panel", **{field: value})
            # 把**落库后**的值回传：note 会被规范化（补尾随「；」），
            # 前端要用它回填输入框，否则界面上和刷新后的不一致
            stored = labels_mod.get_labels(conn).get(uid, {})
            self._json({"ok": True, "field": field, "value": stored.get(field)}, 200, started)
        except ValueError as exc:
            self._send(str(exc).encode(), 400, "text/plain", started)
        finally:
            conn.close()

    def _post_skills(self, payload: dict, started: float):
        """技能词表的两个写操作：新增技能 / 给已有技能加别名。

        只写 skills.local.yaml（补充层），**不碰**手写的 skills.yaml。
        config 里没有 skills_path 时用仓库默认词表；独立 demo 会显式给出自己的路径
        （补充层固定在它旁边），见 jobs/demo.py。
        写完立刻重算 job_skills，所以页面刷新后 tag 和筛选项都是新的。
        """
        conn = database.connect(_db_path(self.config))
        try:
            skills_path = self.config.get("skills_path")
            local = skills_mod.load_local(skills_path)
            merged = skills_mod.load_skills(skills_path)
            try:
                info = skills_mod.apply_edit(local, merged, payload.get("token"),
                                             payload.get("action"), payload.get("target"))
            except ValueError as exc:
                self._json({"error": str(exc)}, 400, started)
                return
            skills_mod.save_local(local, skills_path)
            fresh = skills_mod.load_skills(skills_path)
            if info.get("kind") == "remove":
                # 删除会波及所有靠旧 token 命中过的岗位（别名尤其如此），
                # 只重抽含该 token 的文档不够，必须全量。
                skills_mod.sync(conn, fresh)
                self._json({"ok": True, **info}, 200, started)
                return
            # add / alias 是纯增量：只重抽含新 token 的文档
            updated = skills_mod.sync_incremental(conn, fresh, [info.get("token")])
            self._json({"ok": True, "docs": updated, **info}, 200, started)
        finally:
            conn.close()


def _find(conn, config, uid: str):
    if not uid:
        return None
    kept, excluded, labels, index = report_mod.load(conn, config)
    for row in kept + excluded:
        if row["uid"] == uid:
            return report_mod.decorate([row], labels, index)[0]
    return None


def _db_path(config: dict):
    from .config import db_path
    return db_path(config)


def serve(config: dict) -> None:
    panel = config.get("panel") or {}
    host = str(panel.get("host", "")).strip()
    port = int(panel.get("port", 8090))

    if host in FORBIDDEN_HOSTS:
        raise SystemExit(
            f"拒绝启动：panel.host={host!r} 是通配地址，会监听所有网卡。\n"
            "请在配置里指定明确的地址（例如回环地址 127.0.0.1）；"
            "如需远程访问，请自行配置受保护的访问层。")

    Handler.config = config
    httpd = ThreadingHTTPServer((host, port), Handler)

    # 启动指纹：Python 在启动时就把模块加载进内存，改了代码不重启不会生效。
    # 把 panel.py 的修改时间打出来，「跑的是哪一版」就不用猜了。
    import os
    mtime = time.strftime("%Y-%m-%d %H:%M:%S",
                          time.localtime(os.path.getmtime(__file__)))
    print(f"面板已启动： http://{host}:{port}")
    print(f"  panel.py 版本时间戳 {mtime}　·　启动于 "
          f"{time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("  Ctrl-C 停止")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()
