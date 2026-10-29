# Contributing

This project is released under the MIT License; see [LICENSE](../LICENSE)
(Copyright (c) 2026 SkywingJam). The repository is
<https://github.com/SkywingJam/au-job-board>. This page describes the working conventions and how changes are
made.

## What the project is for

A local, single-user pipeline for Australian job listings: collect, de-duplicate,
screen with hard filters, rank, and follow up in a local panel. Changes that keep
it local, deterministic and explainable fit. Changes that turn it into an
application bot, a hosted service, or a tool that makes eligibility decisions
about people do not.

## Ground rules

- **Keep exclusions explainable.** For eligibility, a posting that clearly
  cannot be applied for should be excluded, and the exclusion keeps a faithful
  quote of the original text as evidence. Vague or unknown wording is never
  treated as unmet automatically. Changes to regular expressions, scoring or new
  features have their own agreed scope; this guide does not extend them.
- **Do not describe more than the code does.** Documentation and UI text must
  match current behaviour. Do not write that something is verified, secure,
  compliant or supported unless it has been checked.
- **Profiles are rule switches.** Do not present a preset as an eligibility
  judgement. See [legacy-rule-profiles.md](legacy-rule-profiles.md).
- **Preserve human work.** Labels, notes, their history and vocabulary-overlay
  edits are not reproducible from listings. Their write paths are high risk,
  including indirect cross-module changes; see [AGENTS](../AGENTS.md).
  `analyze` must preserve labels and their history.
- **Keep fetching separate from analysis.** Only `fetch`, `details` and `run` may
  use the network.
- **Keep dependencies minimal.** The runtime uses `python-jobspy`,
  `PyYAML` and `ruamel.yaml` (comment-preserving configuration edits). The HTTP
  server uses the standard library.

## Data rules

Only hand-written synthetic data belongs in the repository, in tests and in
examples.

- Do not commit real job descriptions, listings, links, company names taken from
  real postings, your database, reports, label or note exports, or your
  configuration and vocabulary overlay.
- Rewording a real posting does not make it synthetic. Write the sentence new for
  the test.
- Placeholder values such as `https://example.invalid/...` and generic source
  names are fine.
- Test inputs that depend on a private corpus are not a public entry. The test
  data policy explains the boundary:
  [test-data-policy.md](test-data-policy.md).

## Tests

The tests are plain Python scripts under `tools/` that use only temporary
directories and synthetic data. The full public suite has one explicit entry:

```bash
.venv/bin/python tools/run_public_tests.py
```

It runs a fixed list (there is no test discovery), prints one result per test
and exits non-zero if any applicable test fails. It needs Node for the panel
note-separator behavior test; a normal install does not. The list, the
exclusions and the CI boundary are in
[public-tests.md](public-tests.md). To run a single area while working, the
scripts can still be called directly, for example:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tools/test_public_configuration.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tools/test_legacy_rule_profiles.py
```

`tools/regression_corpus.py` needs a private corpus and is not a public test.
The entry is a fixed list, so a maintainer-only test is never collected by
accident. A GitHub Actions workflow runs the same entry on macOS 15 and on Windows
Server 2022, both with Python 3.12; see [public-tests.md](public-tests.md). The source install and what it
checks are in [install.md](install.md) and
[installation-verification.md](installation-verification.md).

Use synthetic data for public checks. Authorized private local-copy comparisons
follow [test-data-policy.md](test-data-policy.md); production data remains
untouched and all private-derived results stay local.

## Pull requests

Development starts from `dev`; stable changes to `main` are made through a pull request. The workflow check named
`macOS 15 / Python 3.12 / Node 24.21.0` (the public offline suite) runs
automatically on the pull request and should pass; the Windows job
`Windows 2022 / Python 3.12 / Node 24.21.0` runs alongside it and its failures
should be investigated, although it is not yet required, and review discussions should
be resolved, before merging. No second person's approval is required. The
maintainer may use an emergency exception, and only through a pull request.
Force-pushing to `main` or deleting it is not part of the process.

## Documentation

- Write documents in English first. The Chinese entry is
  [README.zh-CN.md](../README.zh-CN.md); keep its core information consistent with
  [README.md](../README.md).
- Describe what exists. Put open questions in a clearly marked status section
  rather than turning a whole page into a to-do list.
- Keep internal task identifiers, personal paths, machine names and network
  addresses out of reader documentation.
- Screenshots, banners and videos must come from synthetic data and must not
  suggest real users or real results.

## Agent-assisted maintenance

[AGENTS.md](../AGENTS.md) keeps durable engineering/review invariants. Two
repository-local skills provide task-specific procedures:

- [jobs-verify](../.agents/skills/jobs-verify/SKILL.md): affected checks, synthetic
  browser acceptance and a separate usability walkthrough for substantial UI work.
- [jobs-pr-finish](../.agents/skills/jobs-pr-finish/SKILL.md): complete feedback
  collection, evidence-based fixes and a bounded handoff.

Root AGENTS fixes the authorization boundary for heuristic behavior changes;
the verification skill covers old-state inventories, counterexamples and
risk-based change batches as well as the detailed UX workflow: establish a representative
interaction before full integration, then verify behavior and usability
separately. Basic usability is part of implementation quality.

Direction previews may use a mockup or proof of concept to settle consequential
UX or functional choices early; they do not establish final acceptance.
First-pass review readiness and fewer unnecessary handoffs are goals, not extra
approval gates or a guarantee of a clean first review.

For an explicitly authorized delegated feedback repair trial, use the
[bounded PR repair procedure](pr-feedback-pilot.md). It records roles, the single initial-auto-review repair pass,
escalation and the no-manual-review default; it does not configure a model or
review integration.

Use these files on demand, without requiring extra plugins or a global skill
installation. Local verification and PR CI are evidence about their actual
revision, not authorization to merge, release or deploy.
