import argparse
import json
import os
import collections

import numpy as np
import pandas as pd

"""
Calibrates dhaka_mode_parameters.json's "fare_assumptions" against the DTCA
survey's REPORTED trip costs (q55_total_cost, 98.5% coverage), replacing
assumptions that were demonstrably wrong by factors of 2-5.

Why this was needed - measured against reported costs at the median trip:
    car          observed 100 BDT   model predicted 19.5   5.1x too low
    motorcycle   observed  25 BDT   model predicted  6.7   3.6x too low
    pt           observed  20 BDT   model predicted 10.0   2.0x too low
    paratransit  observed  15 BDT   model predicted 59.0   3.9x too HIGH
    rickshaw     observed  20 BDT   model predicted 13.4   1.5x too low
The two large errors have identifiable causes, not just bad numbers:
  - car/motorcycle were modelled as PRIVATE FUEL COST ONLY
    (122 BDT/l / 12 km/l = 10.2 BDT/km). But those buckets are substantially
    ride-hailing (Uber/Pathao car and bike) plus, for private cars in Dhaka,
    a hired driver, parking and maintenance - all of which a respondent
    counts as the cost of the trip.
  - paratransit used the official BRTA reserved-CNG meter rate (40 BDT for
    the first 2 km, 12 BDT/km after). Most CNG trips in the survey's bucket
    are SHARED and charged per seat, so the effective fare is far below the
    reserved meter. The file's own note already said drivers ignore the
    meter; the data confirms it, in the cheaper direction.

WHAT THIS CALIBRATION CAN AND CANNOT DO
Reported cost is reliable (98.5% coverage, directly observed). Distance is
NOT: 66.3% of survey trips are intra-ward, and for those the ward-sampled
distance is the gap between two random points in one polygon, uncorrelated
with the real trip (r = 0.06-0.13; see build_estimation_dataset.py). Binning
reported cost by that distance produces an almost FLAT cost profile for every
mode - an artifact of the noise, not evidence that fares do not scale with
distance.

Consequence: only the LEVEL of each fare function is estimated here. The
SHAPE (per-km, flag-fall, minimum fare) is kept from the external tariff
sources, because the data cannot inform a slope.

CRITICAL CAVEAT on interpreting the output: the calibrated rates are
EFFECTIVE RATES ON THIS MODEL'S DISTANCE SCALE, not real Dhaka tariffs. They
are valid because the survey and the synthetic population share the same
ward-sampled distance scale (synthetic median 1920 m vs HTS donor 1617 m), so
the distance bias cancels when reproducing cost levels. Do NOT quote them as
Dhaka fares, and DO re-run this script if the distance construction is ever
fixed - the rates would then be wrong by whatever factor the scale moved.

Usage:
    python -m dhaka.mode_choice.calibrate_fares [--dry-run]
"""

PARAMETERS_PATH = os.path.join(os.path.dirname(__file__), "dhaka_mode_parameters.json")
DATASET_PATH = os.path.join(os.path.dirname(__file__), "estimation_dataset.parquet")

# Modes whose fare is structurally zero - not calibrated.
ZERO_FARE_MODES = ["walk", "bike"]


def weighted_quantile(values, weights, quantile):
    order = np.argsort(values)
    values = np.asarray(values)[order]
    weights = np.asarray(weights)[order]
    cumulative = np.cumsum(weights) / np.sum(weights)
    return float(values[np.searchsorted(cumulative, quantile)])


def observed_targets(dataset_path):
    """Trip-weighted median reported cost per mode, and the median
    ward-sampled distance of that mode's OWN users - the pair the fare
    function has to reproduce."""
    df = pd.read_parquet(dataset_path)
    df = df[df["mode"].notna() & (df["mode"] != "other")]
    df = df[df["reported_cost_bdt"].notna() & df["distance_m"].notna() & (df["distance_m"] > 0)]

    targets = {}
    for mode, group in df.groupby("mode"):
        weights = group["trip_weight"].to_numpy()
        targets[mode] = {
            "n": int(len(group)),
            "median_cost_bdt": weighted_quantile(group["reported_cost_bdt"], weights, 0.5),
            "median_distance_m": weighted_quantile(group["distance_m"], weights, 0.5),
            "share_zero_cost": float((group["reported_cost_bdt"] == 0).mean()),
        }
    return targets


