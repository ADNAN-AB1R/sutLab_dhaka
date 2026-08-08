import os
import json

import numpy as np
import pandas as pd
from scipy.optimize import minimize

"""
Fits the DTCA-HTS mode-choice model consumed by
dhaka/synthesis/population/mode_choice.py (static synthesis-time
assignment) and matsim_run's discrete_mode_choice trip estimator
(in-simulation replanning). Reads the cached per-trip estimation dataset
(person/trip covariates + a fixed-speed travel-time proxy per alternative)
and fits a weighted conditional/multinomial logit: one travel-time
coefficient shared across all modes (beta_time) plus mode-specific
alternative-specific constants (asc) and mode-specific covariate
coefficients, with `walk` as the reference mode (baseline utility 0, no
asc/coefficients of its own).

statsmodels' MNLogit doesn't support an alternative-varying regressor
(travel time differs per alternative, not just per case/person), so the
log-likelihood is coded directly here and maximized with
scipy.optimize.minimize. This is the same linear-utility functional form
dhaka/synthesis/population/mode_choice.py's apply_model() already
implements at application time - here it's fit instead of applied.

Run standalone (not a synpp stage - fitted_coefficients.json is a static
artifact read directly by its consumers via a plain file path, same as
before):
    python -m dhaka.mode_choice.estimate
"""

DATASET_PATH = os.path.join(os.path.dirname(__file__), "cache_estimation_dataset.parquet")
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "fitted_coefficients.json")

REFERENCE_MODE = "walk"
COVARIATES = ["income_class", "age", "female", "has_license"]

# Fixed-speed assumption the estimation dataset's time_<mode> proxy columns
# were generated with. Confirmed empirically, not guessed: time_walk /
# time_<mode> is a perfectly constant ratio per mode across all 274,946
# rows (std ~1e-16) - 2.5 for bike/rickshaw, 3.75 for paratransit, 4.5 for
# pt, 5.5 for car - consistent with one shared per-trip distance divided by
# a fixed km/h assumption per mode. Anchoring on a standard 4.0 km/h
# pedestrian speed reproduces those exact ratios, so this dict is
# self-consistent with what the model below is actually fit against.
FALLBACK_SPEED_KMH = {
    "walk": 4.0,
    "bike": 10.0,
    "rickshaw": 10.0,
    "paratransit": 15.0,
    "car": 22.0,
    "pt": 18.0,
}

MODES = list(FALLBACK_SPEED_KMH.keys())
NON_REF_MODES = [mode for mode in MODES if mode != REFERENCE_MODE]


def load_dataset():
    df = pd.read_parquet(DATASET_PATH)
    df = df[df["mode"].isin(MODES)].copy()
    df["mode"] = df["mode"].astype(str)
    return df


def build_matrices(df):
    alt_index = { mode: i for i, mode in enumerate(MODES) }

    time_matrix = np.column_stack([df["time_%s" % mode].to_numpy() for mode in MODES])

    cov_matrix = np.column_stack([
        df["income_class"].to_numpy(dtype = float),
        df["age"].to_numpy(dtype = float),
        (df["female"].to_numpy() != 0).astype(float),
        (df["has_license"].to_numpy() != 0).astype(float),
    ])

    chosen_index = df["mode"].map(alt_index).to_numpy()
    weights = df["trip_weight"].to_numpy()

    return time_matrix, cov_matrix, chosen_index, weights, alt_index


def unpack_params(params):
    n_non_ref = len(NON_REF_MODES)
    n_cov = len(COVARIATES)

    beta_time = params[0]
    asc = params[1:1 + n_non_ref]
    coefficients = params[1 + n_non_ref:].reshape(n_non_ref, n_cov)

    return beta_time, asc, coefficients


def compute_utilities(params, time_matrix, cov_matrix, alt_index):
    beta_time, asc, coefficients = unpack_params(params)
    U = beta_time * time_matrix

    for i, mode in enumerate(NON_REF_MODES):
        m = alt_index[mode]
        U[:, m] = U[:, m] + asc[i] + cov_matrix @ coefficients[i]

    return U


def negative_log_likelihood(params, time_matrix, cov_matrix, chosen_index, weights, alt_index):
    U = compute_utilities(params, time_matrix, cov_matrix, alt_index)
    U = U - U.max(axis = 1, keepdims = True)
    log_denom = np.log(np.exp(U).sum(axis = 1))
    log_p_chosen = U[np.arange(len(chosen_index)), chosen_index] - log_denom
    return -np.sum(weights * log_p_chosen) / weights.sum()


def fit(time_matrix, cov_matrix, chosen_index, weights, alt_index):
    n_non_ref = len(NON_REF_MODES)
    n_cov = len(COVARIATES)
    n_params = 1 + n_non_ref + n_non_ref * n_cov

    x0 = np.zeros(n_params)
    x0[0] = -0.1  # beta_time: negative prior - utility should fall with travel time

    result = minimize(
        negative_log_likelihood, x0,
        args = (time_matrix, cov_matrix, chosen_index, weights, alt_index),
        method = "BFGS",
        options = { "maxiter": 2000, "gtol": 1e-7 },
    )

    if not result.success:
        raise RuntimeError("MNL estimation did not converge: %s" % result.message)

    return result.x


def execute():
    df = load_dataset()
    time_matrix, cov_matrix, chosen_index, weights, alt_index = build_matrices(df)
    params = fit(time_matrix, cov_matrix, chosen_index, weights, alt_index)
    beta_time, asc, coefficients = unpack_params(params)

    model = {
        "modes": MODES,
        "reference_mode": REFERENCE_MODE,
        "covariates": COVARIATES,
        "beta_time": float(beta_time),
        "asc": { mode: float(asc[i]) for i, mode in enumerate(NON_REF_MODES) },
        "coefficients": {
            cov: { mode: float(coefficients[i, k]) for i, mode in enumerate(NON_REF_MODES) }
            for k, cov in enumerate(COVARIATES)
        },
        "fallback_speed_kmh": FALLBACK_SPEED_KMH,
    }

    with open(OUTPUT_PATH, "w", encoding = "utf-8") as f:
        json.dump(model, f, indent = 2)

    # Diagnostics: predicted vs. observed weighted mode shares at the
    # estimated parameters - a badly misspecified/non-converged fit will
    # show these diverging noticeably.
    U = compute_utilities(params, time_matrix, cov_matrix, alt_index)
    U = U - U.max(axis = 1, keepdims = True)
    P = np.exp(U) / np.exp(U).sum(axis = 1, keepdims = True)
    predicted_share = pd.Series(
        (P * weights[:, None]).sum(axis = 0) / weights.sum(), index = MODES
    )
    observed_share = pd.Series(
        [weights[chosen_index == alt_index[mode]].sum() / weights.sum() for mode in MODES],
        index = MODES,
    )
    print("Predicted vs. observed weighted mode shares at convergence:")
    print(pd.DataFrame({ "predicted": predicted_share, "observed": observed_share }))
    print("\nFitted beta_time: %.6f" % beta_time)
    print("Fitted ASCs:", model["asc"])

    return model


if __name__ == "__main__":
    execute()
