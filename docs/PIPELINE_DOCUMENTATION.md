# Dhaka Pipeline Documentation

This document describes the current state of the Dhaka synthetic-population /
MATSim-scenario pipeline (`config_dhaka.yml` + `dhaka/`), a `synpp`-based
adaptation of the eqasim-style pipeline used elsewhere in this repo (e.g.
`seville/`). It covers five things: the config structure and pipeline flow,
the required input files, the output files, the core population-synthesis
mechanism, and where/why Dhaka's pipeline differs from Seville's.

Run with: `python -m synpp config_dhaka.yml` from the repo root.

---

## 1. Config structure and pipeline flow

`config_dhaka.yml` has three top-level blocks:

```yaml
working_directory: cache   # synpp's stage-cache directory

run:                       # which stages to actually execute
  - synthesis.output
  - matsim.scenario.population
  - matsim.scenario.facilities
  - matsim.scenario.households
  - matsim.scenario.vehicles
  - dhaka.matsim.assemble_scenario

config:                    # key-value settings read by individual stages
  ...

aliases:                   # generic stage name -> Dhaka-specific implementation
  ...
```

**`run:`** is the set of target stages synpp actually needs to produce, not
every stage that executes. synpp resolves the full dependency graph
backward from these targets: each stage's `configure()` method calls
`context.stage("some.generic.name")` for its inputs, and synpp looks up
`some.generic.name` in the `aliases:` block to find which concrete
implementation to run. If a generic name has no alias, synpp falls back to
that name's stage class directly (the untouched, city-agnostic
implementation shared across all cities in this repo). This is how the same
`run: synthesis.output` target produces an entirely different execution
graph for Dhaka than for Seville, without either city's config needing to
know about the other.

`matsim.output` (the eqasim-java-driven full scenario + simulation run) is
deliberately **not** in `run:` — see §5.

**Key `config:` values:**

