"""
This stage prepares IPU control totals at the configured aggregation level.

Person-level age/sex targets are now anchored to real ward-level census data
where it exists: BBS's 2022 "Population and Housing Census, Community Report:
Dhaka" PDF tabulates Tables C-01 (household/population/sex by ward) and C-02
(population by 17 age groups by ward) down to individual DNCC/DSCC wards -
see raw_data/Dhaka/census/extracted/census_c0{1,2}_wards.csv, extracted and
cross-validated (both tables agree on every ward's population total, and
DSCC/DNCC sums match the report's own printed city-corp subtotals exactly).

This report has no ward/union-level breakdown for Savar/Keraniganj (only
DNCC/DSCC get individual ward rows; the two peri-urban upazilas are covered
elsewhere in the same report at a much coarser mauza/union grain that isn't
extracted here) - see dhaka/data/census/population.py and the Savar/Keraniganj
scoping discussion. For those two zones we fall back to the same HTS-expansion
approach used everywhere before this change, and add it on top of the census
DNCC+DSCC totals so the final target still covers the whole study area.

The synthetic population targets the population AGED 5 AND OVER: the DTCA
HTS roster contains no persons under age 5 at all (minimum age is 5), so the
census 0-4 bin (~758k people, 7.3% of the DNCC+DSCC population) is
structurally unreachable by IPU - raking cannot create people that don't
exist in the seed. Rather than carrying a permanently-unfillable target (and
a corresponding unexplained shortfall in every validation figure), the 0-4
bin is dropped from the age marginal, and the sex marginal is rescaled to
the 5+ total (preserving the census sex ratio; the census has no ward-level
sex split of the 0-4 bin to subtract exactly). Under-5s generate no
independent trips in MATSim, so this doesn't affect travel demand.

Two further simplifications, both driven by what the census table itself
provides:
- The census's oldest age band is "80+" (one group), while POP_AGE_CLASSES
  (matching the HTS-seed binning used in dhaka/ipu/population.py) has 5-year
  bins through 100. All census 80+ persons are assigned to the age_class=80
  bin; no ward-level census control exists to split them further.
- The census also reports a small "Hijra" sex category (~0.004% of
  population) with no equivalent in the HTS `sex` column, so it's excluded
  from the sex marginal (and left for the age marginal, which is sex-blind
  and unaffected).

No joint age x sex table exists at ward level (C-01 gives sex without age,
C-02 gives age without sex), so the `age_sex` dict this stage still outputs
(to keep the contract used by dhaka/ipu/population.py unchanged) is
reconstructed from the two marginals via an independence assumption (outer
product): this is the maximum-entropy joint distribution consistent with the
real marginals, and dhaka/ipu/population.py only ever re-derives the same two
marginals back out of it before raking, so the assumption has no effect on
what IPU actually constrains against.

Household-size has no *ward-level* census breakdown, but the coarser
district-level BBS workbook (dhaka/data/census/population.py) does report a
household-size distribution (1/2/3/4/5+ persons) for the whole of Dhaka
district. That shape - the best available, even though it isn't
DNCC+DSCC-specific - replaces the previous HTS-derived shape (which was
shown, via the raw survey roster, to under-count 1-person households by
roughly 25x relative to this census figure: a real DTCA survey artifact,
not a pipeline bug - see docs/PIPELINE_DOCUMENTATION.md). The shape's
absolute total is still anchored to the combined (census DNCC+DSCC +
HTS-estimated Savar/Keraniganj) household count, exactly as before.

Employment: age_class x sex x employed (kept for structural parity with
Seville's contract; the IPU raking itself only consumes age_sex/household_size)
- stays HTS-derived, since the census has no employment breakdown either.

NOTE: with the study area fixed to one ward shapefile / one HTS filter, there is
currently only a single aggregation area ("1", the whole study area).
"""
import os
import pandas as pd

import dhaka.wards as wards

POP_AGE_CLASSES = [
    0, 5, 10, 15, 20, 25, 30, 35, 40, 45,
    50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 100
]

# The census's own 17 age groups (0-4, 5-9, ..., 80+), in column order,
# mapped onto the POP_AGE_CLASSES labels above ("80+" collapses onto 80).
CENSUS_AGE_CLASSES = [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80]

# Age classes excluded from the targets because the HTS seed cannot fill them
# (the DTCA roster has no persons under 5 - see module docstring)
EXCLUDED_AGE_CLASSES = [0]

PERIURBAN_ZONES = list(wards.UPAZILA_ZONES.values())  # ["SAVAR", "KERANIGANJ"]

def configure(context):
    context.stage("dhaka.data.hts.entd.filtered")
    context.stage("dhaka.data.census.population")
    context.config("sampling_rate", 1.0)
    context.config("IPU_aggregation_level")
    context.config("data_path")
    context.config("dhaka.census_wards_c01", "census/extracted/census_c01_wards.csv")
    context.config("dhaka.census_wards_c02", "census/extracted/census_c02_wards.csv")

def load_census_household_size_shape(context):
    """District-level household-size shares (1/2/3/4/5+), the finest grain
    available for this attribute - see module docstring. Returned as
    proportions keyed by the same household_size_capped scheme used
    elsewhere (int 1-4, int 5 standing in for "5+")."""
    summary = context.stage("dhaka.data.census.population")
    raw_shares = summary["household_size"]

    total = sum(raw_shares.values())
    return {
        (5 if bucket == "5+" else bucket): count / total
        for bucket, count in raw_shares.items()
    }

def _census_paths(context):
    data_path = context.config("data_path")
    return (
        f"{data_path}/{context.config('dhaka.census_wards_c01')}",
        f"{data_path}/{context.config('dhaka.census_wards_c02')}",
    )

