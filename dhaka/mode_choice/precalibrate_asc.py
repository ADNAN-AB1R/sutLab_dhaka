import argparse
import json
import os

import numpy as np
import pandas as pd
import geopandas as gpd

from dhaka.synthesis.population.mode_choice import compute_fare_bdt

"""
OFFLINE pre-calibration of dhaka_mode_parameters.json's ASCs, run once before
the first MATSim calibration round.

Why this exists (it does NOT replace calibrate_asc.py):
The thesis's published ASCs were estimated on commute-only trips and start
very far from Dhaka's all-trip mode split - walk comes out ~1% modelled
against a ~33% target. Closing a gap that large with calibrate_asc.py alone
costs one full multi-iteration MATSim run (~1.5-2 h) per calibration step,
for many steps, nearly all of it spent covering a distance that has nothing
to do with congestion feedback and everything to do with the ASCs being
anchored to a different trip population.

This script closes that part analytically. It applies the SAME utility
function (U = asc + beta_duration*time + beta_fare*fare) to the same
synthetic trips, using the fallback-speed travel-time proxy, and iterates
    asc_new = asc_old + ln(target_share / modelled_share)
re-based on the reference mode - exactly calibrate_asc.py's update rule - to
a fixed point. Because the proxy times and fares do not move as the ASCs
move, this converges in ~20 rounds and a few seconds.

What it CANNOT do, which is why MATSim rounds are still required afterwards:
  - car time comes from the congested network and pt time from
    SwissRailRaptor (including access/wait/transfer). Neither has a closed
    form here; both fall back to documented proxy speeds (see
    dhaka_mode_parameters.json's "fallback_speed_kmh"), so their ASCs will
    still be off. Every other mode is teleported, and for those the proxy
    speed is exact by construction.
  - MATSim chooses modes per TOUR (modelType=Tour) under a vehicle-
    continuity constraint, not independently per trip as this script does.
  - congestion responds to the mode split, which responds to congestion.

So the intended workflow is:

    1. python -m dhaka.mode_choice.precalibrate_asc            (seconds, once)
    2. regenerate the scenario, run MATSim
    3. python -m dhaka.mode_choice.calibrate_asc output/simulation_output/modestats.csv
    4. repeat 2-3 until every mode is within tolerance

Step 1 removes the large structural part of the gap so step 3 only has to
correct the genuinely simulation-dependent residual.

Target shares are the same ones calibrate_asc.py uses (`mode_hts_donor`), so
the two stages calibrate against an identical target and cannot drift apart.

Distances come from the trips GeoPackage's geometry - the real synthetic
origin-destination distance produced by the location-assignment stage. The
trips CSV's `euclidean_distance` column is the HTS DONOR's distance, not
this synthetic trip's (they correlate at r=0.02), and must not be used here.

Usage:
    python -m dhaka.mode_choice.precalibrate_asc [--trips path.gpkg] [--dry-run]
"""

PARAMETERS_PATH = os.path.join(os.path.dirname(__file__), "dhaka_mode_parameters.json")
DEFAULT_TRIPS_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "output", "dhaka_1pct_trips.gpkg"
)

MAXIMUM_ROUNDS = 200
TOLERANCE = 1e-5


def load_trips(trips_path):
    df = gpd.read_file(trips_path)
    distance_m = df.geometry.length.to_numpy(dtype = float)
    donor_mode = df["mode_hts_donor"].to_numpy()

    usable = np.isfinite(distance_m)
    return distance_m[usable], donor_mode[usable]


def compute_target_shares(modes, donor_mode):
    counts = pd.Series(donor_mode).value_counts()
    counts = counts[counts.index.isin(modes)]
    shares = counts / counts.sum()
    return shares.reindex(modes).fillna(0.0).to_numpy()


def build_utility_base(model, modes, distance_m):
    """The part of the utility that does NOT move while the ASCs are being
    calibrated - computed once, not once per round."""
    time_minutes = np.column_stack([
        distance_m / 1000.0 / model["fallback_speed_kmh"][mode] * 60.0 for mode in modes
    ])
    fare_bdt = np.column_stack([
        np.broadcast_to(
            compute_fare_bdt(mode, distance_m, model["fare_assumptions"]), distance_m.shape)
        for mode in modes
    ])
    return (model["beta_duration_per_minute"] * time_minutes
            + model["beta_fare_per_bdt"] * fare_bdt)


