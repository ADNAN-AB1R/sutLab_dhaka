import numpy as np
import pandas as pd

"""
Yield home location candidates for Dhaka: buildings with no specific
non-residential `location_type` (untyped, or explicitly residential-like),
filtered to a plausible residential footprint size - same 40-400m2 heuristic
Seville uses, since our buildings source is likewise mostly untyped.
"""

MIN_AREA = 40
MAX_AREA = 400

def configure(context):
    context.stage("dhaka.data.buildings")
    context.stage("dhaka.data.spatial.iris")

def execute(context):
    df = context.stage("dhaka.data.buildings")

    df = df[df["location_type"].isna()].copy()
    df = df[(df["weight"] >= MIN_AREA) & (df["weight"] < MAX_AREA)].copy()
    df = df.rename(columns = { "building_id": "home_location_id" })

    df = df[["home_location_id", "weight", "commune_id", "iris_id", "geometry"]]

    # Fill wards with no eligible home candidate with a synthetic centroid location
    df_zones = context.stage("dhaka.data.spatial.iris")
    required_zones = set(df_zones["commune_id"].unique())
    available_zones = set(df["commune_id"].unique())
    missing_zones = required_zones - available_zones

    if len(missing_zones) > 0:
        print("Adding {} centroids as home locations for missing wards".format(len(missing_zones)))
        df_missing = df_zones[df_zones["commune_id"].isin(missing_zones)][["commune_id", "iris_id", "geometry"]].copy()
        df_missing["geometry"] = df_missing["geometry"].centroid
        df_missing["home_location_id"] = np.arange(len(df_missing)) + df["home_location_id"].max() + 1
        df_missing["weight"] = 1.0

        df = pd.concat([df, df_missing[["home_location_id", "weight", "commune_id", "iris_id", "geometry"]]])

    return df
