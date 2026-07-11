import re
import numpy as np
import pandas as pd
import data.hts.hts as hts
import dhaka.wards

"""
This stage cleans the DTCA household travel survey.
"""

def configure(context):
    context.stage("data.hts.entd.raw")

DEPARTMENT_VALUE = "1"

# "Home to Work Place", "Non Home-based Business", ... -> (preceding, following)
PURPOSE_TEXT_MAP = {
    "Home": "home",
    "Work Place": "work",
    "Education Place": "education",
    "Others": "other",
}

def parse_purpose(text):
    if not isinstance(text, str):
        return "other", "other"

    parts = text.split(" to ")
    if len(parts) == 2 and parts[0] in PURPOSE_TEXT_MAP and parts[1] in PURPOSE_TEXT_MAP:
        return PURPOSE_TEXT_MAP[parts[0]], PURPOSE_TEXT_MAP[parts[1]]

    # "Non Home-based Business" / "Non Home-based Others": neither end is home
    return "other", "other"

# Collapsed from the 27 raw q61_trip_mode / Rep_ModName values observed in the
# survey. Rickshaw and paratransit (shared/reserved 3-wheeler CNG/auto) are kept
# as their own categories since they carry a large, structurally distinct share
# of Dhaka trips - fold them into "pt"/"car" instead if the mode choice model
# downstream needs to stick to the standard eqasim mode set.
MODES_MAP = {
    "Walking": "walk",
    "Bicycle": "bike",
    "Rickshaw": "rickshaw",
    "Private car /Microbus/ Jeep etc. (drive by driver)": "car",
    "Private car /Microbus/ Jeep etc. (self-driven)": "car",
    "Private car /Microbus/ Jeep etc. (drive by friends and family)": "car",
    "Motorcycle (self-driven)": "car",
    "Motorcycle (drive by friends and family)": "car",
    "Motorcycle (ride sharing, i.e., UBER, Pathao, etc.)": "car",
    "Taxi/ Private car /Microbus/ Jeep etc. (ride sharing, i.e., UBER, Pathao, etc.)": "car",
    "Staff vehicles car/ Microbus": "car",
    "Assaigned vehicles by the company": "car",
    "Truck/ Pickup": "car",
    "Bus": "pt",
    "Mini Bus": "pt",
    "Staff Bus": "pt",
    "School/ Collage/ University Bus": "pt",
    "School Van": "pt",
    "Laguna/ Tempu": "pt",
    "Metro Rail": "pt",
    "Train": "pt",
    "Boat/ Engine Boat": "pt",
    "Water Taxi": "pt",
    "3-wheeler Auto (shared)": "paratransit",
    "3-wheeler Auto (reserved)": "paratransit",
    "3-wheeler CNG (shared)": "paratransit",
    "3-wheeler CNG (reserved)": "paratransit",
    "Others": "other",
}

# Ordered ascending by lower bound - index doubles as an ordinal income_class,
# consistent with Seville's -1..7 convention (-1 = unknown/not stated)
INCOME_BRACKETS = [
    "Less than Tk 10,000",
    "Tk 10,000 to Tk 20,000",
    "Tk 20,000 to Tk 30,000",
    "Tk 30,000 to Tk 40,000",
    "Tk 40,000 to Tk 50,000",
    "Tk 50,000 to Tk 60,000",
    "Tk 60,000 to Tk 80,000",
    "Tk 80,000 to Tk 100,000",
    "More than Tk 100,000",
]

# Assumed operating speed per mode (km/h), used ONLY as a fallback to estimate
# trip distance from trip_duration for trips where we have no coordinates at
# all for the destination (see note in calculate_trip_distance below).
FALLBACK_SPEED_KMH = {
    "walk": 4.0, "bike": 10.0, "rickshaw": 10.0,
    "paratransit": 15.0, "pt": 18.0, "car": 22.0, "other": 15.0,
}

TIME_PATTERN = re.compile(r"(\d{1,2}):(\d{2}):?(\d{2})?\s*(am|pm)?", re.IGNORECASE)

