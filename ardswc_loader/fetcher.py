# -*- coding: utf-8 -*-
"""Asynchronous downloads for the ARDSWC services.

A *job* is one dataset for one map extent.  MVT jobs fetch many tiles, list
jobs fetch one catalogue.  All requests go through QgsNetworkAccessManager
(QGIS proxy / SSL settings apply), at most ``MAX_ACTIVE`` at a time.  Only the
newest job per dataset counts: a new extent cancels the previous job.

Responses are cached (tiles and catalogues) so panning back and forth does
not download the same data again.  Decoding / merging runs in a QgsTask.
"""

import json
import time
from collections import OrderedDict
from functools import partial

from qgis.PyQt.QtCore import QObject, QTimer, QUrl, pyqtSignal
from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest
from qgis.core import QgsApplication, QgsGeometry, QgsNetworkAccessManager, QgsPointXY, QgsRectangle, QgsTask

from . import mvt
from .catalog import (CATALOG_CACHE_SECONDS, DATA_ZOOMS, FORCED_MAX_TILES, MAX_TILES, MVT, POINTS,
                      REPORT_FILE_ROOT, UAV)

USER_AGENT = b"QGIS-ARDSWCLoader/1.0"
MAX_ACTIVE = 6
TIMEOUT_MS = 60000
TILE_CACHE_SIZE = 3000


class TooLarge(Exception):
    """The view needs more tiles than allowed."""


class ServiceError(Exception):
    """The service answered with something unusable."""


# ---------------------------------------------------------------- planning
def valid_bbox(bbox):
    xmin, ymin, xmax, ymax = (float(v) for v in bbox)
    if not (xmin < xmax and ymin < ymax):
        raise ValueError("invalid bbox %r" % (bbox,))
    if not (-180 <= xmin <= 180 and -180 <= xmax <= 180 and -90 <= ymin <= 90 and -90 <= ymax <= 90):
        raise ValueError("bbox outside lon/lat range %r" % (bbox,))
    return xmin, ymin, xmax, ymax


def choose_zoom(bbox, force=False):
    """Highest data zoom whose tile count fits; raises TooLarge otherwise."""
    for zoom in DATA_ZOOMS:
        if mvt.tile_count(bbox, zoom) <= MAX_TILES:
            return zoom
    lowest = DATA_ZOOMS[-1]
    count = mvt.tile_count(bbox, lowest)
    if force and count <= FORCED_MAX_TILES:
        return lowest
    raise TooLarge("目前範圍需要 %d 張圖磚（上限 %d），請放大地圖" % (count, FORCED_MAX_TILES if force else MAX_TILES))


def plan_requests(dataset, bbox, force=False):
    """[(url, meta)] for one job.  meta is (z, x, y) for tiles, None for lists."""
    bbox = valid_bbox(bbox)
    if dataset.kind == MVT:
        zoom = choose_zoom(bbox, force)
        return [(dataset.tile_url(zoom, x, y), (zoom, x, y)) for x, y in mvt.tiles_for_bbox(bbox, zoom)]
    return [(dataset.url, None)]


