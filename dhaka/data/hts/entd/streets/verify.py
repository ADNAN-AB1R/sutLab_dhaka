import pandas as pd
import numpy as np
import geopandas as gpd
from shapely.geometry import Point
import os
from unidecode import unidecode


"""
Final stage of street-address-to-coordinates processing.
This step validates and corrects coordinates using spatial rules
(streets, boroughs, and province boundaries).
"""


def configure(context):
    context.config("data_path")
    context.config("seville.province_shapefile", "shapefiles/seville_province_shp/seville_province.gpkg.shp")
    context.config("seville.borroughs_shp", "shapefiles/borroughs/Barrios.shp")
    context.config("seville.streets_shp", "shapefiles/streets/vial.shp")

    context.config("seville.street_data", "street_data/street_data.csv")
    context.config("seville.street_name_mapping", "street_data/street_name_mapping.csv")

    context.stage("seville.data.hts.entd.streets.manual_cleaning")


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

ARTICLES = ["EL", "(EL)", "LOS", "(LOS)", "LA", "(LA)", "LAS", "(LAS)", "(DE)", "DE", "(DE", "LA)", "LAS)", "LOS)", "DON"]
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
            "CARDENAL" if word == "CARD." else word
            for word in x.split()
        )
    )
def swap_calle_avenida(s):
    """Swap CALLE and AVENIDA to catch common classification mistakes."""

    return s.apply(
        lambda x: " ".join(
            "CALLE" if word == "AVENIDA" else 
            "AVENIDA" if word == "CALLE" else word
            for word in x.split()
        )
    )
def remove_parenthesis_part(s):
    """Remove any trailing parenthetical clarification in street names."""
    return s.apply(lambda x: x.split('(')[0].strip())    


def streets_to_points(df_streets):
    """
    Prepare street data as a GeoDataFrame and clean basic attributes
    (municipality, zone, street) for verification purposes.
    """
    gdf_points = gpd.GeoDataFrame(df_streets, crs='EPSG:4326')

    # Set municipality
    filter = (
        (gdf_points['municipality'] == "")
        | (gdf_points['municipality'] == "NAN")
        | (gdf_points['municipality'].isna())
    )
    gdf_points.loc[filter, 'municipality'] = 'Sevilla'

    # Set missing zones as NaNs
    filter = (
        (gdf_points['zone'] == "")
        | (gdf_points['zone'] == "-")
        | (gdf_points['zone'] == "NAN")
        | (gdf_points['zone'].isna())
    )
    gdf_points.loc[filter, 'zone'] = np.nan

    # Set missing streets as NaNs
    filter = (
        (gdf_points["street"] == "LA CALLE NO APARECE EN EL LISTADO")
        | (gdf_points["street"] == "")
        | (gdf_points["street"] == "-")
        | (gdf_points["street"] == "NAN")
        | (gdf_points["street"].isna())
    )
    gdf_points.loc[filter, 'street'] = np.nan
    
    return gdf_points
    

def verify_and_fix_by_borrough(gdf_borroughs, gdf_points):
    """
    Ensure points fall inside Seville. If not, If not, move them 
    to the borough's representative point.
    """
    
    if gdf_points.crs != gdf_borroughs.crs:
        gdf_borroughs = gdf_borroughs.to_crs(gdf_points.crs)

    gdf_points_check = gdf_points[
        (gdf_points['zone'].notna()) &
        (gdf_points['municipality'] == 'Sevilla')
    ]

    # Spatial join to check if point lies within a borough
    joined = gpd.sjoin(
        gdf_points_check,
        gdf_borroughs[['Barrio', 'geometry']],        
        how='left',
        predicate='within'
    )

    joined['is_outside'] = joined['Barrio'].isna()
    
    # Cartuja is not included in borough shapefile
    if 'CARTUJA' not in joined['Barrio'].values:
        print(f"[INFO] ignoring validation of zone CARTUJA for {len(joined[joined['zone']=='CARTUJA'])} rows")
        joined.loc[joined['zone']=='CARTUJA', 'is_inside'] = True

    mask_no_borrough = joined['is_outside']
    print(f"[INFO] Seville locations outside Seville: {mask_no_borrough.sum()}. Moving to this to zone.")


    if False:
        # Use representative points as fallback locations
        gdf_borroughs['centroid'] = gdf_borroughs.geometry.representative_point()
        MAP_ZONES = dict(zip(gdf_borroughs['Barrio'], gdf_borroughs['centroid']))

        joined.loc[mask_no_borrough, 'geometry'] = (
            joined.loc[mask_no_borrough, 'zone'].map(MAP_ZONES)
        )
    else:
        joined.loc[mask_no_borrough, 'geometry'] = np.nan


    # Apply fixes back to the original dataframe
    fixed = joined.loc[mask_no_borrough, ['id', 'geometry']]
    gdf_points = gdf_points.set_index('id')
    gdf_points.loc[fixed.id, 'geometry'] = fixed.set_index('id')['geometry']
    gdf_points = gdf_points.reset_index()

    gdf_points.set_geometry('geometry')

    return gdf_points


