"""
The codes are hierarchically structured as follows:

- departement_id: the whole study area covered by dhaka_ward.shp (single fixed value "1")
- commune_id: ward number (2-digit, zero-padded), parsed from the ward shapefile's
  `shapeName` field ("Ward No-NN")
- iris_id: fake, one per commune_id (Bangladesh has no equivalent of the French IRIS)
- region_id: not used, empty string
"""

def configure(context):
    context.stage("dhaka.data.spatial.iris")

def execute(context):

    # Load codes
    df_codes = context.stage("dhaka.data.spatial.iris")

    return df_codes[["region_id", "departement_id", "commune_id", "iris_id"]]
