# Dhaka synthetic population pipeline — full documentation

This documents how `config_dhaka.yml` turns the raw DTCA survey, BBS census, OSM
buildings, and (separately) pt2matsim outputs into a synthetic population and a
MATSim-ready scenario. It assumes familiarity with the `synpp` stage-pipeline
concept (see the root `README.md`); this file focuses on what's specific to
Dhaka and how it differs from the Seville pipeline it was adapted from.

## 1. How it's executed

```
python -m synpp config_dhaka.yml
```

`synpp` reads `run:` (the list of target stages), resolves each stage's
dependencies recursively via `context.stage(...)` calls, substitutes any name
listed in `aliases:` (e.g. the generic `synthesis.output` resolves to
`dhaka.output`), and executes the resulting DAG bottom-up, caching every
stage's output under `cache/` keyed by a hash of its resolved config. Re-runs
only recompute stages whose inputs/config actually changed.

`config_dhaka.yml`'s `run:` list currently has two independent targets:

- `synthesis.output` → the population/activities/trips CSV+GPKG deliverables
- `matsim.scenario.population/facilities/households/vehicles` +
  `dhaka.matsim.assemble_scenario` → the MATSim-format scenario files

`matsim.output` (the full eqasim-driven scenario build, including a live
MATSim run) is deliberately **not** in `run:` — see §6.

## 2. Input files required

All paths are relative to `data_path: raw_data/Dhaka` unless noted.

| Config key | Path | Used for |
|---|---|---|
| `dhaka.hts` | `hts/dtca_full.xlsx` | Household travel survey — the core input; see §4 |
| `dhaka.census` | `census/bangladesh_bbs_population-and-housing-census-dataset_2022_admin-02.xlsx` | Cross-check only; see §5 |
| `dhaka.census_wards_c01` | `census/extracted/census_c01_wards.csv` | Ward-level household/population/sex marginals (extracted from the BBS Community Report PDF) — IPU control totals; see §5 |
| `dhaka.census_wards_c02` | `census/extracted/census_c02_wards.csv` | Ward-level population by 5-year age group — IPU control totals; see §5 |
| `dhaka.ward_shp` | `spatial/ward_dhk_75.shp` | Zoning (DNCC/DSCC current ward boundaries + Savar/Keraniganj) |
| `dhaka.buildings_path` | `buildings/gis_osm_buildings_a_free_1.shp` | Home/work/education/shop/leisure candidate locations |
| `dhaka.osm_path` | `osm/bangladesh-latest.osm.pbf` | **Not yet used** by any active stage — reserved for richer point-amenity extraction |
| (external, copied by `dhaka.matsim.assemble_scenario`) | `matsim/dhaka_multimodalnetwork.xml.gz`, `matsim/dhaka_schedule.xml.gz`, `matsim/vehicles_unmapped.xml` | MATSim road network + transit schedule/vehicles, pre-built outside this pipeline via pt2matsim from the user's own GTFS feed (`gtfs/gtfs_v_1.0.zip`) and OSM extract |

Everything under `raw_data/Dhaka/gtfs/` and the three unused files in
`raw_data/Dhaka/matsim/` (`dhaka_network.xml.gz`, `dhaka_streetnetwork.xml.gz` —
pre-transit-mapping intermediates) aren't read by the Python pipeline directly;
they only fed the external pt2matsim run.

## 3. Output files produced

**From `synthesis.output` (`dhaka/output.py`, generic/unmodified), prefixed `dhaka_1pct_`:**

| File | Contents |
|---|---|
| `persons.csv` | One row per synthetic person: age, sex, employment, license, socioprofessional class, HTS/census linkage IDs |
| `households.csv` | One row per synthetic household: car/bicycle availability, income, census linkage ID |
| `activities.csv` / `.gpkg` | One row per activity in each person's daily schedule: purpose, start/end time, location |
| `trips.csv` / `.gpkg` | One row per trip: departure/arrival time, purpose, distance (mode is **not** included — `mode_choice: False`, see §6) |
| `vehicles.csv`, `vehicle_types.csv` | Synthetic private-vehicle fleet |
| `homes.gpkg`, `commutes.gpkg` | Home locations; home→work desire lines |
| `meta.json` | Run metadata: sampling rate, HTS type, random seed, commit |

