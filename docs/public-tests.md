# Public offline tests

This page describes the curated, offline test entry for the project. It is for
contributors and CI, not for a normal installation. A normal source install
still only needs the two short offline checks in
[install.md](install.md).

## The entry

`tools/run_public_tests.py` runs an explicit, fixed list of synthetic,
offline tests. There is no test discovery or globbing: the inventory is a
literal list, so a maintainer-only test that needs a private corpus can never
be collected by accident. The entry currently has 16 checks: 15 Python tests
plus one Node behavior test.

Run it from the project root with the project interpreter:

```bash
.venv/bin/python tools/run_public_tests.py
.venv/bin/python tools/run_public_tests.py --list
```

It prints one line per test with the command, the exit status and the elapsed
time, then a summary. It exits non-zero if any applicable test fails. If a
required test file or the Node runtime is missing it reports a failure instead
of silently skipping it. The macOS LaunchAgent test is shown as "not
applicable" on a non-macOS system.

## Requirements

- **Python 3.12** with the runtime dependencies:
  `python -m pip install -r requirements.txt`.
- **Node.js 24.21.0** for the panel note-separator behavior test. Node is only
  needed to run the full public suite. It is **not** part of the product
  installation, the panel does not depend on it at runtime, and a normal
  install does not need it.

The tests themselves are offline. Installing dependencies may use the network;
nothing in the suite fetches listings.

## What the tests cover

| Test | Covers | Platform |
|---|---|---|
| `tools/test_public_configuration.py` | Public example configuration, source selection, loopback binding, read-only profile command | all |
| `tools/test_citizenship_rules.py` | Citizenship rules against the synthetic fixture | all |
| `tools/test_eeo_rules.py` | Citizenship EEO / welcome guard | all |
| `tools/test_experience_rules.py` | Experience-year scoring signals | all |
| `tools/test_legacy_rule_profiles.py` | The five legacy rule profiles and per-rule switches | all |
| `tools/test_legacy_rule_profile_integration.py` | Profile wiring through analyze, report and panel | all |
| `tools/test_eligibility_profile.py` | Eligibility profile parsing, validation and the read-only command | all |
| `tools/test_labels_notes.py` | Manual labels and notes | all |
| `tools/test_panel_logic.py` | Panel core logic | all |
| `tools/test_panel_i18n.py` | Panel English / Chinese text | all |
| `tools/test_skills_and_search.py` | Skill vocabulary and search matching | all |
| `tools/test_demo.py` | Synthetic demo end to end (isolated, loopback only) | all |
| `tools/test_legacy_cleanup.py` | Legacy single-path end to end | all |
| `tools/test_fixture_boundary.py` | Test-data boundary and export contract | all |
| `tools/test_launchd_examples.py` | macOS LaunchAgent templates and renderer | macOS |
| `tools/test_panel_note_separators.mjs` | Panel note separators and default labels (UI-02, UI-03) | all |

The fixture-boundary test also guards that a private label export is not used
by default; it is part of keeping the public test data synthetic.

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

The workflow is `workflow_dispatch` only. It is not triggered by push
or pull request while the repository is still a private development checkout;
automatic triggers are enabled only after the clean public candidate is ready.
Before the tests it lists the tracked paths and refuses to run if it finds a
known private path (the private configuration, the vocabulary overlay, the
corpus, runtime data or the real deployment plists). It reads file names only,
never file contents.

CI status: the workflow file and the same commands were verified locally. They
**have not been run on GitHub**, so this page does not claim a cloud CI pass.
The candidate install check is recorded separately in
[installation-verification.md](installation-verification.md).

## Boundary

Everything here is synthetic and offline. The tests use temporary directories,
do not open the production database, do not fetch, and stop any loopback server
they start. A pass is evidence about this source tree only. It is not a
guarantee about real job data, live platform access, other operating systems or
other Python versions, and it is not a public-release acceptance.
