# Plugin interface reference

This page lists everything a plugin may declare, read and do, as of v1.4. It is
the reference for plugin authors and for contributors changing the host. For
how to enable, review and revoke a plugin, and for the reasons behind the
isolation model, see [plugins.md](plugins.md).

Plugins are trusted local code from a source the user chose. The limits below
contain accidents and keep each plugin to what its permission review states;
they are not a sandbox for hostile code.

## Stability

The interface on this page is the baseline from v1.4 on. Changes are
incremental:

- New capabilities are added as new manifest keys, permission profiles, bridge
  operations, events or response fields. Existing ones keep their names,
  shapes and meaning.
- A frame or hook must ignore fields, events and error codes it does not know.
- Anything that widens what a plugin can read or do changes the consent
  fingerprint, so the user reviews it again. Cosmetic changes (name,
  description, version, icons) do not.
- A change that cannot be made additively gets a new `host_api` number; the
  host keeps accepting the old one or disables the plugin with a stated reason.

`tools/test_sidebar_host.py` pins the consent fingerprints of the bundled
plugins and the map frame's CSP. A change that moves them is a breaking change.

Installable packages, the `compatibility.core` range, the `incompatible` status
and the `origin`, `compatibility`, `incompatible` and `install` list fields were
added later under these rules. A manifest without `compatibility.core` behaves
as before, and a consumer that does not know the new fields can ignore them.
A plugin still cannot install, upgrade or remove anything, itself or another
plugin.

## Files and identity

```
plugins/<id>/
  manifest.json        required, at most 32 KiB
  plugin.py            host API 1 entry
  web/                 host API 2 files, served under /plugins/<id>/assets/
  reference/           optional public reference package (map profile)
  skills/              optional agent skills; never loaded or run by the host
  README.md
```

`<id>` matches `^[a-z][a-z0-9_-]{0,63}$` and equals the directory name. The
host reads at most 32 plugin directories and never follows a symlinked plugin
directory. The manifest is read without importing plugin code.