def calibrate(targets):
    """Per-mode fare parameters reproducing each observed median cost at that
    mode's observed median distance, keeping each structure's shape."""
    fares = {}

    # car / motorcycle: a single effective BDT/km. Replaces the old
    # fuel_price / km_per_liter construction, which structurally cannot
    # represent ride-hail fares or a driver's wage - it is private fuel burn
    # by definition, and understated the observed cost 3.6-5.1x.
    for mode in ("car", "motorcycle"):
        target = targets[mode]
        bdt_per_km = target["median_cost_bdt"] / (target["median_distance_m"] / 1000.0)
        fares[mode] = { "bdt_per_km": round(bdt_per_km, 2) }

    # pt: keep minimum-fare + per-km shape (real BRTA bus structure). Solve
    # the per-km rate with the minimum fare held at a plausible 15 BDT.
    pt = targets["pt"]
    pt_minimum = 15.0
    pt_distance_km = pt["median_distance_m"] / 1000.0
    pt_rate = max(pt["median_cost_bdt"], pt_minimum) / pt_distance_km
    fares["pt"] = {
        "bdt_per_km": round(pt_rate, 2),
        "minimum_fare_bdt": pt_minimum,
    }

    # paratransit: keep the flag-fall shape but re-level it for SHARED CNG
    # (per-seat), not the reserved meter. The BRTA per-km rate cannot be kept
    # as-is: at 12 BDT/km the structure already exceeds the observed median
    # before any flag fall is added, which would require a negative flag fall.
    paratransit = targets["paratransit"]
    flag_fall_km = 2.0
    flag_fall_bdt = 8.0
    remaining_km = max(paratransit["median_distance_m"] / 1000.0 - flag_fall_km, 0.1)
    paratransit_rate = (paratransit["median_cost_bdt"] - flag_fall_bdt) / remaining_km
    fares["paratransit"] = {
        "flag_fall_bdt": flag_fall_bdt,
        "flag_fall_km": flag_fall_km,
        "bdt_per_km_after": round(max(paratransit_rate, 0.5), 2),
    }

    # rickshaw: pure per-km, as before.
    rickshaw = targets["rickshaw"]
    fares["rickshaw"] = {
        "bdt_per_km": round(
            rickshaw["median_cost_bdt"] / (rickshaw["median_distance_m"] / 1000.0), 2)
    }

    for mode in ZERO_FARE_MODES:
        fares[mode] = { "bdt_per_km": 0 }

    return fares


def predicted_cost(mode, fares, distance_m):
    distance_km = distance_m / 1000.0
    spec = fares[mode]

    if mode in ("car", "motorcycle", "rickshaw") or mode in ZERO_FARE_MODES:
        return spec["bdt_per_km"] * distance_km
    if mode == "pt":
        return max(spec["minimum_fare_bdt"], spec["bdt_per_km"] * distance_km)
    if mode == "paratransit":
        if distance_km <= spec["flag_fall_km"]:
            return spec["flag_fall_bdt"]
        return spec["flag_fall_bdt"] + spec["bdt_per_km_after"] * (distance_km - spec["flag_fall_km"])
    raise ValueError(mode)


