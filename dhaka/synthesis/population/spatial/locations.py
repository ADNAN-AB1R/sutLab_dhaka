import pandas as pd
import geopandas as gpd
import numpy as np

def configure(context):
    context.stage("synthesis.population.spatial.home.locations")
    context.stage("synthesis.population.spatial.primary.locations")
    context.stage("synthesis.population.spatial.secondary.locations")

    context.stage("synthesis.population.activities")
    context.stage("synthesis.population.sampled")
    context.stage("data.spatial.iris")


def patch_missing_primary_locations(
    df_locations,
    df_primary,
    purpose,
    context,
    mock_location_id
):
    """
    Patch missing primary locations (work / education) by falling back to first non-missing location.
    """

    df_result = df_locations[df_locations["purpose"] == purpose].copy()

    # LEFT merge so missing locations are preserved
    df_result = pd.merge(
        df_result,
        df_primary[["person_id", "location_id", "geometry"]],
        on="person_id",
        how="left"
    )

    missing = df_result["geometry"].isna()
    n_missing = missing.sum()

    if n_missing > 0:
        print(f"INFO: Mocking {n_missing} missing {purpose} locations")


        fallback_geometry = df_result[~missing]['geometry'][0]
        fallback_location_id = df_result[~missing]['location_id'][0]

        df_result.loc[missing, "geometry"] = fallback_geometry
        df_result.loc[missing, "location_id"] = fallback_location_id

    return df_result[["person_id", "activity_index", "location_id", "geometry"]]



def execute(context):
    df_home = context.stage("synthesis.population.spatial.home.locations")
    df_work, df_education = context.stage("synthesis.population.spatial.primary.locations")
    df_secondary = context.stage("synthesis.population.spatial.secondary.locations")[0]

    df_persons = context.stage("synthesis.population.sampled")[["person_id", "household_id"]]
    df_locations = context.stage("synthesis.population.activities")[["person_id", "activity_index", "purpose"]]

    # Home locations
    df_home_locations = df_locations[df_locations["purpose"] == "home"]
    df_home_locations = pd.merge(df_home_locations, df_persons, on = "person_id")
    df_home_locations = pd.merge(df_home_locations, df_home[["household_id", "geometry"]], on = "household_id")
    df_home_locations["location_id"] = -1
    df_home_locations = df_home_locations[["person_id", "activity_index", "location_id", "geometry"]]

    # Work locations
    df_work_locations = df_locations[df_locations["purpose"] == "work"]
    df_work_locations = pd.merge(df_work_locations, df_work[["person_id", "location_id", "geometry"]], on = "person_id")
    df_work_locations = df_work_locations[["person_id", "activity_index", "location_id", "geometry"]]
    assert not df_work_locations["geometry"].isna().any()

    # Education locations
    df_education_locations = df_locations[df_locations["purpose"] == "education"]
    df_education_locations = pd.merge(df_education_locations, df_education[["person_id", "location_id", "geometry"]], on = "person_id")
    df_education_locations = df_education_locations[["person_id", "activity_index", "location_id", "geometry"]]
    assert not df_education_locations["geometry"].isna().any()

    # Secondary locations
    df_secondary_locations = df_locations[~df_locations["purpose"].isin(("home", "work", "education"))].copy()
    df_secondary_locations = pd.merge(df_secondary_locations, df_secondary[[
        "person_id", "activity_index", "location_id", "geometry"
    ]], on = ["person_id", "activity_index"], how = "left")
    df_secondary_locations = df_secondary_locations[["person_id", "activity_index", "location_id", "geometry"]]
    assert not df_secondary_locations["geometry"].isna().any()

    # Validation
    initial_count = len(df_locations)
    


    print(f"df_home_locations {len(df_locations[df_locations['purpose'] == 'home'])} - {len(df_home_locations)}")
    print(f"df_work_locations {len(df_locations[df_locations['purpose'] == 'work'])} - {len(df_work_locations)}")
    print(f"df_education_locations {len(df_locations[df_locations['purpose'] == 'education'])} - {len(df_education_locations)}")
    print(f"df_secondary_locations {len(df_locations[~df_locations['purpose'].isin(('home', 'work', 'education'))])} - {len(df_secondary_locations)}")

    # ========================================
    # TODO: temporary fix
    # MOCK MISSING LOCATIONS USING HOME LOCATION

    # Work locations
    df_work_locations = patch_missing_primary_locations(
        df_locations=df_locations,
        df_primary=df_work,
        purpose="work",
        context=context,
        mock_location_id=-9999
    )

    # Education locations
    df_education_locations = patch_missing_primary_locations(
        df_locations=df_locations,
        df_primary=df_education,
        purpose="education",
        context=context,
        mock_location_id=-9998
    )
    # ========================================


    df_locations = pd.concat([df_home_locations, df_work_locations, df_education_locations, df_secondary_locations])

    df_locations = df_locations.sort_values(by = ["person_id", "activity_index"])
    final_count = len(df_locations)
    assert initial_count == final_count, f"{initial_count} == {final_count}"

    assert not df_locations["geometry"].isna().any()
    df_locations = gpd.GeoDataFrame(df_locations, crs = df_home.crs)

    # add municipalities
    df_iris = context.stage("data.spatial.iris")
    df_iris = gpd.GeoDataFrame(df_iris, crs = df_home.crs)

    df_locations = gpd.sjoin(df_locations,df_iris,how="left")

    return df_locations
