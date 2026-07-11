import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

def configure(context):
    context.stage("data.hts.selected")
    context.config("output_path")

def compute_cdf(context, df, bin_size=100, percentile=0.90):
    print("Distance stats: ", df["distance"].describe())
    
    # calibrate
    quant = df["distance"].quantile(percentile)


    df_quant = df[df["distance"] <= quant]
    
    if len(df_quant) == 0:
        print("ERROR: No trips within reasonable distance range!")
        return None, None, None
    
    # Create histogram plot
    plt.figure()
    plt.hist(df_quant["distance"], weights=df_quant["weight"], bins=bin_size)
    plt.xlabel("Distance (m)")
    plt.ylabel("Frequency")
    plt.title("Distance Distribution")
    
    # Save plot
    output_path = context.config("output_path")
    plt.savefig(f"{output_path}/distance_distribution_{bin_size}.png")
    plt.close()

    # Compute histogram and CDF
    hist_vals, bins_vals = np.histogram(df_quant["distance"], weights=df_quant["weight"], bins=bin_size)

    histbin_midpoints = bins_vals[:-1] + np.diff(bins_vals) / 2
    cdf = np.cumsum(hist_vals)
    cdf = cdf / cdf[-1]

    # The threshold buffer is used to create a maximum radius boundary for sampling the distance
    threshold_buffer = np.diff(bins_vals) / 2
    threshold_buffer = threshold_buffer[0]

    return cdf, histbin_midpoints, threshold_buffer


def execute(context):
    distance_field = "euclidean_distance"

    df_households, df_persons, df_trips = context.stage("data.hts.selected")
    
    df_persons = df_persons[["person_id", "person_weight"]].rename(
        columns={"person_weight": "weight"})

    df_trips = df_trips[["person_id", "trip_id", "mode", distance_field, 
                         "departure_time", "arrival_time", "following_purpose"]]
    df_trips = pd.merge(df_trips, df_persons[["person_id", "weight"]], on="person_id")

    df_trips = df_trips[df_trips[distance_field] > 0.0]

    # Calculate distributions
    # calibrate
    bin_size_work = 200
    bin_size_edu = 100
    distributions = {}

    # Extract work distances
    # Using the person weights and the distance of all trips that end at work
    df_trips_work = df_trips[df_trips["following_purpose"] == "work"].copy()

    if len(df_trips_work) > 0:
        df_work = df_trips_work[[distance_field, "weight"]].rename(
            columns={distance_field: "distance"})
        work_cdf, work_midpoint_bin_distances, work_threshold_buffer = compute_cdf(
            context, df_work, bin_size=bin_size_work, percentile=1.0) # 0.80

        # Write distribution for work
        if work_cdf is not None:
            distributions["work"] = dict(
                cdf=work_cdf, 
                midpoint_bins=work_midpoint_bin_distances, 
                threshold_buffer=work_threshold_buffer
            )
        else:
            print("WARNING: Could not generate work distance distribution")
            distributions["work"] = None
    else:
        print("WARNING: No work trips found in HTS data")
        distributions["work"] = None

    # Extract education distances
    df_trips_edu = df_trips[df_trips["following_purpose"] == "education"].copy()

    if len(df_trips_edu) > 0:
        df_edu = df_trips_edu[[distance_field, "weight"]].rename(
            columns={distance_field: "distance"})
        edu_cdf, edu_midpoint_bin_distances, edu_threshold_buffer = compute_cdf(
            context, df_edu, bin_size=bin_size_edu, percentile=1.0) # 0.90

        # Write distribution for education
        if edu_cdf is not None:
            distributions["education"] = dict(
                cdf=edu_cdf, 
                midpoint_bins=edu_midpoint_bin_distances, 
                threshold_buffer=edu_threshold_buffer
            )
        else:
            print("WARNING: Could not generate education distance distribution")
            distributions["education"] = None
    else:
        print("WARNING: No education trips found in HTS data")
        distributions["education"] = None

    return distributions
