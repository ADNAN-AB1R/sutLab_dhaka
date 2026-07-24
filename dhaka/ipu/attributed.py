import numpy as np
import pandas as pd

import dhaka.wards as wards

"""
This stage adds additional attributes to the generated synthetic population from IPU.

Ward assignment: each household's ward is drawn from the real census
household count per ward (BBS 2022 Community Report Table C-01, extracted in
dhaka/data/census/extract_community_report.py) for all DNCC/DSCC wards - the
same source that anchors the IPU demographic targets (dhaka/ipu/prepare.py),
so the synthetic population's geography and demographics are consistent with
each other. Savar/Keraniganj have no ward-level census rows, so their (single
coarse) zones keep the expansion-factor-weighted HTS household count used for
everything before the census integration. Both sources are absolute
full-population household counts, so mixing them into one draw distribution
is scale-consistent.
"""

def configure(context):
    context.stage("dhaka.ipu.population")
    context.stage("dhaka.data.spatial.iris")
    context.stage("dhaka.data.hts.entd.filtered")
    context.config("random_seed")
    context.config("data_path")
    context.config("dhaka.census_wards_c01", "census/extracted/census_c01_wards.csv")

def load_census_ward_weights(context):
    """Census household count per commune_id for all DNCC/DSCC wards."""
    df_c01 = pd.read_csv(
        f"{context.config('data_path')}/{context.config('dhaka.census_wards_c01')}"
    )

    prefixes = { "Dhaka South City Corporation": "S", "Dhaka North City Corporation": "N" }
    assert wards.CITY_CORP_PREFIXES == prefixes  # keep in sync with the shared zoning scheme

    commune_id = (
        df_c01["city_corp"].map({ "DSCC": "S", "DNCC": "N" })
        + df_c01["ward_num"].astype(int).astype(str).str.zfill(2)
    )

    return pd.Series(df_c01["hh_total"].values, index = commune_id)

def execute(context):
    df = context.stage("dhaka.ipu.population").copy()
    random = np.random.RandomState(context.config("random_seed"))

    df_iris = context.stage("dhaka.data.spatial.iris")[
        ["departement_id", "commune_id", "iris_id"]
    ].drop_duplicates()

    print(f"Adding attributes to {len(df):,} persons from IPU...")

    if "departement_id" not in df.columns and "commune_id" in df.columns:
        df["departement_id"] = df["commune_id"].str[:2]

    # Spatial identifiers: departement -> commune -> iris
    # Distribute households across wards within each departement: census
    # household count per ward for DNCC/DSCC, HTS-weighted count for the two
    # peri-urban zones without ward-level census (see module docstring)
    if "commune_id" not in df.columns:
        df_hts_households, _, _ = context.stage("dhaka.data.hts.entd.filtered")
        hts_ward_weights = df_hts_households.groupby("commune_id")["household_weight"].sum()

        ward_weights = load_census_ward_weights(context)
        for zone in wards.UPAZILA_ZONES.values():
            ward_weights[zone] = hts_ward_weights.get(zone, 0.0)

        household_communes = {}
        for dept_id in df["departement_id"].unique():
            communes_in_dept = df_iris[df_iris["departement_id"] == dept_id]["commune_id"].unique()

            weights = np.array([ward_weights.get(c, 0.0) for c in communes_in_dept])
            if weights.sum() <= 0:
                weights = np.ones(len(communes_in_dept))
            weights = weights / weights.sum()

            dept_households = df[df["departement_id"] == dept_id]["household_id"].unique()

            assigned_communes = random.choice(
                communes_in_dept, size = len(dept_households), p = weights
            )

            for hh_id, commune_id in zip(dept_households, assigned_communes):
                household_communes[hh_id] = commune_id

        df["commune_id"] = df["household_id"].map(household_communes)

    # Map commune to iris
    commune_to_iris = dict(zip(df_iris["commune_id"], df_iris["iris_id"]))
    if "iris_id" not in df.columns or df["iris_id"].isna().any():
        df["iris_id"] = df["commune_id"].map(commune_to_iris)

    df["commune_id"] = df["commune_id"].astype(str)
    df["iris_id"] = df["iris_id"].astype("category")

    # Household attributes
    if "household_size_capped" in df.columns and "household_size" not in df.columns:
        df["household_size"] = df["household_size_capped"]

    if "consumption_units" not in df.columns:
        df["consumption_units"] = 1.0
    if "couple" not in df.columns:
        df["couple"] = False

    # Person attributes
    if "studies" not in df.columns:
        df["studies"] = False
    if "socioprofessional_class" not in df.columns:
        df["socioprofessional_class"] = 0
    if "work_outside_region" not in df.columns:
        df["work_outside_region"] = False
    if "education_outside_region" not in df.columns:
        df["education_outside_region"] = False

    # Vehicle availability
    if "number_of_cars" not in df.columns:
        df["number_of_cars"] = 1
    if "number_of_bikes" not in df.columns:
        df["number_of_bikes"] = 1

    if "commute_mode" not in df.columns:
        df["commute_mode"] = np.nan

    # Assign unique person and household IDs
    if "household_id" in df.columns:
        unique_hh_ids = df["household_id"].unique()
        hh_id_mapping = dict(zip(unique_hh_ids, range(len(unique_hh_ids))))
        df["household_id"] = df["household_id"].map(hh_id_mapping)

    df["person_id"] = np.arange(len(df))

    # Census IDs for compatibility with downstream stages
    df["census_person_id"] = df["person_id"]
    df["census_household_id"] = df["household_id"]

    print(
        f"Attributed population: {len(df):,} persons, "
        f"{df['household_id'].nunique():,} households, "
        f"{df['departement_id'].nunique()} departements"
    )

    return df
