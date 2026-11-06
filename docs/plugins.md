# Plugins

Plugins are optional and disabled by default. Settings > Plugins shows each installed plugin's purpose and permission review. To enable one, read the full review and explicitly accept it. Setting `plugins.sponsorship.enabled: true` alone does not grant permission. Existing enabled configurations need the same review. Changes take effect on the next relevant run. Disabling hides results but keeps both the grant and existing files; revoke removes the grant and disables the plugin.

The local CLI provides the same review and grant store:

```sh
python -m jobs.cli --config config.yaml plugin review sponsorship
python -m jobs.cli --config config.yaml plugin grant sponsorship
python -m jobs.cli --config config.yaml plugin revoke sponsorship
```

Interactive grant asks for `yes` after printing the review. For a non-interactive grant, the user must first review the current permissions and supply the exact printed fingerprint with `plugin grant sponsorship --fingerprint FINGERPRINT`. A different fingerprint is rejected. Do not treat installation or an enabled setting as consent.

The authenticated agent API exposes `GET /api/v1/plugins` for review and status. MCP exposes the read-only `review_plugin_permissions` tool. These interfaces do not grant permission. The user confirms in the local panel or CLI; the model must disclose all permissions and wait for explicit consent before directing the user to that confirmation. Query again to check the result.

The host reads manifests without importing plugin code. A manifest must declare the supported structured permission vocabulary and compatible hooks. The host binds a grant to the plugin ID and scope fingerprint, stores the local user, channel and time beside the database, and checks it before dispatch. A changed permission declaration needs a new grant, including reductions. Display-name, description and version-only changes preserve the grant. An invalid manifest or missing grant prevents the plugin hook from running; core processing continues.

The analyze hook receives only `{uid, company}` per job, its data directory and the analysis ID. It returns validated bilingual badges and sections. Results do not change eligibility, scoring, filtering, ordering or human data. The host retains plugin data, bounded error records and results under the database-adjacent `.<database-name>.plugins/` directory after disable or uninstall.

Plugins are trusted Python code, not an adversarial-code sandbox. The worker clears unrelated environment variables, applies Python audit checks for file paths, denies direct network and external process operations, and retains process time and memory limits. Reads are allowed within the installed plugin, its data and temporary directories, and the Python installation; writes are allowed in its data and temporary directories. Python audit hooks do not contain hostile native code or every possible operating-system bypass. The sponsorship host alone performs its fixed public-reference requests; a plugin cannot choose arbitrary URLs or request bodies. On POSIX the host terminates the worker process group; on Windows it uses a Job Object. Native Windows behavior requires separate acceptance.

Manifest, request, result and diagnostic sizes are bounded. Exceptions, invalid output and refresh failures are isolated from core processing; parent pipeline timeouts and interruptions still propagate. Debug records omit raw exceptions, configuration and job inputs. Install or upgrade only trusted plugins explicitly.

The host download helper runs trusted core code in its own supervised process before the fetch hook. It has the same fetch wall-clock and process-resource limits as that hook; installed plugin code is not imported there. This bounds a stalled public source without allowing plugin networking. Configuration is saved before a new grant is published; a grant write failure leaves the plugin unable to run. Revocation is persisted before disabling so a failed setting save cannot restore authorization.
