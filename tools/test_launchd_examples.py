"""Offline validation for the generic launchd service examples.

The tests read only the two plist templates and the documentation, render them
with synthetic absolute paths inside TemporaryDirectory, and exercise the real
jobs.cli argument parser with cmd_panel / cmd_run replaced by capturing stubs.
They never bootstrap, load, kickstart or inspect a real service, never open a
listener, never touch the network or a database, and never read the production
configuration, vocabulary, corpus or exports. plutil -lint is run on the
templates only.

Usage:
    PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tools/test_launchd_examples.py
"""

from __future__ import annotations

import builtins
import contextlib
import io
import os
import plistlib
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jobs import cli                                  # noqa: E402
from jobs import db as database                       # noqa: E402

REPO_LAUNCHD = ROOT / "launchd"
PANEL_EXAMPLE = REPO_LAUNCHD / "panel.plist.example"
FETCH_EXAMPLE = REPO_LAUNCHD / "fetch.plist.example"
DOC = ROOT / "docs" / "launchd-examples.md"

PANEL_LABEL = "local.au-job-pipeline.panel"
FETCH_LABEL = "local.au-job-pipeline.fetch"
PLACEHOLDERS = (
    "__PROJECT_DIR__", "__PYTHON_PATH__", "__CONFIG_PATH__", "__LOG_DIR__",
)
_PLACEHOLDER_RE = re.compile(r"__[A-Z0-9_]+__")
_PRIVATE_RES = (
    re.compile(r"\bcom\.[a-z0-9][a-z0-9-]*\.", re.I),
    re.compile(r"(?:192[.]168|10[.][0-9]+[.][0-9]+|172[.]1[6-9])"),
    re.compile(r"100[.](?:6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])[.]"),
    re.compile(r"/Users/"),
    re.compile(r"[.]memory"),
    re.compile("tailscale", re.I),
)

