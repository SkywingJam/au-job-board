# Windows source installation (candidate)

The native Windows compatibility candidate targets **Windows x64, Python 3.12
and a local NTFS directory**. Windows runtime acceptance is pending. A successful
macOS test or simulated Windows branch does not establish Windows support.
Windows Server 2022 and Windows 11 acceptance must be recorded separately.

Use an ordinary user account. Normal use needs no administrator rights, Docker,
WSL, Node, OpenSSL, service installation or PowerShell execution-policy change.
Keep the panel on loopback. Source adapters still depend on the recruitment
platforms and their access policies; compatibility does not guarantee scraping.

## Install in PowerShell

Run from the repository root. If obtaining a fresh copy, clone the repository
and select the candidate revision supplied for testing. Do not replace another
installation or its private files.

```powershell
git clone https://github.com/SkywingJam/au-job-board au-job-board
Set-Location au-job-board
# For a development test, select the exact supplied candidate before proceeding.
py -3.12 -m venv .venv
$python = (Resolve-Path .venv\Scripts\python.exe).Path
$env:PYTHONUTF8 = '1'
& $python --version
& $python -m pip install -r requirements.txt
if (-not (Test-Path config.yaml)) { Copy-Item config.example.yaml config.yaml }
& $python -m jobs.cli --help
& $python -m jobs.cli --config config.example.yaml eligibility-profile
```

Calling the venv interpreter directly avoids activating a PowerShell script.
`requirements.txt` installs Windows timezone data. The repository's attributes
keep the hashed location reference in LF format even with Git's default Windows
line-ending conversion; do not change global Git settings. UTF-8 mode also keeps
Chinese text and redirected logs readable. Repeat the environment setting in a
new shell if needed.

Edit `config.yaml` for queries, source switches, location and rule profile as
in [installation](install.md). Configuration paths use the project root; pass
`--config` before the subcommand. Keep your existing `skills.local.yaml` overlay;
a missing overlay is empty and the panel creates it when you save vocabulary.

## Check offline, then use the panel

Choose a new demo directory each time. This creates only fictional postings;
it does not fetch or use your normal database.

```powershell
& $python -m jobs.demo generate --root out/demo-windows-verify
& $python tools/test_public_configuration.py
& $python tools/test_demo.py
& $python -m jobs.cli --config out/demo-windows-verify/config.yaml panel --host 127.0.0.1 --port 8090
# Open http://127.0.0.1:8090/ and /settings; Ctrl+C stops the panel.
```

For normal use with your selected sources, the following command **uses the
network** and writes your configured database and report directory:

```powershell
& $python -m jobs.cli --config config.yaml run --timeout 1800
& $python -m jobs.cli --config config.yaml panel --host 127.0.0.1 --port 8090
```

The panel's Update action runs the same pipeline in the background. One
persistent database lock excludes other updates, analysis and settings saves
where required. Closing the application or a timeout stops its Windows worker
process tree. Ctrl+C interrupts a foreground pipeline and permits a later retry;
an abruptly terminated process is recovered as interrupted when state is next
read. Successful completion and refresh require the durable terminal result.
Do not remove a lock file to force an update.

## Optional API/MCP token

The default installation needs no MCP SDK. To enable it, install the optional
requirements and create a private token **without printing it**. This refuses
to overwrite an existing token and restricts its ACL before writing content:

```powershell
& $python -m pip install -r requirements-mcp.txt
& $python -c "from pathlib import Path; import secrets; from jobs.file_security import set_private; p=Path('data/agent.token'); p.parent.mkdir(parents=True,exist_ok=True); f=p.open('x',encoding='ascii'); set_private(p); f.write(secrets.token_urlsafe(48)); f.close()"
```

Then use the [MCP configuration](mcp.md#streamable-http-with-the-application-recommended)
or [standalone HTTP API](agent-api.md). The API and client validate Windows file
ACLs and reject unexpected owners or additional principals. An unsupported
filesystem or failure to inspect/protect permissions is an error; running as
administrator is not the remedy. Copying a token from another machine may give
it unsuitable inherited permissions; create an appropriate local token instead.

## Contributor acceptance

Node is needed only for the full test entry. OpenSSL is needed by optional TLS
tests, not normal use. With those development tools installed:

```powershell
& $python tools/run_public_tests.py --list
& $python tools/run_public_tests.py --with-mcp
```

The same entry is intended for local Windows and a future Windows CI job.
[Public tests](public-tests.md) documents platform-specific cases. The native
Windows runtime checks cover inherited lock handles, real process-tree timeout,
forced supervisor exit, recovery and saved synthetic notes. POSIX-only signals
and file-descriptor assertions retain their POSIX coverage. Record native ACL,
ordinary-account install, panel update/save/restart and Ctrl+C results on the
exact candidate before treating Windows compatibility as accepted. No Windows
CI job, scheduled task or production service is installed by these instructions.
