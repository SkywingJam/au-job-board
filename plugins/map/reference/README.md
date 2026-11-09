# ABS Postal Areas 2026 map reference

This package contains Australian Bureau of Statistics (ABS), Australian
Statistical Geography Standard (ASGS) Edition 4, Postal Areas 2026, copyright
Commonwealth of Australia, licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
[ABS copyright](https://www.abs.gov.au/website-privacy-copyright-and-disclaimer)
and [digital boundary metadata](https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs/edition-4-july-2026-june-2031/access-and-downloads/digital-boundary-files)
apply. The ABS published these boundaries on 14 October 2026. Postal Areas are
mesh-block approximations, not Australia Post delivery boundaries.

Source: [ABS POA_2026_AUS_SHP_GDA2020.zip](https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs/edition-4-july-2026-june-2031/access-and-downloads/digital-boundary-files/POA_2026_AUS_SHP_GDA2020.zip),
SHA-256 `9d436a58182a9f5e2e81d8fd41566f3e517c53ea225f9aaf4d0f6578ea007cbf`.
It has 2,642 spatial POAs and three null geometries (9494, 9797, ZZZZ). The
latter are omitted. Codes remain strings; a bucket is based only on the first two
digits and does not assign a state. All source vertices, polygon parts and holes
are retained, with no independent simplification. The original is GDA2020
decimal longitude/latitude; coordinates are treated as WGS84 for web display
without a datum transformation. This approximation is not survey accuracy.

The index contains per-bucket SHA-256 hashes of the compressed bytes. Its
reference version is the first 16 hex digits of SHA-256 over the compact JSON
`files` array followed by a newline. Gzip timestamp and filename are fixed.
The package exceeds the 12 MiB compressed target because retaining all source
geometry takes priority. Markers use only `index.json`; a two-digit prefix chunk is
loaded only when one of its postcodes is opened; each decoded chunk is bounded to 12 MiB by the host.
Ring-preserving simplification was evaluated but rejected because 34 combined
POA geometries became invalid. No simplified experimental geometry is shipped.

To rebuild, download the source ZIP outside the checkout, verify its hash and
use Python with maintainer-only dependencies `pyshp==2.3.1` and `shapely==2.1.2`:

```
python tools/build_map_reference.py /path/to/POA_2026_AUS_SHP_GDA2020.zip
python tools/test_map_reference.py
```

The builder verifies the pinned archive hash and validates source polygons,
uses an interior representative point, and emits deterministic gzip files.
The package and tests require no GIS runtime dependencies or network access.
