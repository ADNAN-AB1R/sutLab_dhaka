import os
import re

import numpy as np
import pandas as pd
import geopandas as gpd
import pyogrio

"""
Work/education/shop/leisure candidate locations for Dhaka, from two sources:

1. The typed subset of dhaka.data.buildings (the Geofabrik buildings
   shapefile's `type` tag, populated on ~1% of footprints).
2. Point amenities and amenity-tagged polygons read directly from the
   Bangladesh .osm.pbf (dhaka.osm_path) via GDAL's OSM driver - standalone
   shops/restaurants/clinics/schools etc. that never show up as typed
   building polygons. This is the dominant source by count (~29K classified
   POIs in the study area vs ~10K typed buildings).

The .pbf is optional: if absent, the stage falls back to typed buildings only
(with a warning), so the pipeline never hard-fails on the richer data.

To avoid double counting, amenity-tagged *polygons* from the .pbf are dropped
when their `building` tag is itself one of the mapped types - those footprints
are already captured through the buildings shapefile.
"""

# building-tag classification, shared with dhaka/data/buildings.py
from dhaka.data.buildings import TYPE_LOCATION_MAP as BUILDING_TYPE_MAP

EDUCATION_AMENITIES = ["kindergarten", "school", "college", "university"]

AMENITY_LOCATION_MAP = {
    **{ a: "education" for a in EDUCATION_AMENITIES },
    "hospital": "work", "clinic": "work", "doctors": "work", "dentist": "work",
    "pharmacy": "work", "bank": "work", "post_office": "work", "police": "work",
    "townhall": "work", "courthouse": "work", "embassy": "work",
    "marketplace": "shop",
    "restaurant": "leisure", "cafe": "leisure", "fast_food": "leisure",
    "food_court": "leisure", "bar": "leisure", "cinema": "leisure",
    "theatre": "leisure", "community_centre": "leisure", "library": "leisure",
    "place_of_worship": "leisure",
}

TOURISM_LOCATION_MAP = {
    "hotel": "leisure", "guest_house": "leisure", "museum": "leisure",
    "attraction": "leisure",
}

# Effective floor area assumed for a point amenity (no footprint available)
POINT_AREA = 50.0
POINT_FLOORS = 1

OTHER_TAGS_PATTERNS = {
    key: re.compile(r'"%s"=>"([^"]*)"' % key)
    for key in ["amenity", "shop", "leisure", "office", "tourism"]
}

def configure(context):
    context.stage("dhaka.data.buildings")
    context.stage("dhaka.data.spatial.iris")
    context.config("data_path")
    context.config("dhaka.osm_path", "osm/bangladesh-latest.osm.pbf")

def classify(amenity, shop, leisure, office, tourism):
    """Location type per POI, with education taking precedence. Any shop=* is
    a shop, any office=* a workplace, any leisure=* a leisure venue."""
    location_type = amenity.map(AMENITY_LOCATION_MAP)
    location_type = location_type.fillna(shop.notna().map({ True: "shop", False: np.nan }))
    location_type = location_type.fillna(office.notna().map({ True: "work", False: np.nan }))
    location_type = location_type.fillna(leisure.notna().map({ True: "leisure", False: np.nan }))
    location_type = location_type.fillna(tourism.map(TOURISM_LOCATION_MAP))
    return location_type

def extract_pois(osm_path, df_zones):
    bounds = tuple(df_zones.to_crs("EPSG:4326").total_bounds)

    # --- Point amenities (tags live in the packed other_tags column)
    df_points = pyogrio.read_dataframe(osm_path, layer = "points", bbox = bounds)

    tags = df_points["other_tags"].fillna("")
    tag_values = {
        key: tags.str.extract(pattern, expand = False)
        for key, pattern in OTHER_TAGS_PATTERNS.items()
    }

    df_points["amenity"] = tag_values["amenity"]
    df_points["location_type"] = classify(
        tag_values["amenity"], tag_values["shop"], tag_values["leisure"],
        tag_values["office"], tag_values["tourism"],
    )
    df_points = df_points[df_points["location_type"].notna()].copy()

    df_points["building"] = df_points["amenity"]
    df_points["area"] = POINT_AREA
    df_points["floors"] = POINT_FLOORS
    df_points = df_points[["geometry", "building", "amenity", "area", "floors", "location_type"]]

    # --- Amenity-tagged polygons (schools/hospitals mapped as areas whose
    # building tag is generic, e.g. building=yes - invisible to the buildings
    # shapefile's `type` column)
    df_polygons = pyogrio.read_dataframe(
        osm_path, layer = "multipolygons", bbox = bounds,
        columns = ["osm_id", "building", "amenity", "shop", "leisure", "office", "tourism"],
    )

    df_polygons["location_type"] = classify(
        df_polygons["amenity"], df_polygons["shop"], df_polygons["leisure"],
        df_polygons["office"], df_polygons["tourism"],
    )
    df_polygons = df_polygons[df_polygons["location_type"].notna()]
    # already captured via the typed buildings shapefile
    df_polygons = df_polygons[~df_polygons["building"].isin(BUILDING_TYPE_MAP)].copy()

    df_polygons = df_polygons.to_crs(df_zones.crs)
    df_polygons["area"] = df_polygons.area.clip(lower = POINT_AREA)
    df_polygons["floors"] = POINT_FLOORS
    df_polygons["geometry"] = df_polygons.centroid
    df_polygons["building"] = df_polygons["building"].fillna(df_polygons["amenity"])
    df_polygons = df_polygons[["geometry", "building", "amenity", "area", "floors", "location_type"]]

    df_pois = pd.concat([
        gpd.GeoDataFrame(df_points, crs = df_points.crs).to_crs(df_zones.crs),
        gpd.GeoDataFrame(df_polygons, crs = df_zones.crs),
    ], ignore_index = True)

    # Impute spatial identifiers; drops POIs inside the bbox but outside the zones
    df_pois = gpd.sjoin(
        df_pois, df_zones[["geometry", "commune_id", "iris_id"]],
        how = "left", predicate = "within",
    ).reset_index(drop = True).drop(columns = ["index_right"])

    return df_pois.dropna(subset = ["commune_id", "iris_id"])

def execute(context):
    df_zones = context.stage("dhaka.data.spatial.iris")

    df_buildings = context.stage("dhaka.data.buildings")
    df_buildings = df_buildings[df_buildings["location_type"].notna()].copy()
    df_buildings["floors"] = df_buildings["floors"].fillna(1).astype(int)

    columns = [
        "geometry", "building", "amenity", "area", "floors",
        "commune_id", "iris_id", "location_type",
    ]
    df_buildings = df_buildings[columns]

    osm_path = "{}/{}".format(context.config("data_path"), context.config("dhaka.osm_path"))

    if os.path.exists(osm_path):
        df_pois = extract_pois(osm_path, df_zones)[columns]
        print("Location candidates: {:,} typed buildings + {:,} OSM POIs".format(
            len(df_buildings), len(df_pois)
        ))
        df = pd.concat([df_buildings, df_pois], ignore_index = True)
    else:
        print(f"WARNING: no .osm.pbf at {osm_path} - using typed buildings only")
        df = df_buildings

    counts = df["location_type"].value_counts()
    print("Candidates by type:", counts.to_dict())

    return gpd.GeoDataFrame(df, crs = df_zones.crs)
