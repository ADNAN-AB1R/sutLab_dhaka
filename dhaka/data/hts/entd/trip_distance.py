from tqdm import tqdm
import pandas as pd
import numpy as np
import data.hts.hts as hts
from unidecode import unidecode
from geopy.distance import geodesic
import geopandas as gpd
from shapely.geometry import Point

"""
This stage calculates trip distance for HTS trips.
"""



def configure(context):
    context.stage("seville.data.hts.entd.cleaned")
    context.config("seville.hts", "Household Travel Survey 2017/BD entrevistas telefonicas Completa_Final_v2.xlsb.xlsx")
    context.config('seville.addresses.gpkg', 'shapefiles/addresses_seville_municipality.gpkg')
    context.config("seville.street_name_mapping", "street_data/street_name_mapping.csv")

# TODO: add more here
MUNICIPALITIES = {
    "Paradas": Point(-5.4976628439230835, 37.28983570971475),
    "La Algaba": Point(-6.012231250102343, 37.46139359455287),
    "Carmona": Point(-5.644710440930651, 37.4707779893395),
    "Tomares": Point(-6.04630100056516, 37.37367686919577),
    "La Rinconada": Point(-5.98158989448696, 37.486182193139285),
    "Burguillos": Point(-5.967895113856253, 37.58535602065082),
    "Olivares": Point(-6.157682234637542, 37.41888682807106),
    "Marchena": Point(-5.4168645472017, 37.32805716920212),
    "Herrera": Point(-4.848296470385442, 37.362042687398805),
}


# Map common street abbreviations to normalized street types
STREET_TYPE_MAP = {
    # ---------------- CALLE ----------------
    "CALLE": "CALLE",
    "C": "CALLE",
    "C/": "CALLE",
    "C.": "CALLE",
    "CL": "CALLE",

    # ---------------- AVENIDA ----------------
    "AVENIDA": "AVENIDA",
    "AVDA": "AVENIDA",
    "AVDA.": "AVENIDA",
    "AV": "AVENIDA",
    "AV.": "AVENIDA",

    # ---------------- PLAZA ----------------
    "PLAZA": "PLAZA",
    "PL": "PLAZA",
    "PL.": "PLAZA",
    "PLZA": "PLAZA",
    "PLZA.": "PLAZA",
    "PLZ": "PLAZA",

    # ---------------- PASEO ----------------
    "PASEO": "PASEO",
    "PSO": "PASEO",
    "PSO.": "PASEO",

    # ---------------- CAMINO ----------------
    "CAMINO": "CAMINO",
    "CNO": "CAMINO",
    "CNO.": "CAMINO",
    "CMNO": "CAMINO",

    # ---------------- CARRETERA ----------------
    "CTRA": "CARRETERA",
    "CTRA.": "CARRETERA",

    # ---------------- RONDA ----------------
    "RONDA": "RONDA",
    "RDA": "RONDA",
    "RDA.": "RONDA",

    # ---------------- GLORIETA ----------------
    "GLORIETA": "GLORIETA",
    "GTA": "GLORIETA",
    "GTA.": "GLORIETA",

    # ---------------- PASAJE ----------------
    "PASAJE": "PASAJE",
    "PJE.": "PASAJE",
    "PSAJE": "PASAJE",
    "PSJE": "PASAJE",

    # ---------------- PARQUE ----------------
    "PARQUE": "PARQUE",
    "PARQ": "PARQUE",
    "PARQ.": "PARQUE",
    "PQE": "PARQUE",
    "PQUE": "PARQUE",
    "PQUE.": "PARQUE",

    # ---------------- URBANIZACION ----------------
    "URBANIZACION": "URBANIZACION",
    "URB": "URBANIZACION",
    "URB.": "URBANIZACION",

    # ---------------- BARRIADA ----------------
    "BARRIADA": "BARRIADA",
    "BDA": "BARRIADA",
    "BDA.": "BARRIADA",

    # ---------------- GRUPO ----------------
    "GRUPO": "GRUPO",
    "GPO": "GRUPO",
    "GPO.": "GRUPO",
    "GRUP": "GRUPO",
    "GRUP.": "GRUPO",

    # -------- POLIGONO INDUSTRIAL --------
    "POLIGONO_INDUSTRIAL": "POLIGONO",
    "POLIGONO": "POLIGONO",
    "POLIGONO INDUSTRIAL": "POLIGONO",
    "POL. IND.": "POLIGONO",
    "PI": "POLIGONO",
    "P.I.": "POLIGONO",

    # -------- CENTRO COMERCIAL --------
    "CENTRO COMERCIAL": "CENTRO_COMERCIAL",
    "CC": "CENTRO_COMERCIAL",
    "C.C.": "CENTRO_COMERCIAL",

    # ---------- OTHER ------------
    "BARDA": "BARRIADA",
    "CLLON": "CALLEJON"
}

