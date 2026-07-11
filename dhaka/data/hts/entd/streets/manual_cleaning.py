from tqdm import tqdm
import pandas as pd
import numpy as np
import data.hts.hts as hts
from geopy.distance import geodesic
import geopy
import time
import os
from shapely import Point

"""
This stage resolves some addresses by manually assigned geographic coordinates.
"""


def configure(context):
    context.config("data_path")
    context.config("seville.street_data_2", "street_data/street_data_2.csv")


MUNICIPALITIES= {
    "Paradas": (37.28983570971475, -5.4976628439230835),
    "La Algaba": (37.46139359455287, -6.012231250102343),
    "Carmona": (37.4707779893395, -5.644710440930651),
    "Tomares": (37.37367686919577, -6.04630100056516),
    "Sevilla": (37.392783382893874, -5.987856200329914),
    "La Rinconada": (37.486182193139285, -5.98158989448696),
    "Burguillos": (37.58535602065082, -5.967895113856253),
    "Olivares": (37.41888682807106, -6.157682234637542),
    "Marchena": (37.32805716920212, -5.4168645472017),
    "Herrera": (37.362042687398805, -4.848296470385442),
    }

def execute(context):
    print("Replacing missing values with manually aquired locations.")

    CSV_PATH = f"{context.config('data_path')}/{context.config('seville.street_data_2')}"
    df_streets = pd.read_csv(CSV_PATH, sep='\t', dtype={"location":str})


    condition = df_streets["municipality"].isin(MUNICIPALITIES.keys())
    df_streets.loc[condition, "location"] = df_streets.loc[condition, "municipality"].map(MUNICIPALITIES)

    print("Checking if locations with missing coordinates exist.")
    df_streets = df_streets[df_streets["municipality"] != "Otros"] # Filter out unknown
    assert df_streets[df_streets["location"] == "(None, None)"].empty


    return df_streets

def validate(context):
    filenames = [
        "seville.street_data_2",
    ]

    FILE_LIST = [f"{context.config('data_path')}/{context.config(filename)}" for filename in filenames]

    for FILE in FILE_LIST:
        if not os.path.exists(FILE):
            raise RuntimeError(f"HTS trip validation data is not available at location {FILE}")

    size_list = [os.path.getsize(FILE) for FILE in FILE_LIST]

    return size_list
