import os
import json

import numpy as np
import pandas as pd
import geopandas as gpd

import dhaka.wards
from dhaka.data.hts.entd.cleaned import MODES_MAP, INCOME_BRACKETS

"""
Builds the per-trip mode-choice estimation dataset from the raw DTCA survey,
including the REPORTED cost and time columns the rest of the pipeline never
reads.

Why this is standalone and not a synpp stage:
`dhaka/data/hts/entd/raw.py` deliberately reads only a subset of the survey's
145 trip columns, and widening it would invalidate the cached raw -> cleaned
-> reweighted -> IPU -> locations chain and force a full multi-hour pipeline
re-run for a file that is only ever consumed as a static artifact. The
existing dhaka/mode_choice/estimate.py is standalone for the same reason.
Shared logic (MODES_MAP, INCOME_BRACKETS, ward parsing/point pools) is
IMPORTED from the pipeline rather than copied, so the two cannot drift apart.

What the survey actually gives us (verified coverage over 380,092 raw trips):
  q55_total_cost         98.5%   reported out-of-pocket cost, BDT
  q55_tot_vehicle_time   98.5%   reported in-vehicle time, minutes
  q55_tot_waiting_time   98.5%   reported waiting time, minutes
  q55_tot_travel_time   100.0%   reported total travel time, minutes
  origin/destination ward 99.1% / 99.4%
  q50_trip_lat_1/long_1  21.3%   <- why real point-to-point distance is out

Distance therefore comes from the same ward-polygon sampling the pipeline
itself uses (dhaka/data/hts/entd/cleaned.py): a random point in the origin
ward and a random point in the destination ward. Only 21.3% of trips record
any coordinate at all, so this is not a shortcut around better data - it is
the best available geometry.

IMPORTANT consequence of that, for whoever fits a model on this file:
ward-sampled distance carries substantial measurement error, and classical
errors-in-variables attenuates the coefficient on anything derived from it
TOWARDS ZERO. The project's earlier fit (fitted_coefficients.json) produced
beta_time = -0.0054/min - an hour of travel costing 0.32 utils, ~20x weaker
than Hoque's -0.112/min - and distance-error attenuation is the leading
suspect. `reported_*` columns here are measured directly and let that
hypothesis be tested rather than assumed: fit once on reported time, once on
distance-derived time, and compare.

USAGE NOTE on the reported columns: they describe only the mode each trip
ACTUALLY chose. Do not feed reported LOS for the chosen alternative and
imputed LOS for the others into the same model - that makes the chosen
alternative's attributes systematically more accurate than its competitors'
and biases the estimates. Use the reported values to FIT per-mode
time/cost-vs-distance functions, then impute every alternative (the chosen
one included) from those functions, so the errors stay symmetric.

Run:
    python -m dhaka.mode_choice.build_estimation_dataset
"""

EXCEL_PATH = os.path.join("raw_data", "Dhaka", "hts", "dtca_full.xlsx")
WARD_SHP_PATH = os.path.join("raw_data", "Dhaka", "spatial", "ward_dhk_75.shp")
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "estimation_dataset.parquet")

# Reading the three sheets out of the 145-column workbook takes ~6 minutes, so
# the raw sheets are cached verbatim before any processing. Without this, every
# bug in the (fast) processing below costs another full Excel parse to retry.
SHEET_CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache_dtca_sheets")

RANDOM_SEED = 1234

EXTRA_UPAZILAS = ["Savar", "Keraniganj"]

TRIP_COLUMNS = [
    "hhid", "memberid", "trip_no",
    "q44_trip_purpose",
    "q45_wardunion", "q45_upazila_cc_name",
    "q56_wardunion", "q56_upazila_cc_name",
    "q55_tot_travel_time", "q55_tot_vehicle_time", "q55_tot_waiting_time",
    "q55_total_cost", "subtripno",
    "q61_trip_mode", "Exp_Fac_231206",
]

MEMBER_COLUMNS = ["hhid", "memberid", "q15_age", "q15_sex", "q15_driving_license"]
HOUSEHOLD_COLUMNS = ["hhid", "q7_hh_income"]


