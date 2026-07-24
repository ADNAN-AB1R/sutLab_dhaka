# Results / Validation: Walkthrough of the `analysis_dhaka` Figure Set

This document explains every figure produced by `analysis_dhaka/dhaka/ivt_style/` (in
`output/analysis/`), for use in the paper's "Insight into the synthesized population" /
validation section (see `docs/paper/methodology_population_synthesis.md` §6 for the
short summary version; this is the full walkthrough). It is a distinct figure set from
`paper/figures/validation_*.png` (produced by `paper/scripts/validation.py`) — the two
are complementary, not duplicates: the `paper/figures/` set is the polished,
paper-ready figure set; the `output/analysis/` set is a broader diagnostic battery
(activity chains, mode/purpose distances, employment, license) that mirrors the
`eqasim` framework's own standard validation battery (the same one Seville and other
`eqasim` cities produce), so it also demonstrates methodological continuity with the
broader framework.

## 0. How to read every figure: the three reference series

Every comparison plot uses up to three series:

- **Synthetic** (orange): the final synthesized population/trips, unweighted (it is
  already an expanded, disaggregate population — no weight is needed).
- **HTS** (blue): the household travel survey sample, weighted by its own expansion
  factor (`person_weight`/`household_weight`), representing the *observed* population
  and travel behavior.
- **Census** (green): real ward-level (age, sex) or district-level (household size)
  government census figures — the *external, independent* ground truth used as the IPU
  control total (see the population-synthesis methodology document, §3.1).

Where all three appear, the correct read is: **Synthetic should track Census closely**
(since IPU explicitly rakes to it), while **Synthetic vs. HTS divergence is expected
wherever HTS itself diverges from Census** — that divergence is a property of the
survey, not a synthesis error.

---

## 1. Population-synthesis fidelity (`compare_ipu.py`)

These two figures check the *raw* IPU/TRS output — before trip generation or attribute
enrichment — directly against the targets it was raked to. They answer "did the raking
algorithm actually converge to what it was asked to hit?", independent of anything
downstream.

### `census_vs_synthesis_ages.png`
Age distribution (5-year bins) for Census (the IPU target), IPU-synthetic, and HTS.
Census and IPU-synthetic should be nearly indistinguishable by construction; any
divergence indicates the raking did not fully converge (e.g. hit `max_iterations`
before reaching `tolerance`). HTS is shown as a third, independent series and is
expected to diverge somewhat from the other two, most visibly around ages 20-29 (see
§5).

### `census_vs_synthesis_households.png`
Household-size distribution (1/2/3/4/5+ persons) for the same three series. Census and
IPU-synthetic should again track closely; HTS is expected to diverge substantially
here — the household travel survey under-represents one-person households and
over-represents three/four-person households relative to census (a known survey
artifact, not a pipeline error; see the donor-pool caveat in §5).

---

## 2. Sociodemographic validation (`analysis.py`)

These figures check the *final* population (after enrichment and trip generation).

### `agedistribution.png`
The primary three-way age-distribution comparison (5-year bins), same structure as
`census_vs_synthesis_ages.png` but computed after the full pipeline (enrichment, trip
generation) rather than immediately post-IPU — a check that nothing downstream of
raking re-introduces age-distribution error.

### `agedistribution_syn_hts.png` / `agedistribution_syn_census.png`
The same age comparison split into two separate two-way plots (Synthetic vs. HTS only;
Synthetic vs. Census only), useful when a three-way bar chart is visually too dense for
a specific figure slot in the paper.

### `agedistribution_differences.png`
Signed percentage-point differences, per age bin, for Synthetic-minus-HTS and
Synthetic-minus-Census. This is the most diagnostic single figure in the set: a large
Synthetic-vs-Census bar indicates a raking problem; a large Synthetic-vs-HTS bar with a
*small* Synthetic-vs-Census bar in the same bin (as seen at ages 20-24 and 25-29 in the
current run) indicates the synthetic population is correctly following census while the
HTS itself is the outlier in that bin — an important distinction to state explicitly
when discussing this figure.

### `summary_horizontal.png`
A single consolidated horizontal bar chart covering age (vs. Census), sex (vs. Census),
employment status (vs. HTS), and driving-license possession (vs. HTS) in one figure —
useful as a compact overview figure if the paper has limited space for a full battery of
separate age/sex/employment/license plots.

