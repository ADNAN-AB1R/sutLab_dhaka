# Methodology: Synthetic Population Generation for Dhaka

This document provides publication-ready methodology prose for the population-synthesis
portion of the Dhaka pipeline paper, organized to parallel the structure used in
Sallard, Balać & Hörl (2020), *"A Synthetic Population for the Greater São Paulo
Metropolitan Region"* (§ Creation of a synthesized population). Each subsection below
maps to one paragraph/subsection you can adapt directly. Quantitative figures quoted
here are taken from the current pipeline run (`sampling_rate: 0.01`, `random_seed: 1234`)
and from the underlying source documents (`docs/dhaka_pipeline.md`,
`docs/PIPELINE_DOCUMENTATION.md`) — re-verify against the latest run before final
submission if the pipeline has been re-executed since.

---

## 1. Overview

The Dhaka synthetic population is produced with an adapted version of the open-source
`eqasim`/`synpp` pipeline framework (Hörl & Balać, 2021), the same framework underlying
the Île-de-France (Hörl & Balać, 2021), California (Balać & Hörl, 2020), and São Paulo
(Sallard et al., 2020) synthetic populations. The framework expresses population
synthesis as a directed acyclic graph of *stages*, each with declared dependencies and
cached outputs, allowing generic (city-agnostic) stages to be selectively overridden by
city-specific implementations through a configuration-level alias mechanism. This
design is what makes the Dhaka adaptation directly comparable, methodologically, to its
Seville/Île-de-France counterparts, while allowing the specific data-scarcity
adaptations described below.

The pipeline proceeds through four broad phases, mirroring Figure 2 of Sallard et al.
(2020):

1. **Pre-processing** the household travel survey and auxiliary spatial data.
2. **Creating synthetic households** via Iterative Proportional Updating (IPU) and
   Truncate-Replicate-Sample (TRS) integerization.
3. **Imputing primary activity locations** (home, work, education).
4. **Imputing secondary activity locations** (shopping, leisure, other) via a
   relaxation-discretization algorithm.

A rendered stage-dependency diagram is available at
`paper/figures/pipeline_flowchart.png` and may be included alongside this description
as an illustrative figure.

**Suggested opening paragraph(s) for the Methodology section:**

> Raw survey, census, and geospatial data for Dhaka are transformed into a MATSim-ready
> synthetic population and mobility scenario through five stages: input preparation,
> population synthesis, activity-location assignment, schedule assembly, and scenario
> assembly. Five data sources are drawn on in parallel: the DTCA household travel
> survey (52,672 households), the BBS 2022 census (both a ward-level community report
> and a coarser district-level workbook), ward administrative boundaries, a nationwide
> OpenStreetMap building extract, and a self-built GTFS transit feed paired with an
> OpenStreetMap road network (see the companion GTFS methodology document). Each source
> is first cleaned and spatially reconciled on its own terms: survey trip purposes,
> modes, and distances are recoded and resolved against ward geometry; the census
> tables are extracted from source documents and cross-validated internally; the two
> city corporations' independent ward-numbering schemes are unified into a single
> identifier space; and buildings are classified into candidate activity types.
>
> These prepared inputs then drive population synthesis proper: census-derived control
> totals for age, sex, and household size — falling back to survey-derived totals only
> where no ward-level census coverage exists — are used to reweight the household
> travel survey sample itself via Iterative Proportional Updating, followed by
> Truncate-Replicate-Sample integerization into a discrete synthetic household
> population. Each synthetic household is subsequently assigned a ward through a
> weighted draw from the real census ward distribution, linked back to the survey
> behaviorally through statistical matching, and given home, work, education, and
> secondary activity locations through distance-distribution-based matching against the
> classified building and OpenStreetMap candidate pool. A full daily activity and trip
> schedule is then assembled for every synthetic person. Finally, this demand-side
> output is combined with an independently prepared supply side — a road network and
> transit schedule built externally via `pt2matsim` — to produce a complete MATSim-ready
> scenario. The remainder of this section details each of these stages in turn.

