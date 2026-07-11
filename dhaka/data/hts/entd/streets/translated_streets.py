from tqdm import tqdm
import pandas as pd
import numpy as np
import data.hts.hts as hts
from geopy.distance import geodesic
import geopy
import time

# IMPORTANT! WHEN DEBUGGING LIMIT REQUEST RATE and size of input dataframe
"""
This stage resolves addresses using Nominatim, an online OSM API. This stage
is not used every time during the pipeline due to the time it takes to resolve
all the queries (several hours).
"""


def configure(context):
    context.config("data_path")
    context.stage("seville.data.hts.entd.raw_streets")
    context.config("seville.street_data", "street_data/street_data.csv")

def execute(context):

    df_streets = context.stage("seville.data.hts.entd.raw_streets")


    chunk_size = 20
    chunks = np.array_split(df_streets, len(df_streets) // chunk_size + 1)
    TARGET_PATH = f"{context.config('data_path')}/{context.config('seville.street_data')}"
    df_streets.head(0).to_csv(TARGET_PATH, sep='\t', index=False)

    print("Translating streets to longitude/latitude format:")
    counter = 0
    for chunk in tqdm(chunks, desc="Processing Chunks"):
        counter+=1
        print(f"Chunk number: {counter}")
        print(f"Records processed: {counter * chunk_size}")
        # Apply the geocode_street function on the current chunk
        chunk['street_location'] = chunk.apply(geocode_street, axis=1)
        
        # Append the chunk to the file
        chunk.to_csv(TARGET_PATH, mode='a', sep='\t', header=False, index=False)


    return df_streets

def geocode_street(row):
    full_street_name = str(row.street)
    # If street is unknown, return (None, None)
    if str(row.street) == "LA CALLE NO APARECE EN EL LISTADO":
        return None, None


    if full_street_name.endswith("(CALLE)"):
        street_name = full_street_name.replace("  (CALLE)", "")
        street_name = "C. " + street_name
    elif full_street_name.endswith("(PLAZA)"):
        street_name = full_street_name.replace("  (PLAZA)", "")
        street_name = "Pl. " + street_name
    elif full_street_name.endswith("(CLLON)"):
        street_name = full_street_name.replace("  (CLLON)", "")
        street_name = "Cjón. " + street_name
    elif full_street_name.endswith("(AVDA)"):
        street_name = full_street_name.replace("  (AVDA)", "")
        street_name = "Av. " + street_name
    elif full_street_name.endswith("(PSAJE)"):
        street_name = full_street_name.replace("  (PSAJE)", "")
        street_name = "Pje. " + street_name
    else:
        street_name = full_street_name


    geolocator = geopy.geocoders.Nominatim(user_agent="seville_pipeline")
    while(1):
        try:
            # Geocode the address, appending 'Seville' to ensure we are searching in Seville
            request = f"{street_name}, {row.zone}, {row.municipality}, España"
            location = geolocator.geocode(request, timeout=10)
            if location:
                return location.latitude, location.longitude
            time.sleep(0.5)
            request = f"{street_name}, {row.municipality}, España"
            location = geolocator.geocode(request, timeout=10)
            if location:
                return location.latitude, location.longitude
            else:
                print(f"Failed: {request}")
                return None, None
        except geopy.exc.GeocoderTimedOut:
            print(f"Geocoding request timed out for {street_name}. Retrying...")
            time.sleep(2)  # Delay to avoid overloading the service
            continue # Retry the geocoding request

