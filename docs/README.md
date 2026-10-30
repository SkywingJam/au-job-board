# Documentation

Start with the [project README](../README.md) (or the
[Chinese version](../README.zh-CN.md)). The detailed documents below are in
English.

## Guides

| Document | What it covers |
|---|---|
| [install.md](install.md) | Source installation for a new machine, and the license boundary |
| [docker.md](docker.md) | Optional Docker / Compose deployment, persistent volumes, commands and backup; macOS still prefers source |
| [agent-install.md](agent-install.md) | The same install steps for an AI coding agent |
| [usage.md](usage.md) | The fetch, analyze, report and panel workflow, commands, labels and notes, local data and provenance |
| [manual-updates.md](manual-updates.md) | Editable next-run fetch settings, background updates, configuration timing and failure states |
| [location-workflow.md](location-workflow.md) | Group representative choice, one-box location search, block ordering and limits |
| [location-data.md](location-data.md) | Offline Australian reference data, attribution, precision and matching limits |
| [agent-api.md](agent-api.md) | Authenticated HTTP snapshots (read-only by default), exact batches, external report clients and the opt-in batch label write |
| [mcp.md](mcp.md) | Optional MCP over app-owned Streamable HTTP or local stdio: four read tools, plus an opt-in batch label tool |
| [public-configuration.md](public-configuration.md) | The configuration file, paths, sources and which files stay private |
| [legacy-rule-profiles.md](legacy-rule-profiles.md) | The six rule profiles, per-rule switches and how to apply a change |
| [eligibility-profiles.md](eligibility-profiles.md) | The separate, read-only `eligibility` section and command |
| [safety-and-sources.md](safety-and-sources.md) | Panel exposure, how listings are collected, platform terms and redistribution |
| [demo.md](demo.md) | The offline synthetic demo |
| [launchd-examples.md](launchd-examples.md) | macOS LaunchAgent templates for the panel and a scheduled run |
| [contributing.md](contributing.md) | Working conventions, data rules and tests |
| [public-tests.md](public-tests.md) | The curated public offline test entry and the CI boundary |

## Showcase material

All of it uses synthetic data.

| Item | What it is |
|---|---|
| [demo-screenshots/](demo-screenshots/README.md) | 130 direct browser screenshots of the panel, including location search and mobile drawers (English and Chinese, light and dark, desktop, tablet and phone) |
| [YouTube demo](https://youtu.be/AAfdZ548Gao) · [MP4](demo-video/au-job-board-v1.3-demo.mp4) · [notes](demo-video/README.md) · [script](demo-video/script.md) | The 44-second v1.3 animation based on synthetic panel, HTTP API and MCP outputs, plus its cover and two banners |

## Verification and data policy

| Document | What it records |
|---|---|
| [installation-verification.md](installation-verification.md) | Dated source-install and cloud CI verification records (2026-10-08), their limits, and a short note on the earlier record |
| [test-data-policy.md](test-data-policy.md) | The rule that default tests use only synthetic data, and the private-export boundary |
