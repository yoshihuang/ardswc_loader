import os, sys, json, base64
os.environ["QT_QPA_PLATFORM"]="offscreen"; sys.path.insert(0,"/home/claude/ardswc")
from qgis.testing import start_app; app=start_app()
from qgis.core import QgsCoordinateTransform, QgsCoordinateReferenceSystem, QgsProject, QgsPointXY
from ardswc_loader import mvt
T=json.load(open("/home/claude/ardswc/test/tiles.json"))
tr=QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"),QgsCoordinateReferenceSystem("EPSG:3826"),QgsProject.instance())
t=T["landslide"]; rows,sk=mvt.decode_and_merge([(t["z"],t["x"],t["y"],base64.b64decode(t["b64"]))],"polygon",["gid"])
for g,p in rows:
    c=tr.transform(g.centroid().asPoint()); print(p["gid"], round(c.x()-float(p["x"]),1), round(c.y()-float(p["y"]),1), round(g.area()*1e10/ (1e10) ,8))
t=T["swc"]; rows,_=mvt.decode_and_merge([(t["z"],t["x"],t["y"],base64.b64decode(t["b64"]))],"point",["gid"])
g,p=rows[0]; print("swc", g.asWkt(5), p["epsg4326_x"], p["epsg4326_y"])
t=T["rural"]; rows,_=mvt.decode_and_merge([(t["z"],t["x"],t["y"],base64.b64decode(t["b64"]))],"polygon",["gid"])
for g,p in rows: print("rural", p["gid"], p["社區_1"], g.isGeosValid(), round(g.area(),8), g.wkbType())
