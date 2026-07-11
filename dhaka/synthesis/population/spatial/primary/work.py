import numpy as np
import pandas as pd
import geopandas as gpd
from sklearn.neighbors import KDTree
import data.spatial.utils as spatial_utils

def configure(context):
    context.stage("synthesis.population.trips")
    context.stage("synthesis.population.enriched")
    context.stage("synthesis.population.spatial.home.locations")
    context.stage("synthesis.population.spatial.primary.distance_distributions")
    context.stage("synthesis.locations.work")

    context.config("random_seed")



def prepare_work_persons(context):
    df_persons = context.stage("synthesis.population.enriched")
    df_trips = context.stage("synthesis.population.trips")
    
    # Find persons with work trips
    df_trips_work = df_trips[(df_trips["following_purpose"] == "work") | (df_trips["preceding_purpose"] == "work")].copy()
    df_work_persons = df_persons[df_persons["person_id"].isin(df_trips_work["person_id"].unique())].copy()
    
    return df_work_persons


def prepare_work_destinations(context):
    df_work_candidates = context.stage("synthesis.locations.work")
    
    # Extract coordinates from geometry
    df_work_candidates["destination_x"] = df_work_candidates["geometry"].x
    df_work_candidates["destination_y"] = df_work_candidates["geometry"].y
    
    return df_work_candidates


def prepare_radius_from_cdf(context, df_work_persons):
    distributions = context.stage("synthesis.population.spatial.primary.distance_distributions")
    
    cdf = distributions["work"]["cdf"]
    midpoint_bins = distributions["work"]["midpoint_bins"]
    random_values = np.random.rand(len(df_work_persons))
    value_bins = np.searchsorted(cdf, random_values)
    radius = midpoint_bins[value_bins]

    return radius, distributions


def impute_work_locations_radius(context):
    df_work_persons = prepare_work_persons(context)
    
    df_home = context.stage("synthesis.population.spatial.home.locations")
    df_work_persons = pd.merge(
        df_work_persons, 
        df_home[["household_id", "geometry"]].rename(columns={"geometry": "home_geometry"}),
        on="household_id"
    )
    
    # Extract home coordinates from geometry
    # home_geometry is a GeoSeries, we need to extract x and y from each Point
    df_work_persons["home_x"] = df_work_persons["home_geometry"].apply(lambda geom: geom.x)
    df_work_persons["home_y"] = df_work_persons["home_geometry"].apply(lambda geom: geom.y)
    home_coordinates = np.vstack([df_work_persons["home_x"], df_work_persons["home_y"]]).T
    
    # Get work candidates
    df_work_candidates = prepare_work_destinations(context)
    
    # Prepare the distances used for sampling based on the CDF (this is the radius variable)
    radius, distributions = prepare_radius_from_cdf(context, df_work_persons)
    
    # Create a threshold for donut shape selection
    threshold = distributions["work"]["threshold_buffer"]
    radius = radius + np.array(threshold)

    
    query_size = 3
    no_fac_count = 0
    
    # Build KDTree for work locations
    work_coordinates = np.vstack([df_work_candidates["destination_x"], df_work_candidates["destination_y"]]).T
    tree = KDTree(work_coordinates)
    
    # Sample distances and find candidates within radius
    search_radius = radius * 1.2  # try 1.2
    indices, distances = tree.query_radius(home_coordinates, r=search_radius, return_distance=True, sort_results=True)
    
    chosen_indices = []
    
    for i, (ind, dist) in enumerate(zip(indices, distances)):
        # If not enough facilities found, use nearest neighbors
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
            # print(i, ind, dist)
        
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
        

        # Select facility using number of employees as weight
        weights = df_work_candidates.iloc[ind]["employees"].values
        if np.sum(weights) == 0:    
            weights = np.ones(len(weights))

        # print(f"indices: {ind}, weights: {weights}")
        weights = weights / np.sum(weights)

        ind_current = np.random.choice(ind, p=weights)
        chosen_indices.append(ind_current)
    
    print(f"INFO: Imputing work locations...")
    print(f"INFO: Number of trips without finding initial facility: {no_fac_count}")
    
    # Assign work locations - use the actual geometry from candidates to preserve CRS
    df_work_persons["commune_id"] = df_work_candidates.iloc[chosen_indices]["commune_id"].values
    df_work_persons["location_id"] = df_work_candidates.iloc[chosen_indices]["location_id"].values
    df_work_persons["geometry"] = df_work_candidates.iloc[chosen_indices]["geometry"].values
    
    # Calculate actual distances for validation
    df_work_persons["work_x"] = df_work_candidates.iloc[chosen_indices]["destination_x"].values
    df_work_persons["work_y"] = df_work_candidates.iloc[chosen_indices]["destination_y"].values
    df_work_persons["distance"] = np.sqrt(
        (df_work_persons["home_x"] - df_work_persons["work_x"]) ** 2 +
        (df_work_persons["home_y"] - df_work_persons["work_y"]) ** 2
    )
    
    print(f"INFO: Work distance statistics:")
    print(df_work_persons["distance"].describe())
    
    # Prepare output - create GeoDataFrame with the original CRS from candidates
    df_result = gpd.GeoDataFrame(
        df_work_persons[["person_id", "commune_id", "location_id", "geometry"]],
        geometry="geometry",
        crs=df_work_candidates.crs
    )
    
    return df_result


def execute(context):
    np.random.seed(context.config("random_seed"))
    
    df_work_locations = impute_work_locations_radius(context)
    
    return df_work_locations