### `employmentstatus.png`
Employment status (employed/unemployed) share, Synthetic vs. HTS. No census comparison
exists here — Dhaka's BBS census has no ward-level employment breakdown, so employment
in the synthetic population is HTS-derived rather than census-anchored; this plot is
therefore a self-consistency check (did the synthesis preserve the HTS's own employment
shares?) rather than external validation.

### `drivinglicense.png`
Driving-license possession share, Synthetic vs. HTS — same self-consistency caveat as
employment status above (no census benchmark exists for this attribute).

---

## 3. Activity-chain and mobility-behavior validation

These figures compare the *structure* of each person's daily activity schedule between
Synthetic and HTS (no census equivalent exists for behavioral/mobility variables — the
census is a population-count instrument, not a travel survey).

### `activitychains.png`
Percentage share of each distinct activity chain (e.g. `home-work-home`,
`home-education-home`), ranked by frequency, comparing Synthetic to HTS. A close match
indicates the synthesis correctly preserved the survey's observed daily-schedule
patterns, since trip chains are drawn from real, matched HTS donor schedules rather than
generated from a behavioral model.

### `activitycounts.png`
Distribution of the *number* of activities per person-day (0 through 6+), Synthetic vs.
HTS — a coarser view of the same underlying data as `activitychains.png`.

### `activitycountspurpose.png`
For each purpose (home/work/education/other), how many times that purpose recurs
within a single day's chain (e.g. "work - 1 time" vs. "work - 2 times"), Synthetic vs.
HTS — useful for checking that multi-stop or repeat-purpose behavior (e.g. someone
going to work twice in a day) is preserved, not just single-trip purposes.

---

## 4. Trip-distance validation

These figures compare trip distances (crowfly/great-circle distance in kilometers,
computed as described in the population-synthesis methodology document §2.4)
between Synthetic and HTS, broken out by purpose and by mode.

### `distance_purpose_cdf.png` / `distance_purpose_hist.png`
Cumulative distribution and histogram of trip distance, one subplot per purpose
(home/work/education/other), Synthetic vs. HTS. Close overlap indicates the location-
imputation stage (home/work/education assignment, §4 of the methodology document)
successfully reproduced the survey's observed travel-distance patterns for each
purpose.

### `distance_mode_cdf.png` / `distance_mode_hist.png`
Same comparison, broken out by mode instead of purpose, across all seven Dhaka mode
categories (walk, bike, rickshaw, paratransit, car, pt, other). Close overlap for
walk/bike/rickshaw/paratransit/car/pt indicates good fit; the "other" mode subplot is a
known exception — see §5.

---

## 5. Known caveats to state when citing these figures

Three specific, diagnosed caveats are worth stating explicitly wherever these figures
are cited, rather than left implicit:

1. **"Other"-mode trip distances show visible discrete spikes** (in
   `distance_mode_hist.png` / `distance_mode_cdf.png`), at distances corresponding to
   round trip durations (15/30/45/60 minutes) multiplied by an assumed mode speed. This
   arises because roughly 28% of "other"-mode trips have an unresolvable origin or
   destination ward (they left the study area or used a free-text, unparseable
   location), and therefore fall back to a deterministic duration × speed distance
   estimate rather than a geographically sampled one (§2.4/§4 of the population-
   synthesis methodology document); since survey respondents round reported durations
   to common values, many trips collapse onto the same handful of distance values. This
   affects Synthetic and HTS identically (the synthetic population inherits the HTS's
   own distance column), so it is a property of the distance-imputation method for this
   specific, small mode category (under 1% of all trips), not a synthesis error.
2. **The household-travel survey itself shows an age-heaping pattern around ages
   20-29** relative to census (visible in `agedistribution_differences.png`): HTS
   over-represents ages 20-24 and under-represents ages 25-29 relative to both census
   and the synthetic population. Since the synthetic population is anchored to census,
   not HTS, it correctly does not inherit this specific HTS artifact.
3. **The household-size donor pool is small at the extremes**: the household-size
   distribution matches census almost exactly in aggregate (`census_vs_synthesis_
   households.png`), but this is achieved by replicating a small number of real
   one-person and five-plus-person households many times over, rather than by
   introducing new household diversity in those buckets (see the population-synthesis
   methodology document §6 for the specific donor counts). Any discussion of this
   figure's "good fit" should note that it is an aggregate-share fit, not a
   within-bucket diversity guarantee.
