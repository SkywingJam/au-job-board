# Installation

This guide sets up the pipeline from source for a **new** installation. Run the
commands from the project root: the directory that contains `requirements.txt`
and the `jobs/` package. It does not overwrite an existing configuration or
database.

## Tested environment

- **Python 3.12** (3.12.4 in the 2026-10-08 verification).
- **macOS 15** (15.8, arm64, in the 2026-10-08 local verification; the GitHub
  Actions runner was macOS 15.7.9 with Python 3.12.10 and Node 24.21.0).
- Linux, Windows, other macOS versions, other Python versions and other machines
  are **not** verified. See [installation-verification.md](installation-verification.md).

Getting the source (step 1) and installing dependencies (step 3) may both use
the network. The offline checks and the synthetic demo in step 6 do not fetch
anything.

## 1. Get the source

You need the project root: the directory that contains `requirements.txt` and
the `jobs/` package.

- If you already have the source (cloned, downloaded or extracted), use that
  directory's project root.
- Otherwise clone the repository into a **new** directory:

  ```bash
  git clone https://github.com/SkywingJam/au-job-board au-job-board
  cd au-job-board
  ```

  If the address is unreachable, returns 404 or you have no access, stop: the
  source is not available from there. Obtain it another way, for example a
  downloaded copy from the project owner. Do not guess another repository and do
  not use a private development remote. Use the repository's default branch
  unless your own instruction says otherwise.

Clone into a new directory; do not reset, clean or overwrite an existing
checkout. Cloning may use the network.

## 2. Create a virtual environment

Use a Python 3.12 interpreter. On the tested machine the commands are:

```bash
python3.12 -m venv .venv
.venv/bin/python --version        # confirm 3.12.x
```

You can either activate the environment (`source .venv/bin/activate`) or keep
calling `.venv/bin/python` explicitly as this guide does.

## 3. Install dependencies

This step uses the network to download packages.

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip list | grep -E "python-jobspy|PyYAML"
```

`requirements.txt` pins no exact versions. The 2026-10-08 verification resolved
`python-jobspy 1.2.0` and `PyYAML 6.0.3`; the main resolved versions are in
[installation-verification.md](installation-verification.md).

## 4. Create your configuration

`config.example.yaml` is the starting point. **Only copy it when you do not
already have a `config.yaml`.** An existing deployment must keep its own file.

```bash
if [ -e config.yaml ]; then
  echo "config.yaml already exists; leaving it untouched" >&2
else
  cp config.example.yaml config.yaml
fi
```

Then edit `config.yaml`:

- `queries` and `seek.locations` / `jobspy.*.location` for the roles and
  places you want;
- the `enabled` flag of each source (SEEK and Indeed are on in the example,
  LinkedIn is off);
- `legacy_rule_profile.profile` for your situation (step 5);
- `panel.host` / `panel.port` only if you need to (keep the host on loopback).

The shared skill vocabulary is `skills.yaml`. You do not need to create
anything for it: a missing `skills.local.yaml` is treated as empty. To
pre-seed your own additions, aliases or removals, copy
`skills.local.example.yaml` to `skills.local.yaml` (only if it does not
exist); the panel and the `skills` command also write that overlay. Never
overwrite an existing overlay.

Paths in the configuration are relative to the project root, not to the
directory containing the config file. `--config` is a global option and goes
**before** the subcommand.

## 5. Choose a rule profile

`legacy_rule_profile.profile` is one of `citizen`, `permanent_resident`,
`485`, `student_visa` or `custom`. It only selects which existing rules run.

- It is a **software rule switch, not a legal eligibility determination**.
- Changing it **does not update stored results by itself**. Re-run `analyze`
  to apply it; that command is offline and keeps your labels.
- The separate `eligibility` section in the same file is read-only and does
  not affect filtering or scoring.

See [legacy-rule-profiles.md](legacy-rule-profiles.md) and
[eligibility-profiles.md](eligibility-profiles.md).

## 6. Verify the install offline

These checks need no network and do not touch your configuration or a real
database. Generate the synthetic demo into a **new** directory that does not
exist yet. This guide uses `out/demo-verify`; if it already exists, choose
another new name. The generator refuses to overwrite an existing directory.

```bash
# Parse the public example and print the read-only eligibility snapshot (no DB)
.venv/bin/python -m jobs.cli --config config.example.yaml eligibility-profile

# Generate the synthetic demo into a new directory
.venv/bin/python -m jobs.demo generate --root out/demo-verify

# Run the two public, synthetic test entries (the install smoke check)
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tools/test_public_configuration.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tools/test_demo.py
```

Those two checks are the install smoke test. The full public suite
(`tools/run_public_tests.py`) is a separate entry for contributors and CI: it
needs Node and is not required for a normal installation. See
[public-tests.md](public-tests.md).

Then check the panel against **the generated demo database**, not your own
`config.yaml`:

```bash
.venv/bin/python -m jobs.cli --config out/demo-verify/config.yaml panel --host 127.0.0.1 --port 8090
# open http://127.0.0.1:8090/ and http://127.0.0.1:8090/settings, then Ctrl-C
```

The demo is 19 fictional postings with no scraping and with links to
`example.invalid`. See [demo.md](demo.md). Do not use your own `config.yaml`
for this check: `panel` opens the configured database, enables WAL and creates
the schema if it is missing, so a check against a real configuration and database
is not a no-private-access verification.

## 7. Start and stop the panel for normal use

This is the normal-use command with **your own** `config.yaml` and database, not
the offline verification in step 6. To use your own listings, collect them
first. `fetch` (and `details`) are the network steps; `analyze` is offline. See
[usage.md](usage.md).

```bash
.venv/bin/python -m jobs.cli --config config.yaml fetch      # network
.venv/bin/python -m jobs.cli --config config.yaml analyze    # offline
.venv/bin/python -m jobs.cli --config config.yaml panel --host 127.0.0.1 --port 8090
```

Open <http://127.0.0.1:8090>. Stop the server with Ctrl-C. The panel has **no
built-in authentication**; keep it on the loopback address unless you add your
own protected access layer.

## 8. Optional macOS automation

The LaunchAgent templates in `launchd/` can schedule a run or keep the panel
running. They are **templates only** and are not installed by this guide; the
renderer and the manual steps are in
[launchd-examples.md](launchd-examples.md). Do not enable a scheduled run or
remote access unless you choose to.

## Where your data lives

Your configuration (`config.yaml`), your vocabulary overlay
(`skills.local.yaml`), the database (`data/`) and reports (`out/`) are
private to your machine. Do not commit or share them. See
[public-configuration.md](public-configuration.md) and
[safety-and-sources.md](safety-and-sources.md).

## License

This project is released under the MIT License; see [LICENSE](../LICENSE)
(Copyright (c) 2026 SkywingJam). Third-party dependencies such as
`python-jobspy` and `PyYAML` keep their own licenses, and job listing text
belongs to the platforms and advertisers that published it. The MIT license
covers this project's own code and documentation, not third-party packages or
listing data. The install does not depend on the video-production project under
`media/demo-video/`.
