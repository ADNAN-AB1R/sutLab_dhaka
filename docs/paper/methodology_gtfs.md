# Methodology: Constructing a GTFS Transit Feed for Dhaka

This document provides publication-ready methodology prose covering the transport
*supply* side of the Dhaka scenario — specifically, the construction of a GTFS
(General Transit Feed Specification) feed for the city's bus network, a step that has
no counterpart in the São Paulo paper (whose supply side was pre-existing and not
described in detail) and represents a genuinely novel contribution of this work: to the
authors' knowledge, no official machine-readable transit feed exists for Dhaka's bus
system, and this pipeline had to construct one from partial, non-standard sources
before it could be consumed by MATSim (via `pt2matsim`). Source material is
`docs/GTFS_METHODOLOGY.md`; re-verify specific counts against that file if it has been
updated since this document was written.

---

## 1. Motivation and data sources

Bangladesh has no official GTFS feed for any of its cities. Building one for Dhaka's
bus network was therefore a prerequisite for producing a MATSim-ready transit supply,
rather than an optional data-quality step. Four sources were combined, each supplying a
different piece of the final feed and none sufficient alone:

| Source | Contribution | Format |
|---|---|---|
| A prior, unofficial GTFS-like document (hardcopy) | Initial route/trip/stop structure (`routes.txt`, `stops.txt` stop names and coordinates) | Manually digitized into GTFS text files |
| DTCA "Traffic Survey by BRR Project" (January 2025) | Ground-truth list of currently operating routes, with route number, corridor length, and permitted/actual vehicle counts, plus route-corridor line geometry | Shapefile (custom Bangladesh Transverse Mercator projection) |
| OpenStreetMap (Overpass API) | Real-world bus stop names and coordinates for routes lacking a digitized stop list | Queried live |
| OpenRouteService (ORS) Directions API | Realistic driving travel times between consecutive stops, used to construct schedule timings | Queried live, cached locally |

## 2. Initial feed construction

The starting feed (`routes.txt`, `trips.txt`, `stops.txt`) was digitized by hand from a
hardcopy of a prior, non-machine-readable transit document. Each route was encoded with
two directions and two service periods — peak-hour and non-peak-hour weekday service —
under a single calendar service spanning the full 2025 calendar year. Service frequency
follows a frequency-based (headway) model rather than a fixed-departure-time model:
every trip operates 05:00–23:00 at a 10-minute headway (`frequencies.txt`), consistent
with how Dhaka bus services are actually operated (informally scheduled, frequency-based
rather than timetabled). The initial `stop_times.txt` used a placeholder flat five-minute
inter-stop spacing, irrespective of actual inter-stop distance — a necessary starting
point given the absence of any timing data in the source document, but not intended as
the feed's final timing data (see §3).

## 3. Deriving realistic inter-stop travel times

Placeholder fixed-interval timings were replaced with distance- and mode-informed
estimates using the OpenRouteService Directions API. For each consecutive stop pair
along a trip, a driving-mode route was requested from the API and its reported duration
extracted; this duration was then adjusted by a fixed **1.3× bus-speed factor**, to
account for buses running slower than free-flowing private-vehicle traffic, plus a
fixed **20-second dwell time** per stop. The resulting hop durations were accumulated
into per-stop arrival and departure times. Every queried origin-destination coordinate
pair was cached locally, both to avoid repeated API calls on re-runs and to provide a
reproducible record of the exact travel-time inputs used to build the schedule.

This construction method — real routed travel time, adjusted by an empirically
motivated speed factor and a fixed dwell time, rather than either a naive constant
spacing or a fully modeled transit assignment — is a pragmatic middle ground
appropriate to a context where no authoritative transit schedule exists to calibrate
against; it should be described in the paper as an explicit modeling choice, with the
1.3× and 20-second constants stated as assumptions rather than measured values.

## 4. Cross-validation against an independent ground-truth survey

The constructed feed was cross-checked against the DTCA's January 2025 traffic survey,
an independent, ground-truth enumeration of currently operating bus routes. This
validation required first recovering the survey shapefile's coordinate reference
system, which was not supplied with the data: the projection was reverse-engineered by
matching a known stop's coordinates against the raw shapefile numbers, converging on a
Transverse Mercator variant subsequently confirmed as "Bangladesh Transverse Mercator."
Routes were then matched between the survey and the constructed feed by normalized
route number (not by geometry), which surfaced an exact discrepancy: twelve routes
present in the survey were absent from the constructed feed, and one route present in
the feed could not be confirmed by the survey (and may be discontinued). One additional
route was flagged as a data-quality anomaly in the survey's own source table (well-formed
route geometry, but a recorded corridor length and vehicle count of zero) — noted as a
likely incomplete survey record rather than a routing error, and not corrected, since
there is no independent way to determine the true values.

