"""Host-owned validation of public sponsorship references."""
import csv
import io
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path


def _stale(path, days):
    return not path.exists() or (datetime.now().timestamp() - path.stat().st_mtime) >= days * 86400


def _check_sanctions(body):
    value = json.loads(body)
    data = value.get('d') if isinstance(value, dict) else None
    rows = data.get('data') if isinstance(data, dict) else None
    if (not isinstance(data, dict) or data.get('success') is not True or not isinstance(rows, list) or not rows
            or any(not isinstance(row, dict) or not isinstance(row.get('sponsorname'), str)
                   or not row['sponsorname'].strip()
                   or (row.get('tradingname') is not None and not isinstance(row['tradingname'], str)) for row in rows)):
        raise ValueError('invalid sanctions reference')


def _check_asx(body):
    lines = body.decode('utf-8-sig').splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith('Company name')), None)
    if start is None or not any(row.get('Company name') for row in csv.DictReader(lines[start:])):
        raise ValueError('invalid ASX reference')


def _check_ato(body):
    if not _ato_workbook(io.BytesIO(body)):
        raise ValueError('empty ATO reference')


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


