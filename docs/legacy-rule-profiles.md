# Rule profiles

A profile chooses which of the existing hard-filter rules `analyze` runs. It is a
software switch, not an assessment of your legal eligibility to work in
Australia.

## What a profile is, and is not

- **Is:** a switch per rule. Every existing rule starts on. A preset turns off a
  few entries, and you can then override any rule by its id. The selected rules
  are handed to the unchanged filter engine.
- **Is not:** a legal eligibility judgement. A profile only decides whether an
  existing rule runs. It does not change any rule's regular expression, the
  matching order or the override semantics, and it adds no new extractor or
  capability model.
- **Mixed rules stay on.** `citizenship.requirement` is a mixed rule and is
  enabled for every preset. So even with `citizen` or `permanent_resident`, a job
  can still be excluded by an overlapping condition in that rule, and turning off
  `citizenship.or_pr` does not guarantee that every PR-friendly job is kept. You
  can switch the mixed rule off explicitly, but doing so is blunt rather than
  precise.

## Configuration

The section is called `legacy_rule_profile` and is independent of the older
`eligibility` section (see [eligibility-profiles.md](eligibility-profiles.md)):

```yaml
legacy_rule_profile:
  profile: "485"        # citizen / permanent_resident / 485 / student_visa / custom
  exclude:              # optional; overrides the preset by rule id; true = rule runs
    citizenship.requirement: true
  overrides:            # optional; true = the override is considered
    visa.or: true
```

- **No section:** everything is on, which reproduces the original behaviour. The
  old `eligibility` section is never read for this, so an older configuration does
  not silently enable a preset.
- **An explicit section must contain `profile`.** Only `profile`, `exclude` and
  `overrides` are allowed. The two mappings can be omitted or be objects (`{}` is
  fine). Their values must be real booleans. An explicit `null` is not the same as
  omitting a key and is rejected.
- The string `"485"` and the bare YAML integer `485` are both accepted. Any other
  wrong type, unknown profile, unknown field or unknown rule id is rejected. Error
  messages name the field path and do not echo configuration values.
- `exclude` and `overrides` are separate namespaces. The same rule id may appear in
  both.

## Presets

Presets only act on ids that exist in your `rules.yaml`. A preset id that is
missing is not an error and nothing is invented. A rule id that no preset lists
stays on.

| Profile | Exclude rules switched off by default | Overrides switched off by default |
|---|---|---|
| `citizen` | `citizenship.exclusive`, `citizenship.or_pr` | none |
| `permanent_resident` | `citizenship.or_pr` | none |
| `485` | none | none |
| `student_visa` | none | none |
| `custom` | none; you adjust rule by rule | none; you adjust rule by rule |

The current `rules.yaml` has eight exclude rules and one override:

- exclude: `clearance.named`, `clearance.baseline`, `clearance.generic`,
  `citizenship.exclusive`, `citizenship.or_pr`, `citizenship.requirement`,
  `indigenous.identified`, `adf.enlistment`;
- override: `visa.or`.

Things the presets deliberately do not do:

- The clearance rules, `indigenous.identified` and `adf.enlistment` stay on for
  every preset. Holding a citizenship or visa does not imply a held clearance or
  any other qualification.
- `student_visa` and `485` have the same switches. That is a statement about the
  software, **not** that the two have the same work rights. There is no hours
  logic.
- `visa.or` keeps the semantics of the original global override: it can rescue
  other matches too. The profile only gives you its on/off switch; it does not
  change its scope or priority.

## Examples

Use `citizen`, but also switch off `citizenship.requirement`:

```yaml
legacy_rule_profile:
  profile: citizen
  exclude:
    citizenship.requirement: false
```

Use `citizen`, but turn `citizenship.exclusive` back on:

```yaml
legacy_rule_profile:
  profile: citizen
  exclude:
    citizenship.exclusive: true
```

Control each rule yourself (`custom` starts with everything on):

```yaml
legacy_rule_profile:
  profile: custom
  exclude:
    citizenship.exclusive: false
    citizenship.or_pr: false
  overrides:
    visa.or: true
```

Switch off the global `visa.or` override, so matches it used to rescue stay
excluded:

```yaml
legacy_rule_profile:
  profile: 485
  overrides:
    visa.or: false
```

## Applying a change

1. **Edit the configuration.** Nothing already stored changes, and a running panel
   does not reload it.
2. **Run `analyze` yourself.** It reads the stored descriptions, rebuilds the
   derived decisions, scores, salary, de-duplication and skill tables, **does not
   fetch** anything, and **keeps your labels and notes**. Scheduled `run` jobs
   also analyse, so later scheduled runs read the new profile.
3. **Check the result.** Settings → language and display shows the profile used by
   the last successful analysis. It is read-only: there is no switch and no
   automatic recalculation. Reports show the same recorded profile, and an older
   analysis that recorded none is shown as "not recorded". The jobs panel itself
   does not display the per-rule switch summary.

`analyze` validates the section before it opens the database, so an invalid
profile, a non-boolean switch, an unknown rule id or a `null` mapping fails
safely without touching your data. Scoring, salary, de-duplication and skills
always use the full original rules; the profile only affects which rules
may exclude a job.

## For developers

`jobs/legacy_rule_profiles.py` exposes three pure functions. They do not read
files, the database or the network, do not mutate their inputs, and return
JSON-serialisable values with no regular expressions, descriptions or job text:

```python
from jobs.legacy_rule_profiles import (
    resolve_rule_profile, select_profile_rules, evaluate_profile_rules,
)

resolution = resolve_rule_profile(config, rules)    # effective switches and limits
selected = select_profile_rules(config, rules)      # deep copy without disabled entries
decision = evaluate_profile_rules(text, config, rules)  # calls filters.evaluate
```

`select_profile_rules` removes only the disabled entries. Rule content, order,
`version` and the other top-level fields are unchanged and no `enabled` field is
added. `evaluate_profile_rules` returns the original `filters.evaluate` shape and
does not normalise `text` beyond what that engine does.

## Known limits

- The switches only choose which existing rules run. They are not an individual
  legal eligibility determination.
- Mixed and overlapping rules (`citizenship.requirement`, and `visa.or` as a
  global override) remain blunt. This module does not fix false positives or
  false negatives in `rules.yaml`.
- Presets do not infer a held clearance, an hours cap or other work-rights
  detail from an identity.
