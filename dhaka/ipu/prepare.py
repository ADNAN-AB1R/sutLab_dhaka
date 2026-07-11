"""
This stage prepares IPU control totals at the configured aggregation level.

Unlike Seville, targets are NOT derived from an independent census source -
the only available BBS census data is whole-district granularity, which
doesn't match our DSCC-only study area (see dhaka/data/census/population.py).
Instead, targets are built directly from the weighted DTCA HTS sample: its
expansion factors already scale the sample to the true DSCC population, so it
supplies both the shape (age/sex/household-size/employment distribution) and
the absolute scale of the targets from one internally-consistent source.

Aggregates to aggregation level:
- Person-level: age_class x sex
- Household-level: household_size (capped at 5+)
- Employment: age_class x sex x employed (kept for structural parity with
  Seville's contract; the IPU raking itself only consumes age_sex/household_size)

NOTE: with the study area fixed to one ward shapefile / one HTS filter, there is
currently only a single aggregation area ("1", the whole DSCC study area).
"""
import pandas as pd

POP_AGE_CLASSES = [
    0, 5, 10, 15, 20, 25, 30, 35, 40, 45,
    50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 100
]

def configure(context):
    context.stage("dhaka.data.hts.entd.filtered")
    context.config("sampling_rate", 1.0)
    context.config("IPU_aggregation_level")

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
    # for both households and persons) - no merge needed

    df_households = df_households.copy()
    df_households["household_size_capped"] = df_households["household_size"].clip(upper = 5)

    aggregation_areas = sorted(df_persons[aggregation_level].unique())
    print(f"Preparing IPU targets for {len(aggregation_areas)} {aggregation_level}(s)...")

    targets_by_aggregation_area = {}

    for aggregation_area_id in aggregation_areas:
        df_pop = df_persons[df_persons[aggregation_level] == aggregation_area_id]
        df_hh = df_households[df_households[aggregation_level] == aggregation_area_id]

        age_sex_targets = (
            df_pop.groupby(["sex", "age_class"], observed = True)["person_weight"]
            .sum().mul(sampling_rate).to_dict()
        )

        household_size_targets = (
            df_hh.groupby("household_size_capped", observed = True)["household_weight"]
            .sum().mul(sampling_rate).to_dict()
        )

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