**From the MATSim-assembly stages, same prefix:**

`population.xml.gz`, `facilities.xml.gz`, `households.xml.gz`, `vehicles.xml.gz`
(written by this pipeline), plus `network.xml.gz`, `transit_schedule.xml.gz`,
`transit_vehicles.xml.gz` (copied verbatim from the user's pt2matsim output)
and a hand-written `config.xml` tying them together.

**Current run's numbers** (`sampling_rate: 0.01`, `random_seed: 1234`,
census-anchored targets; see §5/§6):
33,965 households / 117,972 persons / 242,097 trips in the synthetic 1% output,
drawn from a study-area HTS base of 38,092 real households / 133,335 real
persons / 276,060 real trips (post-cleaning; see §4).

## 4. How the HTS (`dtca_full.xlsx`) is handled

The DTCA survey has 5 sheets: `Household info`, `all hh members`,
`individual hh member`, `trip info`, `evaluation` (unused). Processing happens
in four chained stages, `dhaka/data/hts/entd/{raw,cleaned,filtered,reweighted}.py`:

**`raw.py`** — filters `Household info` to `dhaka.study_area_upazilas`
(DNCC, DSCC, Savar, Keraniganj), then restricts `all hh members`,
`individual hh member`, and `trip info` to only those households' `hhid`s.
Builds:
- **persons** directly from `all hh members` (age, sex, relationship, driving
  license, personal vehicle) left-joined with `individual hh member`
  (employment, school level, observed work/school ward)
- **households** from `Household info` (income bracket, vehicle count) plus
  derived `household_size` (member count) and `number_of_bikes` (members
  whose `personal_vehicle` mentions "cycle" but not "motor")
- **trips** from `trip info` (purpose text, origin/destination ward + upazila,
  first-waypoint lat/lon where present, start/end time, mode, duration)

**`cleaned.py`** — the real transformation work:
- Trip purpose: `"Home to Work Place"` etc. split on `" to "` into
  preceding/following purpose via `home`/`work`/`education`/`other`; the two
  `"Non Home-based *"` categories (~4% of trips, neither end is home) map to
  `other`/`other`.
- Trip mode: 27 raw survey values (`Walking`, `Rickshaw`, `Bus`, `3-wheeler
  CNG (shared)`, ...) collapse into 7 categories — see §7 for why this
  differs from Seville's standard 5.
- `employed` = `employment_raw` not null; `studies` = `school_level_raw` not
  null; `has_license` = license field ≠ `"No License"` — all real per-person
  values, no imputation.
- Household/trip zone (`commune_id`) resolved from the paired
  `(upazila, ward_union)` text via `dhaka/wards.py`'s `parse_zone_id` — see
  §7's zoning note.
