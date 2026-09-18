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



def commute_modes(df_trips, df_persons, purpose):
    """Mode of each person's (first) trip TO the given purpose - the mode the
    commute distance should be drawn for. NaN if the person has no such trip
    (e.g. only a trip leaving work); those fall back to the pooled
    distribution."""
    df_commute = df_trips[df_trips["following_purpose"] == purpose][["person_id", "mode"]]
    df_commute = df_commute.drop_duplicates("person_id")
    return df_persons[["person_id"]].merge(df_commute, on = "person_id", how = "left")["mode"].astype(object).values


def sample_radius(distribution, modes):
    """Target distance and matching tolerance per person, drawn from the
    distribution for that person's commute mode (or the purpose's pooled one
    when the mode has none). Modes are processed in sorted order so the
    random draws are reproducible - iterating a set of strings is not."""
    radius = np.zeros(len(modes))
    tolerance = np.zeros(len(modes))
    mode_keys = np.array([m if isinstance(m, str) else "" for m in modes])
    for mode in sorted(set(mode_keys)):
        selected = distribution["by_mode"].get(mode, distribution)
        f = mode_keys == mode
        draws = np.random.rand(f.sum())
        radius[f] = selected["midpoint_bins"][np.searchsorted(selected["cdf"], draws)]
        tolerance[f] = selected["threshold_buffer"]
    return radius, tolerance


def select_near_radius(ind, dist, target, tolerance, query_size):
    """Candidates whose distance from home is CLOSEST to the sampled target.
    Everything within tolerance of it, or failing that the query_size
    candidates nearest to it. Replaces the previous rule, which searched out to
    1.2x the target and then took the FARTHEST candidates found - a systematic
    outward push that made synthetic work commutes ~27% longer than the
    survey's."""
    difference = np.abs(dist - target)
    selected = ind[difference <= tolerance]
    if len(selected) < query_size:
        selected = ind[np.argsort(difference)[:query_size]]
    return selected


def prepare_work_persons(context):
    df_persons = context.stage("synthesis.population.enriched")
    df_trips = context.stage("synthesis.population.trips")
    
    # Find persons with work trips
    df_trips_work = df_trips[(df_trips["following_purpose"] == "work") | (df_trips["preceding_purpose"] == "work")].copy()
    df_work_persons = df_persons[df_persons["person_id"].isin(df_trips_work["person_id"].unique())].copy()
    df_work_persons["commute_mode"] = commute_modes(df_trips, df_work_persons, "work")

    return df_work_persons


def prepare_work_destinations(context):
    df_work_candidates = context.stage("synthesis.locations.work")
    
    # Extract coordinates from geometry
    df_work_candidates["destination_x"] = df_work_candidates["geometry"].x
    df_work_candidates["destination_y"] = df_work_candidates["geometry"].y
    
    return df_work_candidates


def prepare_radius_from_cdf(context, df_work_persons):
    distributions = context.stage("synthesis.population.spatial.primary.distance_distributions")
    
    radius, tolerance = sample_radius(distributions["work"], df_work_persons["commute_mode"].values)
    return radius, tolerance


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
    radius, tolerance = prepare_radius_from_cdf(context, df_work_persons)


    
    query_size = 3
    no_fac_count = 0
    
    # Build KDTree for work locations
    work_coordinates = np.vstack([df_work_candidates["destination_x"], df_work_candidates["destination_y"]]).T
    tree = KDTree(work_coordinates)
    
    # Sample distances and find candidates within radius
    # Search just far enough to hold candidates around the target distance.
    search_radius = radius + tolerance
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
        
        # Keep the candidates closest to the sampled target distance.
        ind = select_near_radius(ind, dist, radius[i], tolerance[i], query_size)

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
