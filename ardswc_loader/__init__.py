# -*- coding: utf-8 -*-
"""ARDSWC Open Data Loader - load the open map services of the Agency of Rural
Development and Soil and Water Conservation (水保署) into QGIS.

Copyright (C) 2026 黃敏郎

This program is free software; you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation; either version 2 of the License, or (at your option) any later
version.
"""


def classFactory(iface):  # noqa: N802 (QGIS API name)
    from .plugin import ArdswcLoader
    return ArdswcLoader(iface)