# ---------------------------------------------------------------- parsing
def _clean(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _records(payload, path):
    data = json.loads(bytes(payload).decode("utf-8-sig"))
    for key in path:
        data = data.get(key) if isinstance(data, dict) else None
    if not isinstance(data, list):
        raise ServiceError("服務回傳的資料格式不是清單")
    return [r for r in data if isinstance(r, dict)]


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None   # NaN check


def parse_points(dataset, payload, bbox):
    xmin, ymin, xmax, ymax = bbox
    rows, skipped = [], 0
    for record in _records(payload, dataset.rows_path):
        lon, lat = _number(record.get(dataset.lon_field)), _number(record.get(dataset.lat_field))
        if lon is None or lat is None or not (-180 <= lon <= 180 and -90 <= lat <= 90) or (lon == 0 and lat == 0):
            skipped += 1
            continue
        if not (xmin <= lon <= xmax and ymin <= lat <= ymax):
            continue
        attrs = {k: _clean(v) for k, v in record.items()}
        if dataset.key == "disaster_report" and attrs.get("FileUrl"):
            attrs["ReportUrl"] = REPORT_FILE_ROOT + str(attrs["FileUrl"])
        rows.append((QgsGeometry.fromPointXY(QgsPointXY(lon, lat)), attrs))
    return rows, skipped


def uav_min_zoom(access_url):
    """Folder names look like 20261020_121785650_024492618_14_001_RSImage...;
    the 4th token is the first zoom level that has tiles."""
    name = str(access_url).rstrip("/").split("/")[-1]
    parts = name.split("_")
    if len(parts) > 3 and parts[3].isdigit():
        return max(0, min(int(parts[3]), 21))
    return 14


def parse_uav(dataset, payload, bbox):
    view = QgsRectangle(*bbox)
    rows, skipped = [], 0
    for record in _records(payload, dataset.rows_path):
        west, east = _number(record.get("WestBoundLongitude")), _number(record.get("EastBoundLongitude"))
        south, north = _number(record.get("SouthBoundLatitude")), _number(record.get("NorthBoundLatitude"))
        if None in (west, east, south, north) or not (west < east and south < north):
            skipped += 1
            continue
        footprint = QgsRectangle(west, south, east, north)
        if not footprint.intersects(view):
            continue
        attrs = {k: _clean(v) for k, v in record.items()}
        attrs["MinZoom"] = uav_min_zoom(record.get("AccessURL", ""))
        attrs["TileUrl"] = str(record.get("AccessURL", "")) + "{z}/{y}/{x}.jpg"
        geometry = QgsGeometry.fromRect(footprint)
        geometry.convertToMultiType()
        rows.append((geometry, attrs))
    return rows, skipped


def parse_job(dataset, payloads, bbox):
    """payloads: [(meta, bytes)] → (rows, skipped)."""
    if dataset.kind == MVT:
        tiles = [(meta[0], meta[1], meta[2], data) for meta, data in payloads]
        return mvt.decode_and_merge(tiles, dataset.geometry, dataset.id_fields)
    payload = payloads[0][1] if payloads else b""
    try:
        if dataset.kind == POINTS:
            return parse_points(dataset, payload, bbox)
        if dataset.kind == UAV:
            return parse_uav(dataset, payload, bbox)
    except ValueError as exc:
        raise ServiceError("服務回傳的不是 JSON（%s）" % exc) from exc
    raise ServiceError("unknown dataset kind %r" % dataset.kind)


def _parse_in_task(task, dataset, payloads, bbox):
    return parse_job(dataset, payloads, bbox)


# ---------------------------------------------------------------- fetcher
class _Job:
    __slots__ = ("dataset", "bbox", "generation", "pending", "results", "replies", "started", "failed")

    def __init__(self, dataset, bbox, generation, requests):
        self.dataset = dataset
        self.bbox = bbox
        self.generation = generation
        self.pending = list(requests)       # not yet sent
        self.results = {}                   # url -> (meta, bytes)
        self.replies = {}                   # url -> (reply, timer)
        self.started = time.monotonic()
        self.failed = False


class Fetcher(QObject):
    started = pyqtSignal(str)
    progress = pyqtSignal(str, int, int)              # key, done, total
    finished = pyqtSignal(str, object, int, float)    # key, rows, skipped, seconds
    failed = pyqtSignal(str, str)                     # key, message

    def __init__(self, parent=None):
        super().__init__(parent)
        self._generation = {}
        self._jobs = OrderedDict()   # key -> _Job (insertion = fairness order)
        self._tasks = {}
        self._alive = set()
        self._tile_cache = OrderedDict()
        self._list_cache = {}

    # -- public -----------------------------------------------------------
    def fetch(self, dataset, bbox, requests):
        key = dataset.key
        self.cancel(key)
        generation = self._generation.get(key, 0) + 1
        self._generation[key] = generation
        job = _Job(dataset, tuple(bbox), generation, requests)
        for url, meta in list(job.pending):
            cached = self._cached(url, meta)
            if cached is not None:
                job.results[url] = (meta, cached)
                job.pending.remove((url, meta))
        self._jobs[key] = job
        self.started.emit(key)
        if not job.pending:
            self._complete(job)
        else:
            self._pump()

    def cancel(self, key):
        self._generation[key] = self._generation.get(key, 0) + 1
        job = self._jobs.pop(key, None)
        if job is not None:
            for reply, timer in job.replies.values():
                timer.stop()
                reply.abort()
                reply.deleteLater()
            job.replies.clear()
        task = self._tasks.pop(key, None)
        if task is not None:
            task.cancel()
        self._pump()

    def cancel_all(self):
        for key in list(self._generation):
            self.cancel(key)

    def clear_cache(self):
        self._tile_cache.clear()
        self._list_cache.clear()

    # -- cache ---------------------------------------------------------------
    def _cached(self, url, meta):
        if meta is not None:
            data = self._tile_cache.get(url)
            if data is not None:
                self._tile_cache.move_to_end(url)
            return data
        entry = self._list_cache.get(url)
        if entry and time.monotonic() - entry[0] < CATALOG_CACHE_SECONDS:
            return entry[1]
        return None

    def _store(self, url, meta, data):
        if meta is not None:
            self._tile_cache[url] = data
            while len(self._tile_cache) > TILE_CACHE_SIZE:
                self._tile_cache.popitem(last=False)
        else:
            self._list_cache[url] = (time.monotonic(), data)

    # -- network -------------------------------------------------------------
    def _active(self):
        return sum(len(job.replies) for job in self._jobs.values())

    def _pump(self):
        # Round-robin over jobs so one big tile job does not starve the others.
        while self._active() < MAX_ACTIVE:
            sent = False
            for job in list(self._jobs.values()):
                if job.pending and self._active() < MAX_ACTIVE:
                    url, meta = job.pending.pop(0)
                    self._send(job, url, meta)
                    sent = True
            if not sent:
                break

    def _send(self, job, url, meta):
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b"User-Agent", USER_AGENT)
        reply = QgsNetworkAccessManager.instance().get(request)
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(partial(self._timeout, job.dataset.key, job.generation, url))
        timer.start(TIMEOUT_MS)
        job.replies[url] = (reply, timer)
        reply.finished.connect(partial(self._reply_finished, job.dataset.key, job.generation, url, meta, reply))

    def _job(self, key, generation):
        job = self._jobs.get(key)
        return job if job is not None and job.generation == generation else None

    def _timeout(self, key, generation, url):
        job = self._job(key, generation)
        if job is None or url not in job.replies:
            return
        reply, _timer = job.replies.pop(url)
        reply.abort()
        reply.deleteLater()
        self._fail(job, "連線逾時（%d 秒）" % (TIMEOUT_MS // 1000))

    def _reply_finished(self, key, generation, url, meta, reply):
        job = self._job(key, generation)
        if job is None or job.replies.get(url, (None,))[0] is not reply:
            reply.deleteLater()
            return
        job.replies.pop(url)[1].stop()
        try:
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            if reply.error() != QNetworkReply.NetworkError.NoError:
                if meta is not None and status is not None and int(status) == 404:
                    data = b""     # a missing tile simply has no data
                else:
                    self._fail(job, "下載失敗：%s" % reply.errorString())
                    return
            elif status is not None and int(status) >= 400:
                self._fail(job, "服務回應 HTTP %s" % status)
                return
            else:
                data = bytes(reply.readAll())
        finally:
            reply.deleteLater()
        self._store(url, meta, data)
        job.results[url] = (meta, data)
        total = len(job.results) + len(job.pending) + len(job.replies)
        self.progress.emit(key, len(job.results), total)
        if not job.pending and not job.replies:
            self._complete(job)
        self._pump()

    def _fail(self, job, message):
        if job.failed:
            return
        job.failed = True
        key = job.dataset.key
        self.cancel(key)
        self.failed.emit(key, message)

    def _complete(self, job):
        key = job.dataset.key
        if self._jobs.get(key) is job:
            del self._jobs[key]
        payloads = list(job.results.values())
        holder = {}
        task = QgsTask.fromFunction(
            "水保署：處理 %s" % job.dataset.title, _parse_in_task, job.dataset, payloads, job.bbox,
            on_finished=partial(self._task_finished, key, job.generation, job.started, holder),
            flags=QgsTask.Flag.CanCancel | QgsTask.Flag.Silent)
        holder["task"] = task
        self._alive.add(task)
        self._tasks[key] = task
        QgsApplication.taskManager().addTask(task)

    def _task_finished(self, key, generation, started, holder, exception, result=None):
        self._alive.discard(holder.get("task"))
        if self._generation.get(key) != generation:
            return
        self._tasks.pop(key, None)
        if exception is not None or result is None:
            self.failed.emit(key, str(exception) if exception is not None else "處理已取消")
            return
        rows, skipped = result
        self.finished.emit(key, rows, skipped, time.monotonic() - started)
