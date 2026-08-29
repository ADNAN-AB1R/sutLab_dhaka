import os
import json

import numpy as np
import pandas as pd

"""
Applies the mode-choice model estimated in Hoque's MSc thesis
(paper/Ismamul Hoque Msc Thesis.pdf, Table 4.1;
dhaka/mode_choice/dhaka_mode_parameters.json) to every synthetic trip,
replacing the fixed mode inherited from each trip's matched HTS donor
(dhaka.synthesis.population.trips) with a modeled choice that responds to
that trip's REAL assigned distance (dhaka.synthesis.population.spatial.
locations). This is the same model matsim_run's FittedMnlTripEstimator uses
for in-simulation replanning - see that class's docstring for the shared
formula (U = asc[mode] + beta_duration*time + beta_fare*fare). This stage
only produces the INITIAL seed plan; the in-simulation estimator immediately
starts re-optimizing it against real routed travel times once MATSim runs.

This has to run as a separate stage AFTER locations are assigned, not inside
dhaka/synthesis/population/trips.py itself: trips.py runs before any
synthetic home/work/education/secondary location has been picked, so at that
point the only distance available is the one inherited wholesale from the
HTS donor trip, not this synthetic person's own geometry. Real distances are
only available once dhaka.synthesis.population.spatial.locations exists -
see that stage (and synthesis/population/activities.py) for how activity
k's location is trip k's origin and activity k+1 is trip k's destination.

Unlike the project's earlier HTS-fitted model, this one has no
socio-demographic covariates at all - that's the thesis's actual
specification (confirmed by reading its methodology), not a simplification
made here. Duration comes from a distance/fallback-speed proxy (no real
routing available yet at this pipeline stage - see
dhaka_mode_parameters.json's "fallback_speed_kmh", not thesis-sourced, kept
only for this proxy); fare comes from the same per-mode cost construction
matsim_run's DhakaCostCalculator implements in Java (ported here so the
seed assignment is consistent with the in-simulation model) - see
dhaka_mode_parameters.json's "fare_assumptions" for each mode's formula and
source (several are documented assumptions filling real gaps the thesis
leaves open, e.g. rickshaw's rate).

Departure/arrival times and trip_duration are intentionally left as-is
(still the HTS donor's values) rather than re-derived from the newly chosen
mode: these are only used as MATSim's INITIAL plan schedule, and MATSim
computes actual simulated travel time itself from the network/transit
schedule and the assigned mode during simulation (car routed on network,
other modes teleported at the per-mode speeds in
dhaka/matsim/assemble_scenario.py's config.xml template) - so an exactly
re-timed static input isn't required for the assigned mode to take effect.

The original HTS-donor mode is kept as `mode_hts_donor` for comparison/
validation, not discarded.
"""

PARAMETERS_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "mode_choice", "dhaka_mode_parameters.json"
)


def configure(context):
    context.stage("dhaka.synthesis.population.trips")
    context.stage("synthesis.population.spatial.locations")
    context.config("random_seed")


def load_model():
    with open(PARAMETERS_PATH, "r", encoding = "utf-8") as f:
        return json.load(f)


def compute_trip_distances(df_trips, df_locations):
    """Real synthetic-population distance per trip: distance between the
    origin activity's location (activity_index == trip_index) and the
    destination activity's location (activity_index == trip_index + 1)."""
    df_points = df_locations[["person_id", "activity_index", "geometry"]].copy()
    df_points["x"] = df_points["geometry"].x
    df_points["y"] = df_points["geometry"].y
    df_points = df_points.drop(columns = "geometry")

    df = df_trips[["person_id", "trip_index"]].copy()

    df_origin = df_points.rename(columns = { "activity_index": "trip_index", "x": "origin_x", "y": "origin_y" })
    df = df.merge(df_origin, on = ["person_id", "trip_index"], how = "left")

    df_destination = df_points.copy()
    df_destination["trip_index"] = df_destination["activity_index"] - 1
    df_destination = df_destination.rename(columns = { "x": "destination_x", "y": "destination_y" }).drop(columns = "activity_index")
    df = df.merge(df_destination, on = ["person_id", "trip_index"], how = "left")

    distance_m = np.hypot(df["destination_x"] - df["origin_x"], df["destination_y"] - df["origin_y"])
    return distance_m.to_numpy()


