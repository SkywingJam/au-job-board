# Eligibility snapshot (read-only)

The `eligibility` section of the configuration is parsed into a fixed capability
snapshot that the `eligibility-profile` command can print.

> **It does not affect filtering.** Filtering is controlled by the separate
> `legacy_rule_profile` section, described in
> [legacy-rule-profiles.md](legacy-rule-profiles.md). Changing `eligibility`
> does not change filters, scores, salary, de-duplication or stored decisions,
> and the snapshot is not used by `analyze`, the database or the panel. The
> command only parses the configuration and prints JSON; it never opens the
> database or the network.

## What it is, and is not

- **Is:** a stable, validated description of a software capability template,
  readable with one command.
- **Is not:** a determination or inference of anyone's legal eligibility, or proof
  that a person qualifies for a whole job. The presets are templates of this
  project. `null` means unknown or not set, which is not the same as `false`.
  `max_weekly_hours: null` does not mean unrestricted work rights. Nothing infers
  a security clearance from citizenship, and no student-visa hours are built in.

## Configuration

```yaml
eligibility:
  profile: "485"
```

or a custom template:

```yaml
eligibility:
  profile: custom
  custom:
    australian_citizen: false
    permanent_resident: false
    unrestricted_work_rights: true
    requires_sponsorship: false
    security_clearance_eligible: false
    max_weekly_hours: null
```

- `profile` is matched exactly, with no fuzzy or case guessing. The bare YAML
  integer `485` is accepted and normalised to the string `"485"`; other
  non-string values are rejected.
- The only allowed keys are `profile` and `custom`. `custom` must give all six
  fields explicitly; a missing or extra one is an error.

## Templates

Field order is fixed: `australian_citizen`, `permanent_resident`,
`unrestricted_work_rights`, `requires_sponsorship`, `security_clearance_eligible`,
`max_weekly_hours`.

| profile | citizen | PR | unrestricted work rights | requires sponsorship | clearance eligible | max weekly hours |
|---|---|---|---|---|---|---|
| `citizen` | true | false | true | false | null | null |
| `permanent_resident` | false | true | true | false | null | null |
| `485` | false | false | true | false | false | null |
| `student_visa` | false | false | null | null | false | null |
| `custom` | all six fields given explicitly | | | | | |

`null` is unknown or not set, not `false`.

## Value rules

- The first five fields accept only a native boolean or `null`. The strings
  `"true"` / `"false"` and the numbers `0` / `1` are rejected, never converted.
- `max_weekly_hours` accepts a positive, finite number or `null`. Booleans, zero,
  negatives, NaN, infinity and strings are rejected.

## Defaults and errors

- The configuration root must be a mapping. An empty document, `null`, `false`,
  `0`, an empty string, a list or a non-empty scalar is rejected and does not
  fall back to a default.
- **No `eligibility` section:** a valid mapping returns the `485` template with
  `config_source: legacy_default`. This is only a migration default of this
  project and not a statement about anyone's visa rights.
- **An explicit `eligibility` section must be fully valid**, otherwise a
  `ValueError` names the field path: `null`, an empty object, a list, a missing
  `profile`, an unknown profile, a misspelled field, a preset carrying `custom`, a
  `custom` with a missing, extra or mistyped field, or a non-string key.
- Error messages name the field path and the expected and actual types. They never
  echo the original values or the rest of the configuration.
- It never silently falls back to `485`.

## Reading the snapshot

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m jobs.cli eligibility-profile
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m jobs.cli --config config.example.yaml eligibility-profile
```

On success stdout is one JSON object and the exit code is 0:

```json
{
  "profile": "485",
  "capabilities": {
    "australian_citizen": false,
    "permanent_resident": false,
    "unrestricted_work_rights": true,
    "requires_sponsorship": false,
    "security_clearance_eligible": false,
    "max_weekly_hours": null
  },
  "config_source": "legacy_default",
  "filtering_mode": "legacy"
}
```

- The command parses the configuration only. It does not open or create the
  database, write the configuration or vocabulary, or call `analyze` or `fetch`.
- A missing, unreadable or directory configuration path, non-UTF-8 text, a YAML
  parse error, or a schema or type error exits non-zero with a safe message on
  stderr. No success JSON is printed and no raw text or secret is echoed.

## The `filtering` section

`filtering` is separate from `eligibility`. Without it the mode is `legacy`. If
present it must be complete and valid. Today the only accepted value is:

```yaml
filtering:
  mode: legacy
```

An explicit `profile_v1` or any other value is rejected during `analyze` before
the database is opened (`resolve_filtering` in `jobs/config.py`). It is a
configuration error gate, not a switch for an alternative filtering mode.

## Status

- Implemented: configuration parsing and the read-only `eligibility-profile`
  command.
- Not connected to: filtering, scoring, `analyze`, the database or the panel.
- A candidate-by-candidate eligibility parser and assessment were explored and
  then removed. They are not part of the current code and should not be treated
  as a recommended direction.
