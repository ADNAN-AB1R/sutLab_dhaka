import os
import json
import hashlib

import numpy as np
import pandas as pd

"""
Applies the mode-choice model estimated in Hoque's MSc thesis
(paper/Ismamul Hoque Msc Thesis.pdf, Table 4.1;
dhaka/mode_choice/dhaka_mode_parameters.json) to every synthetic trip,
replacing the fixed mode inherited from each trip's matched HTS donor
(dhaka.synthesis.population.trips) with a modeled choice that responds to
that trip's REAL assigned distance (dhaka.synthesis.population.spatial.
locations). This is the same model matsim_run's DhakaTripEstimator uses for
in-simulation replanning (U = asc[mode] + beta_duration*time
+ beta_fare(income)*fare - see income_fare_scale below). This stage
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

The thesis's specification has no socio-demographic covariates; the one
added here is income-scaled cost sensitivity (dhaka_mode_parameters.json
"income_scaling"), because the thesis's single pooled beta_fare implies a
VTTS of 4.2x the wage for low-income households. Nothing else is added: per-
mode age/sex/licence coefficients could only come from estimation, which the
DTCA survey cannot support (no usable trip length - see
dhaka/mode_choice/build_estimation_dataset.py). Licence is still respected,
via DiscreteModeChoice's modeAvailability=Car. Duration comes from a distance/fallback-speed proxy (no real
routing available yet at this pipeline stage - see
dhaka_mode_parameters.json's "travel_time_model" - fixed overhead plus a
per-km rate per mode, fitted to simulated door-to-door trip times); fare comes from the same per-mode cost construction
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


def model_file_digest():
    """Content digest of dhaka_mode_parameters.json, registered as a config
    value in configure() so that editing the model invalidates this stage's
    synpp cache.

    Without this the cache is WRONG, not merely stale: synpp's get_stage_hash
    hashes only the stage module's own source code, and the cache key is that
    plus the stage's required config values. A data file read at execute time
    is invisible to both, so re-running the pipeline after (say) an ASC
    calibration silently reuses the cached seed plan computed from the OLD
    coefficients - with no warning, and with output files whose timestamps
    update as though they had been regenerated.

    Digesting the whole file (not just the coefficients) means a comment-only
    edit also invalidates. That is the safe direction to err in: it costs one
    re-run of a cheap stage, whereas under-invalidating silently corrupts the
    seed plan."""
    with open(PARAMETERS_PATH, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def configure(context):
    context.stage("dhaka.synthesis.population.trips")
    context.stage("synthesis.population.spatial.locations")
    context.stage("synthesis.population.sampled")
    context.config("random_seed")
    context.config("dhaka.mode_parameters_digest", model_file_digest())


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

    if mode in ("car", "motorcycle", "rickshaw"):
        # Effective BDT/km calibrated against the survey's reported trip costs
        # (dhaka/mode_choice/calibrate_fares.py). car/motorcycle previously used
        # fuel_price / km_per_liter, which structurally cannot represent a
        # ride-hail fare or a hired driver's wage and understated observed cost
        # 5.1x / 3.6x respectively.
        return fare_assumptions[mode]["bdt_per_km"] * distance_km

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

    return np.zeros_like(distance_km)  # bike, walk


def proxy_travel_time_min(model, mode, distance_m):
    """Door-to-door travel-time proxy in minutes for a straight-line distance:
    overhead_min + min_per_km * km (dhaka_mode_parameters.json
    "travel_time_model"). Shared by this stage and precalibrate_asc.py so the
    seed plan and the offline ASC fit use the same proxy. The Java estimator
    does not use it - it has real routed times."""
    spec = model["travel_time_model"][mode]
    return spec["overhead_min"] + spec["min_per_km"] * (np.asarray(distance_m, dtype = float) / 1000.0)


def income_fare_scale(model, income_class):
    """Per-person multiplier on beta_fare_per_bdt, from the ordinal household
    income class (0-8, -1/NaN = not stated).

    MUST stay identical to matsim_run's DhakaModeParameters.getBetaFare(Person):
    this Python side builds the seed plan and the offline ASC pre-calibration,
    the Java side makes every in-simulation choice, and if the two disagree the
    pre-calibrated ASCs are fitted to a different model from the one that runs.
    Unknown or out-of-range classes get multiplier 1 (unscaled), as in Java.
    See dhaka_mode_parameters.json "income_scaling" for the rationale."""
    scaling = model["income_scaling"]
    midpoints = np.asarray(scaling["class_midpoints_bdt_per_month"], dtype = float)
    scale_by_class = (midpoints / scaling["reference_income_bdt_per_month"]) ** (-scaling["elasticity"])

    income_class = np.asarray(income_class, dtype = float)
    scale = np.ones(len(income_class))
    rounded = np.round(np.nan_to_num(income_class, nan = -1.0))
    valid = (rounded >= 0) & (rounded < len(midpoints))
    scale[valid] = scale_by_class[rounded[valid].astype(int)]
    return scale


# Household vehicle-count column per ownership-constrained mode. The Java side
# reads the same counts from the MATSim person attributes householdCars /
# householdMotorcycles / householdBikes.
OWNERSHIP_COLUMNS = {
    "car": "number_of_cars",
    "motorcycle": "number_of_motorcycles",
    "bike": "number_of_bikes",
}


def ownership_constant_matrix(model, modes, vehicle_counts):
    """(n_trips, n_modes) matrix holding each mode's non_owner_constant where
    the trip-maker's household owns none of that vehicle, else 0.

    MUST stay identical to DhakaModeParameters.getOwnershipConstant(Person,
    mode). vehicle_counts maps mode -> array of household counts; NaN (unknown)
    is treated as an owner, i.e. no penalty, exactly as Java treats a missing
    attribute."""
    n_trips = len(next(iter(vehicle_counts.values())))
    matrix = np.zeros((n_trips, len(modes)))
    for mode, spec in model["ownership_constants"].items():
        if not isinstance(spec, dict) or mode not in modes:
            continue
        counts = np.asarray(vehicle_counts[mode], dtype = float)
        non_owner = np.nan_to_num(counts, nan = 1.0) == 0
        matrix[non_owner, modes.index(mode)] = spec["non_owner_constant"]
    return matrix


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
        time_matrix[:, alt_index[mode]] = proxy_travel_time_min(model, mode, distance_m)

    fare_matrix = np.zeros((n_obs, n_alts))
    for mode in modes:
        fare_matrix[:, alt_index[mode]] = compute_fare_bdt(mode, distance_m, model["fare_assumptions"])

    # Income-scaled cost sensitivity, one multiplier per trip (per person).
    fare_scale = income_fare_scale(model, df["income_class"].to_numpy())
    U = (model["beta_duration_per_minute"] * time_matrix
         + model["beta_fare_per_bdt"] * fare_scale[:, None] * fare_matrix)
    for mode in non_ref_alts:
        U[:, alt_index[mode]] += model["asc"][mode]

    U += ownership_constant_matrix(model, modes, {
        mode: df[column].to_numpy() for mode, column in OWNERSHIP_COLUMNS.items()})

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

    # Household income class per trip, for income-scaled cost sensitivity.
    df_person_attributes = context.stage("synthesis.population.sampled")[
        ["person_id", "income_class"] + list(OWNERSHIP_COLUMNS.values())]
    df_trips = df_trips.merge(df_person_attributes, on = "person_id", how = "left")
    print("Trips with a resolved income class: %.1f%%" % (
        100 * (df_trips["income_class"].fillna(-1) >= 0).mean()))

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
