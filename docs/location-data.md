# Offline Australian location reference

`jobs.locations` resolves supported search terms with bundled public reference
data. It does not fetch at runtime, open a database, store parsed locations or
assert that a place is an office address. All examples and tests use
hand-written synthetic strings.

## Source, license and snapshot

Contains data from [GeoNames](https://www.geonames.org/), licensed under
[Creative Commons Attribution 4.0](https://creativecommons.org/licenses/by/4.0/).
The [official postal-code readme](https://download.geonames.org/export/zip/readme.txt)
identifies this license and states that accuracy, timeliness and completeness
are not guaranteed. GeoNames is the publisher of this reference, not an
Australian postal authority.

- Source: [AU.zip](https://download.geonames.org/export/zip/AU.zip), downloaded
  2026-10-22 UTC with the official readme.
- Archive SHA-256:
  `fc6264334dab43ac67e3b6950bcd733918681b0158e65412448e59fcec054ded`.
- Readme SHA-256:
  `fff60148da20f4d182859d926518efa5b0f3949f3c3273a284901325f1f8f008`.
- `AU.txt` has 18,521 rows; 18,514 distinct `postcode`, `locality`, `state`
  triples remain after projection, deduplication and sorting.
- Seed SHA-256:
  `011ef7d2b65ba2a06af66f43e8ab4f1522e7083b33f5702826681b4b1a0f45ce`.
- Reference version: `geonames-au-011ef7d2b65ba2a0`.

The machine-readable [manifest](../jobs/data/location-reference.json) records
URLs, download time, response metadata, byte counts, hashes, source row count,
attribution and seed schema. The [TSV](../jobs/data/au-postcodes.tsv) contains
only postal code, locality and state. Coordinates and lower administrative
fields were removed.

Postal codes stay four-character strings, including `0800`. The relationship is
many-to-many: `2620` has ACT and NSW rows; `0872` has NT, SA and WA rows. These
are publisher associations, not inferred boundaries.

Eight capital cities have a separately maintained CBD postcode:

| Capital | State/territory | CBD postcode |
|---|---|---|
| Adelaide | SA | 5000 |
| Brisbane | QLD | 4000 |
| Canberra | ACT | 2600 |
| Darwin | NT | 0800 |
| Hobart | TAS | 7000 |
| Melbourne | VIC | 3000 |
| Perth | WA | 6000 |
| Sydney | NSW | 2000 |

The mapping is explicit because postal-box and other special postcodes in the
seed cannot be used to infer a CBD. Other supported place names use GeoNames
locality precision.

## Metropolitan-area reference (ABS)

A capital or CBD-postcode search also needs to know which other places belong to
that city. This is not a hand-written list of suburbs or a distance guess: it is
the Australian Bureau of Statistics' Greater Capital City Statistical Area
(GCCSA, ASGS Edition 3, 2021) applied to postcodes.

- Sources (CC BY 4.0, [ABS copyright](https://www.abs.gov.au/website-privacy-copyright-and-disclaimer)):
  the ASGS Edition 3 allocation files
  [`MB_2021_AUST.xlsx`](https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs/edition-3-july-2021-june-2026/access-and-downloads/allocation-files/MB_2021_AUST.xlsx)
  (mesh block to GCCSA) and
  [`POA_2021_AUST.xlsx`](https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs/edition-3-july-2021-june-2026/access-and-downloads/allocation-files/POA_2021_AUST.xlsx)
  (mesh block to postal area), and the 2021 Census
  [Mesh Block counts](https://www.abs.gov.au/census/guide-census-data/mesh-block-counts/2021/Mesh%20Block%20Counts%2C%202021.xlsx).
  URLs, byte counts, SHA-256 and `Last-Modified` values are in
  [`metro-reference.json`](../jobs/data/metro-reference.json).
- Transformation: for each four-digit postal area (POA) the usual-resident
  persons of its mesh blocks are summed per GCCSA (mesh-block area if a POA has
  no persons). The GCCSA with the largest weight and its share are written to
  [`au-postcode-metro.tsv`](../jobs/data/au-postcode-metro.tsv)
  (`postcode`, `gccsa`, `share_permille`, `basis`; 2,643 rows, verified by
  SHA-256 at runtime like the seed). `tools/build_metro_reference.py` rebuilds
  both files from the ABS downloads (standard library only; it is a maintainer
  tool and nothing downloads at runtime).
- Capitals map to ABS names: Greater Sydney, Greater Melbourne, Greater
  Brisbane, Greater Adelaide, Greater Perth, Greater Hobart, Greater Darwin and,
  for Canberra, the Australian Capital Territory (one GCCSA).
- Decision: a postcode is *inside* a capital's area when its dominant GCCSA is
  that capital and holds at least 90% of its persons; *outside* when the
  dominant GCCSA is another and holds at least 90%; otherwise it is
  *undetermined*. 25 of 2,643 postal areas fall below 90%.

Known limits, none of which is silently guessed: PO-box and large-volume
postcodes have no POA (they are ignored when another postcode of the same place
decides, otherwise the place is undetermined); a postcode shared by several
states (for example 2620, 0872) is undetermined; places missing from the
GeoNames seed (for example Gold Coast) have only state precision; 2021
boundaries and 2021 population are used, while ASGS Edition 4 (July 2026) has
published allocation files but its 2026 Census counts are not available. A
cross-check with the Edition 4 allocation by area changed the verdict of 36
postcodes, always to or from *undetermined*; none moved between inside and
outside. A POA is an approximation of a postcode, not a delivery boundary.

## Search contract

```python
from jobs.locations import match_tier, parse_listing, parse_target

target = parse_target("3053")
tier = match_tier(parse_listing("Melbourne VIC"), target)
assert tier == "near"
```

`parse_target(text) -> dict` accepts one of:

- an Australian postcode;
- a locality, including an ambiguous locality such as `Carlton`;
- one of the eight capital cities;
- a state or territory abbreviation or full name;
- `Australia` or `澳大利亚`.

Work-mode markers and multiple alternatives such as
`Melbourne VIC / Sydney NSW` are rejected as search terms.

Required mapping:

| Search | `exact` | `near` | `unknown` |
|---|---|---|---|
| Capital / CBD postcode | the CBD postcode | the rest of that capital's metropolitan area (below) | places that cannot be placed |
| Suburb / non-CBD postcode | the selected locality/postcode | city- or state-level precision in a matching state | no recognised place |
| State | that state | — | no recognised place |
| Australia | any recognised state | — | no recognised place |

For a capital or CBD postcode, `near` is a city-level listing of that capital
(`Melbourne VIC` never becomes `3000`) or a place whose postcodes are inside the
ABS metropolitan area. A clearly outside place in the same state (`Geelong VIC`)
is `hidden`, like a place in another state. `unknown` here also holds state-only
(`VIC`), country-only and undisclosed listings, and places that are undetermined
under the rules above. Suburb, non-CBD postcode, state and national searches keep
their meaning. Clearing the search passes
`target=None`; a blank target is an input error rather than silently clearing
the box.

`parse_listing(raw) -> dict` returns one query-time fact for the first listed
location. It removes observed work-mode words for geography parsing but does not
assign a work mode. It recognizes explicit postcodes, a known state at an edge,
capital city names, `Melbourne City Centre` / CBD wording, and exact locality
names within a state. Missing or unresolved state evidence is `unknown`.

Supporting examples:

- `Melbourne VIC` is city precision and does not become postcode `3000`.
- `Melbourne City Centre VIC` is postcode precision `3000`.
- `Carlton VIC` resolves to the VIC locality; unqualified `Carlton` can match
  the VIC, NSW and TAS namesakes when used as a search target.
- A state-qualified search does not admit another state's namesake:
  `Carlton VIC` does not exactly match Carlton NSW. The same state check applies
  to shared postcodes such as 2620.
- Known suburbs win before the capital-prefix fallback, so
  `Melbourne Airport VIC` and `Sydney Olympic Park NSW` remain locality searches.
- `Sydney NSW Hybrid / Melbourne VIC Remote` is parsed as Sydney for search.
  The raw string remains available for display.
- `Geelong VIC` has locality postcode evidence and lies outside Greater
  Melbourne, so a `Melbourne` or `3000` search hides it; `Frankston VIC` and
  `Carlton VIC` are metropolitan (`near`).

The parser handles common listing location/spelling forms, not arbitrary prose,
street addresses, real office locations or distance. Metropolitan membership is
only the postcode-level ABS reference above.

## Reproducing the seed

The following in-memory transformation reproduces the TSV. GeoNames changes
over time; compare archive and readme hashes before treating a new download as
the same snapshot. A deliberate refresh must update provenance, seed hash,
version and tests together.

```python
import csv, hashlib, io, urllib.request, zipfile

base = "https://download.geonames.org/export/zip/"
with urllib.request.urlopen(base + "AU.zip", timeout=45) as response:
    archive = response.read()
with urllib.request.urlopen(base + "readme.txt", timeout=45) as response:
    readme = response.read()
print("AU.zip", hashlib.sha256(archive).hexdigest())
print("readme.txt", hashlib.sha256(readme).hexdigest())
source = zipfile.ZipFile(io.BytesIO(archive)).read("AU.txt")
rows = list(csv.reader(io.StringIO(source.decode("utf-8")), delimiter="\t"))
states = {"ACT", "NSW", "NT", "QLD", "SA", "TAS", "VIC", "WA"}
assert all(len(r) == 12 and r[0] == "AU" and r[4] in states
           and len(r[1]) == 4 and r[1].isascii() and r[1].isdigit()
           for r in rows)
triples = sorted({(r[1], r[2], r[4]) for r in rows})
seed = ("postcode\tlocality\tstate\n" +
        "".join("\t".join(row) + "\n" for row in triples)).encode("utf-8")
print("seed", len(triples), hashlib.sha256(seed).hexdigest())
```

## Verification

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tools/test_locations.py
```

The coverage includes capital/CBD behavior, postcode order, ambiguous locality
names, metropolitan-area tiers for all eight capitals, state and country searches, unknown rows, first
location only, seed integrity, malformed input and local-preference patterns.
It does not establish accuracy for arbitrary source prose, production-office
coverage or places absent from the public reference.

The module has no persistence, database, HTTP or UI responsibility. Those
boundaries are covered by the explicit [public test runner](public-tests.md).
