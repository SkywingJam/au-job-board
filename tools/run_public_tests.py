#!/usr/bin/env python3
"""Public offline test entry for the au-job-pipeline.

This runs the curated public regression suite as **explicit** subprocesses.
There is no glob or auto-discovery: the inventory below is the complete list,
so a private-corpus test under tools/ is never picked up by accident.

Boundary:

* offline only -- no fetching, no production database, no real listener;
* every test builds its own synthetic data in temporary directories (the
  synthetic demo test starts a loopback HTTP server by itself and stops it);
* the Python tests are run with the current interpreter (sys.executable);
* Node is used only for the maintainer UI behavior test. A normal install does
  not need Node, but the full public suite does.

The same inventory is the one .github/workflows/offline-tests.yml runs; the
workflow only calls this script.

Entries run concurrently (``--jobs N``, default min(4, cpu count)); each
entry's output is buffered and printed as one block when it finishes, followed
by an ordered summary. ``--jobs 1`` runs the entries one after another in
inventory order with live output. The entries in SERIAL_TESTS are never run
beside another entry: they run alone, after the parallel group has finished.

Usage:
    .venv/bin/python tools/run_public_tests.py
    .venv/bin/python tools/run_public_tests.py --jobs 1
    .venv/bin/python tools/run_public_tests.py --list

Exit status:
    0  every applicable test ran and passed
    1  a required test failed, a required runtime is missing, or a required
       test file is missing
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (relative path, one-line description, applicable sys.platform values)
# platforms=None means every platform; a tuple restricts the entry.
PYTHON_TESTS = (
    ("tools/test_map_pages.py", "registered map page authorization, query parity and summaries", None),
    ("tools/test_map_reference.py", "offline postcode reference integrity and geometry", None),
    ("tools/test_plugin_panel.py", "plugin settings, debug and presentation HTTP boundaries", None),
    ("tools/test_plugin_host.py", "plugin process isolation and result generations", None),
    ("tools/test_plugin_consent_backend.py", "plugin consent and trusted worker boundaries", None),
    ("tools/test_windows_runtime.py", "cross-platform lock ownership and Windows pipeline supervisor", None),
    ("tools/test_file_security.py", "private save permissions and shared token ACL validation", None),
    ("tools/test_platform_install.py", "timezone data, LF resources and Unicode source paths", None),
    ("tools/test_agent_api.py", "authenticated read-only snapshots and external agent client", None),
    ("tools/test_agent_snapshot_lifecycle.py", "snapshot concurrency, cancellation and bounded lazy evidence", None),
    ("tools/test_job_history.py", "local posting history in copy text and API detail", None),
    ("tools/test_batch_labels.py",
     "opt-in batch label write API: atomic group-aware progress/priority, gates and client", None),
    ("tools/test_batches.py", "exact fetch membership and schema 3 recovery", None),
    ("tools/test_location_http.py", "location HTTP scope and same-origin protection", None),
    ("tools/test_locations.py", "offline Australian location reference and matching", None),
    ("tools/test_location_search.py", "group representative selection and three-block location search", None),
    ("tools/test_panel_location_views.py", "location UI rendering and provenance", None),
    ("tools/test_ineligible_last.py",
     "ineligible-last qualification zones across panel, API snapshots and pages", None),
    ("tools/test_label_migration.py",
     "label migration, priority, correction provenance and frozen action state", None),
    ("tools/test_patch_boundaries.py",
     "human-data concurrency, explicit clears, source and report regressions", None),
    ("tools/test_pipeline_boundaries.py",
     "real interrupted-write cleanup and competing analysis processes", None),
    ("tools/test_render_security.py",
     "untrusted Markdown attribute boundaries and normal rendering", None),
    ("tools/test_skill_edit_concurrency.py",
     "overlay restoration and serialized atomic cross-process edits", None),
    ("tools/test_salary.py",
     "salary numeric-token and shared-unit ranges", None),
    ("tools/test_run_state.py",
     "unified task facts, generations and recovery", None),
    ("tools/test_config_edit.py",
     "settings YAML roundtrip, validation, conflicts and shared lock", None),
    ("tools/test_public_configuration.py",
     "public example configuration boundary", None),
    ("tools/test_citizenship_rules.py",
     "citizenship eligibility rules (synthetic fixture)", None),
    ("tools/test_eeo_rules.py",
     "citizenship EEO / welcome guard", None),
    ("tools/test_experience_rules.py",
     "experience-year scoring signals", None),
    ("tools/test_legacy_rule_profiles.py",
     "legacy rule profile switches", None),
    ("tools/test_legacy_rule_profile_integration.py",
     "legacy profile wiring through analyze / report / panel", None),
    ("tools/test_eligibility_profile.py",
     "eligibility profile parsing and read-only CLI", None),
    ("tools/test_labels_notes.py",
     "manual labels and notes", None),
    ("tools/test_panel_logic.py",
     "panel core logic", None),
    ("tools/test_panel_update.py",
     "panel update configuration / concurrency / failure contracts (offline)", None),
    ("tools/test_panel_update_http.py",
     "panel update HTTP API and same-origin boundary (loopback)", None),
    ("tools/test_panel_i18n.py",
     "panel English / Chinese text", None),
    ("tools/test_panel_stats_http.py",
     "read-only header counts endpoint, same-origin boundary and navigation hooks (loopback)", None),
    ("tools/test_panel_multiselect.py",
     "panel multi-select bulk marking endpoint, same-origin boundary and rendered hooks (loopback)", None),
    ("tools/test_panel_display.py",
     "panel display layer: evidence moved not removed, shared tags unchanged", None),
    ("tools/test_skills_and_search.py",
     "skill vocabulary and search matching", None),
    ("tools/test_demo.py",
     "synthetic demo end to end (isolated, loopback only)", None),
    ("tools/test_legacy_cleanup.py",
     "legacy single-path end to end", None),
    ("tools/test_fixture_boundary.py",
     "test-data boundary and export contract", None),
    ("tools/test_launchd_examples.py",
     "macOS LaunchAgent templates and renderer", ("darwin",)),
)

NODE_TESTS = (
    ("tools/test_map_bridge.mjs", "plugin page bridge messages, bounds and core detail guard", None),
    ("tools/test_panel_shortcuts.mjs", "keyboard profiles, native selection and overlay guards", None),
    ("tools/test_panel_copy.mjs", "full JD clipboard, fallback and focus preservation", None),
    ("tools/test_plugin_settings_race.mjs", "plugin switches survive a late initial settings read", None),
    ("tools/test_panel_locations.mjs", "group save identity and location search behavior", None),
    ("tools/test_panel_note_separators.mjs",
     "panel note separators / default labels (UI-02, UI-03)", None),
    ("tools/test_panel_navigation.mjs",
     "panel detail navigation, label sheet, filter drawer, header counts and skill candidates", None),
)

MCP_TESTS = (
    ("tools/test_mcp.py", "optional MCP stdio / authenticated HTTP integration", None),
    ("tools/test_mcp_http.py", "optional Streamable HTTP / app lifecycle / capacity", None),
)

# Entries that must not share the machine with any other entry. They run one
# at a time, alone, after the parallel group has finished.
SERIAL_TESTS = {
    # Reserves a panel port and relies on "panel port + 1" staying free
    # between its probe and the real bind; any concurrent ephemeral listener
    # could take that port.
    "tools/test_mcp_http.py": "derived panel-port+1 probe-then-bind",
    # Snapshots every file in the checkout before and after the demo run and
    # fails on any change, so no other entry may write there meanwhile.
    "tools/test_demo.py": "whole-checkout before/after file snapshot",
}


def build_env() -> dict:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"
    return env


def default_jobs() -> int:
    return max(1, min(4, os.cpu_count() or 1))


def applicable(platforms) -> bool:
    return platforms is None or sys.platform in platforms


def node_version() -> str:
    node = shutil.which("node")
    if not node:
        return ""
    try:
        proc = subprocess.run([node, "--version"], capture_output=True, text=True)
    except OSError:
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


_live_lock = threading.Lock()
_live: dict = {}  # pid -> Popen for every child that is still running
_stopping = False  # set once an interrupt starts; no new child may start


def _kill_tree(proc: subprocess.Popen, force: bool) -> None:
    """Stop a child and everything it started (POSIX: its own session)."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True)
        else:
            os.killpg(proc.pid, signal.SIGKILL if force else signal.SIGTERM)
    except (OSError, ProcessLookupError):
        pass


