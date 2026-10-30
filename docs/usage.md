# Usage guide

How the pipeline behaves today: the workflow, the commands, what is stored where,
how notes and languages work, and where the data in the panel comes from. It
describes what the code does; it is not an installation guide (see
[install.md](install.md)).

Run commands from the project root, or pass the right `--config` path.

## The workflow

```
fetch ──► details ──► analyze ──► report
(network) (network)   (offline)   (offline)
                          │
                          └──► panel (offline): read, label, annotate, follow up
```

Fetching and analysis are decoupled. `fetch` and `details` are the only commands
that use the network; they write the raw listings to the local database.
`analyze` reads the stored text and never touches the network, so you can change
rules or the vocabulary and re-run it in seconds without fetching again.

### What `analyze` does

1. **Hard filters.** A short list of rules decides whether a posting is
   excluded. Examples of what the current rules look for: a named, baseline or
   generic security-clearance requirement, citizen-only or citizen-or-PR wording,
   Indigenous-identified positions and ADF enlistment. One override keeps a
   posting that also accepts a valid work visa. Postings that clearly cannot be
   applied for are excluded, and each exclusion keeps the matched original text
   as evidence. Vague or unknown wording is not automatically treated as unmet.
   The Excluded view lets you check for mistakes.
2. **Scoring.** Jobs get a tier (T1 / T2 / T3) from the platform's classification
   or the title, a level signal from the title, a penalty for stated experience
   requirements, and a salary hint. The total orders the list.
3. **Salary.** Salary from the platform field or, failing that, from the
   description is parsed and annualised where it can be. A missing salary only
   means the source did not state it.
4. **De-duplication.** Cross-platform copies are grouped by normalised company,
   title and city, followed by a fuzzy merge for near-identical titles from the
   same company and city. The panel shows one card per group and lists the
   sources of the duplicates.
5. **Skills.** The description is matched against the skill vocabulary
   (`skills.yaml` plus your `skills.local.yaml`). Skills tag cards and drive the
   skill filter; they do not affect the score.

`analyze` deletes and rebuilds the derived tables (decisions, scores, salary,
de-duplication groups, skills). It never touches your labels.
It shares the database update lock with `run` and panel updates. If another
update owns that lock, standalone `analyze` exits with code 4; retry after that
update finishes.
Conversely, a scheduled `run` that overlaps a standalone `analyze` exits busy
without an automatic retry. Avoid manual analysis around the scheduled time,
or rerun the update after analysis finishes.

### The panel

`panel` serves a local web page with five views:

| View | What it shows |
|---|---|
| Recommended | Unprocessed jobs that passed the hard filters, best score first. Jobs listed for more than 30 days are not recommended. Notes, stars and qualification corrections alone do not count as processed. |
| All | Everything, including excluded jobs, with the full set of filters. |
| New | Jobs first seen in the most recent fetch batch, excluded ones included and marked as excluded. |
| Excluded | Jobs removed by a hard filter, with the rule id and the matched text. |
| Labelled | Jobs with any human record, which can overlap Recommended. The Priority filter selects Want to apply plus an independent priority star. |

Filters include search over title, company, location, notes, tags and
description, salary present, freshness, skills, tier and your own records:
status (including "not processed"), the ★ priority queue, eligibility
correction and label status. Legacy interest records are kept in the database
and API but no longer shown in the panel; old `interest=` links still work.
All, New, Excluded and Labelled also provide one location box for an Australian
postcode, suburb, capital city, state or `Australia`. A capital or its CBD
postcode lists the CBD, then the rest of that city's metropolitan area, then
places that cannot be placed; other places in the state outside the area are
hidden. Results are grouped in blocks with dividers; the recommended view
does not offer location search. See [Locations and records](location-workflow.md).

**More filters → Job details → Ineligible last** (All, New and Labelled; off by
default and remembered per view in this browser) moves jobs whose effective
qualification is ineligible after the rest of the whole result. It uses the same
qualification shown on the card: the rule result, replaced by your
Eligible/Ineligible correction; unknown is not ineligible. It filters nothing
and changes no score or rule. Inside each part the location blocks and your
score/date sort stay in order, and paging runs over the complete ordering.
A correction saved in the detail pane shows at once and keeps the open job and
its previous/next targets; the list re-sorts on the next refresh or when the
query is applied again. Recommended and Excluded keep their own order.
The detail pane shows the original description, the extracted evidence sentence
for eligibility-related requirements, and a link to the original posting.

