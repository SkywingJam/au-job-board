---
name: jobs-pr-finish
description: Triage AU Job Board PR or auto-review feedback and verify fixes, or finalize an existing PR for handoff.
---

# Finish a PR review round

## 1. Establish the round and authorization

Read root AGENTS and the user's current constraints. Confirm repository, PR, base/head, actual diff and CI revision; attach the PR to the task when the app tool is available.

Record the snapshot time/head and authorized stop point. “Check the latest/all issues” means one complete snapshot including older unresolved items, then authorized fixes and verification. Continuous monitoring, external replies, resolving a human reviewer's thread, new review requests, merge, release and deployment follow current-task authorization; reuse existing permission. Threads opened by an automated or AI reviewer are yours to close under **Resolve automated-review threads** below. A no-new-review instruction persists after a push. Platform-triggered reviews differ from a manual request. Default babysitting handles the initial automatic review and one repair batch, then hands back; do not request another Codex review without explicit user authorization. Do not infer another repair cycle from a platform-triggered review after pushing.

For feedback, continue to **2**. For a handoff with no feedback check requested, go to **4** once the agreed implementation/independent acceptance evidence is available.

## 2. Collect a complete snapshot

Read all four sources and their pages:

- Conversation/issue comments.
- Inline review comments.
- Review submissions, including bodies with no inline comment.
- Review threads with resolution/outdated metadata and their comments.

When available, GitHub MCP's `fetch_pr_comments` provides a merged timeline and `list_pull_request_review_threads` supplies thread metadata. Establish completeness from actual responses; use `gh` REST/GraphQL when the tool cannot establish pagination coverage.

For the three REST feeds, replace NUMBER with the confirmed PR number:

```bash
gh api --paginate 'repos/SkywingJam/au-job-board/issues/NUMBER/comments?per_page=100'
gh api --paginate 'repos/SkywingJam/au-job-board/pulls/NUMBER/comments?per_page=100'
gh api --paginate 'repos/SkywingJam/au-job-board/pulls/NUMBER/reviews?per_page=100'
```

For GraphQL `reviewThreads`, request `pageInfo { hasNextPage endCursor }` and advance `after` to the end; paginate comments within each thread too. REST inline comments alone lack resolved state. Normalize with feed type and stable ID; a quoted summary is not automatically another defect.

**Coverage gate:** each source is exhausted or explicitly marked incomplete. On failure/truncation, retry the failed read once or try one alternate path. If it remains incomplete, retain known feedback and report the missing coverage. A partial snapshot cannot support “all clear.”

## 3. Judge and verify every collected finding

Keep a compact local ledger: source/ID or URL, reviewed SHA when supplied, affected behavior, disposition/evidence and fixed SHA when applicable. Each candidate finding needs a disposition: confirmed, already fixed, not applicable/unsupported, duplicate or unresolved. Resolved/outdated metadata alone does not prove correctness; issue comments may have no reviewed SHA.

Read the affected call chain and reproduce plausible defects. Batch common-root fixes and leave material uncertain product choices to the user with evidence. Use [jobs-verify](../jobs-verify/SKILL.md) for affected checks and UI journeys; root AGENTS defines independent-review requirements. Independently inspect the integrated diff after delegated fixes.

Keep the agreed scope and completion criteria. Repair this change's regressions and small affected edge cases; record justified deferrals or rejection of unrelated low-impact improvements. Escalate material data, safety or core-workflow risks to the lead. Finishing the round requires an evidenced disposition for every item, not implementing every suggestion.

Proceed when every collected candidate has a disposition and confirmed fixes have actual verification results or explicit gaps.

### Resolve automated-review threads

`main` merges only when every review thread is resolved (ruleset `required_review_thread_resolution`), so an open thread blocks the PR even with green checks. For a thread opened by an automated or AI reviewer (Codex auto review and the like), resolve it yourself once its finding has an evidenced disposition; no separate authorization is needed:

- **Fixed:** the fix is pushed and its verification ran. Resolve the thread; the commit is the record.
- **Not applicable, duplicate or deferred:** reply in the thread with one line giving the disposition and reason, then resolve it.
- **Unresolved or escalated:** leave it open and report it.

A human reviewer's thread stays open until that reviewer or the user closes it, or the user authorizes you to.

## 4. Update and hand off

For an authorized PR update, describe the final problem/behavior and actual validation on the candidate. Verify base/head/diff and checks on the latest revision, then read the PR's merge state and count open review threads: report whether GitHub shows it mergeable and name whatever still blocks it. Follow the contributor guide's delivery route; retain draft status while unfinished when requested, and set ready only after agreed local acceptance/review.

Stop when this snapshot's findings have dispositions, verification/coverage gaps are reported, authorized updates are complete and the user has the remaining CI/acceptance status. Required verification or feedback coverage gaps keep the result a validation-pending candidate, not implemented, complete or merge-ready. Wait for user acceptance at that boundary.

Only for an explicitly authorized delegated repair pilot, read the repository's [bounded feedback repair procedure](../../../docs/pr-feedback-pilot.md) and establish its task contract before handing off. It defines the single-pass repair boundary, selective escalation and lead acceptance; it requires no machine-local skill or watcher. Other continuous care needs its own explicit duration, budget and action limits. Ordinary checks remain one complete snapshot.
