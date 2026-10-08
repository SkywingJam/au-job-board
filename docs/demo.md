# Standalone synthetic demo

A self-contained demo of the job panel that runs on 19 hand-written, fictional
postings (16 distinct jobs after de-duplication). It scrapes nothing, needs no
network, reads none of your private files and writes only to its own directory.

This is a demo, not a public release and not a production switch. Qualification
handling stays the keyword-based hard filter described in
[legacy-rule-profiles.md](legacy-rule-profiles.md).

## Prerequisites

- Python 3 (verified on 3.13) and `PyYAML` (from `requirements.txt`; the demo does not import
  `python-jobspy`).
- Run from the repository root.

```bash
python3 -m venv .venv
.venv/bin/pip install "PyYAML>=6.0"
```

## Generate, start, stop

One command (generate if missing, then start; reuse the data if it exists):

```bash
.venv/bin/python -m jobs.demo run
```

Then open <http://localhost:8091>. Press `Ctrl-C` to stop.

Or step by step. `generate` does not start any server, so you can inspect the
result first:

```bash
.venv/bin/python -m jobs.demo generate     # writes out/demo/ only
.venv/bin/python -m jobs.demo serve        # serves an existing demo
```

Options: `--root DIR` (default `out/demo`), `--lang en|zh` (language of the
synthetic labels, default `en`; generation time only), `--port N` (default `8091`),
`--host` (loopback only), `--today YYYY-MM-DD` (anchors the relative listing
dates; default today).

To reset the demo, stop it, remove `out/demo/` yourself and run it again. The
tool never deletes or overwrites an existing directory.

## Language

The demo's own text defaults to English. `--lang zh` generates a Chinese
variant of the synthetic labels (notes and the "ineligible" reason); the job
postings themselves stay English either way, and scores, tiers and label values
are identical in both. The **Demo · Synthetic data** banner follows the panel's
UI language (English / 中文) and switches with the language button. The chosen
label language is recorded in `demo-manifest.json`.

## What is isolated

Everything the demo reads or writes is inside the demo root (`out/demo/` by
default) or is one of the hand-written inputs under `demo/`:

| Path | Role |
|---|---|
| `demo/demo.jobs.json` | The synthetic postings and their labels (source) |
| `demo/demo.config.yaml` | Config template: loopback, port 8091, no scraping section |
| `demo/demo.rules.yaml` | Generic synthetic filter / scoring rules |
| `demo/demo.skills.yaml` | Generic technology vocabulary |
| `<root>/config.yaml` | Generated; every path in it is an absolute path inside the root |
| `<root>/jobs.db` | Demo-only SQLite DB (labels, applied status) |
| `<root>/rules.yaml`, `skills.yaml` | Copies of the demo rules / vocabulary |
| `<root>/skills.local.yaml` | Created when you add a skill or alias in Settings |
| `<root>/reports/` | Demo report directory (unused by the panel) |
| `<root>/demo-manifest.json` | Written last; a directory without it is not a demo |

Every file the panel reads or writes inside the root (`demo-manifest.json`,
`config.yaml`, `rules.yaml`, `skills.yaml`, `jobs.db`, its `-wal` / `-shm` /
`-journal` files and `skills.local.yaml`) must be a plain, single-link file in
that root. A symbolic link (including a dangling one), a non-regular file or a
hard link to a file elsewhere is refused. The check runs when the server starts
**and before every request**, so a link added later makes requests fail with
HTTP 403 (`demo data check failed: ...`) without reading or writing the target.
A valid manifest does not authorize files outside the root. This protects
against mistakes and links that already exist when a request arrives; it is not
a defense against a deliberately racing local attacker.

The demo never reads or falls back to `config.yaml`, `rules.yaml`,
`skills.yaml`, `skills.local.yaml`, `data/jobs.db` or the production `out/`.
Relative DB paths resolve against the project root, not the config directory,
so the generated config uses absolute paths and the launcher refuses to start if
any path points outside the demo root.

The only change to the panel is two explicit config fields: `skills_path`
(where the vocabulary is read from and edits are written) and `demo` (turns on
the banner). Without them the panel behaves exactly as before.

## No-network guarantee

- No source adapter is imported, no scraping config exists, and the demo opens
  no outbound connection.
- The server binds only to an IPv4 loopback address (`127.0.0.1` or
  `localhost`). IPv6 (`::1`), wildcard and LAN addresses are refused.
