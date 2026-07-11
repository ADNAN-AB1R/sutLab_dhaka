import numpy as np

"""
Derive Seville-specific driving license constraints from the HTS (ENTD)
reweighted sample. Returns a dict with key "license_constraints"
containing a list of constraint dicts compatible with the Seville
enriched calibration stage.

Constraints cover:
- Overall share
- By sex (male/female)
- By age ranges (0-5, 6-14, 15-17, 18-23, 24-29, 30-44, 45-64, 65-79, 80+)

Targets are computed as weighted shares using person_weight.
"""


def configure(context):
    # Use reweighted ENTD for Seville
    context.stage("data.hts.entd.reweighted")


def _weighted_share(df, col_bool, wcol):
    if len(df) == 0:
        return 0.0
    num = df.loc[df[col_bool] == True, wcol].sum()
    den = df[wcol].sum()
    return float(num / den) if den > 0 else 0.0


def execute(context):
    hh, persons, trips = context.stage("data.hts.entd.reweighted")

    persons = persons.copy()
    persons["person_weight"] = persons["person_weight"].astype(float)


# TODO: FIX AGE BINS
    age_bins = [
        (-np.inf, 5),
        (6, 14),
        (15, 17),
        (18, 23),
        (24, 29),
        (30, 44),
        (45, 64),
        (65, 79),
        (80, np.inf),
    ]

    constraints = []

    # Overall target
    overall = _weighted_share(persons, "has_license", "person_weight")
    constraints.append({"target": overall})

    # By sex
    for sex in ["male", "female"]:
        df = persons[persons["sex"] == sex]
        target = _weighted_share(df, "has_license", "person_weight")
        constraints.append({"sex": sex, "target": target})

    # By age ranges
    for low, high in age_bins:
        df = persons[persons["age"].between(low, high)]
        target = _weighted_share(df, "has_license", "person_weight")
        constraints.append({"age": (low, high), "target": target})

    # Age*sex for teens (15–17) to tighten this sensitive bin
    low, high = (15, 17)
    df_age = persons[persons["age"].between(low, high)]
    for sex in ["male", "female"]:
        df = df_age[df_age["sex"] == sex]
        target = _weighted_share(df, "has_license", "person_weight")
        constraints.append({"age": (low, high), "sex": sex, "target": target})

    return {"license_constraints": constraints}
