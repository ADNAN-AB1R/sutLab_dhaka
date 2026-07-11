import data.hts.hts as hts

"""
Finishing stage: selects the final HTS column contract and runs consistency
checks. Named "filtered" (not "finishing") to match the stage-naming convention
used by the generic pipeline / Seville, even though no filtering happens here -
see reweighted.py for the actual person filtering step.
"""

def configure(context):
    context.stage("dhaka.data.hts.entd.cleaned")

def execute(context):
    df_households, df_persons, df_trips = context.stage("dhaka.data.hts.entd.cleaned")

    df_households = df_households[hts.HOUSEHOLD_COLUMNS + ["urban_type", "income_class", "commune_id"]]
    df_persons = df_persons[hts.PERSON_COLUMNS]
    df_trips = df_trips[hts.TRIP_COLUMNS + ["euclidean_distance"]]

    hts.check(df_households, df_persons, df_trips)
    return df_households, df_persons, df_trips
