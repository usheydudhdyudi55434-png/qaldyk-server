"""Which Almaty district a point lies in.

Boundaries come from OpenStreetMap (sensor/data/almaty_districts.json,
© OpenStreetMap contributors, ODbL) and are stored on the District model.
"""

import json
from pathlib import Path

BOUNDARIES_FILE = Path(__file__).parent / "data" / "almaty_districts.json"


def load_boundaries():
    """The eight districts as (slug, name, geometry) from the bundled file."""

    data = json.loads(BOUNDARIES_FILE.read_text(encoding="utf-8"))

    return [
        (feature["properties"]["slug"], feature["properties"]["name"], feature["geometry"])
        for feature in data["features"]
    ]


def polygons(geometry):
    """Polygons of a GeoJSON geometry, each a list of rings of [lon, lat]."""

    if geometry.get("type") == "Polygon":
        return [geometry["coordinates"]]

    if geometry.get("type") == "MultiPolygon":
        return geometry["coordinates"]

    return []


def in_ring(lon, lat, ring):
    """Ray casting: does a horizontal ray from the point cross the ring an odd number of times?"""

    inside = False

    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > lat) != (y2 > lat):
            crossing = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if lon < crossing:
                inside = not inside

    return inside


def contains(geometry, latitude, longitude):

    for polygon in polygons(geometry):

        outer, holes = polygon[0], polygon[1:]

        if in_ring(longitude, latitude, outer) and not any(
            in_ring(longitude, latitude, hole) for hole in holes
        ):
            return True

    return False


def district_for(latitude, longitude):
    """The District containing the point, or None (no point, or outside the city)."""

    if latitude is None or longitude is None:
        return None

    from .models import District

    for district in District.objects.all():
        if contains(district.boundary, latitude, longitude):
            return district

    return None
