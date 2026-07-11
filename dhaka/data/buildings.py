import os
import numpy as np
import pandas as pd
import geopandas as gpd

"""
Loads Dhaka's OSM-derived building polygons. The source file
(gis_osm_buildings_a_free_1.shp) is a *nationwide* Geofabrik extract (11M+
rows) - reading it in full is impractical, so this stage clips to the study
area's bounding box while reading.

Unlike Seville (a plain, untyped building registry, home-only), the Geofabrik
schema carries a `type` tag populated on ~1% of rows (school/hospital/mosque/
commercial/retail/office/warehouse/etc). This stage classifies those into a
`location_type` (education/work/shop/leisure), so it doubles as the source for
both the home-candidate pool (dhaka/locations/home.py) and the work/education/
shop/leisure candidate pool (dhaka/data/osm/locations.py) - avoiding reading
the huge source file twice. The untyped remainder (~99% of rows: house,
residential, apartments, and unlabeled footprints) is treated purely as a home
candidate, matching Seville's untyped-registry approach.

NOTE: this only captures amenities that show up as *building* polygons. Point
amenities (standalone shops, restaurants, bus stops, etc, tagged as OSM nodes)
are not present in this file and need a real OSM .pbf extract to fill in - see
dhaka.osm_path in the config.
"""

DEFAULT_FLOORS = 2

TYPE_LOCATION_MAP = {
    "school": "education", "college": "education", "university": "education",
    "kindergarten": "education",
    "industrial": "work", "commercial": "work", "office": "work",
    "warehouse": "work", "hospital": "work", "public": "work", "train_station": "work",
    "mosque": "leisure", "hotel": "leisure", "church": "leisure", "temple": "leisure",
    "retail": "shop", "supermarket": "shop", "mall": "shop", "kiosk": "shop",
}

def configure(context):
    context.config("data_path")
    context.config("dhaka.buildings_path", "buildings/gis_osm_buildings_a_free_1.shp")
    context.stage("dhaka.data.spatial.iris")

def execute(context):
    df_zones = context.stage("dhaka.data.spatial.iris")
    BUILDINGS_PATH = "{}/{}".format(context.config("data_path"), context.config("dhaka.buildings_path"))

    # Source file is EPSG:4326 - clip to bounding box of the study area while reading
    bounds = tuple(df_zones.to_crs("EPSG:4326").total_bounds)
    df_buildings = gpd.read_file(BUILDINGS_PATH, bbox = bounds, columns = ["osm_id", "type", "geometry"])

    if len(df_buildings) == 0:
        raise RuntimeError(f"No buildings found within study area bounds {bounds} in {BUILDINGS_PATH}")

    df_buildings = df_buildings.to_crs(df_zones.crs)

    df_buildings["area"] = df_buildings.area
    df_buildings["weight"] = df_buildings["area"]
    df_buildings["building_id"] = np.arange(len(df_buildings))
    df_buildings["floors"] = DEFAULT_FLOORS
    df_buildings["building"] = df_buildings["type"]
    df_buildings["amenity"] = df_buildings["type"]
    df_buildings["location_type"] = df_buildings["type"].map(TYPE_LOCATION_MAP)

    df_buildings["geometry"] = df_buildings.centroid

    # Impute spatial identifiers
    df_buildings = gpd.sjoin(
        df_buildings, df_zones[["geometry", "commune_id", "iris_id"]],
        how = "left", predicate = "within"
    ).reset_index(drop = True).drop(columns = ["index_right"])

    df_buildings = df_buildings.dropna(subset = ["commune_id", "iris_id"])

    return df_buildings[[
        "building_id", "area", "weight", "commune_id", "iris_id", "geometry",
        "location_type", "floors", "building", "amenity",
    ]]

def validate(context):
    PATH = "{}/{}".format(context.config("data_path"), context.config("dhaka.buildings_path"))
    if not os.path.exists(PATH):
        raise RuntimeError(f"Dhaka buildings data is not available at {PATH}")

    return os.path.getsize(PATH)
