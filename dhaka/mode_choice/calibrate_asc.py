import argparse
import json
import os

import pandas as pd
import numpy as np

"""
Standard sample-enumeration ASC calibration (Train, 2009): adjusts
dhaka_mode_parameters.json's `asc` values (from Hoque's MSc thesis, Table
4.1) so the modes DiscreteModeChoice actually converges to in a real MATSim
run (matsim_run's simulated modestats.csv) match our own HTS-observed
target shares (the same weighted mode distribution the earlier HTS-fitted
model used, kept here as the local calibration target). This is standard
"transfer + local calibration" practice: the thesis's estimated behavioral
sensitivities (beta_duration, beta_fare) are kept as-is, only the ASCs
(which absorb unmeasured local/contextual factors) are re-anchored to a
local aggregate mode-share target - not a contradiction of "using the
published model," the same thing would be done for a model transferred
from a different city entirely.

For each mode: delta = ln(target_share / simulated_share), then every
delta is re-based relative to the reference mode's own delta (so the
reference mode's implicit ASC=0 stays fixed at 0 - only relative utility
differences matter for a logit model, shifting every ASC by the same
constant changes nothing).

This is ONE calibration step, not a solver - MATSim's simulated response
to a new set of ASCs is itself non-linear (route/congestion feedback), so
recalibration is iterative: run matsim_run with the newly-written
dhaka_mode_parameters.json for enough iterations to reconverge (see
matsim_run/README.md), regenerate modestats.csv, run this script again
pointing at the new file, repeat until each mode's simulated share is
within tolerance of its target.

Target shares come from the pipeline's own output trips file
(`mode_hts_donor` column), NOT from a standalone estimation parquet - see
compute_target_shares() for why that matters.

Usage:
    python -m dhaka.mode_choice.calibrate_asc path/to/modestats.csv [--iteration N] [--trips path/to/trips.csv]
"""

COEFFICIENTS_PATH = os.path.join(os.path.dirname(__file__), "dhaka_mode_parameters.json")
DEFAULT_TRIPS_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "output", "dhaka_1pct_trips.csv"
)


def compute_target_shares(modes, trips_path):
    """Target = the HTS-observed mode of each synthetic trip's matched donor
    (`mode_hts_donor`, written by dhaka/synthesis/population/mode_choice.py).

    Deliberately NOT read from a standalone estimation parquet: that file is
    a static artifact that silently goes stale whenever MODES_MAP in
    dhaka/data/hts/entd/cleaned.py changes - which is exactly what happened
    when `motorcycle` was split out of `car` (the parquet still had only the
    old 6 modes, so a motorcycle target simply did not exist). Reading
    mode_hts_donor from the pipeline's own output instead means the target
    can never drift out of sync with the mode definitions, and it is already
    restricted to the study-area synthetic population rather than the raw
    nationwide survey."""
    df = pd.read_csv(trips_path, sep = ";", usecols = ["mode_hts_donor"])
    counts = df["mode_hts_donor"].value_counts()
    counts = counts[counts.index.isin(modes)]
    shares = counts / counts.sum()
    return { mode: float(shares.get(mode, 0.0)) for mode in modes }


def read_simulated_shares(modestats_path, iteration, modes):
    df = pd.read_csv(modestats_path, sep = ";")
    row = df.iloc[-1] if iteration is None else df[df["iteration"] == iteration].iloc[0]
    return { mode: float(row[mode]) for mode in modes if mode in row }


def calibrate(modestats_path, iteration, trips_path):
    with open(COEFFICIENTS_PATH, "r", encoding = "utf-8") as f:
        model = json.load(f)

    modes = model["modes"]
    reference_mode = model["reference_mode"]

    target_shares = compute_target_shares(modes, trips_path)
    simulated_shares = read_simulated_shares(modestats_path, iteration, modes)

    delta = {
        mode: np.log(target_shares[mode] / simulated_shares[mode])
        for mode in modes
        if simulated_shares.get(mode, 0.0) > 0 and target_shares.get(mode, 0.0) > 0
    }

    if reference_mode not in delta:
        raise RuntimeError(
            "Reference mode '%s' has no usable target/simulated share, so the other ASCs "
            "cannot be re-based against it. Target=%.4f, simulated=%.4f." % (
                reference_mode, target_shares.get(reference_mode, 0.0),
                simulated_shares.get(reference_mode, 0.0)))

    reference_delta = delta[reference_mode]
    skipped = []

    print(f"{'mode':<14}{'target':>10}{'simulated':>12}{'old_asc':>12}{'new_asc':>12}")
    for mode in modes:
        if mode == reference_mode:
            print(f"{mode:<14}{target_shares[mode]:>10.4f}{simulated_shares.get(mode, float('nan')):>12.4f}{'0 (ref)':>12}{'0 (ref)':>12}")
            continue

        old_asc = model["asc"][mode]

        # A mode with a zero target or zero simulated share has no defined
        # log-ratio - skip it rather than crashing (this is what a newly
        # added mode looks like before it appears in both sources).
        if mode not in delta:
            skipped.append(mode)
            print(f"{mode:<14}{target_shares.get(mode, 0.0):>10.4f}{simulated_shares.get(mode, float('nan')):>12.4f}{old_asc:>12.4f}{'skipped':>12}")
            continue

        new_asc = old_asc + (delta[mode] - reference_delta)
        model["asc"][mode] = new_asc

        print(f"{mode:<14}{target_shares[mode]:>10.4f}{simulated_shares.get(mode, float('nan')):>12.4f}{old_asc:>12.4f}{new_asc:>12.4f}")

    with open(COEFFICIENTS_PATH, "w", encoding = "utf-8") as f:
        json.dump(model, f, indent = 2)

    print(f"\nUpdated {COEFFICIENTS_PATH}")
    if skipped:
        print("SKIPPED (zero target or zero simulated share, ASC left unchanged): %s" % ", ".join(skipped))
    print("Re-run matsim_run with this file, regenerate modestats.csv, and re-run this script until each mode's simulated share is within tolerance of target.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("modestats_path")
    parser.add_argument("--iteration", type = int, default = None, help = "Defaults to the last row in modestats.csv")
    parser.add_argument("--trips", default = DEFAULT_TRIPS_PATH,
        help = "Trips CSV supplying the HTS-observed target shares via its mode_hts_donor column (default: output/dhaka_1pct_trips.csv)")
    args = parser.parse_args()

    calibrate(args.modestats_path, args.iteration, args.trips)
