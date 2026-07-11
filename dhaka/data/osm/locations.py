"""
Work/education/shop/leisure candidate locations for Dhaka.

Unlike Seville (pyrosm extraction from a regional .osm.pbf), this sources the
typed subset of dhaka.data.buildings (the Geofabrik buildings file's `type`
tag), since no Bangladesh .osm.pbf is available yet. Once one is provided
(dhaka.osm_path), this stage should be extended to merge in point-amenity data
(standalone shops/restaurants/etc not captured as building polygons) for
better shop/leisure coverage.
"""

def configure(context):
    context.stage("dhaka.data.buildings")

def execute(context):
    df = context.stage("dhaka.data.buildings")
    df = df[df["location_type"].notna()].copy()

    df["floors"] = df["floors"].fillna(1).astype(int)

    return df[[
        "geometry", "building", "amenity", "area", "floors",
        "commune_id", "iris_id", "location_type",
    ]]
