"""Offline integration test for ardswc_loader against a local fake ARDSWC server."""
import base64
import dataclasses
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_deps"))
import mapbox_vector_tile  # noqa: E402  (test-only dependency, encodes synthetic tiles)
from qgis.testing import start_app  # noqa: E402

APP = start_app()

from qgis.PyQt.QtCore import QEventLoop, QTimer  # noqa: E402
from qgis.core import (Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject, QgsRectangle,  # noqa: E402
                       QgsVectorLayer, QgsWkbTypes)

HERE = os.path.dirname(os.path.abspath(__file__))
TILES = json.load(open(os.path.join(HERE, "tiles.json")))
REQUESTS = []

PHOTOS = [
    {"No": 1, "EventID": "638937244658707281", "PhotoType": "災害事件", "Lat": 24.99, "Lng": 121.61, "County": "新北市",
     "Town": "石碇區", "Vill": "X", "PhotoDate": "2025-09-17T00:00:00", "PhotoAngle": 225, "Source": "測試",
     "Description": "測試照片", "Note": "", "DisasterYear": 2024, "DisasterName": "山陀兒颱風",
     "FileName": "https://photo.ardswc.gov.tw/api/Media/638937244658707281"},
    {"No": 2, "EventID": "2", "PhotoType": "災害事件", "Lat": 22.62511, "Lng": 121.005028, "PhotoAngle": 0,
     "Description": "範圍外", "FileName": "x"},
    {"No": 3, "EventID": "3", "PhotoType": "災害事件", "Lat": None, "Lng": None, "Description": "無座標"},
]
REPORTS = {"DisasterExpress": {"Row": [
    {"ForeignID": "95-0001", "Year": 95, "DisasterName": "0609豪雨", "County": "新北市", "Town": "石碇區",
     "FileUrl": "/AllFiles/PDF/test.pdf", "Disaster": "測試災情", "DisasterType": "崩塌災害",
     "DisasterDescription": "說明", "wgs84_x": "121.60", "wgs84_y": "24.98", "eventid": "e1"},
    {"ForeignID": "97-0013", "Year": 95, "wgs84_x": "120.895306", "wgs84_y": "23.776939"}]}}
UAV = [
    {"Id": "03-002VI", "CenterLatitude": 24.98, "CenterLongitude": 121.6,
     "AccessURL": "https://storage.geodac.tw/Tile/v2/RSImage/UAV/Ortho/20261020_121785650_024492618_14_001_RSImage_UAV_Ortho/",
     "WestBoundLongitude": 121.59, "EastBoundLongitude": 121.61, "SouthBoundLatitude": 24.97, "NorthBoundLatitude": 24.99,
     "Title": "20261020_UAV正射影像_測試", "ProjectNo": "115LL017", "ProjectName": "測試計畫", "CloudCover": None,
     "Date": "2026-10-20", "Class": "UAV正射影像"},
    {"Id": "03-002G4", "CenterLatitude": 23.1, "CenterLongitude": 120.7,
     "AccessURL": "https://storage.geodac.tw/Tile/v2/RSImage/UAV/Ortho/20240423_1_2_18_000_RSImage_UAV_Ortho/",
     "WestBoundLongitude": 120.69, "EastBoundLongitude": 120.71, "SouthBoundLatitude": 23.09, "NorthBoundLatitude": 23.11,
     "Title": "範圍外", "Date": "2024-04-23", "Class": "UAV正射影像"},
]


