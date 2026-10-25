# -*- coding: utf-8 -*-
"""Memory (scratch) layers holding the features of the current map view."""

import zlib

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QFont
from qgis.core import (
    Qgis,
    QgsCategorizedSymbolRenderer,
    QgsFeature,
    QgsFillSymbol,
    QgsLayerMetadata,
    QgsLinePatternFillSymbolLayer,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsPalLayerSettings,
    QgsProject,
    QgsProperty,
    QgsRendererCategory,
    QgsSimpleFillSymbolLayer,
    QgsSingleSymbolRenderer,
    QgsSymbolLayer,
    QgsTextFormat,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
)

from .catalog import LICENSE_TEXT, LICENSE_URL, LINE, PLATFORM_URL, POINT, RISK_COLORS, SOURCE_TEXT
from .compat import make_field

GROUP_NAME = "水保署 ARDSWC"
KEY_PROPERTY = "ardswc_loader/key"
BBOX_PROPERTY = "ardswc_loader/bbox"

_URI_TYPES = {POINT: "Point", LINE: "MultiLineString"}
_PLACEMENT = getattr(Qgis, "LabelPlacement", QgsPalLayerSettings)
ANGLE_PROPERTY = getattr(QgsSymbolLayer.Property, "Angle", None)
if ANGLE_PROPERTY is None:   # QGIS 3.34 member name
    ANGLE_PROPERTY = getattr(QgsSymbolLayer.Property, "Property" + "Angle")
NUMERIC_FIELDS = {"Lat", "Lng", "PhotoAngle", "DisasterYear", "No", "Year", "MinZoom", "AlertValue",
                  "CenterLatitude", "CenterLongitude", "WestBoundLongitude", "EastBoundLongitude",
                  "SouthBoundLatitude", "NorthBoundLatitude", "DisasterDead", "DisasterMiss", "DisasterHurt",
                  "DisasterHouseCrash"}


def value_color(value):
    digest = zlib.crc32(str(value).encode("utf-8"))
    return QColor.fromHsl(digest % 360, 110 + (digest >> 9) % 90, 130 + (digest >> 17) % 60).name()


def _fill(color, opacity=1.0, outline="#555555", width=0.2, dashed=False):
    layer = QgsSimpleFillSymbolLayer()
    if color is None:
        layer.setBrushStyle(Qt.BrushStyle.NoBrush)
    else:
        fill = QColor(color)
        fill.setAlphaF(opacity)
        layer.setColor(fill)
    layer.setStrokeColor(QColor(outline))
    layer.setStrokeWidth(width)
    if dashed:
        layer.setStrokeStyle(Qt.PenStyle.DashLine)
    return QgsFillSymbol([layer])


def _hatch(color, width):
    hatch = QgsLinePatternFillSymbolLayer()
    hatch.setLineAngle(45)
    hatch.setDistance(2.0)
    hatch.setColor(QColor(color))
    sub = hatch.subSymbol()
    if sub is not None:
        sub.setColor(QColor(color))
        sub.setWidth(0.2)
    outline = QgsSimpleFillSymbolLayer()
    outline.setBrushStyle(Qt.BrushStyle.NoBrush)
    outline.setStrokeColor(QColor(color))
    outline.setStrokeWidth(width)
    return QgsFillSymbol([hatch, outline])


def _labeling(expression, size=8.0, color="#1d3557", geometry=None):
    settings = QgsPalLayerSettings()
    settings.fieldName = expression
    settings.isExpression = "(" in expression or '"' in expression
    text_format = QgsTextFormat()
    font = QFont()
    font.setBold(True)
    text_format.setFont(font)
    text_format.setSize(size)
    text_format.setColor(QColor(color))
    buffer = text_format.buffer()
    buffer.setEnabled(True)
    buffer.setSize(0.8)
    buffer.setColor(QColor("white"))
    text_format.setBuffer(buffer)
    settings.setFormat(text_format)
    if geometry == LINE:
        # Labels follow the line; short streams may overrun the ends a little.
        settings.placement = _PLACEMENT.Line
        settings.lineSettings().setOverrunDistance(4.0)
    elif geometry != POINT:
        settings.placement = _PLACEMENT.Horizontal
    return QgsVectorLayerSimpleLabeling(settings)


