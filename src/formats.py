"""Tidy up the format labels CKAN publishers type by hand.

The catalogue holds 'XLSX', 'EXCEL (.XLSX)', '.XLSX' and even 'XSLX' for the
same thing. Anything that compares formats, such as picking six sources that
differ from each other, will be fooled unless we fold them first.

We keep the raw string too. The tidy one is for logic, the raw one is evidence.
"""

from __future__ import annotations

import re

# Words that mean the same format. Checked in order, first match wins.
_RULES: list[tuple[str, str]] = [
    (r"\bGEOJSON\b", "GEOJSON"),
    (r"\bGPKG|GEOPACKAGE\b", "GEOPACKAGE"),
    (r"\bSHP|SHAPEFILE\b", "SHAPEFILE"),
    (r"\bXLSX|XSLX|EXCEL\b", "XLSX"),
    (r"\bXLS\b", "XLS"),
    (r"\bTSV\b", "TSV"),
    (r"\bCSV\b", "CSV"),
    (r"\bJSON\b", "JSON"),
    (r"\bXML|XSD\b", "XML"),
    (r"\bZIP|KMZ\b", "ZIP"),
    (r"\bPDF\b", "PDF"),
    (r"\bDOCX?\b|\bRTF\b", "DOC"),
    (r"\bTXT\b", "TXT"),
    (r"\bWMS|WFS|WCS|ARCGIS|ESRI|REST|OGC|MAP VIEWER\b", "WEB_SERVICE"),
    (r"\bAPI\b", "API"),
    (r"\bHTML|WEBSITE|URL\b", "HTML"),
    (r"\bKML\b", "KML"),
    (r"\bPNG|JPE?G|JP2|TIFF?|GEOTIFF|ECW\b", "IMAGE"),
    (r"\bNETCDF\b", "NETCDF"),
    (r"\bTAB|MIF|DWG|DXF|GDB|SLD\b", "GIS_OTHER"),
    (r"\bAUDIO\b", "AUDIO"),
]

# Formats a machine can read records out of. A dataset needs at least one of
# these or Part 2 cannot process it, however promising the title sounds.
MACHINE_READABLE = {"CSV", "TSV", "XLSX", "XLS", "JSON", "GEOJSON", "XML", "ZIP", "TXT"}

# Formats that are not a clean CSV. The assignment asks for at least one.
NOT_PLAIN_CSV = {"XLSX", "XLS", "JSON", "GEOJSON", "XML", "ZIP"}


def normalise(raw: str | None) -> str:
    """Fold a publisher's format string into one of our tokens."""
    if not raw:
        return "UNKNOWN"
    text = re.sub(r"[^A-Z0-9 ]+", " ", raw.upper()).strip()
    for pattern, token in _RULES:
        if re.search(pattern, text):
            return token
    return "OTHER"


def machine_readable(formats) -> set[str]:
    """The subset of a dataset's formats that we could actually parse."""
    return {f for f in formats if f in MACHINE_READABLE}
