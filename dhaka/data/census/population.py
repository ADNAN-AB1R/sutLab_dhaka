import os
import pandas as pd

"""
Cross-check stage: surfaces district-level BBS 2022 census totals (population,
sex split, household-size distribution) for the Dhaka district row of the
"Merged_All_Table" sheet.

Unlike Seville, this is NOT a hard input to IPU - the BBS census here is only
available at whole-district granularity (which also covers DNCC/Savar/Keraniganj/
etc, not just our DSCC study area), so it can't be used directly as a control
total for a DSCC-only synthetic population without misrepresenting scale. IPU
control totals instead come directly from the weighted DTCA HTS sample (see
dhaka/ipu/prepare.py). This stage exists to sanity-check that HTS-implied
totals for DSCC are in a plausible range relative to the wider district.
"""

DISTRICT = "Dhaka"

HOUSEHOLD_SIZE_COLUMNS = {
    "Number of Person & Avg HH Size_Total_HH #": "total_households",
    "Number of Person & Avg HH Size_# of HH with 1-Person": 1,
    "Number of Person & Avg HH Size_# of HH with 2-Person": 2,
    "Number of Person & Avg HH Size_# of HH with 3-Person": 3,
    "Number of Person & Avg HH Size_# of HH with 4-Person": 4,
    "Number of Person & Avg HH Size_# of HH with 5-Person": "5+",
    "Number of Person & Avg HH Size_# of HH with 6-Person": "5+",
    "Number of Person & Avg HH Size_# of HH with 7-Person": "5+",
    "Number of Person & Avg HH Size_# of HH with 8-Person": "5+",
    "Number of Person & Avg HH Size_# of HH with 9-Person ": "5+",
    "Number of Person & Avg HH Size_# of HH with 10-Person ": "5+",
    "Number of Person & Avg HH Size_# of HH with 10-Person +": "5+",
}

def configure(context):
    context.config("data_path")
    context.config("dhaka.census", "census/bangladesh_bbs_population-and-housing-census-dataset_2022_admin-02.xlsx")

def execute(context):
    EXCEL_PATH = f"{context.config('data_path')}/{context.config('dhaka.census')}"

    df = pd.read_excel(EXCEL_PATH, sheet_name=  "Merged_All_Table") # Total dataframe contains merged_all_table sheet from census excel file
    df_district = df[df["District"] == DISTRICT] # Pull dhaka in df_district

    if len(df_district) != 1:
        raise RuntimeError(f"Expected exactly one '{DISTRICT}' row in the BBS census, found {len(df_district)}") # Need to be only Dhaka

    row = df_district.iloc[0]

    summary = {
        "district": DISTRICT,
        "population_total": row["Population_Total"], # takes in pop total
        "population_male": row["Population by Sex, Dist & Loca_Population_Male_#"],
        "population_female": row["Population by Sex, Dist & Loca_Population_Female_#"],
        "household_total": row["Household_Total"],
        "employed_total": row["Overall_Employed_Working_Status_5 Year+_#"],
        "employed_male": row["Overall_Employed_Working_Status_5 Year+_Male_#"],
        "employed_female": row["Overall_Employed_Working_Status_5 Year+_Female_#"],
    }

    household_size_shares = {} 
    for column, bucket in HOUSEHOLD_SIZE_COLUMNS.items():
        if bucket == "total_households":
            continue
        household_size_shares[bucket] = household_size_shares.get(bucket, 0) + row[column]

    return { "summary": summary, "household_size": household_size_shares }

def validate(context):
    EXCEL_PATH = f"{context.config('data_path')}/{context.config('dhaka.census')}"
    if not os.path.exists(EXCEL_PATH):
        raise RuntimeError(f"BBS census file is not available at location {EXCEL_PATH}")

    return os.path.getsize(EXCEL_PATH)