---

## 2. Pre-processing the input data

### 2.1 Data source

The core demand-side input is the DTCA (Dhaka Transport Coordination Authority)
household travel survey (`dtca_full.xlsx`), covering Dhaka North City Corporation
(DNCC), Dhaka South City Corporation (DSCC), and the neighboring upazilas of Savar and
Keraniganj. Unlike the São Paulo and Seville household travel surveys, in which only one
household member is typically interviewed in full and other members' attributes must be
imputed, the DTCA survey records every household member directly — age, sex,
employment, education, driving-license status, and personal vehicle ownership are all
observed, not imputed. This removes an entire imputation stage (household-member
attribute synthesis) that both the Seville and São Paulo pipelines require, at the cost
of dependence on a single-source, single-year survey with no independent household
travel survey for cross-validation.

The survey's own expansion factor (`Exp_Fac_231206`) is used throughout as the
person-, household-, and trip-level sample weight, playing the same role as the São
Paulo HTS weight column: it is what the raking step (§3) targets, and what the
ward-assignment draw (§3.5) uses to scale a real, geographically resolved sample up to
the full study-area population.

### 2.2 Trip purpose and mode categorization

Following the same simplification logic as Sallard et al. (2020) — who collapse
purposes into six categories and modes into eight — trip purposes are collapsed to four
categories (home, work, education, other; the DTCA survey does not separately code
shopping or leisure trip legs) by splitting each recorded purpose string (e.g. *"Home to
Work Place"*) on its directional keyword.

Transport modes, however, are collapsed to **seven** categories rather than the
standard five used in the Seville pipeline (walk, public transport, bike, car, car
passenger), because two locally dominant modes have no equivalent in that standard set:

| Mode category | Raw survey values folded in | Share of trips |
|---|---|---|
| Walk | Walking | — |
| Bike | Bicycle | — |
| **Rickshaw** | Rickshaw | ~30–35% |
| **Paratransit** | 3-wheeler CNG/Auto (shared or reserved) | — |
| Car | Private car/microbus/jeep, motorcycle (incl. ride-share), taxi/ride-share, staff/assigned vehicles, truck/pickup | — |
| Public transport (PT) | Bus, mini bus, staff bus, school/college bus, school van, Laguna/Tempu, metro rail, train, water taxi | — |
| Other | Unclassified | — |

Rickshaw alone accounts for roughly a third of all recorded trips — larger than bike,
public transport, or car passenger individually — so folding it into an existing
category (as would be conventional in a Global North context) would materially distort
the mode-share validation. This is one of the paper's explicit adaptations for a
non-Western urban mobility context and is worth stating as such.

### 2.3 Employment, license, and income categorization

Employment status, student status, and driving-license possession are derived directly
from observed survey fields (non-null employment record, non-null school-level record,
license field ≠ "No License" respectively) rather than imputed or matched against an
external registry — again a simplification relative to Seville, where driving license
must be imputed for all but the primary respondent via a Bernoulli draw calibrated
against government license statistics. Household income is captured as an ordinal
`income_class` derived from nine reported Taka brackets (e.g. *"Tk 80,000 to Tk
100,000"*), replacing Seville's placeholder of a hardcoded zero household income.

### 2.4 Spatial referencing

Administrative wards from the DNCC and DSCC city corporations form the finest available
zoning unit (analogous to São Paulo's 633 IBGE zones or Seville's INE census sections).
Because DNCC and DSCC maintain independent, overlapping ward-numbering schemes (both
have a "Ward 01", for instance), zone identifiers are prefixed by city corporation
(`S07`, `N54`, …). Savar and Keraniganj, the two surrounding upazilas with DTCA survey
coverage but no ward-level administrative subdivision in the available data, are each
modeled as a single coarse zone.

---

## 3. Creating synthetic households: Iterative Proportional Updating and Truncate-Replicate-Sample

Unlike the São Paulo pipeline — which directly expands a fully individual-level census
by its sampling weight and subsequently hot-deck matches each expanded individual to a
household travel survey respondent — the Dhaka pipeline (following the Seville/Île-de-
France baseline) synthesizes households by **reweighting the household travel survey
sample itself** against independent control totals, using Iterative Proportional
Updating (IPU; Ye et al., 2009) followed by Truncate-Replicate-Sample (TRS)
integerization. This is a deliberate methodological choice suited to the Dhaka context:
no individual-level census microdata is available (only tabulated ward-level summary
counts), so a São-Paulo-style direct census expansion is not possible, whereas IPU is
designed exactly for the case where only marginal control totals are observed.

### 3.1 Control totals

Two independent census sources are combined, at different spatial grains, to build the
IPU control totals:

- **Ward-level marginals** (primary control totals). The BBS 2022 *Population and
  Housing Census, Community Report: Dhaka* tabulates, for every individual DNCC and DSCC
  ward: household counts and population by sex (Table C-01), and population by
  seventeen 5-year age groups (Table C-02). These tables were extracted programmatically
  from the report PDF and cross-validated internally — Tables C-01 and C-02 agree
  exactly on every ward's population total, and the ward-level sums reproduce the
  report's own printed city-corporation subtotals exactly (DSCC: 4,305,063 population /
  1,101,733 households; DNCC: 5,990,723 population / 1,634,550 households).
- **District-level workbook** (household-size shape only). The machine-readable BBS
  2022 district-level dataset provides a household-size distribution (1/2/3/4/5+
  persons) that has no ward-level equivalent in the Community Report; this shape is used
  to construct the household-size control total, rescaled to the census-derived
  household count.
- **HTS-derived fallback** for Savar and Keraniganj, the two upazilas with no
  ward-level census breakdown in the available report.

Two simplifications follow directly from what the census tables provide: (i) the
synthetic population targets the population aged five and over, since the survey
roster contains no individuals below age five and the corresponding census 0-4 bin is
therefore structurally unfillable by raking; and (ii) because Table C-01 reports sex
without age and Table C-02 reports age without sex, no joint age × sex ward table
exists, so the joint control total used by IPU is constructed as the outer product of
the two independent marginals — the maximum-entropy joint distribution consistent with
both observed margins.

### 3.2 IPU formulation

Let $w_h^{(0)}$ denote the initial sample weight of household $h$ (its survey expansion
factor), and let $\mathcal{C}$ be the set of control-total categories (e.g. `age_class
= 15`, `household_size_capped = 3`). For each category $c \in \mathcal{C}$ with target
total $T_c$, IPU computes an adjustment factor at each iteration $t$:

$$
f_c^{(t)} = \frac{T_c}{\sum_{h \,:\, h \in c} w_h^{(t)}}
$$

and updates the weight of every household (or person) matching category $c$:

$$
w_h^{(t+1)} = w_h^{(t)} \cdot f_c^{(t)} \quad \text{for all } h \in c
$$

Iterating over all categories in $\mathcal{C}$ and repeating until every category's
weighted total is within a specified tolerance of its target ($10^{-3}$ in this
implementation, capped at 300 iterations) yields a set of household weights that
simultaneously satisfy every marginal control total as closely as the seed sample's
structure allows.

A methodological requirement specific to *person-level* control totals (age, sex) —
which is not needed for genuinely household-level totals (household size) — deserves
explicit statement: because a household travel survey lists multiple household members
per household, a weight adjustment triggered by matching one person's age/sex category
must be broadcast to **every member of that person's household**, not applied to that
person's row in isolation. This preserves a single, internally consistent weight per
household, which the following integerization step requires; failing to do so would let
a later step effectively drop the correction for any household member who was not the
representative row for their household. All raking categories in this implementation —
household size, age, and sex — accordingly broadcast to the full household.

### 3.3 Truncate-Replicate-Sample (TRS) integerization

IPU produces continuous, non-integer household weights. TRS converts these into a
concrete, whole-number population: each household's raked weight is decomposed into an
integer replication count plus a fractional remainder, the integer replicas are
materialized deterministically, and the fractional remainders across all households are
resolved via weighted random sampling until the target total household count is
reached. Every synthetic household is therefore literally a resampled real survey
household, replicated as many times as its raked weight implies — no synthetic
household is a statistical composite of multiple real ones.

At full census scale (`sampling_rate: 1.0`), this replicates a roughly 38,000-household
survey sample into approximately 2.7 million synthetic households; the default
`sampling_rate: 0.01` configuration produces roughly 34,000 households (118,000
persons), scaled proportionally. The implementation performs this replication via a
single vectorized array-gather operation rather than a per-replica loop, for tractable
runtime at full census scale.

### 3.4 Ward assignment

Each synthetic household is assigned a ward (`commune_id`) by a population-weighted
random draw, using the real census household count per ward (Table C-01) as the draw
weight for all DNCC/DSCC wards — the same source that anchors the demographic control
totals in §3.1, ensuring that a synthetic population's geography and demographic
composition are drawn from mutually consistent sources. Savar and Keraniganj, lacking
ward-level census rows, retain an HTS-expansion-weighted household count for this draw.

### 3.5 Explicit scope caveat

All census-anchored quantities above (§3.1, §3.4) cover DNCC and DSCC only. For Savar
and Keraniganj — together roughly a fifth of the synthesized households — population
scale, demographic composition, and spatial placement rest solely on the DTCA survey
and its expansion factors, with no independent ward-level census verification. This
should be stated as an explicit scope limitation of the demographic anchoring, not
elided.

---

## 4. Imputing primary locations (home, work, education)

### 4.1 Facility location data

Facility candidates are sourced from a Geofabrik OpenStreetMap building-polygon extract
(nationwide, over 11 million polygons, spatially clipped to the study area at read
time), supplemented by point-of-interest and amenity-tagged polygon data read directly
from a Bangladesh `.osm.pbf` extract. The buildings file's `type` tag is populated on
only around one percent of records; where present, it is used to classify a building as
a work, education, shop, or leisure candidate. The OSM point-amenity extraction
supplies the majority of typed work/education/shop/leisure candidates by count (on the
order of 27,000 additional candidates against roughly 3,200 typed buildings), and is
treated as optional — the pipeline falls back to buildings-only candidates, with a
logged warning, if the `.pbf` file is not supplied. Untyped building footprints (the
remaining ~99%) are treated as home candidates, restricted to a plausible residential
floor-area range (40-400 m²), following the same heuristic used in the Seville
pipeline (whose building registry is likewise largely untyped).

### 4.2 Home location assignment

Each synthetic household is assigned a home location by drawing among the residential
candidate pool located within its assigned ward.

### 4.3 Work and education location assignment

Work and education location assignment follows a distance-matching approach
conceptually similar to São Paulo's origin-destination-matrix method, adapted to the
absence of a directly usable OD matrix: for each purpose, an empirical target-distance
distribution is built from the household travel survey's observed home-to-work (or
home-to-education) trip distances, and each synthetic person is assigned the candidate
location — among those reachable from their home location — whose distance most closely
matches a distance drawn from that empirical distribution, located via a k-d tree
nearest-neighbor search over candidate coordinates. As of the current candidate set
(typed buildings merged with OSM point amenities), the education-location match success
rate is approximately 91%, up from approximately 87% using typed buildings alone,
reflecting the improvement contributed by the supplementary OSM amenity data described
in §4.1.

