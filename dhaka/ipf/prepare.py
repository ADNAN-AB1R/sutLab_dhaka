import pandas as pd
import numpy as np

"""
This stage updates the formatting of the population and employment census data sets such
that they can be procesed by the IPF algorithm.

For clarity:
    --------------------------------
    | Seville ~ Hannover
    --------------------------------
    | df_province ~ df_country
    | df_municipality ~ df_kreis
    | province_id ~ department_id
    | census_section_id ~ commune_id
    --------------------------------
"""

def configure(context):
    context.stage("seville.data.census.population")
    context.stage("seville.data.census.employment")
    context.stage("seville.data.census.licenses")

def execute(context):
    # Load data
    df_population = context.stage("seville.data.census.population")
    df_employment = context.stage("seville.data.census.employment")
    df_licenses_municipality = context.stage("seville.data.census.licenses")

    MAP_COLUMNS = {
        "province": "province_id",
        "municipality": "municipality_id",
        "census_section": "census_section_id",
        "age": "age_class"
    }
    for df in [df_population, df_employment, df_licenses_municipality]:
        df.rename(MAP_COLUMNS, axis=1, inplace=True)

    
    
    # Generate numeric sex
    df_population["sex"] = df_population["sex"].replace({ "male": 1, "female": 2 })
    df_employment["sex"] = df_employment["sex"].replace({ "male": 1, "female": 2 })
    df_licenses_municipality["sex"] = df_licenses_municipality["sex"].replace({ "male": 1, "female": 2 })


    # Validation
    unique_population_province = set(df_population["province_id"].unique())
    unique_employment_province = set(df_employment["municipality_id"].str[:2].unique())
    unique_licenses_province_mun = set(df_licenses_municipality["municipality_id"].str[:2].unique())

    assert unique_population_province == unique_employment_province
    assert unique_population_province == unique_licenses_province_mun

    unique_population_municipality = set(df_population["municipality_id"].unique())
    unique_licenses_municipality = set(df_licenses_municipality["municipality_id"].unique())
    unique_employment_municipality = set(df_employment["municipality_id"].unique())

    assert unique_population_municipality == unique_licenses_municipality
    assert unique_population_municipality == unique_employment_municipality

    unique_population_census_section = set(df_population["census_section_id"].unique())
    unique_employment_census_section = set(df_employment["census_section_id"].unique())

    assert unique_population_census_section == unique_employment_census_section

    unique_population_sex = set(df_population['sex'].unique())
    unique_employment_sex = set(df_employment['sex'].unique())
    unique_licenses_sex_mun = set(df_licenses_municipality['sex'].unique())

    assert unique_population_sex == unique_employment_sex, f"symm.difference: {unique_population_sex ^ unique_employment_sex}"
    assert unique_population_sex == unique_licenses_sex_mun, f"symm.difference: {unique_population_sex ^ unique_licenses_sex_mun}"

    # Generate numeric department index
    df_licenses_municipality["province_id"] = df_licenses_municipality["municipality_id"].str[:2]
    df_employment["province_id"] = df_employment["municipality_id"].str[:2]

    unique_census_sections = np.sort(df_population["census_section_id"].unique())
    unique_municipalities = np.sort(df_population["municipality_id"].unique())
    unique_provinces = np.sort(list(
        set(df_employment["province_id"].unique()) | 
        set(df_population["province_id"].unique())))

    
    census_section_mapping = { c: k for k, c in enumerate(unique_census_sections) }
    municipality_mapping = { c: k for k, c in enumerate(unique_municipalities) }
    province_mapping = { c: k for k, c in enumerate(unique_provinces) }


    df_population["census_section_index"] = df_population["census_section_id"].replace(census_section_mapping)
    df_employment["census_section_index"] = df_employment["census_section_id"].replace(census_section_mapping)

    df_population["municipality_index"] = df_population["municipality_id"].replace(municipality_mapping)
    df_employment["municipality_index"] = df_employment["municipality_id"].replace(municipality_mapping)
    df_licenses_municipality["municipality_index"] = df_licenses_municipality["municipality_id"].replace(municipality_mapping)

    df_population["province_index"] = df_population["province_id"].replace(province_mapping)
    df_employment["province_index"] = df_employment["province_id"].replace(province_mapping)
    df_licenses_municipality["province_index"] = df_licenses_municipality["province_id"].replace(province_mapping)

    ## Licenses

    # Consolidate sex and age
    population_age_classes = np.sort(df_population["age_class"].unique())
    license_age_classes = np.sort(df_licenses_municipality["age_class"].unique())
    
    joint_age_classes = np.sort(list(set(population_age_classes) & set(license_age_classes)))
    joint_age_upper = list(joint_age_classes[1:]) + [9999]

    for municipality_id in df_licenses_municipality["municipality_id"].unique():
        for sex in [1, 2]:
            for lower, upper in zip(joint_age_classes, joint_age_upper):
                f_population = df_population["sex"] == sex
                f_population &= df_population["age_class"] >= lower 
                f_population &= df_population["age_class"] < upper
                f_population &= df_population["municipality_id"] == municipality_id

                f_license = df_licenses_municipality["sex"] == sex
                f_license &= df_licenses_municipality["age_class"] >= lower
                f_license &= df_licenses_municipality["age_class"] < upper
                f_license &= df_licenses_municipality["municipality_id"] == municipality_id

                population = df_population.loc[f_population, "weight"].sum()
                licenses = df_licenses_municipality.loc[f_license, "weight"].sum()

                if population < licenses:
                    factor = population / licenses

                    print("Adapting sex:{} age:({}, {}) by factor {}".format(
                        ["", "m", "f"][sex], lower, upper, factor
                    ))

                    df_licenses_municipality.loc[f_license, "weight"] *= factor

    ## Employment

    # Consolidate sex and age
    population_age_classes = np.sort(df_population["age_class"].unique())
    employment_age_classes = np.sort(df_employment["age_class"].unique())

    joint_age_classes = np.sort(list(set(population_age_classes) & set(employment_age_classes)))
    joint_age_upper = list(joint_age_classes[1:]) + [9999]

    for census_section_id in df_employment["census_section_id"].unique():
        for sex in [1, 2]:
            for lower, upper in zip(joint_age_classes, joint_age_upper):

                f_population = df_population["sex"] == sex
                f_population &= df_population["age_class"] >= lower
                f_population &= df_population["age_class"] < upper
                f_population &= df_population["census_section_id"] == census_section_id

                f_employment = df_employment["sex"] == sex
                f_employment &= df_employment["age_class"] >= lower
                f_employment &= df_employment["age_class"] < upper
                f_employment &= df_employment["census_section_id"] == census_section_id

                population = df_population.loc[f_population, "weight"].sum()
                employment = df_employment.loc[f_employment, "weight"].sum()

                if population < employment:
                    factor = population / employment

                    print("Adapting EMPLOYMENT cs:{} sex:{} age:({}, {}) by factor {}".format(
                        census_section_id,
                        ["", "m", "f"][sex],
                        lower, upper,
                        factor
                    ))

                    df_employment.loc[f_employment, "weight"] *= factor


    return df_population, df_employment, df_licenses_municipality
