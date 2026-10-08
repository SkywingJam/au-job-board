# Script and storyboard

Archived planning notes for the 30-second demo. They were made in one pass, with no
approval round. Timings are frames at 30 fps.

## Approach

- **Audience:** someone job-hunting across several boards, watching muted on a
  feed or in a README. The captions carry the story, and the music is optional.
- **Story:** three pains, then the four steps that answer them, each shown with one
  real action: *Collect → Screen → Decide → Follow up*. One feature per pain, and only
  features that exist and work on the mock data.
- **Look:** the panel's own tokens (paper `#F5F4F0`, surface white, ink `#1D1C1A`,
  accent `#2357A6`, the panel's system font), one rounded window, a calm camera and
  no decorative effects. The intro and outro reuse the same palette, so the cut never
  leaves the product's visual world.
- **Pacing:** each action is followed by a still beat long enough to read the
  result. The camera moves only after the UI has responded, and the pointer comes to
  rest away from the text it points at.

### Feature choice

| Pain | Feature shown | Why this one |
|---|---|---|
| Listings scattered across boards | All view + detail line "2 duplicates (seek…; indeed…)" | One card for a job cross-posted on three boards is the clearest proof of consolidation |
| Screening takes hours | Excluded view → rule banner + quoted original sentence | Shows that the tool flags deal-breakers *and* why, so the user can trust or overrule it |
| Follow-ups slip | Label (Eligible / Want / Saved) + note suggestion "Prioritize" | Shows the two-second triage that feeds tracking |
| (payoff) | Labelled view + "Priority only" → the job just saved appears, "To apply" goes 4 → 5 | Closes the loop: the action from the previous shot changes the queue |

Left out on purpose: Settings / skill vocabulary, language and theme switching, and
search. They are real, but they explain *how* rather than *why*.

## Storyboard

| Frames | Time | Scene | On screen (real UI unless noted) | Caption |
|---|---|---|---|---|
| 0–119 | 0.0–4.0 s | Intro *(composited)* | Eyebrow "Job hunting, today"; three pain lines with line icons appear 0.67 s apart, then rise out | — (the lines are the text) |
| 120–287 | 4.0–9.6 s | **Collect** · take `t1_list` | All view. Pointer glides to "Graduate Software Engineer" and clicks; the detail opens; the camera eases in (1.45x) on the header; the pointer rests right of "2 duplicates (seek@…; indeed@…)" | COLLECT — Jobs from several boards in one ranked list — duplicates merged. |
| 288–461 | 9.6–15.4 s | **Screen** · `t2_excluded` | Clicks the Excluded tab, then "Junior Data Analyst"; the camera frames "Excluded by rule: citizenship.or_pr" and the quoted sentence; the pointer moves off-frame | SCREEN — Deal-breakers flagged, quoting the original posting. |
| 462–677 | 15.4–22.6 s | **Decide** · `t3_label` | For you view. Opens "Mobile Developer (Graduate)"; the camera frames the label panel (1.32x); clicks Eligible → Want → Saved; clicks the note field, types "prio", picks "Prioritize", presses Enter; the camera pulls out to show the "Note saved" toast and the card's status dots | DECIDE — Label and note each job in a few clicks. |
| 678–809 | 22.6–27.0 s | **Follow up** · `t4_queue` | Clicks Labelled, then "Priority only"; the queue shows the job just saved, and "To apply 5" in the stats line; the pointer settles on that card | FOLLOW UP — Priority jobs become your apply queue — nothing slips. |
| 810–899 | 27.0–30.0 s | Outro *(composited)* | Last real frame shrinks away; "AU Job Board", "Collect. Screen. **Follow up.**", "Every lead in one calm, ranked list." | — |

Transitions: the window rises in at 4.0 s; later takes start with a 6-frame
lift from 35 % opacity; captions fade and slide in over frames 4–18 of each take and
fade out over its last 8. Music fades in over 0.5 s and out over the last 1.5 s. A
soft tick plays at each logged click.

## Take scripts (absolute take time, seconds)

The capture tool waits for each planned time (`at(t)`) and logs a warning if a step
runs late. In the final capture, none did. Pointer moves have fixed durations (eased
cubic, slight arc). Targets are re-measured from the DOM right before each move.

- `t1_list` (5.6 s): start (1020,560) · 0.70 move 0.85 s → card · 1.75 click ·
  2.35 focus box `.d-head + .d-tags` · move 0.9 s to rest beside the duplicates line.
- `t2_excluded` (5.8 s): 0.40 move → Excluded tab · 1.25 click · 1.90 move → card ·
  2.85 click · 3.30 focus `.rule + first .step-block` · move pointer out of frame.
- `t3_label` (7.2 s): 0.35 move → card · 1.10 click · 1.55 focus `.decide` ·
  2.25 Eligible · 2.95 Want · 3.70 Saved · 4.40 note field · 4.75 type "prio" (110 ms/key) ·
  6.00 click "Prioritize" · 6.35 Enter · small drift away.
- `t4_queue` (4.4 s): 0.30 move → Labelled tab · 1.05 click · 2.15 click "Priority only" ·
  2.70 focus `.list` · move to rest on the new card.

## Caption rules

One line, at most about ten words, 44 px semibold in ink, with a 25 px step chip in
the accent colour. Captions sit in their own band under the window, so they never
cover the UI. Wording claims only what the shot shows: several boards (not "all"),
quoting the original posting (the panel labels it "Original excerpt"), and an apply
queue (the panel's own words for "Priority only").
