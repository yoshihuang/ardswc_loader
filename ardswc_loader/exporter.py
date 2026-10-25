# -*- coding: utf-8 -*-
"""Save every plugin scratch layer to a folder in one go."""

import os
from datetime import datetime

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
    QgsVectorFileWriter,
    QgsVectorLayer,
)

from .compat import save_default_style_to_gpkg
from .catalog import LICENSE_TEXT, LICENSE_URL, SOURCE_TEXT
from .layers import BBOX_PROPERTY, KEY_PROPERTY

# (id, label, OGR driver, extension, one file for all layers?)
FORMATS = [
    ("gpkg_single", "GeoPackage（全部圖層存成一個檔）", "GPKG", ".gpkg", True),
    ("gpkg", "GeoPackage（每個圖層一個檔）", "GPKG", ".gpkg", False),
    ("shp", "ESRI Shapefile", "ESRI Shapefile", ".shp", False),
    ("geojson", "GeoJSON", "GeoJSON", ".geojson", False),
]
FORMAT_BY_ID = {f[0]: f for f in FORMATS}

CRS_CHOICES = [
    ("EPSG:4326", "WGS 84 經緯度（EPSG:4326，原始資料）"),
    ("EPSG:3826", "TWD97 / TM2 121（EPSG:3826，臺灣本島）"),
    ("EPSG:3825", "TWD97 / TM2 119（EPSG:3825，澎湖金馬）"),
    ("PROJECT", "與目前專案座標系統相同"),
]


def shapefile_field_names(names):
    """DBF field names are limited to 10 bytes.  GDAL cuts UTF-8 names in the
    middle of a character (e.g. '岩石強度' -> '岩石強\ufffd'), so shorten on
    character boundaries ourselves and keep the names unique."""
    used, result = set(), []
    for name in names:
        candidate = name
        while len(candidate.encode("utf-8")) > 10:
            candidate = candidate[:-1]
        base, counter = candidate, 1
        while candidate.lower() in used:
            suffix = str(counter)
            base_cut = base
            while len((base_cut + suffix).encode("utf-8")) > 10:
                base_cut = base_cut[:-1]
            candidate = base_cut + suffix
            counter += 1
        used.add(candidate.lower())
        result.append(candidate)
    return result


def _write(layer, path, driver, layer_name, dest_crs, overwrite_file, project):
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = driver
    options.fileEncoding = "UTF-8"
    options.layerName = layer_name
    options.actionOnExistingFile = (
        QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteFile if overwrite_file
        else QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteLayer)
    if dest_crs.isValid() and dest_crs != layer.crs():
        options.ct = QgsCoordinateTransform(layer.crs(), dest_crs, project)
    if driver == "ESRI Shapefile":
        options.attributesExportNames = shapefile_field_names(layer.fields().names())
    if driver == "GeoJSON":
        options.layerOptions = ["RFC7946=NO", "WRITE_BBOX=YES"]
    result = QgsVectorFileWriter.writeAsVectorFormatV3(layer, path, project.transformContext(), options)
    error, message = result[0], result[1]
    if error != QgsVectorFileWriter.WriterError.NoError:
        raise RuntimeError(message or "寫檔錯誤代碼 %s" % error)


def _copy_style_into_gpkg(source, path, layer_name):
    """Store the layer's style inside the GeoPackage so it opens styled."""
    exported = QgsVectorLayer("%s|layername=%s" % (path, layer_name), layer_name, "ogr")
    if not exported.isValid():
        return
    exported.setRenderer(source.renderer().clone())
    if source.labeling() is not None:
        exported.setLabeling(source.labeling().clone())
    exported.setLabelsEnabled(source.labelsEnabled())
    exported.setOpacity(source.opacity())
    exported.setMetadata(source.metadata())
    try:
        save_default_style_to_gpkg(exported, layer_name)
    except (TypeError, AttributeError, RuntimeError):
        # The style is a convenience: a .qml sidecar file is written as well.
        exported.saveNamedStyle(os.path.splitext(path)[0] + "_" + layer_name + ".qml")


def export_layers(layers, folder, format_id, crs_choice, project=None):
    """Write ``layers`` into ``folder``.  Returns (written_paths, errors)."""
    project = project or QgsProject.instance()
    _fmt_id, _label, driver, extension, single_file = FORMAT_BY_ID[format_id]
    if crs_choice == "PROJECT":
        dest_crs = project.crs()
    else:
        dest_crs = QgsCoordinateReferenceSystem(crs_choice)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(folder, exist_ok=True)

    written, errors = [], []
    single_path = os.path.join(folder, "ARDSWC_%s%s" % (stamp, extension))
    first = True
    summary = []
    for layer in layers:
        key = layer.customProperty(KEY_PROPERTY) or layer.id()
        if layer.featureCount() == 0:
            summary.append("%s：0 筆（未匯出）" % layer.name())
            continue
        path = single_path if single_file else os.path.join(
            folder, "ARDSWC_%s_%s%s" % (key, stamp, extension))
        try:
            _write(layer, path, driver, key, dest_crs, first or not single_file, project)
        except RuntimeError as exc:
            errors.append("%s：%s" % (layer.name(), exc))
            continue
        first = False
        if driver == "GPKG":
            _copy_style_into_gpkg(layer, path, key)
        else:
            layer.saveNamedStyle(os.path.splitext(path)[0] + ".qml")
        if path not in written:
            written.append(path)
        summary.append("%s：%d 筆 → %s（圖層 %s）%s" % (
            layer.name(), layer.featureCount(), os.path.basename(path), key,
            "\n    下載範圍 bbox：%s" % layer.customProperty(BBOX_PROPERTY)
            if layer.customProperty(BBOX_PROPERTY) else ""))

    if written:
        note = os.path.join(folder, "ARDSWC_%s_資料來源說明.txt" % stamp)
        with open(note, "w", encoding="utf-8") as handle:
            handle.write("資料來源：%s\n" % SOURCE_TEXT)
            handle.write("授權：%s\n%s\n" % (LICENSE_TEXT, LICENSE_URL))
            handle.write("使用時請註明出處。\n\n")
            handle.write("匯出時間：%s\n" % datetime.now().isoformat(timespec="seconds"))
            handle.write("座標系統：%s\n\n" % (dest_crs.authid() or dest_crs.description()))
            handle.write("\n".join(summary) + "\n")
        written.append(note)
    return written, errors