ARTICLES = ["EL", "(EL)", "LOS", "(LOS)", "LA", "(LA)", "LAS", "(LAS)", "(DE)", "DE", "(DE", "LA)", "LAS)", "LOS)", "DON", "DEL", "(DEL)"]
def remove_articles(s):
    return s.apply(
        lambda x: " ".join(
            word for word in x.split() if word not in ARTICLES
        )
    )
def fix_title_abbreviations(s):
    return s.apply(
        lambda x: " ".join(
            "DONA" if word == "DNA." else
            "DOCTOR" if word == "DR." else
            "DOCTORA" if word == "DRA." else
            "NUESTRA" if word == "NTRA." else
            "SENORA" if word == "SRA." else
            "CARDENAL" if word == "CARD." else
            "MAESTRO" if word == "MTRO." else word
            for word in x.split()
        )
    )

def remove_parenthesis_part(s):
    """Remove any trailing parenthetical clarification in street names."""
    return s.apply(lambda x: x.split('(')[0].strip())    


def normalize_street_string(addr: str):
    """
    Normalize free-text street strings by:
    - removing street numbers (STREET_NAME, 11 => STREET_NAME)
    - resolving abbreviations (Av. STREET_NAME => AVENIDA STREET_NAME)
    - reassembling TYPE + NAME (STREET_NAME (AVDA) => AVENIDA STREET_NAME)
    """
    street_name = addr
    # 1. Remove street number (everything after comma or last number)
    tokens = street_name.split(',')
    street_name = tokens[0]
    if len(tokens) == 2:
        # has number
        tokens = street_name.split()
        street_type = tokens[0]
        street_type = STREET_TYPE_MAP.get(street_type, street_type)
        street_name = street_type + " " + " ".join(tokens[1:]).strip()
    elif street_name[-1] == ')':
        tokens = street_name[:-1].split('(')
        street_type = tokens[-1]
        if street_type in STREET_TYPE_MAP.keys():
            street_type = STREET_TYPE_MAP.get(street_type, street_type)
            street_name = street_type + " " + "(".join(tokens[:-1]).strip()

    tokens = street_name.split(' ', maxsplit=1)
    tokens[0] = STREET_TYPE_MAP.get(tokens[0], tokens[0])
    street_name = " ".join(tokens).strip()

    return street_name

def normalize_street_type(name):
    if pd.isna(name):
        return name
    parts = name.split(" ", 1)
    street_type = parts[0]
    rest = parts[1] if len(parts) > 1 else ""
    
    street_type = STREET_TYPE_MAP.get(street_type, street_type)
    
    return f"{street_type} {rest}".strip()

def swap_calle_avenida(s):
    """Swap CALLE and AVENIDA to catch common classification mistakes."""

    return s.apply(
        lambda x: " ".join(
            "CALLE" if word == "AVENIDA" else 
            "AVENIDA" if word == "CALLE" else word
            for word in x.split()
        )
    )


