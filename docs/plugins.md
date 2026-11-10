# Plugins

Plugin authors and host contributors: the complete list of what a plugin may declare, read and do is in [plugin-interface.md](plugin-interface.md).

Plugins are optional and disabled by default. Settings > Plugins shows each installed plugin's purpose and permission review. To enable one, read the full review and explicitly accept it. Setting `plugins.sponsorship.enabled: true` alone does not grant permission. Existing enabled configurations need the same review. Changes take effect on the next relevant run. Disabling hides results but keeps both the grant and existing files; revoke removes the grant and disables the plugin.

The local CLI provides the same review and grant store:

```sh
python -m jobs.cli --config config.yaml plugin review sponsorship
python -m jobs.cli --config config.yaml plugin grant sponsorship
python -m jobs.cli --config config.yaml plugin revoke sponsorship
```

Interactive grant asks for `yes` after printing the review. For a non-interactive grant, the user must first review the current permissions and supply the exact printed fingerprint with `plugin grant sponsorship --fingerprint FINGERPRINT`. A different fingerprint is rejected. Do not treat installation or an enabled setting as consent.

Fetch and analyze skip enabled plugins that need consent and print a notice to stderr, including the plugin ID, the reason and a review command for the selected configuration. Scheduled runs retain this notice in their captured output. Disabled plugins do not produce consent notices.

The authenticated agent API exposes `GET /api/v1/plugins` for review and status. MCP exposes the read-only `review_plugin_permissions` tool. These interfaces do not grant permission. The user confirms in the local panel or CLI; the model must disclose all permissions and wait for explicit consent before directing the user to that confirmation. Query again to check the result.

The host reads manifests without importing plugin code. A manifest must declare the supported structured permission vocabulary and compatible hooks. The host binds a grant to the plugin ID and scope fingerprint, stores the local user, channel and time beside the database, and checks it before dispatch. A changed permission declaration needs a new grant, including reductions. Display-name, description and version-only changes preserve the grant. An invalid manifest or missing grant prevents the plugin hook from running; core processing continues.

The analyze hook receives only `{uid, company}` per job, its data directory and the analysis ID. It returns validated bilingual badges and sections, either inline per job or as a key into an optional `views` table so repeated content (for example, many jobs from one employer) is stored once. Results do not change eligibility, scoring, filtering, ordering or human data. The host retains plugin data, bounded error records and results under the database-adjacent `.<database-name>.plugins/` directory after disable or uninstall.

Plugins are trusted Python code, not an adversarial-code sandbox. The worker clears unrelated environment variables, applies Python audit checks for file paths, denies direct network and external process operations, and retains process time and memory limits. Reads are allowed within the installed plugin, its data and temporary directories, and the Python installation; writes are allowed in its data and temporary directories. Python audit hooks do not contain hostile native code or every possible operating-system bypass. The sponsorship host alone performs its fixed public-reference requests; a plugin cannot choose arbitrary URLs or request bodies. On POSIX the host terminates the worker process group; on Windows it uses a Job Object. Native Windows behavior requires separate acceptance.

Manifest, request, result and diagnostic sizes are bounded. Exceptions, invalid output and refresh failures are isolated from core processing; parent pipeline timeouts and interruptions still propagate. Debug records omit raw exceptions, configuration and job inputs. Install or upgrade only trusted plugins explicitly.

The host download helper runs trusted core code in its own supervised process before the fetch hook. It has the same fetch wall-clock and process-resource limits as that hook; installed plugin code is not imported there. This bounds a stalled public source without allowing plugin networking. Configuration is saved before a new grant is published; a grant write failure leaves the plugin unable to run. Revocation is persisted before disabling so a failed setting save cannot restore authorization.


## Installing, upgrading and removing plugins

Plugins live in two places:

- **Bundled** plugins are in `plugins/<id>/` in the Job Board folder. `sponsorship`, `map` and `workspace` are bundled.
- **Installed** packages are in `.<database-name>.plugin-packages/<id>/` beside the database, next to the plugin data directory `.<database-name>.plugins/`. For `jobs.db` that is `.jobs.db.plugin-packages/`. They belong to the instance, so they follow the database. In Docker they are in the data volume (see [docker.md](docker.md#installed-plugins)).

A bundled ID cannot be replaced by an installed package: install refuses it. The host reads at most 32 plugins in total, bundled and installed together.

Installing, upgrading and removing is possible only from the local command line, on the machine that runs Job Board. The panel, the agent API and MCP cannot do it, and a plugin cannot do it to itself. The panel has no authentication, so an install action there could not tell who asked for it. Settings > Plugins shows where each plugin comes from and how to install one, and nothing more.

```sh
python -m jobs.cli --config config.yaml plugin list
python -m jobs.cli --config config.yaml plugin install ./example-plugin-1.0.0.zip
python -m jobs.cli --config config.yaml plugin install https://example.com/example-plugin-1.0.0.zip --sha256 HEX
python -m jobs.cli --config config.yaml plugin upgrade ./example-plugin-1.1.0.zip
python -m jobs.cli --config config.yaml plugin uninstall example-plugin
```

| Command | What it does |
|---|---|
| `plugin list [--json]` | One line per plugin: ID, version, origin (`builtin` or `installed`), status, and the reason when a plugin is incompatible. `--json` prints the same items as JSON. |
| `plugin install SOURCE [--sha256 HEX] [--version V] [--yes]` | Installs a package that is not installed yet. |
| `plugin upgrade SOURCE [--sha256 HEX] [--version V] [--yes]` | Replaces an installed package. The plugin ID in the package must already be installed. |
| `plugin uninstall ID [--yes]` | Removes an installed package. A bundled plugin is refused with a message. |

`SOURCE` is a local `.zip` file or an `https://` URL. `--sha256` is the SHA-256 of the `.zip` file. It is required for a URL and optional for a local file; when given, it must match or nothing is opened. `--version V` stops the install unless the version in the package manifest is exactly `V`. Both commands print the computed SHA-256. `--yes` answers the confirmation question; without `--yes` and without a terminal, nothing is installed.

A download accepts only `https`, no credentials in the URL, and redirects only to `https` (at most 5). It has a 60-second time limit for the whole download and the 128 MiB package size limit, and a failed or partial download installs nothing. The request names the application and its version and sends no job data, configuration or token.

Install checks, in this order, and writes nothing until all pass:

1. The hash, when one is given or required.
2. The archive: size, entry count and entry kinds, as listed in [plugin-interface.md](plugin-interface.md#packages-and-installation).
3. The manifest, against the same three permission profiles as a bundled plugin. A package with any other permission set is refused.
4. The core version range in `compatibility.core`, if the manifest declares one. A package that does not fit this Job Board is refused.
5. The ID: not a bundled ID, and not already installed (use `upgrade`).

Install then prints a review: ID, name, version, the permission profile in plain words, whether the plugin runs Python code on this machine, the source and the SHA-256. It asks for `yes` before writing anything. Files go into a staging directory and are moved into place in one step; if anything fails, the previous state is intact. The package location must be a real directory: a symbolic link there is refused before anything is changed. Install, upgrade and uninstall use the same run lock as updates, or refuse with a message while an update holds it, so a running update never sees a half-replaced package.

**Install never enables a plugin and never grants permission.** The new plugin is installed disabled. If the configuration or grant store still holds an enabled setting or a grant for the same ID from an earlier install, install clears them first. Review and accept its permissions the usual way, in Settings > Plugins or with `plugin review` and `plugin grant` above. An installed plugin shows "Installed" next to its version in Settings > Plugins. Its details and review show the source, the first 12 characters of its SHA-256 and the install date, as plain text.

**Upgrade** prints the old and the new version and, before it asks for confirmation, whether your existing grant stays valid, so that the new code runs without another review, or a new review is needed. A downgrade is allowed and is stated. The package is replaced in one step, and the old one is put back if the replacement fails. The enabled setting is kept. The grant is kept only when the permission declaration is unchanged; a changed declaration, including a reduction, needs a new review, and the plugin does not run until you accept it. Name, description, version, icon and `compatibility.core` changes keep the grant.

**Uninstall** removes the package and its install record, disables the plugin in the configuration and revokes its grant. It keeps the plugin's data, error records and results in `.<database-name>.plugins/<id>/` and says so. Delete that directory yourself if you want them gone.

**Core version range.** A manifest can declare which Job Board versions it works with (`compatibility.core`, see [plugin-interface.md](plugin-interface.md#core-version-range)). When the running version is outside the range, the plugin gets the status `incompatible`. It is never run, served or shown as a page or sidebar. Its stored grant and results are kept, and Settings > Plugins says which version it needs and which one is running. This applies to bundled and installed plugins. Upgrade the plugin or Job Board and it starts working again; the earlier grant applies if its permissions did not change.

**Trust.** A package with Python hooks (host API 1) runs code on this machine, with the limits described on this page, which contain accidents and are not a sandbox for hostile code. A page plugin (host API 2) runs in the sandboxed frame described below. Install only packages from a source you trust. The SHA-256 shows that the file is the one the publisher described: compare it with the value the publisher states, in a place other than the download link. It does not show that the plugin is safe. The install review states whether the package runs Python code, and the permission review still comes before it can run.

## Browser display pages (host API 2)

The optional Map plugin registers a display choice next to List in the common
search toolbar. `view` still selects the core business view; `display=map`
selects presentation. Core search, grouping, representatives and ordering stay
in core. A map area/postcode selection applies only to the map; returning to
List restores its query, page and scroll anchor. Detail opens the existing core
panel and keeps its ordinary note/save/navigation guards. On the map it is an
overlay with one Back to map control (Esc also returns, focus goes back to the
map); previous/next is hidden because the hidden list order is not the map
selection. The map has no area mode: a live summary counts the jobs whose
postcode marker is in view and opens them as a list. List switching uses the
toolbar.

Map uses local MapLibre GL JS and bundled ABS POA 2026 boundaries with
OpenFreeMap/OpenStreetMap tiles. No user key is required. Core metropolitan
classification continues to use its existing ABS 2021 population reference.
Postal Areas approximate postcodes; a marker is a point inside a postcode, not
an office address. Jobs that only name a capital city are shown as a separate,
dashed citywide marker near that city's centre; they are never assigned to a
postcode. State-only, ambiguous and unavailable locations remain in the
separate other-locations list (state-only groups for states in view). Counts follow the core view: jobs normally,
source records in Excluded. Markers render from the reference index as soon as
results arrive; a postcode outline is fetched only when that postcode is opened.

Enable Map through its full permission review in Settings > Plugins (or the
same CLI commands with `map`). Opening an authorized map sends the browser IP,
viewed tile coordinates and an origin-only Referer (the board's address, no path or query) to `tiles.openfreemap.org`.
Job summaries, search text, JDs and notes are not sent to the tile provider.
Disabled/list/demo pages do not load map assets or tiles. A local boundary and
results fallback remains usable if WebGL or tiles fail. Attribution is always
shown as a one-line footer with an expandable full source note (text only: the
sandbox has no popups). The host forwards live theme changes (`jobs-plugin-theme`). Reference files are cached with the installation;
there is no runtime ABS fetch or GIS dependency.

A page manifest declares `compatibility.host_api: 2`, `entry: web/index.html`,
`hooks: []` and a structured `page` registration (`id`, bilingual `label`,
`entry`, optional `icon` from the core's fixed set, currently `map`; the icon is
drawn by core and is not part of the consent fingerprint). The first supported permission profile is the bounded summary,
plugin-reference and OpenFreeMap display profile in `jobs/plugin_host.py`.
This does not authorize arbitrary toolbar HTML, endpoints or commands. Existing
host API 1 Python hook plugins keep their existing contract and fingerprints.

Core serves allowlisted local web files and integrity-checked public reference
JSON. Pages run in `sandbox="allow-scripts"` without same-origin permission.
CSP (built from the validated Host, with its own `sandbox allow-scripts`) limits local
sources to `/plugins/<id>/` and connections, images and fonts to that path plus the
declared tile origin. The sandbox and CSP limit what the page can load and call; they do
not stop a page from navigating its own frame elsewhere, so a page plugin must come from a
trusted source. Module assets have
public CORS headers for the opaque frame. MapLibre's opaque-origin module
worker uses a data URL, permitted only by `worker-src`.

The v1 postMessage bridge validates the frame window, random channel, monotonic
request IDs, payload shape and at most eight active requests. Operations are
`load`, `reference`, `open_job`, `navigate_list`, and `save_state` (validated
camera/postcode/list state in this tab's session storage, one per page plugin, so a new
search or filter keeps the map where it was). No arbitrary
URL or write operation is exposed. Summary snapshots are complete for the core
query, expire after five minutes (re-entering the same query and result generation
reuses a live snapshot; when the store is full the page's oldest snapshot is evicted) and are bounded to 20,000 rows, 8 MiB per
snapshot and 48 MiB total. Oversized results fail explicitly. Job detail requires
membership in the snapshot and opens the core reader. Permission is checked
on every resource/data/bridge operation and polled while the frame is open;
revocation removes the frame and cancels requests on detection (three consecutive
failed permission reads are treated the same; one transient failure is not). A frame
that navigates itself loses the bridge.

The summary field allowlist is `PAGE_JOB_FIELDS`; it omits JD bodies, notes and
history. Frozen membership does not freeze core detail: opening a row reads the
current core job. Reference packages use an index of bounded gzip files with
SHA-256 hashes. See [map provenance](../plugins/map/reference/README.md) and
[plugin use and dependency provenance](../plugins/map/README.md).


## Sidebar plugins and handed jobs

The optional Workspace plugin uses a second host API 2 permission profile,
"handed jobs". It registers a `sidebar` and a `page`, declares
`network: "none"` and reads only the jobs the user hands to it, at most ten.
It never receives the result list. The exact profile, with or without
`job_detail`, is whitelisted in `jobs/plugin_host.py`; any other combination is
rejected, and the map profile still rejects `sidebar`, `switch`, `job_scope`
and `job_detail`.

Both registrations are structural. `sidebar` has `id` (the plugin ID), bilingual
`label` and `handoff_label` (at most 32 characters each), `entry: web/index.html`
and an optional core icon. `page` has `entry: web/compare.html` and an optional
`switch` boolean (default `false`). Core icons are `map`, `workspace` and
`compare`. Icons stay out of the consent fingerprint; `sidebar` and `page`
otherwise join it. A manifest without `sidebar` hashes exactly as before, so the
existing Map and Sponsorship grants stay valid (the regression test pins both).

`plugin_host.page_plugins()` items carry `switch`: `true` for map pages, the
manifest value for handed pages. Only switch pages belong in the toolbar
List/Map switch. `?display=<id>` resolves for every authorized page, so the
compare page opens without appearing in the switch.
`plugin_host.sidebar_plugins()` lists `{id, label, handoff_label, icon}` for
enabled and granted sidebar plugins. Both are empty when no plugin is
configured, in demo mode, or when the plugin is disabled, ungranted or invalid.
The page context reports `agent_access: "on"` only when this panel process runs
its HTTP MCP listener.

`GET /plugins/<id>/frame?slot=sidebar|page` serves that slot's registered entry;
without `slot` the frame keeps the top-level entry. The frame CSP is built from
the manifest profile: only declared network targets become external origins. A
`network: "none"` plugin gets none, and the map CSP is unchanged.

`GET /api/plugin-view/<id>/handed?uid=…&uid=…[&detail=1]` is the only data route
for this profile. It takes 1 to 10 distinct UIDs of at most 512 characters each,
and no other parameters. It reads all of them in one read-only transaction,
using the agent API's row loading and decoration, and answers in request order:

```json
{"plugin_id": "workspace", "as_of": "…", "generation": "…",
 "items": [{"uid": "…", "missing": true}, {…summary…}]}
```

A summary has the `PAGE_JOB_FIELDS` fields (`core_order` is the position in the
request), `revision` and `human_labels`. `revision` is the first 16 hex digits of
SHA-256 over the JSON array `[title, company, location, salary, description]`.
`human_labels` is `{eligibility, action, priority}`: the effective group values,
never the note text. With `detail=1`, which requires `job_detail` in the
manifest, items add `description`, `skills`, `tags`, `qualification` (the same
object the agent API returns) and `eligibility_evidence`
`{rule_id, rule_kind, matched_text, snippet, cue}`. Excluded jobs are found like
in the agent API; a UID absent from the database is `missing`.

Errors are explicit: `invalid_query`, `invalid_uid_count`, `invalid_uid` and
`duplicate_uid` return 400. `detail_not_granted` returns 403. A response over
4 MiB returns 413 `response_too_large`. Map pages get 403
`permission_not_granted` on `handed`, and handed plugins get the same on
`jobs`, `member` and `reference`. The route adds no write path. Labels, notes,
history and vocabulary are only read.

The browser bridge keeps one instance per frame (own channel, request IDs and
limits), so a map page and a sidebar from two plugins run side by side.
Operations are dispatched by profile and slot; the map operations above are not
available to a handed-profile frame and the reverse. Handed-profile operations:

| Operation | Slots | Effect |
|---|---|---|
| `handed_jobs {uids, detail}` | sidebar, page | Reads the `handed` route; every UID must be in the host-owned list |
| `remove_handed {uids}`, `clear_handed {}` | sidebar, page / sidebar | Shrinks the host-owned list |
| `open_job {uid}` | sidebar, page | Opens a listed job in the core reader through the ordinary navigation guards |
| `copy_text {text}` | sidebar, page | Core copies at most 2,000 characters and reports `{copied, fallback}` honestly; the fallback is the core manual-copy dialog |
| `open_page {}` | sidebar | Opens the plugin's own page; "back" returns to where it was opened from |
| `navigate_list {}` | page | Returns from the page |
| `dismiss {}` | sidebar | Closes the phone sheet (keys pressed inside the frame never reach the board) |
| `save_state {state}` | sidebar, page | Stores at most 4 KiB of plugin UI state in this browser and reports `{ok}` |

The list of handed jobs (`jobs.plugin.handed:<id>`, at most ten) is owned and
written only by core, in this browser's local storage. Core tells the frames
about it with `jobs-plugin-handed` (also after a change in another tab) and tells
the sidebar which job is open with `jobs-plugin-current` `{uid, title, company}`.
That current-job notice is the only data about a job the user has not handed
over, and the permission review says so. A handed-profile frame's context is a
fixed set of fields without the current view, filters or search text. The only writes these operations make
are the clipboard copy and the two browser-storage keys; none reaches the
database, labels or notes, and no operation takes a URL.