- **Trip distance**: the survey records GPS coordinates for a trip's first
  waypoint only ~30% of the time, and **never** for the actual destination, so
  a real point-to-point distance isn't computable. Instead, `euclidean_distance`
  is the distance between a random point sampled within the origin ward's
  polygon and a random point sampled within the destination ward's polygon
  (`dhaka.wards.build_ward_point_pools`, pools of 500 pre-sampled points per
  ward, reused across trips). This grounds distances in real geography and
  gives same-ward trips a small nonzero distance instead of a single
  duration-derived number regardless of where the trip actually went. Falls
  back to `trip_duration × assumed_speed_for_that_mode` (e.g. walk 4 km/h,
  rickshaw 10 km/h, car 22 km/h) only for the ~2% of trips whose origin/
  destination ward couldn't be resolved (left the study area). Floored at 50m
  either way.

  One caveat worth knowing: Savar and Keraniganj are each modeled as a single
  zone (~286 km² and ~191 km², vs. a typical city-corp ward's ~1.3 km²) since
  no ward-level subdivision exists for them in our data (§8). Same-zone trips
  within Savar/Keraniganj therefore get noticeably larger sampled distances
  (median ~9.1km) than same-ward trips in DNCC/DSCC (median ~745m) — a real
  reflection of the zone-size mismatch, not a bug, but worth keeping in mind
  when interpreting distance distributions for those two zones specifically.
- Standard HTS hygiene shared with Seville's generic `data/hts/hts.py`
  helpers: fixing swapped/midnight-crossing departure-arrival times,
  reconciling inconsistent activity-purpose chains, dropping persons with
  irreconcilable trip-time conflicts.

**`filtered.py` / `reweighted.py`** — Seville's versions do real work here
(filter out respondent-only placeholder rows, override weights). For Dhaka
these are near pass-throughs, since every person already has real trips and a
real weight from `raw.py` — kept only so the stage-naming convention matches
the rest of the pipeline family (`data.hts.entd.filtered` /
`.reweighted` are both real generic-pipeline dependency names).

## 5. How the BBS census is handled

Two BBS census sources feed the pipeline, in different roles:

**District-level workbook (cross-check only).** The original machine-readable
BBS dataset (`dhaka/data/census/population.py`, reading `Merged_All_Table`)
is whole-district granularity — it also covers rural upazilas well outside
the study area, so it can't act as a control total. It remains a
sanity-check reference point only.

**Ward-level Community Report (primary IPU control totals).** The BBS 2022
*"Population and Housing Census, Community Report: Dhaka"* PDF tabulates,
for every individual DNCC and DSCC ward:

- **Table C-01**: households (total/general/institutional/other),
  population by sex (male/female/hijra), sex ratio
- **Table C-02**: population by 17 five-year age groups (0-4 … 80+)

These were extracted programmatically (PyMuPDF text extraction + row-pattern
parsing over the tables' `Ward No. NN` subtotal rows) into
`raw_data/Dhaka/census/extracted/census_c0{1,2}_wards.csv` — 75 DSCC wards +
55 DNCC wards (54 numbered + the "Ward 98" restricted/cantonment area). The
extraction is fully cross-validated: Tables C-01 and C-02 agree on every
ward's population total, and the ward sums reproduce the report's own
printed city-corporation subtotals *exactly* (DSCC 4,305,063 pop / 1,101,733
HH; DNCC 5,990,723 pop / 1,634,550 HH).

`dhaka/ipu/prepare.py` now anchors the IPU age and sex marginals (and the
household-count scale) to these census figures for the DNCC+DSCC portion of
the study area, keeping the HTS-expansion approach only as a fallback for
Savar/Keraniganj (the report has no ward/union-level rows there). Key
methodological notes, all documented in that file's docstring:

- The synthetic population targets the **population aged 5+** (~9.56M for
  DNCC+DSCC): the DTCA HTS roster contains no under-5s at all, so the census
  0-4 bin is structurally unfillable by IPU and is dropped from the targets
  (under-5s make no independent trips in MATSim).
- No joint age×sex ward table exists (C-01 is sex-only, C-02 age-only), so
  the joint `age_sex` target is the outer product of the two real marginals
  — the maximum-entropy joint consistent with them, and IPU only ever rakes
  against the two marginals anyway.
- Household-*size* shape has no ward-level census breakdown (Table C-01 only
  gives household *counts*, not size distribution), so it comes from the
  coarser district-level BBS workbook instead — census-derived, just at a
  wider spatial grain — with its total rescaled to the census household
  count. See §6 for the donor-diversity caveat this carries.
- An earlier version of this raking implementation had a real bug (since
  fixed — see §6): `age_class`/`sex` weight adjustments only updated the
  matching person's own row, not their whole household, which the later
  TRS integerization step silently discarded for any household member who
  wasn't first-listed in the roster. Because DTCA lists household heads
  first, this systematically discarded correctly-raked weight for children
  and teenagers far more than for adults — concretely, a ~2.8 percentage
  point undershoot on the 15-19 age cohort and a ±3 percentage point sex
  residual, despite raking's own internal targets being satisfied almost
  exactly. After the fix, every one of the 17 age bins matches census to
  within ~0.3 percentage points and the sex residual is eliminated.

## 6. How the synthetic population originates from `dtca_full` (IPU/TRS)

This is the same generic algorithm Seville uses (`dhaka/ipu/{prepare,
population,attributed}.py`, `synthesis.py` unmodified from Seville) — Iterative
Proportional Updating to reweight a seed sample against control totals, then
Truncate-Replicate-Sample (TRS) to integerize into discrete households. What
differs for Dhaka is **where the seed and the targets come from**:

1. **`ipu/prepare.py`** builds age and sex control totals from the
   **ward-level BBS census extraction** (§5) for DNCC+DSCC, plus a weighted-HTS
   fallback for Savar/Keraniganj; household-size shape comes from the
   **district-level BBS workbook** (`dhaka.data.census.population`), rescaled
   to the census household count; the employment target stays fully
   HTS-derived (no census employment breakdown exists). One aggregation zone
   (`departement_id = "1"`, the whole study area).
2. **`ipu/population.py`** uses the cleaned+filtered HTS persons/households as
   the **seed pool** and rakes their weights to match those targets, then
   TRS-integerizes into whole households (each synthetic household is
   literally a resampled real HTS household, replicated as many times as its
   raked weight implies). Every raking category — `household_size_capped`,
   but also `age_class` and `sex` — broadcasts its weight adjustment to the
   *whole household* of any matching person, not just that person's own row
   (`dhaka/ipu/synthesis.py`'s `person_household_vars` mechanism). This
   matters because TRS collapses each household to one representative
   weight: without whole-household consistency, different members of the
   same household would end up with different post-raking weights, and
   whichever member wasn't first-listed in the household roster would have
   their raking result silently thrown away. **This was a real, verified bug
   until fixed**: DTCA lists household heads first, so the discarded weight
   was overwhelmingly a child's or teenager's — the confirmed cause of a
   ~2.8 percentage point undershoot on the 15-19 age cohort and a ±3
   percentage point sex-ratio residual, even though raking's own internal
   targets for those categories were satisfied almost exactly before TRS.
   Diagnosed by comparing the person-level raked weight sum (matched target
   within 0.1pp) against a simulation of what TRS actually keeps (matched
   the observed shortfall almost exactly), and confirmed structurally: 100%
   of multi-member households had internal weight variance after raking
   before the fix, 0% after. Verified post-fix in the real pipeline output:
   every age bin now within ~0.3pp of census, sex residual eliminated.
3. **`ipu/attributed.py`** assigns each synthetic household a ward
   (`commune_id`) by a weighted random draw whose weights are the **census
   household count per ward** (Table C-01) for all DNCC/DSCC wards — the same
   source that anchors the demographic targets, so geography and demographics
   are mutually consistent. Savar/Keraniganj keep the HTS-weighted household
   count (no ward-level census rows exist there). Both are absolute
   full-population household counts, so the mixed draw distribution is
   scale-consistent.

**Explicit caveat on Savar and Keraniganj** (relevant for any write-up): every
census-anchored quantity above covers DNCC+DSCC only. For Savar and
Keraniganj — roughly 20% of the synthesized households — population scale,
demographic composition, and spatial placement (one coarse zone each) all
rest **solely on the DTCA HTS and its expansion factors**, with no independent
census verification at sub-upazila level. Their totals are therefore subject
to the same expansion-factor uncertainty that census anchoring corrected for
the city corporations (where HTS expansion undershot the census population
by ~7%).

Net effect: the synthetic population's **scale and age/sex shape are anchored
to the real ward-level census** (DNCC+DSCC), while its behavioral content
(who lives with whom, who works, who travels how) and geographic (ward)
distribution come from `dtca_full.xlsx`, scaled to `sampling_rate`.

A note on performance: at `sampling_rate: 1.0`, TRS at census scale means
replicating the ~38K-household seed into ~2.7M synthetic households (the
default config uses `sampling_rate: 0.01`, i.e. ~34K households / ~118K
persons — all census-anchored targets are scaled by `sampling_rate`). `dhaka/ipu/synthesis.py`'s
`household_aware_trs()` does this with a vectorized numpy gather (one `iloc`
over a precomputed replica index array) rather than a per-replica Python
loop — the loop formulation took 45+ minutes and ~18GB without finishing at
this scale; the vectorized version completes in ~15 seconds within ~2GB.

## 7. Expansion factors

Yes — `Exp_Fac_231206`, the survey's own expansion factor column (present on
`all hh members` and `trip info`), is used throughout:

- **`person_weight`** = each person's own `Exp_Fac_231206` (`raw.py`)
- **`household_weight`** = the household head's `Exp_Fac_231206`
  (`relationship == "Head of Household (self)"`), falling back to the mean of
  all members' weights if no head row exists
- **`trip_weight`** = the same `Exp_Fac_231206`, carried onto each trip row

These weights are what IPU rakes against (§6) and what the ward-assignment
draw in `ipu/attributed.py` uses — the expansion factor is the single thread
that scales the ~38K-household raw sample up to represent the true DSCC+
DNCC+Savar+Keraniganj population, and then back down to the requested 1%
synthetic output via `sampling_rate`.

## 8. Differences from the Seville pipeline

| Aspect | Seville | Dhaka |
|---|---|---|
| **HTS respondent coverage** | One person/household interviewed about trips; other members known only by age/sex, Bernoulli-imputed via `household_members/{add_persons,set_attributes,add_trips}.py` | Every household member listed directly with real age/sex/employment/education/license — imputation chain skipped entirely |
| **IPU control totals** | From census (`population.csv`, `households.xlsx`, 7-file employment breakdown) | Age/sex/household-count from ward-level BBS census (§5) for DNCC+DSCC; household-size shape from the district-level BBS workbook; HTS-derived fallback for Savar/Keraniganj and for employment |
| **Ward/zone assignment in IPU** | Population-weighted draw from census-section population counts | Population-weighted draw from the **census household count per ward** (Table C-01) for DNCC+DSCC, matching the same source that anchors the demographic targets (§5); HTS-weighted fallback for Savar/Keraniganj |
| **Zoning unit** | Official INE census-section codes (fine-grained, single numbering scheme) | Custom `S01`–`S75` (DSCC) / `N01`–`N54`+`N98` (DNCC) / `SAVAR` / `KERANIGANJ` scheme (`dhaka/wards.py`) — needed because DNCC and DSCC have independent, overlapping ward numbers |
| **Trip distance** | Real geocoded coordinates (Nominatim + official address/street-name matching, `data/hts/entd/{streets,trip_distance}.py`) | No destination coordinates exist in the survey at all; distance is sampled between random points in the origin/destination ward polygons, with a duration×speed fallback for the ~2% of trips that leave the study area (§4) |
| **Mode set** | 5 standard modes: walk, pt, bike, car, car_passenger | 7 modes: walk, bike, car, pt, **rickshaw**, **paratransit**, other — added because rickshaw alone is ~30–35% of Dhaka trips and doesn't fit any standard category |
| **Driving license** | Real for the respondent only; imputed for others via Bernoulli draw calibrated against government license statistics | Real for every person directly from the survey — no imputation, no external license dataset needed |
| **Household income** | Hardcoded to 0 (`seville/income.py`) — no real income data available | Real income bracket (`income_class` ordinal from `q7_hh_income`, computed in `dhaka/data/hts/entd/cleaned.py`) is threaded through IPU/TRS as a passenger column (`dhaka/ipu/population.py`) and surfaced as `household_income` by `dhaka/income.py` — no longer hardcoded to 0 |
| **PT-subscription/license calibration** | `seville/synthesis/population/enriched.py` overrides the generic stage to calibrate against `seville.data.pt.constraints`/`license.constraints` | Not needed (real license data, no PT-subscription data either way) — generic `synthesis/population/enriched.py` used unmodified |
| **Home/amenity locations** | Separate official building registry (homes) + OSM `.pbf` via `pyrosm` (work/education/shop/leisure) | Homes: the nationwide Geofabrik buildings file, bbox-clipped. Work/education/shop/leisure: the same file's sparsely-populated `type` tag (~3.2K candidates) **merged with point amenities and amenity-tagged polygons read directly from `bangladesh-latest.osm.pbf`** via GDAL's OSM driver (~26.9K more candidates) — see `dhaka/data/osm/locations.py`. The `.pbf` path is optional; the stage falls back to buildings-only with a warning if it's absent |
| **GTFS / road network** | Fetched and auto-built inside the pipeline (`data/gtfs/cleaned.py`, `data/osm/chunked.py`, pt2matsim via `matsim.scenario.supply.*`) | No official Bangladesh GTFS exists — the user built one manually (stop times derived via the ORS API) and ran pt2matsim externally; this pipeline just copies the resulting network/schedule/vehicles files in (`dhaka.matsim.assemble_scenario`) |
| **MATSim scenario assembly** | `eqasim-java`'s `SevilleConfigurator`/`RunAdaptConfig`/`RunSimulation` (Java) — full config generation, population routing, mode-choice calibration, and can run the simulation itself | Bypasses eqasim's Java "prepare" step entirely, because `SevilleModeChoiceModule` hardcodes exactly 5 modes and doesn't recognize rickshaw/paratransit. Instead: pure-Python demand-side writers + a hand-written `config.xml` (teleported routing for every non-car mode). Actually running MATSim uses the `matsim_run/` Maven project in this repo (vanilla MATSim, no eqasim-java needed) — see §9 |
| **`mode_choice` config** | `True` — MATSim dynamically re-chooses mode during replanning iterations via `eqasim-java`'s discrete-choice module | `False` — no *dynamic* re-choice during MATSim's iterations (config.xml's replanning module only has ChangeExpBeta + ReRoute). But every leg's mode comes from a discrete choice model fitted on the DTCA HTS (`dhaka/mode_choice/estimate.py`) and applied to each synthetic trip's real assigned distance and traveler covariates (`dhaka/synthesis/population/mode_choice.py`) — a modeled, per-trip assignment made once at synthesis time, not a wholesale copy of the matched HTS donor's mode |

## 9. Running MATSim on the assembled scenario

`dhaka.matsim.assemble_scenario` produces a complete, plain-MATSim scenario
under `output/` (`dhaka_1pct_population.xml.gz`, `facilities.xml.gz`,
`households.xml.gz`, `vehicles.xml.gz`, `network.xml.gz`,
`transit_schedule.xml.gz`, `transit_vehicles.xml.gz`, `config.xml`) — no
eqasim-java involved (see §8's "MATSim scenario assembly" row for why).
Actually running it uses `matsim_run/`, a small Maven project in this repo
(vanilla `matsim-core`, which already bundles public-transit support at the
pinned version — no separate `pt` contrib needed).

**Full build/run/troubleshooting instructions live in
[`matsim_run/README.md`](../matsim_run/README.md)** — colocated with the
Maven project itself. Summary:

```bash
# One-time build (downloads MATSim + dependencies, ~10-15 min first time)
cd matsim_run
mvn clean package

# Run (from the output/ directory, so relative paths in config.xml resolve)
cd ../output
java -jar ../matsim_run/target/dhaka-matsim-run-1.0.jar dhaka_1pct_config.xml
```

Key things to know before running:
- **MATSim version is pinned to 2025.0**, not the newest snapshot line -
  confirmed empirically that 2026.0's class files need JDK 25, while 2025.0
  runs on JDK 17+. See `matsim_run/pom.xml`'s comments if you want to bump
  this.
- **`lastIteration` defaults to 100** (`matsim_last_iteration` in
  `config_dhaka.yml`), and `flowCapacityFactor`/`storageCapacityFactor` in
  `config.xml` are scaled to `sampling_rate` (0.01) — both added specifically
  because their absence is a correctness bug, not a style choice: without
  capacity scaling, a 1% population tries to congest a network sized for
  100% of real Dhaka traffic and the simulation looks artificially empty.
- **Smoke-test with 1 iteration before a real run** — `matsim_run/README.md`
  has the exact steps. A real 100-iteration run takes substantially longer
  and should run detached/in the background.
- **This is not calibration.** A completed run gives you `scorestats.csv`
  (score convergence), `modestats.csv` (mode-share stability across
  iterations - should stay flat, since mode isn't dynamically re-chosen),
  and link-level volumes/travel times to sanity-check for implausible
  congestion. Calibration - tuning scoring parameters and capacity factors
  against real observed Dhaka benchmarks - only becomes meaningful once
  you have that output to compare against, and hasn't started yet.

## Known limitations / natural next steps

- Trip distances are ward-polygon-sampled, not geocoded to real addresses
  (§4) — good enough to ground distances in real geography, but Savar/
  Keraniganj's coarse single-zone modeling means their internal-trip
  distances are rougher than DNCC/DSCC's.
- Education-location matching now draws on ~2,172 real school/college/
  university candidates (typed buildings + OSM POIs, `dhaka/data/osm/
  locations.py`), up from ~244 — match success rose from 87.3% to 90.8%.
  The remaining gap is mostly university seekers, still the sparsest category.
- `household_income` is now derived from `income_class` (threaded through
  IPU/TRS and surfaced by `dhaka/income.py`) rather than hardcoded to 0 — see
  §8's "Household income" row. It remains on the raw 0–8/-1 ordinal DTCA
  bracket scale, not a monetary (Taka) figure, by design (avoids assuming a
  value for the open-ended "more than Tk 100,000" top bracket).
- **Household-size shape now comes from the district-level BBS workbook
  instead of the HTS** (§5/§6), fixing a real gap: 1-person households were
  0.26% of the *raw, unweighted* survey roster (135/52,672 households,
  verified directly against `all hh members`) versus 6.5% in the census — a
  ~25x undercount that HTS expansion weighting cannot correct, since it
  reweights existing rows rather than inventing missing household types.
  Post-fix, the synthetic population matches census on every size bucket to
  within ~0.15 percentage points (`validation_household_size.png`). This
  was applied deliberately despite a real, still-present caveat: the
  study-area HTS sample has only 29 real 1-person households (out of
  38,092) to draw from, so at full census scale those 29 households
  collectively stand in for the entire ~180,000-household 1-person segment
  — roughly 6,200x replication weight each (about 77x at the default 1%
  sampling rate). The synthetic "1-person household" segment therefore
  matches the census *aggregate share* correctly, but doesn't gain the
  underlying diversity the census implies — it's a small number of real
  households cloned many times over, not really-diverse synthetic
  1-person households. The same concentration, less severely, affects the
  5+ bucket (3,313 real donors, target share nearly triples) and the
  3-/4-person buckets (which the census shape roughly halves versus HTS).
  The district workbook's scope (whole Dhaka district, including rural
  upazilas outside DNCC+DSCC) also isn't obviously more representative of
  the study area specifically than the HTS is — it's a different source
  with its own scope mismatch, not a strictly better one. This trade
  (correct aggregate share vs. donor diversity within a bucket) was made
  because the aggregate fit was judged more important for this pipeline's
  purposes; anyone using the household-size distribution for anything
  beyond aggregate shares (e.g., analyzing within-household correlation
  structure for 1-person or 5+ households specifically) should be aware
  the underlying donor pool for those buckets is small.
- `mode_choice: False` means MATSim doesn't dynamically re-choose mode during
  replanning (the eqasim-java module that would is blocked, see §8) — but
  every leg's mode now comes from a discrete choice (conditional logit) model
  fitted on the DTCA HTS (`dhaka/mode_choice/estimate.py`, coefficients in
  `dhaka/mode_choice/fitted_coefficients.json`) and applied per synthetic
  trip's real assigned distance and traveler covariates
  (`dhaka/synthesis/population/mode_choice.py`), not a wholesale copy of the
  matched HTS donor's mode. Two caveats worth keeping in mind: (1)
  alternative-specific travel times are still a distance ÷ assumed-speed
  proxy, not real network/GTFS skims, so the model doesn't yet respond to
  actual network conditions the way MATSim's own simulation will; (2) the
  model's standard errors are approximate (normalized survey weights, no
  cluster-robust correction for repeated trips per person/household) — the
  coefficients themselves are stable across multiple estimation runs, but
  treat significance levels as indicative, not publication-grade.
- Savar and Keraniganj (~20% of synthesized households) have no ward-level
  census coverage: their population scale, demographics, and spatial
  placement all rest solely on the HTS and its expansion factors, unlike
  DNCC/DSCC which are now anchored to real census counts throughout (§5, §6).
- `matsim.output` (eqasim-driven full scenario + simulation run) still needs
  either new Dhaka-specific Java classes in `eqasim-java`, or continuing with
  the eqasim-free path already in place - the latter is what's actually used:
  `dhaka.matsim.assemble_scenario` produces a complete plain-MATSim scenario,
  and `matsim_run/` (a small Maven project in this repo, not eqasim-java) runs
  it. See §9.
- The MATSim run itself is not yet calibrated - `config.xml`'s scoring
  parameters (activity utility rates, mode constants) are reasonable
  defaults, not empirically tuned against observed Dhaka benchmarks (traffic
  counts, transit ridership, travel-time surveys). Calibration is meaningful
  only once a full run's output exists to compare against those benchmarks;
  see §9's "Interpreting results" for what a first run's output can and
  can't tell you before that happens.
