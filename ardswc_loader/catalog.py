# -*- coding: utf-8 -*-
"""Catalogue of the open (開放資料) map services of the ARDSWC data platform.

Source list: https://data.ardswc.gov.tw/Data/OpenData/Api
(農業部農村發展及水土保持署資料平台 → API查詢).  Only services marked
「資料使用權限-開放資料」 that carry locations are included:

* MVT vector tiles  https://gis.ardswc.gov.tw/api/ardswc/vectortiles/shp/<name>/{z}/{y}/{x}.pbf
  (note the {y}/{x} order; one MVT layer called "default"; features carry gid)
* JSON point lists  (disaster photos, major debris-disaster reports)
* UAV ortho-image catalogue (footprints + XYZ image tiles)

Services marked 「依申請提供」 (on application only), the 20 m H.O.S.T / CS map
tiles (they answer 401 without a token) and non-spatial statistics are left out.
"""

from dataclasses import dataclass, field
from typing import Optional, Tuple

MVT_ROOT = "https://gis.ardswc.gov.tw/api/ardswc/vectortiles/shp/"
PLATFORM_URL = "https://data.ardswc.gov.tw/Data/OpenData/Api"
SOURCE_TEXT = "農業部農村發展及水土保持署 資料平台"
LICENSE_TEXT = "農業部農村發展及水土保持署 政府網站資料開放宣告（無償、非專屬、得再授權，使用時應註明出處）"
LICENSE_URL = "https://www.ardswc.gov.tw/Home/WebService/GWOIA?id=af86de76394f4df28ecbec7e8592b6fa"

SERVICE_BOUNDS = (117.5, 21.5, 123.5, 26.6)

POINT, LINE, POLYGON = "point", "line", "polygon"
MVT, POINTS, UAV = "mvt", "points", "uav"

GROUPS = ["土石流與大規模崩塌", "崩塌與集水區", "山坡地與水土保持", "農村與農路", "影像與災害紀錄"]

# Tiles per request: the highest zoom in DATA_ZOOMS whose tile count for the
# view stays under MAX_TILES is used (z14 ~ 0.6 m, z12 ~ 2.4 m precision).
DATA_ZOOMS = (14, 13, 12)
MAX_TILES = 48
FORCED_MAX_TILES = 400


@dataclass(frozen=True)
class Dataset:
    key: str
    title: str
    group: str
    kind: str                       # MVT / POINTS / UAV
    geometry: str                   # POINT / LINE / POLYGON
    description: str
    source: str = ""                # MVT layer name, or JSON URL
    id_fields: Tuple[str, ...] = ("gid",)
    lon_field: Optional[str] = None
    lat_field: Optional[str] = None
    rows_path: Tuple[str, ...] = ()  # JSON keys leading to the record list
    style: dict = field(default_factory=dict)

    @property
    def url(self) -> str:
        if self.kind == MVT:
            return MVT_ROOT + self.source + "/{z}/{y}/{x}.pbf"
        return self.source

    def tile_url(self, z, x, y) -> str:
        return "%s%s/%d/%d/%d.pbf" % (MVT_ROOT, self.source, z, y, x)

    @property
    def layer_name(self) -> str:
        return "水保署｜" + self.title


def _mvt(key, title, group, geometry, description, style, id_fields=("gid",)):
    return Dataset(key, title, group, MVT, geometry, description, source=key, id_fields=id_fields, style=style)


RISK_COLORS = {"高": "#d7191c", "中": "#fdae61", "低": "#a6d96a", "持續觀察": "#9e9e9e"}