def synthetic_tile(layer, z, x, y):
    """Every tile row carries one stream (gid = row) crossing the whole tile plus
    the 64-unit buffer; polygons: one feature gid=1 covering the tile+buffer;
    points: one point per tile."""
    if layer in ("debris_flow", "farm_road"):
        props = {"gid": str(y), "debrisno": "DF%03d" % (y % 1000), "risk": ["高", "中", "低"][y % 3], "name": "溪%d" % y}
        if layer == "farm_road":
            props = {"newcode": "R%d" % y, "name": "農路%d" % y}
        feats = [{"geometry": "LINESTRING(-64 2048, 4160 2048)", "properties": props}]
    elif layer == "swc_outdoor_eduction_area":
        feats = [{"geometry": "POINT(2048 2048)", "properties": {"gid": "%d-%d" % (x, y), "name": "教室"}}]
    else:
        feats = [{"geometry": "POLYGON((-64 -64, 4160 -64, 4160 4160, -64 4160, -64 -64))",
                  "properties": {"gid": "1", "risk": "中", "ws_name": "測試", "planname": "計畫"}}]
    return mapbox_vector_tile.encode([{"name": "default", "features": feats}], default_options={"y_coord_down": True})


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        path = urlparse(self.path).path
        REQUESTS.append(path)
        if "/slow_road/" in path:
            time.sleep(0.3)
        if "/vectortiles/shp/" in path:
            parts = path.split("/")
            layer, z, y, x = parts[-4], int(parts[-3]), int(parts[-2]), int(parts[-1].split(".")[0])
            if layer == "broken":
                self.send_response(500)
                self.end_headers()
                return
            if layer == "empty":
                body = b""
            else:
                body = synthetic_tile(layer, z, x, y)
            ctype = "text/html; charset=utf-8"
        elif path.endswith("/photos"):
            body, ctype = json.dumps(PHOTOS, ensure_ascii=False).encode(), "application/json"
        elif path.endswith("/reports"):
            body, ctype = json.dumps(REPORTS, ensure_ascii=False).encode(), "application/json"
        elif path.endswith("/uav"):
            body, ctype = json.dumps(UAV, ensure_ascii=False).encode(), "application/json"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
ROOT = "http://127.0.0.1:%d" % server.server_port

import ardswc_loader.catalog as catalog  # noqa: E402
catalog.MVT_ROOT = ROOT + "/api/ardswc/vectortiles/shp/"
for key, suffix in [("photo_disaster", "/photos"), ("photo_landscape", "/photos"), ("photo_media", "/photos"),
                    ("disaster_report", "/reports"), ("uav_ortho", "/uav")]:
    catalog.BY_KEY[key] = dataclasses.replace(catalog.BY_KEY[key], source=ROOT + suffix)
catalog.REPORT_FILE_ROOT = "https://246.ardswc.gov.tw"

from ardswc_loader import mvt  # noqa: E402
from ardswc_loader.catalog import BY_KEY, DATASETS, MAX_TILES  # noqa: E402
from ardswc_loader.exporter import export_layers  # noqa: E402
from ardswc_loader.fetcher import Fetcher, TooLarge, choose_zoom, plan_requests, uav_min_zoom  # noqa: E402
from ardswc_loader.layers import GROUP_NAME, LayerManager  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + ("  " + str(detail) if detail != "" else ""))


def wait(cond, timeout=20):
    loop = QEventLoop()
    end = time.time() + timeout
    while not cond() and time.time() < end:
        QTimer.singleShot(30, loop.quit)
        loop.exec()
    return cond()


# ---------------------------------------------------------------- real tiles
tr = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"), QgsCoordinateReferenceSystem("EPSG:3826"),
                            QgsProject.instance())
t = TILES["landslide"]
rows, _ = mvt.decode_and_merge([(t["z"], t["x"], t["y"], base64.b64decode(t["b64"]))], "polygon", ("gid",))
offsets = []
for geometry, props in rows:
    c = tr.transform(geometry.centroid().asPoint())
    offsets.append(max(abs(c.x() - float(props["x"])), abs(c.y() - float(props["y"]))))
check("real landslide tile: 6 polygons within z10 precision of their TWD97 x/y", len(rows) == 6 and max(offsets) < 12,
      [round(o, 1) for o in offsets])
t = TILES["rural"]
rows, _ = mvt.decode_and_merge([(t["z"], t["x"], t["y"], base64.b64decode(t["b64"]))], "polygon", ("gid",))
check("real rural tile: 2 valid multipolygons with CJK attributes",
      len(rows) == 2 and all(g.isGeosValid() and QgsWkbTypes.isMultiType(g.wkbType()) for g, _ in rows)
      and rows[0][1]["社區_1"] == "烏塗社區", [p.get("社區_1") for _, p in rows])