## 5. Data-quality issues identified and resolved

Systematic validation of the constructed feed (referential integrity, monotonic stop
times, absence of duplicate-location stops within a single trip) surfaced several
concrete digitization and processing errors, each independently diagnosed and corrected:

| Issue class | Root cause | Resolution |
|---|---|---|
| Route/trip mislabeling | A return-direction trip was tagged with the wrong route identifier, giving one route only a single direction and its counterpart an extra, spurious set of trips | Corrected the trip-route linkage |
| Stop coordinate error | A single stop's digitized coordinates placed it roughly 90 km from its true location | Re-sourced the correct coordinates from OpenStreetMap |
| Looping/duplicated stops | Seven routes had the same physical stop appearing two or three times under distinct stop identifiers within a single trip, producing impossible "revisit" patterns and multi-hour phantom travel segments | Detected via a systematic same-coordinate scan across every route; retained the first occurrence and renumbered the stop sequence |
| Geographically incoherent stop order | Two routes had widely separated stops with no exact duplicates, suggesting multiple route variants concatenated during digitization | Not auto-correctable with the available data; flagged for manual review rather than guessed |
| Silent API-failure fallback | A small number of inter-stop hops had silently substituted a fixed fallback duration when the routing API failed | Re-queried the real travel times for the affected hops |
| Duplicate/conflicting timing files | Both the naive fixed-interval timings and the routed timings existed simultaneously | Promoted the routed version to canonical, archived the placeholder |
| Exposed credentials | An API key was hardcoded in a processing script (with additional stray copies in loose files) | Moved to an environment variable; stray key files removed (the exposed keys should still be rotated) |

Following these corrections, the feed was re-validated to confirm zero duplicate-
location stops, zero non-monotonic stop times, full referential integrity between
`stops.txt`/`trips.txt`/`stop_times.txt`, and a consistent four-trips-per-route
structure throughout.

## 6. Extending route coverage using independently sourced stop data

For the twelve routes identified in §4 as present in the ground-truth survey but
missing from the constructed feed, stop locations had to be sourced independently,
since the survey shapefile provides route *geometry* only, not a stop list. Points were
sampled at regular intervals (approximately 500 m to 1.2 km) along each route's
corridor geometry, and the OpenStreetMap Overpass API was queried for bus-stop and
transit-platform features within roughly 200-250 m of each sampled point. Candidate
stops were filtered to those within approximately 250 m of the route corridor itself
(to reject stops belonging to nearby parallel roads) and merged where multiple
candidates fell within roughly 450 m of one another, preferring named stops over
unnamed nodes. Stops were then ordered along each route by projecting them onto the
corridor line and sorting by distance-along-line. Where a gap exceeding 1.5 km
contained no OpenStreetMap-tagged stop, that gap was left unfilled rather than
inventing a plausible but unverified stop — a deliberate precision-over-completeness
choice, at the cost of leaving a small number of routes with sparse stop coverage (see
§7). Seven of the twelve identified routes were successfully reconstructed this way;
schedule timings for their new trips were generated using the same travel-time
methodology described in §3.

## 7. Known limitations

- Five routes confirmed present in the ground-truth survey remain outside the
  constructed feed's scope (deferred, not attempted, in this round of work).
- One route in the constructed feed could not be confirmed against the 2025 survey and
  may reflect a discontinued service.
- One reconstructed route (from §6) has notably sparse stop coverage, with large
  uncovered gaps at both ends of its corridor, and would benefit from manual stop
  input.
- Two routes have a stop ordering that could not be automatically corrected and are
  flagged rather than resolved.
- One survey record has an internal data-quality anomaly (§4) that could not be
  resolved without access to the original hardcopy survey.
- A small number of inter-stop hops (on the order of twenty, out of several thousand)
  exceed one hour; each was individually checked and corresponds to genuine
  long-distance segments extending well beyond the city's built-up area, not a data
  error.

## 8. Reproducibility

All feed-construction and validation scripts, the OpenRouteService response cache, and
an explicit record of superseded intermediate files (a naive-timing backup, and a stale
pre-correction feed snapshot) are retained alongside the final feed, so that every
correction described above is independently re-derivable rather than only
narratively documented.

---

## Framing note for the paper

This GTFS-construction effort is a genuine methodological contribution distinct from
anything in the São Paulo or Seville pipelines, both of which consume a pre-existing,
authoritative transit feed. It is worth stating explicitly in the paper as an example of
the broader "data scarcity in Global South megacities" theme: producing a MATSim-ready
transit scenario for Dhaka required constructing foundational transit data — not merely
cleaning or reformatting it — from a hardcopy document, an independent ground-truth
survey with its own data-quality issues, and two general-purpose web APIs, with every
correction step independently verifiable rather than taken on faith.
