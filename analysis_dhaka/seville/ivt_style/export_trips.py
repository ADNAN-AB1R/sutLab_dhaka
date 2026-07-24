import numpy as np

"""
This stage exports synthetic population from IPU.
"""
import pandas as pd
import geopandas as gpd
from shapely.geometry import LineString


def configure(context):
    context.stage("seville.data.hts.entd.trip_distance")
    context.config("analysis_path")


def execute(context):
    df_households, df_persons, df_trips, df_household_members = context.stage("seville.data.hts.entd.trip_distance")

    
    # Example dataframe
    df = df_trips.copy()

    valid_geometry = df["origin_location"].notna() & df["destination_location"].notna()
    # Create LineString from existing Point geometries
    df.loc[valid_geometry, "geometry"] = df[valid_geometry].apply(
        lambda row: LineString([
            row["origin_location"],
            row["destination_location"]
        ]),
        axis=1
    )

    # Convert to GeoDataFrame
    gdf = gpd.GeoDataFrame(df, geometry="geometry", crs="EPSG:4326")

    gdf.to_file(f"{context.config('analysis_path')}/trips.gpkg")


    gdf.head()
