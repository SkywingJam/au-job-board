# Plugins

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
toolbar, not a control in the frame.

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