def convert_time(x):
    if pd.isna(x):
        return np.nan
    if hasattr(x, "hour"):
        return float(x.hour * 3600 + x.minute * 60 + getattr(x, "second", 0))
    if isinstance(x, str):
        # Most values are clean "HH:MM:SS" (24h); a handful come through as
        # malformed strings like "0/1/1900  8:00:51 am" (Excel date/time
        # mixup) - the regex below extracts the time-of-day from either.
        match = TIME_PATTERN.search(x)
        if match is None:
            return np.nan

        h, m, s, ampm = match.groups()
        h, m, s = int(h), int(m), int(s) if s else 0

        if ampm is not None:
            ampm = ampm.lower()
            if ampm == "pm" and h != 12:
                h += 12
            if ampm == "am" and h == 12:
                h = 0

        return float(h * 3600 + m * 60 + s)
    return np.nan

def execute(context):
    df_persons, df_households, df_trips = context.stage("data.hts.entd.raw")

    df_persons = pd.DataFrame(df_persons, copy = True)
    df_households = pd.DataFrame(df_households, copy = True)
    df_trips = pd.DataFrame(df_trips, copy = True)

    # ------------------------------------------------------------------
    # Identifiers and weights

    df_households["household_id"] = df_households["household_id"].astype(np.int64)
    df_persons["household_id"] = df_persons["household_id"].astype(np.int64)
    df_trips["household_id"] = df_trips["household_id"].astype(np.int64)

    df_persons["person_id"] = df_persons["person_id"].astype(np.int64)
    df_trips["person_id"] = df_trips["person_id"].astype(np.int64)

    df_households["household_weight"] = df_households["household_weight"].astype(float)
    df_persons["person_weight"] = df_persons["person_weight"].astype(float)
    df_trips["trip_weight"] = df_trips["trip_weight"].astype(float)

    df_households["household_size"] = df_households["household_size"].astype(int)
    df_households["number_of_vehicles"] = df_households["number_of_vehicles"].astype(float)
    df_households["number_of_bikes"] = df_households["number_of_bikes"].astype(float)

    # Department: single study-area zone
    df_households["departement_id"] = DEPARTMENT_VALUE
    df_persons["departement_id"] = DEPARTMENT_VALUE
    df_trips["origin_departement_id"] = DEPARTMENT_VALUE
    df_trips["destination_departement_id"] = DEPARTMENT_VALUE
    df_households["departement_id"] = df_households["departement_id"].astype("category")
    df_persons["departement_id"] = df_persons["departement_id"].astype("category")
    df_trips["origin_departement_id"] = df_trips["origin_departement_id"].astype("category")
    df_trips["destination_departement_id"] = df_trips["destination_departement_id"].astype("category")

    df_households["urban_type"] = "central_city"
    df_households["urban_type"] = df_households["urban_type"].astype("category")

    # Home ward, used downstream to draw the IPU-synthesized households' zones
    # from the real weighted ward distribution (see dhaka/ipu/attributed.py).
    # Strict: every household here was already filtered to a study-area upazila.
    df_households["commune_id"] = dhaka.wards.parse_zone_id_strict(
        df_households["upazila"], df_households["ward_union"]
    )

    # ------------------------------------------------------------------
    # Persons

    df_persons["sex"] = df_persons["sex"].astype(str).str.lower()
    df_persons["sex"] = df_persons["sex"].astype("category")

    df_persons["age"] = df_persons["age"].astype(int)

    df_persons["employed"] = df_persons["employment_raw"].notna()
    df_persons["studies"] = df_persons["school_level_raw"].notna()

    df_persons["has_license"] = df_persons["license_raw"].astype(str) != "No License"

    df_persons["has_pt_subscription"] = np.nan
    df_persons["socioprofessional_class"] = 8  # unknown, matches Seville's "no data" fallback

    df_persons["work_ward_id"] = dhaka.wards.parse_zone_id(df_persons["work_upazila_raw"], df_persons["work_ward_raw"])
    df_persons["school_ward_id"] = dhaka.wards.parse_zone_id(df_persons["school_upazila_raw"], df_persons["school_ward_raw"])

    # ------------------------------------------------------------------
    # Household income

    df_households["income_class"] = df_households["income_bracket"].apply(
        lambda x: INCOME_BRACKETS.index(x) if x in INCOME_BRACKETS else -1
    )

    # ------------------------------------------------------------------
    # Trip purpose & mode

    purposes = df_trips["purpose_text"].apply(parse_purpose)
    df_trips["preceding_purpose"] = purposes.apply(lambda x: x[0]).astype("category")
    df_trips["following_purpose"] = purposes.apply(lambda x: x[1]).astype("category")

    df_trips["mode"] = df_trips["mode_raw"].map(MODES_MAP).fillna("other").astype("category")

    # Trip endpoints legitimately fall outside the study area (Gazipur,
    # Narayanganj, ...), and a handful are given as free-text place names
    # instead of a ward - both become NaN rather than raising
    df_trips["origin_ward_id"] = dhaka.wards.parse_zone_id(df_trips["origin_upazila_raw"], df_trips["origin_ward_raw"])
    df_trips["destination_ward_id"] = dhaka.wards.parse_zone_id(df_trips["destination_upazila_raw"], df_trips["destination_ward_raw"])

    # ------------------------------------------------------------------
    # Trip distance
    #
    # NOTE: the survey only records GPS coordinates for the trip origin (and,
    # rarely, intermediate waypoints) - never for the final destination - and
    # even the origin is only ~30% filled. We therefore cannot compute a real
    # point-to-point euclidean distance here. As a placeholder (until the
    # corrected ward shapefile is in place and we can fall back to ward
    # centroid/building-level distances), we approximate distance from
    # trip_duration and an assumed operating speed per mode. This is a rough
    # stand-in, not a geocoded distance - flagged for revisiting.

    df_trips["trip_duration"] = df_trips["trip_duration"].astype(float) * 60.0  # minutes -> seconds
    speed_kmh = df_trips["mode"].astype(str).map(FALLBACK_SPEED_KMH).fillna(15.0)
    df_trips["euclidean_distance"] = (df_trips["trip_duration"] / 3600.0) * speed_kmh * 1000.0
    df_trips["euclidean_distance"] = df_trips["euclidean_distance"].clip(lower = 50.0)  # avoid zero-length trips

    # ------------------------------------------------------------------
    # Trip times

    df_trips = hts.compute_first_last(df_trips.sort_values(by = ["person_id", "trip_sequence"]).rename(
        columns = { "trip_sequence": "trip_id" }
    ))

    df_trips["departure_time"] = df_trips["departure_time_raw"].apply(convert_time).astype(float)
    df_trips["arrival_time"] = df_trips["arrival_time_raw"].apply(convert_time).astype(float)
    df_trips = df_trips.dropna(subset = ["departure_time", "arrival_time"]).copy()
    df_trips = hts.fix_trip_times(df_trips)

    hts.compute_activity_duration(df_trips)

    # ------------------------------------------------------------------
    # Number of trips & passenger flag

    df_number_of_trips = df_trips.groupby("person_id").size().rename("number_of_trips").reset_index()
    df_persons = pd.merge(df_persons, df_number_of_trips, on = "person_id", how = "left")
    df_persons["number_of_trips"] = df_persons["number_of_trips"].fillna(0).astype(int)

    df_persons["trip_weight"] = df_persons["person_weight"]

    df_persons["is_passenger"] = df_persons["person_id"].isin(
        df_trips[df_trips["mode"].isin(["car_passenger", "paratransit"])]["person_id"].unique()
    )

    # ------------------------------------------------------------------
    # Consumption units (household composition), directly from the full roster

    df_households = pd.merge(
        df_households, hts.calculate_consumption_units(df_persons), on = "household_id"
    )

    # ------------------------------------------------------------------
    # Consistency fixes/checks, same as Seville

    hts.fix_activity_types(df_trips)

    next_departure_time = df_trips["departure_time"].shift(-1)
    f = (~df_trips["is_last_trip"]) & (df_trips["arrival_time"] > next_departure_time)
    problematic_ids = df_trips.loc[f, "person_id"].unique()
    print(f"Deleting {len(problematic_ids)} persons with arrival_time > next departure_time")
    df_trips = df_trips[~df_trips["person_id"].isin(problematic_ids)].copy()
    df_persons = df_persons[~df_persons["person_id"].isin(problematic_ids)].copy()

    unknown_ids = set(df_trips[
        (df_trips["mode"] == "unknown") | (df_trips["preceding_purpose"] == "unknown")
        | (df_trips["following_purpose"] == "unknown")
    ]["person_id"])
    print("  Removed %d persons with trips with unknown mode or unknown purpose" % len(unknown_ids))
    df_trips = df_trips[~df_trips["person_id"].isin(unknown_ids)]
    df_persons = df_persons[~df_persons["person_id"].isin(unknown_ids)].copy()

    print(len(set(df_persons["person_id"].values) - set(df_trips["person_id"].values)), "raw number of persons without trips")

    return df_households, df_persons, df_trips
