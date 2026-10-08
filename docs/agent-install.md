# Agent-assisted installation

This page is a short procedure for an AI coding agent that installs the pipeline
**on behalf of a user**. It is install assistance, not an automatic release, a
production-operations authorization, or permission to fetch listings, schedule
services or expose a panel. The agent follows exactly the same steps as a human
in [install.md](install.md); it does not invent a different implementation.

## Copyable instruction for the user

> Install AU Job Board from source following docs/install.md and
> docs/agent-install.md. The intended repository is
> https://github.com/SkywingJam/au-job-board, which may not be
> published yet. Clone it into a new directory I name only if it is reachable;
> if it returns 404, is unreachable or you lack access, report that the source
> is unavailable and ask me for another source, such as a downloaded copy. Do
> not guess another address, use a private remote, or create or publish a
> repository. Create a project-local virtual
> environment with Python 3.12 and install requirements.txt. Getting the source
> and installing dependencies may both use the network. Keep any existing
> configuration and database untouched. Before guessing any personal setting,
> show me the job keywords, city or region, rule profile and sources options to
> confirm. Verify offline by generating the synthetic demo into a new directory
> that does not exist yet and running the two public tests, then start the normal
> panel only against that generated demo config on 127.0.0.1, confirm the home
> and settings pages answer, and stop it. Report the install path, the start/stop
> commands, which checks passed or failed, and which personal settings are still
> unconfirmed. Once verification and personal configuration are complete, ask
> whether I want a one-off fetch. On macOS, also ask whether I want daily
> automatic updates through launchd and at what local time. Explain that daily
> updates fetch, analyze and write reports. Both options are off unless I
> explicitly choose them; do not treat silence as approval. On other systems,
> do not offer or configure an unverified scheduler by default. Do not expose
> the panel or push anything to GitHub.

## Getting the source

The project is AU Job Board. The intended repository is
`https://github.com/SkywingJam/au-job-board`, but it has not been created or published
(it returned 404 on 2026-10-08), so it may not be reachable.

- If the user already has the source, use that directory's project root (the
  directory containing `requirements.txt` and `jobs/`).
- Otherwise try to clone the repository into a **new** directory the user names:

  ```bash
  git clone https://github.com/SkywingJam/au-job-board <new-directory>
  cd <new-directory>
  ```

  If the address returns 404, is unreachable or you have no access, stop and
  report that the source is unavailable; ask the user for another source, such
  as a downloaded copy. A failed download or clone is a failed step, not a
  success. Do not guess another repository, do not use the maintainer's private
  development remote, and do not create, publish or change the visibility of a
  repository to make the address work. Use the default branch unless the user
  explicitly says otherwise. Clone into a non-existent new directory; do not
  reset, clean or overwrite an existing checkout. Getting the source may use the
  network. Do not push anything to GitHub.

## Inputs

Two kinds of input. Installation parameters have safe defaults; personal
configuration must come from the user and must not be guessed.

### Installation parameters (use the default if not given)

| Input | Default |
|---|---|
| Install location | A new directory the user names; or the project root they already have |
| Python | 3.12 (tested) |
| Verification panel address | `127.0.0.1` on a free port |

State the default you used instead of asking again.

### Personal configuration (ask, do not infer)

Show the concrete options and a recommendation, then let the user choose. Do not
re-ask anything already explicit, and do **not** infer a rule profile or other
personal facts from the request to install.

| Setting | Options / recommendation |
|---|---|
| Job keywords / roles | Free text; recommend a few broad terms such as cybersecurity, software engineer or IT support |
| City / region | e.g. Melbourne VIC, or "All Australia" |
| Rule profile (`legacy_rule_profile.profile`) | `citizen`, `permanent_resident`, `485`, `student_visa`, `custom`. No assumed default: ask which one matches the user's own situation. The public example uses `485` only as syntax. |
| Sources to enable | SEEK and Indeed recommended; LinkedIn optional and slower |

Do not ask about regular expressions, scoring weights or every YAML field.
Interface language and theme are UI settings and can be left to the UI. Actual
fetching and any scheduled task remain the user's separate choice.

If the user has not answered the personal configuration yet, report the offline
install verification as its own finished result and say that the personal
configuration is still pending. Do not fill it in with guessed values.

## Procedure

1. **Inspect, do not modify.** Check the operating system, the available
   `python3.12`, and whether the target directory and any `config.yaml`,
   `skills.local.yaml`, `data/` or `out/` already exist. Report what you find
   before writing anything.
2. **Get the source.** Follow "Getting the source" above. Preserve any existing
   checkout.
3. **Confirm personal configuration.** Ask for the four settings in "Personal
   configuration" if the user has not already given them, showing the options
   first. Do not guess.
