from tqdm import tqdm
import pandas as pd
import geopandas as gpd
import numpy as np
import data.hts.hts as hts


import shapely.geometry as geo


def configure(context):
    context.stage("seville.data.hts.entd.trip_distance")

def execute(context):
    df_households, df_persons, df_trips, df_household_members = context.stage("seville.data.hts.entd.trip_distance")
    

    # ----------------------------------------------------------------------- #
    # Generate LEGS

    df_legs = pd.wide_to_long(
        df_trips[['person_id', 'trip_id', 'trip_sequence', 'mode_part1', 'mode_part2', 'mode_part3', 'mode_part4']],
        stubnames=["mode_part"],
        i=["trip_id"],
        j="leg_sequence",
        sep="",
        suffix="\d+"
    ).reset_index()



    # ----------------------------------------------------------------------- #
    # Fix IDs

    # Assign IDs to households
    df_households = df_households.sort_values(by = "household_id")
    df_households["new_household_id"] = np.arange(len(df_households))
    
    # Assign IDs to persons
    df_persons = pd.merge(df_persons, df_households[["household_id", "new_household_id"]], on = "household_id")
    df_persons = df_persons.sort_values(by = ["household_id", "person_id"])
    df_persons["new_person_id"] = np.arange(len(df_persons))
    
    # Assign IDs to trips
    df_trips = pd.merge(df_trips, df_persons[["household_id", "person_id", "new_person_id", "new_household_id"]], on = ["person_id"])
    df_trips = df_trips.sort_values(by = ["household_id", "person_id", "trip_sequence"])
    df_trips["new_trip_id"] = np.arange(len(df_trips))
    df_trips["trip_sequence"] = df_trips["trip_sequence"].astype(int)
    
    # Assign IDs to legs
    df_legs = pd.merge(df_legs, df_trips[["household_id", "person_id", "trip_id", "new_person_id", "new_household_id", "new_trip_id"]], on = ["person_id", "trip_id"])
    df_legs = df_legs.sort_values(by = ["household_id", "person_id", "trip_sequence", "leg_sequence"])
    df_legs["new_leg_id"] = np.arange(len(df_legs))
    df_legs["trip_sequence"] = df_legs["trip_sequence"].astype(int)
    df_legs["leg_sequence"] = df_legs["leg_sequence"].astype(int)
    
    def replace_id_columns(df):
        NEW_COLUMNS = ['new_household_id', 'new_person_id', 'new_trip_id', 'new_leg_id']
        for column in NEW_COLUMNS:
            if column in df.columns:
                df[column[4:]] = df[column]
                df = df.drop(columns=[column])
        return df
    
    df_households = replace_id_columns(df_households)
    df_persons = replace_id_columns(df_persons)
    df_trips = replace_id_columns(df_trips)
    df_legs = replace_id_columns(df_legs)

    # ----------------------------------------------------------------------- #
    # HOUSEHOLDS

    # number_of_vehicles
    # number_of_bikes

    # we only have income per single person, not per the whole household
    # INCOME_CLASS_BOUNDS_SEVILLE = [1000, 1500, 2000, 3000, 4000, 5000, 1e6]

    df_households = df_households[[
        "household_id", "number_of_vehicles", 
        "number_of_bikes",]]

    # ----------------------------------------------------------------------- #
    # PERSONS

    DEPARTEMENT_ID = "41"

    df_persons["weight"] = df_persons["person_weight"]
    df_persons["home_departement_id"] = DEPARTEMENT_ID
    df_persons["has_driving_permit"] = df_persons["has_license"]
    df_persons["has_pt_subscription"] = np.nan # we dont know
    df_persons["age"] = df_persons["age"].astype(int)

    df_persons = df_persons[[
        "household_id", "person_id", "has_driving_permit", "has_pt_subscription", "age", 
        "weight"]]

    # ----------------------------------------------------------------------- #
    # TRIPS
    df_trips["origin_departement_id"] = DEPARTEMENT_ID
    df_trips["destination_departement_id"] = DEPARTEMENT_ID

    df_trips["origin_municipality_id"] = df_trips["municipality_code_ori"].astype(str)
    df_trips["destination_municipality_id"] = df_trips["municipality_code_des"].astype(str)

    df_trips["travel_time"] = df_trips["trip_duration"]
    df_trips["origin_activity_type"] = df_trips["preceding_purpose"]
    df_trips["destination_activity_type"] = df_trips["following_purpose"]

    df_trips = df_trips[[
        "household_id", "person_id", "trip_id", "trip_sequence", "mode", "euclidean_distance",
        "origin_municipality_id", "destination_municipality_id", "travel_time", "departure_time",
        "origin_activity_type", "destination_activity_type", "origin_location", "destination_location"
    ]]      

    # ----------------------------------------------------------------------- #
    # LEGS



    


    df_legs = df_legs.merge(df_trips[['trip_id', 'mode']], on='trip_id')
    df_legs['transit_mode'] = df_legs['mode']
    df_legs['mode'] = df_legs['mode_part']

    MODES_MAP = {
        1: "walk",
        2: "pt",
        3: "bike",
        4: "car",
        5: "car_passenger",
        6: "pt" # Other assume pt
    }


    df_legs['mode'] = df_legs['mode'].map(MODES_MAP)

    df_legs = df_legs[["household_id", "person_id", "trip_id", "leg_id", "trip_sequence", "leg_sequence",
    "mode", "transit_mode"]]

    # ----------------------------------------------------------------------- #
    # Checking for NaN

    df_trips["is_valid"] = True

    for column in df_trips.columns:
        f = df_trips[column].isna()
        df_trips.loc[f, "is_valid"] = False

        if np.count_nonzero(f) > 0:
            print(column, np.count_nonzero(f))

     

    # ------------------------------------------------------------------------ #
    # Filter out trips without valid geometry

    invalid = df_trips['origin_location'].isna() | df_trips['destination_location'].isna()
    df_invalid_trips_households = df_trips.loc[invalid, 'household_id']

    def trip_filter(df, df_invalid_trips_households):
        return df[~(df['household_id'].isin(df_invalid_trips_households))]

    df_households = trip_filter(df_households, df_invalid_trips_households)
    df_persons = trip_filter(df_persons, df_invalid_trips_households)
    df_trips = trip_filter(df_trips, df_invalid_trips_households)
    df_legs = trip_filter(df_legs, df_invalid_trips_households)

    # ----------------------------------------------------------------------- #
    # Convert points to lines

    df_trips["geometry"] = [
        geo.LineString([origin, destination])
        for origin, destination in zip(df_trips["origin_location"], df_trips["destination_location"])
    ]

    CRS = "EPSG:4326"
    df_trips = gpd.GeoDataFrame(df_trips, crs = CRS, geometry = "geometry")
    df_trips = df_trips.to_crs("EPSG:2154")



    return df_households, df_persons, df_trips, df_legs