def load_sheet(sheet_name, columns, cache_name):
    """Read one sheet, caching the raw result to parquet first (see
    SHEET_CACHE_DIR). Delete cache_dtca_sheets/ to force a re-read."""
    cache_path = os.path.join(SHEET_CACHE_DIR, "%s.parquet" % cache_name)

    if os.path.exists(cache_path):
        df = pd.read_parquet(cache_path)
        print("  %-18s %7d rows (from cache)" % (sheet_name, len(df)))
        return df

    df = pd.read_excel(EXCEL_PATH, sheet_name = sheet_name, usecols = columns)
    os.makedirs(SHEET_CACHE_DIR, exist_ok = True)
    # Object columns with mixed types break parquet; coerce them to string.
    for column in df.columns:
        if df[column].dtype == object:
            df[column] = df[column].astype(str)
    df.to_parquet(cache_path, index = False)
    print("  %-18s %7d rows (read from Excel, cached)" % (sheet_name, len(df)))
    return df


def load_survey():
    print("Loading DTCA survey sheets from %s" % EXCEL_PATH)
    df_trips = load_sheet("trip info", TRIP_COLUMNS, "trip_info")
    df_members = load_sheet("all hh members", MEMBER_COLUMNS, "members")
    df_households = load_sheet("Household info", HOUSEHOLD_COLUMNS, "households")
    return df_trips, df_members, df_households


def attach_person_attributes(df_trips, df_members, df_households):
    """age / female / has_license / income_class, derived exactly as
    dhaka/data/hts/entd/cleaned.py derives them so the estimated model's
    covariates mean the same thing as the synthetic population's."""
    df_members = df_members.rename(columns = {
        "q15_age": "age", "q15_sex": "sex_raw", "q15_driving_license": "license_raw",
    })

    df_members["female"] = df_members["sex_raw"].astype(str).str.lower().str.startswith("f")
    # Same rule as cleaned.py: anything other than the literal "No License".
    df_members["has_license"] = df_members["license_raw"].astype(str) != "No License"

    bracket_to_class = { bracket: index for index, bracket in enumerate(INCOME_BRACKETS) }
    df_households["income_class"] = df_households["q7_hh_income"].map(bracket_to_class)
    df_households["income_class"] = df_households["income_class"].fillna(-1).astype(int)

    df = df_trips.merge(
        df_members[["hhid", "memberid", "age", "female", "has_license"]],
        on = ["hhid", "memberid"], how = "left")
    df = df.merge(df_households[["hhid", "income_class"]], on = "hhid", how = "left")
    return df


def build_zones():
    """One polygon per commune_id, mirroring dhaka/data/spatial/raw.py.

    Deliberately DUPLICATED rather than imported: that is a synpp stage, and
    synpp hashes a stage's module source, so refactoring it to share this code
    would change its hash and invalidate raw -> iris -> cleaned -> reweighted ->
    IPU -> locations, forcing a multi-hour re-run of the whole pipeline. The
    duplication is 10 lines and must stay in sync with that file - the zone
    scheme is 'S01'..'S75' (DSCC), 'N01'..'N54'/'N98' (DNCC) and 'SAVAR'/
    'KERANIGANJ', see dhaka/wards.py.

    Note the shapefile's ADM3_EN column is null for every city-corporation
    polygon, so commune_id for those comes from the CC field plus the ward
    number, NOT from parse_zone_id_strict on ADM3_EN."""
    gdf = gpd.read_file(WARD_SHP_PATH)[["Ward No.", "CC", "ADM3_EN", "geometry"]]
    print("  ward shapefile: %d polygons, crs=%s" % (len(gdf), gdf.crs))

    # Some polygons have invalid rings, which makes dissolve() throw a GEOS
    # TopologyException - same repair raw.py applies.
    gdf["geometry"] = gdf["geometry"].make_valid()

    gdf_wards = gdf[gdf["CC"].isin(["S", "N"])].dropna(subset = ["Ward No."]).copy()
    gdf_wards["commune_id"] = (
        gdf_wards["CC"] + gdf_wards["Ward No."].astype(int).astype(str).str.zfill(2))
    gdf_wards = gdf_wards.dissolve(by = "commune_id", as_index = False)[["commune_id", "geometry"]]

    gdf_upazilas = gdf[gdf["ADM3_EN"].isin(EXTRA_UPAZILAS)].copy()
    gdf_upazilas["commune_id"] = gdf_upazilas["ADM3_EN"].str.upper()
    gdf_upazilas = gdf_upazilas.dissolve(by = "commune_id", as_index = False)[["commune_id", "geometry"]]

    gdf_zones = pd.concat([gdf_wards, gdf_upazilas], ignore_index = True)
    gdf_zones = gpd.GeoDataFrame(gdf_zones, geometry = "geometry", crs = gdf.crs)
    print("  zones: %d wards + %d upazilas" % (len(gdf_wards), len(gdf_upazilas)))
    return gdf_zones


