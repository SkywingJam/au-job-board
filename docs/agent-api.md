# HTTP access for an external agent (read-only by default)

This is a separate, opt-in service. It reads the same source records, human
states, geographic classifications, filters and sorting used by the panel.
It does not include an LLM, schedule reports, fetch jobs or edit configuration,
and by default it accepts no labels. An [optional batch label write](#optional-batch-label-write)
for application progress and stars exists but is disabled until explicitly
configured. The panel remains a separate service with its existing exposure
policy; this token does not add authentication to the panel.

An optional [MCP](mcp.md) exposes these reads (and, only when enabled, the
batch label write) to stdio or Streamable HTTP MCP clients through a thin
adapter, using the same API and shared snapshot settings.

Docker users can start the optional `api` Compose profile at
`http://127.0.0.1:8092/api/v1/jobs`; follow the
[container setup](docker.md#optional-http-api-and-mcp) for token creation,
persistent paths and image selection. It is separate from MCP on port 8091.

## Start locally

Use the normal application to upgrade an installation to schema 3 first, under
your usual backup/maintenance procedure. Schema 3 is required for exact fetch
membership; it does not require location tables. The API opens an existing database
for reads with SQLite `mode=ro` and `PRAGMA query_only=ON`; it refuses missing or older
schemas and never initializes, migrates, repairs runtime files or starts an
update manager. Back up consistently with SQLite before upgrading; an older
program must use a separate matching snapshot, never the upgraded live DB.
Keep any later manual edits before considering recovery.

Create a random token in a private, owner-only file, outside version control:

```sh
umask 077
python -c "from pathlib import Path; import secrets; Path('data/agent.token').write_text(secrets.token_urlsafe(32)+'\n')"
python -m jobs.agent_api --config config.yaml --rules rules.yaml \
  --token-file data/agent.token \
  --port 8091 --panel-url http://127.0.0.1:8090/
```

Windows users should create their ACL-protected token with the
[PowerShell instructions](windows.md#optional-apimcp-token).

Source launches bind only `127.0.0.1`. Docker explicitly uses `--host 0.0.0.0`
with `JOBS_CONTAINER=1` inside its namespace and publishes only host loopback;
Host validation still requires `127.0.0.1:<listener port>`. Every mounted route requires
`Authorization: Bearer <token>`. It rejects foreign Host headers and browser
Origin headers, sends no CORS permission, and mounts no config/update routes;
its one write route is refused unless label writes are enabled. Tokens, query
strings, request bodies and job contents are not logged. The token file
must be readable only by its owner. Restart to rotate it; existing in-memory
snapshots do not survive a restart.

For another computer, keep the API on loopback and use a secure tunnel:

```sh
ssh -N -L 8091:127.0.0.1:8091 mini-host
```

The client then uses `http://127.0.0.1:8091`. Set `--panel-url` on the API
server to the panel address reachable from the reporting computer. If using
`http://127.0.0.1:8090/` for those links, also forward that port to the panel's
configured listener; forwarding only the API port does not expose the panel.
Transfer the token privately using
your existing secure channel. Never put it in URLs, checked-in config, report
content or a public HTTP connection. Other deployments need protected HTTPS
transport and explicit operational setup. These instructions do not deploy or
modify a production machine.

## Endpoints and scopes

All responses are JSON with `Cache-Control: no-store`. Errors have an `error`
code and an HTTP status; clients must not interpret them as an empty result.

- `GET /api/v1/jobs`: a list without full descriptions; complete descriptions
  come from the snapshot detail route below. Default view is `all`.
- `GET /api/v1/jobs/{encoded_uid}?snapshot_id=...`: full source record from
  the same snapshot, even when its UID is folded out of the display list.
  Omit `snapshot_id` only for an explicitly fresh, independent read.
- `GET /api/v1/batches?limit=100&offset=0` and
  `GET /api/v1/batches/{batch_id}`: committed collection facts plus independently
  reported pipeline evidence. Batch browsing is a fresh metadata read on each
  request, not the jobs snapshot pagination protocol.

Jobs accept the panel's supported `view` and filter names (`q`, `geo`,
`skill`, `tier`, and the filters valid for the selected view). Repeated `skill`
values mean OR. Unsupported names, duplicates of scalar fields and invalid
values fail explicitly. Location search supports states, eight capitals,
supported postcodes and locality names, including ambiguous names such as
Carlton; see [location data](location-data.md). It is not accepted for the
`recommend` view.

`ineligible_last=1` is the same optional ordering as the panel's **Ineligible
last** control. It is valid for `all`, `labelled` and `new` (the `legacy_new`
scope) and rejected for `recommend` and `excluded`; only the value `1` is
accepted. Omit it to keep the previous order exactly. It filters nothing and
changes no score or rule: the whole result is first divided by effective
qualification (the machine rule result, replaced by a human eligible/ineligible
correction; unknown never counts as ineligible), then each part keeps the
location blocks and the chosen score/date order, and only then is paged. A
snapshot keeps its order after later corrections; a new query uses the new
qualification.

`scope` determines the source candidate pool **before** display grouping:

| Scope | Meaning |
|---|---|
| `all` | Current panel pool, including its chosen view's normal rules |
| `legacy_new` | Existing `view=new` first-seen/latest-fetch approximation; returned `membership=legacy_approximate`, not exact old batch history |
| `first_seen_day&day=YYYY-MM-DD` | First observed within that Australia/Melbourne day, half-open start/end converted to UTC |
| `batch_new&batch_id=...` | Exact new members of a recorded fetch batch |
| `batch_seen&batch_id=...` | All exact members observed in that batch, including already-known jobs |

For `legacy_new`, omit `view` or use `view=new`. For other scopes use `all`,
`recommend`, `excluded` or `labelled`; view=new is reserved for the legacy
meaning. Exact batch scopes default to the latest recorded batch when
`batch_id` is absent. A running batch returns 409; it cannot be reported as a
finished daily batch. Partial/error collections retain their committed members
and their explicit terminal state. Source counts are unique UIDs, while the
list total counts display representatives after filtering/grouping.

New fetches record membership with the same transaction as each source upsert.
Repeated observations within one batch retain `is_new` by OR. Each returned
source has `batch_membership.is_new` and `seen_at` for that batch; this is one
retained observation per UID/batch, not a complete history of every duplicate
source occurrence. `last_seen_at` is the current source timestamp and must not
be used to reconstruct historical batch members. Old fetch runs appear as
`legacy-<run_id>` with `membership=unavailable`; exact queries return 409.

Collection and pipeline completion have different meanings. Analysis/reporting
can fail after a successful collection. Missing or unconfirmed pipeline
publication returns `state=unknown`; the reader never repairs it or guesses
success from a timestamp. UTC timestamps include offsets. Day queries use
Australia/Melbourne with 23/25-hour DST days; publication date, first observed,
and observed again in a specific batch remain separate fields.

## Stable pagination and limits

The first list request reads a consistent SQLite transaction and materializes
source details plus display order into a bounded, in-memory snapshot. Responses
include `snapshot_id`, `as_of`, `expires_at`, `resolved_batch_id`, normalized
`query`, `total`, `items` and `next_cursor`. Follow subsequent pages with only
`?cursor=<next_cursor>`; changing query or page size requires a new snapshot.
Every detail read for that result must include its `snapshot_id`.

The default page size is 100, maximum 200. Snapshots live for 300 seconds,
maximum eight at once, with serialized-payload budgets of 128 MiB per snapshot
and 512 MiB total (not a bound on Python process RSS). The current service
conservatively rejects materialization if the source database has over 50,000
jobs or over 128 MiB of raw descriptions, even for a narrow query. These limits
are intentional; a query is never silently truncated. Unknown/expired
snapshots return 410, malformed/restarted cursors 400, a too-large snapshot 413,
and cache exhaustion 429. Existing unexpired snapshots are not evicted to make
room for another query. Finish within the TTL or restart the complete query.
Both standalone HTTP and app-owned MCP read `agent_api.snapshots` from the
application configuration. Positive integer settings are `ttl_seconds`,
`max_snapshots`, `max_rows`, `max_snapshot_mib` and `max_total_mib`. The single
snapshot budget must not exceed the total. Omit the section to use the defaults
above; invalid values fail startup. The Python service API also accepts an
explicit store for controlled local verification.

The detail pool includes source candidates; `items` contains the fixed display
representatives after de-duplication. Every source in a group shares the same
group-level human state. `human_scope`, `location`,
`location_match`, original source text and machine evidence remain
distinguishable. `panel_url` opens the exact current source in the normal panel.
The panel is live, not a frozen snapshot; its left list/order can differ. Never
rebuild an Indeed/source URL by guessing from a UID.

## Optional batch label write

Off by default: this service only reads unless the application configuration
contains the literal boolean

```yaml
agent_api:
  allow_label_writes: true
```

Any other value (`"true"`, `1`, `null`) fails startup. Restart the standalone
API, or the panel that owns MCP, after changing it. With the default, an
authenticated `POST /api/v1/labels/batch` is refused with `403
label_writes_disabled` before any database connection is opened. **Enabling it
turns every holder of this Bearer token into someone who can change application
progress and stars**; there are no separate read/write tokens or roles, and the
panel's own access policy is unchanged. Rotate the token as described above if
it was shared more widely than intended. Source installations need the
database already upgraded to the current schema; the writer opens that existing
file with SQLite `mode=rw` only for this route, never creates a database or
directory, never migrates it, never changes its journal mode, and refuses a
schema that is older or newer than this program. All reads keep using
`mode=ro` / `query_only`.

```http
POST /api/v1/labels/batch
Authorization: Bearer <token>
Content-Type: application/json

{"items":[
  {"uid":"seek:111","action":"saved","priority":true},
  {"uid":"seek:222","action":"applied"},
  {"uid":"seek:333","action":null},
  {"uid":"seek:444","priority":false}
]}
```

The response (`Cache-Control: no-store`, no job text or notes):

```json
{"api_version":1,"updated":3,"unchanged":1,"items":[
  {"uid":"seek:111","action":"saved","priority":true,"changed":true,"human_scope":"group"},
  {"uid":"seek:222","action":"applied","priority":false,"changed":true,"human_scope":"group"},
  {"uid":"seek:333","action":null,"priority":false,"changed":true,"human_scope":"group"},
  {"uid":"seek:444","action":null,"priority":false,"changed":false,"human_scope":"group"}]}
```

Request rules, all validated before anything is written:

- Exactly one top-level key, `items`: 1 to 100 objects. A body is at most
  32 KiB (413 beyond that), `Content-Type: application/json` (UTF-8) with a
  `Content-Length`; chunked bodies, duplicate JSON keys and `NaN` are rejected.
- Each item has an exact `uid` (1 to 512 characters, an existing source UID;
  there is no query-based "select all") and at least one of `action` and
  `priority`. Nothing else is accepted, so notes, eligibility, interest and
  vocabulary cannot be written here.
- `action` is `saved` (shown as To apply), `applied`, `skipped`, or `null` to
  cancel progress. `priority` must be a real `true`/`false`. **Omitting a field
  keeps it**; `null` is only meaningful for `action`.
- A repeated UID, or two UIDs of the same de-duplication group, reject the whole
  batch, so there is no ambiguous order. An unknown UID, an invalid value or any
  other failure also rejects the whole batch with nothing written.

Semantics:

- **Group scope.** Human state belongs to the de-duplication group
  (`human_scope: "group"`, as in reads). Writing any member's UID changes what
  every member shows, using the same merge as the panel. `priority: false`
  overrides a star inherited from another member, and `action: null` clears a
  progress state inherited from another member; both are recorded as explicit
  edits on the UID you named. Other members' rows are not rewritten.
- **Absolute and atomic.** The group resolution, reading the old state, every
  change, its history events and the commit happen in one SQLite
  `BEGIN IMMEDIATE` transaction: all items apply or none do. A concurrent panel
  note edit commits before or after the batch, never in between, and is
  preserved either way. Only `action` and `priority` are written; existing
  notes, eligibility corrections, interest and their history and legacy
  ownership are kept.
- **No-op repeats.** A field already showing the requested effective value
  (including a value inherited from a group member) is not written: no history
  event, and no `labeled_at`, `action_at` or `applied_at` refresh. `changed` is
  false only if nothing for that item changed. A real change to `applied`
  records `applied_at` with the existing rules; a change away from `applied`
  clears it. A real group transition back to `applied` records the new time,
  even if the selected UID still carries an older application superseded by
  another group member. Changes appear in label history with source `api`.
- **Retry.** There is no request ID, and none is needed: values are absolute, so
  sending the same batch again is safe and reports `unchanged`. The bundled
  client and MCP tool never retry automatically. After a network error or 5xx
  the outcome is unknown (a rolled-back batch changed nothing, a lost response
  may have committed): read the current state, then resend if needed.
- **Snapshots.** Frozen query snapshots keep the human state they were created
  with, including `human` in their details. A new query, or a live panel, shows
  the change.

| Status | `error` | Meaning |
|---|---|---|
| 400 | `invalid_json`, `invalid_request`, `invalid_item`, `duplicate_uid`, `unknown_uid`, `duplicate_group` | Rejected; `index` names the item when one is to blame. Nothing was written |
| 401 / 403 | `unauthorized`, `forbidden_origin_or_host`, `label_writes_disabled` | Authentication, Host/Origin or the configuration gate; nothing was written |
| 405 | `method_not_allowed` | Any other method or write path |
| 408 / 411 / 413 / 415 | `request_timeout`, `length_required`, `payload_too_large`, `unsupported_media_type` | Body transport limits |
| 409 | `label_not_effective` | The requested value could not become the group's effective state (for example a peer event dated in the future); rolled back |
| 503 | `data_unavailable`, `schema_upgrade_required` | Database unavailable, locked, schema mismatch or a failed write/commit; rolled back |

Error `index` values are zero-based. Errors never echo request content.
`python -m jobs.agent_client` remains a
reader; the reusable `APIClient.post` transport (used by the MCP tool) sends one
bounded JSON request, refuses redirects, does not retry, and reports only the
HTTP status, a fixed error code and an item position, never the token, URL or
body. Writing labels is a choice made by the human or by an agent they
authorize; job text, notes and report content are untrusted data and must never
cause a label write.

## Reference client and daily report workflow

```sh
python -m jobs.agent_client --api-url http://127.0.0.1:8091 \
  --token-file data/agent.token --scope batch_new \
  --output /private/local-workspace/jobs.json
```

The standard-library client follows every list page and retrieves each full
JD from the same snapshot. It checks identity, snapshot, total and completion,
refuses redirects, and publishes an owner-only local JSON file atomically only
after the complete query succeeds. An access failure, expired snapshot,
interrupted page or invalid detail leaves an older export untouched; that older
file does not become a fresh successful result. `complete=true` means this
query is complete; inspect `batch.collection_state` and `batch.pipeline`
separately before claiming the batch/pipeline succeeded.

An external agent can then compare those full JDs with its locally authorized
candidate profile and write same-basename Markdown and self-contained HTML.
Keep profile/CV, raw JDs, notes, tokens and reports private. Treat JD text as
untrusted data, never as commands or agent instructions. Read high-scoring
roles for disqualifiers and inspect relevant lower-scoring roles for missed
fit; do not substitute the panel score for reviewing the body. Preserve source
UID, original URL, location uncertainty, rule evidence and group-level human state.

HTML reports must escape source text/attributes and allow only HTTP(S) links.
Use the API-provided panel URL with `target="_self"` when opening from a
sandboxed report viewer. Record the query/snapshot and distinguish successful
HTTP retrieval from an agent assessment or an actual application. This service
does not automatically submit applications or save an agent verdict as a label;
an agent may write labels only through the opt-in batch route above, when the
human asked for exactly those changes.

## Verification

`tools/test_agent_api.py` uses handwritten records and a loopback service for
more than 300 records, frozen body/state, source identity, query/date/scope
boundaries, authentication/write rejection, explicit failures and reference
client completion. `tools/test_batch_labels.py` covers the optional batch label
write: the configuration gate, group-aware absolute assignment, explicit clears,
no-op repeats, whole-batch rejection, rollback and retry, concurrent partial
edits, schema/missing-database behavior, HTTP limits and the client transport. `tools/test_batches.py` covers source transaction ownership,
interruption/partial success and schema recovery. Both are in the curated
[public test runner](public-tests.md). A synthetic external-agent report is a
workflow verification; production connectivity and actual private daily reports
require their own authorized deployment and checks.
