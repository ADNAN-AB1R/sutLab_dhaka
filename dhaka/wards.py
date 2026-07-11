import re
import numpy as np
import pandas as pd

"""
Shared helpers to resolve a `commune_id` zone identifier for the Dhaka study
area, which spans two independent ward-numbering schemes (DNCC, DSCC) plus two
surrounding upazilas with no ward-level subdivision in our data (Savar,
Keraniganj):

  - Within DNCC/DSCC: "N"/"S" + 2-digit ward number, e.g. "S07", "N54"
    (ward numbers are NOT unique across the two city corporations, so the
    prefix is required to avoid collisions - e.g. both have a ward "01")
  - Within Savar/Keraniganj: a single zone per upazila ("SAVAR"/"KERANIGANJ"),
    since neither our HTS nor the ward shapefile has ward/union-level
    boundaries there
  - Anything else (other upazilas/districts, e.g. Gazipur, Narayanganj) is
    outside the study area -> NaN

This scheme is used both for the ward shapefile (dhaka/data/spatial/raw.py,
keyed off its "CC"/"Ward No."/upazila fields) and the HTS (keyed off its
"upazila" + "ward_union" text fields, e.g. "DSCC Ward 01").
"""

WARD_NUMBER_PATTERN = re.compile(r"Ward\s*(?:No[.\-]?)?\s*(\d+)", re.IGNORECASE)

# Cantonment/reserved wards are given as a bare place name rather than
# "Ward NN" in the HTS, but do have a real ward number (98) in the shapefile
# for both city corporations
RESTRICTED_AREA_WARD_NUMBER = "98"

CITY_CORP_PREFIXES = {
    "Dhaka South City Corporation": "S",
    "Dhaka North City Corporation": "N",
}

UPAZILA_ZONES = {
    "Savar": "SAVAR",
    "Keraniganj": "KERANIGANJ",
}

def parse_zone_id(upazila_series, ward_union_series):
    """Resolve a commune_id per row from a (upazila, ward_union) pair, e.g.
    ("Dhaka South City Corporation", "DSCC Ward 01") -> "S01",
    ("Savar", "Konda") -> "SAVAR", ("Gazipur City Corporation", ...) -> NaN."""
    zone_id = pd.Series(np.nan, index = upazila_series.index, dtype = object)

    for upazila_name, prefix in CITY_CORP_PREFIXES.items():
        mask = upazila_series == upazila_name
        ward_numbers = ward_union_series.loc[mask].str.extract(WARD_NUMBER_PATTERN)[0]

        is_restricted_area = ward_union_series.loc[mask].str.strip().str.lower() == "restricted area"
        ward_numbers = ward_numbers.where(~is_restricted_area, RESTRICTED_AREA_WARD_NUMBER)

        resolved = pd.Series(np.nan, index = ward_numbers.index, dtype = object)
        valid = ward_numbers.notna()
        resolved.loc[valid] = prefix + ward_numbers.loc[valid].str.zfill(2)

        zone_id.loc[mask] = resolved

    for upazila_name, zone_name in UPAZILA_ZONES.items():
        mask = upazila_series == upazila_name
        zone_id.loc[mask] = zone_name

    return zone_id

def parse_zone_id_strict(upazila_series, ward_union_series):
    """Same as parse_zone_id, but raises if any row can't be resolved - for
    fields that are expected to always be within the study area by
    construction (e.g. a household's own home ward, after already filtering
    to study-area upazilas)."""
    zone_id = parse_zone_id(upazila_series, ward_union_series)

    if zone_id.isna().any():
        bad = pd.DataFrame({ "upazila": upazila_series, "ward_union": ward_union_series })[zone_id.isna()]
        raise RuntimeError(f"Could not resolve zone id for rows: {bad.drop_duplicates().values.tolist()}")

    return zone_id
