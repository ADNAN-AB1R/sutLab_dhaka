import os
import geopandas as gpd
import pandas as pd

"""
This stage loads the Dhaka ward shapefile, which is used as the finest zoning unit
(the equivalent of Seville's census sections). This shapefile (ward_dhk_75.shp)
covers DNCC + DSCC (current, post-2020 ward boundaries and numbering, matching
the DTCA household travel survey's own ward numbering, e.g. "DSCC Ward 01") plus
several surrounding upazilas with no ward-level subdivision in this file.

Study area = DNCC + DSCC (full ward detail) + Savar + Keraniganj (single zone
each - the only two surrounding upazilas with real HTS survey coverage). See
dhaka/wards.py for the shared commune_id scheme ("S07"/"N54"/"SAVAR"/...).

The city-corporation wards are split into several polygons sharing the same
ward number ("Ward No.") - these are dissolved into a single polygon per ward
so that downstream stages can rely on one row per `commune_id`.
"""

EXTRA_UPAZILAS = ["Savar", "Keraniganj"]

def configure(context):
    context.config("data_path")
    context.config("dhaka.ward_shp", "spatial/ward_dhk_75.shp")
    context.config("dhaka.ward_number_field", "Ward No.")
    context.config("dhaka.ward_cc_field", "CC")
    context.config("dhaka.ward_upazila_field", "ADM3_EN")

def execute(context):
    SHP_FILE = f"{context.config('data_path')}/{context.config('dhaka.ward_shp')}"
    number_field = context.config("dhaka.ward_number_field")
    cc_field = context.config("dhaka.ward_cc_field")
    upazila_field = context.config("dhaka.ward_upazila_field")

    gdf = gpd.read_file(SHP_FILE)[[number_field, cc_field, upazila_field, "geometry"]]

    # Some polygons have invalid rings (see pyogrio warning on read) which
    # breaks the dissolve() below with a GEOS TopologyException - repair first
    gdf["geometry"] = gdf["geometry"].make_valid()

    # City-corporation wards: S01..S75 (DSCC), N01..N54 + N98 (DNCC)
    gdf_wards = gdf[gdf[cc_field].isin(["S", "N"])].copy()
    gdf_wards = gdf_wards.dropna(subset = [number_field])
    gdf_wards["commune_id"] = gdf_wards[cc_field] + gdf_wards[number_field].astype(int).astype(str).str.zfill(2)
    gdf_wards = gdf_wards.dissolve(by = "commune_id", as_index = False)[["commune_id", "geometry"]]

    # Surrounding upazilas with no ward-level subdivision: one zone each
    gdf_upazilas = gdf[gdf[upazila_field].isin(EXTRA_UPAZILAS)].copy()
    gdf_upazilas["commune_id"] = gdf_upazilas[upazila_field].str.upper()
    gdf_upazilas = gdf_upazilas.dissolve(by = "commune_id", as_index = False)[["commune_id", "geometry"]]

    gdf_zones = pd.concat([gdf_wards, gdf_upazilas], ignore_index = True)
    return gpd.GeoDataFrame(gdf_zones, geometry = "geometry", crs = gdf.crs)

def validate(context):
    SHP_FILE = f"{context.config('data_path')}/{context.config('dhaka.ward_shp')}"
    if not os.path.exists(SHP_FILE):
        raise RuntimeError(f"Dhaka ward shapefile is not available at location {SHP_FILE}")

    return os.path.getsize(SHP_FILE)



