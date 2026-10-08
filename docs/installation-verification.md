# Installation verification

This records a **candidate install-flow verification** for the source
installation. It is not a final clean-snapshot fresh-clone acceptance: no public
snapshot or clean history exists yet, and CI is a later scope.

## Source and environment

| Item | Value |
|---|---|
| Source commit | `d415ab0d356be02243ef8951d3727c639e813e72` |
| Verification date | 2026-10-07 |
| Operating system | macOS 15.7.8 (build 24G824) |
| Python | 3.12.14 |
| pip (in the new venv) | 25.0.1 |

## Isolation method

- A new empty directory under the git-ignored `out/DSH-JOBS-REL-058/iso` was
  used, with a **new virtual environment**. The development `.venv`, its
  site-packages, the real configuration and any real database were not reused.
- 36 files were selected **by explicit path from the commit blob**
  (`git archive HEAD <paths>`). Exclusion precedes reading: the private
  `config.yaml`, `skills.local.yaml`, `research/`, `.memory/`, `data/`,
  `out/` and real deployment plists were never extracted or opened; only their
  SHA256 was computed in the project checkout. The project repository was not
  exported and then pruned.

Selected paths:

```
config.example.yaml
demo/demo.config.yaml
demo/demo.jobs.json
demo/demo.rules.yaml
demo/demo.skills.yaml
jobs/ (25 files: __init__, cli, config, db, dedupe, demo, eligibility,
      filters, i18n, labels, legacy_rule_profiles, panel, panel_assets/panel.css,
      panel_assets/panel.js, panel_assets/settings.js, panel_data, panel_views,
      render, report, salary, scoring, skills, sources/__init__,
      sources/jobspy_source, sources/seek)
requirements.txt
rules.yaml
skills.local.example.yaml
skills.yaml
tools/test_demo.py
tools/test_public_configuration.py
```

## Commands and results

The commands below are **normalized** for readability, not a verbatim shell
transcript. In this run the isolated root was `out/DSH-JOBS-REL-058/iso`
(called `<iso>` here), the new virtual environment was `<iso>/venv` and the
source was extracted to `<iso>/src`. The table writes the interpreter as
`.venv/bin/python`, which stands for `<iso>/venv/bin/python`; the two are the
same new environment. No symlink between the development `.venv` and
`<iso>/venv` is claimed; they are separate environments. This record was
reconstructed from the retained run logs (the install log, `pip freeze`, the
generated-demo log, the panel log and the two test logs); exact intermediate
argv beyond those logs is not claimed.

| Step | Normalized command (run from `<iso>/src`) | Result |
|---|---|---|
| Create venv | `python3.12 -m venv <iso>/venv` (interpreter `<iso>/venv/bin/python`) | Python 3.12.14 |
| Install deps | `.venv/bin/python -m pip install -r requirements.txt` | rc 0 |
| CLI help | `.venv/bin/python -m jobs.cli --help` | rc 0 |
| Profile (read-only) | `.venv/bin/python -m jobs.cli --config config.example.yaml eligibility-profile` | rc 0; `profile=citizen`, `filtering_mode=legacy` |
| Synthetic demo | `.venv/bin/python -m jobs.demo generate --root <iso>/demo-out --today 2026-10-07` | rc 0; 19 postings, 16 groups, 2 excluded, 6 labelled |
| Panel | `.venv/bin/python -m jobs.cli --config <iso>/demo-out/config.yaml panel --host 127.0.0.1 --port 18099` | `/` 200, `/settings` 200, `/api/skills` 200 |
| Panel shutdown | stop the server, then check the port | port 18099 released; no lingering process |
| Public config test | `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tools/test_public_configuration.py` | rc 0 (70 checks) |
| Public demo test | `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tools/test_demo.py` | rc 0 (all checks) |

The generated demo root `<iso>/demo-out` is confirmed by the retained
generated-demo log, and the panel log confirms port 18099 and the three HTTP 200
responses. That is the extent of the evidence kept; the record does not claim
more detail than those logs support.

Resolved dependency versions (`pip freeze`): direct dependencies
`python-jobspy==1.2.0` and `PyYAML==6.0.3`; key transitive versions
`pandas==3.0.6`, `numpy==2.5.3`, `curl_cffi==0.16.3`, `pydantic==2.13.5`,
`pydantic_core==2.46.5`, `requests==2.34.2`, `beautifulsoup4==4.15.0`,
`markdownify==1.2.3`, `cffi==2.1.1`, `urllib3==2.8.0`,
`certifi==2026.7.22`, `idna==3.20`, `charset-normalizer==3.5.2`,
`python-dateutil==2.9.0.post0`, `six==1.17.0`, `soupsieve==2.10`,
`annotated-types==0.8.0`, `pycparser==3.0`, `typing-extensions==4.16.0`,
`typing-inspection==0.4.4`. `requirements.txt` states only lower bounds and was
not modified.

## What this proves and what it does not

- Proves: a fresh venv can install the declared dependencies and the selected
  public source, the CLI and the read-only profile command run, the synthetic
  demo generates, the panel serves on loopback and stops cleanly, and the two
  public synthetic tests pass, all without the development environment or any
  private input.
- Test dependencies were checked first: `test_public_configuration.py` imports
  only `jobs/**` and reads `config.example.yaml` and `rules.yaml`;
  `test_demo.py` imports only `jobs/**` and reads `demo/**`, `jobs/**` and
  `tools/**`. No private-corpus test was run and no unrelated full suite was run.
- Does **not** prove: a clean-snapshot fresh-clone acceptance, CI, a published
  installer or archive, other operating systems or Python versions, license
  review, or real network fetching. Fetching was deliberately not exercised, so
  the SEEK / Indeed / LinkedIn integrations remain unverified against the live
  platforms. The macOS LaunchAgent templates were not installed.
- The pip cache was reported as not writable and was disabled; downloads still
  succeeded, so this is not an install failure. No system software, browser or
  video tooling was installed.

## State checks

- The project's protected `config.yaml`, `skills.local.yaml` and
  `jobs/filters.py` were not modified; only their SHA256 was computed.
- No production `data/` or `out/` was created. The isolated tree is under the
  git-ignored `out/` task root.
- The production panel, `fetch`, `analyze` and `report` were not run, and no
  live service was restarted.
