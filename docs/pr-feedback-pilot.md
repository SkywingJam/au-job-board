# Bounded PR feedback repair

This opt-in procedure delegates one feedback-repair pass while the lead retains
core decisions and final acceptance. It does not configure a model, watcher or
review integration. Ordinary one-shot checks follow
[jobs-pr-finish](../.agents/skills/jobs-pr-finish/SKILL.md).

## Establish the task contract

Record the PR, head branch and starting SHA, scope, completion criteria,
executor, allowed files/actions, necessary checks, stop point and time/call
budget before handing off. Use the repository's
[verification skill](../.agents/skills/jobs-verify/SKILL.md). Keep one writer on
the branch during the handoff; report unexpected head movement before writing.
Replies, push, merge, release and deployment require their respective task
authorization. Thread resolution follows
[jobs-pr-finish](../.agents/skills/jobs-pr-finish/SKILL.md#resolve-automated-review-threads):
automated-review threads are resolved by the executor once dispositioned; a
human reviewer's thread needs authorization.

**Default: process the initial automatically triggered review, make one batch of
permitted repairs, verify it and hand back.** Do not post a Codex review request,
re-request a reviewer, or use another mechanism to solicit another review unless
the user explicitly authorizes it. A repair push does not authorize a new loop.
If the integration itself starts another review, record that fact; do not claim
it was disabled or start another repair cycle on that basis.

Extra review or repair passes need explicit task bounds. Authorization to request
one additional review does not by itself extend the repair budget. Carry attempt
history across comments, commits and agents. If a repair reveals the same root
cause is still not understood, stop iterative patching and hand that issue to
the lead while continuing independent permitted work.

## Process the single batch

1. Collect the complete initial feedback snapshot using the four sources and
   pagination in jobs-pr-finish, bound to the actual reviewed SHA. A pending
   review or incomplete read is not zero feedback. When no review was triggered,
   report that fact rather than creating one to satisfy this procedure.
2. Record each finding's stable ID, disposition, evidence and repair history.
   Reproduce plausible defects and group common-root repairs. Escalate affected
   issues under the boundary below; continue other independent small repairs.
3. Make the authorized repair batch, execute affected checks and push only when
   authorized. The lead independently checks the integrated result. Follow CI
   only within the agreed budget; report failures or pending checks accurately.
4. Hand back the actual final SHA/diff, ledger, executed checks, CI status,
   unresolved/escalated items and budget use. Finish after this batch, no
   findings, budget exhaustion or an unsafe shared state. Do not wait for merge
   or closure, request another review, or start a repeated repair loop.

## Escalate selectively

Skip the affected issue and continue independent permitted work when it involves
human-data/overlay write paths, data models/migrations, heuristic or scoring
behavior, deduplication/ownership, locking/transactions/async saves,
permissions/privacy, or CI/release authority. Small diffs can still cross these
boundaries. Also escalate uncertain findings or unavailable required validation.

Send the collected list to the lead rather than interrupting the user for every
item. The lead handles technical rework under existing scope; product choices,
scope expansion, heuristic behavior changes and new authority go to the user.

## Lead acceptance and resource use

A resolved thread, green CI or executor completion claim does not replace
independent acceptance. Recheck affected integration boundaries and preserve
required verification gaps. Report accepted limitations rather than expanding
scope until every suggestion is implemented.

Use targeted regression and risk-based independent review during development.
An explicitly requested, scoped audit after a substantial change can supplement
this process; neither repeated automatic review nor routine full-repository
audits are a default gate. These limits reduce review cost without promising a
clean first review or relaxing data-protection requirements.