def _risk_symbol(kind, value, style):
    color = RISK_COLORS.get(str(value).strip(), value_color(value))
    if kind == "risk_line":
        return QgsLineSymbol.createSimple({"line_color": color, "line_width": str(style.get("width", 0.8)),
                                           "capstyle": "round"})
    return _fill(color, style.get("opacity", 0.35), QColor(color).darker(140).name(), 0.3, style.get("dashed", False))


def photo_symbol(color):
    """Arrow pointing in the photo direction (PhotoAngle, clockwise from north)."""
    symbol = QgsMarkerSymbol.createSimple({"name": "circle", "color": color, "size": "2.6",
                                           "outline_color": "white", "outline_width": "0.3"})
    arrow_symbol = QgsMarkerSymbol.createSimple({"name": "filled_arrowhead", "color": color, "size": "3.0",
                                                 "outline_color": "white", "outline_width": "0.2",
                                                 "offset": "2.5,0", "offset_unit": "MM"})
    arrow = arrow_symbol.symbolLayer(0).clone()   # keep arrow_symbol alive while cloning
    arrow.setDataDefinedProperty(ANGLE_PROPERTY, QgsProperty.fromExpression('coalesce("PhotoAngle", 0) - 90'))
    symbol.appendSymbolLayer(arrow)
    return symbol


def apply_style(layer, dataset):
    style = dataset.style
    kind = style.get("kind")
    if kind in ("risk_line", "risk_fill"):
        layer.setRenderer(QgsCategorizedSymbolRenderer(style["field"], []))
    elif kind == "fill":
        layer.setRenderer(QgsSingleSymbolRenderer(_fill(style.get("color"), style.get("opacity", 1.0),
                                                        style.get("outline", "#555555"), style.get("width", 0.2),
                                                        style.get("dashed", False))))
    elif kind == "hatch":
        layer.setRenderer(QgsSingleSymbolRenderer(_hatch(style["color"], style.get("width", 0.3))))
    elif kind == "line":
        layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol.createSimple(
            {"line_color": style["color"], "line_width": str(style.get("width", 0.5))})))
    elif kind == "marker":
        layer.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol.createSimple({
            "name": style.get("shape", "circle"), "color": style["color"], "size": str(style.get("size", 3)),
            "outline_color": "white", "outline_width": "0.3"})))
    elif kind == "photo":
        layer.setRenderer(QgsSingleSymbolRenderer(photo_symbol(style["color"])))
        layer.setMapTipTemplate(
            '<b>[% "Description" %]</b><br>[% "County" %][% "Town" %][% "Vill" %]　[% left("PhotoDate", 10) %]<br>'
            '[% "DisasterName" %]　來源：[% "Source" %]<br>'
            '<img src="[% "FileName" %]" width="320">')
    elif kind == "report":
        layer.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol.createSimple({
            "name": "star", "color": style["color"], "size": "4.2", "outline_color": "white", "outline_width": "0.3"})))
        layer.setMapTipTemplate(
            '<b>[% "Disaster" %]</b>（[% "DisasterType" %]）<br>[% "County" %][% "Town" %][% "Vill" %]　'
            '[% "Location" %]<br>[% "DisasterDescription" %]<br><a href="[% "ReportUrl" %]">災情報告 PDF</a>')
    if style.get("label"):
        layer.setLabeling(_labeling(style["label"], geometry=dataset.geometry))
        layer.setLabelsEnabled(True)


def update_categories(layer, dataset):
    renderer = layer.renderer()
    if not isinstance(renderer, QgsCategorizedSymbolRenderer):
        return
    index = layer.fields().indexOf(renderer.classAttribute())
    if index < 0:
        return
    known = {category.value() for category in renderer.categories()}
    order = list(RISK_COLORS)
    for value in layer.uniqueValues(index):
        if value in known or value is None or str(value).strip() in ("", "NULL"):
            continue
        symbol = _risk_symbol(dataset.style["kind"], value, dataset.style)
        renderer.addCategory(QgsRendererCategory(value, symbol, "風險：%s" % value))
    ordered = sorted(renderer.categories(),   # categories() returns copies
                     key=lambda c: (order.index(str(c.value())) if str(c.value()) in order else 99, str(c.value())))
    renderer.deleteAllCategories()
    for category in ordered:
        renderer.addCategory(category)


