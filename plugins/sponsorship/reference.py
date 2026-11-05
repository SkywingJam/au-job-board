"""Employer reference matching adapted from PR #27 by Patrickchenxi."""
from __future__ import annotations
import io
import json, re, urllib.request, csv, zipfile, html
from datetime import datetime
from pathlib import Path
import xml.etree.ElementTree as ET

SANCTIONS_URL = "https://www.abf.gov.au/_layouts/15/api/Data.aspx/GetSanctionsData"
ASX_URL = "https://www.asx.com.au/asx/research/ASXListedCompanies.csv"
ATO_PACKAGE_URL = "https://data.gov.au/data/api/3/action/package_show?id=corporate-transparency"
DEFAULT_REFRESH_DAYS = 30
RESULTS_NAME = "sponsorship-checks.json"

# strict: drop only legal suffixes and the country; the rest of the name must match exactly
_LEGAL = {"pty", "ltd", "limited", "proprietary", "the", "australia", "australian", "aust", "au",
          "holdings", "holding", "services", "corporation", "corp", "company", "inc", "incorporated",
          "plc", "llc", "gmbh", "pte", "and"}
# loose: also drop regions and generic words, to find "possibly the same employer"
_DROP = _LEGAL | {"anz", "apac", "asia", "pacific", "nz", "group", "service", "co", "international",
                  "operations", "trading", "trustee", "trust"}
# The listed name may add one of these to the advertised name ("Example" -> "Example Systems").
_SPONSOR_TAIL = {"systems", "technologies", "technology", "wholesale", "clothing", "supermarkets",
                 "private", "enterprises", "australasia", "victoria", "vic", "lawyers", "motors",
                 "travel", "solutions", "global"}
# The advertised name may add one of these to the listed name ("Example Brands" -> "Example").
_COMPANY_TAIL = {"brands", "new", "zealand"}
# OCR noise in the accredited list: page numbers, headers, watermark fragments
_NOISE = re.compile(r"^\d{1,3}$|freedom of information|released by|department of|home affairs|"
                    r"australian government|list of accredited|^fa \d", re.I)
# Recruiter / anonymous advertisers: the real employer cannot be checked
_AGENCY = re.compile(r"\b(recruit\w*|talent|careers|staffing|personnel|resourcing|search|"
                     r"executive search|placements?|private advertiser)\b", re.I)


def _norm_tokens(name: str, drop: set[str] = _DROP) -> list[str]:
    name = (name or "").split("|")[0]
    name = re.sub(r"\(.*?\)", " ", name).lower().replace("&", " and ")
    tokens = re.findall(r"[a-z0-9#]+", name)
    return [t for t in tokens if t not in drop]


def _key(name: str) -> str:
    return " ".join(_norm_tokens(name))


def _strict(name: str) -> str:
    return " ".join(_norm_tokens(name, {"pty", "ltd", "limited", "proprietary", "inc", "incorporated", "plc", "llc", "gmbh", "pte"}))


class NameIndex:
    """Matcher over a list of employer names: ``match(company) -> (exact|fuzzy|None, listed name)``."""

    def __init__(self, names: list[str]):
        self.strict = {_strict(n): n for n in names}
        self.loose: dict[str, str] = {}
        self.by_first: dict[str, list[tuple[list[str], str]]] = {}
        for original in names:
            tokens = _norm_tokens(original)
            if not tokens:          # nothing left after removing suffixes
                continue
            self.loose.setdefault(" ".join(tokens), original)
            self.by_first.setdefault(tokens[0], []).append((tokens, original))

    def match(self, company: str) -> tuple[str | None, str | None]:
        tokens = _norm_tokens(company)
        if not tokens:
            return None, None
        if (hit := self.strict.get(_strict(company))):
            return "exact", hit
        if (hit := self.loose.get(" ".join(tokens))):
            return "fuzzy", hit
        for listed, original in self.by_first.get(tokens[0], []):
            if len(listed) > len(tokens) and listed[:len(tokens)] == tokens and set(listed[len(tokens):]) <= _SPONSOR_TAIL:
                return "fuzzy", original
            if len(tokens) > len(listed) and tokens[:len(listed)] == listed and set(tokens[len(listed):]) <= _COMPANY_TAIL:
                return "fuzzy", original
        return None, None


