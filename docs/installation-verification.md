# Installation verification

This records the current local verification of AU Job Board's source
installation, and what it does and does not show. The project is **not formally
released**: the intended repository, `https://github.com/SkywingJam/au-job-board`,
had not been created or published when this was recorded (it returned 404), so
nothing here verifies getting the source from GitHub.

## Current verification (2026-10-08)

The subject was the public candidate tree: 296 files under the MIT license,
committed as a **single root commit** (`9386c8e9c5dffbda3e3d45f92641cc090385c9f2`)
with no parent and no earlier history. It was verified from a local fresh clone
of that commit, in a new Python virtual environment, on one machine.

| Item | Value |
|---|---|
| Date | 2026-10-08 |
| Operating system | macOS 15.8, arm64 (one MacBook) |
| Python / pip (new venv) | 3.12.4 / 24.0 |
| Node | 24.5.0 (the CI workflow targets 24.21.0, which differs) |
| Direct dependencies | `python-jobspy 1.2.0`, `PyYAML 6.0.3` |
| Main transitive versions | `pandas 3.0.6`, `numpy 2.5.3`, `curl_cffi 0.16.3`, `pydantic 2.13.5`, `requests 2.34.2` |

`requirements.txt` states only lower bounds and was not changed; the versions
above are what pip resolved that day.

### Candidate and clone

- Each of the 296 files was checked one by one against the reviewed list for
  SHA-256, byte count, file mode (0644) and Git blob identity. The total is
  31,263,029 bytes and every file matched.
- The candidate repository and the clone each contain one commit, the root
  commit above. The clone was made with `git clone --no-local`: it has no
  `alternates`, no older Git objects and no dependency on the source checkout.
  Its only remote is the local candidate directory. The independent review also
  confirmed that the original source checkout was unchanged.
- The virtual environment was created with
  `include-system-site-packages = false`. Its `sys.prefix` and the locations of
  the imported `yaml` and `jobspy` packages were confirmed to be inside the new
  environment, not an existing development environment.

### Steps and results

Run from the clone's project root, in the order below. Commands are the ones in
[install.md](install.md); paths are shown as `out/demo-verify` and `.venv`.

| Step | Command | Result |
|---|---|---|
| Install | `.venv/bin/python -m pip install -r requirements.txt` | rc 0 (network) |
| CLI help | `.venv/bin/python -m jobs.cli --help` | rc 0 |
| Profile (read-only) | `.venv/bin/python -m jobs.cli --config config.example.yaml eligibility-profile` | rc 0; `profile` is `citizen` |
| Synthetic demo | `.venv/bin/python -m jobs.demo generate --root out/demo-verify` | rc 0; 19 postings, 16 groups, 2 excluded, 6 labelled |
| Full public suite | `.venv/bin/python tools/run_public_tests.py` | rc 0; 16 passed, 0 failed, 0 not applicable |
| Panel | `.venv/bin/python -m jobs.cli --config out/demo-verify/config.yaml panel --host 127.0.0.1 --port <free-port>` | `/` and `/settings` answered 200 in English and in Chinese; `/api/skills` answered 200 |
| Panel shutdown | stop the server, then check the port | port released; no process left |

The public suite includes the two install smoke tests in
[install.md](install.md) and the Node-based panel test, so the two smoke tests
were not run a second time on their own. The panel was started only against the
generated demo configuration, bound to loopback, and no personal configuration,
database, `fetch` or `analyze` against real data was touched.

After the run the clone's tracked files were byte-for-byte unchanged, Git
reported no modified or untracked source files, and the clone still had one
commit. The virtual environment, `out/` and other generated files are ignored by
`.gitignore`.

### What this shows and does not show

- Shows: on that one machine, a fresh clone of the clean-history candidate
  installs its declared dependencies in a new environment, the CLI and read-only
  profile command run, the synthetic demo is generated, the panel serves the
  synthetic demo on loopback and stops, and the 16 public offline tests pass.
- Does **not** show: getting the source from a public GitHub repository (the
  address was not published), the GitHub Actions workflow, other operating
  systems or Python versions, a different machine, or the same Node version as
  CI. It also does not cover real fetching from SEEK, Indeed or LinkedIn,
  installing the macOS LaunchAgent templates, or the correctness of every
  eligibility rule or source. A new virtual environment on this machine is not a
  separate physical machine.

## Earlier record (2026-10-07)

An earlier candidate check, made on 2026-10-07 before any clean history
existed, used macOS 15.7.8, Python 3.12.14 and pip 25.0.1 in a temporary
isolated directory (written below as `<isolated-root>`, a placeholder for
reading; it is not a literal path). A new virtual environment, a source
directory and a demo-output directory were created under it, and only 36
public files were extracted by explicit path from one commit. It ran the CLI
help, the read-only profile command, the synthetic demo and the two install
smoke tests (public configuration and demo), and served the synthetic demo panel
on a loopback port, with all three pages answering 200 and the port released
afterwards. It resolved `python-jobspy 1.2.0` and `PyYAML 6.0.3`, the same
direct versions as above.

That record was reconstructed from retained run logs and is a normalized
description, not a verbatim transcript. It was explicitly **not** a clean-history
fresh-clone acceptance, and it ran neither the full public suite nor CI. It is
kept only as history; the 2026-10-08 verification above is the current one.
