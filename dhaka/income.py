import numpy as np
import pandas as pd

"""
Household income for the synthetic population.

Carries each synthetic household's ORDINAL income class through to MATSim's
`householdIncome` person attribute: 0-8 following INCOME_BRACKETS in
dhaka/data/hts/entd/cleaned.py ("Less than Tk 10,000" .. "More than Tk
100,000"), -1 where the survey household did not state an income. The class is
inherited from each synthetic household's IPU seed household (see
dhaka/ipu/population.py, where it rides through raking as a passenger column).

Deliberately the ORDINAL class, not a Taka amount: the survey only records a
bracket, and any conversion to money is an assumption about where within the
bracket households sit. That conversion is made once, visibly, in
dhaka/mode_choice/dhaka_mode_parameters.json ("income_scaling" ->
"class_midpoints_bdt_per_month"), where the mode-choice model uses it - so it
can be revised without regenerating the population.

History: this stage previously hardcoded household_income = 0.0 for everyone
(a leftover from the Seville template, where income was not needed). A fix
recorded in project notes as made on 2026-07-31 never actually reached the
repository; this is that fix.
"""

def configure(context):
    context.stage("synthesis.population.sampled")

def execute(context):
    df = context.stage("synthesis.population.sampled")

    if "income_class" not in df.columns:
        raise RuntimeError(
            "synthesis.population.sampled has no income_class column - "
            "dhaka/ipu/population.py must merge it from the HTS households "
            "into the IPU seed.")

    # Every member of a synthetic household comes from the same seed
    # household, so income_class must be constant within household_id.
    classes_per_household = df.groupby("household_id")["income_class"].nunique(dropna = False)
    if (classes_per_household > 1).any():
        raise RuntimeError(
            "income_class varies within %d household(s)" % (classes_per_household > 1).sum())

    df = df[["household_id", "income_class"]].drop_duplicates("household_id")

    df["household_income"] = df["income_class"].fillna(-1).astype(float)

    return df[["household_id", "household_income"]]
