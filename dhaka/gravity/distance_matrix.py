from tqdm import tqdm
import pandas as pd
import numpy as np
import numpy.linalg as la

"""
Generates a distance matrix for the Seville's census sections.
"""

def configure(context):
    context.stage("seville.data.spatial.iris")

def execute(context):
    # One municipality per "IRIS"
    df_census_sections = context.stage("seville.data.spatial.iris")
    #assert not df_census_sections.isnull().values.any(), "Df muni contains NaNs!"
    municipalities = df_census_sections["commune_id"].values
        
    # Initialize matrix to zero
    distance_matrix = np.ones((len(municipalities), len(municipalities)))
    
    # Convert locations to (N,2)-array
    locations = np.array([
        df_census_sections["geometry"].centroid.x,
        df_census_sections["geometry"].centroid.y
    ]).T
    
    # Calculate Euclidean distances per row
    for k in range(len(locations)):
        distance_matrix[k,:] = la.norm(locations[k] - locations, axis = 1)
    
    # Convert to km
    distance_matrix *= 1e-3
    
    # Formatting into a data frame
    df_distances = pd.DataFrame({ "distance_km": distance_matrix.reshape(-1) }, index = pd.MultiIndex.from_product([
    municipalities, municipalities
    ], names = ["origin_id", "destination_id"])).reset_index()
   
    assert not df_distances.isnull().values.any(), "Df distances contains NaNs!"
    
    return df_distances