class Sanctions:
    """strict legal name (2+ words) = confirmed; same/similar name or trading name = possible."""

    def __init__(self, rows: list[dict]):
        self.confirmed: dict[str, dict] = {}
        self.possible: dict[str, dict] = {}
        for r in rows:
            entry = {"name": (r.get("sponsorname") or "").strip(), "trading": (r.get("tradingname") or "").strip(),
                     "abn": r.get("abn"), "sanction": r.get("sanctionimposed"),
                     "date": r.get("sanctionimposeddatetext"), "end": r.get("sanctionenddate"),
                     "state": r.get("state")}
            legal = _strict(entry["name"])
            if len(legal.split()) >= 2:
                self.confirmed.setdefault(legal, entry)
            for name in (entry["name"], entry["trading"]):
                if (key := _key(name)):
                    self.possible.setdefault(key, entry)

    def match(self, company: str) -> tuple[str | None, dict | None]:
        if (hit := self.confirmed.get(_strict(company))):
            return "confirmed", hit
        if (key := _key(company)) and (hit := self.possible.get(key)):
            return "possible", hit
        return None, None


# ---- reference data ----

def load_accredited(data_dir: Path) -> list[str]:
    names: dict[str, str] = {}
    for path in sorted(data_dir.glob("accredited-*.txt")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if len(re.findall(r"[A-Za-z]", line)) < 3 or _NOISE.search(line):
                continue
            names.setdefault(_strict(line), line)
    return [n for n in names.values() if _key(n)]


def load_sanctions(data_dir: Path) -> Sanctions:
    path = data_dir / "sanctioned.json"
    rows = _sanction_rows(path.read_bytes()) if path.exists() else []
    return Sanctions(rows)


def _stale(path: Path, days: int) -> bool:
    return not path.exists() or (datetime.now().timestamp() - path.stat().st_mtime) >= days * 86400


_USER_AGENT = {"User-Agent": "au-job-board (sponsorship reference data)"}


def _download(request, target: Path, check=None) -> None:
    if isinstance(request, str):
        request = urllib.request.Request(request, headers=_USER_AGENT)
    with urllib.request.urlopen(request, timeout=60) as response:
        body = response.read(64 * 1024 * 1024 + 1)
    if len(body) > 64 * 1024 * 1024:
        raise ValueError('reference data too large')
    if check is not None:
        check(body)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_bytes(body)
    temporary.replace(target)


def _sanction_rows(body):
    value = json.loads(body)
    data = value.get('d') if isinstance(value, dict) else None
    rows = data.get('data') if isinstance(data, dict) else None
    if (not isinstance(data, dict) or data.get('success') is not True
            or not isinstance(rows, list) or not rows
            or any(not isinstance(row, dict) or not isinstance(row.get('sponsorname'), str)
                   or not row['sponsorname'].strip()
                   or (row.get('tradingname') is not None and not isinstance(row['tradingname'], str))
                   for row in rows)):
        raise ValueError('invalid sanctions reference')
    return rows


def _check_sanctions(body: bytes) -> None:
    _sanction_rows(body)


def _check_asx(body: bytes) -> None:
    lines = body.decode('utf-8-sig').splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith('Company name')), None)
    if start is None or not any(row.get('Company name') for row in csv.DictReader(lines[start:])):
        raise ValueError('invalid ASX reference')


def _check_ato(body: bytes) -> None:
    _ato_workbook(io.BytesIO(body))


