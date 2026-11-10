# Workspace plugin

The Workspace plugin lets you collect up to 10 jobs while you browse, look at them side by side, and hand them to a local coding agent.

- **Sidebar.** Add a job to the workspace from the job view (**Add to workspace**), by dragging its card onto the sidebar, with the + on a card, with the keyboard shortcut shown in Settings, or by selecting several jobs and adding them together. The sidebar lists the jobs you collected, so you can keep them while you search and filter.
- **Compare page.** Shows the collected jobs next to each other, with the full description, rule results and your label states.
- **Hand to agent.** Copies a one-line instruction such as `Use the jobs-compare skill to compare these jobs: seek:1, seek:2` for Claude Code or Codex. The line carries only job IDs.

## What it can read, and when

The plugin reads only the jobs you put into the workspace. The sidebar uses the job summary fields: title, company, location, salary, work type, listing date, score, priority and the panel link. The compare page also reads each collected job's full description, skills, tags, qualification result, eligibility evidence and your label states. It does not read note text. A job you have not added is never read, with one exception: the sidebar is told the ID, title and company of the job you are currently viewing, so it can show whether that job is already in the workspace.

It has no network access. It writes nothing to the board: no labels, notes or settings change. The workspace list lives in this browser only, up to 10 jobs, and does not follow you to another browser or device. Disabling the plugin keeps your existing board data unchanged.

## Enable it

Plugins are off by default. Open Settings > Plugins, read the permission review for Workspace and accept it. The same review and grant are available from the command line, as described in [plugins.md](../../docs/plugins.md):

```sh
python -m jobs.cli --config config.yaml plugin review workspace
python -m jobs.cli --config config.yaml plugin grant workspace
python -m jobs.cli --config config.yaml plugin revoke workspace
```

## Hand to agent

The agent must already have the Job Board MCP connected, including the read-only `get_jobs` tool (see [mcp.md](../../docs/mcp.md)). The instruction you copy contains job IDs only. The agent reads the jobs through the MCP, not from the plugin; without the MCP it cannot read them and should tell you so.

Two skills, `jobs-compare` and `jobs-write`, tell the agent how to use those jobs:

| Skill | Use |
|---|---|
| [`jobs-compare`](skills/jobs-compare/SKILL.md) | One job: fit check. Several jobs: evidence-backed comparison table, with CV overlap if you give a CV path. |
| [`jobs-write`](skills/jobs-write/SKILL.md) | A tailored CV and cover letter per job from your own material: Markdown drafts first, then Word (`.docx`) files once you approve the content. Needs your CV (or other notes about your background) and a private output directory; exporting needs a document tool in your agent. |

Both skills quote the stored job record and its rule evidence, treat job text and notes as untrusted data, and never write labels or notes back. A skill reuses job records it already read earlier in the same conversation. Otherwise, when the records will be reused (`jobs-write` whenever it reads jobs; `jobs-compare` for more than 3 jobs, sub-agents or several steps), they keep temporary per-job reference files in the operating system's temporary directory (`au-job-board/<run-id>/`, readable only by you) and delete them when the task ends. A short comparison works from the records it just read and writes no files. Your CV and results go only to the private directory you name, never into this repository.

## Install the skills

The skills are ordinary `SKILL.md` folders and ship inside this plugin. Nothing installs them for you, and the board never runs them. Copy or link both folders from the repository root into your client's user skills folder, then start a new session.

Locations checked against the Claude Code and Codex command-line tools and their bundled skill documentation when this was written (November 2026):

- **Claude Code:** `~/.claude/skills/<skill-name>/SKILL.md`. `claude plugin init --help` scaffolds into `~/.claude/skills/<name>/`, which loads in the next session.
- **Codex:** `$CODEX_HOME/skills/<skill-name>/`, which is `~/.codex/skills/` when `CODEX_HOME` is unset, as the skill-creator and skill-installer bundled with Codex describe it.

Copy (a later update of the plugin needs a new copy):

```sh
mkdir -p ~/.claude/skills
cp -R plugins/workspace/skills/jobs-compare plugins/workspace/skills/jobs-write ~/.claude/skills/
```

Link (the installed skill follows the repository):

```sh
ln -s "$PWD/plugins/workspace/skills/jobs-compare" ~/.claude/skills/jobs-compare
ln -s "$PWD/plugins/workspace/skills/jobs-write" ~/.claude/skills/jobs-write
```

For Codex, use `"${CODEX_HOME:-$HOME/.codex}/skills"` instead of `~/.claude/skills`. This repository keeps its own contributor skills in `.agents/skills/`; that does not make it a user install location for these two. Other platforms, project-level folders and later client versions can differ, so when in doubt follow your client's current skill documentation. The skills use only `name` and `description` in their frontmatter.

## Design notes and provenance

The skills were written for this project. The ideas behind them are common practice and also appear in open projects such as [career-ops](https://github.com/santifer/career-ops) (MIT): an A–F style structured evaluation and multi-job comparison, reasoning-based fit instead of keyword matching, asking before using undocumented experience, writing the CV before the cover letter, and Australian/New Zealand resume conventions. No third-party text is included.
