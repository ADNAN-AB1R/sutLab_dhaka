"""
Generates the IRIS zoning system that is not used in Bangladesh. Instead, we create
one fake IRIS per ward. See the `codes` stage for more information.
"""

def configure(context):
    context.stage("dhaka.data.spatial.raw")

def execute(context):

    # Load codes
    df_codes = context.stage("dhaka.data.spatial.raw")

    # Clean up identifiers
    df_codes["commune_id"] = df_codes["commune_id"].astype(str).astype("category")

    # No region id, single department (the study area covered by the ward shapefile)
    df_codes["region_id"] = ""
    df_codes["region_id"] = df_codes["region_id"].astype("category")
    df_codes["departement_id"] = "1"
    df_codes["departement_id"] = df_codes["departement_id"].astype("category")

    # Fake IRIS
    df_codes["iris_id"] = df_codes["commune_id"].astype(str) + "0000"
    df_codes["iris_id"] = df_codes["iris_id"].astype("category")

    return df_codes[["region_id", "departement_id", "commune_id", "iris_id", "geometry"]]