A plugin directory is in one of two places. Bundled plugins are in `plugins/<id>/`
of the Job Board folder (`origin: "builtin"`). Installed packages are in
`.<database-name>.plugin-packages/<id>/` beside the database
(`origin: "installed"`); see [Packages and installation](#packages-and-installation).
The layout is the same in both. The 32 plugins are counted across both places.
An installed package never takes the ID of a bundled plugin.

Common manifest keys:

| Key | Rule |
|---|---|
| `id` | The directory name |
| `name` | String, or `{zh, en}`; at most 100 characters each |
| `description` | Optional string or `{zh, en}`; at most 200 characters each |
| `version` | Non-empty string, at most 64 characters |
| `compatibility` | `{"host_api": 1}` or `{"host_api": 2}`, with an optional `core` range (see [Core version range](#core-version-range)); no other keys |
| `entry`, `hooks`, `network`, `display`, `i18n`, `permissions` | Fixed by the profile, see below |

Settings live under `plugins.<id>` in the configuration. Only `enabled`
(boolean, default false) is defined. A plugin runs only when it is enabled
**and** the user granted its current fingerprint.

## Permission profiles

The host accepts exactly three permission profiles. A manifest whose
permissions differ from all of them is rejected and the plugin shows as
`error`; nothing else is affected.

| | Python hook (host API 1) | Map page (host API 2) | Handed jobs (host API 2) |
|---|---|---|---|
| Bundled example | `sponsorship` | `map` | `workspace` |
| `entry` | `plugin.py` | `web/index.html` | `web/index.html` |
| `hooks` | subset of `after_fetch`, `after_analyze_success` | `[]` | `[]` |
| `network` | `after_fetch` or `none` | `view_open` | `none` |
| `display` | boolean | `true` | `true` |
| `i18n` | subset of `zh`, `en` | `["zh","en"]` | `["zh","en"]` |
| `permissions.job_fields` | `["uid","company"]` | the 13 summary fields | the 13 summary fields |
| `permissions.job_scope` | — | — | `"handed"` |
| `permissions.job_detail` | — | — | optional, the fixed detail list |
| `permissions.files` | `["plugin_data"]` | `["plugin_reference"]` | `[]` |
| `permissions.network` | `[]` or the fixed public-reference entry | the fixed tile entry | `[]` |
| `permissions.execution` | `["python_worker"]` | `["browser_frame"]` | `["browser_frame"]` |
| `permissions.display` | `["job_badges","job_details"]` | `["page","display_mode"]` | `["sidebar","page","handoff"]` |
| Registrations | — | `page` | `sidebar` and `page` |

The 13 summary fields (`PAGE_JOB_FIELDS`): `uid`, `group_id`, `title`,
`company`, `location_raw`, `location_fact`, `salary`, `work_type`,
`listing_date`, `total_score`, `priority`, `core_order`, `panel_url`.

The detail list (`job_detail`): `description`, `skills`, `tags`,
`qualification`, `eligibility_evidence`, `human_labels`.

No profile can read note text, posting history, configuration, tokens or files
outside its own directories, and none can write labels, notes, history, the
vocabulary overlay or job data.

### Registrations (host API 2)

```json
"page":    {"id": "<id>", "label": {"zh": "…", "en": "…"}, "entry": "web/index.html", "icon": "map"}
"sidebar": {"id": "<id>", "label": {…}, "handoff_label": {…}, "entry": "web/index.html", "icon": "workspace"}
```

- Labels are bilingual, 1 to 32 characters each. `handoff_label` is the text of
  the core-drawn "add" action.
- `icon` is optional and names an icon core draws: `map`, `workspace`,
  `compare`. Plugins supply no markup for core surfaces.
- Map profile: `page.entry` is `web/index.html`; the page appears in the
  toolbar List/Map switch.
- Handed profile: `sidebar.entry` is `web/index.html`, `page.entry` is
  `web/compare.html`, and `page.switch` (boolean, default false) says whether
  the page is listed in the toolbar switch. A page with `switch: false` is
  opened only through `open_page`.

### Consent fingerprint

SHA-256 over `id`, `hooks`, `network`, `display` and `permissions`; host API 2
adds `compatibility`, `entry`, `page`, and `sidebar` when declared, each
without `icon`. `compatibility` is hashed without `core`: changing the core
range needs no new consent. The grant is stored beside the database and checked before
every hook dispatch, route and bridge operation.

## Core version range

`compatibility` may add an optional `core` string next to `host_api`:

```json
"compatibility": {"host_api": 2, "core": ">=1.4.0,<2.0.0"}
```

- The value is one or two comparators separated by a comma, written without
  spaces: `>=X`, `<X`, or both. `>=X` is the lowest version that works and `<X`
  the first version that does not.
- `X` is one to four whole numbers separated by dots, for example `1.4`,
  `1.4.0` or `1.4.0.1`. A missing part counts as 0. Numbers have no leading zeros,
  each comparator appears at most once, and the whole value is at most 64
  characters.
- The host compares the range numerically with the running Job Board version.
- Anything else in `core` makes the manifest invalid, and the plugin shows as
  `error`.
- `core` is not a permission. It is not part of the consent fingerprint.

A plugin without `core` is treated as working with every version, as before. A
plugin whose range does not include the running version gets the status
`incompatible`: it is never run, served, dispatched or shown as a page or
sidebar. Its grant, data and results are kept, and core processing continues.
This holds for bundled and installed plugins. Install and upgrade refuse a
package that is incompatible with the running version.

## Packages and installation

A package is one `.zip` file. `manifest.json` is at its root, or everything is
inside a single top-level folder, which is removed on install. The manifest and
all other rules on this page apply to the files in the package unchanged.

Limits, checked before anything is written:

| Limit | Value |
|---|---|
| Archive size | 128 MiB |
| Entries | 4,000 |
| Total uncompressed size | 256 MiB |
| Each file | 64 MiB |

Every entry must be a regular file or a directory. These are rejected:
symbolic links and other special entries, absolute paths, drive letters, `..`
segments, backslashes, empty names, names that differ only by letter case, and
encrypted entries. Each part of a path uses only letters, digits, `.`, `_` and
`-`, does not start with a dot or end with one, and is not a Windows device
name; a path is at most 512 characters and 16 levels deep. Files are stored or
deflate-compressed. Every entry file the manifest names must be in the
package. Modes stored in the archive are not kept. The manifest must
pass the host's validation, so only the three permission profiles above can be
installed, its `id` must match the ID rules, and the ID must not be one of the
bundled plugins.

Build a package from a plugin directory:

```sh
python tools/build_plugin_package.py PLUGIN_DIR -o OUT.zip
```

The tool checks the manifest with the host's validator and writes a `.zip` with
sorted entries and fixed timestamps, so the same files always give the same
bytes. It leaves out `__pycache__`, dotfiles and symbolic links, and prints the
path, the size and the SHA-256. Publish the SHA-256 beside the file; the person
installing passes it with `--sha256`.

The commands are `plugin list`, `plugin install`, `plugin upgrade` and `plugin
uninstall` of `python -m jobs.cli`, described with their options in
[plugins.md](plugins.md#installing-upgrading-and-removing-plugins). Install
never enables a plugin or grants permission. Upgrade keeps a grant only when the
consent fingerprint is unchanged. Uninstall keeps the plugin's data and results.
They run only from the local command line.

Next to each installed package the host keeps an install record,
`.<database-name>.plugin-packages/<id>.install.json`, with `id`, `version`,
`sha256`, `source` and `installed_at`. `source` is the file name, or the URL
without its query string and fragment (they can carry tokens). The record is informational: if it is missing or
damaged the plugin still works and shows no source details.

## Plugin status and list fields

`GET /api/plugins` (panel, same origin), `GET /api/v1/plugins` (agent API), the
MCP `review_plugin_permissions` tool and `plugin list --json` report each
plugin with the same fields. New fields are optional for a consumer.

| Field | Meaning |
|---|---|
| `id`, `name`, `version`, `description`, `enabled`, `error_count`, `consent` | As before |
| `status` | `enabled`, `disabled`, `consent_required`, `error` or `incompatible` |
| `origin` | `builtin` or `installed` |
| `compatibility` | `{"host_api": 1\|2, "core": "<range>"\|null}`; may be null when the manifest could not be read |
| `incompatible` | Only with status `incompatible`: `{"core": "<running version>", "requires": "<range>"}` |
| `install` | Installed plugins only: `{"version", "sha256", "source", "installed_at"}`, or `null` when the record is missing or damaged |

`incompatible` is not an error and does not mean the permission is missing: the
stored grant is kept. Settings > Plugins shows the two versions in one sentence
and offers no switch and no review for such a plugin. A plugin with this status
is treated like a disabled one for everything it would run or show.

## Host API 1: Python hooks

`plugin.py` defines a function per declared hook. Each call runs in a fresh,
supervised child process and receives one request dictionary:

```json
{"api": 1, "hook": "after_analyze_success", "data_dir": "…/data",
 "config": {}, "jobs": [{"uid": "…", "company": "…"}],
 "analysis_id": 123, "allow_network": false}
```

| Hook | When | Time limit | Returns |
|---|---|---|---|
| `after_fetch` | After a fetch; the host first performs the plugin's declared public downloads | 180 s | `{"errors": [...]}` |
| `after_analyze_success` | After a successful analysis, with every analysed job | 30 s | `{"analysis_id", "results", "views"?}` |

`after_analyze_success` must return the same `analysis_id` and one entry per
job in `results`. An entry is a bilingual view or the key of a shared view in
`views`:

```json
{"zh": {"badges": [...], "sections": [...]}, "en": {"badges": [...], "sections": [...]}}
```

- Badge: `{"text", "level", "tooltip"}`, level one of `good`, `warn`, `bad`,
  `info`; at most 20 per view. Core shows the most important one on a card.
- Section: `{"title", "status", "items"}`, at most 20 sections and 40 items;
  shown in the job detail.
- Strings are at most 1,000 characters. The whole result is at most 8 MiB.
- Results are stored per analysis run. A result for an older analysis is not
  shown.

Inside the worker: unrelated environment variables are cleared; reads are
limited to the plugin, its data and temporary directories and the Python
installation; writes to its data and temporary directories; direct network
access and external processes are refused; time and memory are bounded. Hook
results never change eligibility, scores, filters, ordering or human data. An
exception, invalid output or timeout is logged for that plugin and core
continues; pipeline timeouts and interrupts still propagate.

## Host API 2: browser frames

A frame is a plugin HTML file shown in an `<iframe sandbox="allow-scripts">`.
It has an opaque origin: no cookies, no local or session storage, no popups,
no modal dialogs, no access to the board's page.

### Served resources

| URL | Serves |
|---|---|
| `/plugins/<id>/frame` | The top-level entry |
| `/plugins/<id>/frame?slot=sidebar\|page` | That registration's entry |
| `/plugins/<id>/assets/<path>` | A regular file under `web/`, at most 8 MiB; extensions `.html .js .mjs .css .txt .json .png .svg .woff2` |

The frame's Content Security Policy is built from the manifest. Scripts,
styles, images, fonts and connections are limited to `/plugins/<id>/`; inline
styles are allowed, inline scripts are not. Only network targets declared in
the permission profile are added (the map's tile origin); a `network: "none"`
plugin gets no external origin. A frame cannot call the board's `/api/` routes
itself: everything goes through the bridge.

### Bridge protocol, version 1

All messages are `postMessage` between the frame and its parent.

1. Frame → host: `{type: "jobs-plugin-ready", version: 1}`.
2. Host → frame: `{type: "jobs-plugin-context", channel, context}`. `channel`
   is a random token for this frame; a later `ready` starts a new channel.
3. Frame → host: `{type: "jobs-plugin-request", channel, id, operation, payload}`.
   `id` is a decimal string that increases with every request; `payload` is an
   object.
4. Host → frame: `{type: "jobs-plugin-response", channel, id, ok: true, result}`
   or `{…, ok: false, error: {code, message}}`.

The host ignores messages from another window, with another channel or with a
non-increasing id. A frame must ignore messages whose source is not its parent
or whose channel differs. Each frame has its own bridge: at most 8 requests in
flight, 14 seconds each. Operations are dispatched by profile and slot; one
outside the frame's table returns `unsupported_operation`.

Host → frame events on the same channel:

| Event | Sent to | Payload |
|---|---|---|
| `jobs-plugin-theme` | every frame | `{theme: "light"\|"dark"}` |
| `jobs-plugin-handed` | handed-profile frames | `{handed}` after every change, including one made in another tab |
| `jobs-plugin-current` | handed sidebar | `{current: {uid, title, company} \| null}` after a job opens or the detail closes |

Common error codes: `plugin_disabled_or_permission_required`,
`unsupported_operation`, `invalid_request`, `page_busy`, `page_timeout`. Route
errors pass through with their own code.

Permission is re-checked before every operation and polled while a frame is
open. When the plugin is disabled or its grant is gone, the host removes the
frame, cancels its requests and removes its entry points. A frame that
navigates itself away loses its bridge.

### Map profile

Context: `plugin_id`, `slot` (`page`), `lang`, `theme`, `display`, `view`,
`query_url`, `return_url`, `pages`, `sidebars`, `agent_access`, and `state` when
one was saved. The map reads the current result list, so it is told the
current query.

| Operation | Payload | Result |
|---|---|---|
| `load` | `{}` | Summary snapshot of the current core query: `{items, total, snapshot_id, expires_at, as_of, view, query, count_unit, generation, reference_version}` |
| `reference` | `{file}` | A file of the plugin's integrity-checked reference package |
| `open_job` | `{uid, snapshot_id}` | `{opened}`; the uid must be in that snapshot; opens the core reader |
| `navigate_list` | `{}` | Returns to the list |
| `save_state` | `{box, area, selected, collapsed}` | `{ok}`; validated camera state, this tab only |

Snapshots hold at most 20,000 rows and 8 MiB (48 MiB in total), expire after
five minutes and fail explicitly when a result is larger.

### Handed-jobs profile

The plugin reads only jobs the user handed to it. The list of handed jobs is
owned and written by core, in this browser's local storage
(`jobs.plugin.handed:<id>`): at most 10 entries of
`{uid, title, company, revision, added_at}`. Core adds to it from its own
entry points (drag a card onto the sidebar, the detail action, the card
action, the multi-select action, a keyboard shortcut), each after validating
the job through the `handed` route.

Context, the same fixed set for both slots: `plugin_id`, `slot`
(`sidebar`\|`page`), `lang`, `theme`, `handed`, `agent_access`
(`on` only when this panel process runs its HTTP MCP listener), `state`,
`compare_columns` (3 on a window at least 900 px wide, else 2), and for the
sidebar `current`. The current view, filters and search text are not included.

`current` and `jobs-plugin-current` are the only data about a job the user has
not handed over: the ID, title and company of the job being viewed. The
permission review states this.

| Operation | Slots | Payload | Result |
|---|---|---|---|
| `handed_jobs` | sidebar, page | `{uids: string[1..10], detail: boolean}` | The `handed` route's response; every uid must be in the list, else `not_handed` |
| `remove_handed` | sidebar, page | `{uids: string[1..10]}` | `{handed}` |
| `clear_handed` | sidebar | `{}` | `{handed}` |
| `open_job` | sidebar, page | `{uid}` | `{opened}`; the uid must be in the list; goes through the core reader's ordinary guards |
| `copy_text` | sidebar, page | `{text}` at most 2,000 characters | `{copied, fallback}`; `fallback` means the core manual-copy dialog is showing |
| `open_page` | sidebar | `{}` | `{ok}`; opens the plugin's page; its "back" returns to where it was opened from |
| `navigate_list` | page | `{}` | `{ok}` |
| `dismiss` | sidebar | `{}` | `{closed}`; closes the phone sheet (keys pressed in a frame never reach the board) |
| `save_state` | sidebar, page | `{state: object}` at most 4 KiB as JSON | `{ok}`; stored per plugin in this browser (`jobs.plugin.state:<id>`) and returned as `context.state`. Treat `ok: false` as not saved |

Other errors: `state_too_large`, `storage_unavailable`.

`GET /api/plugin-view/<id>/handed?uid=…&uid=…[&detail=1]` (reached only
through `handed_jobs`) takes 1 to 10 distinct UIDs of at most 512 characters
and reads them in one read-only transaction:

```json
{"plugin_id": "…", "as_of": "…", "generation": "…",
 "items": [{"uid": "…", "missing": true}, {"uid": "…", "title": "…", …}]}
```

Items come back in request order. A record has the 13 summary fields
(`core_order` is the position in the request), `revision` (16 hex digits over
title, company, location, salary and description, to detect a changed posting)
and `human_labels` `{eligibility, action, priority}`. With `detail=1`, allowed
only when the manifest declares `job_detail`, it adds `description`, `skills`,
`tags`, `qualification` and `eligibility_evidence`
`{rule_id, rule_kind, matched_text, snippet, cue}`. Errors: `invalid_query`,
`invalid_uid_count`, `invalid_uid`, `duplicate_uid` (400), `detail_not_granted`,
`permission_not_granted` (403), `response_too_large` (413, over 4 MiB).

The only writes this profile can cause are the clipboard copy and its two
browser-storage keys. No operation takes a URL, and none reaches the database.

## Rules for frame code

- Treat every job field as untrusted text: render with `textContent`, never as
  HTML, and expect any field to be absent or null.
- Keep no state the bridge did not store for you; the frame can be reloaded at
  any time and gets a fresh context.
- Report failures honestly (not saved, not copied, not available) and stay
  usable when a request is refused.
- Supply both languages and follow the theme from the context and the theme
  event.

## Agent skills in a plugin

A plugin may ship `skills/<name>/SKILL.md` folders for the user to install
into their own agent. The host does not read, install or run them, and they
grant no permission. An agent reaches job data only through the separate,
authenticated [agent API](agent-api.md) or [MCP](mcp.md); the read-only
`get_jobs` tool reads up to ten jobs by exact UID, which is how job IDs copied
from the Workspace sidebar are resolved.

## Not available

These are deliberately absent, and adding any of them needs a new profile and
a new permission review: arbitrary network access from a frame or a hook;
running external programs; writing labels, notes or configuration; reading
note text, the result list (handed profile) or jobs outside the declared
scope; adding HTML, CSS or script to the board's own page; top-bar entries or
routes of a plugin's own; filtering, ordering or scoring by plugin results.

Installing, upgrading and removing plugins is not available to a plugin, to the
panel, to the agent API or to MCP. It is a local command-line action, because the
panel has no authentication.
