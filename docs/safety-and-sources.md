# Safety and sources

What the local panel does and does not protect, where the job data comes from,
and what you are responsible for. This page describes the current
implementation. It makes no security audit, certification or legal compliance
claim, and it does not interpret any third party's terms.

## The panel

The panel is a small local web server (Python standard library, plain HTTP). What
the implementation actually does:

- **No authentication.** Anyone who can reach the address and port can open every
  page, read all listings, descriptions and your notes, and use the write
  endpoints: change labels and notes, add, alias or remove skills in your
  vocabulary overlay, and trigger a full network update.
- **No transport encryption.** It serves HTTP, not HTTPS. The update, configuration, label and skill write
  endpoints require JSON and check the request host and browser origin. Label and
  skill bodies are limited to 64 KiB, including JSON encoding. Scripts must use
  `Content-Type: application/json` and an accepted Host; an Origin header, when
  present, must match the local HTTP origin. These checks are not authentication.
- **Wildcard binding is refused, nothing else is.** `panel.serve` refuses to start
  on `0.0.0.0`, `::` or an empty host. It still accepts any specific address, so a
  LAN or other interface address works, and then it is exposed to everyone on
  that network. The refusal is a guard against a common mistake, not an access
  control.
- **Loopback by default.** `config.example.yaml` binds `127.0.0.1:8090`. The
  `panel --host` and `--port` options override the configured values, so
  `panel --host 127.0.0.1 --port 8090` forces loopback for that run.
- **The synthetic demo is stricter:** it accepts only the IPv4 loopback addresses
  `127.0.0.1` and `localhost`, and refuses port 8090. See [demo.md](demo.md).
- **Data at rest is not encrypted.** The database, reports, labels, notes and
  configuration are ordinary local files, readable by anyone who can read your
  files.
- **No outside resources.** The pages load no external scripts, fonts or images.
  Opening a job's link goes to the original platform's page, which is a request
  from your browser to that platform.

If you need access from another device, put your own protected layer in front of
the panel (for example an authenticating reverse proxy or a private network) and
keep the panel itself on a loopback address. This repository does not provide or
document such a layer, and nothing here sandboxes the process.

## Where the data comes from

| Source | How it is accessed |
|---|---|
| SEEK | The adapter calls the SEEK website's own search endpoint and GraphQL detail query and identifies itself with a browser-style User-Agent. Listings and descriptions are fetched in two separate steps (`fetch`, then `details`). |
| Indeed | Through the `python-jobspy` library. |
| LinkedIn | Optional, through `python-jobspy`. Disabled in the example configuration. Description fetching is slow because it makes one request per job. |

None of these is an official, supported API for this use. The integrations are
best-effort: a platform can change its responses, rate-limit you or block
access at any time, and then they will stop working. Coverage and freshness are
not guaranteed, and the project makes no claim about either.

### Platform terms

This project does not state, summarise or interpret the terms of use of SEEK,
Indeed, LinkedIn or any other site, and it has not researched them. Whether
automated collection is acceptable, and under what limits, is for you to
determine from each platform's current terms and from the rules that apply to
you. The example configuration disables LinkedIn by default because it is the
slowest source and automated access to it is a grey area; turning it on is your
decision and your responsibility.

You control volume and pacing in your configuration (`queries`,
`pages_per_query`, `daterange`, `detail_sleep`, `results_wanted`). Keep them
modest. Reading the configuration, running `analyze`, `report` or `panel`, or
starting the panel never fetches anything. `fetch`, `details` and `run` use the
network; clicking **Update jobs** in the panel starts `run`. See
[manual-updates.md](manual-updates.md).

### Third-party data and redistribution

Listing text, company names, links and salary figures belong to the platforms
and advertisers that published them. They are stored locally for your own use.
Any license chosen for this project's code would cover the code; the content
you fetch stays with its publishers.

- Do not publish or share your database, raw descriptions, reports or label
  exports.
- The demo, screenshots, banners and video use hand-written fictional data, with
  links to the reserved `example.invalid` domain. The rest of the
  repository (what a public release will contain, and the private history of
  the development checkout) still needs its own review.
- Do not add fetched text to the repository, to tests or to an issue. See
  [contributing.md](contributing.md).

## Your own data

Treat the database, reports, labels, notes, `config.yaml` and your vocabulary
overlay as private. Notes are free text and may contain whatever you wrote;
nothing detects or removes names, employers or other identifying detail.
Switching the interface language rewrites nothing you stored.

## What is not claimed

No security audit has been done, and the panel has no authentication, encryption
or sandbox to rely on. The rules are not legal advice, and the project does not
guarantee compliance with any platform's terms or any law. Platform behaviour
can change without notice.
