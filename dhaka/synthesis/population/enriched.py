import synthesis.population.enriched as delegate

import pandas as pd
import numpy as np


def configure(context):
    # Base enriched stage (matched + income, etc.)
    delegate.configure(context)
    # seville-specific constraints for PT and license
    context.stage("seville.data.pt.constraints")
    context.stage("seville.data.license.constraints")
    context.config("random_seed")


"""
seville enriched stage
- Starts from the generic enriched population
- Calibrates PT subscription against seville HTS-derived constraints
- Enforces PT subscription = 0 for ages <6
- Calibrates driving license against seville HTS-derived constraints
All calibrations use simple proportional fitting and deterministic micro-adjustments
for sensitive bins to keep results stable across runs.
"""


def _fit_probabilities(n, groups, targets):
    # groups: list of integer index arrays; targets: desired sums over those indices
    prob = np.ones(n, dtype=float)
    for _ in range(500):
        for idx, target in zip(groups, targets):
            current = float(prob[idx].sum())
            factor = 0.0 if (current == 0 or target == 0) else (target / current)
            prob[idx] = prob[idx] * float(factor)
    return np.clip(prob, 0.0, 1.0)


def _build_groups(df, constraints, sex_arr, age_arr):
    groups = []
    targets = []
    n = len(df)
    for c in constraints:
        f = np.ones(n, dtype=bool)
        if "sex" in c:
            f &= (sex_arr == c["sex"])  # category-safe
        if "age" in c:
            low, high = c["age"]
            f &= (age_arr >= low) & (age_arr <= high)
        idx = np.flatnonzero(f)
        groups.append(idx)
        targets.append(float(c["target"]) * int(idx.size))
    return groups, targets


def execute(context):
    # Start from base enriched population (donor copy)
    df_persons = delegate.execute(context).copy()

    # Load seville-specific constraints
    constraints_pt = context.stage("seville.data.pt.constraints")["pt_subscription_constraints"]
    constraints_lic = context.stage("seville.data.license.constraints")["license_constraints"]

    # Prepare arrays
    n = len(df_persons)
    sex_arr = df_persons["sex"].astype(str).to_numpy()
    age_arr = df_persons["age"].to_numpy()

    # --- PT subscription calibration ---
    pt_groups, pt_targets = _build_groups(df_persons, constraints_pt, sex_arr, age_arr)
    pt_prob = _fit_probabilities(n, pt_groups, pt_targets)

    rng_pt = np.random.RandomState(context.config("random_seed") + 92341)
    sel_pt = rng_pt.random_sample(n) < pt_prob
    # Enforce 0% PT subscription for children <6
    sel_pt[age_arr < 6] = False
    df_persons["has_pt_subscription"] = sel_pt

    # Optional deterministic micro-adjustment for 15–17 age×sex
    teen_targets_pt = {c["sex"]: float(c["target"]) for c in constraints_pt if c.get("age") == (15, 17) and "sex" in c}
    for sex in teen_targets_pt:
        mask = (age_arr >= 15) & (age_arr <= 17) & (sex_arr == sex)
        pos = np.flatnonzero(mask)
        if pos.size == 0:
            continue
        desired = int(round(teen_targets_pt[sex] * int(pos.size)))
        current = int(df_persons.loc[df_persons.index[pos], "has_pt_subscription"].sum())
        delta = desired - current
        if delta != 0:
            rngx = np.random.RandomState(context.config("random_seed") + (1123 if sex == "male" else 2246))
            perm = pos.copy()
            rngx.shuffle(perm)
            idx = df_persons.index.to_numpy()
            if delta > 0:
                to_flip = [idx[i] for i in perm if not df_persons.at[idx[i], "has_pt_subscription"]][:delta]
                df_persons.loc[to_flip, "has_pt_subscription"] = True
            else:
                to_flip = [idx[i] for i in perm if df_persons.at[idx[i], "has_pt_subscription"]][:-delta]
                df_persons.loc[to_flip, "has_pt_subscription"] = False

    # --- Driving license calibration ---
    lic_groups, lic_targets = _build_groups(df_persons, constraints_lic, sex_arr, age_arr)
    lic_prob = _fit_probabilities(n, lic_groups, lic_targets)

    rng_lic = np.random.RandomState(context.config("random_seed") + 45421)
    has_license = rng_lic.random_sample(n) < lic_prob

    # Deterministic micro-adjustment for 15–17 by sex
    teen_targets_lic = {c["sex"]: float(c["target"]) for c in constraints_lic if c.get("age") == (15, 17) and "sex" in c}
    for sex in teen_targets_lic:
        mask = (age_arr >= 15) & (age_arr <= 17) & (sex_arr == sex)
        pos = np.flatnonzero(mask)
        if pos.size == 0:
            continue
        desired = int(round(teen_targets_lic[sex] * int(pos.size)))
        current = int(has_license[pos].sum())
        delta = desired - current
        if delta != 0:
            rngx = np.random.RandomState(context.config("random_seed") + (6611 if sex == "male" else 7722))
            perm = pos.copy()
            rngx.shuffle(perm)
            if delta > 0:
                to_flip = [i for i in perm if not has_license[i]][:delta]
                if to_flip:
                    has_license[np.array(to_flip, dtype=int)] = True
            else:
                to_flip = [i for i in perm if has_license[i]][:-delta]
                if to_flip:
                    has_license[np.array(to_flip, dtype=int)] = False

    df_persons["has_license"] = has_license

    return df_persons