def compute_fare_bdt(mode, distance_m, fare_assumptions):
    """Mirrors matsim_run's DhakaCostCalculator/costs/* Java classes exactly
    - see those and dhaka_mode_parameters.json's "fare_assumptions" for each
    mode's formula and source."""
    distance_km = distance_m / 1000.0

    if mode in ("car", "motorcycle"):
        params = fare_assumptions[mode]
        bdt_per_km = params["fuel_price_bdt_per_liter"] / params["km_per_liter"]
        return bdt_per_km * distance_km

    if mode == "pt":
        params = fare_assumptions["pt"]
        return np.maximum(params["minimum_fare_bdt"], params["bdt_per_km"] * distance_km)

    if mode == "paratransit":
        params = fare_assumptions["paratransit"]
        flag_fall_km = params["flag_fall_km"]
        return np.where(
            distance_km <= flag_fall_km,
            params["flag_fall_bdt"],
            params["flag_fall_bdt"] + params["bdt_per_km_after"] * (distance_km - flag_fall_km)
        )

    if mode == "rickshaw":
        return fare_assumptions["rickshaw"]["bdt_per_km"] * distance_km

    return np.zeros_like(distance_km)  # bike, walk


def apply_model(df, model, random_seed):
    modes = model["modes"]
    reference_mode = model["reference_mode"]
    non_ref_alts = [m for m in modes if m != reference_mode]

    n_obs = len(df)
    n_alts = len(modes)
    alt_index = { m: i for i, m in enumerate(modes) }

    distance_m = df["distance_m"].to_numpy()

    # Alternative-specific travel-time proxy - no real routing available at
    # this pipeline stage (see module docstring).
    time_matrix = np.zeros((n_obs, n_alts))
    for mode in modes:
        speed_m_per_min = model["fallback_speed_kmh"][mode] * 1000.0 / 60.0
        time_matrix[:, alt_index[mode]] = distance_m / speed_m_per_min

    fare_matrix = np.zeros((n_obs, n_alts))
    for mode in modes:
        fare_matrix[:, alt_index[mode]] = compute_fare_bdt(mode, distance_m, model["fare_assumptions"])

    U = model["beta_duration_per_minute"] * time_matrix + model["beta_fare_per_bdt"] * fare_matrix
    for mode in non_ref_alts:
        U[:, alt_index[mode]] += model["asc"][mode]

    U = U - U.max(axis = 1, keepdims = True)
    exp_U = np.exp(U)
    P = exp_U / exp_U.sum(axis = 1, keepdims = True)

    # Sample one mode per trip from its choice-probability distribution
    # (not argmax) - a discrete choice model describes a probability
    # distribution over alternatives, and sampling reproduces that
    # distribution's aggregate mode shares; argmax would instead always
    # pick the single most likely mode for every trip, collapsing all the
    # modeled variation.
    random = np.random.RandomState(random_seed)
    cumulative = np.cumsum(P, axis = 1)
    draws = random.random_sample(n_obs)
    chosen_index = (draws[:, None] < cumulative).argmax(axis = 1)

    chosen_mode = np.array(modes)[chosen_index]

    # Trips with no resolvable distance (e.g. a person missing a location)
    # keep their original HTS-donor mode rather than being assigned an
    # undefined/NaN-driven choice.
    missing = ~np.isfinite(distance_m)
    chosen_mode = np.where(missing, df["mode"].astype(str).to_numpy(), chosen_mode)

    return chosen_mode


def execute(context):
    df_trips = context.stage("dhaka.synthesis.population.trips").copy()
    df_locations = context.stage("synthesis.population.spatial.locations")

    model = load_model()

    df_trips["distance_m"] = compute_trip_distances(df_trips, df_locations)

    df_trips["mode_hts_donor"] = df_trips["mode"]
    df_trips["mode"] = apply_model(df_trips, model, context.config("random_seed"))
    df_trips["mode"] = df_trips["mode"].astype("category")

    print("Modeled mode shares (vs. HTS-donor-inherited baseline):")
    comparison = pd.DataFrame({
        "modeled": df_trips["mode"].value_counts(normalize = True),
        "hts_donor": df_trips["mode_hts_donor"].value_counts(normalize = True),
    }).fillna(0.0)
    print(comparison)

    return df_trips[[
        "person_id", "trip_index",
        "departure_time", "arrival_time",
        "preceding_purpose", "following_purpose",
        "is_first_trip", "is_last_trip",
        "trip_duration", "activity_duration",
        "mode", "mode_hts_donor",
        "euclidean_distance",
    ]]
