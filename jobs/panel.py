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
import sqlite3
import os
import time
from http.server import BaseHTTPRequestHandler
from .http_server import ThreadingHTTPServer
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
    VIEW_FILTER_KEYS, INELIGIBLE_LAST_KEY, INELIGIBLE_LAST_VIEWS, parse_filters, passes_filters, _date_key, age_days, is_stale,
    age_tag, _ordered, _label_time, _unlabelled_at, _search_text,
    _search, _resolve_since, _query, _link, skill_tree,
)
from .panel_views import (  # noqa: F401
    _e, _btn, _tag_html, _label_state, _card, _detail_html, STALE_AFTER_DAYS,
    _last_run_note, _skill_picker, _tier_select, _filter_bar, _search_explanation,
    SETTINGS_SECTIONS, STAT_KEYS, filter_layout, render_settings, render_page,
)

FORBIDDEN_HOSTS = {"0.0.0.0", "::", ""}
EDIT_MAX_BYTES = 64 * 1024  # Complete JSON body; allow notes larger than update requests.
# 100 items x (512-char uid + fields) stays well below this.
LABEL_BATCH_MAX_BYTES = 128 * 1024
LABEL_BATCH_PATH = "/api/labels/batch"


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
        qs = parse_qs(parsed.query, keep_blank_values=True)
        lang, theme = self._prefs()
        if parsed.path == "/api/config-settings":
            self._config_settings(None, started)
            return
        if parsed.path in ("/api/update-config", "/api/update-state"):
            manager = getattr(self.server, "update_manager", None)
            reason = "demo_disabled" if "demo" in self.config else "config_unavailable"
            if parsed.path == "/api/update-config":
                result = manager.preview() if manager else {"ok": False, "error": reason}
            else:
                result = manager.status() if manager else {"state": "disabled", "error": reason}
            self._json(result, 200, started)
            return
        if parsed.path == "/api/stats":
            self._stats(started)
            return
        conn = database.connect(_db_path(self.config))
        try:
            if parsed.path == "/api/job":
                if "location_id" in qs or "mode" in qs:
                    raise ValueError("Unsupported location scope")
                uid = (qs.get("uid") or [""])[0]
                row = _find(conn, self.config, uid, filters=parse_filters(qs))
                if row is None:
                    self._send(f"uid={uid} not found".encode(), 404, "text/plain; charset=utf-8", started)
                    return
                payload = json.dumps({"html": _detail_html(row, lang, i18n.resolve_keys(self.headers.get("Cookie")))},
                                     ensure_ascii=False)
                self._send(payload.encode("utf-8"), 200,
                           "application/json; charset=utf-8", started)
                return

            if parsed.path == "/api/skills":
                self._json(skill_tree(conn, self.config.get("skills_path")), 200, started)
                return

            if parsed.path == "/settings":
                section = (qs.get("section") or [SETTINGS_SECTIONS[0]])[0]
                page = render_settings(conn, self.config, lang, theme, section,
                                       i18n.lang_pref(self.headers.get("Cookie")),
                                       i18n.resolve_keys(self.headers.get("Cookie")))
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
            try:
                filters = parse_filters(qs)
            except ValueError:
                # Keep an invalid region editable in the HTML panel. The view
                # renders guidance and no matches; JSON readers stay strict.
                raw_geo = (qs.get("geo") or [""])[0].strip()[:120]
                if not raw_geo:
                    raise
                filters = parse_filters({k: v for k, v in qs.items() if k != "geo"})
                filters["geo"] = raw_geo
            if view == "recommend":
                filters.pop("geo", None)
                filters.pop("mode", None)
            if view not in INELIGIBLE_LAST_VIEWS:
                filters.pop(INELIGIBLE_LAST_KEY, None)
            page = render_page(conn, self.config, view, offset, limit, started, filters,
                               lang, theme, i18n.lang_pref(self.headers.get("Cookie")))
            self._send(page.encode("utf-8"), 200, "text/html; charset=utf-8", started)
        except ValueError as exc:
            self._send(str(exc).encode(), 400, "text/plain; charset=utf-8", started)
        finally:
            conn.close()

    def _json(self, obj, status: int = 200, started: float | None = None):
        self._send(json.dumps(obj, ensure_ascii=False).encode("utf-8"), status,
                   "application/json; charset=utf-8", started)

    def do_POST(self):                                  # noqa: N802
        started = time.perf_counter()
        if self.path == "/api/config-settings":
            from .config_edit import MAX_BYTES
            payload = self._local_json(started, MAX_BYTES)
            if payload is not None:
                self._config_settings(payload, started)
            return
        if self.path == "/api/update":
            self._post_update(started)
            return
        if self.path == LABEL_BATCH_PATH:
            self._post_labels_batch(started)
            return
        if self.path not in ("/api/label", "/api/skills"):
            self._send(b"not found", 404, "text/plain", started)
            return
        payload = self._local_json(started, EDIT_MAX_BYTES)
        if payload is None:
            return
        if self.path == "/api/skills":
            if (not isinstance(payload.get("token"), str)
                    or not isinstance(payload.get("action"), str)
                    or not isinstance(payload.get("target"), (str, type(None)))):
                self._json({"error": "invalid_request"}, 400, started)
                return
            self._post_skills(payload, started)
            return
        uid, field, value = payload.get("uid"), payload.get("field"), payload.get("value")
        valid_value = (isinstance(value, (bool, int, type(None))) if field == "priority"
                       else isinstance(value, (str, type(None))))
        if (not isinstance(uid, str) or not uid or field not in labels_mod.WRITABLE
                or not valid_value):
            self._json({"error": "invalid_request"}, 400, started)
            return
        if payload.get("location_id"):
            self._json({"error": "invalid_request"}, 400, started)
            return
        conn = None
        try:
            conn = database.connect(_db_path(self.config))
            conn.execute("BEGIN IMMEDIATE")
            row = _find(conn, self.config, uid)
            if row is None:
                row = {"uid": uid, "_label": labels_mod.get_labels(conn).get(uid, {})}
            old_label = row.get("_label") or {}
            q = labels_mod.qualification(row, old_label)
            if field == "eligibility" and value == q["analysis"]:
                # A machine-selected default is a read, not a human assertion.
                if not q["corrected"]:
                    conn.rollback()
                    self._json({"ok": True, "field": field, "value": None,
                                "label": old_label, "qualification": q}, 200, started)
                    return
                value = None
            labels_mod.set_label(conn, uid, source="panel", **{field: value})
            # 把**落库后**的值回传：note 会被规范化（补尾随「；」），
            # 前端要用它回填输入框，否则界面上和刷新后的不一致
            row = _find(conn, self.config, uid) or {"_label": labels_mod.get_labels(conn).get(uid, {})}
            stored = row.get("_label") or {}
            self._json({"ok": True, "field": field, "value": stored.get(field),
                        "label": stored,
                        "qualification": labels_mod.qualification(row, stored)}, 200, started)
        except ValueError as exc:
            self._send(str(exc).encode(), 400, "text/plain", started)
        except sqlite3.Error:
            self._json({"error": "write_failed"}, 503, started)
        finally:
            if conn is not None:
                conn.close()

    def _same_origin(self) -> bool:
        """Host (and Origin, when sent) must name this listener. A malformed
        port raises ValueError; callers treat that as a rejection."""
        request_host = urlparse("//" + self.headers.get("Host", ""))
        bound_host, bound_port = self.server.server_address[:2]
        allowed_hosts = {str(bound_host).lower()}
        configured_host = str((self.config.get("panel") or {}).get("host") or "").strip().lower()
        if configured_host:
            allowed_hosts.add(configured_host)
        if bound_host in ("127.0.0.1", "::1"):
            allowed_hosts.add("localhost")
        if os.environ.get("JOBS_CONTAINER") == "1":
            allowed_hosts.update(("localhost", "127.0.0.1", "::1"))
        host_ok = (request_host.hostname in allowed_hosts
                   and (request_host.port or 80) == bound_port
                   and not request_host.username and not request_host.password)
        origin = self.headers.get("Origin")
        if origin is not None:
            parsed = urlparse(origin)
            host_ok = host_ok and (parsed.scheme == "http"
                                  and parsed.netloc == request_host.netloc
                                  and not parsed.path and not parsed.query
                                  and not parsed.fragment)
        return host_ok

    def _stats(self, started: float):
        """Read-only header counters, re-fetched after a label/note write.
        Same counting as the rendered page (labels.summary); no write path."""
        try:
            host_ok = self._same_origin()
        except ValueError:
            host_ok = False
        if not host_ok:
            self.close_connection = True
            self._json({"error": "origin_rejected"}, 403, started)
            return
        conn = database.connect(_db_path(self.config))
        try:
            summary = labels_mod.summary(conn)
        finally:
            conn.close()
        self._json({key: summary[name] for key, name in STAT_KEYS.items()}, 200, started)

    def _local_json(self, started: float, max_bytes=4096, strict: bool = False):
        """Validate local-origin JSON writes before reaching any mutable state.

        `strict` additionally rejects duplicate object keys and NaN/Infinity,
        which the lenient default silently accepts."""
        try:
            host_ok = self._same_origin()
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            host_ok, length = False, 0
        if not host_ok:
            self.close_connection = True
            self._json({"error": "origin_rejected"}, 403, started)
            return
        if self.headers.get_content_type() != "application/json" or not 0 < length <= max_bytes:
            self.close_connection = True
            self._json({"error": "invalid_request"}, 400, started)
            return
        try:
            raw = self.rfile.read(length).decode("utf-8")
            payload = json.loads(raw, object_pairs_hook=_unique_keys,
                                 parse_constant=_no_constant) if strict else json.loads(raw)
        except (UnicodeDecodeError, ValueError, RecursionError):
            self._json({"error": "invalid_request"}, 400, started)
            return
        if not isinstance(payload, dict):
            self._json({"error": "invalid_request"}, 400, started)
            return None
        return payload

    def _post_labels_batch(self, started: float):
        """Panel multi-select: absolute action/priority for several cards, all or nothing.

        Same-origin and body checks are the panel's own (`_local_json`); this does
        not depend on `agent_api.allow_label_writes`, which only gates the token
        API. Validation and the single transaction are `labels.parse_progress_items`
        / `labels.set_progress_batch` unchanged; nothing is looped per item here.
        """
        payload = self._local_json(started, LABEL_BATCH_MAX_BYTES, strict=True)
        if payload is None:
            return
        if set(payload) != {"items"}:
            self._json({"error": "invalid_request"}, 400, started)
            return
        conn = None
        try:
            items = labels_mod.parse_progress_items(payload["items"])  # before any DB access
            conn = database.connect(_db_path(self.config))
            results = labels_mod.set_progress_batch(conn, items, source="panel")
        except labels_mod.BatchLabelError as exc:
            body = {"error": exc.code}
            if exc.index is not None:
                body["index"] = exc.index
            self._json(body, 409 if exc.code == "label_not_effective" else 400, started)
            return
        except ValueError:
            self._json({"error": "invalid_request"}, 400, started)
            return
        except (sqlite3.Error, OSError):
            self._json({"error": "write_failed"}, 503, started)
            return
        finally:
            if conn is not None:
                conn.close()
        updated = sum(1 for result in results if result["changed"])
        self._json({"ok": True, "updated": updated, "unchanged": len(results) - updated,
                    "items": results}, 200, started)

    def _config_settings(self, payload, started):
        from .config_edit import ConfigEditError
        editor = getattr(self.server, "config_editor", None)
        if editor is None:
            self._json({"ok": False, "error": "config_unavailable"}, 503, started)
            return
        try:
            self._json(editor.read() if payload is None else editor.save(payload), 200, started)
        except ConfigEditError as exc:
            status = 409 if exc.code in ("conflict", "run_busy") else (503 if exc.code in (
                "config_unavailable", "write_failed", "demo_disabled") else 400)
            result = {"ok": False, "error": exc.code}
            if exc.field:
                result["field"] = exc.field
            self._json(result, status, started)

    def _post_update(self, started: float):
        payload = self._local_json(started)
        if payload is None:
            return
        if payload != {}:
            self._json({"error": "invalid_request"}, 400, started)
            return
        manager = getattr(self.server, "update_manager", None)
        if manager is None:
            self._json({"state": "disabled", "error": "demo_disabled" if "demo" in self.config
                        else "config_unavailable", "accepted": False}, 503, started)
            return
        result = manager.start()
        status = 202 if result.get("accepted") else (
            409 if result.get("state") in ("busy", "running") else 400)
        self._json(result, status, started)

    def _post_skills(self, payload: dict, started: float):
        """技能词表编辑，只把差异写进本地补充层。

        只写 skills.local.yaml（补充层），**不碰**手写的 skills.yaml。
        config 里没有 skills_path 时用仓库默认词表；独立 demo 会显式给出自己的路径
        （补充层固定在它旁边），见 jobs/demo.py。
        写完立刻重算 job_skills，所以页面刷新后 tag 和筛选项都是新的。
        """
        conn = database.connect(_db_path(self.config))
        try:
            skills_path = self.config.get("skills_path")
            try:
                info = skills_mod.edit_and_sync(conn, skills_path, payload.get("token"),
                                                payload.get("action"), payload.get("target"))
            except ValueError as exc:
                self._json({"error": str(exc)}, 400, started)
                return
            except (OSError, sqlite3.Error):
                self._json({"error": "write_failed"}, 503, started)
                return
            result = {key: value for key, value in info.items() if key != "counts"}
            self._json({"ok": True, **result}, 200, started)
        finally:
            conn.close()


