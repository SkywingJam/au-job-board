# Configuration

`config.example.yaml` is the starting point for your own configuration. It holds
generic, editable values with English comments and no personal preferences,
statistics, network addresses or absolute private paths. This page explains what
each part controls, how paths are resolved, and which files stay private.

Filtering and scoring rules are not in the configuration. They live in
`rules.yaml`.

## Creating your configuration

Copy the example only for a **new installation that has no `config.yaml` yet**.
This command checks first and never overwrites:

```bash
if [ -e config.yaml ]; then
  echo "config.yaml already exists; leaving it untouched" >&2
else
  cp config.example.yaml config.yaml
fi
```

Never overwrite an existing configuration. Point commands at it with an explicit
`--config` path instead.

- There is **no automatic fallback** from a missing `config.yaml` to
  `config.example.yaml`, and there is no environment-variable layer. Pass the file
  you mean with `--config`.
- `--config` is a global option and goes **before** the subcommand, for example
  `python -m jobs.cli --config my-config.yaml status`.

## Paths

- `db_path` defaults to `data/jobs.db` and `report.out_dir` defaults to `out/`
  when absent.
- Absolute values are used as they are. Relative database and report paths are
  anchored to the project root (`jobs.config.ROOT`), **not** to the directory that
  contains the `--config` file.
- A relative `--config` path itself is resolved against the current working
  directory.

## Sections

| Section | Controls |
|---|---|
| `db_path` | The SQLite database. |
| `queries` | Search terms used by every source. The example terms are generic; replace them with the roles you want. |
| `seek` | Enable flag, locations, pages per query, date range and the pause between detail requests. |
| `jobspy.indeed`, `jobspy.linkedin` | Enable flag, location and number of results. LinkedIn also has `fetch_description`. |
| `report` | Number of top jobs and the output directory. |
| `panel` | Host, port, page size and the Recommended-view calibration sampling. |
| `filtering` | Must be `mode: legacy`. See [eligibility-profiles.md](eligibility-profiles.md). |
| `legacy_rule_profile` | Which existing hard-filter rules run. See [legacy-rule-profiles.md](legacy-rule-profiles.md). |
| `eligibility` | A read-only capability snapshot for the `eligibility-profile` command. It does not affect filtering. |

### Sources

SEEK and Indeed are enabled in the example and LinkedIn is disabled. Each source
has its own `enabled` flag and can be switched off independently. The
integrations are unofficial and best-effort; see
[safety-and-sources.md](safety-and-sources.md) before enabling them.

Reading the configuration or starting the panel never fetches anything. Only
`fetch`, `details` and `run` use the network.

### Rule profile and eligibility snapshot

Two sections mention profiles and they do different things:

- `legacy_rule_profile` is the one that matters. `profile` is `citizen`,
  `permanent_resident`, `485`, `student_visa` or `custom`, and the optional
  `exclude` / `overrides` mappings switch individual rule ids on or off. A missing
  section keeps every rule on. The example uses `profile: "485"` (all rules on).
  `citizenship.requirement` is a mixed rule that stays on for every preset, so
  citizen and PR presets can still exclude jobs.
- `eligibility` is only read by the read-only `eligibility-profile` command. The
  example sets `profile: citizen` purely to show its syntax. Choose the value that
  matches your own situation and do not copy the example's value by default.

Editing either section does not update results that are already stored. See
[Changing the rule profile](usage.md#changing-the-rule-profile) for how to apply a
change.

### Panel

The panel has **no built-in authentication**. The example binds it to
`127.0.0.1`. Read [safety-and-sources.md](safety-and-sources.md) before using any
other address.

## Read-only checks

These never open the database or the network:

```bash
# Parse the example and print one scalar
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c \
  "from jobs.config import load_config; print(load_config('config.example.yaml')['db_path'])"

# Print the eligibility snapshot using the example as an explicit --config
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m jobs.cli \
  --config config.example.yaml eligibility-profile
```

The second command prints one JSON object and exits 0. Do not use `run`, `fetch`,
`details` or `analyze` against a production database as a smoke test.

## Files that stay private

Keep these out of anything you share or publish:

- your real `config.yaml`;
- `.env` files, credentials and tokens;
- the SQLite database (`*.db`, `data/`) and the report directory (`out/`);
- your vocabulary overlay `skills.local.yaml` and your labels and notes.

Ignoring a path in Git does not remove content that was already committed, and it
does not erase it from existing history. Which files a public release will ship
is decided separately and is not defined by this page.