def logit_shares(utility_base, asc):
    utility = utility_base + asc
    utility = utility - utility.max(axis = 1, keepdims = True)
    exponentiated = np.exp(utility)
    return (exponentiated / exponentiated.sum(axis = 1, keepdims = True)).mean(axis = 0)


def precalibrate(trips_path, dry_run):
    with open(PARAMETERS_PATH, "r", encoding = "utf-8") as f:
        model = json.load(f)

    modes = model["modes"]
    reference_mode = model["reference_mode"]
    reference_index = modes.index(reference_mode)

    distance_m, donor_mode = load_trips(trips_path)
    print("Loaded %d trips with usable distances (median %.0f m)" % (
        len(distance_m), np.median(distance_m)))

    target = compute_target_shares(modes, donor_mode)
    utility_base = build_utility_base(model, modes, distance_m)

    asc_start = np.array([0.0 if m == reference_mode else model["asc"][m] for m in modes])
    share_start = logit_shares(utility_base, asc_start)

    asc = asc_start.copy()
    for round_index in range(MAXIMUM_ROUNDS):
        share = logit_shares(utility_base, asc)
        if np.abs(share - target).max() < TOLERANCE:
            break

        # Same update rule as calibrate_asc.py, re-based on the reference
        # mode so its implicit ASC stays pinned at 0 (only utility
        # DIFFERENCES matter in a logit model).
        delta = np.log(np.maximum(target, 1e-12) / np.maximum(share, 1e-12))
        delta = delta - delta[reference_index]
        asc = asc + delta
        asc[reference_index] = 0.0

    share = logit_shares(utility_base, asc)
    print("Converged after %d rounds (largest remaining share error %.4f points)\n" % (
        round_index + 1, 100.0 * np.abs(share - target).max()))

    header = ("mode", "target", "before", "after", "old_asc", "new_asc")
    print("%-14s%10s%10s%10s%12s%12s" % header)
    for i, mode in enumerate(modes):
        suffix = "  (reference)" if mode == reference_mode else ""
        print("%-14s%9.2f%%%9.2f%%%9.2f%%%12.3f%12.3f%s" % (
            mode, 100 * target[i], 100 * share_start[i], 100 * share[i],
            asc_start[i], asc[i], suffix))

    if dry_run:
        print("\n--dry-run: %s NOT modified." % PARAMETERS_PATH)
        return

    for i, mode in enumerate(modes):
        if mode != reference_mode:
            # 4 dp is far below the resolution at which an ASC changes a mode
            # share (1e-4 utils moves a share by well under 0.01 points) and
            # keeps the file readable/diffable.
            model["asc"][mode] = round(float(asc[i]), 4)

    model["asc_provenance"] = (
        "Pre-calibrated offline by dhaka/mode_choice/precalibrate_asc.py against the "
        "mode_hts_donor target shares, starting from the thesis's published ASCs (which "
        "are kept verbatim in this file's 'source' and 'composition' fields). The values "
        "in 'asc' are therefore NO LONGER the thesis values - see 'source' for those. "
        "They are still to be refined by MATSim calibration rounds via "
        "dhaka/mode_choice/calibrate_asc.py, which corrects the part of the gap only the "
        "simulation can reveal: congested car time, routed pt time, tour-level choice, "
        "and congestion feedback.")

    with open(PARAMETERS_PATH, "w", encoding = "utf-8") as f:
        json.dump(model, f, indent = 2, ensure_ascii = False)

    print("\nUpdated %s" % PARAMETERS_PATH)
    print("Next: regenerate the scenario, run MATSim, then run calibrate_asc.py "
          "on the resulting modestats.csv.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--trips", default = DEFAULT_TRIPS_PATH,
        help = "Trips GeoPackage supplying real synthetic O-D distances and the "
               "mode_hts_donor target column (default: output/dhaka_1pct_trips.gpkg)")
    parser.add_argument("--dry-run", action = "store_true",
        help = "Report the fitted ASCs without writing them back")
    args = parser.parse_args()

    precalibrate(args.trips, args.dry_run)