def _latest_ato_url() -> tuple[str, str] | None:
    with urllib.request.urlopen(ATO_PACKAGE_URL, timeout=60) as response:
        package = json.loads(response.read(2 * 1024 * 1024 + 1))["result"]
    reports = []
    for resource in package.get("resources") or []:
        year = re.match(r"(\d{4}-\d{2}) Report of Entity Tax Information", resource.get("name") or "")
        if year and str(resource.get("url") or "").lower().endswith(".xlsx"):
            reports.append((year.group(1), resource["url"]))
    return max(reports) if reports else None



def load_asx(data_dir: Path) -> list[str]:
    path = data_dir / "asx-listed.csv"
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("Company name")), None)
    if start is None:
        return []
    return [row["Company name"] for row in csv.DictReader(lines[start:]) if row.get("Company name")]


def _xlsx_rows(path: Path, sheet: int):
    """Preserve sparse Excel column positions and decode shared/inline strings."""
    with zipfile.ZipFile(path) as archive:
        def read_xml(name):
            if archive.getinfo(name).file_size > 64 * 1024 * 1024:
                raise ValueError('worksheet too large')
            return ET.fromstring(archive.read(name))
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            for item in read_xml("xl/sharedStrings.xml").iter():
                if item.tag.rsplit('}', 1)[-1] == 'si':
                    strings.append(''.join(n.text or '' for n in item.iter()
                                           if n.tag.rsplit('}', 1)[-1] == 't'))
        tree = read_xml(f"xl/worksheets/sheet{sheet}.xml")
    for row in tree.iter():
        if row.tag.rsplit('}', 1)[-1] != 'row':
            continue
        cells = []
        for cell in row:
            if cell.tag.rsplit('}', 1)[-1] != 'c':
                continue
            letters = re.match(r'([A-Z]+)', cell.get('r', ''))
            index = len(cells)
            if letters:
                index = 0
                for letter in letters.group(1):
                    index = index * 26 + ord(letter) - ord('A') + 1
                index -= 1
            if index > 1000:
                raise ValueError('invalid column')
            while len(cells) <= index:
                cells.append('')
            values = [n.text or '' for n in cell.iter()
                      if n.tag.rsplit('}', 1)[-1] in ('v', 't')]
            value = ''.join(values)
            if cell.get('t') == 's':
                value = strings[int(value)]
            cells[index] = value
        yield cells


def _ato_workbook(path):
    income = {}
    with zipfile.ZipFile(path) as archive:
        sheets = sorted(int(m.group(1)) for n in archive.namelist()
                        if (m := re.fullmatch(r'xl/worksheets/sheet(\d+)\.xml', n)))
    for sheet in sheets:
        header = None
        for cells in _xlsx_rows(path, sheet):
            if header is None:
                if "Name" in cells and any("Total income" in c for c in cells):
                    header = cells
                continue
            record = dict(zip(header, cells))
            name = record.get("Name")
            total = next((v for k, v in record.items() if "Total income" in k), None)
            try:
                if not isinstance(name, str) or not name.strip():
                    continue
                income[name] = max(income.get(name, 0.0), float(total))
            except (TypeError, ValueError):
                continue
        if header is not None:
            return income
    raise ValueError('ATO data sheet not found')


def load_ato(data_dir: Path) -> tuple[dict[str, float], str | None]:
    files = sorted(data_dir.glob("ato-tax-*.xlsx"))
    if not files:
        return {}, None
    income = _ato_workbook(files[-1])
    year = re.search(r"\d{4}-\d{2}", files[-1].name)
    return income, (year.group(0) if year else None)


class Companies:
    """ASX and ATO lookups using the same conservative matcher as the accredited list."""

    def __init__(self, data_dir: Path, matcher_cls):
        self.asx = matcher_cls(load_asx(data_dir))
        self.income, self.ato_year = load_ato(data_dir)
        self.ato = matcher_cls(list(self.income))

    def lookup(self, company: str) -> dict:
        asx_level, asx_name = self.asx.match(company)
        ato_level, ato_name = self.ato.match(company)
        return {"asx": asx_level, "asx_name": asx_name, "ato": ato_level, "ato_name": ato_name,
                "ato_income": self.income.get(ato_name) if ato_name else None, "ato_year": self.ato_year}


