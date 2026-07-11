import numpy as np
import pandas as pd
import geopandas as gpd
from sklearn.neighbors import KDTree
import data.spatial.utils as spatial_utils
import matplotlib.pyplot as plt

def configure(context):
    context.stage("synthesis.population.trips")
    context.stage("synthesis.population.enriched")
    context.stage("synthesis.population.spatial.home.locations")
    context.stage("synthesis.population.spatial.primary.distance_distributions")
    context.stage("synthesis.locations.education")

    context.config("random_seed")

    context.config("missing_trips_for_young_people")


def prepare_education_persons(context):
    df_persons = context.stage("synthesis.population.enriched")
    df_trips = context.stage("synthesis.population.trips")
    
    # Find persons with education trips
    df_trips_education = df_trips[df_trips["following_purpose"] == "education"].copy()
    df_education_persons = df_persons[df_persons["person_id"].isin(df_trips_education["person_id"].unique())].copy()
    
    return df_education_persons


def prepare_education_destinations(context):
    df_edu_candidates = context.stage("synthesis.locations.education")
    
    # Filter out fake locations (they should only be used as last resort)
    # We'll handle missing facilities with fallback logic
    df_edu_candidates = df_edu_candidates[~df_edu_candidates["fake"]].copy()
    
    # Extract coordinates from geometry
    df_edu_candidates["destination_x"] = df_edu_candidates["geometry"].x
    df_edu_candidates["destination_y"] = df_edu_candidates["geometry"].y
    
    return df_edu_candidates


def prepare_radius_from_cdf(context, df_education_persons):
    distributions = context.stage("synthesis.population.spatial.primary.distance_distributions")  
    cdf = distributions["education"]["cdf"]
    midpoint_bins = distributions["education"]["midpoint_bins"]
    random_values = np.random.rand(len(df_education_persons))
    value_bins = np.searchsorted(cdf, random_values)
    radius = midpoint_bins[value_bins]

    return radius, distributions


