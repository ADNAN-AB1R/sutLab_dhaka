import traceback

import numpy as np
import pandas as pd

from .synthesis import PopulationSynthesis

"""
This stage ..
"""


def configure(context):
    context.stage("dhaka.ipu.prepare")
    context.stage("dhaka.data.hts.entd.filtered")

    context.config("random_seed", 42)
    context.config("ipu_max_iterations", 300)
    context.config("ipu_tolerance", 1e-3)
    context.config("ipu_apply_trs", True)
    context.config("processes")

    context.config("IPU_aggregation_level")

POP_AGE_CLASSES = [
    0, 5, 10, 15, 20, 25, 30, 35, 40, 45,
    50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 100
]

def execute(context):
    census_targets_by_dept = context.stage("dhaka.ipu.prepare")
    df_households_hts, df_persons_hts, df_trips_hts = context.stage(
        "dhaka.data.hts.entd.filtered"
    )

    # Prepare HTS seed data
    hts_df = df_persons_hts.merge(
        df_households_hts[["household_id", "household_size", "household_weight"]],
        on="household_id",
        how="left",
    )

    # Map HTS ages to Sevilla census population age bins
    hts_df["age_class"] = pd.cut(
        hts_df["age"],
        bins=POP_AGE_CLASSES + [np.inf],
        labels=POP_AGE_CLASSES,
        right=False,
    ).astype(int)

    print(hts_df.info())
    print(hts_df["household_size"])
    hts_df["household_size_capped"] = hts_df["household_size"].clip(upper=5)

    # Create combined employment x age x sex column for constraint
    if "employed" not in hts_df.columns:
        raise ValueError("HTS data missing 'employed' column")

    hts_df["employment_age_sex"] = list(
        zip(hts_df["employed"], hts_df["sex"], hts_df["age_class"])
    )

    print(
        f"HTS seed population: {len(hts_df):,} persons from {len(df_households_hts):,} households"
    )
    print(f"Processing {len(census_targets_by_dept)} departements...")

    # Configuration
    processes = context.config("processes")
    apply_trs = context.config("ipu_apply_trs")
    max_iterations = context.config("ipu_max_iterations")
    tolerance = context.config("ipu_tolerance")
    random_seed = context.config("random_seed")

    # Prepare parallel batches
    departements = sorted(census_targets_by_dept.keys())
    dept_batches = np.array_split(departements, min(processes, len(departements)))

    batches = []
    random = np.random.RandomState(random_seed)
    random_seeds = random.randint(10000, size=len(dept_batches))

    for batch_idx, dept_batch in enumerate(dept_batches):
        batch_targets = {
            dept_id: census_targets_by_dept[dept_id] for dept_id in dept_batch
        }
        batches.append((batch_targets, random_seeds[batch_idx]))

    # Run IPU in parallel
    print(
        f"Running IPU on {len(batches)} parallel batches for {len(departements)} departements..."
    )
    final_population_list = []

    with context.progress(label="Running IPU by departement", total=len(departements)):
        with context.parallel(
            processes=min(processes, len(batches)),
            data=dict(
                hts_df=hts_df,
                apply_trs=apply_trs,
                max_iterations=max_iterations,
                tolerance=tolerance,
            ),
        ) as parallel:
            for batch_result in parallel.imap_unordered(process_ipu_batch, batches):
                final_population_list.extend(batch_result)

    # Combine all departements
    if final_population_list:
        full_synthetic_pop = pd.concat(final_population_list, ignore_index=True)

        print(f"\n{'=' * 70}")
        print("IPU SYNTHESIS COMPLETE")
        print(f"{'=' * 70}")
        print(f"Total synthetic population: {len(full_synthetic_pop):,} persons")
        print(f"Total households: {full_synthetic_pop['household_id'].nunique():,}")
        print(f"Departements covered: {full_synthetic_pop[context.config('IPU_aggregation_level')].nunique()}")

        return full_synthetic_pop
    else:
        print("WARNING: No population synthesized!")
        return pd.DataFrame()