def kill_all_children() -> None:
    global _stopping
    with _live_lock:
        _stopping = True
        procs = list(_live.values())
    for proc in procs:
        _kill_tree(proc, force=False)
    deadline = time.monotonic() + 5
    for proc in procs:
        try:
            proc.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
    for proc in procs:
        _kill_tree(proc, force=True)  # also reaps stragglers in the group
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def _on_sigterm(_signum, _frame):
    raise KeyboardInterrupt


def run_command(command: list, env: dict, capture: bool):
    """Run one command; return (returncode, output bytes or None, seconds)."""
    started = time.monotonic()
    kwargs = {"cwd": str(ROOT), "env": env}
    if os.name != "nt":
        kwargs["start_new_session"] = True  # lets us kill the whole tree
    # Output goes to a temporary file rather than a pipe, so waiting only
    # depends on the test process itself, as with inherited stdout before.
    sink = tempfile.TemporaryFile() if capture else None
    try:
        if sink is not None:
            kwargs.update(stdout=sink, stderr=subprocess.STDOUT)
        with _live_lock:
            if _stopping:
                raise KeyboardInterrupt
            proc = subprocess.Popen(command, **kwargs)
            _live[proc.pid] = proc
        try:
            proc.wait()
        finally:
            with _live_lock:
                _live.pop(proc.pid, None)
        output = None
        if sink is not None:
            sink.seek(0)
            output = sink.read()
    finally:
        if sink is not None:
            sink.close()
    return proc.returncode, output, time.monotonic() - started