failures: list = []
checks = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{('  ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


def strings_in(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from strings_in(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings_in(item)


def placeholders_in(value) -> set:
    found = set()
    for text in strings_in(value):
        found.update(_PLACEHOLDER_RE.findall(text))
    return found


def extract_doc_code() -> str:
    text = DOC.read_text(encoding="utf-8")
    fence = chr(96) * 3
    nl = chr(10)
    pattern = re.escape(fence) + "python" + nl + "(.*?)" + nl + re.escape(fence)
    match = re.search(pattern, text, re.S)
    if not match:
        raise AssertionError("docs/launchd-examples.md has no python block")
    return match.group(1)


def load_doc_renderer() -> dict:
    namespace = {"__name__": "doc_renderer"}
    exec(compile(extract_doc_code(), str(DOC), "exec"), namespace)
    return namespace


@contextlib.contextmanager
def offline_guard():
    real_run = subprocess.run
    originals = {
        "connect": database.connect,
        "sqlite": sqlite3.connect,
        "socket": socket.socket,
        "create_connection": socket.create_connection,
    }

    def blocked(*_args, **_kwargs):
        raise AssertionError("offline guard: network/DB access is not allowed")

    def guarded_run(cmd, *args, **kwargs):
        argv = list(cmd) if isinstance(cmd, (list, tuple)) else [str(cmd)]
        if os.path.basename(str(argv[0])) == "launchctl":
            raise AssertionError("offline guard: launchd management is not allowed")
        return real_run(cmd, *args, **kwargs)

    database.connect = blocked
    sqlite3.connect = blocked
    socket.socket = blocked
    socket.create_connection = blocked
    subprocess.run = guarded_run
    try:
        yield
    finally:
        database.connect = originals["connect"]
        sqlite3.connect = originals["sqlite"]
        socket.socket = originals["socket"]
        socket.create_connection = originals["create_connection"]
        subprocess.run = real_run


def _run_doc_script(root: Path, template_dir, output_dir, mapping):
    script = root / "render_doc.py"
    script.write_text(extract_doc_code(), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(script),
         "--template-dir", str(template_dir),
         "--output-dir", str(output_dir),
         "--project-dir", mapping["__PROJECT_DIR__"],
         "--python-path", mapping["__PYTHON_PATH__"],
         "--config-path", mapping["__CONFIG_PATH__"],
         "--log-dir", mapping["__LOG_DIR__"]],
        capture_output=True, text=True)


def make_env(root: Path):
    project = root / "project & space"
    log_dir = root / "log & space"
    config = root / "conf & ig.yaml"
    project.mkdir()
    log_dir.mkdir()
    config.write_text("db_path: data/jobs.db", encoding="utf-8")
    mapping = {
        "__PROJECT_DIR__": str(project),
        "__PYTHON_PATH__": sys.executable,
        "__CONFIG_PATH__": str(config),
        "__LOG_DIR__": str(log_dir),
    }
    return mapping, project, log_dir, config


def test_parse_and_private_scan() -> None:
    print("== 1. parse, field types, placeholders, private scan, plutil ==")
    panel = plistlib.loads(PANEL_EXAMPLE.read_bytes())
    fetch = plistlib.loads(FETCH_EXAMPLE.read_bytes())
    check("panel parses to a dict", isinstance(panel, dict))
    check("fetch parses to a dict", isinstance(fetch, dict))
    for name, data in (("panel", panel), ("fetch", fetch)):
        check(f"{name} ProgramArguments is a list of strings",
              isinstance(data.get("ProgramArguments"), list)
              and bool(data["ProgramArguments"])
              and all(isinstance(item, str) for item in data["ProgramArguments"]))
        check(f"{name} WorkingDirectory is a string",
              isinstance(data.get("WorkingDirectory"), str))
        check(f"{name} stdout/stderr are strings",
              isinstance(data.get("StandardOutPath"), str)
              and isinstance(data.get("StandardErrorPath"), str))
        check(f"{name} has exactly the four known placeholders",
              placeholders_in(data) == set(PLACEHOLDERS),
              str(sorted(placeholders_in(data))))
    check("panel label is the generic public label",
          panel.get("Label") == PANEL_LABEL, str(panel.get("Label")))
    check("fetch label is the generic public label",
          fetch.get("Label") == FETCH_LABEL, str(fetch.get("Label")))
    raw = (PANEL_EXAMPLE.read_text(encoding="utf-8")
           + chr(10) + FETCH_EXAMPLE.read_text(encoding="utf-8"))
    for pattern in _PRIVATE_RES:
        check(f"templates contain no private marker /{pattern.pattern}/",
              pattern.search(raw) is None)
    with offline_guard():
        lint = subprocess.run(
            ["plutil", "-lint", "--", str(PANEL_EXAMPLE), str(FETCH_EXAMPLE)],
            capture_output=True, text=True)
    check("plutil -lint accepts both examples", lint.returncode == 0,
          (lint.stdout + lint.stderr).strip()[:80])
    doc_text = DOC.read_text(encoding="utf-8")
    for pattern in _PRIVATE_RES:
        check(f"documentation contains no private marker /{pattern.pattern}/",
              pattern.search(doc_text) is None)


def test_render_contract() -> None:
    print("== 2. documented renderer and synthetic path round-trip ==")
    namespace = load_doc_renderer()
    render = namespace["render_templates"]
    check("documented KNOWN_PLACEHOLDERS match the templates",
          tuple(namespace["KNOWN_PLACEHOLDERS"]) == tuple(PLACEHOLDERS))
    check("documented TEMPLATE_NAMES are the two fixed templates",
          tuple(namespace["TEMPLATE_NAMES"])
          == ("panel.plist.example", "fetch.plist.example"),
          str(namespace.get("TEMPLATE_NAMES")))
    with tempfile.TemporaryDirectory() as d, offline_guard():
        root = Path(d)
        mapping, project, log_dir, config = make_env(root)
        out = root / "rendered & agents"
        written = render(REPO_LAUNCHD, out, mapping)
        check("renderer writes two plists",
              sorted(path.name for path in written) == ["fetch.plist", "panel.plist"],
              str([path.name for path in written]))
        check("output directory is created", out.is_dir())
        panel = plistlib.loads((out / "panel.plist").read_bytes())
        fetch = plistlib.loads((out / "fetch.plist").read_bytes())
        check("panel argv round-trips exactly",
              panel["ProgramArguments"] == [
                  mapping["__PYTHON_PATH__"], "-u", "-m", "jobs.cli",
                  "--config", mapping["__CONFIG_PATH__"],
                  "panel", "--host", "127.0.0.1", "--port", "8090"],
              str(panel["ProgramArguments"]))
        check("fetch argv round-trips exactly",
              fetch["ProgramArguments"] == [
                  mapping["__PYTHON_PATH__"], "-u", "-m", "jobs.cli",
                  "--config", mapping["__CONFIG_PATH__"], "run"],
              str(fetch["ProgramArguments"]))
        check("WorkingDirectory round-trips",
              panel["WorkingDirectory"] == mapping["__PROJECT_DIR__"]
              and fetch["WorkingDirectory"] == mapping["__PROJECT_DIR__"])
        check("panel log paths round-trip",
              panel["StandardOutPath"] == str(log_dir / "panel.log")
              and panel["StandardErrorPath"] == str(log_dir / "panel.err"))
        check("fetch log paths round-trip",
              fetch["StandardOutPath"] == str(log_dir / "fetch.log")
              and fetch["StandardErrorPath"] == str(log_dir / "fetch.err"))
        check("no placeholder remains after rendering",
              placeholders_in(panel) == set() and placeholders_in(fetch) == set())
        raw_panel = (out / "panel.plist").read_bytes()
        check("XML special characters are escaped",
              b"&amp;" in raw_panel and b"project & space" not in raw_panel)
        try:
            render(REPO_LAUNCHD, out, mapping)
            outcome = "no-error"
        except FileExistsError:
            outcome = "error"
        check("an existing output directory is refused", outcome == "error", outcome)

        script = root / "render_doc.py"
        script.write_text(extract_doc_code(), encoding="utf-8")
        args = ["--template-dir", str(REPO_LAUNCHD),
                "--project-dir", mapping["__PROJECT_DIR__"],
                "--python-path", mapping["__PYTHON_PATH__"],
                "--config-path", mapping["__CONFIG_PATH__"],
                "--log-dir", mapping["__LOG_DIR__"]]
        out2 = root / "doc script out"
        proc = subprocess.run([sys.executable, str(script), "--output-dir", str(out2), *args],
                              capture_output=True, text=True)
        check("documented script runs end to end", proc.returncode == 0,
              (proc.stderr or proc.stdout).strip()[:80])
        check("documented script produced both files",
              (out2 / "panel.plist").is_file() and (out2 / "fetch.plist").is_file())
        again = subprocess.run([sys.executable, str(script), "--output-dir", str(out2), *args],
                               capture_output=True, text=True)
        check("documented script refuses to overwrite", again.returncode != 0)


def test_cli_dispatch() -> None:
    print("== 3. rendered argv dispatches through the real jobs.cli ==")
    namespace = load_doc_renderer()
    with tempfile.TemporaryDirectory() as d, offline_guard():
        root = Path(d)
        mapping, project, log_dir, config = make_env(root)
        out = root / "rendered"
        namespace["render_templates"](REPO_LAUNCHD, out, mapping)
        panel = plistlib.loads((out / "panel.plist").read_bytes())
        fetch = plistlib.loads((out / "fetch.plist").read_bytes())

        captured: dict = {}
        original_panel = cli.cmd_panel
        original_run = cli.cmd_run

        def stub_panel(args):
            captured["func"] = "panel"
            captured["args"] = args
            return 0

        def stub_run(args):
            captured["func"] = "run"
            captured["args"] = args
            return 0

        opened: list = []
        real_builtin = builtins.open
        real_io = io.open

        def spy_open(file, *args, **kwargs):
            try:
                opened.append(os.fspath(file))
            except TypeError:
                opened.append(str(file))
            return real_io(file, *args, **kwargs)

        builtins.open = spy_open
        io.open = spy_open
        cli.cmd_panel = stub_panel
        cli.cmd_run = stub_run
        try:
            rc_panel = cli.main(panel["ProgramArguments"][4:])
            panel_func = captured.get("func")
            panel_args = captured.get("args")
            captured.clear()
            rc_run = cli.main(fetch["ProgramArguments"][4:])
            run_func = captured.get("func")
            run_args = captured.get("args")
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    cli.main(["panel", "--config", mapping["__CONFIG_PATH__"]])
                reversed_outcome = "no-error"
            except SystemExit:
                reversed_outcome = "error"
        finally:
            cli.cmd_panel = original_panel
            cli.cmd_run = original_run
            builtins.open = real_builtin
            io.open = real_io

        check("panel argv dispatches to cmd_panel",
              rc_panel == 0 and panel_func == "panel")
        check("panel --config is parsed",
              panel_args is not None and panel_args.config == mapping["__CONFIG_PATH__"])
        check("panel --host is parsed",
              panel_args is not None and panel_args.host == "127.0.0.1")
        check("panel --port is parsed",
              panel_args is not None and panel_args.port == 8090)
        check("run argv dispatches to cmd_run", rc_run == 0 and run_func == "run")
        check("run --config is parsed",
              run_args is not None and run_args.config == mapping["__CONFIG_PATH__"])
        check("run keeps the CLI default timeout",
              run_args is not None and run_args.timeout == 1800,
              str(run_args.timeout if run_args else None))
        check("no repository file is opened during dispatch",
              not [p for p in opened if str(p).startswith(str(ROOT))],
              str(opened[:2]))
        check("global --config must precede the subcommand",
              reversed_outcome == "error", reversed_outcome)


def test_semantics_and_labels() -> None:
    print("== 4. panel KeepAlive versus scheduled timer ==")
    panel = plistlib.loads(PANEL_EXAMPLE.read_bytes())
    fetch = plistlib.loads(FETCH_EXAMPLE.read_bytes())
    check("panel RunAtLoad true", panel.get("RunAtLoad") is True)
    check("panel KeepAlive true", panel.get("KeepAlive") is True)
    check("panel ThrottleInterval is 10", panel.get("ThrottleInterval") == 10)
    check("panel ProcessType Background", panel.get("ProcessType") == "Background")
    check("panel binds loopback only", "127.0.0.1" in panel["ProgramArguments"])
    check("fetch RunAtLoad false", fetch.get("RunAtLoad") is False)
    check("fetch has no KeepAlive key", "KeepAlive" not in fetch)
    schedule = fetch.get("StartCalendarInterval")
    check("fetch is a calendar timer", isinstance(schedule, dict), str(schedule))
    check("example schedule is 07:30",
          isinstance(schedule, dict) and schedule.get("Hour") == 7
          and schedule.get("Minute") == 30, str(schedule))
    check("panel has no calendar schedule", "StartCalendarInterval" not in panel)
    check("labels differ", panel["Label"] != fetch["Label"])
    check("labels do not use a private reverse-DNS namespace",
          not panel["Label"].lower().startswith("com.")
          and not fetch["Label"].lower().startswith("com."))
    check("labels share the generic public prefix",
          panel["Label"].startswith("local.au-job-pipeline.")
          and fetch["Label"].startswith("local.au-job-pipeline."))


def test_failure_paths() -> None:
    print("== 5. failure paths ==")
    namespace = load_doc_renderer()
    render = namespace["render_templates"]
    with tempfile.TemporaryDirectory() as d, offline_guard():
        root = Path(d)
        mapping, project, log_dir, config = make_env(root)
        out = root / "rendered"

        bad = dict(mapping)
        bad["__PROJECT_DIR__"] = "relative/project"
        try:
            render(REPO_LAUNCHD, out, bad)
            outcome = "no-error"
        except ValueError:
            outcome = "error"
        check("non-absolute path is rejected", outcome == "error", outcome)

        missing = {key: value for key, value in mapping.items() if key != "__LOG_DIR__"}
        try:
            render(REPO_LAUNCHD, out, missing)
            outcome = "no-error"
        except ValueError:
            outcome = "error"
        check("missing placeholder key is rejected", outcome == "error", outcome)

        bad = dict(mapping)
        bad["__CONFIG_PATH__"] = 123
        try:
            render(REPO_LAUNCHD, out, bad)
            outcome = "no-error"
        except ValueError:
            outcome = "error"
        check("non-string replacement is rejected", outcome == "error", outcome)

        bad = dict(mapping)
        bad["__PROJECT_DIR__"] = str(root / "does-not-exist")
        try:
            render(REPO_LAUNCHD, root / "missing-env", bad)
            outcome = "no-error"
        except ValueError:
            outcome = "error"
        check("missing project directory is rejected", outcome == "error", outcome)

        template_dir = root / "templates"
        template_dir.mkdir()
        (template_dir / "panel.plist.example").write_text(
            PANEL_EXAMPLE.read_text(encoding="utf-8").replace("__LOG_DIR__", "__EXTRA__"),
            encoding="utf-8")
        (template_dir / "fetch.plist.example").write_text(
            FETCH_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
        try:
            render(template_dir, root / "unreplaced", mapping)
            outcome = "no-error"
        except ValueError as exc:
            outcome = "error" if "__EXTRA__" in str(exc) else f"wrong-error {exc}"
        check("unreplaced placeholder is rejected", outcome == "error", outcome)

        render(REPO_LAUNCHD, out, mapping)
        try:
            render(REPO_LAUNCHD, out, mapping)
            outcome = "no-error"
        except FileExistsError:
            outcome = "error"
        check("existing output directory is refused", outcome == "error", outcome)

        script = root / "render_doc.py"
        script.write_text(extract_doc_code(), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(script),
             "--template-dir", str(REPO_LAUNCHD),
             "--output-dir", str(root / "bad-output"),
             "--project-dir", "relative",
             "--python-path", mapping["__PYTHON_PATH__"],
             "--config-path", mapping["__CONFIG_PATH__"],
             "--log-dir", mapping["__LOG_DIR__"]],
            capture_output=True, text=True)
        check("documented script exits non-zero on a relative path",
              proc.returncode != 0, str(proc.returncode))


def test_template_input_completeness() -> None:
    print("== 6. real script rejects missing or incomplete template inputs ==")
    panel_bytes = PANEL_EXAMPLE.read_bytes()
    fetch_bytes = FETCH_EXAMPLE.read_bytes()
    with tempfile.TemporaryDirectory() as d, offline_guard():
        root = Path(d)
        mapping, project, log_dir, config = make_env(root)

        def write_templates(directory, names):
            directory.mkdir(parents=True, exist_ok=True)
            if "panel" in names:
                (directory / "panel.plist.example").write_bytes(panel_bytes)
            if "fetch" in names:
                (directory / "fetch.plist.example").write_bytes(fetch_bytes)
            return directory

        cases = [
            ("missing directory", root / "missing", "template directory does not exist"),
            ("empty directory", write_templates(root / "empty", []), "missing template"),
            ("only panel", write_templates(root / "only-panel", ["panel"]),
             "fetch.plist.example"),
            ("only fetch", write_templates(root / "only-fetch", ["fetch"]),
             "panel.plist.example"),
        ]
        for index, (label, template_dir, needle) in enumerate(cases):
            out = root / ("out-" + str(index))
            proc = _run_doc_script(root, template_dir, out, mapping)
            check(f"{label}: exits non-zero", proc.returncode != 0, str(proc.returncode))
            check(f"{label}: stderr names the missing input", needle in proc.stderr,
                  proc.stderr.strip()[-80:])
            check(f"{label}: stdout has no success output", proc.stdout.strip() == "",
                  proc.stdout.strip()[:40])
            check(f"{label}: output directory is not created", not out.exists(), str(out))

        as_file = root / "not-a-directory"
        as_file.write_text("x", encoding="utf-8")
        out_file = root / "out-file"
        proc = _run_doc_script(root, as_file, out_file, mapping)
        check("template path that is a file exits non-zero", proc.returncode != 0)
        check("template path that is a file names the problem",
              "not a directory" in proc.stderr, proc.stderr.strip()[-80:])
        check("template path that is a file creates no output", not out_file.exists())

        good = write_templates(root / "good", ["panel", "fetch"])
        out_good = root / "out-good"
        proc_good = _run_doc_script(root, good, out_good, mapping)
        check("complete templates exit 0", proc_good.returncode == 0,
              proc_good.stderr.strip()[:80])
        check("complete templates produce exactly two files",
              out_good.is_dir()
              and sorted(p.name for p in out_good.iterdir()) == ["fetch.plist", "panel.plist"],
              str(sorted(p.name for p in out_good.iterdir()) if out_good.is_dir() else None))
        rendered_panel = plistlib.loads((out_good / "panel.plist").read_bytes())
        rendered_fetch = plistlib.loads((out_good / "fetch.plist").read_bytes())
        check("rendered panel argv matches the contract",
              rendered_panel["ProgramArguments"] == [
                  mapping["__PYTHON_PATH__"], "-u", "-m", "jobs.cli",
                  "--config", mapping["__CONFIG_PATH__"],
                  "panel", "--host", "127.0.0.1", "--port", "8090"])
        check("rendered fetch argv matches the contract",
              rendered_fetch["ProgramArguments"] == [
                  mapping["__PYTHON_PATH__"], "-u", "-m", "jobs.cli",
                  "--config", mapping["__CONFIG_PATH__"], "run"])

        extra = write_templates(root / "extra", ["panel", "fetch"])
        (extra / "unrelated.plist.example").write_text("<plist/>", encoding="utf-8")
        out_extra = root / "out-extra"
        proc_extra = _run_doc_script(root, extra, out_extra, mapping)
        check("complete templates with an extra example exit 0",
              proc_extra.returncode == 0, proc_extra.stderr.strip()[:80])
        check("an unrelated example adds no third output",
              out_extra.is_dir()
              and sorted(p.name for p in out_extra.iterdir()) == ["fetch.plist", "panel.plist"],
              str(sorted(p.name for p in out_extra.iterdir()) if out_extra.is_dir() else None))

        existing = root / "existing-out"
        existing.mkdir()
        marker = existing / "keep.txt"
        marker.write_text("KEEP", encoding="utf-8")
        proc_existing = _run_doc_script(root, good, existing, mapping)
        check("existing output is refused by the real script",
              proc_existing.returncode != 0)
        check("existing output content is unchanged",
              marker.read_text(encoding="utf-8") == "KEEP"
              and sorted(p.name for p in existing.iterdir()) == ["keep.txt"])

        broken = write_templates(root / "broken", ["fetch"])
        (broken / "panel.plist.example").write_bytes(
            panel_bytes.replace(b"__LOG_DIR__", b"__EXTRA__"))
        out_broken = root / "out-broken"
        proc_broken = _run_doc_script(root, broken, out_broken, mapping)
        check("unreplaced placeholder exits non-zero", proc_broken.returncode != 0)
        check("unreplaced placeholder names the template",
              "__EXTRA__" in proc_broken.stderr, proc_broken.stderr.strip()[-80:])
        check("unreplaced placeholder creates no output directory", not out_broken.exists())


def test_selfcheck() -> None:
    print("== 7. framework self-check ==")
    panel = plistlib.loads(PANEL_EXAMPLE.read_bytes())
    deliberately_wrong = (panel.get("Label") == FETCH_LABEL)
    check("deliberately wrong label expectation is false",
          deliberately_wrong is False, str(panel.get("Label")))


def main() -> int:
    test_parse_and_private_scan()
    test_render_contract()
    test_cli_dispatch()
    test_semantics_and_labels()
    test_failure_paths()
    test_template_input_completeness()
    test_selfcheck()
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