def verify_and_fix_street_proximity(gdf_streets, gdf_borroughs, gdf_points, df_street_manual_mapping):
    """
    Ensure points are close to their associated street geometry.
    If too far away, relocate them to the street's representative point.
    """

    gdf_borroughs = gdf_borroughs.copy()

    gdf_points = gdf_points[
        (gdf_points['street'].notna()) &
        (gdf_points['municipality'] == 'Sevilla')
    ]


    gdf_points['street_name'] = gdf_points['street'].str.upper()

    gdf_streets = gdf_streets.rename(columns={
        "nom_normal": "street_name_original",
        "nom_via": "street_name",
        "nom_tip_vi": "street_type"
        })
    gdf_streets['street_name'] = gdf_streets['street_name'].str.upper()

    # Cases:
    # 1) street is already normalized
    # 2) TYPE STREET_NAME, NUMBER
    # 3) STREET_NAME, NUMBER - REGIMIENTO DE SORIA, 11
    # 4) STREET_NAME - HOSPITAL VIRGEN DEL ROCIO
    def normalize_street_string(addr: str):
        """
        Normalize free-text street strings by:
        - removing street numbers (STREET_NAME, 11 => STREET_NAME)
        - resolving abbreviations (Av. STREET_NAME => AVENIDA STREET_NAME)
        - reassembling TYPE + NAME (STREET_NAME (AVDA) => AVENIDA STREET_NAME)
        """

        street_name = addr
        street_name = unidecode(street_name)
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
        return street_name
    
    # Normalize official street names
    gdf_streets['duplicated'] = gdf_streets.duplicated(subset=['street_name'], keep=False)
    gdf_streets['street_name'] = gdf_streets['street_type'] + " " + gdf_streets['street_name']
    gdf_streets['street_name'] = gdf_streets['street_name'].apply(unidecode)
    gdf_streets['street_name'] = remove_articles(gdf_streets['street_name'])
    gdf_streets = gdf_streets.drop_duplicates(subset='street_name')

    # Normalize point street names
    gdf_points['street_name'] = gdf_points['street_name'].apply(normalize_street_string)
    gdf_points['street_name'] = remove_articles(gdf_points['street_name'])
    gdf_points['street_name'] = fix_title_abbreviations(gdf_points['street_name'])
    gdf_points['street_name'] = remove_parenthesis_part(gdf_points['street_name'])

    gdf_points['true_street_name'] = gdf_points['street_name']

    # Switch to metric CRS for distance calculations
    METRIC_CRS = "EPSG:25830"
    gdf_points = gdf_points.to_crs(METRIC_CRS)
    gdf_streets = gdf_streets.to_crs(METRIC_CRS)
    gdf_borroughs = gdf_borroughs.to_crs(METRIC_CRS)

    # Multiple matching strategies (A -> D)
    # A) Match by normalized name
    gdf = gdf_points.merge(gdf_streets[['street_name', 'geometry']], on='street_name', how='left', suffixes=('_point', '_street'))

    unmatched = gdf.loc[gdf['geometry_street'].isna()]
    gdf = gdf[gdf['geometry_street'].notna()]

    # B) Manual mapping fallback
    unmatched = unmatched.rename(columns={'geometry_point':'geometry'})
    unmatched = unmatched.drop(columns='geometry_street')
    unmatched = unmatched.merge(df_street_manual_mapping[['street_name', 'street_name_original']], on='street_name', how='left')

    gdf2 = unmatched.merge(gdf_streets[['geometry', 'street_name_original']], on='street_name_original', how='left', suffixes=('_point', '_street'))
    gdf = pd.concat([gdf, gdf2])
    unmatched = gdf.loc[gdf['geometry_street'].isna()]
    gdf = gdf[gdf['geometry_street'].notna()]

    # C) Try swapping CALLE/AVENIDA
    unmatched['street_name'] = swap_calle_avenida(unmatched['street_name'])
    unmatched = unmatched.rename(columns={'geometry_point':'geometry'})
    unmatched = unmatched.drop(columns='geometry_street')
    gdf2 = unmatched.merge(gdf_streets[['street_name', 'geometry']], on='street_name', how='left', suffixes=('_point', '_street'))
    gdf = pd.concat([gdf, gdf2])
    unmatched = gdf.loc[gdf['geometry_street'].isna()]
    gdf = gdf[gdf['geometry_street'].notna()]

    # D) Final attempt: force CALLE prefix
    unmatched['street_name'] = 'CALLE ' + unmatched['street_name']
    unmatched = unmatched.rename(columns={'geometry_point':'geometry'})
    unmatched = unmatched.drop(columns='geometry_street')
    gdf2 = unmatched.merge(gdf_streets[['street_name', 'geometry']], on='street_name', how='left', suffixes=('_point', '_street'))
    gdf = pd.concat([gdf, gdf2])
    unmatched = gdf.loc[gdf['geometry_street'].isna()]
    gdf = gdf[gdf['geometry_street'].notna()]
    


    
    print("[INFO] number of unmatched street names:", len(unmatched))
    unmatched['geometry_point'] = np.nan
    

    # Constrain street geometry to borough boundaries
    gdf_borroughs = gdf_borroughs.rename(
        columns={
            "Barrio": 'zone',
            "geometry": 'geometry_borrough'
            }
        )
    gdf = gdf.merge(gdf_borroughs[['zone', 'geometry_borrough']], on='zone', how='left')
    mask = gdf['geometry_borrough'].notna()    

    gdf['original_geometry_street'] = gdf['geometry_street']

    gdf.loc[mask, 'geometry_street'] = gdf.loc[mask].apply(
        lambda r: r.geometry_street.buffer(10).intersection(r.geometry_borrough), # 10m buffer
        axis=1
    )
    gdf['geometry_street'] = gdf['geometry_street'].apply(
        lambda g: None if g is None or g.is_empty else g
    )

    # Restore original street geometry if intersection fails
    mask_reverse_change = (gdf['geometry_street'] == None) | gdf['geometry_street'].isna()
    print("[INFO] number of streets that do not match address borrough:", len(gdf[gdf['geometry_street'] == None]))
    gdf.loc[mask_reverse_change, 'geometry_street'] = gdf.loc[mask_reverse_change, 'original_geometry_street']

    gdf = gpd.GeoDataFrame(gdf, geometry='geometry_point', crs=METRIC_CRS)  

    # Distance-based validation
    gdf['dist_to_street'] = gdf.distance(gdf['geometry_street'])
    MAX_DIST = 60  # meters
    gdf['is_near_street'] = gdf['dist_to_street'] <= MAX_DIST

    mask = ~gdf['is_near_street'] & gdf['geometry_street'].notna()  
    gdf['old_geometry_point'] = gdf['geometry_point']
    print(f'[INFO] fixing {len(gdf.loc[mask])} locations by assigning them street coordinates (total = {len(gdf)})')

    print(f'[INFO] fixing with numbers {len(gdf.loc[mask & True])} locations by assigning them street coordinates (total = {len(gdf)})')


    gdf.loc[mask, 'geometry_point'] = gdf.loc[mask, 'geometry_street'].representative_point()

    # Return to geographic CRS
    gdf = gdf.to_crs("EPSG:4326")

    # Final gdf with also with unmatched streets set to np.nan
    gdf = pd.concat([gdf, unmatched], ignore_index=True)
    gdf['geometry'] = gdf['geometry_point']


    gdf.set_geometry('geometry')

    return gdf


