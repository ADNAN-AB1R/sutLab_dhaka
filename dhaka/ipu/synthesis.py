import random
import time
import warnings
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


class PopulationSynthesis:
    """
    Enhanced Population Synthesis Pipeline with Household Structure Preservation
    """

    def __init__(
        self,
        max_iterations: int,  # = 300,
        tolerance: float,  # = 1e-5,
        random_seed: Optional[int],  # = 42,
        verbose: bool,  #  = True,
        household_id_col: str = "household_id",
        person_id_col: str = "person_id",
        household_vars: List[str] = None,
    ):
        self.max_iterations = max_iterations
        self.tolerance = tolerance
        self.verbose = verbose
        self.household_id_col = household_id_col
        self.person_id_col = person_id_col

        self.household_vars = household_vars if household_vars else []

        if random_seed is not None:
            np.random.seed(random_seed)
            random.seed(int(random_seed))  # stdlib random rejects numpy int types

        # Dictionary to store execution time for each step
        self._performance_stats = {}

    def _log_performance(self, step_name: str, start_time: float):
        """Record and optionally print execution time for a pipeline step."""
        self._performance_stats[step_name] = time.time() - start_time
        if self.verbose:
            print(
                f"Time for {step_name}: {self._performance_stats[step_name]:.2f} seconds"
            )

    def _validate_inputs(
        self, df: pd.DataFrame, census_targets: Dict[str, Dict[str, int]]
    ):
        """Enhanced validation including household structure checks."""
        if df.empty:
            raise ValueError("Input dataframe is empty!")

        if self.household_id_col not in df.columns:
            raise ValueError(f"HH ID {self.household_id_col} missing.")

        if not census_targets:
            raise ValueError("Census targets dictionary is empty!")

        # Check for missing columns (skip special total constraints)
        special_constraints = ["total_households", "total_population"]
        missing_vars = [
            var
            for var in census_targets.keys()
            if var not in df.columns and var not in special_constraints
        ]
        if missing_vars:
            raise ValueError(f"Missing variables in dataframe: {missing_vars}")

    def _analyze_household_structure(self, df: pd.DataFrame) -> Dict:
        """Analyze the original household structure to preserve it during synthesis."""
        household_analysis = {}

        # Group by household
        household_groups = df.groupby(self.household_id_col)

        # Calculate household sizes
        household_sizes = household_groups.size()
        household_analysis["size_distribution"] = (
            household_sizes.value_counts().sort_index()
        )
        household_analysis["avg_size"] = household_sizes.mean()

        if self.verbose:
            print("\nHousehold Structure Analysis:")
            print(f"   Total households: {len(household_sizes):,}")
            print(f"   Average household size: {household_analysis['avg_size']:.2f}")
            print(
                f"   Size distribution: {dict(household_analysis['size_distribution'])}"
            )

        return household_analysis

    def _check_missing_categories(
        self, df: pd.DataFrame, census_targets: Dict[str, Dict[str, int]]
    ) -> Dict[str, List[str]]:
        """Identify categories in census targets that are missing in the synthetic pool."""
        missing = defaultdict(list)

        for var, target_cats in census_targets.items():
            if var not in df.columns:
                missing[var] = list(target_cats.keys())
                continue

            present_cats = set(df[var].dropna().unique())
            missing_cats = set(target_cats.keys()) - present_cats

            if missing_cats:
                missing[var] = list(missing_cats)

        return dict(missing)

    def _create_category_masks(self, df: pd.DataFrame, census_targets: Dict) -> Dict:
        """Pre-compute boolean masks for each category of each variable."""
        masks = {}

        for var, categories in census_targets.items():
            if var not in df.columns:
                continue

            masks[var] = {}

            for category in categories.keys():
                masks[var][category] = (df[var] == category).values

        return masks

    def ipu_raking(
        self,
        df: pd.DataFrame,
        census_targets: Dict[str, Dict[str, int]],
        initial_weight_col: str = "household_weight",
    ) -> pd.DataFrame:
        """
        Iterative Proportional Updating (IPU)
        """
        self._validate_inputs(df, census_targets)

        df_work = df.copy()

        if initial_weight_col in df_work.columns:
            total_target_hh = 0
            for var in self.household_vars:
                if var in census_targets:
                    total_target_hh = sum(census_targets[var].values())
                    break

            hh_weights = df_work.groupby(self.household_id_col)[
                initial_weight_col
            ].first()
            current_total_hh = hh_weights.sum()

            scale_factor = (
                (total_target_hh / current_total_hh)
                if (total_target_hh > 0 and current_total_hh > 0)
                else 1.0
            )

            df_work["weight"] = (
                df_work[self.household_id_col].map(hh_weights) * scale_factor
            )
        else:
            df_work["weight"] = 1.0

        masks = self._create_category_masks(df_work, census_targets)
        weights = df_work["weight"].values

        if self.verbose:
            print(
                f"Starting IPU. Max Iter: {self.max_iterations}. Household Vars: {self.household_vars}"
            )

        current_lr = 0.2

        for iteration in range(1, self.max_iterations + 1):
            max_adjustment = 0.0

            if iteration > 1 and iteration % 25 == 0:
                current_lr = min(1.0, current_lr + 0.1)

            for var_name, target_categories in census_targets.items():
                if var_name not in masks and var_name not in [
                    "total_households",
                    "total_population",
                ]:
                    continue

                is_household_var = var_name in self.household_vars

                for category, target_count in target_categories.items():
                    # Handle special total constraints
                    if var_name == "total_households":
                        # Use sum of household weights, not count of households
                        # Fast approach: get first occurrence of each household
                        df_work["_temp_weight"] = weights
                        hh_first = df_work.drop_duplicates(
                            subset=self.household_id_col, keep="first"
                        )
                        current_sum = hh_first["_temp_weight"].sum()
                        mask = None
                    elif var_name == "total_population":
                        current_sum = weights.sum()
                        mask = None
                    else:
                        if category not in masks[var_name]:
                            continue
                        mask = masks[var_name][category]

                        if is_household_var:
                            subset_ids = df_work.loc[mask, self.household_id_col]
                            unique_mask = ~subset_ids.duplicated()
                            current_sum = weights[mask][unique_mask].sum()
                        else:
                            current_sum = weights[mask].sum()

                    if current_sum == 0:
                        continue

                    adjustment_factor = (target_count / current_sum) ** current_lr

                    if abs(adjustment_factor - 1.0) > 1e-7:
                        # Total constraints: adjust all weights uniformly
                        if var_name in ["total_households", "total_population"]:
                            weights[:] *= adjustment_factor
                        elif is_household_var:
                            affected_hh_ids = df_work.loc[
                                mask, self.household_id_col
                            ].unique()
                            affected_rows_mask = df_work[self.household_id_col].isin(
                                affected_hh_ids
                            )
                            weights[affected_rows_mask] *= adjustment_factor
                        else:
                            weights[mask] *= adjustment_factor

                        max_adjustment = max(
                            max_adjustment, abs(adjustment_factor - 1.0)
                        )

            if self.verbose and iteration % 100 == 0:
                print(f"Iteration {iteration}: max adj {max_adjustment:.6f}")

            if max_adjustment < self.tolerance:
                if self.verbose:
                    print(f"Converged at iteration {iteration}")
                break

        df_work["weight"] = weights
        return df_work

    def generalized_raking(
        self,
        df: pd.DataFrame,
        census_targets: Dict[str, Dict[str, int]],
        area_id: Optional[str] = None,
    ) -> pd.DataFrame:
        """Household-aware generalized raking that maintains household structures."""
        start_time = time.time()

        # Validate inputs
        self._validate_inputs(df, census_targets)

        # Create working copy
        df_work = df.copy()

        # Initialize household-level weights
        household_weights = pd.Series(
            1.0, index=df_work[self.household_id_col].unique()
        )
        df_work["weight"] = df_work[self.household_id_col].map(household_weights)

        if self.verbose:
            area_msg = f" for {area_id}" if area_id else ""
            # Total target population calculation (sums the first category to get this value)
            total_target = sum(
                census_targets.get(list(census_targets.keys())[0], {}).values()
            )
            print(f"\nStarting household-aware raking{area_msg}...")
            print(f"Total target population: {total_target:,}")

        missing = self._check_missing_categories(df_work, census_targets)
        for var, missing_cats in missing.items():
            if missing_cats:
                warnings.warn(f"Variable '{var}' missing categories: {missing_cats}")

        masks = self._create_category_masks(df_work, census_targets)
        weights = df_work["weight"].values  # Iterative adjustment loop

        current_lr = 0.2

        for iteration in range(1, self.max_iterations + 1):
            max_adjustment = 0.0

            if iteration > 1 and iteration % 25 == 0:
                current_lr = min(1.0, current_lr + 0.1)

            for var_name, target_categories in census_targets.items():
                if var_name not in masks and var_name not in [
                    "total_households",
                    "total_population",
                ]:
                    continue

                for category, target_count in target_categories.items():
                    # Handle special total constraints
                    if var_name == "total_households":
                        # Use sum of household weights, not count of households
                        # Fast approach: get first occurrence of each household
                        df_work["_temp_weight"] = weights
                        hh_first = df_work.drop_duplicates(
                            subset=self.household_id_col, keep="first"
                        )
                        current_sum = hh_first["_temp_weight"].sum()
                        mask = None
                    elif var_name == "total_population":
                        current_sum = weights.sum()
                        mask = None
                    else:
                        if category not in masks[var_name]:
                            continue
                        mask = masks[var_name][category]
                        current_sum = weights[mask].sum()

                    if current_sum == 0:
                        continue

                    adjustment_factor = (target_count / current_sum) ** current_lr

                    is_household_var = var_name in self.household_vars

                    if is_household_var or var_name in [
                        "total_households",
                        "total_population",
                    ]:
                        if var_name in ["total_households", "total_population"]:
                            weights[:] *= adjustment_factor
                        else:
                            affected_households = df_work.loc[
                                mask, self.household_id_col
                            ].unique()
                            affected_households_mask = df_work[
                                self.household_id_col
                            ].isin(affected_households)
                            weights[affected_households_mask] *= adjustment_factor
                    else:
                        weights[mask] *= adjustment_factor

                    max_adjustment = max(max_adjustment, abs(adjustment_factor - 1.0))

            if self.verbose and (iteration <= 5 or iteration % 25 == 0):
                print(
                    f"  Iteration {iteration:3d}: max adjustment = {max_adjustment:.6f}"
                )

            if max_adjustment < self.tolerance:
                if self.verbose:
                    print(f"Converged after {iteration} iterations")
                break
        else:
            warnings.warn(
                f"Raking did not converge within {self.max_iterations} iterations."
            )

        df_work["weight"] = weights
        self._log_performance("Household-Aware Raking", start_time)
        return df_work

    def integerize_weights(
        self, weighted_df: pd.DataFrame, department_id: str = None
    ) -> pd.DataFrame:
        """
        Wrapper that calls the stratified household-aware TRS.
        """
        return self.household_aware_trs(weighted_df, department_id=department_id)

    def household_aware_trs(
        self, weighted_df: pd.DataFrame, department_id: str = None
    ) -> pd.DataFrame:
        """Size-stratified household-aware TRS that replicates by household size."""
        start_time = time.time()

        if self.verbose:
            print("\nConverting household weights to integers (stratified by size)...")

        # Calculate household sizes
        df_work = weighted_df.copy()
        hh_sizes = df_work.groupby(self.household_id_col).size()
        df_work["household_size"] = df_work[self.household_id_col].map(hh_sizes)

        # Get unique household data with weights and sizes
        hh_data = df_work[
            [self.household_id_col, "weight", "household_size"]
        ].drop_duplicates(subset=[self.household_id_col])

        # Cap household size for stratification (6+ = 6)
        hh_data["size_capped"] = hh_data["household_size"].apply(lambda x: min(x, 5))

        total_target_hh = int(np.round(hh_data["weight"].sum()))

        if self.verbose:
            print(f"   Starting households: {len(hh_data):,}")
            print(f"   Target households: {total_target_hh:,}")
            print("   Applying size-stratified TRS...")

        # Process each household size stratum separately
        all_replicated = []

        # Use department ID in household IDs to ensure uniqueness across departments
        dept_prefix = f"{department_id}_" if department_id else ""
        replica_counter = 0

        # Calculate target for each size stratum proportionally
        # This ensures sum of targets equals total_target_hh
        size_weights = {}
        for size in sorted(hh_data["size_capped"].unique()):
            size_hhs_temp = hh_data[hh_data["size_capped"] == size]
            size_weights[size] = size_hhs_temp["weight"].sum()

        total_weight = sum(size_weights.values())
        size_targets = {}
        allocated_so_far = 0

        for size in sorted(size_weights.keys()):
            if size == max(size_weights.keys()):
                # Last size gets remainder to ensure exact total
                size_targets[size] = total_target_hh - allocated_so_far
            else:
                # Proportional allocation
                size_targets[size] = int(
                    np.round(size_weights[size] / total_weight * total_target_hh)
                )
                allocated_so_far += size_targets[size]

        for size in sorted(hh_data["size_capped"].unique()):
            # Get households of this size
            size_hhs = hh_data[hh_data["size_capped"] == size].copy()

            if len(size_hhs) == 0:
                continue

            # Use pre-calculated proportional target
            target_for_size = size_targets[size]

            # Integer and fractional parts
            size_hhs["int_weight"] = np.floor(size_hhs["weight"]).astype(int)
            size_hhs["frac_weight"] = size_hhs["weight"] - size_hhs["int_weight"]

            # Replicate integer part
            size_hh_ids = size_hhs[self.household_id_col].tolist()
            size_int_weights = size_hhs["int_weight"].tolist()

            for hh_id, int_weight in zip(size_hh_ids, size_int_weights):
                if int_weight > 0:
                    hh_persons = df_work[df_work[self.household_id_col] == hh_id].copy()
                    for rep in range(int_weight):
                        hh_rep = hh_persons.copy()
                        hh_rep[self.household_id_col] = (
                            f"{dept_prefix}hh_{replica_counter:08d}"
                        )
                        replica_counter += 1
                        all_replicated.append(hh_rep)

            # Handle fractional part
            current_count = size_hhs["int_weight"].sum()
            needed_count = target_for_size - current_count

            if needed_count > 0:
                candidates = size_hhs[size_hhs["frac_weight"] > 0].copy()
                if not candidates.empty:
                    # Probability proportional to fractional weight
                    probs = candidates["frac_weight"] / candidates["frac_weight"].sum()
                    n_sample = needed_count  # Sample exactly what we need

                    selected_hh_ids = np.random.choice(
                        candidates[self.household_id_col],
                        size=n_sample,
                        replace=True,  # Allow same HH to be sampled multiple times
                        p=probs,
                    )

                    for hh_id in selected_hh_ids:
                        hh_persons = df_work[
                            df_work[self.household_id_col] == hh_id
                        ].copy()
                        hh_persons[self.household_id_col] = (
                            f"{dept_prefix}hh_{replica_counter:08d}"
                        )
                        replica_counter += 1
                        all_replicated.append(hh_persons)

            if self.verbose:
                size_label = f"{size}+" if size == 6 else str(size)
                actual_count = current_count + min(
                    needed_count, len(size_hhs[size_hhs["frac_weight"] > 0])
                )
                print(
                    f"     Size {size_label}: target={target_for_size:>6,}, seed={len(size_hhs):>5,}, output={actual_count:>6,}"
                )

        # Combine all strata
        if all_replicated:
            replicated_df = pd.concat(all_replicated, ignore_index=True)
        else:
            replicated_df = df_work.iloc[
                :0
            ].copy()  # Empty dataframe with same structure

        replicated_df["weight"] = 1.0
        replicated_df = replicated_df.reset_index(drop=True)
        replicated_df["Person ID"] = [
            f"person_{i:08d}" for i in range(len(replicated_df))
        ]

        final_hh_sizes = replicated_df.groupby(self.household_id_col).size()

        if self.verbose:
            print(f"   Final households: {len(final_hh_sizes):,}")
            print(f"   Final population: {len(replicated_df):,}")
            print(f"   Average household size: {final_hh_sizes.mean():.2f}")

        self._log_performance("Size-Stratified TRS", start_time)
        return replicated_df

    def run_pipeline(
        self,
        df: pd.DataFrame,
        census_targets: Dict[str, Dict[str, int]],
        method: str = "ipu",
        area_id: Optional[str] = None,
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """Run the enhanced household-aware synthesis pipeline."""
        pipeline_start = time.time()

        print("\nStarting Household-Aware Population Synthesis")
        print("=" * 60)
        print(type(df))
        print(
            f"Loaded {len(df):,} records from {len(df[self.household_id_col].unique()):,} households"
        )

        if method == "ipu":
            weighted_df = self.ipu_raking(df, census_targets)
            final_df = self.integerize_weights(weighted_df)
            return weighted_df, final_df

        weighted_df = self.generalized_raking(df, census_targets, area_id)
        final_df = self.household_aware_trs(weighted_df)

        print(
            f"Household-aware pipeline complete in {time.time() - pipeline_start:.2f}s"
        )
        return weighted_df, final_df

    def diagnostic_check(
        self,
        df: pd.DataFrame,
        census_targets: Dict[str, Dict[str, int]],
        stage: str = "Post-processing",
    ) -> None:
        """Enhanced diagnostic check including household structure validation."""
        print(f"\nHousehold-Aware Diagnostic Check ({stage})")
        print("=" * 60)

        for var_name, target_cats in census_targets.items():
            if var_name not in df.columns:
                print(f"Variable '{var_name}' missing in data")
                continue

            print(f"\n=== {var_name.upper()} ===")
            abs_errors = []

            for category, target_val in target_cats.items():
                actual_val = (
                    df[df[var_name] == category]["weight"].sum()
                    if "weight" in df.columns
                    else (df[var_name] == category).sum()
                )

                abs_err = abs(target_val - actual_val)
                rel_err = abs_err / target_val * 100 if target_val != 0 else 0
                abs_errors.append(rel_err)

                print(
                    f"{category:<15} Target: {target_val:10,.0f}  "
                    f"Actual: {actual_val:10,.0f}  "
                    f"AbsErr: {abs_err:6,.0f}  RelErr%: {rel_err:5.2f}"
                )

            overall_abs_error_pct = np.mean(abs_errors) if abs_errors else 0
            print(f"Overall Abs Error (rounded): {overall_abs_error_pct:.2f}")