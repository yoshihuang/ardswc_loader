import os, sys, json, base64
os.environ["QT_QPA_PLATFORM"]="offscreen"; sys.path.insert(0,"/home/claude/ardswc")
from qgis.testing import start_app; app=start_app()
from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QColor, QImage, QPainter
from qgis.core import *
from ardswc_loader import mvt
from ardswc_loader.catalog import BY_KEY
from ardswc_loader.layers import LayerManager
from ardswc_loader.fetcher import parse_points, parse_uav
Z=json.load(open("tiles_z12.json")); T=json.load(open("tiles.json"))
z,x,y=12,3431,1754
m=LayerManager()
for key in ["debris_watershed","debris_affect","debris_flow"]:
    ds=BY_KEY[key]; rows,_=mvt.decode_and_merge([(z,x,y,base64.b64decode(Z[key]))], ds.geometry, ds.id_fields); m.replace_features(ds, rows)
ds=BY_KEY["rural_regeneration"]; rows,_=mvt.decode_and_merge([(z,x,y,base64.b64decode(T["rural"]["b64"]))], ds.geometry, ds.id_fields); m.replace_features(ds, rows)
n=2**z; import math
lon0=x/n*360-180; lon1=(x+1)/n*360-180; lat0=math.degrees(math.atan(math.sinh(math.pi*(1-2*(y+1)/n)))); lat1=math.degrees(math.atan(math.sinh(math.pi*(1-2*y/n))))
bbox=(lon0,lat0,lon1,lat1)
photos=[{"EventID":str(i),"Lat":lat0+(lat1-lat0)*f, "Lng":lon0+(lon1-lon0)*g, "PhotoAngle":a, "Description":"照片%d"%i,"FileName":"x"} for i,(f,g,a) in enumerate([(0.3,0.3,0),(0.6,0.5,90),(0.8,0.75,225),(0.45,0.8,135)])]
m.replace_features(BY_KEY["photo_disaster"], parse_points(BY_KEY["photo_disaster"], json.dumps(photos).encode(), bbox)[0])
rep={"DisasterExpress":{"Row":[{"ForeignID":"1","Disaster":"測試災情","wgs84_x":str(lon0+(lon1-lon0)*0.55),"wgs84_y":str(lat0+(lat1-lat0)*0.3)}]}}
m.replace_features(BY_KEY["disaster_report"], parse_points(BY_KEY["disaster_report"], json.dumps(rep).encode(), bbox)[0])
uav=[{"Id":"U1","AccessURL":"https://x/20261020_1_2_14_001_RSImage_UAV_Ortho/","WestBoundLongitude":lon0+(lon1-lon0)*0.1,"EastBoundLongitude":lon0+(lon1-lon0)*0.35,"SouthBoundLatitude":lat0+(lat1-lat0)*0.55,"NorthBoundLatitude":lat0+(lat1-lat0)*0.85,"Date":"2026-10-20","Title":"UAV"}]
m.replace_features(BY_KEY["uav_ortho"], parse_uav(BY_KEY["uav_ortho"], json.dumps(uav).encode(), bbox)[0])
order=[m.layer(k) for k in ["photo_disaster","disaster_report","uav_ortho","debris_flow","debris_affect","rural_regeneration","debris_watershed"]]
crs=QgsCoordinateReferenceSystem("EPSG:3826"); ms=QgsMapSettings(); ms.setLayers(order); ms.setDestinationCrs(crs)
tr=QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"),crs,QgsProject.instance())
ms.setExtent(tr.transformBoundingBox(QgsRectangle(*bbox))); ms.setOutputSize(QSize(1200,1000)); ms.setOutputDpi(110); ms.setBackgroundColor(QColor("#f7f7f2"))
img=QImage(ms.outputSize(), QImage.Format.Format_ARGB32); img.fill(QColor("#f7f7f2")); p=QPainter(img)
j=QgsMapRendererCustomPainterJob(ms,p); j.start(); j.waitForFinished(); p.end(); img.save("map.png")
root=QgsLayerTree(); [root.addLayer(l) for l in order]
model=QgsLayerTreeModel(root); leg=QgsLegendRenderer(model, QgsLegendSettings()); s=leg.minimumSize()
li=QImage(int(s.width()*4)+20,int(s.height()*4)+20,QImage.Format.Format_ARGB32); li.fill(QColor("white")); lp=QPainter(li); lp.scale(4,4)
ctx=QgsRenderContext.fromQPainter(lp); leg.drawLegend(ctx); lp.end(); li.save("legend.png")
print({l.name():l.featureCount() for l in order})