def emit(data: bytes) -> None:
    sys.stdout.flush()
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        buffer.write(data)
        buffer.flush()
    else:
        sys.stdout.write(data.decode("utf-8", "replace"))
        sys.stdout.flush()


class Entry:
    def __init__(self, index, path, label, command, error=None):
        self.index = index
        self.path = path
        self.label = label
        self.command = command  # None when the entry cannot run
        self.error = error      # failure reason when it cannot run
        self.serial = path in SERIAL_TESTS
        self.ok = False
        self.rc = None
        self.seconds = 0.0


def print_header(entry: Entry, total: int) -> None:
    print("[%d/%d] %s" % (entry.index, total, entry.label))
    if entry.command:
        print("        $ " + " ".join(entry.command))


def print_result(entry: Entry) -> None:
    if entry.command is None:
        print("        -> FAIL (%s)" % entry.error)
    else:
        print("        -> %s (rc=%s, %.1fs)" % (
            "PASS" if entry.ok else "FAIL", entry.rc, entry.seconds))
    print()


def execute(entry: Entry, env: dict, capture: bool):
    rc, output, seconds = run_command(entry.command, env, capture)
    entry.rc, entry.seconds, entry.ok = rc, seconds, rc == 0
    return output


def run_parallel(entries: list, total: int, env: dict, jobs: int) -> None:
    """Run entries concurrently; print each one as a block when it finishes."""
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=jobs)
    try:
        futures = {pool.submit(execute, e, env, True): e for e in entries}
        for future in concurrent.futures.as_completed(futures):
            entry = futures[future]
            output = future.result()
            print_header(entry, total)
            if output:
                emit(output)
                if not output.endswith(b"\n"):
                    print()
            print_result(entry)
    except BaseException:
        kill_all_children()
        pool.shutdown(wait=True, cancel_futures=True)
        raise
    pool.shutdown(wait=True)


