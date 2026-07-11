import numpy as np
import pandas as pd
import numpy as np
import pandas as pd
import geopandas as gpd
from sklearn.neighbors import KDTree
import data.spatial.utils as spatial_utils
import matplotlib.pyplot as plt
from pyproj import Geod
from pyproj import Transformer, Geod
from scipy.spatial import cKDTree
from shapely import Point

"""
This stage removes adult persons that were added in add_persons stage and adds trips to childrens added in add_persons stage.

Adult persons from add_persons stage are removed, because we do not have any trip information about them, but we have adults
from the original HTS dataset with trip information and those are kept.

Original HTS does not contain any trip information about children with age < 15. As mentioned, these were added in add_persons
and this stage generates education trips to and from to closest kindergarten/school of their home location.
"""


def configure(context):
    context.stage("seville.data.hts.entd.household_members.set_attributes")
    context.stage("seville.locations.education")


def get_home_locations(df_persons, df_trips):
    df_home_ori = df_trips.loc[df_trips['preceding_purpose'] == 'home', ['person_id', 'origin_location']]
    df_home_ori = df_home_ori.rename(columns={"origin_location":"home_geometry"})   

    df_home_dst = df_trips.loc[df_trips['following_purpose'] == 'home', ['person_id', 'destination_location']]
    df_home_dst = df_home_dst.rename(columns={"destination_location":"home_geometry"})  

    df_homes = pd.concat([df_home_ori, df_home_dst]).drop_duplicates('person_id')
    df_homes = pd.merge(df_homes, df_persons[['person_id', 'household_id']], on='person_id')
    df_homes = df_homes.drop_duplicates('household_id')

    return df_homes[['household_id', 'home_geometry']]



