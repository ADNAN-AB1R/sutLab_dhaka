import argparse
import json
import os

import pandas as pd
import numpy as np

"""
Standard sample-enumeration ASC calibration (Train, 2009): adjusts
fitted_coefficients.json's `asc` values so the modes DiscreteModeChoice
actually converges to in a real MATSim run (matsim_run's simulated
modestats.csv) match the HTS-observed target shares (the same weighted
mode distribution dhaka/mode_choice/estimate.py fits against), rather than
whatever the model produces when driven by real routed travel times
instead of the training-time distance/speed proxy - the two are expected
to diverge somewhat (see dhaka/mode_choice/estimate.py's docstring on
fallback_speed_kmh), and that gap is exactly what ASC calibration exists
to close.

For each mode: delta = ln(target_share / simulated_share), then every
delta is re-based relative to the reference mode's own delta (so the
reference mode's implicit ASC=0 stays fixed at 0 - only relative utility
differences matter for a logit model, shifting every ASC by the same
constant changes nothing).

This is ONE calibration step, not a solver - MATSim's simulated response
to a new set of ASCs is itself non-linear (route/congestion feedback), so
recalibration is iterative: run matsim_run with the newly-written
fitted_coefficients.json for enough iterations to reconverge (see
matsim_run/README.md), regenerate modestats.csv, run this script again
pointing at the new file, repeat until each mode's simulated share is
within tolerance of its target.

Usage:
    python -m dhaka.mode_choice.calibrate_asc path/to/modestats.csv [--iteration N]
"""

COEFFICIENTS_PATH = os.path.join(os.path.dirname(__file__), "fitted_coefficients.json")
DATASET_PATH = os.path.join(os.path.dirname(__file__), "cache_estimation_dataset.parquet")


def compute_target_shares(modes):
    df = pd.read_parquet(DATASET_PATH)
    df = df[df["mode"].isin(modes)]
    weights = df.groupby("mode", observed = True)["trip_weight"].sum()
    weights = weights / weights.sum()
    return { mode: float(weights.get(mode, 0.0)) for mode in modes }


def read_simulated_shares(modestats_path, iteration, modes):
    df = pd.read_csv(modestats_path, sep = ";")
    row = df.iloc[-1] if iteration is None else df[df["iteration"] == iteration].iloc[0]
    return { mode: float(row[mode]) for mode in modes if mode in row }


def calibrate(modestats_path, iteration):
    with open(COEFFICIENTS_PATH, "r", encoding = "utf-8") as f:
        model = json.load(f)

    modes = model["modes"]
    reference_mode = model["reference_mode"]

    target_shares = compute_target_shares(modes)
    simulated_shares = read_simulated_shares(modestats_path, iteration, modes)

    delta = {
        mode: np.log(target_shares[mode] / simulated_shares[mode])
        for mode in modes
        if simulated_shares.get(mode, 0.0) > 0 and target_shares.get(mode, 0.0) > 0
    }
    reference_delta = delta[reference_mode]

    print(f"{'mode':<14}{'target':>10}{'simulated':>12}{'old_asc':>12}{'new_asc':>12}")
    for mode in modes:
        if mode == reference_mode:
            print(f"{mode:<14}{target_shares[mode]:>10.4f}{simulated_shares.get(mode, float('nan')):>12.4f}{'0 (ref)':>12}{'0 (ref)':>12}")
            continue

        relative_delta = delta[mode] - reference_delta
        old_asc = model["asc"][mode]
        new_asc = old_asc + relative_delta
        model["asc"][mode] = new_asc

        print(f"{mode:<14}{target_shares[mode]:>10.4f}{simulated_shares.get(mode, float('nan')):>12.4f}{old_asc:>12.4f}{new_asc:>12.4f}")

    with open(COEFFICIENTS_PATH, "w", encoding = "utf-8") as f:
        json.dump(model, f, indent = 2)

    print(f"\nUpdated {COEFFICIENTS_PATH}")
    print("Re-run matsim_run with this file, regenerate modestats.csv, and re-run this script until each mode's simulated share is within tolerance of target.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("modestats_path")
    parser.add_argument("--iteration", type = int, default = None, help = "Defaults to the last row in modestats.csv")
    args = parser.parse_args()

    calibrate(args.modestats_path, args.iteration)