def _metadata(dataset):
    metadata = QgsLayerMetadata()
    metadata.setTitle(dataset.layer_name)
    abstract = "%s\n資料來源：%s\n%s\nAPI 清單：%s" % (dataset.description, SOURCE_TEXT, dataset.url, PLATFORM_URL)
    metadata.setAbstract(abstract)
    metadata.setRights(["資料來源：" + SOURCE_TEXT])
    metadata.setLicenses([LICENSE_TEXT + " " + LICENSE_URL])
    metadata.setKeywords({"ARDSWC": ["水保署", dataset.key]})
    return metadata


class LayerManager:
    """Creates, refreshes and removes one memory layer per dataset."""

    def __init__(self, project=None):
        self.project = project or QgsProject.instance()
        self._ids = {}

    def layer(self, key):
        layer_id = self._ids.get(key)
        layer = self.project.mapLayer(layer_id) if layer_id else None
        if layer is None:
            self._ids.pop(key, None)
        return layer

    def layers(self):
        return [layer for layer in (self.layer(key) for key in list(self._ids)) if layer is not None]

    def _group(self):
        root = self.project.layerTreeRoot()
        group = root.findGroup(GROUP_NAME)
        if group is None:
            group = root.insertGroup(0, GROUP_NAME)
        return group

    def ensure_layer(self, dataset):
        layer = self.layer(dataset.key)
        if layer is not None:
            return layer
        geometry_type = _URI_TYPES.get(dataset.geometry, "MultiPolygon")
        layer = QgsVectorLayer("%s?crs=EPSG:4326" % geometry_type, dataset.layer_name, "memory")
        layer.setCustomProperty(KEY_PROPERTY, dataset.key)
        layer.setMetadata(_metadata(dataset))
        apply_style(layer, dataset)
        self.project.addMapLayer(layer, False)
        self._group().insertLayer(0, layer)
        self._ids[dataset.key] = layer.id()
        return layer

    def remove(self, key):
        layer = self.layer(key)
        self._ids.pop(key, None)
        if layer is not None:
            self.project.removeMapLayer(layer.id())
        group = self.project.layerTreeRoot().findGroup(GROUP_NAME)
        if group is not None and not group.children():
            group.parent().removeChildNode(group)

    def replace_features(self, dataset, rows, bbox=None):
        """Swap the layer content for ``rows``; fields are created from the data.
        Returns the feature count, or None while the layer is being edited."""
        layer = self.ensure_layer(dataset)
        if layer.isEditable():
            return None
        provider = layer.dataProvider()
        names = set(layer.fields().names())
        extra = []
        for _geometry, attributes in rows:
            for name in attributes:
                if name not in names:
                    names.add(name)
                    extra.append(make_field(name, "double" if name in NUMERIC_FIELDS else "string"))
        if extra:
            provider.addAttributes(extra)
            layer.updateFields()

        fields = layer.fields()
        numeric = [fields.at(i).isNumeric() for i in range(fields.count())]
        features = []
        for geometry, attributes in rows:
            if dataset.geometry == POINT and geometry.isMultipart():
                parts = [p for p in geometry.asMultiPoint()]
                geometries = [type(geometry).fromPointXY(p) for p in parts]
            else:
                geometries = [geometry]
            values = [None] * fields.count()
            for name, value in attributes.items():
                index = fields.indexOf(name)
                if index < 0 or value is None:
                    continue
                if numeric[index]:
                    try:
                        values[index] = float(value)
                    except (TypeError, ValueError):
                        values[index] = None
                else:
                    values[index] = str(value)
            for part in geometries:
                feature = QgsFeature(fields)
                feature.setGeometry(part)
                feature.setAttributes(values)
                features.append(feature)

        provider.truncate()
        if features:
            provider.addFeatures(features)
        layer.updateExtents()
        if bbox:
            layer.setCustomProperty(BBOX_PROPERTY, "%.6f,%.6f,%.6f,%.6f" % tuple(bbox))
        update_categories(layer, dataset)
        layer.triggerRepaint()
        return layer.featureCount()