SOURCES = {
    "car": ("EFFECTIVE rate on this model's distance scale, calibrated to the DTCA survey's "
            "reported trip costs - NOT a real Dhaka tariff and NOT fuel cost. Replaces a "
            "fuel-only construction (122 BDT/l / 12 km/l = 10.2 BDT/km) that understated "
            "observed cost 5.1x, because this bucket is substantially ride-hailing plus, for "
            "private cars, a hired driver/parking/maintenance that respondents count as trip "
            "cost. Re-run calibrate_fares.py if the distance construction changes."),
    "motorcycle": ("EFFECTIVE rate on this model's distance scale, calibrated to reported costs "
            "- NOT fuel cost. The old 122 BDT/l / 35 km/l = 3.5 BDT/km understated observed "
            "cost 3.6x; the bucket includes Pathao/Uber ride-share bike fares."),
    "pt": ("Minimum fare + per-km, the real BRTA bus structure, but the per-km rate is an "
           "EFFECTIVE rate on this model's distance scale calibrated to reported costs (the "
           "regulated ~2.5 BDT/km with a 10 BDT minimum predicted 10 BDT against 20 observed). "
           "Bucket is 76.5% Bus at a 20 BDT reported median, so that target is solid; also "
           "Laguna/Tempu 13.1% at 15 BDT, Mini Bus 4.8% at 30, Metro Rail 0.5% at 50."),
    "paratransit": ("Re-levelled for SHARED per-seat service, NOT the official BRTA reserved "
            "meter rate of 40 BDT for the first 2 km plus 12 BDT/km, which overstated observed "
            "cost 3.9x. Composition evidence for why (weighted shares of this bucket, with "
            "reported median cost): 3-wheeler Auto SHARED 63.1% at 10 BDT, Auto reserved 16.2% "
            "at 20 BDT, CNG shared 13.4% at 30 BDT, CNG RESERVED only 7.2% at 150 BDT. The "
            "bucket is dominated by cheap shared autos, so the reserved meter was the wrong "
            "tariff entirely. The shallow slope is also behaviourally right, not just a fitting "
            "artifact: shared route-based services charge flat or stepped fares rather than "
            "scaling linearly with distance."),
    "rickshaw": ("EFFECTIVE rate on this model's distance scale, calibrated to reported costs. "
            "Supersedes the previous UNVERIFIED PLACEHOLDER of 10 BDT/km - which the data shows "
            "was close (13.4 predicted vs 20 observed), the smallest error of any mode."),
    "bike": "Zero-fare (thesis, and 94.9% of survey bike trips report zero cost).",
    "walk": "Zero-fare (thesis, and 94.4% of survey walk trips report zero cost).",
}


def main(dry_run):
    targets = observed_targets(DATASET_PATH)
    fares = calibrate(targets)

    with open(PARAMETERS_PATH, "r", encoding = "utf-8") as f:
        model = json.load(f, object_pairs_hook = collections.OrderedDict)

    print("%-13s %7s %11s %11s %13s %13s" % (
        "mode", "n", "obs_cost", "obs_dist_m", "old_predicted", "new_predicted"))

    from dhaka.synthesis.population.mode_choice import compute_fare_bdt
    old_fares = model["fare_assumptions"]

    for mode in model["modes"]:
        target = targets.get(mode)
        if target is None:
            continue
        distance = target["median_distance_m"]
        try:
            old = float(np.atleast_1d(compute_fare_bdt(mode, np.array([distance]), old_fares))[0])
        except Exception:
            old = float("nan")
        new = predicted_cost(mode, fares, distance)
        print("%-13s %7d %11.0f %11.0f %13.1f %13.1f" % (
            mode, target["n"], target["median_cost_bdt"], distance, old, new))

    if dry_run:
        print("\n--dry-run: %s NOT modified." % PARAMETERS_PATH)
        return

    for mode, spec in fares.items():
        spec["source"] = SOURCES[mode]
    fares["note"] = (
        "Calibrated by dhaka/mode_choice/calibrate_fares.py against the DTCA survey's reported "
        "trip costs (q55_total_cost). Only the LEVEL of each fare function is data-estimated; "
        "the SHAPE is retained from external tariff sources because ward-sampled distance is "
        "too noisy to identify a slope (66.3% of survey trips are intra-ward, giving an almost "
        "flat observed cost-vs-distance profile that is an artifact, not behaviour). Every rate "
        "here is an EFFECTIVE rate on this model's ward-sampled distance scale - valid because "
        "the survey and the synthetic population share that scale, but NOT a real Dhaka tariff. "
        "Re-run the script if the distance construction ever changes.")
    model["fare_assumptions"] = fares

    with open(PARAMETERS_PATH, "w", encoding = "utf-8") as f:
        json.dump(model, f, indent = 2, ensure_ascii = False)

    print("\nUpdated %s" % PARAMETERS_PATH)
    print("car/motorcycle now use bdt_per_km - the Java cost models and the Python")
    print("compute_fare_bdt must read that instead of fuel_price/km_per_liter.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action = "store_true")
    main(parser.parse_args().dry_run)
