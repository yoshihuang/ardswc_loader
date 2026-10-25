# -*- coding: utf-8 -*-
"""Dock panel: dataset check list, update controls and one-click export."""

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QBrush, QColor
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qgis.gui import QgsDockWidget

from .catalog import DATASETS, GROUPS, LICENSE_URL, PLATFORM_URL, SOURCE_TEXT, limit_text
from .exporter import CRS_CHOICES, FORMATS

KEY_ROLE = Qt.ItemDataRole.UserRole + 1


class ArdswcDock(QgsDockWidget):
    selection_changed = pyqtSignal(str, bool)   # dataset key, checked
    refresh_requested = pyqtSignal()
    export_requested = pyqtSignal()
    auto_toggled = pyqtSignal(bool)
    uav_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__("水保署開放資料載入", parent)
        self.setObjectName("ArdswcLoaderDock")
        self._items = {}
        self._muted = False

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(6, 6, 6, 6)

        hint = QLabel("勾選資料項目後，移動或縮放地圖時會自動下載目前畫面範圍內的資料，"
                      "以暫存圖層顯示。")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["資料項目", "狀態"])
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(False)
        self._build_tree()
        self.tree.itemChanged.connect(self._item_changed)
        layout.addWidget(self.tree, 1)

        buttons = QHBoxLayout()
        self.clear_button = QPushButton("全部取消勾選")
        self.clear_button.clicked.connect(self.clear_checks)
        self.refresh_button = QPushButton("立即更新")
        self.refresh_button.setToolTip("以目前地圖範圍重新下載所有勾選的資料")
        self.refresh_button.clicked.connect(self.refresh_requested)
        buttons.addWidget(self.clear_button)
        buttons.addStretch(1)
        buttons.addWidget(self.refresh_button)
        layout.addLayout(buttons)

        self.auto_check = QCheckBox("移動地圖時自動更新")
        self.auto_check.setChecked(True)
        self.auto_check.toggled.connect(self.auto_toggled)
        layout.addWidget(self.auto_check)
        self.force_check = QCheckBox("忽略建議範圍（大範圍可能要下載很久）")
        self.force_check.setToolTip("向量圖磚資料預設最多下載 48 張圖磚；勾選後放寬到 400 張（z12）。")
        layout.addWidget(self.force_check)

        self.uav_button = QPushButton("加入選取的 UAV 正射影像")
        self.uav_button.setToolTip("先在「UAV 空拍正射影像範圍」圖層選取影像範圍（最多 10 幅），再按此鈕加入影像圖磚")
        self.uav_button.clicked.connect(self.uav_requested)
        layout.addWidget(self.uav_button)

        export_box = QGroupBox("匯出暫存圖層")
        form = QFormLayout(export_box)
        self.format_combo = QComboBox()
        for format_id, label, *_rest in FORMATS:
            self.format_combo.addItem(label, format_id)
        self.crs_combo = QComboBox()
        for crs_id, label in CRS_CHOICES:
            self.crs_combo.addItem(label, crs_id)
        self.export_button = QPushButton("一鍵匯出所有暫存圖層…")
        self.export_button.clicked.connect(self.export_requested)
        form.addRow("格式", self.format_combo)
        form.addRow("座標", self.crs_combo)
        form.addRow(self.export_button)
        layout.addWidget(export_box)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        credit = QLabel('資料來源：<a href="%s">%s</a>，依<a href="%s">政府網站資料開放宣告</a>提供，'
                        "使用時請註明出處。" % (PLATFORM_URL, SOURCE_TEXT, LICENSE_URL))
        credit.setWordWrap(True)
        credit.setOpenExternalLinks(True)
        credit.setStyleSheet("color: palette(mid); font-size: 11px;")
        layout.addWidget(credit)

        self.setWidget(body)

    # -- tree ----------------------------------------------------------------
    def _build_tree(self):
        groups = {}
        for name in GROUPS:
            item = QTreeWidgetItem(self.tree, [name, ""])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate)
            item.setCheckState(0, Qt.CheckState.Unchecked)
            item.setExpanded(True)
            groups[name] = item
        for dataset in DATASETS:
            item = QTreeWidgetItem(groups[dataset.group], [dataset.title, ""])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Unchecked)
            item.setData(0, KEY_ROLE, dataset.key)
            item.setToolTip(0, "%s\n%s\nAPI：%s" % (dataset.description, limit_text(dataset), dataset.url))
            self._items[dataset.key] = item

    def _item_changed(self, item, column):
        if column != 0 or self._muted:
            return
        key = item.data(0, KEY_ROLE)
        if key:
            self.selection_changed.emit(key, item.checkState(0) == Qt.CheckState.Checked)

    def checked_keys(self):
        return [key for key, item in self._items.items() if item.checkState(0) == Qt.CheckState.Checked]

    def set_checked(self, keys, emit=False):
        self._muted = not emit
        try:
            for key, item in self._items.items():
                item.setCheckState(0, Qt.CheckState.Checked if key in keys else Qt.CheckState.Unchecked)
        finally:
            self._muted = False

    def clear_checks(self):
        for key in self.checked_keys():
            self._items[key].setCheckState(0, Qt.CheckState.Unchecked)

    def set_state(self, key, text, tooltip=None, error=False):
        item = self._items.get(key)
        if item is None:
            return
        item.setText(1, text)
        item.setToolTip(1, tooltip or text)
        item.setForeground(1, QBrush(QColor("#c1121f")) if error else self.tree.palette().text())

    # -- settings helpers --------------------------------------------------
    def export_format(self):
        return self.format_combo.currentData()

    def export_crs(self):
        return self.crs_combo.currentData()

    def select_combo(self, combo, value):
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)