def load_census_marginals(context):
    """DNCC+DSCC-wide sex marginal, age marginal, and household total, summed
    across all extracted ward rows (see module docstring)."""
    c01_path, c02_path = _census_paths(context)

    df_c01 = pd.read_csv(c01_path)
    df_c02 = pd.read_csv(c02_path)

    sex_totals = {
        "male": df_c01["pop_male"].sum(),
        "female": df_c01["pop_female"].sum(),
    }

    age_columns = [f"age_{group}" for group in [
        "0-4", "5-9", "10-14", "15-19", "20-24", "25-29", "30-34", "35-39",
        "40-44", "45-49", "50-54", "55-59", "60-64", "65-69", "70-74", "75-79", "80+"
    ]]
    age_totals = {
        label: df_c02[column].sum()
        for label, column in zip(CENSUS_AGE_CLASSES, age_columns)
        if label not in EXCLUDED_AGE_CLASSES
    }

    household_total = df_c01["hh_total"].sum()

    return sex_totals, age_totals, household_total

def execute(context):
    aggregation_level = context.config("IPU_aggregation_level")
    sampling_rate = context.config("sampling_rate")

    df_households, df_persons, df_trips = context.stage("dhaka.data.hts.entd.filtered")

    df_persons = df_persons.copy()
    df_persons["age_class"] = pd.cut(
        df_persons["age"], bins = POP_AGE_CLASSES + [1000],
        labels = POP_AGE_CLASSES, right = False
    ).astype(int)

    # df_persons already carries `aggregation_level` directly (set in cleaned.py
    # for both households and persons) - no merge needed, but commune_id (used
    # below to isolate the Savar/Keraniganj fallback) only lives on households.
    df_persons = df_persons.merge(
        df_households[["household_id", "commune_id"]], on = "household_id", how = "left"
    )

    df_households = df_households.copy()
    df_households["household_size_capped"] = df_households["household_size"].clip(upper = 5)

    census_sex_totals, census_age_totals, census_household_total = load_census_marginals(context)
    census_household_size_shape = load_census_household_size_shape(context)

    # Census marginals are full-population counts - scale them down to the
    # requested synthesis sample size, same as every HTS-derived target below
    census_sex_totals = {k: v * sampling_rate for k, v in census_sex_totals.items()}
    census_age_totals = {k: v * sampling_rate for k, v in census_age_totals.items()}
    census_household_total = census_household_total * sampling_rate

    aggregation_areas = sorted(df_persons[aggregation_level].unique())
    print(f"Preparing IPU targets for {len(aggregation_areas)} {aggregation_level}(s)...")

    targets_by_aggregation_area = {}

    for aggregation_area_id in aggregation_areas:
        df_pop = df_persons[df_persons[aggregation_level] == aggregation_area_id]
        df_hh = df_households[df_households[aggregation_level] == aggregation_area_id]

        # HTS-derived fallback, restricted to Savar/Keraniganj, added on top
        # of the census DNCC+DSCC totals (see module docstring).
        df_pop_periurban = df_pop[df_pop["commune_id"].isin(PERIURBAN_ZONES)]
        df_hh_periurban = df_hh[df_hh["commune_id"].isin(PERIURBAN_ZONES)]

        periurban_sex_totals = (
            df_pop_periurban.groupby("sex", observed = True)["person_weight"]
            .sum().mul(sampling_rate).to_dict()
        )
        periurban_age_totals = (
            df_pop_periurban.groupby("age_class", observed = True)["person_weight"]
            .sum().mul(sampling_rate).to_dict()
        )
        periurban_household_total = df_hh_periurban["household_weight"].sum() * sampling_rate

        final_sex_totals = {
            sex: census_sex_totals.get(sex, 0) + periurban_sex_totals.get(sex, 0)
            for sex in set(census_sex_totals) | set(periurban_sex_totals)
        }
        final_age_totals = {
            age: census_age_totals.get(age, 0) + periurban_age_totals.get(age, 0)
            for age in set(census_age_totals) | set(periurban_age_totals)
        }

        # The sex marginal excludes the census's small "Hijra" category (see
        # module docstring) so its total falls slightly short of the age
        # marginal's; rescale it to match exactly so the age x sex outer
        # product below reproduces both marginals exactly when re-summed.
        total_population = sum(final_age_totals.values())
        sex_total_sum = sum(final_sex_totals.values())
        if sex_total_sum > 0:
            final_sex_totals = {
                sex: count * total_population / sex_total_sum
                for sex, count in final_sex_totals.items()
            }

        age_sex_targets = {
            (sex, age): sex_count * final_age_totals[age] / total_population
            for sex, sex_count in final_sex_totals.items()
            for age in final_age_totals
        } if total_population > 0 else {}

        # District-level census household-size shape (see module docstring),
        # scaled to the combined (census DNCC+DSCC + HTS-estimated
        # Savar/Keraniganj) household count for this aggregation area.
        target_household_total = census_household_total + periurban_household_total
        household_size_targets = {
            size: share * target_household_total
            for size, share in census_household_size_shape.items()
        }

        employment_targets = (
            df_pop.groupby(["sex", "age_class", "employed"], observed = True)["person_weight"]
            .sum().mul(sampling_rate).to_dict()
        )

        targets_by_aggregation_area[aggregation_area_id] = {
            "age_sex": age_sex_targets,
            "household_size": household_size_targets,
            "employment": employment_targets,
        }

    print(f"Prepared targets for {len(targets_by_aggregation_area)} {aggregation_level}(s)")

    return targets_by_aggregation_area

def validate(context):
    for path in _census_paths(context):
        if not os.path.exists(path):
            raise RuntimeError(f"Ward-level census file is not available at location {path}")
