# -*- coding: utf-8 -*-
"""Small helpers that keep the plugin working on QGIS 3.34+ (Qt5) and QGIS 4 (Qt6)."""

from qgis.core import Qgis, QgsField

try:  # Qt6 / QGIS 4: QAction lives in QtGui
    from qgis.PyQt.QtGui import QAction
except ImportError:  # Qt5
    from qgis.PyQt.QtWidgets import QAction  # noqa: F401

_USE_METATYPE = Qgis.versionInt() >= 33800

if _USE_METATYPE:
    from qgis.PyQt.QtCore import QMetaType

    _TYPES = {"string": QMetaType.Type.QString, "double": QMetaType.Type.Double}
else:  # QGIS 3.34 - 3.36 (always Qt5)
    from qgis.PyQt.QtCore import QVariant

    _TYPES = {"string": QVariant.String, "double": QVariant.Double}


def make_field(name, kind):
    """QgsField of type 'string' or 'double' without deprecated constructors."""
    return QgsField(name, _TYPES.get(kind, _TYPES["string"]))


def save_default_style_to_gpkg(layer, name):
    """Store a style in the GeoPackage as the layer's default style."""
    saver = getattr(layer, "saveStyleToDatabaseV2", None)
    if saver is not None:
        saver(name, "ARDSWC Loader", True, "")
    else:
        layer.saveStyleToDatabase(name, "ARDSWC Loader", True, "")
