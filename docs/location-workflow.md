# Locations and displayed representatives

The panel keeps each de-duplicated job group as one record. Its status, priority,
qualification correction and note apply to the whole group. The panel does not
create location-specific copies, corrections or upgrade tables.

Each group has one display representative. Selection order is:

1. a source whose `location` matches `location.local` in `rules.yaml`;
2. higher total score;
3. title in alphabetical order;
4. the first source row read from the database.

The representative rule changes which source in a group is shown. The outer
list ordering and the existing score, date, salary, eligibility and duplicate
heuristics remain unchanged.

The location box searches the representative only. It accepts an Australian
postcode, suburb, capital city, state/territory abbreviation or name, or
`Australia`/`澳大利亚`. It is available in All, New, Excluded and Labelled, and is omitted
from For you. It does not interpret Remote, Hybrid or Onsite.

Location results are arranged in three ordered blocks before pagination:

| Search | First block | Second block | Third block |
|---|---|---|---|
| Capital or its CBD postcode, e.g. Melbourne / 3000 | listings in the CBD postcode | other places in that capital's metropolitan area (ABS Greater Capital City area) | unknown or undeterminable |
| Suburb or postcode, e.g. Carlton / 3053 | listings in the selected postcode/locality | listings at state or capital-city precision in the same state | unknown |
| State, e.g. VIC / Victoria | listings in that state | — | unknown |
| Australia | any recognised Australian state | — | unknown |

For a capital, a listing that only says `Melbourne` is metropolitan, not the CBD;
state-only (`VIC`), `Australia` and undisclosed listings are in the last block;
a place clearly outside the metropolitan area, such as `Geelong VIC`, or in
another state is hidden. A place whose area cannot be determined stays in the
last block rather than being hidden. See [location data](location-data.md) for
the source and limits.

With **Ineligible last** on (All, New, Labelled), the whole result is first
divided by effective qualification, and these blocks apply inside each part;
a divider also marks that boundary.

Each block uses the view's normal score/date sort and pagination continues
across the concatenated blocks. The list renders one divider between adjacent
blocks, without headings or explanatory copy. A clear mismatch is omitted for
the current search only; clearing the box restores the full view.

Duplicate locality names are valid. Searching `Carlton` can therefore produce
Carlton listings from more than one state; `Carlton VIC` or a specific postcode
keeps the state constraint.

The parser uses the first listed location only. Alternatives separated by
slash, semicolon, newline, `and`, `&`, or a comma followed by a new state are
handled before parsing. A value such as
`Sydney NSW / Melbourne VIC` is treated as Sydney for search even though the
original text remains visible in the title metadata and complete group summary.
This can hide the Melbourne spelling in a Melbourne search; that is the expected
consequence of treating the group as one record.

The database schema version remains 3 for exact fetch batches. Current fresh
databases do not create location tables, and the location search performs no
write or migration. A database created by the unused location-scope candidate
may still contain those tables; the current application ignores them and leaves
their rows intact.

The bundled reference and its limits are described in
[location data](location-data.md).
