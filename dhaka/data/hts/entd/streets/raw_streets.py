from tqdm import tqdm
import pandas as pd
import numpy as np

"""
This stage extracts all trip addresses that are present in the Seville HTS survey.
"""


def configure(context):
    context.config("data_path")
    context.config("seville.hts", "Sent by the City/Household Travel Survey 2017/BD entrevistas telefonicas Completa_Final_v2.xlsb.xlsx")
    context.config("seville.street_data", "street_data/street_data.csv")
    context.config("seville.street_data_2", "street_data/street_data_2.csv")


MAP_STREET_COLUMNS_DES = {
    "#Den_CALLESEV_ORI": "street",
    "#Zona_CALLESEV_ORI": "zone_code",
    "MUNI_ORI_SEV_D1": "municipality_code",
}

MAP_STREET_COLUMNS_ORI = {
    "#Den_CALLESEVDES": "street",
    "#Zona_CALLESEV_DES": "zone_code",
    "MUNI_DES_SEV_D1": "municipality_code",
}

MAP_ZONES_COLUMNS = {
    "ZONA17": "zone_code",
    "BARRIO": "zone",
}

MAP_MUNICIPALITY_COLUMNS = {
    "Cod": "municipality_code",
    "MUNICIPIO": "municipality"
}

def execute(context):

    tqdm.pandas()
    EXCEL_PATH = f"{context.config('data_path')}/{context.config('seville.hts')}"
    trips_sheet = pd.read_excel(
        EXCEL_PATH,
        dtype = {},
        sheet_name="Viajes_Dep",
    )

    # Filter out streets that belong to trips partially outside province
    trips_sheet = trips_sheet[(trips_sheet["PROVI_ORI_D1"] == "-") & (trips_sheet["PROVI_DES_D1"] == "-") ]

    df_origin = trips_sheet[MAP_STREET_COLUMNS_ORI.keys()]
    df_origin = df_origin.rename(MAP_STREET_COLUMNS_ORI, axis=1)

    df_destination = trips_sheet[MAP_STREET_COLUMNS_DES.keys()]
    df_destination = df_destination.rename(MAP_STREET_COLUMNS_DES, axis=1)

    df_streets: pd.DataFrame = pd.concat([df_origin, df_destination])
    df_streets = df_streets.drop_duplicates()

    # -------------------------------------------------------------

    EXCEL_PATH = f"{context.config('data_path')}/{context.config('seville.hts')}"
    df_zones = pd.read_excel(
        EXCEL_PATH,
        dtype = {},
        sheet_name="Zon_",
        usecols="A:B",
        nrows=137
    )
    df_zones = df_zones.rename(MAP_ZONES_COLUMNS, axis=1)

    df_municipalities = pd.read_excel(
        EXCEL_PATH,
        dtype = {},
        sheet_name="Zon_",
        usecols="A:B",
        skiprows=137
    )
    df_municipalities = df_municipalities.rename(MAP_MUNICIPALITY_COLUMNS, axis=1)
    seville_row = pd.DataFrame({"municipality_code": ["-", ""], "municipality": ["Sevilla", "Sevilla"]})
    df_municipalities = pd.concat([df_municipalities, seville_row], ignore_index=True)

    # -------------------------------------------------------------


    #  map zone names in the df_streets
    df_streets = pd.merge(df_streets, df_zones, on=['zone_code'], how='left')
    df_streets = pd.merge(df_streets, df_municipalities, on=['municipality_code'], how='left')

    # Unknown destination
    df_streets = df_streets[df_streets["municipality"] != "Otros"]
    
    df_streets = df_streets[['municipality', 'zone', 'street']]

    return df_streets