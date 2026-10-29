#!/usr/bin/env python3
"""Maintainer tool: rebuild the bundled metropolitan-area postcode reference.

Reads three official ABS files (ASGS Edition 3 allocation files for Mesh Block to
GCCSA and Mesh Block to POA, and the 2021 Census Mesh Block counts), weights each
postal area (POA) by usual-resident persons per Greater Capital City Statistical
Area (GCCSA) and writes ``jobs/data/au-postcode-metro.tsv`` and
``jobs/data/metro-reference.json``.

It is not part of the public test entry and not used at runtime: the application
only reads the two generated files. Standard library only; ``--download`` needs the
network, ``--source-dir`` does not.

    python tools/build_metro_reference.py --download /tmp/abs-source
    python tools/build_metro_reference.py --source-dir /tmp/abs-source --check
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "jobs" / "data"
ASGS = ("https://www.abs.gov.au/statistics/standards/"
        "australian-statistical-geography-standard-asgs/"
        "edition-3-july-2021-june-2026/access-and-downloads/allocation-files/")
SOURCES = {
    "MB_2021_AUST.xlsx": ASGS + "MB_2021_AUST.xlsx",
    "POA_2021_AUST.xlsx": ASGS + "POA_2021_AUST.xlsx",
    "Mesh Block Counts, 2021.xlsx": (
        "https://www.abs.gov.au/census/guide-census-data/mesh-block-counts/2021/"
        "Mesh%20Block%20Counts%2C%202021.xlsx"),
}
# ABS names of the capital-city statistical areas. The ACT is one GCCSA.
CAPITAL_AREAS = {
    "Adelaide": "Greater Adelaide", "Brisbane": "Greater Brisbane",
    "Canberra": "Australian Capital Territory", "Darwin": "Greater Darwin",
    "Hobart": "Greater Hobart", "Melbourne": "Greater Melbourne",
    "Perth": "Greater Perth", "Sydney": "Greater Sydney",
}
MIN_SHARE_PERMILLE = 900
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _column(ref):
    number = 0
    for char in re.match(r"[A-Z]+", ref).group():
        number = number * 26 + ord(char) - 64
    return number - 1


def sheet_rows(path, sheet):
    """Yield each row of one worksheet as a list of cell text (None if empty)."""
    with zipfile.ZipFile(path) as archive:
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            with archive.open("xl/sharedStrings.xml") as handle:
                for _, element in ET.iterparse(handle):
                    if element.tag == NS + "si":
                        strings.append("".join(t.text or "" for t in element.iter(NS + "t")))
                        element.clear()
        with archive.open(sheet) as handle:
            for _, element in ET.iterparse(handle):
                if element.tag != NS + "row":
                    continue
                cells = {}
                for cell in element.findall(NS + "c"):
                    value = cell.find(NS + "v")
                    if value is None:
                        text = "".join(t.text or "" for t in cell.iter(NS + "t")) or None
                    else:
                        text = strings[int(value.text)] if cell.get("t") == "s" else value.text
                    cells[_column(cell.get("r"))] = text
                yield [cells.get(i) for i in range(max(cells) + 1)] if cells else []
                element.clear()


def _sheet_names(path):
    with zipfile.ZipFile(path) as archive:
        return sorted((n for n in archive.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)),
                      key=lambda n: int(re.search(r"(\d+)", n).group()))


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def download(target):
    target.mkdir(parents=True, exist_ok=True)
    headers = {}
    for name, url in SOURCES.items():
        with urllib.request.urlopen(url, timeout=120) as response:
            (target / name).write_bytes(response.read())
            headers[name] = response.headers.get("Last-Modified")
    (target / "headers.json").write_text(json.dumps(headers, indent=2), encoding="utf-8")


def build(source):
    """Return (tsv_bytes, manifest) from the three source files."""
    regions, mesh = {}, {}
    for row in sheet_rows(source / "MB_2021_AUST.xlsx", "xl/worksheets/sheet1.xml"):
        if not row or row[0] == "MB_CODE_2021":
            continue
        mesh[row[0]] = (row[11], row[12], float(row[17] or 0))
        regions[row[11]] = row[12]
    postal = {}
    for row in sheet_rows(source / "POA_2021_AUST.xlsx", "xl/worksheets/sheet1.xml"):
        if row and row[0] != "MB_CODE_2021":
            postal[row[0]] = row[1]
    persons = {}
    counts = source / "Mesh Block Counts, 2021.xlsx"
    for sheet in _sheet_names(counts):
        header = None
        for row in sheet_rows(counts, sheet):
            if header is None:
                header = row if row and row[0] == "MB_CODE_2021" else None
                continue
            if row and row[0] and row[0].isdigit():
                persons[row[0]] = int(float(row[header.index("Person")] or 0))
    if len(persons) < 0.99 * len(mesh):
        raise SystemExit("Mesh block counts do not cover the allocation file.")

    people = collections.defaultdict(lambda: collections.defaultdict(float))
    area = collections.defaultdict(lambda: collections.defaultdict(float))
    for block, postcode in postal.items():
        if not re.fullmatch(r"[0-9]{4}", postcode) or block not in mesh:
            continue
        people[postcode][mesh[block][0]] += persons.get(block, 0)
        area[postcode][mesh[block][0]] += mesh[block][2]
    lines = ["postcode\tgccsa\tshare_permille\tbasis"]
    for postcode in sorted(people):
        weights, basis = people[postcode], "persons"
        if not sum(weights.values()):
            weights, basis = area[postcode], "area"
        total = sum(weights.values())
        if not total:
            continue
        code, best = max(sorted(weights.items()), key=lambda item: item[1])
        lines.append(f"{postcode}\t{code}\t{int(best / total * 1000)}\t{basis}")
    tsv = ("\n".join(lines) + "\n").encode("utf-8")

    by_name = {name: code for code, name in regions.items()}
    capitals = {city: {"gccsa": by_name[name], "name": name}
                for city, name in sorted(CAPITAL_AREAS.items())}
    headers_path = source / "headers.json"
    modified = json.loads(headers_path.read_text()) if headers_path.exists() else {}
    manifest = {
        "schema_version": 1,
        "reference_version": "abs-asgs3-poa-gccsa-" + hashlib.sha256(tsv).hexdigest()[:16],
        "publisher": "Australian Bureau of Statistics",
        "license": "CC BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "license_source": "https://www.abs.gov.au/website-privacy-copyright-and-disclaimer",
        "attribution": ("Based on Australian Bureau of Statistics data, ASGS Edition 3 (2021) "
                        "allocation files and 2021 Census mesh block counts, CC BY 4.0. "
                        "Reduced to postal area, dominant GCCSA and its person share; "
                        "derived by tools/build_metro_reference.py."),
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "resources": {
            name: {"url": SOURCES[name], "sha256": sha256(source / name),
                   "bytes": (source / name).stat().st_size,
                   "last_modified": modified.get(name)}
            for name in SOURCES},
        "method": ("Each 2021 mesh block belongs to one POA and one GCCSA. For every four-digit "
                   "POA the 2021 Census usual-resident persons of its mesh blocks are summed per "
                   "GCCSA (mesh-block area if the POA has no persons); the GCCSA with the largest "
                   "weight and its floor-rounded permille share are kept."),
        "min_share_permille": MIN_SHARE_PERMILLE,
        "tsv": {"file": "au-postcode-metro.tsv", "sha256": hashlib.sha256(tsv).hexdigest(),
                "rows": len(lines) - 1, "columns": lines[0].split("\t")},
        "capitals": capitals,
        "regions": {code: name for code, name in sorted(regions.items())},
        "limits": ("Postal areas approximate postcodes; PO-box and large-volume postcodes have no "
                   "POA and stay undetermined. ASGS Edition 4 (July 2026) boundaries and 2026 Census "
                   "counts are not used. A postcode below the minimum share is undetermined."),
    }
    return tsv, manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--download", type=Path, help="fetch the sources into this empty directory")
    parser.add_argument("--source-dir", type=Path, help="directory holding the three source files")
    parser.add_argument("--check", action="store_true",
                        help="compare the result with the bundled files without writing")
    args = parser.parse_args(argv)
    if bool(args.download) == bool(args.source_dir):
        parser.error("choose exactly one of --download and --source-dir")
    source = args.download or args.source_dir
    if args.download:
        download(source)
    tsv, manifest = build(source)
    bundled = json.loads((DATA / "metro-reference.json").read_text(encoding="utf-8")) \
        if (DATA / "metro-reference.json").exists() else None
    if args.check:
        same = bundled and bundled["tsv"]["sha256"] == manifest["tsv"]["sha256"] and bundled["resources"] == manifest["resources"]
        print("bundled reference matches" if same else "bundled reference differs")
        return 0 if same else 1
    (DATA / "au-postcode-metro.tsv").write_bytes(tsv)
    (DATA / "metro-reference.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(manifest["reference_version"], manifest["tsv"]["rows"], "rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
