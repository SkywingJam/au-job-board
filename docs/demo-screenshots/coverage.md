# Coverage

Counts are original PNGs. Total 102 (56 desktop, 42 phone, 4 tablet).

## A. Main views — 40 / 40
Recommended, All, New, Excluded, Labelled × en/zh × light/dark × desktop 1440x1000 / phone 390x844 (first-screen viewport shots).
- Skipped: full-page companions. The panel scrolls inside its own list/detail panes, so the document is never taller than the viewport; a full-page capture would equal the viewport shot.

## B. Settings — 16 / 16
Skills vocabulary and Language & display × en/zh × light/dark × desktop/phone. Language & display shows the read-only "Profile: 485 / last analyze" item (no dropdown, no save). The vocabulary page shows 22 seed terms with hit counts, source colours and search.

## C. Job detail — 21 (≥ 12 required)
- Retained (`linkedin:DEMO-013`, duplicate pair with salary/skills), excluded (`indeed:MOCK-106`, rule `citizenship.or_pr` with excerpt), labelled (`seek:MOCK-101`, status + default/custom/emoji note), each × en/zh × desktop/phone, with both themes covered (en desktop light, en phone dark, zh desktop dark, zh phone light).
- Six scrolled-to-description shots (en desktop dark, zh phone light) for the three kinds.
- Extras: labelled/applied (`indeed:DEMO-017`), excluded + clearance (`linkedin:MOCK-104`), job without description (`linkedin:DEMO-019`).

## D. Interactions and edge cases — 25
Done: search hit, search no result (desktop en dark, phone zh light), combined filter (eligible + want + Python), more-filters expanded, labelled view with Priority filter, note suggestions in en (English names) and zh (Chinese defaults), pick → save → filled back (en; zh saved), multi-tag note typed with half-width semicolons/spaces/emoji and after reload, label states (saved; skipped, red), labelled view afterwards, skills search, add-skill typed and done (writes only the mock `skills.local.yaml`), long title on a phone (list + detail, no horizontal overflow), tablet 768x1024 ×4.
- Skipped: skill alias editing (pencil dialog) — not captured; add/search covers the skills interaction.
- Skipped: missing-salary and long-note edge shots are covered inside A/C/D images (e.g. `linkedin:MOCK-108`, `seek:MOCK-107`) rather than as separate scenes.

## Checks
- Horizontal overflow measured per shot: none (`horizontal_overflow` is false for all 102).
- No Demo banner element in the served HTML; Profile 485 recorded by a normal `analyze` run.

## Visual issues (found on the first pass, re-shot after the fix)
- Fixed in `f304d0d19669313764419fe1c025be7c6fe53803`: English UI showed Chinese in salary chips (`/年`, `（正文）`) and in the empty-description placeholder. The whole library was re-shot on a new mock root afterwards; English shots now read `A$75k–85k/yr`, `(from description)` and "(No description was captured for this posting)". Chinese UI unchanged.
- Fixed in `847af8bafdac122de68f7dea1318bc08241c32cc`: English notes now display `Relocation; follow up; 🎉 great team;` (a space after the separator between tags; the trailing `;` stays single, stored form `tag；tag；` unchanged, Chinese unchanged).
  Re-shot (7, partial re-shoot on a new mock root, everything else left as shot at `f304d0d`): `C-detail-labelled-en-light-desktop`, `C-detail-labelled-en-dark-mobile`, `C-detail-labelled-scrolled-en-dark-desktop`, `C-detail-labelled-demo-017-en-light-desktop`, `D-long-title-detail-en-light-mobile`, `D-note-multitag-typed-en-dark-desktop`, `D-note-multitag-reloaded-en-dark-desktop`.
  Not re-shot because they show no multi-tag English note: the single-tag note shots (`D-note-picked/saved-en`), all zh shots, list views.
- No other overflow or truncation found in the reviewed contact sheets and spot-checked originals.
