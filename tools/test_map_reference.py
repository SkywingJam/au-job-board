#!/usr/bin/env python3
"""Offline package integrity and geometry checks; no GIS packages required."""
import gzip
import hashlib
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1] / 'plugins/map/reference'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def in_ring(point, ring):
    x, y = point
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def contains(point, geometry):
    polygons = [geometry['coordinates']] if geometry['type'] == 'Polygon' else geometry['coordinates']
    return any(in_ring(point, p[0]) and not any(in_ring(point, h) for h in p[1:]) for p in polygons)


def validate(index, blobs):
    if index['schema_version'] != 1 or not re.fullmatch(r'abs-poa-2026-[0-9a-f]{16}', index['reference_version']):
        raise ValueError('invalid index version')
    version_bytes = (json.dumps(index['files'], separators=(',', ':')) + '\n').encode()
    if index['reference_version'] != 'abs-poa-2026-' + digest(version_bytes)[:16]:
        raise ValueError('reference version mismatch')
    seen = set()
    paths = set()
    for item in index['files']:
        path = item['path']
        if not re.fullmatch(r'poa-[0-9]{2}\.geojson\.gz', path) or path in paths:
            raise ValueError('invalid or duplicate filename')
        paths.add(path)
        blob = blobs[path]
        if digest(blob) != item['sha256']:
            raise ValueError('hash mismatch')
        collection = json.loads(gzip.decompress(blob))
        if collection['type'] != 'FeatureCollection':
            raise ValueError('invalid collection')
        for feature in collection['features']:
            code = feature['properties']['postcode']
            if not isinstance(code, str) or not re.fullmatch(r'[0-9]{4}', code) or code in seen:
                raise ValueError('invalid or duplicate postcode')
            seen.add(code)
            entry = index['postcodes'][code]
            if entry['file'] != path or not path.startswith('poa-' + code[:2]):
                raise ValueError('incorrect bucket')
            geometry = feature['geometry']
            if feature['type'] != 'Feature' or geometry['type'] not in ('Polygon', 'MultiPolygon'):
                raise ValueError('invalid feature geometry')
            polygons = [geometry['coordinates']] if geometry['type'] == 'Polygon' else geometry['coordinates']
            bounds = [180, 90, -180, -90]
            if not polygons:
                raise ValueError('empty geometry')
            for polygon in polygons:
                if not polygon:
                    raise ValueError('empty polygon')
                for ring in polygon:
                    if len(ring) < 4 or ring[0] != ring[-1]:
                        raise ValueError('invalid ring')
                    for x, y in ring:
                        if not (-180 <= x <= 180 and -90 <= y <= 90):
                            raise ValueError('invalid coordinates')
                        bounds = [min(bounds[0], x), min(bounds[1], y), max(bounds[2], x), max(bounds[3], y)]
            if bounds != entry['bbox'] or not contains(entry['point'], geometry):
                raise ValueError('bbox or representative point mismatch')
    if seen != set(index['postcodes']):
        raise ValueError('index and features differ')
    return seen


def fixture():
    geometry = {'type': 'MultiPolygon', 'coordinates': [
        [[[0,0],[10,0],[10,10],[0,10],[0,0]], [[2,2],[2,4],[4,4],[4,2],[2,2]]],
        [[[20,0],[22,0],[22,2],[20,2],[20,0]]]]}
    feature = {'type':'Feature','properties':{'postcode':'0800'},'geometry':geometry}
    blob = gzip.compress(json.dumps({'type':'FeatureCollection','features':[feature]}).encode(), mtime=0)
    index = {'schema_version':1, 'reference_version':'abs-poa-2026-0123456789abcdef',
             'files':[{'path':'poa-08.geojson.gz','sha256':digest(blob)}],
             'postcodes':{'0800':{'file':'poa-08.geojson.gz','bbox':[0,0,22,10],'point':[21,1]}}}
    version_bytes = (json.dumps(index['files'], separators=(',', ':')) + '\n').encode()
    index['reference_version'] = 'abs-poa-2026-' + digest(version_bytes)[:16]
    return index, {'poa-08.geojson.gz':blob}


class ReferenceTests(unittest.TestCase):
    def test_synthetic_multipart_hole_and_leading_zero(self):
        index, blobs = fixture()
        self.assertEqual(validate(index, blobs), {'0800'})
        index['postcodes']['0800']['point'] = [3, 3]
        with self.assertRaisesRegex(ValueError, 'representative point'):
            validate(index, blobs)

    def test_corruption_and_wrong_index(self):
        index, blobs = fixture()
        blobs['poa-08.geojson.gz'] += b'bad'
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            validate(index, blobs)
        index, blobs = fixture()
        index['postcodes']['0800']['bbox'] = [0,0,10,10]
        with self.assertRaisesRegex(ValueError, 'bbox'):
            validate(index, blobs)
        index, blobs = fixture()
        index['files'][0]['path'] = '../outside'
        index['reference_version'] = 'abs-poa-2026-' + digest((json.dumps(index['files'], separators=(',', ':')) + '\n').encode())[:16]
        with self.assertRaisesRegex(ValueError, 'filename'):
            validate(index, blobs)

    def test_rehashed_broken_geometry_and_duplicate(self):
        index, blobs = fixture()
        path = 'poa-08.geojson.gz'
        collection = json.loads(gzip.decompress(blobs[path]))
        collection['features'][0]['geometry']['coordinates'][0][0].pop()
        blobs[path] = gzip.compress(json.dumps(collection).encode(), mtime=0)
        index['files'][0]['sha256'] = digest(blobs[path])
        index['reference_version'] = 'abs-poa-2026-' + digest((json.dumps(index['files'], separators=(',', ':')) + '\n').encode())[:16]
        with self.assertRaisesRegex(ValueError, 'invalid ring'):
            validate(index, blobs)
        index, blobs = fixture()
        collection = json.loads(gzip.decompress(blobs[path]))
        collection['features'].append(collection['features'][0])
        blobs[path] = gzip.compress(json.dumps(collection).encode(), mtime=0)
        index['files'][0]['sha256'] = digest(blobs[path])
        index['reference_version'] = 'abs-poa-2026-' + digest((json.dumps(index['files'], separators=(',', ':')) + '\n').encode())[:16]
        with self.assertRaisesRegex(ValueError, 'duplicate postcode'):
            validate(index, blobs)

    def test_public_package(self):
        index = json.loads((ROOT / 'index.json').read_bytes())
        blobs = {f['path']: (ROOT / f['path']).read_bytes() for f in index['files']}
        codes = validate(index, blobs)
        self.assertEqual(len(codes), 2642)
        self.assertTrue({'0800', '0872', '2620'} <= codes)
        self.assertEqual(index['source_date'], '2026-10-14')
        self.assertTrue(all(blob[4:8] == b'\0\0\0\0' for blob in blobs.values()))


if __name__ == '__main__':
    unittest.main()
