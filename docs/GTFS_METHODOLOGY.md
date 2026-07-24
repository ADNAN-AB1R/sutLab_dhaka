# Dhaka Active Routes GTFS Feed — Methodology

Bangladesh has no official GTFS feed. This document describes how this feed was built, validated, and corrected, since none of that provenance is otherwise recorded anywhere.

## 1. Data sources

| Source | What it provided | Format |
|---|---|---|
| Hardcopy of a previous/unofficial GTFS | Original `routes.txt`, `stops.txt` (stop names + coordinates), `trips.txt` structure | Manually digitized into CSV/GTFS text files |
| DTCA "Traffic Survey by BRR Project" 2025 (`Active_Routes_Survey_Jan2025.shp`) | Ground-truth list of which routes are *currently operating*, with route number, length, permitted/actual vehicle counts, and route-corridor line geometry | Shapefile, custom "Bangladesh Transverse Mercator" projection |
| OpenStreetMap (Overpass API) | Real-world bus stop names and coordinates for routes that had no stop list of their own | Queried live, no local copy |
| OpenRouteService (ORS) Directions API | Real driving travel time between consecutive stops, used to build realistic `stop_times.txt` | Queried live, cached locally in `ors_cache.csv` |

## 2. Original feed construction

- `routes.txt` / `trips.txt` / `stops.txt`: digitized by hand from a hardcopy of a prior GTFS-like document. Each route got two directions × two service periods (`PH_Weekday_*` = peak hour, `NH_Weekday_*` = non-peak hour), all under a single `BMC` calendar service (all days, 2025-01-01 to 2025-12-31).
- `frequencies.txt`: every trip runs 05:00–23:00 with a 600s (10 min) headway — this is a frequency-based schedule, not a fixed-departure one.
- Original `stop_times.txt` (now archived as `stop_times_naive_backup.txt`): every stop-to-stop hop was given a **flat 5-minute gap** regardless of actual distance. This was a placeholder, not real timing data.

## 3. Realistic travel times via ORS (`ors_based_stop_times.py`)

To replace the naive 5-minute spacing, `ors_based_stop_times.py`:
1. Reads each trip's ordered stop list from `stops.txt` / `stop_times.txt`.
2. For each consecutive stop pair, requests a `driving-car` route from the OpenRouteService Directions API and takes its `duration`.
3. Applies a **1.3× bus-speed factor** (buses are slower than free-flow car traffic) plus a **20-second dwell time** per stop.
4. Accumulates these into `arrival_time`/`departure_time` (equal to each other — dwell is folded into the *following* hop, not modeled as a stop-level delay).
5. Caches every `(origin_lat, origin_lon, dest_lat, dest_lon) → duration` pair in `ors_cache.csv` so re-runs don't re-spend API calls.
6. On repeated API failure for a hop, silently substituted a 300-second fallback duration (this is what later caused the "fallback artifact" issue described below).

The result (`stop_times_ors.txt.txt` at the time) was later promoted to be the canonical `stop_times.txt`.

## 4. Cross-checking against the 2025 survey

The survey shapefile initially shipped with only its `.shp` file — no `.dbf` (attribute table: route numbers, names) and no `.prj` (coordinate system). Both were later supplied. Steps taken:

1. **Recovered the projection.** With no `.prj`, the coordinate system was reverse-engineered by matching a known stop's coordinates against the raw shapefile numbers, converging on a Transverse Mercator variant: `+proj=tmerc +lat_0=0 +lon_0=90 +k=0.9996 +x_0=500000 +y_0=-2000000 +ellps=evrst30`. The supplied `.prj` later confirmed this as "Bangladesh Transverse Mercator (BTM)."
2. **Matched routes by number**, not geometry, once `.dbf` was available: normalized `Route_No` (e.g. `A-101` → `A101`) against `routes.txt`'s `route_short_name`.
3. This produced an exact diff: **12 routes** in the survey but absent from `routes.txt` (A119, A181, A190, A202, A207, A220, A221, A222, A238, A362, A380, A416), and **1 route** in `routes.txt` not confirmed by the survey (A265, possibly discontinued).
4. A-202 was flagged as an anomaly: it has real, well-formed line geometry in the shapefile but `Length=0` and zero permitted/bus/minibus counts in the survey's own attribute table — likely an incomplete survey record, not a routing error.

## 5. Bugs found and fixed