**Previous / next** buttons in the detail pane follow the page's list order.
Their keyboard shortcuts are chosen in **Settings → Display → Keyboard
shortcuts**: Default `←` / `→`, Vim `K` / `J`, or Gamer `A` / `D`. Only the
chosen set works, and the button hints show it. Keys typed in text fields,
menus, the location search box and open dialogs or drawers never switch jobs, and
unsaved or saving notes are protected as with the buttons.

On a narrow (portrait) screen, a one-finger swipe to the right on the job text
goes back to the list like the **Back** button: it does not need to start at the
screen edge, but must travel at least 80 px, mostly sideways, in under 0.8 s,
and not start in the leftmost 24 px (left to the browser's own back gesture).
Vertical scrolling, left swipes, short or slow drags, pinch zoom, selected text,
buttons, links, fields, horizontally scrolling content and open drawers or
dialogs are never treated as a swipe back, and a mouse on a desktop layout is
unaffected. Saving notes are awaited and unsaved notes ask first, exactly as
with the button; the list keeps its filters and scroll position.

**Copy job** in the detail header copies plain text for pasting into another
tool such as an AI assistant: UID, title, company, listing location, listing
link and the complete stored description, not the
on-screen rendering. Copying changes no job or label. If
the browser blocks clipboard access (for example over plain HTTP from another
device), a dialog shows the same text selected for manual copying.

**Settings → Fetching & updates** shows current configuration in each region,
with saved changes in a dashed next-run region. The rule profile is part of the
legacy rule-switch region. **Update jobs**
starts the full pipeline in the background and shows its outcome. The homepage
icon beside the last-fetch time starts the same pipeline. New results load when
no job is open; open details and unsaved notes stay protected. Updating uses the
network; opening the panel
does not. See [fetch settings and manual updates](manual-updates.md) for config
timing, partial failures and concurrency limits.

## Labels, notes and follow-up

Choose one main status. Only a saved status marks a job as processed; opening
a job or adding other information does not.

In the panel, the detail pane (a bottom sheet on narrow screens) puts the three
statuses and a separate star on one row when space permits; the star wraps on
very narrow screens so translated status labels stay readable. A state such as
"Processed: To apply · ★ Priority". The note follows. Eligibility correction and
any legacy interest or unsure records are folded into one section below; its
summary shows the current result and whether it comes from the analysis or a
manual correction. A machine result is outlined with a dashed border and a
manual correction is filled. Cards show the status and star; they also show a
correction tag when it contradicts the analysis. In Recommended, jobs processed
during the current visit stay in place. Applied and Skipped cards are dimmed;
changing a card to To apply restores its normal brightness.

| Axis | Values | Meaning |
|---|---|---|
| Status | saved, applied, skipped | Want to apply (shown as “To apply”), Applied or Skipped. Setting `applied` records `applied_at`. Click the current status again to clear only progress. |
| Priority | true, false | Independent star. Only starred `saved` jobs enter the priority queue; other statuses retain the star. |
| Qualification correction | eligible, ineligible | Optional manual override of the displayed analysis result. New writes do not accept unsure. |
| Legacy interest | want, maybe, no | Preserved for history, export and CLI compatibility; absent from the main controls. |

Qualification controls start collapsed. Expanding them or clicking the selected
machine result writes nothing. Choosing the other result records a manual
correction with the original analysis, rule version and evidence. Clicking that
correction again removes the current override while preserving history. Missing
analysis stays unknown. An unexcluded result is not a guarantee that you meet
all requirements. Old `unsure` values remain readable and are not guessed into
either binary result. Re-analysis does not remove manual corrections.

Status changes keep the current job open. The Recommended pool is frozen at
entry using an event watermark; refresh or enter a new session to rebuild it.

### Marking several cards at once

Cards show no checkbox until you start selecting. On a computer, Cmd/Ctrl+click
on a card (or Cmd/Ctrl+Enter on a focused card) starts selecting; on a touch screen
a long press (about half a second without moving) does. In selecting mode every
card shows a circle on its left (click it, or Space/Enter on it, to select without
opening the job), and the search and filter row is replaced by a bar with the
number selected, a **Mark as…** menu (**To apply**, **Applied**, **Skipped**; choosing one saves it at once),
**Set star** and **Cancel**. Search text and filters you had are untouched and
return when selecting ends. The menu and star are disabled while nothing is
selected or a save is running; **Cancel** always works.

How selecting ends depends on the layout. On a phone-sized (single-column) layout,
tapping a card or its circle toggles it instead of opening the job; tapping
elsewhere, scrolling or pressing Esc does not leave, and unselecting every card
keeps you in selecting mode ("0 selected", actions disabled) so you can pick again;
only **Cancel** ends it. On a wide layout, selecting ends when you cancel the last
card, press Esc, click blank space or another control, or click a card normally
(which opens the job). Resizing from phone-sized to wide ends an empty selecting mode
and keeps any selection. Selections are never kept across pages. A newer result set
waits while you are selecting, including with 0 selected, and loads after you cancel.

Each action is applied to every selected card in one all-or-nothing save: a status
choice sets exactly that status (replacing any other) and **Set star** only sets
the star; neither touches the other axis, notes or qualification. Repeating the
same action changes nothing. After a successful save the selection stays so you
can add a star or correct the status; cards are not removed or reordered, and the
card marks, header counts and an open job's status buttons update. If the save
is rejected, nothing is written and the selection stays for retry. If the connection
fails before confirmation, the panel reports an unknown save outcome; retrying the
same assignment is safe and does not duplicate label events. At most 100
cards can be selected, only from the list currently loaded. There is no select
all, cross-page selection, range selection, unstar or clear-status action here;
use the job's own controls for those.

On a wide layout, clicking a plain card or a navigation link keeps the selection
until the destination is really entered, so a cancelled "unsaved changes"
confirmation leaves it as it was. A save already sent is not undone by leaving
selecting mode; its late result only updates the marks and never reopens it.

Plus a free-form note. Every change is also appended to a history table
(`label_events`), so you can check what changed and restore a label by hand from
that history. There is no automatic undo or replay. Labels are
the one thing in the database that cannot be regenerated by fetching again.

Within a duplicate group, each displayed field follows its latest recorded
edit, including an explicit clear or a repeated choice. Changing another field
or tidying note separators does not make an older value current again. This
does not rewrite labels on the other source records. Older fields without edit
history use their existing label timestamp; unknown historical intent cannot
be reconstructed.

`label` is the command-line equivalent (`--list`, `--history`, `--export`,
`--find`, `--clear`, and `--tidy-notes` which previews note formatting changes
and only writes with `--apply`). Use `--priority` / `--no-priority` to set the
star, `--clear-action` to clear only progress, or `--clear-eligibility` to remove
only the qualification override. `--clear` still removes the entire label row.

The first database connection upgrades an older database in a transaction.
Existing progress, application dates and history are preserved. The complete
stored Chinese note tag `优先投递` moves to the priority field and is removed
from the note; other text stays. Similar phrases and English custom notes do
not migrate. The upgrade runs once, so a cleared star cannot reappear from an
old tag. Back up your database before upgrading; a code downgrade alone is not
a database rollback.

### Note tags

- A note is a sequence of tags separated by `;` or `；`. On save it is normalised
  to the stored form `tag；tag；`.
- The panel offers a list of default tags (location, role quality, seniority,
  progress, eligibility and personal conditions, positives). The defaults are
  **stored as fixed Chinese values**. In the English interface they are only
  *displayed* with English names. Anything you type that is not exactly one of
  the defaults is stored and shown as you wrote it. It is never translated or
  guessed.
- Priority uses its independent star, not note text. Typing a priority-related
  phrase into a note does not change the star or the apply queue.
- Notes are free text. Do not assume the tool can recognise or anonymise people,
  employers or other identifying details you write into them.

### Languages and themes

The interface language (English or Chinese), the theme (light or dark) and the
keyboard shortcut set are stored in browser cookies (`jobs_lang`, `jobs_theme`,
`jobs_keys`), not in the database or configuration file.
Switching them rewrites nothing you stored. The language is taken from the
cookie, then the browser's `Accept-Language`, then defaults to Chinese. Job text,
original evidence sentences and skill names are never translated.

## Commands

| Command | Network | What it does |
|---|---|---|
| `fetch` | yes | Collects listings and inserts or updates the `jobs` table |
| `details` | yes | Fetches SEEK descriptions for the detail queue |
| `analyze` | no | Clears and rebuilds `decisions` and `scores`, plus salary, de-duplication and skill tables; never touches `labels` |
| `report` | no | Writes CSV and Markdown files under the report directory |
| `audit` | no | Prints a sample of excluded listings; also opens or creates the database |
| `status` | no | Prints database counts; also opens or creates the database |
| `label` | no | Reads and writes `labels`; `--export` also writes files under `out/` |
| `skills` | no | `list` reads the vocabulary; `add`, `alias` and `remove` write `skills.local.yaml` |
| `panel` | no | Serves the local panel; the panel can write labels and vocabulary |
| `eligibility-profile` | no | Prints JSON; does not open the database |
| `run` | yes | `fetch`, then `details`, then `analyze`, then `report` |

Notes:

- `--config` is a global option and goes **before** the subcommand, for example
  `python -m jobs.cli --config my-config.yaml status`.
- `analyze` is offline but **not** read-only: it deletes and rebuilds derived
  decisions and scores. Your labels and notes are kept.
- `report` is offline but writes files.
- Any command that opens the database creates and initialises it (schema only)
  if it does not exist. `eligibility-profile` never opens it.
- `run` is the only combined command that uses the network.

A typical session, with your own configuration path:

```bash
CONFIG=/path/to/your/config.yaml

python -m jobs.cli --config "$CONFIG" fetch      # network
python -m jobs.cli --config "$CONFIG" details    # network
python -m jobs.cli --config "$CONFIG" analyze    # offline
python -m jobs.cli --config "$CONFIG" report     # offline, writes files
# --host forces loopback for this run. The panel has no authentication.
python -m jobs.cli --config "$CONFIG" panel --host 127.0.0.1 --port 8090
```

The optional LaunchAgent templates that run these on a schedule are described in
[launchd-examples.md](launchd-examples.md).

## Changing the rule profile

The profile is set in the `legacy_rule_profile` section of your configuration and
only selects which existing rules run. Settings → Fetching & updates can save
this profile for the next run. The legacy rule-switch region shows the current
profile first and any saved next-run changes after it. Saving edits changes the next-run
configuration without reprocessing existing results.

To apply a change you run `analyze` yourself. That command does not fetch new
jobs and keeps your labels and notes. A scheduled `run` also analyses, so later
scheduled runs read the new profile. The details, the six presets and their
limits are in [legacy-rule-profiles.md](legacy-rule-profiles.md).

## Local state and data

Everything below is local to the machine running the tool.

| Item | Where | Notes |
|---|---|---|
| Configuration | your `config.yaml` (start from `config.example.yaml`) | Selects sources, paths, panel address and rule profile. See [public-configuration.md](public-configuration.md). |
| Rules | `rules.yaml` | Hard filters and scoring. Changing them does not require fetching. |
| Vocabulary | `skills.yaml` (seed) and `skills.local.yaml` (your additions, aliases and removals) | The panel and `skills` write only the local overlay, never the seed. |
| Database | `db_path`, default `data/jobs.db` | Raw listings and descriptions, derived tables, labels and label history. |
| Reports | `report.out_dir`, default `out/` | CSV and Markdown output. |

The repository's `.gitignore` excludes `data/`, `out/` and `*.db`. Ignoring a
path does not remove content that was already committed.

Treat the database, reports, labels, notes, your configuration and your
vocabulary overlay as private data. Do not commit or share them.

### Where each piece of data comes from

Provenance is kept so a result can be traced back and checked:

- **Listings.** Each job has a `uid` of the form `source:source_id`, the source,
  the posting `url`, `first_seen_at` / `last_seen_at`, the raw description, when
  the description was fetched, and the raw source payload. The raw description is
  kept permanently; that is what makes re-analysis offline.
- **Exclusions.** Each decision records the rule id, whether it was an exclude or
  override rule, the matched text and the rules version. The panel shows the rule
  and the quoted sentence from the original text.
- **Scores and salary.** Scores keep their reasons. A parsed salary records
  whether it came from the platform field or from the description, and the raw
  string.
- **Labels.** Each label records the rules version in force when you made it.

The text itself belongs to the platforms and advertisers that published it; see
[safety-and-sources.md](safety-and-sources.md).

### Offline demo versus real fetching

The demo and the screenshot library use fictional postings with links to
`https://example.invalid/...`. They never contact any platform. Commands that
fetch (`fetch`, `details`, `run`) contact the sources enabled in your
configuration. Reading the configuration or starting the panel never fetches.

## Known limitations

- The hard filters and the eligibility evidence are English pattern matching.
  Some wordings will be missed, and some will be matched wrongly.
- Experience, level and direction signals are heuristics and can misjudge a
  posting.
- Duplicate detection can miss copies with very different titles or company
  names and can occasionally merge different jobs.
- Rule profiles are rule switches only; see
  [legacy-rule-profiles.md](legacy-rule-profiles.md).
