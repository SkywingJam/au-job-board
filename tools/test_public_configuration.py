"""Public example configuration boundary checks (offline, no new dependencies).

These tests exercise the real consumers of config.example.yaml:

  1. jobs.config.load_config parses it and the values match what the consumers
     expect, with public relative DB/report paths.
  2. jobs.sources.iter_sources yields seek + indeed and not linkedin, honours
     turning linkedin on and each source off, and never calls a fetch function.
  3. jobs.panel.serve binds 127.0.0.1 with a stubbed server (no real listener),
     and the existing forbidden-host policy still rejects 0.0.0.0.
  4. the read-only eligibility-profile CLI with --config pointing at the example
     returns parseable JSON with filtering_mode=legacy, and neither the
     in-process call nor a guarded subprocess opens a DB or the network.

The network, real port listeners and the production database are treated as
failures: an offline guard blocks database.connect, socket.socket,
socket.create_connection and sqlite3.connect, and the subprocess runs with a
sitecustomize that installs the same guard. Only the example file is read from
disk; everything else uses memory or TemporaryDirectory.

Usage:
    PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tools/test_public_configuration.py
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import io
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jobs import cli                                    # noqa: E402
from jobs import db as database                         # noqa: E402
from jobs import panel                                  # noqa: E402
from jobs.config import (db_path, get_eligibility, load_config,  # noqa: E402
                         resolve_filtering)
from jobs.sources import iter_sources                   # noqa: E402
import jobs.sources.jobspy_source as jobspy_source      # noqa: E402
import jobs.sources.seek as seek_source                 # noqa: E402

EXAMPLE = ROOT / "config.example.yaml"
ALL_SOURCES = ("seek", "indeed", "linkedin")

failures: list[str] = []
checks = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{('  ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


@contextlib.contextmanager
def offline_guard():
    """Make any network or database access fail loudly inside the block."""
    originals = {
        "connect": database.connect,
        "socket": socket.socket,
        "create_connection": socket.create_connection,
        "sqlite3_connect": sqlite3.connect,
    }

    def blocked(*_args, **_kwargs):
        raise AssertionError("offline guard: network/DB access is not allowed here")

    database.connect = blocked
    socket.socket = blocked
    socket.create_connection = blocked
    sqlite3.connect = blocked
    try:
        yield
    finally:
        database.connect = originals["connect"]
        socket.socket = originals["socket"]
        socket.create_connection = originals["create_connection"]
        sqlite3.connect = originals["sqlite3_connect"]


def source_names(config: dict) -> list:
    return [name for name, _ in iter_sources(config)]


def is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def test_example_schema() -> None:
    print("== 1. example parses and matches consumer schema ==")
    check("config.example.yaml exists", EXAMPLE.is_file(), str(EXAMPLE))
    with offline_guard():
        cfg = load_config(EXAMPLE)
        check("root is a mapping", isinstance(cfg, dict))

        check("db_path is the public relative path",
              cfg.get("db_path") == "data/jobs.db", repr(cfg.get("db_path")))
        check("db_path(config) resolves under the project root",
              db_path(cfg) == ROOT / "data" / "jobs.db", str(db_path(cfg)))

        queries = cfg.get("queries")
        check("queries is a non-empty list of non-empty strings",
              isinstance(queries, list) and len(queries) > 0
              and all(isinstance(q, str) and q.strip() for q in queries))

        seek = cfg.get("seek") or {}
        check("seek is a mapping", isinstance(cfg.get("seek"), dict))
        check("seek.enabled is true", seek.get("enabled") is True)
        check("seek.locations is a non-empty list of strings",
              isinstance(seek.get("locations"), list) and seek["locations"]
              and all(isinstance(x, str) and x.strip() for x in seek["locations"]))
        check("seek.pages_per_query is a positive int",
              is_int(seek.get("pages_per_query")) and seek["pages_per_query"] > 0)
        check("seek.daterange is a positive int",
              is_int(seek.get("daterange")) and seek["daterange"] > 0)
        check("seek.detail_sleep is a positive number",
              is_number(seek.get("detail_sleep")) and seek["detail_sleep"] > 0)

        jobspy = cfg.get("jobspy") or {}
        indeed = jobspy.get("indeed") or {}
        linkedin = jobspy.get("linkedin") or {}
        check("jobspy.indeed.enabled is true", indeed.get("enabled") is True)
        check("jobspy.indeed.location is a non-empty string",
              isinstance(indeed.get("location"), str) and bool(indeed["location"].strip()))
        check("jobspy.indeed.country_indeed is australia",
              indeed.get("country_indeed") == "australia")
        check("jobspy.indeed.results_wanted is a positive int",
              is_int(indeed.get("results_wanted")) and indeed["results_wanted"] > 0)
        check("jobspy.linkedin.enabled is false", linkedin.get("enabled") is False)
        check("no invalid top-level linkedin key", "linkedin" not in cfg,
              str(sorted(cfg)))
        check("jobspy.linkedin.results_wanted is a positive int",
              is_int(linkedin.get("results_wanted")) and linkedin["results_wanted"] > 0)
        check("jobspy.linkedin.fetch_description is a bool",
              isinstance(linkedin.get("fetch_description"), bool))

        report = cfg.get("report") or {}
        check("report.out_dir is the public relative path",
              report.get("out_dir") == "out", repr(report.get("out_dir")))
        check("report.top_n is a positive int",
              is_int(report.get("top_n")) and report["top_n"] > 0)

        panel_cfg = cfg.get("panel") or {}
        check("panel.host is 127.0.0.1", panel_cfg.get("host") == "127.0.0.1",
              repr(panel_cfg.get("host")))
        check("panel.port is 8090", panel_cfg.get("port") == 8090)
        check("panel.page_size is a positive int",
              is_int(panel_cfg.get("page_size")) and panel_cfg["page_size"] > 0)
        recommend = panel_cfg.get("recommend") or {}
        check("panel.recommend.low_ratio is a number in [0, 1]",
              is_number(recommend.get("low_ratio"))
              and 0 <= recommend["low_ratio"] <= 1)
        check("panel.recommend.low_max_score is an int",
              is_int(recommend.get("low_max_score")))

        eligibility = cfg.get("eligibility")
        check("eligibility is a mapping with a documented preset",
              isinstance(eligibility, dict)
              and eligibility.get("profile") in
              {"citizen", "permanent_resident", "485", "student_visa"})
        result = get_eligibility(cfg)
        check("get_eligibility parses the example profile",
              result.get("profile") == (eligibility or {}).get("profile"))
        check("filtering_mode stays legacy",
              result.get("filtering_mode") == "legacy")
        check("filtering.mode is explicitly legacy",
              resolve_filtering(cfg) == {"mode": "legacy"},
              repr(cfg.get("filtering")))


def test_sources_switches() -> None:
    print("== 2. iter_sources honours enabled switches and does not fetch ==")
    base = load_config(EXAMPLE)
    snapshot = copy.deepcopy(base)
    original_seek = seek_source.fetch
    original_jobspy = jobspy_source.fetch

    def must_not_fetch(*_args, **_kwargs):
        raise AssertionError("a fetch function was called during a config test")

    try:
        seek_source.fetch = must_not_fetch
        jobspy_source.fetch = must_not_fetch
        with offline_guard():
            check("default source list is seek + indeed",
                  source_names(base) == ["seek", "indeed"], str(source_names(base)))

            enabled = copy.deepcopy(base)
            enabled["jobspy"]["linkedin"]["enabled"] = True
            check("enabling linkedin adds it",
                  source_names(enabled) == ["seek", "indeed", "linkedin"],
                  str(source_names(enabled)))

            disables = {
                "seek": lambda c: c["seek"].__setitem__("enabled", False),
                "indeed": lambda c: c["jobspy"]["indeed"].__setitem__("enabled", False),
                "linkedin": lambda c: c["jobspy"]["linkedin"].__setitem__("enabled", False),
            }
            for name, mutate in disables.items():
                candidate = copy.deepcopy(enabled)
                mutate(candidate)
                expected = [item for item in ALL_SOURCES if item != name]
                check(f"disabling {name} removes only it",
                      source_names(candidate) == expected, str(source_names(candidate)))

            all_off = copy.deepcopy(enabled)
            for mutate in disables.values():
                mutate(all_off)
            check("all sources disabled yields none",
                  source_names(all_off) == [], str(source_names(all_off)))
    finally:
        seek_source.fetch = original_seek
        jobspy_source.fetch = original_jobspy
    check("iter_sources does not mutate the config", base == snapshot)


def test_panel_binding() -> None:
    print("== 3. panel.serve binds loopback and still rejects wildcard hosts ==")
    check("0.0.0.0 remains forbidden", "0.0.0.0" in panel.FORBIDDEN_HOSTS)
    cfg = load_config(EXAMPLE)
    created = []

    class FakeServer:
        def __init__(self, address, handler):
            self.address = address
            self.handler = handler
            self.served = False
            self.closed = False
            created.append(self)

        def serve_forever(self):
            self.served = True

        def server_close(self):
            self.closed = True

    original_server = panel.ThreadingHTTPServer
    original_config = panel.Handler.config
    try:
        panel.ThreadingHTTPServer = FakeServer
        with offline_guard(), contextlib.redirect_stdout(io.StringIO()):
            panel.serve(cfg)
        check("exactly one server was constructed", len(created) == 1, str(len(created)))
        check("panel binds 127.0.0.1:8090",
              bool(created) and created[0].address == ("127.0.0.1", 8090),
              str(created[0].address if created else None))
        check("serve_forever was called", bool(created) and created[0].served)
        check("server_close was called", bool(created) and created[0].closed)

        bad = copy.deepcopy(cfg)
        bad["panel"]["host"] = "0.0.0.0"
        before = len(created)
        raised = False
        try:
            with offline_guard(), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                panel.serve(bad)
        except SystemExit:
            raised = True
        check("wildcard host raises SystemExit", raised)
        check("wildcard host constructs no server", len(created) == before,
              str(len(created)))
    finally:
        panel.ThreadingHTTPServer = original_server
        panel.Handler.config = original_config
    check("Handler.config global state restored",
          panel.Handler.config is original_config)


GUARD_SOURCECODE = '''import socket
import sqlite3


def _blocked(*_args, **_kwargs):
    raise RuntimeError("OFFLINE_GUARD: blocked")


socket.socket = _blocked
socket.create_connection = _blocked
sqlite3.connect = _blocked
'''


def test_eligibility_profile_cli() -> None:
    print("== 4. eligibility-profile with --config <example> is read-only ==")
    buf = io.StringIO()
    with offline_guard(), contextlib.redirect_stdout(buf):
        rc = cli.cmd_eligibility_profile(argparse.Namespace(config=str(EXAMPLE)))
    try:
        parsed = json.loads(buf.getvalue())
    except ValueError:
        parsed = None
    check("in-process exit 0", rc == 0, f"rc={rc}")
    check("in-process stdout is a JSON object", isinstance(parsed, dict))
    check("in-process profile comes from the example",
          bool(parsed) and parsed.get("profile") == "citizen",
          str(parsed.get("profile") if parsed else None))
    check("in-process filtering_mode=legacy",
          bool(parsed) and parsed.get("filtering_mode") == "legacy")

    with tempfile.TemporaryDirectory() as d:
        guard = Path(d) / "sitecustomize.py"
        guard.write_text(GUARD_SOURCECODE, encoding="utf-8")
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONPATH"] = str(Path(d)) + os.pathsep + str(ROOT)
        proc = subprocess.run(
            [sys.executable, "-m", "jobs.cli", "--config", str(EXAMPLE),
             "eligibility-profile"],
            cwd=str(ROOT), env=env, capture_output=True, text=True)
    check("subprocess exit 0", proc.returncode == 0,
          f"rc={proc.returncode} {proc.stderr.strip()[:120]}")
    check("subprocess prints no traceback", "Traceback" not in proc.stderr)
    try:
        sub = json.loads(proc.stdout)
    except ValueError:
        sub = None
    check("subprocess stdout is a JSON object", isinstance(sub, dict))
    check("subprocess profile comes from the example",
          bool(sub) and sub.get("profile") == "citizen",
          str(sub.get("profile") if sub else None))
    check("subprocess filtering_mode=legacy",
          bool(sub) and sub.get("filtering_mode") == "legacy")


def test_guard_and_selfcheck() -> None:
    print("== 5. offline guard fails closed and restores state ==")
    original_socket = socket.socket
    original_connect = database.connect
    original_sqlite = sqlite3.connect
    outcomes = {}
    with offline_guard():
        probes = (
            ("database.connect", lambda: database.connect(":memory:")),
            ("socket.socket", lambda: socket.socket()),
            ("socket.create_connection",
             lambda: socket.create_connection(("127.0.0.1", 9), 0.1)),
            ("sqlite3.connect", lambda: sqlite3.connect(":memory:")),
        )
        for name, call in probes:
            try:
                call()
                outcomes[name] = "not-blocked"
            except AssertionError as exc:
                outcomes[name] = ("blocked" if "offline guard" in str(exc)
                                  else f"other: {exc}")
    for name, outcome in outcomes.items():
        check(f"guard blocks {name}", outcome == "blocked", outcome)
    check("guard restores socket.socket", socket.socket is original_socket)
    check("guard restores database.connect", database.connect is original_connect)
    check("guard restores sqlite3.connect", sqlite3.connect is original_sqlite)

    cfg = load_config(EXAMPLE)
    deliberately_wrong = (cfg.get("db_path") == "data/private.db")
    check("deliberately wrong expectation is detected as false",
          deliberately_wrong is False, str(cfg.get("db_path")))


def test_no_private_values() -> None:
    print("== 6. example contains no private or invalid values ==")
    text = EXAMPLE.read_text(encoding="utf-8")
    check("comments are English only (no CJK)",
          re.search(r"[\u4e00-\u9fff]", text) is None)
    check("no absolute home paths", not re.search(r"/(Users|home|private)/", text))
    check("no LAN or Tailscale literal",
          not re.search(r"(192\.168\.|10\.10\.|100\.\d+\.)", text))
    check("no wildcard bind address", "0.0.0.0" not in text)
    with offline_guard():
        cfg = load_config(EXAMPLE)
    host = (cfg.get("panel") or {}).get("host", "")
    check("panel host is loopback", host.startswith("127."), host)


def test_filtering_mode_gate() -> None:
    print("== 7. explicit profile_v1 is rejected before any database connect ==")
    calls = {"connect": 0}

    def spy_connect(*_args, **_kwargs):
        calls["connect"] += 1
        raise AssertionError("database.connect must not run for a rejected mode")

    original_connect = database.connect
    stderr = io.StringIO()
    with tempfile.TemporaryDirectory() as d:
        cfg_path = Path(d) / "config.yaml"
        cfg_path.write_text(
            "filtering:\n  mode: profile_v1\ndb_path: data/jobs.db\n",
            encoding="utf-8")
        database.connect = spy_connect
        try:
            with contextlib.redirect_stderr(stderr):
                rc = cli.cmd_analyze(argparse.Namespace(
                    config=str(cfg_path), rules=str(ROOT / "rules.yaml"),
                    skills=None))
        finally:
            database.connect = original_connect
    check("explicit profile_v1 exits non-zero", rc != 0, f"rc={rc}")
    check("explicit profile_v1 fails before database.connect",
          calls["connect"] == 0, f"connect calls={calls['connect']}")
    message = stderr.getvalue()
    check("rejection is a safe filtering-mode error",
          "filtering" in message and "profile_v1" not in message,
          message.strip()[:100])


def main() -> int:
    test_example_schema()
    test_sources_switches()
    test_panel_binding()
    test_eligibility_profile_cli()
    test_guard_and_selfcheck()
    test_no_private_values()
    test_filtering_mode_gate()
    print()
    if failures:
        print(f"失败 {len(failures)} 项：")
        for item in failures:
            print(f"  - {item}")
        return 2
    print(f"全部通过（{checks} 项检查）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