def process_ipu_batch(context, arguments):
    """
    Process a batch of departements in parallel.
    Each worker processes multiple departements sequentially.
    """
    census_targets_by_dept, random_seed = arguments

    hts_df = context.data("hts_df")
    apply_trs = context.data("apply_trs")
    max_iterations = context.data("max_iterations")
    tolerance = context.data("tolerance")

    # Initialize synthesis engine
    # Define which variables are Household level vs Person level
    household_vars = ["household_size_capped"]

    synthesizer = PopulationSynthesis(
        max_iterations=max_iterations,
        tolerance=tolerance,
        random_seed=random_seed,
        household_id_col="household_id",
        person_id_col="person_id",
        household_vars=household_vars,
        verbose=False,
    )

    batch_results = []

    for dept_id in sorted(census_targets_by_dept.keys()):
        dept_targets = census_targets_by_dept[dept_id]

        # Prepare Census Targets for this Zone
        # We need a dictionary: {'age_class': {0: 50, 6: 30...}, 'household_size_capped': {1: 20...}}
        census_targets = {}

        # 1. Age x Sex constraints (person-level)
        # Split combined constraint into separate dimensions
        sex_totals = {}
        age_totals = {}
        for (sex, age_class), count in dept_targets["age_sex"].items():
            sex_totals[sex] = sex_totals.get(sex, 0) + count
            age_totals[age_class] = age_totals.get(age_class, 0) + count

        census_targets["sex"] = sex_totals
        census_targets["age_class"] = age_totals

        # 2. Household size constraints (household-level)
        census_targets["household_size_capped"] = dept_targets["household_size"]

        # Include total constraints if present
        if "total_households" in dept_targets:
            census_targets["total_households"] = dept_targets["total_households"]
        if "total_population" in dept_targets:
            census_targets["total_population"] = dept_targets["total_population"]

        # Run IPU
        # OPTION 1: Use Global Seed (Recommended for small zones)
        # We use the entire HTS as the pool, but we reset weights to the initial survey weights
        try:
            weighted_df = synthesizer.ipu_raking(
                df=hts_df,
                census_targets=census_targets,
                initial_weight_col="household_weight",
            )

            # Debug: check weight distribution after raking
            hh_weights = weighted_df.groupby("household_id")["weight"].first()
            target_hh = sum(dept_targets["household_size"].values())
            if dept_id in ["011", "101"]:  # Sample small and large departments
                print(f"\nDept {dept_id} raking output:")
                print(f"  Target HH: {target_hh}")
                print(f"  Weight sum: {hh_weights.sum():.1f}")
                print(
                    f"  Weight stats: min={hh_weights.min():.4f}, max={hh_weights.max():.2f}, mean={hh_weights.mean():.4f}, median={hh_weights.median():.4f}"
                )
                print(
                    f"  HH with weight >= 1.0: {(hh_weights >= 1.0).sum()} ({(hh_weights >= 1.0).sum() / len(hh_weights) * 100:.1f}%)"
                )
                print(
                    f"  HH with weight < 0.1: {(hh_weights < 0.1).sum()} ({(hh_weights < 0.1).sum() / len(hh_weights) * 100:.1f}%)"
                )

            # Integerize (Create Synthetic Population)
            if apply_trs:
                final_df = synthesizer.integerize_weights(
                    weighted_df, department_id=dept_id
                )
                # Debug: check TRS output
                target_hh = sum(dept_targets["household_size"].values())
                actual_hh = final_df["household_id"].nunique()
                if actual_hh < target_hh * 0.5:  # Less than 50% of target
                    print(
                        f"WARNING: Dept {dept_id} TRS under-generated: target={target_hh}, actual={actual_hh} ({actual_hh / target_hh * 100:.1f}%)"
                    )
            else:
                final_df = weighted_df.copy()

            # Add departement ID to result
            final_df[context.config('IPU_aggregation_level')] = dept_id

            batch_results.append(final_df)
            context.progress.update()

        except Exception as e:
            print(f"ERROR in departement {dept_id}: {str(e)}")

            traceback.print_exc()
            continue

    return batch_results