def _unique_keys(pairs):
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("duplicate key")
    return value


def _no_constant(_name):
    raise ValueError("non-finite number")


def _find(conn, config, uid: str, filters=None):
    if not uid:
        return None
    from .location_query import find
    return find(conn, config, uid, filters)


def _db_path(config: dict):
    from .config import db_path
    return db_path(config)


def serve(config: dict, config_path=None, rules_path=None, skills_path=None) -> None:
    from pathlib import Path
    from .config import ROOT

    config["rules_path"] = str(Path(rules_path).resolve() if rules_path is not None
                               else ROOT / "rules.yaml")
    panel = config.get("panel") or {}
    host = str(panel.get("host", "")).strip()
    port = int(panel.get("port", 8090))

    if host in FORBIDDEN_HOSTS and os.environ.get("JOBS_CONTAINER") != "1":
        raise SystemExit(
            f"拒绝启动：panel.host={host!r} 是通配地址，会监听所有网卡。\n"
            "请在配置里指定明确的地址（例如回环地址 127.0.0.1）；"
            "如需远程访问，请自行配置受保护的访问层。")

    from .panel_update import UpdateManager
    from .config_edit import ConfigEditor

    # Each listener owns its configuration and runner; demo/test handlers do not
    # inherit a runner from another listener in the same interpreter.
    handler = type("PanelHandler", (Handler,), {"config": config})
    httpd = ThreadingHTTPServer((host, port), handler)
    mcp = None
    try:
        from .mcp_http import start
        mcp = start(config, httpd)
        httpd.config_editor = ConfigEditor(config, config_path, rules_path)
        httpd.update_manager = UpdateManager(config, config_path, rules_path=rules_path,
                                            skills_path=skills_path)
    except BaseException:
        if mcp:
            mcp.close()
        httpd.server_close()
        raise

    # 启动指纹：Python 在启动时就把模块加载进内存，改了代码不重启不会生效。
    # 把 panel.py 的修改时间打出来，「跑的是哪一版」就不用猜了。
    mtime = time.strftime("%Y-%m-%d %H:%M:%S",
                          time.localtime(os.path.getmtime(__file__)))
    print(f"面板已启动： http://{host}:{port}")
    print(f"  panel.py 版本时间戳 {mtime}　·　启动于 "
          f"{time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("  Ctrl-C 停止")
    if mcp:
        mode = "MCP（含标注写入）" if mcp.api.api.allow_label_writes else "只读 MCP"
        print(f"  {mode}：{mcp.url}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()
        if mcp:
            mcp.close()