def execute(context):
    df_households, df_persons, df_trips, df_household_members = context.stage("seville.data.hts.entd.cleaned")

    print(len(df_trips) * 2)


    #########################
    # EXTRACT ADDRESSES
    #########################

    ORI_COLUMNS = {
        "trip_id": "trip_id",
        "street_ori": "street_name",
        "zone_code_ori": "zone_code",
        "street_num_ori": "street_num",
        "municipality_code_ori": "municipality_code"
    }
    DES_COLUMNS = {
        "trip_id": "trip_id",
        "street_des": "street_name",
        "zone_code_des": "zone_code",
        "street_num_des": "street_num",
        "municipality_code_des": "municipality_code"
    }

    df_ori = df_trips[ORI_COLUMNS.keys()]
    df_ori = df_ori.rename(columns=ORI_COLUMNS)
    df_ori['type'] = 'ori'

    df_des = df_trips[DES_COLUMNS.keys()]
    df_des = df_des.rename(columns=DES_COLUMNS)
    df_des['type'] = 'des'

    df_addresses = pd.concat([df_ori, df_des])
    df_addresses['geometry'] = np.nan

    #########################
    # CLEAN ADDRESS
    #########################
 

    NO_STREET_INFO = ["LA CALLE NO APARECE EN EL LISTADO", "", "-", np.nan]
    df_addresses.loc[df_addresses['street_name'].isin(NO_STREET_INFO), 'street_name'] = np.nan
    
    # Clean municipality info
    SEVILLE_MUNICIPALITY_CODE = "41091"
    df_addresses.loc[df_addresses['municipality_code'].isin(['', '-', np.nan]), 'municipality_code'] = SEVILLE_MUNICIPALITY_CODE

    EXCEL_PATH = f"{context.config('data_path')}/{context.config('seville.hts')}"
    df_municipalities = pd.read_excel(
        EXCEL_PATH,
        dtype = {},
        sheet_name="Zon_",
        usecols="A:B",
        skiprows=137
    )
    MAP_MUNICIPALITY_COLUMNS = {
        "Cod": "municipality_code",
        "MUNICIPIO": "municipality_name"
    }    
    df_municipalities = df_municipalities[MAP_MUNICIPALITY_COLUMNS.keys()]
    df_municipalities = df_municipalities.rename(MAP_MUNICIPALITY_COLUMNS, axis=1)
    df_addresses = pd.merge(df_addresses, df_municipalities, on=['municipality_code'], how='left')
    df_addresses.loc[df_addresses['municipality_code'] == SEVILLE_MUNICIPALITY_CODE, 'municipality_name'] = 'Sevilla'
    df_addresses = df_addresses[df_addresses["municipality_name"] != "Otros"]


    # Clean street number
    df_addresses['street_num'] = df_addresses['street_num'].astype("string")
    df_addresses['street_num'] = df_addresses['street_num'].str.strip()


    df_addresses.loc[~df_addresses['street_num'].str.strip().str.isdigit(), 'street_num'] = np.nan

    # sometimes street numbers are in the street name
    no_street_number = df_addresses['street_name'].str.contains(',') & df_addresses['street_num'].isna()

    df_addresses.loc[no_street_number, 'street_num'] = (
        df_addresses.loc[no_street_number, "street_name"].str.split(",").str[1].str.strip()
    )
    
    has_coma = df_addresses['street_name'].str.contains(',') & df_addresses['street_name'].notna()
    df_addresses.loc[has_coma, 'street_name'] = (
        df_addresses.loc[has_coma, "street_name"].str.split(",").str[0].str.strip()
    )


    # Clean street name
    df_addresses['street_name'] = df_addresses['street_name'].astype("string")
    notna = df_addresses['street_name'].notna()
    df_addresses.loc[notna, 'street_name'] = df_addresses.loc[notna, 'street_name'].str.upper()
    df_addresses.loc[notna, 'street_name'] = df_addresses.loc[notna, 'street_name'].apply(unidecode)
    df_addresses.loc[notna, 'street_name'] = df_addresses.loc[notna, 'street_name'].apply(normalize_street_string)
    df_addresses.loc[notna, 'street_name'] = remove_articles(df_addresses.loc[notna, 'street_name'])
    df_addresses.loc[notna, 'street_name'] = fix_title_abbreviations(df_addresses.loc[notna, 'street_name'])
    df_addresses.loc[notna, 'street_name'] = remove_parenthesis_part(df_addresses.loc[notna, 'street_name'])

    df_addresses['geometry'] = np.nan

    # mark trip addresses that are inside Seville and have no street information
    unknown_street = (~notna) & (df_addresses['municipality_code'] == SEVILLE_MUNICIPALITY_CODE)
    df_addresses['known_address'] = True
    df_addresses.loc[unknown_street, 'known_address'] = False
    ###
    COMMON_CRS = "EPSG:4326"

    ##################################################
    # ASSIGN GEOMTERY BY STREET + STREET NUMBER
    ##################################################

    FILE_URL = f"{context.config('data_path')}/{context.config('seville.addresses.gpkg')}"
    gdf_real_addresses = gpd.read_file(FILE_URL)
    gdf_real_addresses = gdf_real_addresses.to_crs(COMMON_CRS)
    MAP_ADDRESS_COLUMNS = {
        "street": "street_name",
        "housenumber": "street_num",
        "geometry": "geometry"
        }


    gdf_real_addresses = gdf_real_addresses[MAP_ADDRESS_COLUMNS.keys()]
    gdf_real_addresses = gdf_real_addresses.rename(columns=MAP_ADDRESS_COLUMNS)
    gdf_real_addresses = gdf_real_addresses[gdf_real_addresses['street_name'].notna()]

    gdf_real_addresses['street_name'] = gdf_real_addresses['street_name'].astype("string")
    gdf_real_addresses['street_name'] = gdf_real_addresses['street_name'].str.upper()
    gdf_real_addresses['street_name'] = gdf_real_addresses['street_name'].apply(unidecode)
    gdf_real_addresses["street_name"] = gdf_real_addresses["street_name"].apply(normalize_street_type)
    gdf_real_addresses['street_name'] = remove_articles(gdf_real_addresses['street_name'])
    gdf_real_addresses = gdf_real_addresses[~gdf_real_addresses.duplicated(subset=['street_name'], keep=False)]
    # TODO: clean irregular street number values (e.g., "8-10")
    df_addresses = df_addresses.merge(gdf_real_addresses[['street_name', 'street_num', 'geometry']], on='street_name', how='left', suffixes=('', '_street'))
    df_addresses['geometry'] = df_addresses['geometry'].fillna(df_addresses['geometry_street'])
    df_addresses = df_addresses.drop(columns=['geometry_street'])


    total = len(df_addresses)
    potentially_assignable = df_addresses['street_num'].notna() & df_addresses['street_name'].notna()
    actually_assigned = df_addresses['street_num'].notna() & df_addresses['geometry'].notna() & df_addresses['street_name'].notna()
    pct = actually_assigned.sum() / potentially_assignable.sum() * 100
    print(f"[INFO] potentially_assignable {potentially_assignable.sum()} / {total} ({(potentially_assignable.sum() / total * 100):.2f}%)")
    print(f"[INFO] managed to convert using street number {actually_assigned.sum()} / {potentially_assignable.sum()} ({pct:.2f}%)")

    invalid_geometry = df_addresses['geometry'].isna()
    invalid_dist_pct = invalid_geometry.sum() / len(df_addresses) * 100
    print(f"[INFO] not resolved after street number {invalid_geometry.sum()} / {len(df_addresses)} ({invalid_dist_pct:.2f}%)")


    ###########################################
    # STREET MANUAL MAPPING
    ###########################################

    

    CSV_FILE = f"{context.config('data_path')}/{context.config('seville.street_name_mapping')}"
    df_street_manual_mapping = pd.read_csv(CSV_FILE, sep=',')
    mapping = df_street_manual_mapping.set_index('street_name')['street_name_original']

    df_addresses['street_name_'] = df_addresses['street_name']
    df_addresses['street_name'] = df_addresses['street_name'].map(mapping)
    df_addresses = df_addresses.merge(gdf_real_addresses[['street_name', 'street_num', 'geometry']], on='street_name', how='left', suffixes=('', '_street'))
    df_addresses['geometry'] = df_addresses['geometry'].fillna(df_addresses['geometry_street'])
    df_addresses['street_name'] = df_addresses['street_name_']
    df_addresses = df_addresses.drop(columns=['geometry_street', 'street_name_'])

    ###########################################
    # TRY PREPENDING "CALLE"
    ###########################################

    df_addresses['street_name_'] = df_addresses['street_name']
    df_addresses['street_name'] = "CALLE " + df_addresses['street_name']
    df_addresses = df_addresses.merge(gdf_real_addresses[['street_name', 'street_num', 'geometry']], on='street_name', how='left', suffixes=('', '_street'))
    df_addresses['geometry'] = df_addresses['geometry'].fillna(df_addresses['geometry_street'])
    df_addresses['street_name'] = df_addresses['street_name_']
    df_addresses = df_addresses.drop(columns=['geometry_street', 'street_name_'])


    ###########################################
    # SWITCH "CALLE" WITH "AVENIDA"
    ###########################################

    df_addresses['street_name_'] = df_addresses['street_name']
    notna = df_addresses['street_name'].notna()
    df_addresses.loc[notna, 'street_name'] = swap_calle_avenida(df_addresses.loc[notna, 'street_name'])
    df_addresses = df_addresses.merge(gdf_real_addresses[['street_name', 'street_num', 'geometry']], on='street_name', how='left', suffixes=('', '_street'))
    df_addresses['geometry'] = df_addresses['geometry'].fillna(df_addresses['geometry_street'])
    df_addresses['street_name'] = df_addresses['street_name_']
    df_addresses = df_addresses.drop(columns=['geometry_street', 'street_name_'])


    invalid_geometry = df_addresses['geometry'].isna()
    invalid_dist_pct = invalid_geometry.sum() / len(df_addresses) * 100
    print(f"[INFO] unresolved after tricks with street names {invalid_geometry.sum()} / {len(df_addresses)} ({invalid_dist_pct:.2f}%)")

    failed_to_assign = df_addresses['geometry'].isna() & df_addresses['street_name'].notna()
    num = 80
    print(df_addresses.loc[failed_to_assign, 'street_name'].value_counts().sort_values(ascending=False)[:num])
    print(df_addresses.loc[failed_to_assign, 'street_name'].value_counts().sort_values(ascending=False)[:num].sum())
    print(len(df_addresses[failed_to_assign]))


    ##################################################
    # MERGE BACK TO TRIPS
    ##################################################

    ori_geom = df_addresses[df_addresses["type"] == "ori"][["trip_id", "geometry", 'known_address']]
    des_geom = df_addresses[df_addresses["type"] == "des"][["trip_id", "geometry", 'known_address']]
    ori_geom = ori_geom.rename(columns={"geometry": "geometry_ori", 'known_address':'known_address_ori'})
    des_geom = des_geom.rename(columns={"geometry": "geometry_des", 'known_address':'known_address_des'})
    df_trips = df_trips.merge(ori_geom, on="trip_id", how="left")
    df_trips = df_trips.merge(des_geom, on="trip_id", how="left")

    # Calculate euclidean distance
    # df_trips['euclidean_distance'] = np.nan
    has_valid_geometry = df_trips['geometry_ori'].notna() & df_trips['geometry_des'].notna()
    df_trips.loc[has_valid_geometry, 'euclidean_distance'] = df_trips.loc[has_valid_geometry].apply(
        lambda x: geodesic((x.geometry_ori.y, x.geometry_ori.x), (x.geometry_des.y, x.geometry_des.x)).meters, 
        axis=1
    )
    df_trips.loc[has_valid_geometry]

    df_trips.loc[has_valid_geometry, 'origin_location'] = df_trips.loc[has_valid_geometry, "geometry_ori"]
    df_trips.loc[has_valid_geometry, 'destination_location'] = df_trips.loc[has_valid_geometry, "geometry_des"]


    ##################################################
    # FALLBACK
    ##################################################

    # if euclidean_distance == 0 or is np.nan


    def distance_from_time(row):
        walk_time = row['walk_origin'] + row['walk_destination']
        walk_time = walk_time * 60 # minutes => seconds
        main_mode_time = row['trip_duration'] - walk_time

        # if the main mode is lesser than 0, assume only main mode time travel
        if main_mode_time < 0:
            print(f"[WARNING] duration of the main mode transport of trip is negative {main_mode_time}, defaulting to {row['trip_duration']}")
            main_mode_time = row['trip_duration']


        SPEEDS_MS = {
            "walk": 1.3,  # walk (~4.7 km/h)
            "pt": 3.8,  # public transport (~13.7 km/h)
            "bike": 4.5,  # bike (~16.2 km/h)
            "car": 6.0,  # car (~21.6 km/h)
            "car_passenger": 6.0,  # car passenger
            "pt": 3.8   # public transport
        }

        routed_distance = SPEEDS_MS[row['mode']] * main_mode_time
        routed_distance += SPEEDS_MS['walk'] * walk_time



        euclidean_distance = routed_distance / 1.3

        return euclidean_distance

    df_trips.loc[df_trips['walk_origin'] == '-', 'walk_origin'] = 0
    df_trips.loc[df_trips['walk_destination'] == '-', 'walk_destination'] = 0


    invalid_distance = (
        (df_trips['euclidean_distance'].isna()) |
        (df_trips['euclidean_distance'] <= 10) |
        (df_trips['known_address_des'] == False) |
        (df_trips['known_address_des'] == False)
    )

    invalid_dist_pct = invalid_distance.sum() / len(df_trips) * 100
    print(f"[INFO] Distance fallback for {invalid_distance.sum()} / {len(df_trips)} ({invalid_dist_pct:.2f}%)")


    df_trips.loc[invalid_distance, 'euclidean_distance'] = df_trips[invalid_distance].apply(distance_from_time, axis=1)
    df_trips.loc[invalid_distance, "origin_location"] = np.nan
    df_trips.loc[invalid_distance, "destination_location"] = np.nan

    invalid_euclidean_distance = len(df_trips.loc[(df_trips['euclidean_distance'] < 0) | df_trips['euclidean_distance'].isna()])
    assert invalid_euclidean_distance == 0, f"invalid euclidean distance for {invalid_euclidean_distance} trips"

    return df_households, df_persons, df_trips, df_household_members
