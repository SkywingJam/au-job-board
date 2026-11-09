#!/usr/bin/env python3
"""Build the offline ABS POA 2026 map package from the pinned public ZIP.

Maintainer only: pip install pyshp==2.3.1 shapely==2.1.2, then run
  python tools/build_map_reference.py /path/to/POA_2026_AUS_SHP_GDA2020.zip
The source is GDA2020 longitude/latitude. Coordinates are used as WGS84 for
web display without a datum transformation (see reference/README.md).
"""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import tempfile
import zipfile

SOURCE_SHA256 = '9d436a58182a9f5e2e81d8fd41566f3e517c53ea225f9aaf4d0f6578ea007cbf'
SOURCE_URL = ('https://www.abs.gov.au/statistics/standards/australian-statistical-'
              'geography-standard-asgs/edition-4-july-2026-june-2031/access-and-'
              'downloads/digital-boundary-files/POA_2026_AUS_SHP_GDA2020.zip')
OUT = Path(__file__).resolve().parents[1] / 'plugins/map/reference'
NAME = 'POA_2026_AUST_GDA2020'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded(obj):
    return (json.dumps(obj, ensure_ascii=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def compressed(data):
    result = io.BytesIO()
    with gzip.GzipFile(fileobj=result, mode='wb', filename='', mtime=0, compresslevel=9) as out:
        out.write(data)
    return result.getvalue()


def build(archive, outdir):
    import shapefile
    from shapely.geometry import shape

    data = archive.read_bytes()
    if sha(data) != SOURCE_SHA256:
        raise ValueError('Source archive SHA-256 does not match pinned ABS release')
    with tempfile.TemporaryDirectory(prefix='abs-poa-') as tmp:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for ext in ('shp', 'shx', 'dbf'):
                member = NAME + '.' + ext
                Path(tmp, member).write_bytes(z.read(member))
        reader = shapefile.Reader(str(Path(tmp, NAME + '.shp')), encoding='utf-8')
        buckets = {f'{n:02}': [] for n in range(100)}
        index = {}
        nonspatial = []
        for record in reader.iterShapeRecords():
            code = record.record['POA_CODE26']
            if not record.shape.shapeType:
                nonspatial.append(code)
                continue
            if not re.fullmatch(r'[0-9]{4}', code) or code in index:
                raise ValueError(f'invalid or duplicate spatial code: {code!r}')
            geometry = record.shape.__geo_interface__
            if geometry['type'] not in ('Polygon', 'MultiPolygon'):
                raise ValueError(f'unsupported geometry for {code}')
            polygon = shape(geometry)
            if polygon.is_empty or not polygon.is_valid:
                raise ValueError(f'invalid source geometry for {code}')
            point = polygon.representative_point()
            if not polygon.contains(point):
                raise ValueError(f'no interior point for {code}')
            filename = f'poa-{code[:2]}.geojson.gz'
            index[code] = {'bbox': list(polygon.bounds), 'point': [point.x, point.y], 'file': filename}
            buckets[code[:2]].append({'type': 'Feature', 'properties': {'postcode': code}, 'geometry': geometry})
        if len(index) != 2642 or len(nonspatial) != 3:
            raise ValueError(f'unexpected source counts: {len(index)}, {nonspatial}')
        files = []
        outputs = {}
        for prefix, features in buckets.items():
            if not features:
                continue
            features.sort(key=lambda f: f['properties']['postcode'])
            name = f'poa-{prefix}.geojson.gz'
            content = compressed(encoded({'type': 'FeatureCollection', 'features': features}))
            outputs[name] = content
            files.append({'path': name, 'sha256': sha(content)})
        version_hash = sha(encoded(files))[:16]
        manifest = {'schema_version': 1, 'reference_version': 'abs-poa-2026-' + version_hash,
                    'source_date': '2026-10-14',
                    'attribution': 'Australian Bureau of Statistics, ASGS Edition 4 Postal Areas 2026, © Commonwealth of Australia, CC BY 4.0. Postal Areas approximate postcodes.',
                    'files': files, 'postcodes': dict(sorted(index.items()))}
        outputs['index.json'] = encoded(manifest)
        outdir.mkdir(parents=True, exist_ok=True)
        for old in outdir.glob('poa-*.geojson.gz'):
            if old.name not in outputs:
                old.unlink()
        for name, content in outputs.items():
            (outdir / name).write_bytes(content)
        print(f'{len(index)} POAs, {len(files)} buckets, {sum(map(len, outputs.values()))} bytes, {manifest["reference_version"]}; non-spatial: {nonspatial}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--output', type=Path, default=OUT)
    args = parser.parse_args()
    build(args.archive, args.output)


if __name__ == '__main__':
    main()
