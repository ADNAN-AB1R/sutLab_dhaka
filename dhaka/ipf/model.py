import pandas as pd
import numpy as np
import itertools

def configure(context):
    context.stage("seville.ipf.prepare")

def execute(context):
    df_population, df_employment, df_licenses_municipality = context.stage("seville.ipf.prepare")

    EPSILON = 1e-10
    MAX_UPDATE = 1e8
    MIN_UPDATE = 1e-8
    TOL = 5e-3
    MAX_ITER = 1000

    # -------------------------
    # Age class handling
    # -------------------------
    population_age_classes = np.sort(df_population["age_class"].unique())
    employment_age_classes = np.sort(df_employment["age_class"].unique())
    license_age_classes = np.sort(df_licenses_municipality["age_class"].unique())

    combined_age_classes = np.array(np.sort(list(
        set(population_age_classes) |
        set(employment_age_classes) |
        set(license_age_classes)
    )))

    # Build mappings from combined age -> dataset age class.
    # Use safe searchsorted style mapping (treat classes as lower bounds)
    def make_age_mapping(src_bins, target_bins):
        # both sorted arrays
        mapping = {}
        for v in src_bins:
            # find index in target_bins such that target_bins[idx] <= v < next
            idx = np.searchsorted(target_bins, v, side='right') - 1
            if idx < 0:
                idx = 0
            if idx >= len(target_bins):
                idx = len(target_bins) - 1
            mapping[v] = target_bins[idx]
        return mapping

    population_age_mapping = make_age_mapping(combined_age_classes, population_age_classes)
    employment_age_mapping = make_age_mapping(combined_age_classes, employment_age_classes)
    license_age_mapping = make_age_mapping(combined_age_classes, license_age_classes)

    # -------------------------
    # Unique categories
    # -------------------------
    unique_sexes = np.sort(list(set(df_population["sex"]) | set(df_employment["sex"])))
    unique_employed = [True, False]
    unique_license = [True, False]
    unique_census_sections = np.sort(df_population["census_section_index"].unique())
    unique_municipalities = np.sort(df_population["municipality_index"].unique())
    # provinces not used any more
    # unique_provinces = np.sort(df_employment["province_index"].unique())

    # ---------------
    # Build df_model
    # ---------------
    index = pd.MultiIndex.from_product([
        unique_census_sections, unique_sexes, combined_age_classes, unique_employed, unique_license
    ], names=["census_section_index", "sex", "combined_age_class", "employed", "license"])

    df_model = pd.DataFrame(index=index).reset_index()

    # Attach municipality and province indices
    df_spatial = df_population[["census_section_index", "municipality_index", "province_index"]].drop_duplicates()
    df_model["municipality_index"] = df_model["census_section_index"].replace(dict(zip(
        df_spatial["census_section_index"], df_spatial["municipality_index"]
    )))
    df_model["province_index"] = df_model["census_section_index"].replace(dict(zip(
        df_spatial["census_section_index"], df_spatial["province_index"]
    )))

    # map age classes
    df_model["age_class_population"] = df_model["combined_age_class"].replace(population_age_mapping)
    df_model["age_class_employment"] = df_model["combined_age_class"].replace(employment_age_mapping)
    df_model["age_class_license"] = df_model["combined_age_class"].replace(license_age_mapping)

    # -------------------------
    # Seed df_model weights from population marginals (better starting point)
    # -------------------------

    population_total = float(df_population["weight"].sum())

    # compute population marginal per (census_section, sex, age_class)
    pop_marg = df_population.groupby(["census_section_index", "sex", "age_class"])["weight"].sum()

    # map keys for each model row
    keys = list(zip(df_model["census_section_index"], df_model["sex"], df_model["age_class_population"]))
    pop_values = np.array([pop_marg.get(k, 0.0) for k in keys], dtype=float)

    # count how many model rows correspond to each (cs,sex,age) to distribute pop_values evenly across employed/license booleans
    combo_counts = df_model.groupby(["census_section_index", "sex", "age_class_population"]).size().to_dict()
    counts = np.array([combo_counts.get(k, 1) for k in keys], dtype=float)
    seed_weights = pop_values / np.maximum(counts, 1.0)

    # floor tiny zeros and normalize to population_total
    seed_weights = np.maximum(seed_weights, 1e-6)
    seed_weights *= population_total / seed_weights.sum()
    
    df_model["weight"] = seed_weights

    # -------------------------
    # Build selectors + targets
    #   - group A: population + employment
    #   - group B: licenses
    # -------------------------
    selectors_A = []
    targets_A = []

    # Population constraints (census_section x sex x pop_age)
    pop_combinations = list(itertools.product(unique_census_sections, unique_sexes, population_age_classes))
    for cs_idx, sex, age in context.progress(pop_combinations, total=len(pop_combinations), label="Pop constraints"):
        f_reference = (
            (df_population["census_section_index"] == cs_idx) 
            & (df_population["sex"] == sex) 
            & (df_population["age_class"] == age)
        )
        f_model = (
            (df_model["census_section_index"] == cs_idx) 
            & (df_model["sex"] == sex) 
            & (df_model["age_class_population"] == age)
        )
        selectors_A.append(f_model)
        target = float(df_population.loc[f_reference, "weight"].sum())
        # TODO: check
        # if zero but model rows exist, use EPSILON to avoid strict impossible zeros (optionally)
        if target == 0.0 and f_model.sum() > 0:
            target = EPSILON
        targets_A.append(target)

    # Employment constraints (census_section x sex x emp_age)
    emp_combinations = list(itertools.product(unique_census_sections, unique_sexes, employment_age_classes))
    for cs_idx, sex, age in context.progress(emp_combinations, total=len(emp_combinations), label="Emp constraints"):
        f_reference = (
            (df_employment["census_section_index"] == cs_idx) 
            & (df_employment["sex"] == sex) 
            & (df_employment["age_class"] == age)
        )
        f_model = (
            (df_model["census_section_index"] == cs_idx) 
            & (df_model["sex"] == sex) 
            & (df_model["age_class_employment"] == age) 
            & (df_model["employed"])
        )
        selectors_A.append(f_model)
        target = float(df_employment.loc[f_reference, "weight"].sum())
        # TODO: check
        # if zero but model rows exist, use EPSILON to avoid strict impossible zeros (optionally)
        if target == 0.0 and f_model.sum() > 0:
            target = EPSILON
        targets_A.append(target)

    # License constraints (municipality x sex x license_age_class) - group B
    selectors_B = []
    targets_B = []
    lic_combinations = list(itertools.product(unique_municipalities, unique_sexes, license_age_classes))
    for mun_idx, sex, age in context.progress(lic_combinations, total=len(lic_combinations), label="Lic constraints"):
        f_reference = (
            (df_licenses_municipality["municipality_index"] == mun_idx) 
            &(df_licenses_municipality["sex"] == sex) 
            &(df_licenses_municipality["age_class"] == age)
        )
        f_model = (
            (df_model["municipality_index"] == mun_idx) 
            &(df_model["sex"] == sex) 
            &(df_model["age_class_license"] == age) 
            &(df_model["license"])
        )
        selectors_B.append(f_model)
        target = float(df_licenses_municipality.loc[f_reference, "weight"].sum())
        # TODO: check
        # if zero but model rows exist, use EPSILON to avoid strict impossible zeros (optionally)
        if target == 0.0 and f_model.sum() > 0:
            target = EPSILON
        targets_B.append(target)

    # -------------------------
    # Diagnostics summary before IPF
    # -------------------------
    print("Diagnostics before IPF:")
    print("Population total (data):", population_total)
    print("Sum(pop targets):", sum(targets_A[:len(pop_combinations)]))
    print("Sum(emp targets):", sum(targets_A[len(pop_combinations):len(pop_combinations)+len(emp_combinations)]))
    print("Sum(lic targets):", sum(targets_B))
    print("Model rows:", len(df_model))

    # -------------------------
    # Helper: convert boolean selectors -> index arrays and prune empties
    # -------------------------
    def selectors_to_index_and_prune(sel_list, target_list):
        idxs = [np.flatnonzero(s.values) for s in sel_list]
        paired = [(i, t) for i, t in zip(idxs, target_list) if i.size > 0]
        if not paired:
            return [], []
        idxs2, targets2 = zip(*paired)
        return list(idxs2), list(targets2)

    selA_idx, targetA = selectors_to_index_and_prune(selectors_A, targets_A)
    selB_idx, targetB = selectors_to_index_and_prune(selectors_B, targets_B)

    print("Stage A constraints (used):", len(selA_idx))
    print("Stage B constraints (used):", len(selB_idx))

    # -------------------------
    # IPF runner
    # -------------------------
    def run_ipf(weights, selectors_idx, targets, tol=TOL, max_iter=MAX_ITER):
        prev_weights = weights.copy()
        for iteration in range(1, max_iter + 1):
            # iterate through all constraints
            for idxs, target in zip(selectors_idx, targets):
                # idxs is array of integer positions
                current = weights[idxs].sum()
                if current <= 0:
                    # tiny seed to allow flow
                    weights[idxs] += EPSILON
                    current = weights[idxs].sum()
                factor = target / (current + EPSILON)
                factor = float(np.clip(factor, MIN_UPDATE, MAX_UPDATE))
                weights[idxs] *= factor

            rel_change = np.max(np.abs(weights - prev_weights) / (prev_weights + EPSILON))
            if iteration % 50 == 0 or rel_change < tol:
                print(f"IPF iter={iteration}, rel_change={rel_change:.3e}")
            if rel_change < tol:
                return weights, True, iteration
            prev_weights[:] = weights
        return weights, False, max_iter

    # -------------------------
    # Stage 1: population + employment
    # -------------------------
    weights = df_model["weight"].values.copy()
    
    print("Running Stage 1 IPF (population + employment)...")
    if len(selA_idx) == 0:
        raise RuntimeError("No Stage A constraints (population+employment) after pruning - check data.")
    weights, converged_1, it1 = run_ipf(weights, selA_idx, targetA)
    
    assert converged_1, "IPF stage 1 did not converge"
    print("Stage1 converged:", converged_1, "iterations:", it1)

    # -------------------------
    # Stage 2: licenses (start from stage1 weights)
    # -------------------------
    print("Running Stage 2 IPF (licenses) starting from Stage1 solution...")
    if len(selB_idx) == 0:
        raise RuntimeError("No Stage B constraints (license) after pruning - check data.")
    weights, converged_2, it2 = run_ipf(weights, selB_idx, targetB)

    assert converged_2, "IPF stage 2 did not converge"
    print("Stage2 converged:", converged_2, "iterations:", it2)


    # final check
    if not converged_1 or not converged_2:
        print("WARNING: IPF did not fully converge in one of the stages.")
        raise AssertionError("IPF failed to converge")

    print("IPF both stages converged.")
    df_model["weight"] = weights


    # Reestablish sex categories
    df_model["sex"] = df_model["sex"].replace({1: "male", 2: "female"})
    
    # Add identifiers
    df_model = pd.merge(df_model, df_population[["census_section_index", "census_section_id"]].drop_duplicates(),
                        on="census_section_index", how="left")
    df_model = pd.merge(df_model, df_population[["municipality_index", "municipality_id"]].drop_duplicates(),
                        on="municipality_index", how="left")
    df_model = pd.merge(df_model, df_population[["province_index", "province_id"]].drop_duplicates(),
                        on="province_index", how="left")

    df_model = df_model.rename(columns={"combined_age_class": "age_class"})

    return df_model[["census_section_id", "province_id", "sex", "age_class", "employed", "license", "weight"]]