---

## 5. Imputing secondary locations

Secondary activities (shopping, leisure, other) are not fixed to a fully solved
schedule the way primary activities are; instead, following the same method used in the
Seville and Île-de-France pipelines, they are assigned via the CARLA
relaxation-discretization algorithm (Hörl & Axhausen, 2020), which iteratively relaxes a
continuous facility-location assignment problem and discretizes it against the real
facility candidate pool, seeking an assignment whose resulting trip geometry matches
the household travel survey's observed distance and mode distribution for secondary
trips. This stage is used unmodified from the generic pipeline implementation — no
Dhaka-specific adaptation was required.

---

## 6. Validation summary

The synthesized population is validated against three independent reference series —
the household travel survey (self-consistency check), the ward-level census (external
validation for DNCC/DSCC), and, where available, the district-level census workbook —
across age distribution, sex ratio, household-size distribution, employment status,
driving-license possession, activity-chain structure, and trip-distance distribution by
purpose and mode. Full validation figures are in `paper/figures/validation_*.png`
(generated by `paper/scripts/validation.py`) with supplementary figures in
`output/analysis/` (generated by `analysis_dhaka/dhaka/ivt_style/`). Headline results at
the time of writing:

- **Age distribution**: every one of the seventeen 5-year age bins matches the
  ward-level census within approximately 0.3 percentage points.