def execute(context):


    # Merge both dataframes:
    CSV_PATH = f"{context.config('data_path')}/{context.config('seville.street_data')}"
    df_streets_original = pd.read_csv(CSV_PATH, sep='\t', dtype={"location":str})
    df_streets_fixed = context.stage("seville.data.hts.entd.streets.manual_cleaning")


    # Merge manual corrections
    df_streets = df_streets_original.merge(df_streets_fixed[['municipality', 'zone', 'street', 'location']], 
                           on=['municipality', 'zone', 'street'], 
                           how='left', 
                           suffixes=('', '_updated'))
    df_streets['location'] = df_streets['location_updated'].fillna(df_streets['location'])

    df_streets = df_streets[df_streets["municipality"] != "Otros"] # Filter out unknown locations

    def parse_location(location_str):
        # Clean and parse the string
        location_str = str(location_str)
        cleaned_str = location_str.strip("()")
        latitude, longitude  = cleaned_str.split(',')
        return Point(float(longitude), float(latitude))



    df_streets['geometry'] = df_streets['location'].apply(parse_location)
    df_streets = df_streets.drop(columns=['location'])
    df_streets['id'] = np.arange(len(df_streets))



    gdf_points = streets_to_points(df_streets)
    
    # Verify geometry is inside Seville province
    print("Checking that all locations are in the province of Seville...")
    SHP_FILE = f"{context.config('data_path')}/{context.config('seville.province_shapefile')}"
    gdf_province = gpd.read_file(SHP_FILE, crs='EPSG:4326')
    gdf_points['is_inside'] = gdf_points.geometry.apply(lambda point: gdf_province.contains(point).any())
    assert np.all(gdf_points['is_inside']==True), 'some of the locations are outside Seville province'

    # Load street and borrough(zone) shapefiles
    SHP_FILE = f"{context.config('data_path')}/{context.config('seville.borroughs_shp')}"
    gdf_borroughs = gpd.read_file(SHP_FILE)
    gdf_borroughs = gdf_borroughs.to_crs("EPSG:4326")

    SHP_FILE = f"{context.config('data_path')}/{context.config('seville.streets_shp')}"
    gdf_real_streets = gpd.read_file(SHP_FILE)
    gdf_real_streets = gdf_real_streets.to_crs("EPSG:4326")

    SHP_FILE = f"{context.config('data_path')}/{context.config('seville.street_name_mapping')}"
    df_street_manual_mapping = pd.read_csv(SHP_FILE, sep=',')

    # Street-based correction
    gdf_points = verify_and_fix_street_proximity(gdf_real_streets, gdf_borroughs, gdf_points, df_street_manual_mapping)
    df_streets = df_streets.set_index('id')
    df_streets.loc[gdf_points['id'], 'geometry'] = gdf_points.geometry.values
    df_streets = df_streets.reset_index()

    # Borough-based fallback correction
    gdf_points = streets_to_points(df_streets)
    gdf_points = verify_and_fix_by_borrough(gdf_borroughs, gdf_points)
    df_streets = df_streets.set_index('id')
    df_streets.loc[gdf_points['id'], 'geometry'] = gdf_points.geometry.values
    df_streets = df_streets.reset_index()


    print("[INFO] parsing HTS trip addresses finished successfully.")

    return df_streets[['municipality', 'zone', 'street', 'geometry']]

def validate(context):
    filenames = [
        "seville.province_shapefile",
        "seville.borroughs_shp",
        "seville.streets_shp",
        "seville.street_data",
        "seville.street_name_mapping",
    ]

    FILE_LIST = [f"{context.config('data_path')}/{context.config(filename)}" for filename in filenames]

    for FILE in FILE_LIST:
        if not os.path.exists(FILE):
            raise RuntimeError(f"HTS trip validation data is not available at location {FILE}")

    size_list = [os.path.getsize(FILE) for FILE in FILE_LIST]

    return size_list