| Issue | Root cause | Fix |
|---|---|---|
| A_141/A_142 route mix-up | The return trip of A_141 (`Sia_Masjid→Banasri`) was tagged `route_id=A_142` in `trips.txt`, giving A_141 only 1 direction and A_142 six trips instead of the standard four | Corrected `route_id` on both the PH and NH instances |
| "Mohammadpur Bus Stand" coordinate | Digitization error — the stop was recorded near Faridpur (~90km away) instead of Dhaka's Mohammadpur | Looked up the real location via OSM (`23.7569928, 90.3617359`) and corrected `stops.txt` |
| 7 routes with looping/duplicated stops (A_355, A_368, A_377, A_406, A_412, A_460, A_475) | The same physical stop (identical coordinates) appeared 2-3 times under different `stop_id`s within a single trip, producing impossible "revisit" patterns and multi-hour phantom hops | Systematically scanned every route for stops sharing coordinates within a trip; kept the first occurrence, dropped the repeats, renumbered `stop_sequence` |
| A_414 / A_465 | Stops far apart geographically with no exact duplicates — likely multiple route variants concatenated during digitization | **Not auto-fixed** (no reliable way to infer true order); flagged for manual review |
| 3 hops using a silent 300s ORS fallback (A_271, A_285, A_381) | Original script's retry-then-guess fallback when the API failed | Re-fetched real ORS durations (which happened to be ~300s anyway — the fallback was coincidentally close to reality) |
| Two conflicting `stop_times` files | Naive fixed-interval version and ORS-based version both present | Promoted the ORS-based file to the canonical `stop_times.txt`; archived the naive one as `stop_times_naive_backup.txt`; deleted the byte-identical duplicate `stop_times.txt.csv` |
| Plaintext API keys | ORS key hardcoded in `ors_based_stop_times.py`, plus duplicate copies in loose `ors api` / `tomtom api` files | Script now reads `os.environ['ORS_API_KEY']`; loose key files deleted. **Keys should still be rotated** since they were exposed in plaintext |

All fixes were re-validated afterward: zero duplicate-location stops, zero non-monotonic `stop_times`, full referential integrity, and every route has exactly 4 trips.

## 6. Adding the 7 missing routes (A119, A202, A207, A220, A221, A222, A362)

The survey only provides route *geometry*, not a stop list, so stops had to be sourced independently:

1. **Extract each route's line** from the (now correctly projected) survey shapefile.
2. **Sample points every ~500m–1.2km** along the line and query the OSM Overpass API (`highway=bus_stop`, `amenity=bus_station`, `public_transport=platform`) within ~200-250m of each sample point.
3. **Filter and cluster**: keep only OSM stops within ~250m of the survey line (to reject stops belonging to nearby parallel roads), then merge stops within ~450m of each other into one, preferring named stops over unnamed nodes.
4. **Order stops** by projecting each onto the survey line and sorting by distance-along-line.
5. **Flag gaps** — any stretch >1.5km with no OSM-tagged stop was left unfilled rather than inventing a stop name/location (per explicit instruction). A-207 in particular has poor OSM coverage and needs manual stop additions at both ends.
6. **Write GTFS scaffolding**: `routes.txt`, `stops.txt` (`stop_id` pattern `A<num>_B<n>`), `trips.txt` (4 trips per route, same PH/NH × direction convention as the rest of the feed), `frequencies.txt` (same 05:00–23:00 / 600s headway convention).
7. **Generate `stop_times.txt`** for the 28 new trips using the same ORS methodology as the original feed (`generate_new_route_stop_times.py`, a scoped variant of `ors_based_stop_times.py` that only processes the new routes' trips and appends to the existing cache/file rather than overwriting).

## 7. Known remaining limitations

- **A_181, A_190, A_238, A_380, A_416** are still in the survey but not in `routes.txt` (out of scope for this round).
- **A_265** is in `routes.txt` but not confirmed by the 2025 survey — may be discontinued.
- **A-207** has only 7 stops with large uncovered gaps at both ends of the line; needs manual stop input.
- **A_414 / A_465** have geographically incoherent stop ordering that wasn't auto-correctable.
- **A-202**'s survey record has a `Length=0` / zero-vehicle-count anomaly in the source attribute table, worth checking against the original hardcopy survey.
- 20 stop-to-stop hops across the feed exceed 1 hour — these were individually checked and correspond to genuine long-distance/inter-district segments (e.g. to Paturia, Manikganj, Chandra, Mawa), not data errors.

## 8. File manifest

**Feed files (zip these for a valid GTFS):**
`agency.txt`, `stops.txt`, `routes.txt`, `trips.txt`, `stop_times.txt`, `calendar.txt`, `frequencies.txt`

**Supporting/reference files (not part of the feed):**
- `Active_Routes_Survey_Jan2025.*` — source survey shapefile
- `ors_based_stop_times.py` — original full-feed ORS time generator
- `generate_new_route_stop_times.py` — scoped generator for the 7 new routes
- `ors_cache.csv` — cached ORS hop durations (reused across both scripts)
- `stop_times_naive_backup.txt` — archived pre-ORS placeholder timings
- `backup_travel_times.csv` — unrelated earlier working file
- `gtfs_for_pri.zip` — an earlier packaged snapshot; **stale**, predates all fixes in this document
