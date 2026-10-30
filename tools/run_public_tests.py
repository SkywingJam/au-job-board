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

Usage:
    .venv/bin/python tools/run_public_tests.py
    .venv/bin/python tools/run_public_tests.py --list

Exit status:
    0  every applicable test ran and passed
    1  a required test failed, a required runtime is missing, or a required
       test file is missing
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (relative path, one-line description, applicable sys.platform values)
# platforms=None means every platform; a tuple restricts the entry.
PYTHON_TESTS = (
    ("tools/test_windows_runtime.py", "cross-platform lock ownership and Windows pipeline supervisor", None),
    ("tools/test_file_security.py", "private save permissions and shared token ACL validation", None),
    ("tools/test_platform_install.py", "timezone data, LF resources and Unicode source paths", None),
    ("tools/test_agent_api.py", "authenticated read-only snapshots and external agent client", None),
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
    ("tools/test_panel_shortcuts.mjs", "keyboard profiles, native selection and overlay guards", None),
    ("tools/test_panel_copy.mjs", "full JD clipboard, fallback and focus preservation", None),
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


def build_env() -> dict:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"
    return env


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


def run_one(index: int, total: int, label: str, command: list, env: dict) -> bool:
    print("[%d/%d] %s" % (index, total, label))
    print("        $ " + " ".join(command))
    started = time.monotonic()
    proc = subprocess.run(command, cwd=str(ROOT), env=env)
    elapsed = time.monotonic() - started
    ok = proc.returncode == 0
    print("        -> %s (rc=%s, %.1fs)" % (
        "PASS" if ok else "FAIL", proc.returncode, elapsed))
    print()
    return ok


def print_inventory() -> None:
    print("Repository root: %s" % ROOT)
    print("Python: %s (%s)" % (sys.executable, sys.version.split()[0]))
    print("Platform: %s" % sys.platform)
    print()
    print("Public Python tests:")
    for path, label, platforms in PYTHON_TESTS:
        scope = "all platforms" if platforms is None else "/".join(platforms)
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the curated public offline test suite.")
    parser.add_argument("--list", action="store_true",
                        help="print the inventory and exit without running")
    parser.add_argument("--with-mcp", action="store_true",
                        help="also run the optional MCP integration test (requires requirements-mcp.txt)")
    args = parser.parse_args(argv)

    if args.list:
        print_inventory()
        return 0

    print("Public offline test entry")
    print("Repository root: %s" % ROOT)
    print("Python: %s (%s)" % (sys.executable, sys.version.split()[0]))
    print("Platform: %s" % sys.platform)
    nv = node_version()
    print("Node: %s" % (nv if nv else "not found (needed for the maintainer UI test)"))
    print()

    env = build_env()
    passed = 0
    failed = 0
    failures: list = []

    python_tests = PYTHON_TESTS + (MCP_TESTS if args.with_mcp else ())
    py_applicable = [(p, d, pl) for (p, d, pl) in python_tests if applicable(pl)]
    node_applicable = [(p, d, pl) for (p, d, pl) in NODE_TESTS if applicable(pl)]
    total = len(py_applicable) + len(node_applicable)
    index = 0

    for path, label, _platforms in py_applicable:
        index += 1
        target = ROOT / path
        if not target.is_file():
            failed += 1
            failures.append(path)
            print("[%d/%d] %s" % (index, total, label))
            print("        -> FAIL (missing required test file: %s)" % path)
            print()
            continue
        if run_one(index, total, label, [sys.executable, str(target)], env):
            passed += 1
        else:
            failed += 1
            failures.append(path)

    if node_applicable:
        node = shutil.which("node")
        for path, label, _platforms in node_applicable:
            index += 1
            target = ROOT / path
            if not target.is_file():
                failed += 1
                failures.append(path)
                print("[%d/%d] %s" % (index, total, label))
                print("        -> FAIL (missing required test file: %s)" % path)
                print()
                continue
            if not node:
                failed += 1
                failures.append(path)
                print("[%d/%d] %s" % (index, total, label))
                print("        -> FAIL (node not found on PATH; this maintainer")
                print("           UI test is required for the full public suite)")
                print()
                continue
            if run_one(index, total, label, [node, str(target)], env):
                passed += 1
            else:
                failed += 1
                failures.append(path)

    not_applicable = [p for (p, _d, pl) in PYTHON_TESTS + NODE_TESTS
                      if not applicable(pl)]
    skipped = len(not_applicable)

    print("=" * 64)
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