def impute_education_locations_radius(context):
    df_education_persons = prepare_education_persons(context)
    
    df_home = context.stage("synthesis.population.spatial.home.locations")
    df_education_persons = pd.merge(
        df_education_persons, 
        df_home[["household_id", "geometry"]].rename(columns={"geometry": "home_geometry"}),
        on="household_id"
    )
    
    # Extract home coordinates from geometry
    # home_geometry is a GeoSeries, we need to extract x and y from each Point
    df_education_persons["home_x"] = df_education_persons["home_geometry"].apply(lambda geom: geom.x)
    df_education_persons["home_y"] = df_education_persons["home_geometry"].apply(lambda geom: geom.y)
    
    # Get education candidates
    df_edu_candidates = prepare_education_destinations(context)
    
    # Prepare the distances used for sampling based on the CDF (this is the radius variable)
    radius, distributions = prepare_radius_from_cdf(context, df_education_persons)
    
    # Create a threshold for donut shape selection
    threshold = distributions["education"]["threshold_buffer"]  # in meters
    
    radius = radius + np.array(threshold)

    
    # Group destinations into age categories
    age_bounds = [(-np.inf, 5), (6, 16), (17, np.inf)]

    education_types = [["kindergarten"], ["school"], ["university"]]
    query_sizes = [1, 1, 1] #[5, 5, 5]
    
    # Initialize result columns
    df_education_persons["education_x"] = np.nan
    df_education_persons["education_y"] = np.nan
    df_education_persons["commune_id_edu"] = None
    df_education_persons["location_id"] = None
    df_education_persons["geometry"] = None
    
    no_fac_count = 0
    
    # Process each age group
    for (lower_bound, upper_bound), types, query_size in zip(age_bounds, education_types, query_sizes):
        print()
        print(f"[INFO] synthesis/population/location/primary/education.py: \n {((lower_bound, upper_bound), types, query_size)}")
        # TODO: TEMP FIX: ignore all education assignment for ages < 20
        if context.config("missing_trips_for_young_people") == True and lower_bound < 6:
            continue

        f_persons = (df_education_persons["age"] >= lower_bound) & (df_education_persons["age"] <= upper_bound)        
        df_candidates = df_edu_candidates[df_edu_candidates["education_type"].isin(types)].copy()
        education_coordinates = np.vstack([df_candidates["destination_x"], df_candidates["destination_y"]]).T
        home_coordinates = np.vstack([
            df_education_persons.loc[f_persons, "home_x"], 
            df_education_persons.loc[f_persons, "home_y"]
        ]).T
        
        tree = KDTree(education_coordinates)
        
        # Sample distances and find candidates within radius
        indices, distances = tree.query_radius(
            home_coordinates, 
            r=radius[f_persons], 
            return_distance=True, 
            sort_results=True
        )
        
        chosen_indices = []
        
        for i, (ind, dist) in enumerate(zip(indices, distances)):
            # When no facility is found within radius
            if len(ind) < query_size:
                no_fac_count += 1
                new_dist, new_ind = tree.query(
                    np.array(home_coordinates[i]).reshape(1, -1), 
                    query_size, 
                    return_distance=True,
                    sort_results=True
                )
                ind = new_ind[0]
                dist = new_dist[0]
            
            # If enough facilities, apply donut selection
            elif len(ind) >= query_size:
                farthest_dist = dist[-1]
                min_threshold_band = farthest_dist - threshold
                minimum_selection_bound = max(min_threshold_band, dist[0])
                maximum_selection_bound = farthest_dist
                donut_ind = ind[(dist >= minimum_selection_bound) & (dist <= maximum_selection_bound)]
                
                # grow the donut until we have enough candidates
                growth_factor = 1.5
                while len(donut_ind) < query_size and minimum_selection_bound > dist[0]:
                    donut_width = maximum_selection_bound - minimum_selection_bound
                    minimum_selection_bound = max(minimum_selection_bound - donut_width * growth_factor, dist[0])
                    maximum_selection_bound = min(maximum_selection_bound + donut_width * growth_factor, dist[-1])
                    donut_ind = ind[(dist >= minimum_selection_bound) & (dist <= maximum_selection_bound)]
                
                ind = donut_ind
            
            # Select facility using weight
            weights = df_candidates.iloc[ind]["weight"].values
            weights = weights / np.sum(weights)
            
            ind_current = np.random.choice(ind, p=weights)
            chosen_indices.append(ind_current)
        
        print(f"INFO: Imputing education locations for age range ({lower_bound}, {upper_bound}]...")
        print(f"INFO: % of persons with education facilities not found: {100 * no_fac_count / len(indices):.2f}%")
        
        # Assign education locations - use actual geometry from candidates to preserve CRS
        df_education_persons.loc[f_persons, "commune_id_edu"] = df_candidates.iloc[chosen_indices]["commune_id"].values
        df_education_persons.loc[f_persons, "location_id"] = df_candidates.iloc[chosen_indices]["location_id"].values
        df_education_persons.loc[f_persons, "geometry"] = df_candidates.iloc[chosen_indices]["geometry"].values
        
        # Store coordinates for distance calculation
        df_education_persons.loc[f_persons, "education_x"] = df_candidates.iloc[chosen_indices]["destination_x"].values
        df_education_persons.loc[f_persons, "education_y"] = df_candidates.iloc[chosen_indices]["destination_y"].values
    
    # Calculate actual distances for validation
    df_education_persons["distance"] = np.sqrt(
        (df_education_persons["home_x"] - df_education_persons["education_x"]) ** 2 +
        (df_education_persons["home_y"] - df_education_persons["education_y"]) ** 2
    )
    
    print(f"INFO: Education distance statistics:")
    print(df_education_persons["distance"].describe())
    
    # Verify all persons have been assigned
    n_missing = df_education_persons["geometry"].isna().sum()
    print(f"total education count people{ len(df_education_persons['geometry'])}")
    if n_missing > 0:
        print(f"ERROR: {n_missing} persons were not assigned education locations!")
        raise ValueError(f"{n_missing} persons missing education location assignments")
    
    # Prepare output - create GeoDataFrame with the original CRS from candidates
    df_result = gpd.GeoDataFrame(
        df_education_persons[["person_id", "commune_id_edu", "location_id", "geometry"]].rename(
            columns={"commune_id_edu": "commune_id"}
        ),
        geometry="geometry",
        crs=df_edu_candidates.crs
    )
    
    return df_result


def execute(context):
    np.random.seed(context.config("random_seed"))
    
    df_education_locations = impute_education_locations_radius(context)
    
    return df_education_locations