t = TILES["swc"]
rows, _ = mvt.decode_and_merge([(t["z"], t["x"], t["y"], base64.b64decode(t["b64"]))], "point", ("gid",))
check("real point tile decodes (121.4943, 25.1505)", len(rows) == 1 and
      abs(rows[0][0].asMultiPoint()[0].x() - 121.49428) < 1e-4, rows[0][0].asWkt(5))

# ---------------------------------------------------------------- tile maths / planning
check("tiles_for_bbox order {x,y} and count", mvt.tile_count((121.5, 24.9, 121.6, 25.0), 12) ==
      len(mvt.tiles_for_bbox((121.5, 24.9, 121.6, 25.0), 12)))
check("small view -> z14", choose_zoom((121.60, 24.97, 121.62, 24.99)) == 14)
check("medium view -> z12 or z13", choose_zoom((121.4, 24.9, 121.7, 25.1)) in (12, 13))
try:
    choose_zoom((120.0, 22.0, 122.0, 25.3))
    check("island view rejected", False)
except TooLarge as exc:
    check("island view rejected with message", "請放大地圖" in str(exc), exc)
url = plan_requests(BY_KEY["debris_flow"], (121.60, 24.97, 121.61, 24.98))[0][0]
check("tile URL uses {z}/{y}/{x}", url.endswith("/debris_flow/14/%d/%d.pbf" % (
    mvt.lonlat_to_tile(121.60, 24.98, 14)[1], mvt.lonlat_to_tile(121.60, 24.98, 14)[0])), url)
check("UAV min zoom from folder name", uav_min_zoom(UAV[0]["AccessURL"]) == 14 and uav_min_zoom(UAV[1]["AccessURL"]) == 18)

# ---------------------------------------------------------------- fetcher end-to-end
fetcher = Fetcher()
done, fail = {}, {}
fetcher.finished.connect(lambda k, r, s, sec: done.__setitem__(k, (r, s)))
fetcher.failed.connect(lambda k, m: fail.__setitem__(k, m))
bbox = (121.58, 24.96, 121.64, 25.0)
reqs = plan_requests(BY_KEY["debris_flow"], bbox)
fetcher.fetch(BY_KEY["debris_flow"], bbox, reqs)
check("debris_flow job finishes", wait(lambda: "debris_flow" in done, 30), fail)
rows = done.get("debris_flow", ([], 0))[0]
rows_tiles = sorted({m[2] for _u, m in reqs})
check("fragments merged: one stream per tile row (%d rows, %d tiles)" % (len(rows_tiles), len(reqs)),
      len(rows) == len(rows_tiles) and all(g.constGet().numGeometries() == 1 for g, _ in rows),
      [g.constGet().numGeometries() for g, _ in rows])
cols = sorted({m[1] for _u, m in reqs})
lengths = sorted(round(g.length(), 5) for g, _ in rows)
expected = (mvt.tiles_for_bbox(bbox, reqs[0][1][0]) and None)
check("merged stream spans all tile columns", all(abs(length - lengths[0]) < 1e-6 for length in lengths) and
      lengths[0] > (cols[-1] - cols[0]) * 360.0 / 2 ** reqs[0][1][0], lengths[:2])
REQUESTS.clear()
fetcher.fetch(BY_KEY["debris_flow"], bbox, reqs)
done.pop("debris_flow", None)
check("second fetch served from tile cache (no HTTP)", wait(lambda: "debris_flow" in done, 10) and not REQUESTS, REQUESTS[:3])
reqs = plan_requests(BY_KEY["landslide"], bbox)
fetcher.fetch(BY_KEY["landslide"], bbox, reqs)
check("polygon pieces union into one feature", wait(lambda: "landslide" in done, 30) and
      len(done["landslide"][0]) == 1 and done["landslide"][0][0][0].isGeosValid(), len(done.get("landslide", [[]])[0]))
reqs = plan_requests(BY_KEY["swc_outdoor_eduction_area"], bbox)
fetcher.fetch(BY_KEY["swc_outdoor_eduction_area"], bbox, reqs)
check("points: one per tile", wait(lambda: "swc_outdoor_eduction_area" in done, 30) and
      len(done["swc_outdoor_eduction_area"][0]) == len(reqs))
