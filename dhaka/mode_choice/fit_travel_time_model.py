import argparse
import collections
import gzip
import json
import math
import os
import re

import numpy as np

"""
Fits dhaka_mode_parameters.json's "travel_time_model" (door-to-door time =
overhead_min + min_per_km * straight-line km, per mode) to the PLANNED trip
times in a MATSim plans file - the times DiscreteModeChoice actually uses.

Why planned, not executed: the travel-time model exists so that the offline
pre-calibration (precalibrate_asc.py) predicts what the in-simulation mode
choice will do. Mode choice (DhakaTripEstimator) scores each alternative on
the ROUTER's planned leg times, so the proxy has to reproduce those. The
previous fit used EXECUTED times from an older run (before the speed-capped
routing and distance fixes) and assumed a 1-2 km rickshaw trip takes 19.1 min
when mode choice sees 10.8 min; car 9.1 vs 5.0; pt 22.6 vs 17.1. Pre-calibration
therefore set constants for a world where every vehicle mode was far slower
than the simulation offers, and walk (teleported, where planned = executed and
the proxy was right) drained into rickshaw from the very first replanning -
rickshaw 29.6% -> 33.8%, walk 32.7% -> 30.2% in one iteration.

A trip is the run of legs between two consecutive main (non-"interaction")
activities; its main mode is the highest-priority mode among its legs (so a pt
trip's access walks do not make it a walk trip), and its distance is the
straight line between the two activities. Walk fitting to 0 overhead and
~19.7 min/km (1.1 m/s over a 1.3 detour factor) is the built-in sanity check:
walk is teleported, so its planned and executed times are identical.

Usage:
    python -m dhaka.mode_choice.fit_travel_time_model output/simulation_output/ITERS/it.0/0.plans.xml.gz [--dry-run]
"""

PARAMETERS_PATH = os.path.join(os.path.dirname(__file__), "dhaka_mode_parameters.json")

# Main-mode priority within a trip: the first of these present among its legs.
MAIN_MODE_PRIORITY = ["pt", "car", "motorcycle", "paratransit", "rickshaw", "bike", "walk"]
MINIMUM_TRIPS = 200
TRIM_QUANTILE = 0.99

ACTIVITY_PATTERN = re.compile(r'<activity type="([^"]+)"[^>]*x="([-0-9.eE]+)" y="([-0-9.eE]+)"')
LEG_PATTERN = re.compile(r'<leg mode="([^"]+)"[^>]*trav_time="([0-9:.]+)"')


def seconds(text):
    parts = text.split(":")
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])


def trips_from_plan(elements):
    """(main mode, straight-line metres, planned seconds) per trip."""
    main = [i for i, e in enumerate(elements) if e[0] == "act" and not e[1].endswith("interaction")]
    for a, b in zip(main, main[1:]):
        legs = [e for e in elements[a + 1:b] if e[0] == "leg"]
        if not legs:
            continue
        modes = { leg[1] for leg in legs }
        main_mode = next((m for m in MAIN_MODE_PRIORITY if m in modes), None)
        if main_mode is None:
            continue
        distance = math.hypot(elements[b][2] - elements[a][2], elements[b][3] - elements[a][3])
        yield main_mode, distance, sum(leg[2] for leg in legs)


def read_planned_trips(plans_path):
    trips = collections.defaultdict(list)
    selected = False
    elements = []
    with gzip.open(plans_path, "rt", encoding = "utf-8") as f:
        for line in f:
            if "<plan " in line:
                selected = 'selected="yes"' in line
                elements = []
                continue
            if "</plan>" in line:
                if selected:
                    for mode, distance, time in trips_from_plan(elements):
                        trips[mode].append((distance, time))
                selected = False
                continue
            if not selected:
                continue
            m = ACTIVITY_PATTERN.search(line)
            if m:
                elements.append(("act", m.group(1), float(m.group(2)), float(m.group(3))))
                continue
            m = LEG_PATTERN.search(line)
            if m:
                elements.append(("leg", m.group(1), seconds(m.group(2))))
    return trips


def main(plans_path, dry_run):
    trips = read_planned_trips(plans_path)

    with open(PARAMETERS_PATH, "r", encoding = "utf-8") as f:
        model = json.load(f, object_pairs_hook = collections.OrderedDict)
    ttm = model["travel_time_model"]

    print("%-12s %7s %13s %11s %13s %13s" % (
        "mode", "trips", "overhead_min", "min_per_km", "old_overhead", "old_min_km"))
    for mode in model["modes"]:
        data = np.array(trips.get(mode, []))
        old = ttm[mode]
        if len(data) < MINIMUM_TRIPS:
            print("%-12s %7d   (too few trips - kept previous fit)" % (mode, len(data)))
            continue
        km = data[:, 0] / 1000.0
        minutes = data[:, 1] / 60.0
        keep = (km > 0) & (minutes > 0) & (minutes <= np.quantile(minutes, TRIM_QUANTILE))
        slope, intercept = np.polyfit(km[keep], minutes[keep], 1)
        intercept = max(float(intercept), 0.0)
        print("%-12s %7d %13.2f %11.2f %13.2f %13.2f" % (
            mode, len(data), intercept, slope, old["overhead_min"], old["min_per_km"]))
        if not dry_run:
            old["overhead_min"] = round(intercept, 2)
            old["min_per_km"] = round(float(slope), 2)

    if dry_run:
        print("\n--dry-run: %s NOT modified." % PARAMETERS_PATH)
        return

    ttm["note"] = (
        "Door-to-door travel-time proxy per mode: time_min = overhead_min + min_per_km * "
        "straight-line km. Used ONLY on the Python side (synthesis seed assignment and "
        "precalibrate_asc.py), which have no routing. Fitted by "
        "dhaka/mode_choice/fit_travel_time_model.py to the PLANNED door-to-door trip times "
        "(router leg times summed over every leg of the trip) in %s - the times "
        "DiscreteModeChoice itself scores alternatives on, so that pre-calibration predicts "
        "in-simulation mode choice. Fitting to EXECUTED times from an older run previously "
        "overstated every vehicle mode (1-2 km rickshaw 19.1 min assumed vs 10.8 planned) and "
        "drove walk into rickshaw from the first replanning. Re-fit after any change to "
        "routing, the network, the population or congestion." % os.path.relpath(plans_path))
    with open(PARAMETERS_PATH, "w", encoding = "utf-8") as f:
        json.dump(model, f, indent = 2, ensure_ascii = False)
    print("\nUpdated %s - now re-run precalibrate_asc.py." % PARAMETERS_PATH)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("plans_path")
    parser.add_argument("--dry-run", action = "store_true")
    args = parser.parse_args()
    main(args.plans_path, args.dry_run)
