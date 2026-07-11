import numpy as np

"""
This stage derives Seville-specific PT subscription constraints from the HTS (ENTD)
reweighted sample. It returns a dict with key "pt_subscription_constraints"
containing a list of constraint dicts compatible with the Seville enriched stage.

Constraints cover:
- Overall share
- By sex (male/female)
- By age ranges (including 0-5 to avoid unconstrained children)

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

    # Ensure columns exist and types are friendly
    persons = persons.copy()
    persons["person_weight"] = persons["person_weight"].astype(float)

    # Age bins as inclusive ranges compatible with between()
    age_bins = [
        (-np.inf, 5),
        (6, 14),
        # (-np.inf, 14),
        (15, 17),
        (18, 23),
        (24, 29),
        (30, 44),
        (45, 64),
        (65, 79),
        (80, np.inf),
    ]

    constraints = []

    # Overall target across all ages
    overall_target = _weighted_share(persons, "has_pt_subscription", "person_weight")
    constraints.append({"target": overall_target})

    # By sex
    for sex in ["male", "female"]:
        df = persons[persons["sex"] == sex]
        target = _weighted_share(df, "has_pt_subscription", "person_weight")
        constraints.append({"sex": sex, "target": target})

    # By age ranges
    for low, high in age_bins:
        df = persons[persons["age"].between(low, high)]
        target = _weighted_share(df, "has_pt_subscription", "person_weight")
        constraints.append({"age": (low, high), "target": target})

    # Focused age×sex interaction for 15–17 to tighten that bin
    low, high = (15, 17)
    df_age = persons[persons["age"].between(low, high)]
    for sex in ["male", "female"]:
        df = df_age[df_age["sex"] == sex]
        target = _weighted_share(df, "has_pt_subscription", "person_weight")
        constraints.append({"age": (low, high), "sex": sex, "target": target})

    return {"pt_subscription_constraints": constraints}