fetcher.fetch(BY_KEY["photo_disaster"], bbox, plan_requests(BY_KEY["photo_disaster"], bbox))
check("photos filtered to view, bad coords skipped", wait(lambda: "photo_disaster" in done) and
      len(done["photo_disaster"][0]) == 1 and done["photo_disaster"][1] == 1, done.get("photo_disaster", [0, 0])[1])
fetcher.fetch(BY_KEY["disaster_report"], bbox, plan_requests(BY_KEY["disaster_report"], bbox))
check("disaster reports: nested Row path + PDF URL", wait(lambda: "disaster_report" in done) and
      done["disaster_report"][0][0][1]["ReportUrl"] == "https://246.ardswc.gov.tw/AllFiles/PDF/test.pdf")
fetcher.fetch(BY_KEY["uav_ortho"], bbox, plan_requests(BY_KEY["uav_ortho"], bbox))
check("UAV footprints filtered + tile template", wait(lambda: "uav_ortho" in done) and
      len(done["uav_ortho"][0]) == 1 and done["uav_ortho"][0][0][1]["TileUrl"].endswith("{z}/{y}/{x}.jpg"))
broken = dataclasses.replace(BY_KEY["landslide"], key="broken", source="broken")
fetcher.fetch(broken, bbox, plan_requests(broken, bbox))
check("HTTP 500 tile -> failed signal", wait(lambda: "broken" in fail), fail.get("broken"))
empty = dataclasses.replace(BY_KEY["landslide"], key="empty", source="empty")
fetcher.fetch(empty, bbox, plan_requests(empty, bbox))
check("empty tiles -> 0 features, no error", wait(lambda: "empty" in done) and done["empty"][0] == [])
REQUESTS.clear()
big = (121.4, 24.85, 121.75, 25.15)
slow = dataclasses.replace(BY_KEY["farm_road"], source="slow_road")
reqs = plan_requests(slow, big)
fetcher.fetch(slow, big, reqs)
QTimer.singleShot(200, lambda: fetcher.cancel("farm_road"))
wait(lambda: False, 2)
check("cancel stops a running tile job", "farm_road" not in done and len(REQUESTS) < len(reqs), (len(REQUESTS), len(reqs)))

# ---------------------------------------------------------------- layers + styles + export
project = QgsProject.instance()
manager = LayerManager(project)
for ds in DATASETS:
    layer = manager.ensure_layer(ds)
    check("layer valid %s" % ds.key, layer.isValid())
manager.replace_features(BY_KEY["debris_flow"], done["debris_flow"][0], bbox)
lyr = manager.layer("debris_flow")
cats = [c.label() for c in lyr.renderer().categories()]
check("risk legend ordered 高/中/低", cats == ["風險：高", "風險：中", "風險：低"], cats)
check("debris_flow labelled with debrisno along the line",
      lyr.labelsEnabled() and lyr.labeling().settings().fieldName == "debrisno"
      and lyr.labeling().settings().placement == Qgis.LabelPlacement.Line)
