# Public offline tests

This page describes the curated, offline test entry for the project. It is for
contributors and CI, not for a normal installation. A normal source install
still only needs the two short offline checks in
[install.md](install.md).

## The entry

`tools/run_public_tests.py` runs an explicit, fixed list of synthetic,
offline tests. There is no test discovery or globbing: the inventory is a
literal list, so a maintainer-only test that needs a private corpus can never
be collected by accident. The entry currently has 41 checks: 36 Python tests
plus five Node behavior tests.

Run it from the project root with the project interpreter:

```bash
.venv/bin/python tools/run_public_tests.py
.venv/bin/python tools/run_public_tests.py --list
```

The optional MCP adapter has two opt-in integration entries:
`python tools/run_public_tests.py --with-mcp` (43 entries on macOS).
It requires `requirements-mcp.txt` in addition to the runtime dependencies;
missing SDK dependencies fail the opt-in run. The default local command keeps
41 entries. Release CI installs the optional SDK and runs all 43 entries.
See [MCP](mcp.md).

It prints one line per test with the command, the exit status and the elapsed
time, then a summary. It exits non-zero if any applicable test fails. If a
required test file or the Node runtime is missing it reports a failure instead
of silently skipping it. The macOS LaunchAgent test is shown as "not
applicable" on a non-macOS system.

## Requirements

- **Python 3.12** with the runtime dependencies:
  `python -m pip install -r requirements.txt`.
- **Node.js 24.21.0** for the five panel behavior tests. Node is only
  needed to run the full public suite. It is **not** part of the product
  installation, the panel does not depend on it at runtime, and a normal
  install does not need it.

The tests themselves are offline. Installing dependencies may use the network;
nothing in the suite fetches listings.

## What the tests cover

| Test | Covers | Platform |
|---|---|---|
| `tools/test_windows_runtime.py` | Lock handoff, supervisor success/busy/start failure, process-tree timeout, interruption and retry; run-state replace/read under a concurrent reader (Windows sharing retry, POSIX unchanged); `platform` skips WMI on CPython < 3.13.4 (gh-130727); native forced-parent-exit and platform lookup checks | all; native case on Windows |
| `tools/test_file_security.py` | POSIX modes, Windows ACL dispatch/failures and native private/public token checks | all; native ACL case on Windows |
| `tools/test_platform_install.py` | Melbourne DST, Git autocrlf byte stability, Chinese/space paths | all |
| `tools/test_agent_api.py` | Authenticated read-only HTTP, frozen pagination/detail, scope and external client failures | all |
| `tools/test_batches.py` | Exact fetch membership, source transactions and schema 3 recovery | all |
| `tools/test_location_http.py` | Group-level writes, location query guidance and same-origin protection | all |
| `tools/test_locations.py` | Offline Australian geography, ambiguity and three-tier matching | all |
| `tools/test_location_search.py` | Representative selection, block ordering and pagination | all |
| `tools/test_panel_location_views.py` | One location box, dividers and simplified group detail | all |
| `tools/test_panel_locations.mjs` | Group save identity, location search drafts and navigation guards | all |
| `tools/test_panel_shortcuts.mjs` | Keyboard profiles, cookie persistence, native text selection and input/overlay guards | all |
| `tools/test_panel_copy.mjs` | Complete JD copy, clipboard fallbacks, safe text and restored focus/selection | all |
| `tools/test_label_migration.py` | Atomic legacy upgrade, independent priority, correction provenance, concurrent partial edits and frozen action state | all |
| `tools/test_patch_boundaries.py` | Concurrent partial labels, explicit group clears and legacy events, config candidate limit, unknown-employer grouping, detail retries and score/CSV values | all |
| `tools/test_pipeline_boundaries.py` | Real interrupted writes, timeout finalization and competing standalone analysis | all |
| `tools/test_render_security.py` | Markdown URL attribute injection and ordinary rendering controls | all |
| `tools/test_skill_edit_concurrency.py` | Seed restoration, atomic file publication and competing overlay editors across processes | all |
| `tools/test_salary.py` | Decimal percentages/money, shared range units and numeric controls | all |
| `tools/test_config_edit.py` | YAML roundtrip/CLI semantics, allowlist, conflicts, shared lock, applied and legacy-file snapshots | all |
| `tools/test_run_state.py` | Unified run/fetch facts, generations and orphan-worker recovery | all |
| `tools/test_public_configuration.py` | Public example configuration, source selection, loopback binding, read-only profile command | all |
| `tools/test_citizenship_rules.py` | Citizenship rules against the synthetic fixture | all |
| `tools/test_eeo_rules.py` | Citizenship EEO / welcome guard | all |
| `tools/test_experience_rules.py` | Experience-year scoring signals | all |
| `tools/test_legacy_rule_profiles.py` | The six legacy rule profiles and per-rule switches | all |
| `tools/test_legacy_rule_profile_integration.py` | Profile wiring through analyze, report and panel | all |
| `tools/test_eligibility_profile.py` | Eligibility profile parsing, validation and the read-only command | all |
| `tools/test_labels_notes.py` | Manual labels and notes | all |
| `tools/test_panel_logic.py` | Panel core logic | all |
| `tools/test_panel_i18n.py` | Panel English / Chinese text | all |
| `tools/test_panel_stats_http.py` | Read-only header-count endpoint (same counts as the page, same-origin/host checks, no write path) and the rendered hooks for detail navigation, the mobile label sheet, the filter drawer and skill candidates | all |
| `tools/test_panel_display.py` | Panel display layer: card/detail keep every score, salary and label signal (moved, not removed), `render.tags_for` output for report/CSV/search is unchanged, Display menu option order, update confirmation, settings sections | all |
| `tools/test_panel_update.py` | Frozen fetch settings, background-run state, locks, partial failure, timeout and label preservation | all |
| `tools/test_panel_update_http.py` | Update API, same-origin/host checks, JSON input and disabled demo | all |
| `tools/test_skills_and_search.py` | Skill vocabulary, compatible display names and search matching | all |
| `tools/test_demo.py` | Synthetic demo end to end (isolated, loopback only) | all |
| `tools/test_legacy_cleanup.py` | Legacy single-path end to end | all |
| `tools/test_fixture_boundary.py` | Test-data boundary and export contract | all |
| `tools/test_launchd_examples.py` | macOS LaunchAgent templates and renderer | macOS |
| `tools/test_panel_note_separators.mjs` | Panel note separators, default labels (UI-02, UI-03) and generation-based refresh, draft protection and destination navigation, update confirmation, card progress text and the unknown-outcome status line | all |
| `tools/test_panel_navigation.mjs` | Detail previous/next and profile keys over the frozen list (save/draft gate, pending result version, repeated keys, ignored keys), mobile label sheet, header-count refresh, mobile filter drawer apply/discard and skill candidates writing canonical names | all |

