import numpy as np
import pandas as pd

"""
This stage sets attributes for household members based on the census distribution.
"""

def configure(context):
    context.stage("seville.data.hts.entd.household_members.add_persons")
    context.stage("seville.data.census.population")
    context.stage("seville.data.census.employment")
    context.stage("seville.data.census.licenses")
    context.config("random_seed")

def expand_age_bins(df_population):
    """
    Expand 5-year age_class population into single-year ages
    assuming uniform distribution within each bin.
    """

    AGE_BINS = sorted(df_population['age_class'].unique().tolist() + [101])
    rows = []

    for _, row in df_population.iterrows():
        age_start = row["age_class"]
        idx = AGE_BINS.index(age_start)
        age_end = AGE_BINS[idx + 1]
        bin_size = age_end - age_start

        weight_per_age = row["weight"] / bin_size

        for age in range(age_start, age_end):
            rows.append({
                "sex": row["sex"],
                "age": age,
                "weight": weight_per_age
            })

    return pd.DataFrame(rows)

def assign_attribute(df_population, df_attribute, df_persons, column_name, random):

    initial_len = len(df_persons)

    df_population_lic = df_population.copy()
    df_population_lic = df_population_lic.groupby(['age_class', 'sex'])['weight'].sum().reset_index()

    # add empty rows of employment for persons younger than 15
    min_attribute_age = df_attribute["age_class"].min()
    df_attribute_young = df_attribute[df_attribute['age_class'] == min_attribute_age].copy()
    df_attribute_young['weight'] = 0
    df_attribute_young['age_class'] = 0
    df_attribute = pd.concat([df_attribute, df_attribute_young])

    # match population data with attribute age bins

    df_population_lic = expand_age_bins(df_population_lic)
    df_attribute = expand_age_bins(df_attribute)
    
    df_population_lic = df_population_lic.rename(columns={"weight": "total"})

    print("df_attribute columns:", df_attribute.columns)
    print("df_population_lic columns:", df_population_lic.columns)

    df_attribute = df_attribute.merge(df_population_lic, on=['sex', 'age'])
    df_attribute['rate'] = df_attribute['weight'] / df_attribute['total']

    df_persons = df_persons.merge(df_attribute[['sex', 'age', 'rate']], on=['sex', 'age'])
    # Bernoulli draw per row
    df_persons[column_name] = random.rand(len(df_persons)) < df_persons["rate"]

    df_persons = df_persons.drop(columns=['rate'])

    final_len = len(df_persons)
    assert initial_len == final_len, f"before {initial_len} after {final_len}"

    return df_persons


def fix_ids(df_persons: pd.DataFrame, df_trips: pd.DataFrame):
    
    # Sort
    df_persons = df_persons.sort_values(by='household_id').reset_index(drop=True)
    
    df_persons['new_person_id'] = np.arange(len(df_persons))
    id_map = dict(zip(df_persons['person_id'], df_persons['new_person_id']))
    
    # Apply mapping to trips
    df_trips['person_id'] = df_trips['person_id'].map(id_map)
    
    # Replace old IDs in persons
    df_persons['person_id'] = df_persons['new_person_id']
    
    # drop helper column
    df_persons = df_persons.drop(columns=['new_person_id'])
    
    return df_persons, df_trips


def execute(context):
    df_households, df_persons, df_trips, df_added_persons = context.stage("seville.data.hts.entd.household_members.add_persons")
    random = np.random.RandomState(context.config("random_seed"))

    df_population = context.stage("seville.data.census.population").copy()
    df_employment = context.stage("seville.data.census.employment").copy()
    df_licenses = context.stage("seville.data.census.licenses").copy()

    df_population = df_population.groupby(['sex', 'age_class'])['weight'].sum().reset_index()
    df_employment = df_employment.groupby(['sex', 'age_class'])['weight'].sum().reset_index()
    df_licenses = df_licenses.groupby(['sex', 'age_class'])['weight'].sum().reset_index()

    print(df_population.info())
    print(df_employment.info())
    print(df_licenses.info())

    initial_len = len(df_added_persons)
    df_added_persons = assign_attribute(df_population, df_licenses, df_added_persons, 'has_license', random)
    df_added_persons = assign_attribute(df_population, df_employment, df_added_persons, 'employed', random)

    df_persons_study = df_persons.copy()
    df_persons_study = (
        df_persons
        .groupby(["sex", "age"])["studies"]
        .mean()
        .reset_index(name="rate")
    )

    df_added_persons = df_added_persons.merge(df_persons_study[['sex', 'age', 'rate']], on=['sex', 'age'], how='left')
    # Bernoulli draw per row
    df_added_persons["studies"] = random.rand(len(df_added_persons)) < df_added_persons["rate"]
    df_added_persons.loc[df_added_persons['age'] <= 15, "studies"] = True
    df_added_persons.loc[df_added_persons['studies'].isna(), "studies"] = False
    df_added_persons = df_added_persons.drop(columns=['rate'])

    final_len = len(df_added_persons)
    assert initial_len == final_len, f"before {initial_len} after {final_len}"
    assert df_added_persons.isnull().values.any() == False

    # merge results
    df_persons = pd.concat([df_persons, df_added_persons])


    df_persons, df_trips = fix_ids(df_persons, df_trips)

    return df_households, df_persons, df_trips