check("fields created from tile attributes", {"gid", "debrisno", "risk"} <= set(lyr.fields().names()), lyr.fields().names())
manager.replace_features(BY_KEY["photo_disaster"], done["photo_disaster"][0], bbox)
pl = manager.layer("photo_disaster")
check("photo map tip shows image", "<img" in pl.mapTipTemplate() and pl.fields().field("PhotoAngle").isNumeric())
manager.replace_features(BY_KEY["swc_outdoor_eduction_area"], done["swc_outdoor_eduction_area"][0], bbox)
check("point layer stays single-part", manager.layer("swc_outdoor_eduction_area").wkbType() == Qgis.WkbType.Point)
manager.replace_features(BY_KEY["uav_ortho"], done["uav_ortho"][0], bbox)
manager.replace_features(BY_KEY["landslide"], done["landslide"][0], bbox)
manager.replace_features(BY_KEY["disaster_report"], done["disaster_report"][0], bbox)
check("group on top", project.layerTreeRoot().children()[0].name() == GROUP_NAME)
for fmt in ["gpkg_single", "shp", "geojson"]:
    out = tempfile.mkdtemp(prefix="ard_%s_" % fmt)
    written, errors = export_layers(manager.layers(), out, fmt, "EPSG:3826", project)
    total = 0
    for path in [p for p in written if not p.endswith(".txt")]:
        if fmt == "gpkg_single":
            from osgeo import ogr
            ds_ = ogr.Open(path)
            total += sum(ds_.GetLayer(i).GetFeatureCount() for i in range(ds_.GetLayerCount())
                         if ds_.GetLayer(i).GetName() != "layer_styles")
        else:
            total += QgsVectorLayer(path, "x", "ogr").featureCount()
    expected = sum(layer.featureCount() for layer in manager.layers())
    check("export %s (%d features)" % (fmt, expected), not errors and total == expected, (errors, total))
    note = [p for p in written if p.endswith(".txt")][0]
    check("export %s source note names ARDSWC" % fmt, "水土保持署" in open(note, encoding="utf-8").read())

# ---------------------------------------------------------------- plugin (mocked iface)
from qgis.testing.mocked import get_iface  # noqa: E402
iface = get_iface()
canvas = iface.mapCanvas()
canvas.resize(800, 600)
project.removeAllMapLayers()
crs3857 = QgsCoordinateReferenceSystem("EPSG:3857")
project.setCrs(crs3857)
canvas.setDestinationCrs(crs3857)
to3857 = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"), crs3857, project)
from ardswc_loader import classFactory  # noqa: E402
plugin = classFactory(iface)
plugin.initGui()
canvas.setExtent(to3857.transformBoundingBox(QgsRectangle(121.58, 24.96, 121.64, 25.0)))
plugin.dock.set_checked(["debris_flow", "uav_ortho", "photo_disaster"], emit=True)
check("checking datasets loads them", wait(lambda: all(
    plugin.layers.layer(k) is not None and plugin.layers.layer(k).featureCount() > 0
    for k in ["debris_flow", "uav_ortho", "photo_disaster"]), 30),
    {k: plugin.dock._items[k].text(1) for k in ["debris_flow", "uav_ortho", "photo_disaster"]})
canvas.setExtent(to3857.transformBoundingBox(QgsRectangle(119.5, 22.0, 122.5, 25.5)))
wait(lambda: plugin.dock._items["debris_flow"].text(1) == "請放大地圖", 5)
check("zoomed out: tiles refused, catalogs still filtered", plugin.dock._items["debris_flow"].text(1) == "請放大地圖"
      and wait(lambda: plugin.layers.layer("uav_ortho").featureCount() == 2, 10),
      (plugin.dock._items["debris_flow"].text(1), plugin.layers.layer("uav_ortho").featureCount()))
uav_layer = plugin.layers.layer("uav_ortho")
uav_layer.selectAll()
plugin.add_selected_uav()
rasters = [lyr for lyr in project.mapLayers().values() if lyr.type() == lyr.type().RasterLayer] if hasattr(
    uav_layer.type(), "RasterLayer") else [lyr for lyr in project.mapLayers().values() if lyr.providerType() == "wms"]
check("UAV images added as XYZ layers with min zoom", len(rasters) == 2 and all(
    "zmin=" in lyr.source() and "%7Bz%7D/%7By%7D/%7Bx%7D.jpg" in lyr.source() or "{z}/{y}/{x}.jpg" in lyr.source()
    for lyr in rasters), [lyr.source()[:160] for lyr in rasters])
plugin.add_selected_uav()
check("adding again does not duplicate", len([lyr for lyr in project.mapLayers().values() if lyr.providerType() == "wms"]) == 2)
plugin.dock.set_checked(["uav_ortho"], emit=True)
check("unchecking removes layers", plugin.layers.layer("debris_flow") is None)
plugin.unload()
check("unload clean", plugin.dock is None)

server.shutdown()
failed = [r for r in results if not r[1]]
print("\n%d checks, %d failed" % (len(results), len(failed)))
sys.exit(1 if failed else 0)