DATASETS = [
    _mvt("debris_flow", "土石流潛勢溪流", GROUPS[0], LINE,
         "土石流潛勢溪流圖：編號、溪流名稱、風險等級、保全住戶、地質、流域等",
         {"kind": "risk_line", "field": "risk", "width": 0.9, "label": "debrisno"}, ("gid", "debrisno")),
    _mvt("debris_watershed", "土石流潛勢溪流集水區", GROUPS[0], POLYGON,
         "土石流潛勢溪流集水區圖：編號、集水區面積",
         {"kind": "fill", "color": "#6baed6", "opacity": 0.18, "outline": "#2171b5", "width": 0.3}, ("gid", "debrisno")),
    _mvt("debris_affect", "土石流潛勢溪流影響範圍", GROUPS[0], POLYGON,
         "土石流潛勢溪流影響範圍圖：風險等級、保全住戶、溢流點座標",
         {"kind": "risk_fill", "field": "risk", "opacity": 0.35}),
    _mvt("potential_landslide", "大規模崩塌潛勢區", GROUPS[0], POLYGON,
         "大規模崩塌潛勢區圖：編號、名稱、風險等級、保全住戶、地質",
         {"kind": "risk_fill", "field": "risk", "opacity": 0.4, "label": "lslno"}, ("gid", "lslno")),
    _mvt("potential_landslide_affect", "大規模崩塌潛勢區影響範圍", GROUPS[0], POLYGON,
         "大規模崩塌潛勢區影響範圍圖：風險等級、保全住戶",
         {"kind": "risk_fill", "field": "risk", "opacity": 0.25, "dashed": True}),

    _mvt("landslide", "全臺崩塌地", GROUPS[1], POLYGON,
         "全臺崩塌地圖層：面積、判釋日期、坡度、最低高程",
         {"kind": "fill", "color": "#a6611a", "opacity": 0.6, "outline": "#6b3d10", "width": 0.15}),
    _mvt("event_landslide_inventories", "事件型崩塌目錄", GROUPS[1], POLYGON,
         "事件型崩塌目錄：事件、面積(ha)、前後期影像日期",
         {"kind": "fill", "color": "#e66101", "opacity": 0.55, "outline": "#b34700", "width": 0.15}),
    _mvt("subbasin", "全臺子集水區", GROUPS[1], POLYGON,
         "全臺子集水區範圍圖：集水區編號、名稱、所屬流域",
         {"kind": "fill", "color": None, "outline": "#08519c", "width": 0.35, "label": "ws_name"}),

    _mvt("slopeland", "山坡地範圍（水保法、不含六都）", GROUPS[2], POLYGON,
         "山坡地範圍圖：公告文號、公告日期、縣市",
         {"kind": "fill", "color": "#b8e186", "opacity": 0.25, "outline": "#4d9221", "width": 0.2}),
    _mvt("soil_water_conservation", "特定水土保持區", GROUPS[2], POLYGON,
         "特定水土保持區範圍圖：計畫名稱、劃定文號、劃定面積",
         {"kind": "fill", "color": "#f4a6a6", "opacity": 0.35, "outline": "#c51b1b", "width": 0.6, "label": "planname"}),
    _mvt("swc_upstream_slope", "上游坡地水土保持範圍", GROUPS[2], POLYGON,
         "上游坡地水土保持範圍圖：縣市、鄉鎮",
         {"kind": "hatch", "color": "#7b3294", "width": 0.3}),
    _mvt("mountain_remediation_flood_prevention", "治山防洪範圍", GROUPS[2], POLYGON,
         "治山防洪範圍圖：縣市、鄉鎮",
         {"kind": "fill", "color": None, "outline": "#018571", "width": 0.5, "dashed": True}),
    _mvt("ecological_inspection", "生態檢核區域", GROUPS[2], POLYGON,
         "生態檢核區域圖",
         {"kind": "fill", "color": "#c2a5cf", "opacity": 0.3, "outline": "#762a83", "width": 0.3}),
    _mvt("swc_outdoor_eduction_area", "水土保持戶外教室及教學園區", GROUPS[2], POINT,
         "水土保持戶外教室及教學園區分布圖：名稱、管理單位、電話、開放時間",
         {"kind": "marker", "shape": "pentagon", "color": "#1a9850", "size": 3.2, "label": "name"}),

    _mvt("rural_regeneration", "農村再生計畫社區", GROUPS[3], POLYGON,
         "農村再生計畫社區分布圖：社區、村里、核定日期",
         {"kind": "fill", "color": "#ffe08a", "opacity": 0.45, "outline": "#b8860b", "width": 0.3, "label": "社區_1"}),
    _mvt("farm_road", "農路", GROUPS[3], LINE,
         "農路圖：農路編號、名稱、寬度、長度、起訖點",
         {"kind": "line", "color": "#8c510a", "width": 0.5}, ("newcode", "no", "農路編號")),

    Dataset("photo_disaster", "土砂災害事件影像", GROUPS[4], POINTS, POINT,
            "影像平台：土砂災害事件照片位置（滑鼠停留可預覽照片）",
            source="https://photo.ardswc.gov.tw/api/v1/rest/dataset/metadata/0",
            id_fields=("EventID",), lon_field="Lng", lat_field="Lat",
            style={"kind": "photo", "color": "#d7301f"}),
    Dataset("photo_landscape", "重要地景影像", GROUPS[4], POINTS, POINT,
            "影像平台：重要地景照片位置（滑鼠停留可預覽照片）",
            source="https://photo.ardswc.gov.tw/api/v1/rest/dataset/metadata/6",
            id_fields=("EventID",), lon_field="Lng", lat_field="Lat",
            style={"kind": "photo", "color": "#2b8cbe"}),
    Dataset("photo_media", "媒體報導土砂災點", GROUPS[4], POINTS, POINT,
            "影像平台：媒體報導土砂災點（滑鼠停留可預覽照片）",
            source="https://photo.ardswc.gov.tw/api/v1/rest/dataset/metadata/8",
            id_fields=("EventID",), lon_field="Lng", lat_field="Lat",
            style={"kind": "photo", "color": "#984ea3"}),
    Dataset("disaster_report", "重大土石災情報告", GROUPS[4], POINTS, POINT,
            "重大土石災情報告：事件、地點、災情描述、致災原因、處置與建議、報告 PDF",
            source="https://246.ardswc.gov.tw/api/DisasterExpress/GetDisasterExpress/1",
            id_fields=("ForeignID",), lon_field="wgs84_x", lat_field="wgs84_y",
            rows_path=("DisasterExpress", "Row"),
            style={"kind": "report", "color": "#b10026"}),
    Dataset("uav_ortho", "UAV 空拍正射影像範圍", GROUPS[4], UAV, POLYGON,
            "UAV 空拍正射影像目錄：每幅影像的範圍、日期、計畫；選取後可加入影像圖磚",
            source="https://gis.ardswc.gov.tw/api/swcb/tiles/ortho", id_fields=("Id",),
            style={"kind": "fill", "color": None, "outline": "#2166ac", "width": 0.5, "dashed": True,
                   "label": "Date"}),
]

BY_KEY = {d.key: d for d in DATASETS}

# Feeds that are downloaded once and filtered locally (cache lifetime, seconds).
CATALOG_CACHE_SECONDS = 15 * 60
REPORT_FILE_ROOT = "https://246.ardswc.gov.tw"


def limit_text(dataset):
    if dataset.kind == MVT:
        return "依畫面大小自動選用 z12–z14 圖磚（最多 %d 張）" % MAX_TILES
    return "整份目錄下載一次後，依畫面範圍篩選"