| Key | Purpose |
|---|---|
| `data_path` | `raw_data/Dhaka` — root for all raw input paths below |
| `output_path` / `output_prefix` | `output` / `dhaka_1pct_` — where and how output files are named |
| `sampling_rate` | `0.01` — synthesize 1% of the true population (full-scale run is `1.0`, see §4 for why 1% is the practical default) |
| `random_seed` | `1234` — seed for IPU/TRS and all weighted random draws |
| `hts` | `entd` — selects the DTCA survey code path (mirrors Seville's ENTD/EGT switch, though only `entd` is implemented for Dhaka) |
| `dhaka.study_area_upazilas` | DNCC, DSCC, Savar, Keraniganj — defines the HTS filter and the zoning scope |
| `dhaka.ward_shp`, `dhaka.ward_number_field`, `dhaka.ward_cc_field`, `dhaka.ward_upazila_field` | Ward boundary shapefile and its relevant column names |
| `dhaka.hts` | `hts/dtca_full.xlsx` |
| `dhaka.census` | District-level BBS workbook (cross-check only, not an IPU input) |
| `dhaka.census_wards_c01`, `dhaka.census_wards_c02` | Ward-level extracted census marginals — the actual IPU control-total source |
| `dhaka.buildings_path` | Nationwide OSM building extract |
| `dhaka.osm_path` | Bangladesh `.osm.pbf` — POI supplement for activity locations (optional; falls back gracefully if absent) |
| `home_location_sampling` | `weighted` — area-weighted candidate draw for home locations |
| `mode_choice` | `False` — no dynamic MATSim mode re-choice (see §5) |
| `matching_attributes` | `["sex", "age_class", "has_license"]` — hierarchical statistical-matching keys for attaching activity chains |
| `IPU_aggregation_level` | `departement_id` — IPU rakes at whole-study-area granularity, not per ward (see §4) |
| `eqasim_commit` / `eqasim_version` / `eqasim_branch` | Provenance stamp only; no live eqasim-java dependency is exercised (see §5) |

**`aliases:`** is where Dhaka actually diverges from the generic pipeline.
Each line maps a generic stage name to a `dhaka.*` implementation:

- HTS chain: `data.hts.entd.{raw,cleaned,filtered,reweighted}` → `dhaka.data.hts.entd.*`
- Zoning: `data.spatial.iris` → `dhaka.data.spatial.iris`, `data.spatial.codes` → `dhaka.entd_codes`
- Population synthesis: `synthesis.population.sampled` and `data.census.filtered` → `dhaka.ipu.attributed`
- Attributes: `synthesis.population.income.selected` → `dhaka.income`, `synthesis.population.spatial.home.zones` → `dhaka.homes`
- Locations: `synthesis.locations.{home,education,secondary,work}` → `dhaka.locations.*`
- Spatial/location assignment: `synthesis.population.spatial.{locations,primary.distance_distributions,primary.work,primary.education,secondary.locations}` → `dhaka.synthesis.population.spatial.*`
- Trips and output: `synthesis.population.trips` → `dhaka.synthesis.population.trips`, `synthesis.output` → `dhaka.output`
- MATSim: `matsim.scenario.population` → `dhaka.matsim.scenario.population`

Stages **not** aliased fall through to the generic implementation unmodified
— notably `synthesis.population.enriched` (Seville's override only
calibrates PT-subscription/license against imputed data; Dhaka's HTS
already has real `has_license` per person and no PT-subscription field, so
there's nothing city-specific to calibrate) and
`matsim.scenario.{facilities,households,vehicles}` (fully generic).

**End-to-end flow**, in execution order:

1. **Data cleaning & zoning** — ward-scheme reconciliation (`dhaka.data.spatial.iris`), HTS cleaning (`dhaka.data.hts.entd.cleaned`), ward-level census PDF extraction (pre-processed once, read as CSV — see §2 and §4), building classification.
2. **Population synthesis** — `dhaka.ipu.prepare` (control totals) → `dhaka.ipu.population` (IPU raking + TRS) → `dhaka.ipu.attributed` (ward assignment + attribute defaults).
3. **Activity & location assignment** — statistical matching (generic `synthesis.population.enriched`) → income/home zone (`dhaka.income`, `dhaka.homes`) → home/work/education/secondary location choice (`dhaka.locations.*`, `dhaka.data.osm.locations`) → trip assembly (`dhaka.synthesis.population.trips`).
4. **Output** — `dhaka.output` writes the persons/households/trips/activities files; `matsim.scenario.{population,facilities,households,vehicles}` write MATSim demand XML; `dhaka.matsim.assemble_scenario` merges these with the externally-supplied network/schedule and writes `config.xml`.

---

## 2. Required input files

All paths are relative to `data_path` (`raw_data/Dhaka/`):

| Path | Description | Consumed by |
|---|---|---|
| `hts/dtca_full.xlsx` | DTCA household travel survey — full household rosters, not single-respondent | `dhaka.data.hts.entd.raw` |
| `census/Community Report Dhaka.pdf` | BBS 2022 Community Report — the 1,668-page source PDF. **Not read live by the pipeline**; pre-processed once via the extraction script into the two CSVs below | one-time offline extraction only |
| `census/extracted/census_c01_wards.csv` | Extracted ward-level household/sex marginals (Table C-01), DNCC+DSCC | `dhaka.ipu.prepare`, `dhaka.ipu.attributed` |
| `census/extracted/census_c02_wards.csv` | Extracted ward-level age marginals (Table C-02), DNCC+DSCC | `dhaka.ipu.prepare` |
| `census/bangladesh_bbs_population-and-housing-census-dataset_2022_admin-02.xlsx` | District-level BBS workbook | `dhaka.data.census.population` (cross-check only, not an IPU input) |
| `spatial/ward_dhk_75.shp` (+`.dbf`/`.shx`/`.prj`) | Post-2020 75-ward DSCC-numbering-compatible ward boundaries | `dhaka.data.spatial.raw` |
| `buildings/gis_osm_buildings_a_free_1.shp` (+ sidecars) | Nationwide Geofabrik OSM building extract (11M+ polygons, bbox-clipped on read) | `dhaka.data.buildings` |
| `osm/bangladesh-latest.osm.pbf` | Bangladesh OSM extract — point amenities and amenity-tagged polygons, supplements the buildings file for work/education/shop/leisure candidates | `dhaka.data.osm.locations` (optional: falls back to buildings-only with a warning if absent) |
| `matsim/dhaka_multimodalnetwork.xml.gz` | Externally pt2matsim-built road network | `dhaka.matsim.assemble_scenario` |
| `matsim/dhaka_schedule.xml.gz` | Externally pt2matsim-built transit schedule (from the self-built, cross-validated GTFS feed — see `docs/GTFS_METHODOLOGY.md`) | `dhaka.matsim.assemble_scenario` |
| `matsim/vehicles_unmapped.xml` | Transit vehicle types for the schedule above | `dhaka.matsim.assemble_scenario` |

Not read directly by this synpp pipeline (already baked into the `matsim/`
files above via an external pt2matsim run): `gtfs/gtfs_v_1.0.zip`,
`osm/allroads.osm.pbf`, `osm/bigroads.osm.pbf`.

---

## 3. Output files

Written to `output_path` (`output/`) with `output_prefix` (`dhaka_1pct_`):

**Synthetic population (plain + geospatial):**

| File | Contents |
|---|---|
| `persons.csv` | One row per synthetic person: age, sex, employment, education, license, income class, etc. |
| `households.csv` | One row per synthetic household: size, vehicle/bike counts, ward (`commune_id`), income |
| `trips.csv` / `trips.gpkg` | One row per trip: times, purpose, mode, distance, geolocated origin/destination |
| `activities.csv` / `activities.gpkg` | One row per activity: purpose, start/end time, geolocated location |
| `homes.gpkg` | Home locations (points) |
| `commutes.gpkg` | Home→work desire lines |
| `vehicle_types.csv`, `vehicles.csv` | Synthetic private-vehicle fleet |
| `meta.json` | Run metadata: sampling rate, HTS type, random seed, commit |

**MATSim scenario:**

| File | Source |
|---|---|
| `population.xml.gz`, `facilities.xml.gz`, `households.xml.gz`, `vehicles.xml.gz` | Written by this pipeline (demand side) |
| `network.xml.gz`, `transit_schedule.xml.gz`, `transit_vehicles.xml.gz` | Copied verbatim from `raw_data/Dhaka/matsim/` (externally-built supply side) |
| `config.xml` | Hand-written by `dhaka.matsim.assemble_scenario`, ties demand + supply together (teleported routing for every non-car mode; car routed on the network) |

Diagnostic byproducts (not part of the scenario): `distance_distribution_*.png`.

---

## 4. Core population synthesis mechanism

The seed population is the cleaned, filtered DTCA household travel survey
(`dhaka.data.hts.entd.reweighted`) — full real households and persons, each
carrying a survey expansion factor as an initial weight. Three stages turn
this seed into the final synthetic population:

**`dhaka.ipu.prepare`** builds the control totals IPU will rake against.
Age and sex marginals, and the household-count scale, come from the
ward-level census extraction (`census_c01_wards.csv`/`census_c02_wards.csv`),
summed to the whole DNCC+DSCC study area — anchoring both the *shape*
(age/sex distribution) and the *absolute scale* of the target population to
real census counts, not just survey expansion. Because no joint age×sex
table exists at ward level (Table C-01 is sex-only, Table C-02 is age-only),
the joint control used internally is the outer product of the two
marginals — the maximum-entropy joint consistent with both, which doesn't
bias raking since IPU only ever re-derives and enforces the two marginals
separately. Household-size *shape* has no ward-level census equivalent, so
it comes from the coarser district-level BBS workbook
(`dhaka.data.census.population`'s `household_size` breakdown, 1/2/3/4/5+)
instead — the best available grain — with its *total* rescaled to the
census household count. Savar and Keraniganj, which have no ward-level rows
in the Community Report, keep HTS-expansion-based control totals for age
and sex, added on top of the census-anchored DNCC/DSCC totals so the
combined target still covers the full study area.

**`dhaka.ipu.population`** runs Iterative Proportional Updating (IPU): each
household's weight is adjusted iteratively so its weighted sum within every
control category (age, sex, household size) converges toward that
category's target, `w_i ← w_i · (target/current)^λ` with a damping
exponent `λ` to avoid overshooting across simultaneously-updated
categories. IPU rakes at `departement_id` granularity — the whole study
area as a single zone — not per ward, because the ~38K-household HTS
seed isn't large enough to rake independently at ward level. Every
category's weight adjustment is broadcast to the *whole household* of any
matching person, not just that person's own row — this applies both to
`household_size_capped` (a genuine household-level attribute) and to
`age_class`/`sex` (person-level attributes whose target is a person count,
but whose adjustment must still apply household-wide). This matters because
the next step collapses each household to a single representative weight:
without whole-household broadcasting, different members of the same
household would end up with different post-raking weights, and whichever
member wasn't first-listed in the household roster would have their
raking result silently discarded. This was an actual bug until it was
found and fixed: DTCA lists household heads first, so the discarded weight
was overwhelmingly a child or teenager's, not an adult's — the specific,
verified cause of a systematic ~2.8 percentage point undershoot in the
15-19 age cohort (and smaller distortions in every other age bin and the
sex ratio) prior to the fix. Once raking converges,
`dhaka/ipu/synthesis.py`'s vectorized Truncate-Replicate-Sample (TRS) step
integerizes each household's (now consistent) fractional weight into a
whole number of replicated synthetic households (floor, plus a stochastic
extra copy with probability equal to the fractional remainder), so the
expected replica count matches the raked weight exactly while every
realized count is a whole household.

**`dhaka.ipu.attributed`** assigns each synthetic household a ward via a
weighted categorical draw. For DNCC/DSCC wards the draw weight is the
census household count (Table C-01) — the same source anchoring the
demographic targets, so a household's geography and demographics trace to
the same authoritative source. Savar/Keraniganj again fall back to
HTS-expansion-weighted draws. Since both sources are absolute
full-population household counts, mixing them in one draw distribution
doesn't introduce a scale inconsistency.

Downstream, activity chains, mode, and unaffected attributes (driving
license, employment, income bracket) are attached via hierarchical
statistical matching against the HTS: candidate donors are found matching
on a decreasing number of `matching_attributes` until a minimum donor-pool
size is reached, then one is drawn weighted by survey weight, and its full
activity chain and mode are copied onto the synthetic person. Because every
DTCA household lists a full real roster, no Bernoulli-imputation model is
needed for non-respondent members (contrast with Seville, §5). Home,
work, education, and secondary locations are then chosen from
building+OSM-POI candidate pools, area/employee-weighted and matched
against HTS-observed trip-distance distributions.

At full census scale the DNCC+DSCC household target alone is ~2.74 million;
the default `sampling_rate: 0.01` produces a practically-sized ~34K-household
/ ~118K-person output for iteration and testing, while the full-scale
(`sampling_rate: 1.0`) run is intended for final results only, given the
runtime and memory this implies.

---

## 5. Differences from the Seville pipeline (and why)

`dhaka/` began as a direct copy of `seville/`; every deviation below was
driven by what the actual Dhaka data does or doesn't provide, not stylistic
preference.

| Aspect | Seville | Dhaka | Reason |
|---|---|---|---|
| **HTS respondent coverage** | One respondent per household; other members Bernoulli-imputed from census/employment/license rates (`household_members/{add_persons,set_attributes,add_trips}.py`) | Every household member listed directly with real age/sex/employment/education/license | DTCA rosters the full household; the imputation stages exist unmodified in `dhaka/data/hts/entd/household_members/` but are dead code — nothing in the live pipeline references them (confirmed unused; candidate for deletion) |
| **IPU control totals** | Structured census tables (`population.csv`, `households.xlsx`, 7-file employment breakdown) | Age/sex/household-count: ward-level BBS marginals extracted from a 1,668-page unstructured PDF (Tables C-01/C-02) for DNCC+DSCC. Household-size shape: district-level BBS workbook (coarser, but still census, not survey). HTS-expansion fallback for Savar/Keraniganj | No structured, fine-grained Bangladesh census product exists; the PDF is the only source at ward grain, so it had to be parsed and cross-validated rather than read directly |
| **Ward assignment** | Weighted draw from census-section population counts | Weighted draw from the **same** ward-level census household counts anchoring the demographic targets (DNCC/DSCC); HTS-weighted fallback for Savar/Keraniganj | Keeps geography and demographics anchored to one authoritative source; before this was HTS-only, ward-level spatial correlation with the census was only r=0.86 — now r=0.996 |
| **Zoning unit** | Official INE census-section codes, one numbering scheme | Custom `S01`-`S75` (DSCC) / `N01`-`N54`+`N98` (DNCC) / `SAVAR` / `KERANIGANJ` (`dhaka/wards.py`) | DNCC and DSCC each have independent, overlapping ward numbers (both have a "Ward 1"); a prefix is required to disambiguate |
| **Trip distance** | Real geocoded coordinates (Nominatim + street-name matching) | Random points sampled inside the origin/destination ward polygon, duration×speed fallback for out-of-area trips | The survey has no destination coordinates at all |
| **Mode set** | 5 modes: walk, pt, bike, car, car_passenger | 7 modes: walk, bike, car, pt, rickshaw, paratransit, other | Rickshaw + paratransit are ~39% of trips combined and have no reasonable equivalent among the 5 standard modes |
| **Driving license / employment** | Imputed for non-respondents from government statistics | Real for every person directly from the survey | Full roster means no imputation is needed |
| **Household income** | Hardcoded to 0 | Real income bracket from the survey (`q7_hh_income`) | Real per-household data exists; not yet threaded into a numeric value downstream (open item) |
| **Home/amenity locations** | Official building registry + OSM `.pbf` via `pyrosm` | Nationwide OSM-derived building extract (bbox-clipped) merged with point/polygon POIs read directly from a Bangladesh `.osm.pbf` | No separate official building or facility registry exists; the buildings file is <1% land-use-typed, so POIs are needed to get a usable candidate pool (raised candidates from ~3.2K to ~30K) |
| **GTFS / transit feed** | Fetched and built in-pipeline | No official feed exists; self-built from a prior informal document + a DTCA 2025 route survey + OSM, cross-validated against the survey and corrected for several structural errors (see `docs/GTFS_METHODOLOGY.md`) | Bangladesh has no official GTFS; the resulting feed had to be independently validated since no ground truth existed to check it against otherwise |
| **MATSim scenario assembly** | eqasim-java (`SevilleConfigurator` etc.) — full config generation, routing, mode-choice calibration, can run the simulation | Pure-Python demand writers + hand-written `config.xml`, teleported routing for every non-car mode (`dhaka.matsim.assemble_scenario`) | eqasim-java's mode-choice module hardcodes exactly 5 modes calibrated for Seville and has no slot for rickshaw/paratransit |
| **`mode_choice` config** | `True` — dynamic MATSim mode re-choice during replanning | `False` — mode is fixed at synthesis time (from the matched HTS donor trip) and carried through to `trips.csv`/`population.xml.gz`, not re-simulated | Same root cause as above: the discrete-mode-choice implementation that would enable this is Seville-specific |
| **Buildings scale** | City-scale registry | 11M+ row nationwide extract, requiring a bbox-clip on read | No city-scoped registry exists for Dhaka; only a national one |

### Known limitations carried by these differences

- Savar/Keraniganj's population scale, demographics, and spatial placement rest entirely on HTS expansion — no independent census check exists at that grain.
- `dhaka/data/hts/entd/household_members/` (the Seville imputation stages) is confirmed dead code left over from the initial copy — safe to delete, not yet done.

### Resolved issues worth recording

Two validation findings that were initially documented as accepted
limitations turned out to be fixable and have since been corrected:

- **Household-size shape was HTS-derived and badly wrong** (1-person
  households: <0.2% synthetic vs. 6.5% census — a real ~25x survey
  under-count, confirmed against the raw unweighted HTS roster). Fixed by
  sourcing the shape from the district-level BBS workbook instead (§4);
  synthetic now matches census to within ~0.15 percentage points on every
  size bucket.
- **The 15-19 age cohort undershot both the census and the HTS by ~2.8
  percentage points**, despite IPU raking's own internal target for that
  bin being satisfied almost exactly. Root cause (verified directly, not
  inferred): `age_class`/`sex` weight adjustments during raking only
  updated the matching person's own row, not their household's — so
  different members of the same household ended up with different
  post-raking weights, and TRS's integerization step (which keeps one
  representative weight per household) silently discarded the correct
  result for whichever member wasn't first-listed. Since DTCA lists
  household heads first, this systematically discarded teenagers' and
  children's correctly-raked weights far more often than adults'. Fixed by
  broadcasting `age_class`/`sex` adjustments to the whole household too
  (§4); every one of the 17 age bins now matches census to within ~0.3
  percentage points, and the sex-ratio residual is eliminated as well.
