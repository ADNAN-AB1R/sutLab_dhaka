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


# The three helpers below are deliberately duplicated in work.py rather than
# shared from a module: synpp hashes only a stage's own source, so a shared
# helper could change without invalidating either stage's cache.

def commute_modes(df_trips, df_persons, purpose):
    """Mode of each person's (first) trip TO the given purpose - the mode the
    commute distance should be drawn for. NaN if the person has no such trip;
    those fall back to the pooled distribution."""
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
    """Candidates whose distance from home is CLOSEST to the sampled target:
    everything within tolerance of it, or failing that the query_size
    candidates nearest to it. Replaces the previous rule, which took the
    FARTHEST candidates within the search radius - an outward push."""
    difference = np.abs(dist - target)
    selected = ind[difference <= tolerance]
    if len(selected) < query_size:
        selected = ind[np.argsort(difference)[:query_size]]
    return selected


def prepare_education_persons(context):
    df_persons = context.stage("synthesis.population.enriched")
    df_trips = context.stage("synthesis.population.trips")
    
    # Find persons with education trips
    df_trips_education = df_trips[df_trips["following_purpose"] == "education"].copy()
    df_education_persons = df_persons[df_persons["person_id"].isin(df_trips_education["person_id"].unique())].copy()
    df_education_persons["commute_mode"] = commute_modes(df_trips, df_education_persons, "education")

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
    radius, tolerance = sample_radius(distributions["education"], df_education_persons["commute_mode"].values)
    return radius, tolerance


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
    radius, tolerance = prepare_radius_from_cdf(context, df_education_persons)

    
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
        radius_group = radius[f_persons.values]
        tolerance_group = tolerance[f_persons.values]
        indices, distances = tree.query_radius(
            home_coordinates,
            r=radius_group + tolerance_group, 
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
            
            # Keep the candidates closest to the sampled target distance.
            ind = select_near_radius(ind, dist, radius_group[i], tolerance_group[i], query_size)

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