def attach_ward_distance(df, random_state):
    """Straight-line distance between a random point in the origin ward and a
    random point in the destination ward - the same construction
    cleaned.py uses, for the same reason (only 21.3% coordinate coverage)."""
    gdf_zones = build_zones()

    pools = dhaka.wards.build_ward_point_pools(
        gdf_zones[["commune_id", "geometry"]], random_state = random_state)
    print("  built point pools for %d zones" % len(pools))

    df["origin_ward_id"] = dhaka.wards.parse_zone_id(
        df["q45_upazila_cc_name"], df["q45_wardunion"])
    df["destination_ward_id"] = dhaka.wards.parse_zone_id(
        df["q56_upazila_cc_name"], df["q56_wardunion"])

    def sample(origin_ward, destination_ward):
        if origin_ward not in pools or destination_ward not in pools:
            return np.nan
        origin_point = pools[origin_ward][random_state.randint(len(pools[origin_ward]))]
        destination_point = pools[destination_ward][random_state.randint(len(pools[destination_ward]))]
        return float(np.hypot(*(origin_point - destination_point)))

    df["distance_m"] = [
        sample(o, d) for o, d in zip(df["origin_ward_id"], df["destination_ward_id"])
    ]
    return df


def execute():
    random_state = np.random.RandomState(RANDOM_SEED)

    df_trips, df_members, df_households = load_survey()

    df = attach_person_attributes(df_trips, df_members, df_households)
    df = attach_ward_distance(df, random_state)

    df["mode"] = df["q61_trip_mode"].map(MODES_MAP)

    df = df.rename(columns = {
        "q55_tot_travel_time": "reported_travel_time_min",
        "q55_tot_vehicle_time": "reported_vehicle_time_min",
        "q55_tot_waiting_time": "reported_waiting_time_min",
        "q55_total_cost": "reported_cost_bdt",
        "Exp_Fac_231206": "trip_weight",
        "subtripno": "n_stages",
        "q44_trip_purpose": "purpose_raw",
    })

    for column in ("reported_travel_time_min", "reported_vehicle_time_min",
                   "reported_waiting_time_min", "reported_cost_bdt",
                   "trip_weight", "age", "distance_m"):
        df[column] = pd.to_numeric(df[column], errors = "coerce")

    df["person_id"] = df["hhid"].astype(str) + "_" + df["memberid"].astype(str)

    keep = [
        "person_id", "hhid", "memberid", "trip_no", "purpose_raw",
        "mode", "n_stages", "trip_weight",
        "distance_m", "origin_ward_id", "destination_ward_id",
        "reported_travel_time_min", "reported_vehicle_time_min",
        "reported_waiting_time_min", "reported_cost_bdt",
        "age", "female", "has_license", "income_class",
    ]
    df = df[keep]

    print()
    print("=== coverage before filtering (%d rows) ===" % len(df))
    for column in ("mode", "distance_m", "reported_travel_time_min",
                   "reported_cost_bdt", "income_class", "has_license", "age"):
        print("  %-26s %7d  %5.1f%%" % (
            column, df[column].notna().sum(), 100 * df[column].notna().mean()))

    df.to_parquet(OUTPUT_PATH, index = False)
    print()
    print("Wrote %s (%d rows)" % (OUTPUT_PATH, len(df)))
    return df


if __name__ == "__main__":
    execute()