4. **Create the environment.** Run `python3.12 -m venv .venv` in the project
   root and install `requirements.txt` with
   `.venv/bin/python -m pip install -r requirements.txt`. Getting the source and
   this step may use the network; install nothing else (no system packages,
   browsers or the video tooling under `media/demo-video/`).
5. **Create configuration only if absent.** Copy `config.example.yaml` to
   `config.yaml` only when `config.yaml` does not already exist, and only once
   the user has confirmed the personal settings. Never overwrite an existing
   configuration, database, reports or vocabulary overlay.
6. **Verify offline.** From the project root, run:
   - `.venv/bin/python -m jobs.cli --help`
   - `.venv/bin/python -m jobs.cli --config config.example.yaml eligibility-profile`
   - `.venv/bin/python -m jobs.demo generate --root out/demo-verify` (choose a
     new directory name that does not exist yet)
   - `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tools/test_public_configuration.py`
   - `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tools/test_demo.py`
   These two are the install smoke check and use only public, synthetic inputs.
   Do not run the full public suite (`tools/run_public_tests.py`) during a
   normal install: it is a contributor/CI entry and needs Node, which the
   product does not. Do not run tests that need a private corpus, and do not
   run `fetch`, `details`, `run` or `analyze` against a real database.
7. **Start the panel against the generated demo and check it.** Use the demo
   config, not the user's `config.yaml`, and loopback only:
   `.venv/bin/python -m jobs.cli --config out/demo-verify/config.yaml panel --host 127.0.0.1 --port 8090`.
   Request `/` and `/settings` and confirm HTTP 200; stop it and confirm the
   port is released. `panel` opens the configured database, enables WAL and
   creates the schema, so do not point this check at a real configuration or
   database. Do not leave a server running.
8. **Offer optional use after verification.** Only once offline verification
   has passed and the user has confirmed the personal configuration, follow
   "Optional fetching and macOS automation" below. Ask only about choices that
   the user has not already made. Declining these options does not make the
   installation incomplete.
9. **Report, then stop.** Give the install path, the exact start and stop
   commands, the resolved Python and dependency versions, a pass/fail line for
   each check, and which personal settings remain unconfirmed. Report whether
   the optional fetch and scheduled updates were declined, left pending or
   explicitly enabled. Do not perform further operations.

## Optional fetching and macOS automation

These choices are separate from installation and offline verification. Keep
both off unless the user explicitly chooses them. If personal configuration is
still pending, report that first and defer these questions.

- **One-off fetch:** on a supported environment, ask whether the user wants to
  fetch listings now using the confirmed sources and settings. State that this
  uses the network and writes to their configured database. Follow the existing
  [usage guide](usage.md) for the chosen operation; do not infer permission for
  other operations from a fetch-only request. Other operating systems remain
  unverified; do not present the macOS result as validation of those systems.
- **Daily automatic updates (macOS only):** ask whether to enable the existing
  `launchd` fetch template and, if so, the desired time in the machine's local
  time zone. Confirm the time zone if it is unclear. Explain that the template
  runs the complete pipeline: fetching, analysis and report generation, not
  just fetching. Do not adopt the example 07:30 time without the user's choice.
  Use [the macOS LaunchAgent guide](launchd-examples.md), rendering paths for
  this installation; preserve existing services and do not enable the separate
  resident-panel service unless requested. Report the selected schedule and
  how to disable it. Enabling the schedule does not authorize an extra immediate
  run.
- **Other operating systems:** skip the default scheduling question. The
  project has no verified scheduler setup for them; if the user asks, explain
  that limit rather than inventing a cron, systemd or Task Scheduler procedure.

A source being enabled in YAML is not permission to fetch immediately. Permission
for one-off fetching does not enable daily updates, and enabling daily updates
does not grant permission to expose the panel or upload data.

## Guardrails

- Do **not** read, copy or return private job descriptions or notes. Use only
  the public example configuration and the synthetic demo.
- Do **not** infer the user's job keywords, city, rule profile or sources, and do
  **not** present a guessed configuration as confirmed.
- Do **not** overwrite existing configuration or databases, and do **not**
  silently migrate or recompute an existing database; an update of an existing
  deployment is a different task.
- Do **not** guess a repository when the intended address is unavailable, use a
  private remote, create or publish a repository, or push configuration or user
  data to GitHub.
- Do **not** escalate privileges, modify system settings, install background
  services, or change product logic, rules or vocabulary.
- Do **not** bypass a platform's terms or anti-bot protections.
- **Fetching listings, enabling a scheduled service (`launchd`), and exposing
  the panel beyond loopback each require the user's explicit choice.** The
  default verification performs none of them.
- Treat the panel as unauthenticated: loopback only unless the user has their own
  protected access layer.

This page prepares an installation; it is not a publication step and does not
authorize production use. The license and third-party boundaries are in
[install.md](install.md) and [LICENSE](../LICENSE).
