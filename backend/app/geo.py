from geoalchemy2 import WKTElement
from geoalchemy2.shape import to_shape
from shapely.geometry import Point


def point_wkt(lng: float, lat: float) -> WKTElement:
    """PostGIS expects longitude first (X), latitude second (Y). SRID 4326."""
    return WKTElement(f"POINT({lng} {lat})", srid=4326)


def coords_from_geometry(geom) -> tuple[float, float] | None:
    if geom is None:
        return None
    shape = to_shape(geom)
    if not isinstance(shape, Point):
        return None
    return float(shape.y), float(shape.x)  # lat, lng