The fixture-boundary test also guards that a private label export is not used
by default; it is part of keeping the public test data synthetic. Its output
contains one `[FAIL]` line from a deliberate negative control (a wrong expected
value used to prove the check can fail). Judge a run by the exit status of each
test and the entry's summary line, not by a single printed line.

Docker has a separate opt-in smoke test: `python tools/test_docker.py`. It
requires an active Docker Engine and may download packages during the build.
Runtime sources are mocked; pipeline containers have no network, while
published API/MCP port checks use a temporary bridge without fetching. See
[docker.md](docker.md) for its checks and actual tested architecture.

## Windows candidate coverage

The same entry is intended for native Windows x64 / Python 3.12. It does not
require any public CI change. On Windows there are 40 default entries or 42
with MCP; only the macOS LaunchAgent entry is inapplicable. A native run remains
pending; see [Windows setup](windows.md).

Within the tests, the detached POSIX file-descriptor test keeps its POSIX-only
scope; `test_windows_runtime` covers native Windows inherited handles, atomic Job association (including venv launchers), forced
supervisor exit, process-tree timeout and saved-note/retry behavior. Windows
in-process pipeline tests inject controlled interruption instead of SIGALRM;
the real deadline is checked by the supervisor integration. Mode assertions use
ACL validation on Windows. The Windows HTTP MCP entry runs all cases in one
process, so a native crash in a later server lifecycle fails the entry. An earlier
access violation came from CPython's `_wmi` query thread (gh-130727), reached
through `platform.uname()`; `jobs` disables that WMI path on CPython < 3.13.4.
A symlink test may skip if the account lacks symlink
privilege; ordinary regular-file saves remain covered. The runtime supervisor
also runs on macOS with a POSIX process-tree adapter, which tests the control
flow but cannot establish native Windows API behavior. Native ACL and forced
Windows-exit cases are explicitly skipped on macOS.

## What is not in the public entry

| Test | Why it is excluded |
|---|---|
| `tools/test_public_snapshot.py` | It tests the maintainer public-snapshot planner, not the installed product. It is internal release tooling, so it does not belong in a user-facing public suite. |
| `tools/regression_corpus.py` | It needs a private job corpus. |
| `tools/capture_demo_screenshots.py`, `tools/capture_demo_screenshots.sheets.py`, `tools/capture_demo_video.py`, `tools/prepare_screenshot_mock.py` | Media and presentation tooling, not product tests; it also needs browser or video tools that a CI test job should not install. |

## Continuous integration

`.github/workflows/offline-tests.yml` runs the same entry on
`macos-15` with Python 3.12 and Node 24.21.0. That macOS 15 / Python 3.12
combination is the only environment with evidence so far; Linux, Windows and
other Python versions are not verified and are deliberately not configured.

The workflow runs automatically for a pull request into `main` or `dev` and for every
push to `main` or `dev`, and it can be started by hand (`workflow_dispatch`). It has no
path filter and does not skip drafts, so a documentation-only change still
produces the check. The job is named `macOS 15 / Python 3.12 / Node 24.21.0`;
that is the check name to look for on a pull request. It uses the ordinary
`pull_request` event with read-only repository access and no secrets.
Before the tests it lists the tracked paths and refuses to run if it finds a
known private path (the private configuration, the vocabulary overlay, the
corpus, runtime data or the real deployment plists). It reads file names only,
never file contents.

A manual cloud run on macOS 15 / Python 3.12 / Node 24.21.0 passed 16 of 16 on
2026-10-08; the run and its limits are recorded in
[installation-verification.md](installation-verification.md).

## Boundary

Everything here is synthetic and offline. The tests use temporary directories,
do not open the production database, do not fetch, and stop any loopback server
they start. A pass is evidence about this source tree only. It is not a
guarantee about real job data, live platform access, other operating systems or
other Python versions, and it is not a public-release acceptance.