- **Household-size distribution**: every size bucket (1/2/3/4/5+ persons) matches the
  district-level census within approximately 0.15 percentage points — see the caveat
  below.
- **Sex ratio**: matches the census marginal essentially exactly, by construction (§3.1).

A methodological caveat worth stating explicitly for the household-size result: the
household-travel-survey sample contains only 29 real one-person households (out of
38,092) and 3,313 real five-or-more-person households. Anchoring the household-size
control total to the census — rather than to the survey's own household-size shape,
which under-represents one-person households by roughly a factor of 25 relative to the
census, a known type of household-travel-survey bias — corrects the *aggregate* share
to match census, but does so by replicating a small donor pool many times over rather
than by introducing new household diversity. Analyses that depend on within-household
structure specifically for the smallest and largest household-size buckets should treat
this as a limitation of the donor pool, not of the reweighting method.

---

## 7. Summary: adaptations relative to the Seville/Île-de-France baseline

| Aspect | Baseline (Seville) | Dhaka adaptation | Motivation |
|---|---|---|---|
| Household-member coverage | Primary respondent only; others imputed | Every member observed directly | Survey design difference — removes an imputation stage |
| IPU control totals | Census population/household tables | Ward-level BBS census (age, sex, household count) + district-level workbook (household size) + HTS fallback (Savar/Keraniganj) | No individual-level census microdata available; ward-level tabulations are the finest available grain |
| Zoning unit | Single official census-section scheme | Dual, prefixed ward-numbering scheme (DNCC/DSCC) + two coarse upazila zones | Two independent, overlapping city-corporation ward schemes |
| Trip distance | Real geocoded coordinates | Sampled between random points in origin/destination ward polygons; duration × mode-speed fallback for out-of-area trips | Survey records no destination coordinates and origin coordinates only ~30% of the time |
| Mode set | 5 modes | 7 modes (adds rickshaw, paratransit) | Rickshaw alone is ~30-35% of trips; no standard category fits |
| Driving license | Imputed (Bernoulli, calibrated to license statistics) | Observed directly | Survey design difference |
| Household income | Not modeled (hardcoded zero) | Real ordinal income bracket captured | Survey includes an income question |
| Transit/road supply | Built inside the pipeline (GTFS fetch + pt2matsim) | Built externally (see companion GTFS methodology document) and copied in | No official Bangladesh GTFS feed exists |

---

## References to reuse

- Ye, X., Konduri, K., Pendyala, R. M., Sana, B., & Waddell, P. (2009). A methodology to
  match distributions of both household and person attributes in the generation of
  synthetic populations. *88th Annual Meeting of the Transportation Research Board.*
- Hörl, S., & Balać, M. (2021). Reproducible scenarios for agent-based transport
  simulation: A case study for Paris and Île-de-France.
- Hörl, S., & Axhausen, K. W. (2020). Relaxation-discretization algorithm for spatially
  constrained secondary location assignment. *99th Annual Meeting of the Transportation
  Research Board.*
- Sallard, A., Balać, M., & Hörl, S. (2020). A synthetic population for the greater São
  Paulo metropolitan region. *Working paper, ETH Zürich.*
- Balać, M., & Hörl, S. (2020). Synthetic population for the state of California based
  on open data.
