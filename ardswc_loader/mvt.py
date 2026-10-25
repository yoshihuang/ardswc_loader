# -*- coding: utf-8 -*-
"""Mapbox Vector Tile (MVT v2) decoding and tile-fragment merging.

Pure Python (no extra packages) so it behaves the same on QGIS 3.34 - 4.x and
can run inside a QgsTask.  Geometries are returned in EPSG:4326.

The ARDSWC tile service cuts every feature at tile borders (plus a small
buffer), so a stream or a polygon visible in the map view is split over many
tiles.  ``merge_fragments`` rebuilds one feature per id (``gid``) by
unioning the pieces.
"""

import math
import struct

from qgis.core import Qgis, QgsGeometry, QgsPointXY

POINT, LINE, POLYGON = "point", "line", "polygon"
_TYPES = {1: POINT, 2: LINE, 3: POLYGON}


# ---------------------------------------------------------------- tile maths
def lonlat_to_tile(lon, lat, zoom):
    lat = max(min(lat, 85.0511), -85.0511)
    n = 2 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    rad = math.radians(lat)
    y = int((1.0 - math.log(math.tan(rad) + 1.0 / math.cos(rad)) / math.pi) / 2.0 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def tiles_for_bbox(bbox, zoom):
    """All (x, y) tiles at ``zoom`` covering a lon/lat bbox."""
    xmin, ymin, xmax, ymax = bbox
    x0, y0 = lonlat_to_tile(xmin, ymax, zoom)   # north-west
    x1, y1 = lonlat_to_tile(xmax, ymin, zoom)   # south-east
    return [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]


def tile_count(bbox, zoom):
    xmin, ymin, xmax, ymax = bbox
    x0, y0 = lonlat_to_tile(xmin, ymax, zoom)
    x1, y1 = lonlat_to_tile(xmax, ymin, zoom)
    return (x1 - x0 + 1) * (y1 - y0 + 1)


# ---------------------------------------------------------------- protobuf
class _Reader:
    __slots__ = ("data", "pos", "end")

    def __init__(self, data, start=0, end=None):
        self.data = data
        self.pos = start
        self.end = len(data) if end is None else end

    def more(self):
        return self.pos < self.end

    def varint(self):
        result, shift = 0, 0
        data = self.data
        while True:
            byte = data[self.pos]
            self.pos += 1
            result |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return result
            shift += 7

    def key(self):
        value = self.varint()
        return value >> 3, value & 7

    def sub(self):
        length = self.varint()
        start = self.pos
        self.pos += length
        return _Reader(self.data, start, start + length)

    def string(self):
        sub = self.sub()
        return bytes(self.data[sub.pos:sub.end]).decode("utf-8", "replace")

    def packed(self):
        sub = self.sub()
        values = []
        while sub.more():
            values.append(sub.varint())
        return values

    def skip(self, wire):
        if wire == 0:
            self.varint()
        elif wire == 1:
            self.pos += 8
        elif wire == 2:
            self.pos += self.varint()
        elif wire == 5:
            self.pos += 4
        else:
            raise ValueError("unsupported protobuf wire type %d" % wire)


def _zigzag(value):
    return (value >> 1) ^ -(value & 1)


def _value(reader):
    value = None
    while reader.more():
        field, wire = reader.key()
        if field == 1:
            value = reader.string()
        elif field == 2:
            value = struct.unpack("<f", bytes(reader.data[reader.pos:reader.pos + 4]))[0]
            reader.pos += 4
        elif field == 3:
            value = struct.unpack("<d", bytes(reader.data[reader.pos:reader.pos + 8]))[0]
            reader.pos += 8
        elif field in (4, 5):
            value = reader.varint()
            if field == 4 and value >= 1 << 63:
                value -= 1 << 64
        elif field == 6:
            value = _zigzag(reader.varint())
        elif field == 7:
            value = bool(reader.varint())
        else:
            reader.skip(wire)
    return value


def _rings(commands):
    """Decode MVT geometry commands into lists of (x, y) tile coordinates."""
    parts, current = [], []
    x = y = 0
    i, n = 0, len(commands)
    while i < n:
        command = commands[i] & 0x7
        count = commands[i] >> 3
        i += 1
        if command == 1:            # MoveTo
            for _ in range(count):
                if current:
                    parts.append(current)
                x += _zigzag(commands[i])
                y += _zigzag(commands[i + 1])
                i += 2
                current = [(x, y)]
        elif command == 2:          # LineTo
            for _ in range(count):
                x += _zigzag(commands[i])
                y += _zigzag(commands[i + 1])
                i += 2
                current.append((x, y))
        elif command == 7:          # ClosePath
            if current:
                current.append(current[0])
        else:
            raise ValueError("bad MVT geometry command %d" % command)
    if current:
        parts.append(current)
    return parts


def _signed_area(ring):
    area = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        area += x1 * y2 - x2 * y1
    return area / 2.0


def decode_tile(data, zoom, tile_x, tile_y):
    """Yield (layer_name, kind, parts, properties) with parts in lon/lat.

    kind 'point': parts = [(lon, lat), ...]
    kind 'line': parts = [[(lon, lat), ...], ...]
    kind 'polygon': parts = [[exterior, hole, ...], ...]  (rings of (lon, lat))
    """
    data = memoryview(bytes(data))
    tile = _Reader(data)
    while tile.more():
        field, wire = tile.key()
        if field != 3:
            tile.skip(wire)
            continue
        layer = tile.sub()
        name, keys, values, raw_features, extent = None, [], [], [], 4096
        while layer.more():
            lf, lw = layer.key()
            if lf == 1:
                name = layer.string()
            elif lf == 2:
                raw_features.append(layer.sub())
            elif lf == 3:
                keys.append(layer.string())
            elif lf == 4:
                values.append(_value(layer.sub()))
            elif lf == 5:
                extent = layer.varint()
            else:
                layer.skip(lw)
        scale = 2.0 ** zoom * extent

        def to_lonlat(px, py):
            gx = (tile_x * extent + px) / scale
            gy = (tile_y * extent + py) / scale
            lon = gx * 360.0 - 180.0
            lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * gy))))
            return lon, lat

        for feature in raw_features:
            geom_type, tags, commands, fid = 0, [], [], None
            while feature.more():
                ff, fw = feature.key()
                if ff == 1:
                    fid = feature.varint()
                elif ff == 2:
                    tags = feature.packed()
                elif ff == 3:
                    geom_type = feature.varint()
                elif ff == 4:
                    commands = feature.packed()
                else:
                    feature.skip(fw)
            kind = _TYPES.get(geom_type)
            if kind is None or not commands:
                continue
            props = {}
            for k in range(0, len(tags) - 1, 2):
                if tags[k] < len(keys) and tags[k + 1] < len(values):
                    props[keys[tags[k]]] = values[tags[k + 1]]
            if fid is not None and "_mvt_id" not in props:
                props["_mvt_id"] = fid
            rings = _rings(commands)
            if kind == POINT:
                parts = [to_lonlat(px, py) for ring in rings for px, py in ring]
            elif kind == LINE:
                parts = [[to_lonlat(px, py) for px, py in ring] for ring in rings if len(ring) >= 2]
            else:
                parts, polygon = [], None
                for ring in rings:
                    if len(ring) < 4:
                        continue
                    lonlat = [to_lonlat(px, py) for px, py in ring]
                    # MVT v2: exterior rings have positive area (y axis down).
                    if _signed_area(ring) > 0 or polygon is None:
                        polygon = [lonlat]
                        parts.append(polygon)
                    else:
                        polygon.append(lonlat)
            if parts:
                yield name, kind, parts, props