- Port 8090 (the usual production panel port) is refused. If 8091 is busy the
  launcher reports it and exits; it never stops another process.
- Job links point to `https://example.invalid/...`, a reserved name that cannot
  resolve. The page loads no external scripts, fonts or images.
- `tools/test_demo.py` enforces this at runtime with an audit-hook guard
  (file, SQLite, socket and subprocess events) and checks the guard itself
  rejects private paths and outside hosts.

## What the demo shows

Recommended / All / New / Excluded / Labelled views, filters, search, the
detail pane, labelling, applied status (including red "skipped"), Settings
(skill vocabulary edits, language, theme), the amber **Demo · Synthetic data**
banner (中文界面显示「演示 · 合成数据」), English and Chinese UI, and the narrow-screen layout. Example cases:

- one posting cross-listed on three platforms with different company
  punctuation, and one near-duplicate with a level suffix on the title;
- salary as annual, hourly and daily figures, mined from the description,
  unparseable text and missing;
- a stale (45-day) posting, a posting with no description yet, two jobs
  excluded by rules (citizens-only, security clearance) and two that a
  "citizen / PR or valid work visa" override keeps;
- labelled and unlabelled jobs; saved, applied and skipped.

## Limits

- Not a measure of any real candidate or market. Names, companies, text and
  scores are invented; the scores come from the demo rules only.
- Qualification is keyword-based: the "Citizen / PR" and similar tags are cues
  in the posting text, not a verdict about any candidate. The demo uses its own
  generic synthetic rules, and its generated configuration has no rule-profile
  section, so all of those rules are on.
- Labels and settings persist in `<root>/jobs.db` and `skills.local.yaml` until
  you remove the directory. Listing dates are relative to the generation date,
  so regenerate on a later day (new root) to refresh them.
- Cookies are per host, not per port: language and theme choices made on
  `localhost:8091` also apply to any other app served on `localhost`.
- On a phone-width screen the detail view covers the banner while it is open.
- Panel logic, filters, ordering and tag wording are the existing ones, not
  demo-specific.
- Notes: the panel stores note tags separated by the full-width `；`, and the
  default tags are stored as fixed Chinese values (the English interface only
  displays English names for them). The "Priority" filter reads that stored
  default tag by exact match. The demo's English notes are plain text and do
  **not** trigger it; use `--lang zh` to generate notes that do. Picking the
  default suggestion in the panel always stores the canonical tag.
- The server is IPv4 only (`127.0.0.1` / `localhost`).

## Tests

```bash
.venv/bin/python tools/test_demo.py
```

Uses only temporary directories; touches no production file. Tested
automatically: generation, de-duplication, label states, overwrite refusal,
all panel views / filters / detail / label / settings requests over HTTP, link
and hard-link refusal before and after start (against external synthetic
sentinel files, over real HTTP and CLI output), loopback-only binding, and a
runtime guard (files, SQLite, sockets, subprocesses) that itself is checked to
reject outside synthetic files, repository files and external hosts.

Verified in a browser earlier (desktop and 375px width, English and Chinese,
light and dark): views, filters, detail, labelling and applied / skipped status,
adding a skill in Settings, and the banner. **Not** browser-tested: keyboard
and touch behaviour, reduced-motion, other browsers, and the link-refusal 403
page. The banner's language switch was only checked in HTML output.

## Screenshot library (mock data, normal panel)

A separate set of screenshots lives in [`docs/demo-screenshots/`](demo-screenshots/README.md).
It is **not** Demo mode: a hand-written synthetic mock database is analysed with the
normal `analyze` command and served by the ordinary `jobs.cli panel` entry, so no
Demo banner appears. It is independent of the generator and tests described above.

## Product demo video (mock data, normal panel)

A 30-second, 1920x1080 walkthrough recorded from the same kind of mock root and normal
panel entry: [`docs/demo-video/product-demo-30s.mp4`](demo-video/product-demo-30s.mp4)
(cover [`cover.png`](demo-video/cover.png)). How it was made, the storyboard, the
source and the versions are in [`docs/demo-video/README.md`](demo-video/README.md).
The Remotion media production project (`media/demo-video/`) and its capture tools
were development-only and are **not** part of the public candidate; the finished
video, cover and banners are. The video is not a product dependency.
