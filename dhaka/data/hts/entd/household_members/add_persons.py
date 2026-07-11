import numpy as np
import pandas as pd

"""
This stage adds all the remaining household members that are not present in the df_persons. All the households members
are necessary for the IPU, we add them all.
"""

def configure(context):
    context.stage("seville.data.hts.entd.trip_distance")
    context.stage("seville.locations.education")
    context.config("random_seed")

def add_young_persons(random, df_persons, df_trips, df_household_members):
    # GENERATE PEOPLE
    df_young_people = df_household_members.copy()

    df_young_people = pd.merge(df_young_people, df_persons[['person_id', 'household_id', 'person_weight', 'trip_weight']], on='household_id')
    df_young_people = (
        df_young_people
        .merge(
            df_persons[['household_id', 'age', 'sex']],
            on=['household_id', 'age', 'sex'],
            how='left',
            indicator=True
        )
        .query('_merge == "left_only"')
        .drop(columns='_merge')
    )
    
    # generate new person_id and sex for young persons
    df_young_people = df_young_people.reset_index(drop=True) 
    df_young_people['person_id'] = df_young_people.index
    # person_id negative number to mark it as mock data, avoid duplicate zero
    df_young_people['person_id'] = -df_young_people['person_id'] - 1
        
    df_young_people['person_weight'] = 1

    print(df_young_people.head())

    # Placeholder values, set in the following stage
    df_young_people['employed'] = np.nan
    df_young_people['studies'] = np.nan
    df_young_people['has_license'] = np.nan

    df_young_people['has_pt_subscription'] = False
    # negative value set so the household members without valid trip information are 
    # filtered out in the seville.hts.entd.reweighted stage
    df_young_people['number_of_trips'] = -1
    df_young_people['departement_id'] = "41"
    df_young_people['trip_weight'] = df_young_people['trip_weight']
    df_young_people['is_passenger'] = True
    df_young_people['socioprofessional_class'] = 8

    print(df_trips.info())

    return df_young_people



def execute(context):
    df_households, df_persons, df_trips, df_household_members = context.stage("seville.data.hts.entd.trip_distance")
    random = np.random.RandomState(context.config("random_seed"))


    df_young_persons = add_young_persons(random, df_persons, df_trips, df_household_members)

    

    return df_households, df_persons, df_trips, df_young_persons[df_persons.columns]
