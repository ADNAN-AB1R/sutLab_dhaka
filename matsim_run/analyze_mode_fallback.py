"""
Corrects MATSim's reported mode shares for the fallback-to-walk behavior
built into SwissRailRaptorRoutingModule: when the transit router can't find
a viable pt route for a TRIP, it silently substitutes a direct teleported
walk instead - with no distance or time cap. This is intentional MATSim
design (see matsim_run/README.md's "Known limitations" for the full
investigation), not a bug to patch - the actual gap is that nothing
downstream distinguishes "genuinely walked" from "transit routing failed,
walked instead", so naive mode-share statistics (modestats.csv) silently
overcount walk and undercount pt.

IMPORTANT - this must be done at the TRIP level, not the leg level. A leg's
`routingMode` attribute marks which requested TRIP it belongs to, not
whether that individual leg is itself a failure: a successful multi-leg pt
journey (walk-access -> pt-ride -> walk-egress) legitimately has
routingMode="pt" on its walk access/egress legs too - that's normal
structure, not a failure. Naively flagging every routingMode!=mode leg as a
"failure" wrongly counts legitimate access/egress walking as corrupted
data. The correct unit of analysis is the TRIP (the full sequence of legs
between two real activities): a pt-requested trip is a genuine ROUTING
FAILURE only if NONE of its legs actually executed as mode="pt" - i.e. the
whole journey collapsed to walking (or another mode) instead of ever
boarding a transit vehicle.

Usage:
    python matsim_run/analyze_mode_fallback.py <path-to-N.plans.xml.gz>
"""
import sys
import gzip
import statistics
from collections import Counter
from lxml import etree


def get_routing_mode(leg):
    for attr in leg.iter("attribute"):
        if attr.get("name") == "routingMode":
            return attr.text
    return leg.get("mode")


def analyze(plans_path):
    naive_mode_counts = Counter()          # per-leg executed mode (what modestats.csv effectively reflects)
    trip_requested_counts = Counter()      # per-trip requested mode
    trip_outcome_counts = Counter()        # (requested_mode, "success"/"failed") per trip
    failed_trip_distances = []

    context = etree.iterparse(gzip.open(plans_path, "rb"), events=("end",), tag=("leg", "activity", "person"))

    current_trip_legs = []  # legs since the last real (non "pt interaction") activity

    def flush_trip():
        if not current_trip_legs:
            return
        requested = get_routing_mode(current_trip_legs[0])
        executed_modes = {leg.get("mode") for leg in current_trip_legs}
        trip_requested_counts[requested] += 1

        if requested in executed_modes:
            trip_outcome_counts[(requested, "success")] += 1
        else:
            trip_outcome_counts[(requested, "failed")] += 1
            total_distance = 0.0
            for leg in current_trip_legs:
                route = leg.find("route")
                if route is not None and route.get("distance"):
                    total_distance += float(route.get("distance"))
            if requested == "pt":
                failed_trip_distances.append(total_distance)

        for leg in current_trip_legs:
            leg.clear()

    for event, elem in context:
        if event == "end" and elem.tag == "leg":
            naive_mode_counts[elem.get("mode")] += 1
            current_trip_legs.append(elem)
        elif event == "end" and elem.tag == "activity":
            # "pt interaction" is a stage activity inserted between transfer
            # legs of a single multi-leg trip - only a REAL activity (home,
            # work, shopping, ...) actually ends a trip.
            is_interaction = elem.get("type") == "pt interaction"
            if not is_interaction:
                flush_trip()
                current_trip_legs = []
            elem.clear()
        elif event == "end" and elem.tag == "person":
            flush_trip()  # in case a plan ends mid-trip (shouldn't normally happen)
            current_trip_legs = []
            elem.clear()

    total_legs = sum(naive_mode_counts.values())
    total_trips = sum(trip_requested_counts.values())

    print(f"Total legs: {total_legs:,}   Total trips: {total_trips:,}\n")

    print("=== NAIVE per-LEG mode shares (what modestats.csv reflects) ===")
    for mode, count in naive_mode_counts.most_common():
        print(f"  {mode:<15} {count:>8,}  ({count/total_legs*100:5.1f}%)")

    print("\n=== Per-TRIP requested mode (routingMode of the trip's first leg) ===")
    for mode, count in trip_requested_counts.most_common():
        print(f"  {mode:<15} {count:>8,}  ({count/total_trips*100:5.1f}%)")

    print("\n=== Trip-level success/failure (did the trip ever actually execute its requested mode?) ===")
    for mode in trip_requested_counts:
        succ = trip_outcome_counts.get((mode, "success"), 0)
        fail = trip_outcome_counts.get((mode, "failed"), 0)
        total = succ + fail
        print(f"  {mode:<15} success={succ:>7,} ({succ/total*100:5.1f}%)   failed={fail:>7,} ({fail/total*100:5.1f}%)")

    if failed_trip_distances:
        print(f"\n=== Failed pt trips: distance of the substituted direct-walk journey ===")
        print(f"  n={len(failed_trip_distances):,}")
        print(f"  min={min(failed_trip_distances):.0f}m  "
              f"median={statistics.median(failed_trip_distances):.0f}m  "
              f"mean={statistics.mean(failed_trip_distances):.0f}m  "
              f"max={max(failed_trip_distances):.0f}m")
        implausible = sum(1 for d in failed_trip_distances if d > 3000)
        print(f"  {implausible:,} ({implausible/len(failed_trip_distances)*100:.1f}%) exceed 3km - "
              f"not plausible as a genuine single walking trip")

    print("\n=== Corrected trip-level mode shares (failed pt trips shown separately, not folded into walk) ===")
    corrected = Counter()
    for mode in trip_requested_counts:
        succ = trip_outcome_counts.get((mode, "success"), 0)
        corrected[mode] += succ
    failed_pt = trip_outcome_counts.get(("pt", "failed"), 0)
    corrected["walk (genuine)"] = corrected.pop("walk", 0)
    corrected["pt (failed -> forced walk)"] = failed_pt
    for mode, count in corrected.most_common():
        print(f"  {mode:<28} {count:>8,}  ({count/total_trips*100:5.1f}%)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python analyze_mode_fallback.py <path-to-N.plans.xml.gz>")
        sys.exit(1)
    analyze(sys.argv[1])
