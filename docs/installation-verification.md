# Installation verification

This records dated verifications of AU Job Board's source installation, and what
each does and does not show. Each entry is a historical fact about the named run
and commit; it is not re-run when later changes are made.

## Local fresh clone and new environment (2026-10-08)

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
- Does **not** show: getting the source from GitHub or the GitHub Actions
  workflow (recorded in the next section), other operating systems or Python
  versions, a different machine, or the same Node version as CI. It also does not cover real fetching from SEEK, Indeed or LinkedIn,
  installing the macOS LaunchAgent templates, or the correctness of every
  eligibility rule or source. A new virtual environment on this machine is not a
  separate physical machine.

## GitHub access and cloud CI (2026-10-08)

This entry covers the repository `https://github.com/SkywingJam/au-job-board` at a later commit than the section
above. At that commit the history was two commits: the root commit
(`9386c8e9c5dffbda3e3d45f92641cc090385c9f2`) and one documentation commit,
`9c03c0b1e0d4ebe6c9bb0989b591bfa5b4c7d7ba` (tree
`4f6df1c21ee433b20a0a36adb3f187110d5f436b`, 296 tracked files). The documentation
commit changed six Markdown files, so the per-file SHA-256, byte and blob
identities in the previous section describe the root commit's files, not this
later tree.

- **GitHub access.** The repository was **private** at the time. A clone made
  with the maintainer's authenticated access reported the same HEAD and tree, two
  commits, 296 files and a clean working tree, with no `alternates`. That is an
  authenticated clone, not an anonymous or public one; anonymous access is not
  part of this record.
- **Cloud CI.** The offline workflow was started manually on `main` (event
  `workflow_dispatch`, run 37724352157, commit `9c03c0b1…`) and concluded
  `success`:
  <https://github.com/SkywingJam/au-job-board/actions/runs/37724352157>.
  The runner was `macos-15-arm64` (macOS 15.7.9) with Python 3.12.10 and Node
  24.21.0. Dependencies installed (`python-jobspy 1.2.0`, `PyYAML 6.0.3`), the
  private-path guard passed, and `tools/run_public_tests.py` reported
  **16 passed, 0 failed, 0 not applicable**.
- **A line that looks like a failure.** The fixture-boundary test prints a
  `[FAIL]` line from a deliberate negative control: it feeds a wrong expected
  value to confirm that the check fails when it should. The test as a whole
  exits 0; the outer exit status is what counts, and the run summary above is
  from the entry's own count.

Limits: one runner image and one date; other operating systems, Python versions
and Node versions are not covered, and neither is real fetching or installing
the macOS LaunchAgent templates.

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
kept only as history; the 2026-10-08 records above are the current ones.
