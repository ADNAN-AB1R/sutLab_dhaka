import os
import numpy as np
import pandas as pd

"""
This stage loads the raw DTCA household travel survey (2023) for the Dhaka metro
area and converts it into the household/person/trip schema used by the HTS
pipeline, restricted to the study area covered by the ward shapefile: DNCC +
DSCC (full ward detail) plus Savar + Keraniganj (single zone each, the only two
surrounding upazilas with real survey coverage and shapefile boundaries).

Unlike Seville's ENTD (where only one household member is interviewed about
mobility, and the rest are only known by age/sex), the DTCA survey already lists
every household member with real age/sex/employment/education/driving-license
data (sheets "all hh members" / "individual hh member"). So there is no need for
Bernoulli-imputation of non-respondent household members - `raw.py` here directly
returns a full person roster, and no `household_members` stage chain is needed.
"""

STUDY_AREA_UPAZILAS = [
    "Dhaka North City Corporation", "Dhaka South City Corporation",
    "Savar", "Keraniganj",
]
HEAD_OF_HOUSEHOLD = "Head of Household (self)"

HOUSEHOLD_MEMBER_COLUMNS = [
    "hhid", "memberid", "upazila", "ward_union",
    "q15_age", "q15_sex", "q15_relationship",
    "q15_driving_license", "q15_vehicles", "Exp_Fac_231206",
]

INDIVIDUAL_MEMBER_COLUMNS = [
    "hhid", "memberid",
    "q17_employement", "q35_school_level",
    "q20_wardunion", "q20_upazila_CC_name", "q33_wardunion", "q33_upazila_CC_name",
]

HOUSEHOLD_COLUMNS = [
    "hhid", "upazila", "ward_union", "q7_hh_income", "q11_own_vehicle_no",
]

TRIP_COLUMNS = [
    "hhid", "memberid", "trip_no", "upazila",
    "q44_trip_purpose",
    "q45_wardunion", "q45_upazila_cc_name", "q56_wardunion", "q56_upazila_cc_name",
    "q50_trip_lat_1", "q50_trip_long_1",
    "q48_starting_time", "q59_reaching_time", "q55_tot_travel_time",
    "q61_trip_mode", "Exp_Fac_231206",
]

def configure(context):
    context.config("data_path")
    context.config("dhaka.hts", "hts/dtca_full.xlsx")
    context.config("dhaka.study_area_upazilas", STUDY_AREA_UPAZILAS)

def execute(context):
    EXCEL_PATH = f"{context.config('data_path')}/{context.config('dhaka.hts')}"
    study_areas = context.config("dhaka.study_area_upazilas")

    df_household_info = pd.read_excel(
        EXCEL_PATH, sheet_name = "Household info", usecols = HOUSEHOLD_COLUMNS
    )
    df_household_info = df_household_info[df_household_info["upazila"].isin(study_areas)].copy()
    valid_households = set(df_household_info["hhid"])

    df_members = pd.read_excel(
        EXCEL_PATH, sheet_name = "all hh members", usecols = HOUSEHOLD_MEMBER_COLUMNS
    )
    df_members = df_members[df_members["hhid"].isin(valid_households)].copy()

    df_individual = pd.read_excel(
        EXCEL_PATH, sheet_name = "individual hh member", usecols = INDIVIDUAL_MEMBER_COLUMNS
    )
    df_individual = df_individual[df_individual["hhid"].isin(valid_households)].copy()

    df_trips = pd.read_excel(
        EXCEL_PATH, sheet_name = "trip info", usecols = TRIP_COLUMNS
    )
    df_trips = df_trips[df_trips["hhid"].isin(valid_households)].copy()

    # ------------------------------------------------------------------
    # Households

    df_households = df_household_info.rename(columns = {
        "hhid": "household_id",
        "q7_hh_income": "income_bracket",
        "q11_own_vehicle_no": "number_of_vehicles",
    })
    df_households["number_of_vehicles"] = df_households["number_of_vehicles"].fillna(0)

    # ------------------------------------------------------------------
    # Persons: start from the full member roster, then bring in employment /
    # education / observed work-and-school-location detail

    df_persons = df_members.rename(columns = {
        "hhid": "household_id", "memberid": "person_id",
        "q15_age": "age", "q15_sex": "sex", "q15_relationship": "relationship",
        "q15_driving_license": "license_raw", "q15_vehicles": "personal_vehicle",
        "Exp_Fac_231206": "person_weight",
    })

    df_individual = df_individual.rename(columns = {
        "hhid": "household_id", "memberid": "person_id",
        "q17_employement": "employment_raw", "q35_school_level": "school_level_raw",
        "q20_wardunion": "work_ward_raw", "q20_upazila_CC_name": "work_upazila_raw",
        "q33_wardunion": "school_ward_raw", "q33_upazila_CC_name": "school_upazila_raw",
    })

    df_persons = pd.merge(
        df_persons, df_individual, on = ["household_id", "person_id"], how = "left"
    )

    # Household weight: taken from the head of household's expansion factor
    df_heads = df_persons[df_persons["relationship"] == HEAD_OF_HOUSEHOLD]
    df_heads = df_heads[["household_id", "person_weight"]].rename(
        columns = { "person_weight": "household_weight" }
    ).drop_duplicates("household_id")

    df_households = pd.merge(df_households, df_heads, on = "household_id", how = "left")
    df_mean_weight = df_persons.groupby("household_id")["person_weight"].mean()
    df_households["household_weight"] = df_households["household_weight"].fillna(
        df_households["household_id"].map(df_mean_weight)
    )

    df_household_size = df_persons.groupby("household_id").size().rename("household_size").reset_index()
    df_households = pd.merge(df_households, df_household_size, on = "household_id", how = "left")

    # Number of bikes: household members owning a bicycle (but not a motorcycle)
    is_bike = df_persons["personal_vehicle"].astype(str).str.contains("cycle", case = False, na = False)
    is_bike &= ~df_persons["personal_vehicle"].astype(str).str.contains("motor", case = False, na = False)
    df_bikes = is_bike.groupby(df_persons["household_id"]).sum().rename("number_of_bikes").reset_index()
    df_households = pd.merge(df_households, df_bikes, on = "household_id", how = "left")
    df_households["number_of_bikes"] = df_households["number_of_bikes"].fillna(0)

    # ------------------------------------------------------------------
    # Trips

    df_trips = df_trips.rename(columns = {
        "hhid": "household_id", "memberid": "person_id", "trip_no": "trip_sequence",
        "q44_trip_purpose": "purpose_text",
        "q45_wardunion": "origin_ward_raw", "q45_upazila_cc_name": "origin_upazila_raw",
        "q56_wardunion": "destination_ward_raw", "q56_upazila_cc_name": "destination_upazila_raw",
        "q50_trip_lat_1": "origin_lat", "q50_trip_long_1": "origin_lon",
        "q48_starting_time": "departure_time_raw", "q59_reaching_time": "arrival_time_raw",
        "q55_tot_travel_time": "trip_duration",
        "q61_trip_mode": "mode_raw", "Exp_Fac_231206": "trip_weight",
    })

    df_trips = df_trips.dropna(subset = ["departure_time_raw", "arrival_time_raw"]).copy()

    return df_persons, df_households, df_trips

def validate(context):
    EXCEL_PATH = f"{context.config('data_path')}/{context.config('dhaka.hts')}"
    if not os.path.exists(EXCEL_PATH):
        raise RuntimeError(f"File missing from DTCA HTS: {EXCEL_PATH}")

    return os.path.getsize(EXCEL_PATH)
