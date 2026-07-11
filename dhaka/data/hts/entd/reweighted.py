"""
Overrides person_weight with the trip-level expansion factor and adds a routed
distance estimate. Unlike Seville's ENTD, the DTCA survey records every household
member's trips directly (trip_weight already equals person_weight from raw.py), so
this is mostly a structural pass-through kept for consistency with the pipeline's
stage-naming convention (this is the alias target for `data.hts.entd.reweighted`).
"""

def configure(context):
    context.stage("dhaka.data.hts.entd.filtered")

def execute(context):
    df_households, df_persons, df_trips = context.stage("dhaka.data.hts.entd.filtered")

    df_persons = df_persons[df_persons["number_of_trips"] >= 0].copy()
    df_persons["person_weight"] = df_persons["trip_weight"]

    df_trips = df_trips.copy()
    df_trips["routed_distance"] = df_trips["euclidean_distance"] * 1.3

    invalid_trips = ~df_trips["person_id"].isin(df_persons["person_id"])
    assert invalid_trips.sum() == 0, invalid_trips.sum()

    return df_households, df_persons, df_trips
