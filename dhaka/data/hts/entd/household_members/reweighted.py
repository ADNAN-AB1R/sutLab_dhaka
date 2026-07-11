import numpy as np
import pandas as pd
import numpy as np
import pandas as pd

"""
This stages calibrates HTS weights to help match attribute distribution of IPU population with census distribution.
"""

def configure(context):
    context.stage("seville.data.hts.entd.household_members.add_trips")
    context.stage("seville.data.census.population")
    context.stage("seville.data.census.employment")
    context.stage("seville.data.census.licenses")
    context.config("random_seed")
    context.config("weighted_trips")
    


def execute(context):
    df_households, df_persons, df_trips = context.stage("seville.data.hts.entd.household_members.add_trips")

    df_persons["person_weight"] = 1.0
    weighted_trips = context.config("weighted_trips")
    if not weighted_trips: 
        df_persons["trip_weight"] = 1.0
        df_trips["person_weight"] = 1.0
        df_trips["trip_weight"] = 1.0

    # NOTE: in seville.data.hts.entd.reweighted is following:
    # df_persons["person_weight"] = df_persons["trip_weight"]

    calibrate = True
    if calibrate:
        # CALIBRATE HOUSEHOLD WEIGHTS
        calibration_weights = [
            # (age_class, calibration_factor),
            (0, 4),
            (5, 2),
            (10, 1.5),
            (45, 0.65),
            (50, 0.85),
            (60, 0.85),
        ]
        for age_class, factor in calibration_weights:
            age_min = age_class
            age_max = age_class + 4
            filter = (df_persons['age'] <= age_max) & (df_persons['age'] >= age_min)
            young_households_ids = df_persons.loc[filter, 'household_id']
            filter = df_households['household_id'].isin(young_households_ids)
            df_households.loc[filter, "household_weight"] *= factor


    return df_households, df_persons, df_trips