def impute_education_trips(df_young_persons, df_home_locations ,df_edu_locations, random):


    df = df_young_persons.copy()
    df = pd.merge(df, df_home_locations, on='household_id', how='left')
    df.dropna(subset=['home_geometry'], inplace=True)

    print(df[~df["home_geometry"].apply(lambda x: isinstance(x, Point))])

    # Extract home coordinates
    df["home_x"] = df["home_geometry"].apply(lambda p: p.x)
    df["home_y"] = df["home_geometry"].apply(lambda p: p.y)


    # Extract education coordinates
    df_edu = df_edu_locations.copy()
    df_edu["edu_x"] = df_edu["geometry"].apply(lambda p: p.x)
    df_edu["edu_y"] = df_edu["geometry"].apply(lambda p: p.y)

    print(df_edu.value_counts(subset='education_type'))
    print(df_edu.info())
    print(df.info())

    # Output columns
    df["edu_geometry"] = None
    df["euclidean_distance"] = np.nan

    age_bounds = [(-np.inf, 5), (6, 16),]
    education_types = [["kindergarten"], ["school"],]

    geod = Geod(ellps="WGS84")

    transformer = Transformer.from_crs(
        "EPSG:25830",
        "EPSG:4326",
        always_xy=True
    )
    df_edu["edu_x"], df_edu["edu_y"] = transformer.transform(
        df_edu["edu_x"].values,
        df_edu["edu_y"].values
    )



    for (lower, upper), types in zip(age_bounds, education_types):
        print(f"{(lower, upper, types)}")

        f_persons = (df["age"] >= lower) & (df["age"] <= upper)
        df_candidates = df_edu[df_edu["education_type"].isin(types)]

        edu_coords = np.column_stack((df_candidates["edu_x"], df_candidates["edu_y"]))
        home_coords = np.column_stack((df.loc[f_persons, "home_x"], df.loc[f_persons, "home_y"]))

        tree = cKDTree(edu_coords)
        _, indices = tree.query(home_coords, k=1)
        nearest = df_candidates.iloc[indices].reset_index(drop=True)

        df.loc[f_persons, "edu_geometry"] = nearest['geometry'].values

        print(df[["home_x","home_y"]].head())
        print(nearest[["edu_x","edu_y"]].head())
        print(df[["home_x","home_y"]].dtypes)

        # VECTORIZED geodesic distance
        lon1 = df.loc[f_persons, "home_x"].values
        lat1 = df.loc[f_persons, "home_y"].values
        edu_lon = nearest["edu_x"].values
        edu_lat = nearest["edu_y"].values

        _, _, dist_m = geod.inv(lon1, lat1, edu_lon, edu_lat)
        df.loc[f_persons, "euclidean_distance"] = dist_m



    df_young_trips = df[['person_id', 'trip_weight', 'euclidean_distance']].copy()
    df_young_trips["mode"] = 'pt'
    df_young_trips["origin_departement_id"] = "41"
    df_young_trips["destination_departement_id"] = "41"

    # 1.3 is routed distance detour factor
    # we assume speed of 15 kms^-1
    # trip duration is in seconds
    speed_ms = 15 * 3.6
    df_young_trips['trip_duration'] = df_young_trips['euclidean_distance'] * 1.3 / speed_ms  # in seconds
    
    df_young_trips['education_start'] = 8*3600 + random.randint(0, 3600, size=len(df_young_trips))
    df_young_trips['education_end'] = 14*3600 + random.randint(0, 3600, size=len(df_young_trips))
    df_young_trips['education_duration'] = df_young_trips['education_end'] - df_young_trips['education_start']

    df_to_school_trips = df_young_trips.copy()
    df_to_school_trips["trip_id"] = df_to_school_trips['person_id'].astype(str) + '_1'
    df_to_school_trips['arrival_time'] = df_to_school_trips['education_start']
    df_to_school_trips['activity_duration'] = df_to_school_trips['education_duration']
    df_to_school_trips['departure_time'] = df_to_school_trips['arrival_time'] - df_to_school_trips['trip_duration']
    df_to_school_trips['following_purpose'] = "education"
    df_to_school_trips['preceding_purpose'] = "home"
    df_to_school_trips['is_first_trip'] = True
    df_to_school_trips['is_last_trip'] = False

    df_from_school_trips = df_young_trips.copy()
    df_from_school_trips["trip_id"] = df_from_school_trips['person_id'].astype(str) + '_2'
    df_from_school_trips['departure_time'] = df_from_school_trips['education_end']
    df_from_school_trips['arrival_time'] = df_from_school_trips['departure_time'] + df_from_school_trips['trip_duration']
    df_from_school_trips['activity_duration'] = np.nan
    df_from_school_trips['following_purpose'] = "home"
    df_from_school_trips['preceding_purpose'] = "education"
    df_from_school_trips['is_first_trip'] = False
    df_from_school_trips['is_last_trip'] = True

    print(df_young_trips['euclidean_distance'].describe())
    print(len(df_young_trips[df_young_trips['euclidean_distance'] > 5000]))
    print(df_young_trips.loc[df_young_trips['euclidean_distance'] > 5000, 'euclidean_distance'])

    df_young_trips = pd.concat([df_to_school_trips, df_from_school_trips])
    df_young_trips = df_young_trips.sort_values(by = ["person_id", "trip_id"])
    
    df_young_trips.drop(columns=['education_start', 'education_end', 'education_duration'])

    return df_young_trips


def execute(context):
    df_households, df_persons, df_trips = context.stage("seville.data.hts.entd.household_members.set_attributes")
    df_edu_locations = context.stage("seville.locations.education")

    random = np.random.RandomState(context.config("random_seed"))
    
    # Get home locations of households
    df_homes = get_home_locations(df_persons, df_trips) 

    
    filter_age_5_15 = (df_persons['age'] >= 5) & (df_persons['age'] <= 15)
    df_young_trips = impute_education_trips(df_persons[filter_age_5_15], df_homes, df_edu_locations, random)
    df_trips = pd.concat([df_trips, df_young_trips])    
    
    df_persons.loc[df_persons['age'] < 5, 'number_of_trips'] = 0    
    df_persons.loc[df_persons['person_id'].isin(df_young_trips['person_id']), 'number_of_trips'] = 2


    return df_households, df_persons, df_trips