# ---------------------------------------------------------------- geometry
def _qgs_geometry(kind, parts):
    if kind == POINT:
        points = [QgsPointXY(x, y) for x, y in parts]
        return QgsGeometry.fromMultiPointXY(points)
    if kind == LINE:
        return QgsGeometry.fromMultiPolylineXY([[QgsPointXY(x, y) for x, y in line] for line in parts])
    return QgsGeometry.fromMultiPolygonXY(
        [[[QgsPointXY(x, y) for x, y in ring] for ring in polygon] for polygon in parts])


def _as_kind(geometry, kind):
    """Keep only the dataset's geometry kind, as a multi-part geometry."""
    if geometry is None or geometry.isEmpty():
        return None
    target = {POINT: Qgis.GeometryType.Point, LINE: Qgis.GeometryType.Line, POLYGON: Qgis.GeometryType.Polygon}[kind]
    if geometry.type() != target:
        converted = geometry.convertToType(target, True)
        if converted is None or converted.isNull() or converted.isEmpty():
            return None
        geometry = converted
    if not geometry.isMultipart():
        geometry.convertToMultiType()
    return geometry


def merge_fragments(fragments, kind, id_fields):
    """Combine per-tile pieces of the same feature.

    ``fragments``: iterable of (parts, properties) of one geometry kind.
    Returns [(QgsGeometry, properties)].
    """
    groups, order = {}, []
    for parts, props in fragments:
        key = None
        for name in id_fields:
            if props.get(name) not in (None, ""):
                key = (name, str(props[name]))
                break
        if key is None:   # no id: identical attributes = same feature
            key = ("attrs", tuple(sorted((k, str(v)) for k, v in props.items() if k != "_mvt_id")))
        if key not in groups:
            groups[key] = (props, [])
            order.append(key)
        groups[key][1].append(_qgs_geometry(kind, parts))

    rows = []
    for key in order:
        props, pieces = groups[key]
        if kind == POINT:
            seen, points = set(), []
            for piece in pieces:
                for point in piece.asMultiPoint():
                    marker = (round(point.x(), 7), round(point.y(), 7))
                    if marker not in seen:
                        seen.add(marker)
                        points.append(point)
            geometry = QgsGeometry.fromMultiPointXY(points)
        elif len(pieces) == 1:
            geometry = pieces[0]
        else:
            geometry = QgsGeometry.unaryUnion(pieces)
            if kind == LINE and geometry is not None and not geometry.isEmpty():
                merged = geometry.mergeLines()
                if merged is not None and not merged.isEmpty():
                    geometry = merged
        if kind == POLYGON and geometry is not None and not geometry.isGeosValid():
            geometry = geometry.makeValid()
        geometry = _as_kind(geometry, kind)
        if geometry is not None:
            clean = {k: v for k, v in props.items() if k != "_mvt_id"}
            rows.append((geometry, clean))
    return rows


def decode_and_merge(payloads, kind, id_fields, layer_name=None):
    """payloads: [(zoom, x, y, bytes)].  Returns (rows, skipped_fragments)."""
    fragments, skipped = [], 0
    for zoom, x, y, data in payloads:
        if not data:
            continue
        for name, feature_kind, parts, props in decode_tile(data, zoom, x, y):
            if layer_name and name != layer_name:
                continue
            if feature_kind != kind:
                skipped += 1
                continue
            fragments.append((parts, props))
    return merge_fragments(fragments, kind, id_fields), skipped
