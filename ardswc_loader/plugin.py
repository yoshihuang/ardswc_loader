# -*- coding: utf-8 -*-
"""Plugin entry point: wires the dock, the map canvas, downloads and layers."""

import os

from qgis.PyQt.QtCore import Qt, QTimer, QUrl
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QFileDialog
from qgis.core import (
    Qgis,
    QgsDataSourceUri,
    QgsRasterLayer,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsCsException,
    QgsProject,
    QgsRectangle,
    QgsSettings,
)

from .catalog import BY_KEY, SERVICE_BOUNDS
from .compat import QAction
from .dock import ArdswcDock
from .exporter import export_layers
from .fetcher import Fetcher, TooLarge, plan_requests
from .layers import KEY_PROPERTY, LayerManager

SETTINGS = "ardswc_loader/"
TITLE = "水保署"
MENU = "水保署開放資料載入"
UAV_GROUP = "水保署 UAV 正射影像"
MAX_UAV = 10
DEBOUNCE_MS = 700
PLUGIN_DIR = os.path.dirname(__file__)


class ArdswcLoader:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dock = None
        self.fetcher = None
        self.layers = None
        self.timer = None
        self._last_bbox = {}   # bbox currently shown in each layer
        self._requested = {}   # bbox of the download in flight

    # -- QGIS plugin API -----------------------------------------------------
    def initGui(self):
        icon = QIcon(os.path.join(PLUGIN_DIR, "icon.svg"))
        self.action = QAction(icon, "水保署開放資料載入 (ARDSWC)", self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.setToolTip("開啟／關閉水保署開放資料載入面板")
        self.action.toggled.connect(self._toggle_dock)
        self.iface.addWebToolBarIcon(self.action)
        self.iface.addPluginToWebMenu(MENU, self.action)

        self.layers = LayerManager()
        self.fetcher = Fetcher()
        self.fetcher.started.connect(self._on_started)
        self.fetcher.finished.connect(self._on_finished)
        self.fetcher.failed.connect(self._on_failed)
        self.fetcher.progress.connect(self._on_progress)

        self.timer = QTimer()
        self.timer.setSingleShot(True)
        self.timer.setInterval(DEBOUNCE_MS)
        self.timer.timeout.connect(self.refresh)

        self.dock = ArdswcDock(self.iface.mainWindow())
        self.dock.selection_changed.connect(self._on_selection_changed)
        self.dock.refresh_requested.connect(lambda: self.refresh(force=True))
        self.dock.export_requested.connect(self.export_all)
        self.dock.auto_toggled.connect(self._on_auto_toggled)
        self.dock.force_check.toggled.connect(self._on_force_toggled)
        self.dock.uav_requested.connect(self.add_selected_uav)
        self.dock.visibilityChanged.connect(self.action.setChecked)
        self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
        self.dock.hide()
        self._restore_settings()

        self.iface.mapCanvas().extentsChanged.connect(self._schedule)
        QgsProject.instance().cleared.connect(self._on_project_cleared)

    def unload(self):
        self._save_settings()
        try:
            self.iface.mapCanvas().extentsChanged.disconnect(self._schedule)
            QgsProject.instance().cleared.disconnect(self._on_project_cleared)
        except TypeError:
            pass  # already disconnected
        if self.timer is not None:
            self.timer.stop()
        if self.fetcher is not None:
            self.fetcher.cancel_all()
        if self.dock is not None:
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
        if self.action is not None:
            self.iface.removeWebToolBarIcon(self.action)
            self.iface.removePluginWebMenu(MENU, self.action)
            self.action.deleteLater()
        self.dock = self.action = self.fetcher = self.timer = None

    # -- settings --------------------------------------------------------------
    def _restore_settings(self):
        settings = QgsSettings()
        # New key (1.0.2): auto update starts switched off for everyone.
        self.dock.auto_check.setChecked(settings.value(SETTINGS + "auto_update", False, type=bool))
        self.dock.select_combo(self.dock.format_combo, settings.value(SETTINGS + "format", "gpkg_single"))
        self.dock.select_combo(self.dock.crs_combo, settings.value(SETTINGS + "crs", "EPSG:3826"))
        # Checked datasets are deliberately not restored: nothing should start
        # downloading the moment QGIS opens.

    def _save_settings(self):
        if self.dock is None:
            return
        settings = QgsSettings()
        settings.setValue(SETTINGS + "auto_update", self.dock.auto_check.isChecked())
        settings.setValue(SETTINGS + "format", self.dock.export_format())
        settings.setValue(SETTINGS + "crs", self.dock.export_crs())

    # -- dock ----------------------------------------------------------------
    def _toggle_dock(self, checked):
        if self.dock is not None:
            self.dock.setVisible(checked)
            if checked:
                self._schedule()

    def _on_force_toggled(self, enabled):
        if enabled:
            self.iface.messageBar().pushMessage(
                TITLE, "已放寬查詢範圍上限。請避免一次查詢過大的範圍，以免造成水保署服務主機負荷過大。",
                Qgis.MessageLevel.Warning, 8)

    def _on_auto_toggled(self, enabled):
        if enabled:
            self._schedule()

    def _on_selection_changed(self, key, checked):
        dataset = BY_KEY[key]
        if checked:
            self.layers.ensure_layer(dataset)
            self._last_bbox.pop(key, None)
            self._request(dataset, self.current_bbox(), force=False)
        else:
            self.fetcher.cancel(key)
            self.layers.remove(key)
            self._last_bbox.pop(key, None)
            self.dock.set_state(key, "")

    def _on_project_cleared(self):
        if self.fetcher is not None:
            self.fetcher.cancel_all()
        self._last_bbox.clear()
        if self.dock is not None:
            self.dock.set_checked([])
            for key in BY_KEY:
                self.dock.set_state(key, "")

    # -- extent handling ---------------------------------------------------
    def current_bbox(self):
        """Visible map extent as a lon/lat bbox clipped to the service area,
        or None when the view is outside Taiwan."""
        canvas = self.iface.mapCanvas()
        source = canvas.mapSettings().destinationCrs()
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        extent = canvas.extent()
        if source != wgs84:
            try:
                transform = QgsCoordinateTransform(source, wgs84, QgsProject.instance())
                extent = transform.transformBoundingBox(extent)
            except QgsCsException:
                return None
        clipped = extent.intersect(QgsRectangle(*SERVICE_BOUNDS))
        if clipped.isEmpty() or clipped.width() <= 0 or clipped.height() <= 0:
            return None
        return (round(clipped.xMinimum(), 6), round(clipped.yMinimum(), 6),
                round(clipped.xMaximum(), 6), round(clipped.yMaximum(), 6))

    def _schedule(self):
        if self.dock is not None and self.dock.auto_check.isChecked() and self.dock.checked_keys():
            self.timer.start()

    def refresh(self, force=False):
        if self.dock is None:
            return
        bbox = self.current_bbox()
        for key in self.dock.checked_keys():
            self._request(BY_KEY[key], bbox, force=force)

    def _request(self, dataset, bbox, force):
        key = dataset.key
        if bbox is None:
            self.dock.set_state(key, "不在服務範圍", "目前地圖不在臺灣範圍內，未下載")
            return
        if not force and self._last_bbox.get(key) == bbox and self.layers.layer(key) is not None:
            return
        try:
            requests = plan_requests(dataset, bbox, force or self.dock.force_check.isChecked())
        except TooLarge as exc:
            self.fetcher.cancel(key)
            self.dock.set_state(key, "請放大地圖", "%s；圖層保留上一次的內容。"
                                "為避免造成水保署主機負荷，建議分區放大查詢。" % exc)
            return
        self._requested[key] = bbox
        self.fetcher.fetch(dataset, bbox, requests)

    # -- download results --------------------------------------------------
    def _on_started(self, key):
        if self.dock is not None:
            self.dock.set_state(key, "下載中…")

    def _on_progress(self, key, done, total):
        if self.dock is not None and total > 1:
            self.dock.set_state(key, "下載中 %d/%d" % (done, total))

    def _on_finished(self, key, rows, skipped, seconds):
        if self.dock is None or key not in self.dock.checked_keys():
            return
        bbox = self._requested.get(key)
        count = self.layers.replace_features(BY_KEY[key], rows, bbox)
        if count is None:
            self.dock.set_state(key, "編輯中未更新", "圖層正在編輯模式，為保護您的修改未更新內容", error=True)
            return
        self._last_bbox[key] = bbox
        tip = "%d 筆，%.1f 秒" % (count, seconds)
        if skipped:
            tip += "（%d 筆無有效座標或幾何，已略過）" % skipped
        self.dock.set_state(key, "%d 筆" % count, tip)

    def _on_failed(self, key, message):
        if self.dock is not None and key in self.dock.checked_keys():
            self.dock.set_state(key, "錯誤", message, error=True)
            self.iface.messageBar().pushMessage(TITLE, "%s：%s" % (BY_KEY[key].title, message),
                                                Qgis.MessageLevel.Warning, 6)

    # -- export ----------------------------------------------------------------
    def export_all(self):
        layers = self.layers.layers()
        if not any(layer.featureCount() for layer in layers):
            self.iface.messageBar().pushMessage(TITLE, "目前沒有可匯出的暫存圖層資料。",
                                                Qgis.MessageLevel.Info, 5)
            return
        settings = QgsSettings()
        start = settings.value(SETTINGS + "folder", os.path.expanduser("~"))
        folder = QFileDialog.getExistingDirectory(self.iface.mainWindow(), "選擇匯出資料夾", start)
        if not folder:
            return
        settings.setValue(SETTINGS + "folder", folder)
        self._save_settings()
        written, errors = export_layers(layers, folder, self.dock.export_format(), self.dock.export_crs())
        if errors:
            self.iface.messageBar().pushMessage(TITLE, "部分圖層匯出失敗：" + "；".join(errors),
                                                Qgis.MessageLevel.Warning, 10)
        if written:
            self.dock.status.setText("已匯出 %d 個檔案到：%s" % (len(written), folder))
            self.iface.messageBar().pushMessage(
                TITLE, "已匯出到 <a href=\"%s\">%s</a>" % (
                    QUrl.fromLocalFile(folder).toString(), folder), Qgis.MessageLevel.Success, 8)

    # -- UAV ortho images --------------------------------------------------------
    def add_selected_uav(self):
        layer = self.layers.layer("uav_ortho")
        if layer is None or layer.selectedFeatureCount() == 0:
            self.iface.messageBar().pushMessage(
                TITLE, "請先勾選「UAV 空拍正射影像範圍」，並在地圖上選取要加入的影像範圍。", Qgis.MessageLevel.Info, 6)
            return
        features = list(layer.selectedFeatures())[:MAX_UAV]
        root = QgsProject.instance().layerTreeRoot()
        group = root.findGroup(UAV_GROUP) or root.insertGroup(0, UAV_GROUP)
        existing = {lyr.customProperty(KEY_PROPERTY) for lyr in QgsProject.instance().mapLayers().values()}
        added = 0
        for feature in features:
            tile_url = feature["TileUrl"]
            image_id = "uav:%s" % feature["Id"]
            if not tile_url or image_id in existing:
                continue
            uri = QgsDataSourceUri()
            uri.setParam("type", "xyz")
            uri.setParam("url", str(tile_url))
            uri.setParam("zmin", str(int(feature["MinZoom"] or 14)))
            uri.setParam("zmax", "21")
            name = "UAV｜%s" % (feature["Title"] or feature["Id"])
            raster = QgsRasterLayer(bytes(uri.encodedUri()).decode("utf-8"), name, "wms")
            if not raster.isValid():
                continue
            raster.setCustomProperty(KEY_PROPERTY, image_id)
            QgsProject.instance().addMapLayer(raster, False)
            group.insertLayer(0, raster)
            added += 1
        note = "（一次最多 %d 幅）" % MAX_UAV if layer.selectedFeatureCount() > MAX_UAV else ""
        self.iface.messageBar().pushMessage(
            TITLE, "已加入 %d 幅 UAV 正射影像%s；影像在放大到各自的最小縮放層級（欄位 MinZoom）後才會顯示。" % (added, note),
            Qgis.MessageLevel.Success, 8)