def print_inventory() -> None:
    print("Repository root: %s" % ROOT)
    print("Python: %s (%s)" % (sys.executable, sys.version.split()[0]))
    print("Platform: %s" % sys.platform)
    print()
    print("Public Python tests:")
    for path, label, platforms in PYTHON_TESTS:
        scope = "all platforms" if platforms is None else "/".join(platforms)
        if path in SERIAL_TESTS:
            scope += ", runs alone"
        print("  %-52s %s  [%s]" % (path, label, scope))
    print()
    print("Public Node (maintainer UI) tests:")
    for path, label, platforms in NODE_TESTS:
        scope = "all platforms" if platforms is None else "/".join(platforms)
        print("  %-52s %s  [%s]" % (path, label, scope))
    print()
    print("Excluded from the public entry (see docs/public-tests.md):")
    print("  tools/test_public_snapshot.py      maintainer snapshot planner")
    print("  tools/regression_corpus.py         needs a private corpus")
    print("  tools/build_metro_reference.py     maintainer rebuild of the ABS metropolitan reference")
    print("  Optional --with-mcp: tools/test_mcp.py and tools/test_mcp_http.py (requirements-mcp.txt)")
    print()
    print("Run alone, after the parallel group (--jobs > 1):")
    for path, reason in SERIAL_TESTS.items():
        print("  %-52s %s" % (path, reason))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the curated public offline test suite.")
    parser.add_argument("--list", action="store_true",
                        help="print the inventory and exit without running")
    parser.add_argument("--with-mcp", action="store_true",
                        help="also run the optional MCP integration test (requires requirements-mcp.txt)")
    parser.add_argument("--jobs", type=int, default=default_jobs(), metavar="N",
                        help="entries to run at once (default: min(4, CPU count)); "
                             "1 runs them sequentially in inventory order")
    args = parser.parse_args(argv)
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")

    if args.list:
        print_inventory()
        return 0

    print("Public offline test entry")
    print("Repository root: %s" % ROOT)
    print("Python: %s (%s)" % (sys.executable, sys.version.split()[0]))
    print("Platform: %s" % sys.platform)
    nv = node_version()
    print("Node: %s" % (nv if nv else "not found (needed for the maintainer UI test)"))
    print("Jobs: %d%s" % (args.jobs, " (sequential)" if args.jobs == 1 else ""))
    print()

    env = build_env()

    python_tests = PYTHON_TESTS + (MCP_TESTS if args.with_mcp else ())
    py_applicable = [(p, d, pl) for (p, d, pl) in python_tests if applicable(pl)]
    node_applicable = [(p, d, pl) for (p, d, pl) in NODE_TESTS if applicable(pl)]
    total = len(py_applicable) + len(node_applicable)
    node = shutil.which("node")

    entries: list = []
    for runtime, group in ((sys.executable, py_applicable), (node, node_applicable)):
        for path, label, _platforms in group:
            index = len(entries) + 1
            target = ROOT / path
            if not target.is_file():
                entries.append(Entry(index, path, label, None,
                                     "missing required test file: %s" % path))
            elif not runtime:
                entries.append(Entry(index, path, label, None,
                                     "node not found on PATH; this maintainer UI test is "
                                     "required for the full public suite"))
            else:
                entries.append(Entry(index, path, label, [runtime, str(target)]))

    previous_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, _on_sigterm)
    try:
        for entry in entries:
            if entry.command is None:  # cannot run: report immediately
                print_header(entry, total)
                print_result(entry)
        runnable = [e for e in entries if e.command is not None]
        if args.jobs == 1:
            for entry in runnable:
                print_header(entry, total)
                sys.stdout.flush()
                execute(entry, env, capture=False)
                print_result(entry)
        else:
            run_parallel([e for e in runnable if not e.serial], total, env, args.jobs)
            for entry in (e for e in runnable if e.serial):
                # Exclusive: nothing else is running while this one does.
                run_parallel([entry], total, env, 1)
    except KeyboardInterrupt:
        kill_all_children()
        print()
        print("Interrupted: child test processes were stopped.")
        return 130
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)

    passed = sum(1 for e in entries if e.ok)
    failures = [e.path for e in entries if not e.ok]
    failed = len(failures)

    not_applicable = [p for (p, _d, pl) in PYTHON_TESTS + NODE_TESTS
                      if not applicable(pl)]
    skipped = len(not_applicable)

    print("=" * 64)
    print("Results in inventory order:")
    for entry in entries:
        print("  %-4s %6.1fs  %s" % ("PASS" if entry.ok else "FAIL",
                                     entry.seconds, entry.path))
    print("-" * 64)
    print("Summary: %d passed, %d failed, %d not applicable" % (
        passed, failed, skipped))
    if not_applicable:
        print("Not applicable here (different platform):")
        for path in not_applicable:
            print("  - %s" % path)
    if failures:
        print("Failed:")
        for path in failures:
            print("  - %s" % path)
    print("=" * 64)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
