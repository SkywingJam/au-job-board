# Test data policy: synthetic by default, private exports isolated

The rule in one sentence: **the tests that ship with the project use only
hand-written synthetic data; a real label export goes to the ignored `out/`
directory and can take part in a regression only when it is named explicitly.**

## Synthetic data only

- The default eligibility regression reads only
  `tools/fixtures/synthetic_citizenship.json`. Every entry is a hand-written
  minimal synthetic sentence whose fields are only
  `id` / `expected` / `text` (and optional `category`).
- Ids look like `synthetic_citizenship_001`; they locate a case and map to
  no real posting.
- No real posting provenance and no personal records: no real company, real
  platform id, real posting URL, application timeline, label reason, real uid
  or application record. Renaming a company or deleting metadata from a real
  posting is **not** synthetic; the sentence must be newly written for the test.
- Format placeholders are allowed and expected: reserved domains such as
  `https://example.invalid/...`, generic source names such as seek or
  linkedin, and placeholder uids. They are not real postings and must not be
  replaced with real sources.
- `tools/test_fixture_boundary.py` checks the schema, unique ids, allowed
  fields and category coverage, and scans for URL and platform-id shapes as a
  supplement. It does not replace a manual read and is not a claim that a whole
  repository has passed a privacy audit.

## What the default suites cover

Everything below is offline and uses temporary directories or in-memory
objects; none of it opens the production database, fetches listings or reads a
private label export.

- eligibility rules: `tools/test_citizenship_rules.py` against the synthetic
  fixture, and `tools/test_eeo_rules.py` for the EEO / welcome guard;
- experience-year scoring: `tools/test_experience_rules.py`;
- rule profiles and their wiring:
  `tools/test_legacy_rule_profiles.py` and
  `tools/test_legacy_rule_profile_integration.py`;
- the read-only eligibility profile:
  `tools/test_eligibility_profile.py`;
- labels and notes: `tools/test_labels_notes.py`;
- panel logic and text: `tools/test_panel_logic.py`,
  `tools/test_panel_i18n.py` and
  `tools/test_panel_note_separators.mjs`;
- skill vocabulary and search matching:
  `tools/test_skills_and_search.py`;
- the public example configuration and the read-only profile command:
  `tools/test_public_configuration.py`;
- the synthetic demo: `tools/test_demo.py`;
- the legacy end-to-end path: `tools/test_legacy_cleanup.py`;
- the fixture and export boundary: `tools/test_fixture_boundary.py`;
- macOS LaunchAgent templates: `tools/test_launchd_examples.py`.

The full list and how to run it are in `public-tests.md`.
`tools/run_public_tests.py` runs all of them from one explicit inventory.

## The default and private entries are separate

Default (offline, no database):

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tools/test_citizenship_rules.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tools/test_fixture_boundary.py
```

The default entry never probes or auto-loads `out/`, an old fixture, a
database, a private corpus or a label export. A missing, corrupt, empty or
schema-invalid fixture fails with a non-zero status; it is never silently
skipped and reported as a pass.

A private regression must name the file explicitly:

```bash
.venv/bin/python -m jobs.cli label --export-fixtures   # writes out/fixtures/ by default
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tools/test_citizenship_rules.py \
    --private-fixtures out/fixtures/labeled_citizenship.json
```

`--private-fixtures` accepts the `expected` / `text` schema
produced by `export_fixtures` (other fields are accepted as they are). A
missing, corrupt, empty or schema-invalid file also exits non-zero.

## The private export boundary

- `jobs.labels.FIXTURE_PATH` points by default to
  `<project root>/out/fixtures/labeled_citizenship.json`. `out/` is
  ignored by Git, so the default export does not overwrite the synthetic fixture
  that ships with the project.
- The export does **not** write `note` (free text is the easiest place to
  leak private content), but `uid`, `title`, `company`,
  `reason`, `labeled_at` and the description snippet are real and
  potentially sensitive.
- **Ignored is not backed up.** `out/` is not in Git; back up an export
  yourself if you need it.

## Export contract

Using an in-memory SQLite database and synthetic postings,
`tools/test_fixture_boundary.py` verifies
`export_fixtures(conn, path=None)`:

- the return keys are `written` / `skipped` / `no_snippet` /
  `unowned` / `path`;
- `eligible` becomes `expected: keep`; `ineligible` with a
  rule-owned cue becomes `expected: exclude` and keeps `reason`;
- `unsure` is skipped; a personal condition (such as a licence) is recorded
  in `unowned` without producing a "must exclude" case; a posting whose text
  has no eligibility sentence goes to `no_snippet`;
- ordering is by `labeled_at`; the export has no `note` key or note
  content;
- the test redirects the target to a temporary directory and never writes the
  project's real `out/`.

## What this policy does not prove

These checks are a boundary around the shipped synthetic fixtures and the
private export path. A pass is not a whole-repository privacy audit and is not a
guarantee about real job data. It also does not decide which files a public
release should ship; that is a separate, explicit decision.
