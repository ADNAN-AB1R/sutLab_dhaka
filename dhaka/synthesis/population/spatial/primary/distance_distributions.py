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


# Minimum survey trips for a purpose x mode cell to get its own distribution;
# smaller cells fall back to the purpose's pooled distribution.
MINIMUM_TRIPS = 200


def build_distribution(context, df, distance_field, number_of_bins):
    df = df[[distance_field, "weight"]].rename(columns = { distance_field: "distance" })
    cdf, midpoint_bins, threshold_buffer = compute_cdf(
        context, df, bin_size = number_of_bins, percentile = 1.0)
    if cdf is None:
        return None
    return dict(cdf = cdf, midpoint_bins = midpoint_bins, threshold_buffer = threshold_buffer)


def execute(context):
    distance_field = "euclidean_distance"

    df_households, df_persons, df_trips = context.stage("data.hts.selected")
    
    df_persons = df_persons[["person_id", "person_weight"]].rename(
        columns={"person_weight": "weight"})

    df_trips = df_trips[["person_id", "trip_id", "mode", distance_field, 
                         "departure_time", "arrival_time", "following_purpose"]]
    df_trips = pd.merge(df_trips, df_persons[["person_id", "weight"]], on="person_id")

    df_trips = df_trips[df_trips[distance_field] > 0.0]

    # Distributions per purpose, each POOLED over modes (the fallback) plus
    # one per commute MODE. Work and education locations were previously drawn
    # from the pooled distribution only, so a walking commuter and a bus
    # commuter drew from the same distances - measured on the synthetic
    # population, walk commutes came out ~70% too long and pt/car commutes
    # ~30-45% too short against the survey (distances compressed towards the
    # middle). The location stages now draw from their person's commute-mode
    # distribution when that mode has at least MINIMUM_TRIPS survey trips.
    distributions = {}
    for purpose, number_of_bins in (("work", 200), ("education", 100)):
        df_purpose = df_trips[df_trips["following_purpose"] == purpose]
        if len(df_purpose) == 0:
            print("WARNING: No %s trips found in HTS data" % purpose)
            distributions[purpose] = None
            continue

        pooled = build_distribution(context, df_purpose, distance_field, number_of_bins)
        if pooled is None:
            print("WARNING: Could not generate %s distance distribution" % purpose)
            distributions[purpose] = None
            continue

        pooled["by_mode"] = {}
        for mode, df_mode in df_purpose.groupby("mode"):
            if len(df_mode) < MINIMUM_TRIPS:
                continue
            by_mode = build_distribution(context, df_mode, distance_field, number_of_bins)
            if by_mode is not None:
                pooled["by_mode"][str(mode)] = by_mode

        print("%s distance distributions: pooled + by mode for %s" % (
            purpose, sorted(pooled["by_mode"])))
        distributions[purpose] = pooled

    return distributions
