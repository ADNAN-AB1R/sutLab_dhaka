import gzip
import math
import os
import xml.etree.ElementTree as ET
from typing import Tuple

import contextily as cx
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.markers import MarkerStyle
from matplotlib.patches import Patch
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import pdist
from shapely.geometry import Polygon

from analysis_dhaka.marginals import AGE_CLASS_BOUNDS, AGE_CLASS_LABELS

# Coordinate Reference Systems
SOURCE_EPSG = 25832  # ETRS89 / UTM zone 32N (meters) - analysis CRS
BASEMAP_EPSG = 3857  # Web Mercator for contextily - visualization CRS

# Map styling
MAP_CMAP = "magma_r"  # high contrast on OSM light base
MAP_ALPHA = 0.7  # slightly transparent but readable

# Grid configurations
# GRID_SHAPES = ["square", "hex", "neighborhoods"]  # all grid types to generate
GRID_SHAPES = ["hex", "neighborhoods"]
CELL_SIZE_M = [500.0]  # grid cell sizes in meters (short diagonal for hex)

# Analysis thresholds
PT_STOP_DISTANCE_M = [200.0]  # thresholds for "within" stop distance

# Walk/PT mode definitions
WALK_MODES = ("walk",)
PT_MODES = ("pt",)

# Plot styling
FIGURE_SIZE_MAP = (10, 10)
FIGURE_SIZE_HIST = (12, 5)
FIGURE_SIZE_BOX = (8, 6)
PLOT_DPI = 200

# Colors for boxplots
ACCESS_COLOR = "#3182bd"
EGRESS_COLOR = "#31a354"

# PT Network overlay styling
PT_NETWORK_COLOR = "#0066CC"  # Bright blue for PT lines
PT_NETWORK_LINEWIDTH = 2.0  # Thicker lines for visibility
PT_NETWORK_ALPHA = 0.8  # Semi-transparent
PT_STOP_COLOR = "#00FF00"  # Bright green for stops (not in magma colormap)
PT_STOP_SIZE = 25  # Larger stops
PT_STOP_EDGE_COLOR = "black"  # Black edge for contrast
PT_STOP_EDGE_WIDTH = 0.8  # Edge width
PT_STOP_ALPHA = 0.9  # Nearly opaque

# Output file prefix
OUTPUT_PREFIX = "accessibility"


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================


def compute_age_class(ages: pd.Series) -> pd.Series:
    """
    Compute age classes from numeric ages on-demand.
    Returns a categorical series with proper ordering.
    """
    try:
        # Prefer central definition if importable
        bounds = list(AGE_CLASS_BOUNDS)
        labels = list(AGE_CLASS_LABELS)
    except Exception:
        # Fallback to common 6-class scheme
        bounds = [14, 29, 44, 59, 74, float("inf")]
        labels = ["<15", "15-29", "30-44", "45-59", "60-74", "75+"]

    ages_array = ages.to_numpy()
    idx = np.digitize(ages_array, bounds, right=True)

    # Map indices to labels safely
    mapped = []
    for i, a in zip(idx, ages_array):
        if not np.isfinite(a):
            mapped.append(np.nan)
        else:
            j = int(i)
            if j < 0:
                j = 0
            if j >= len(labels):
                j = len(labels) - 1
            mapped.append(labels[j])

    return pd.Series(
        pd.Categorical(mapped, categories=labels, ordered=True), index=ages.index
    )


def ensure_source_crs(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Ensure GeoDataFrame is in SOURCE_EPSG coordinate system."""
    if gdf.crs is None:
        gdf = gdf.set_crs(epsg=SOURCE_EPSG, allow_override=True)
    elif gdf.crs.to_epsg() != SOURCE_EPSG:
        gdf = gdf.to_crs(epsg=SOURCE_EPSG)
    return gdf


def project_to_visualization_crs(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Project GeoDataFrame to visualization CRS for plotting."""
    gdf = ensure_source_crs(gdf)
    return gdf.to_crs(epsg=BASEMAP_EPSG)


def aggregate_to_grid(
    df_points: pd.DataFrame,
    agg_column: str,
    agg_method: str,
    cell_size: float,
    grid_shape: str = "square",
    neighborhoods_gdf: gpd.GeoDataFrame | None = None,
    data_path: str | None = None,
) -> Tuple[pd.DataFrame, gpd.GeoDataFrame]:
    """
    Generic function to aggregate point data to a spatial grid.

    Parameters:
        df_points: DataFrame with x, y coordinates and data to aggregate
        agg_column: column name to aggregate
        agg_method: aggregation method - one of:
            - 'count': count non-null values
            - 'sum': sum values (useful for boolean flags)
            - 'mean': mean of values
            - 'median': median of values
            - 'share': compute share (sum/count for boolean columns)
        cell_size: grid cell size in meters (ignored for neighborhoods)
        grid_shape: "square", "hex", or "neighborhoods"
        neighborhoods_gdf: pre-loaded neighborhoods GeoDataFrame
        data_path: config data_path (required for neighborhoods if gdf not provided)

    Returns:
        (aggregated_df, grid_gdf): aggregated statistics and grid geometries
    """
    # Clean input data
    clean = df_points.copy()
    required_cols = ["x", "y", agg_column]
    clean = clean.dropna(subset=required_cols)
    clean = clean[np.isfinite(clean["x"]) & np.isfinite(clean["y"])]

    if agg_method in ["mean", "median", "sum"]:
        clean = clean[np.isfinite(clean[agg_column])]

    if clean.empty:
        return pd.DataFrame(), gpd.GeoDataFrame(
            columns=["geometry"], crs=f"EPSG:{SOURCE_EPSG}"
        )

    if grid_shape == "neighborhoods":
        return _aggregate_to_neighborhoods(
            clean, agg_column, agg_method, neighborhoods_gdf, data_path
        )
    elif grid_shape == "square":
        return _aggregate_to_square_grid(clean, agg_column, agg_method, cell_size)
    elif grid_shape == "hex":
        return _aggregate_to_hex_grid(clean, agg_column, agg_method, cell_size)
    else:
        raise ValueError(f"Unknown grid_shape: {grid_shape}")


def _aggregate_to_neighborhoods(
    df_points: pd.DataFrame,
    agg_column: str,
    agg_method: str,
    neighborhoods_gdf: gpd.GeoDataFrame | None,
    data_path: str | None,
) -> Tuple[pd.DataFrame, gpd.GeoDataFrame]:
    """Aggregate points to neighborhood boundaries."""
    if neighborhoods_gdf is None:
        if data_path is None:
            raise ValueError(
                "data_path required for neighborhoods when gdf not provided"
            )
        neighborhoods_gdf = _load_seville_neighborhoods(data_path)

    # Create points GeoDataFrame in source CRS
    pts_gdf = gpd.GeoDataFrame(
        df_points.copy(),
        geometry=gpd.points_from_xy(df_points["x"], df_points["y"]),
        crs=f"EPSG:{SOURCE_EPSG}",
    )

    # Spatial join
    join = gpd.sjoin(
        pts_gdf,
        neighborhoods_gdf[["neighborhood_id", "neighborhood_name", "geometry"]],
        how="left",
        predicate="within",
    )

    # Aggregate
    if agg_method == "count":
        agg = join.groupby(
            ["neighborhood_id", "neighborhood_name"], as_index=False
        ).agg(n_points=(agg_column, "count"), value=(agg_column, "count"))
    elif agg_method == "sum":
        agg = join.groupby(
            ["neighborhood_id", "neighborhood_name"], as_index=False
        ).agg(n_points=(agg_column, "count"), value=(agg_column, "sum"))
    elif agg_method == "mean":
        agg = join.groupby(
            ["neighborhood_id", "neighborhood_name"], as_index=False
        ).agg(n_points=(agg_column, "count"), value=(agg_column, "mean"))
    elif agg_method == "median":
        agg = join.groupby(
            ["neighborhood_id", "neighborhood_name"], as_index=False
        ).agg(n_points=(agg_column, "count"), value=(agg_column, "median"))
    elif agg_method == "share":
        agg = join.groupby(
            ["neighborhood_id", "neighborhood_name"], as_index=False
        ).agg(n_points=(agg_column, "count"), sum_value=(agg_column, "sum"))
        agg["value"] = agg["sum_value"] / agg["n_points"]
        agg = agg.drop(columns=["sum_value"])
    else:
        raise ValueError(f"Unknown agg_method: {agg_method}")

    # Merge with geometries
    grid_gdf = neighborhoods_gdf.merge(
        agg, on=["neighborhood_id", "neighborhood_name"], how="inner"
    )

    return agg, grid_gdf


def _aggregate_to_square_grid(
    df_points: pd.DataFrame, agg_column: str, agg_method: str, cell_size: float
) -> Tuple[pd.DataFrame, gpd.GeoDataFrame]:
    """Aggregate points to square grid cells."""
    minx, miny = df_points["x"].min(), df_points["y"].min()

    # Compute grid indices
    gx = ((df_points["x"] - minx) // cell_size).astype(int)
    gy = ((df_points["y"] - miny) // cell_size).astype(int)
    df = df_points.assign(gx=gx, gy=gy)

    # Aggregate
    if agg_method == "count":
        grp = df.groupby(["gx", "gy"], as_index=False).agg(
            n_points=(agg_column, "count"), value=(agg_column, "count")
        )
    elif agg_method == "sum":
        grp = df.groupby(["gx", "gy"], as_index=False).agg(
            n_points=(agg_column, "count"), value=(agg_column, "sum")
        )
    elif agg_method == "mean":
        grp = df.groupby(["gx", "gy"], as_index=False).agg(
            n_points=(agg_column, "count"), value=(agg_column, "mean")
        )
    elif agg_method == "median":
        grp = df.groupby(["gx", "gy"], as_index=False).agg(
            n_points=(agg_column, "count"), value=(agg_column, "median")
        )
    elif agg_method == "share":
        grp = df.groupby(["gx", "gy"], as_index=False).agg(
            n_points=(agg_column, "count"), sum_value=(agg_column, "sum")
        )
        grp["value"] = grp["sum_value"] / grp["n_points"]
        grp = grp.drop(columns=["sum_value"])
    else:
        raise ValueError(f"Unknown agg_method: {agg_method}")

    # Create geometries
    polys = []
    for _, row in grp.iterrows():
        x0 = minx + row["gx"] * cell_size
        y0 = miny + row["gy"] * cell_size
        geom = Polygon(
            [
                (x0, y0),
                (x0 + cell_size, y0),
                (x0 + cell_size, y0 + cell_size),
                (x0, y0 + cell_size),
            ]
        )
        polys.append(geom)

    grid_gdf = gpd.GeoDataFrame(grp.copy(), geometry=polys, crs=f"EPSG:{SOURCE_EPSG}")

    return grp, grid_gdf


def _hexagon(center_x: float, center_y: float, r: float) -> Polygon:
    """Create a flat-top hexagon polygon centered at (center_x, center_y) with radius r."""
    angles = [0, 60, 120, 180, 240, 300]
    pts = []
    for a in angles:
        rad = math.radians(a)
        pts.append((center_x + r * math.cos(rad), center_y + r * math.sin(rad)))
    return Polygon(pts)


def _aggregate_to_hex_grid(
    df_points: pd.DataFrame, agg_column: str, agg_method: str, cell_size: float
) -> Tuple[pd.DataFrame, gpd.GeoDataFrame]:
    """Aggregate points to hexagonal grid cells."""
    # Create points GeoDataFrame
    pts_gdf = gpd.GeoDataFrame(
        df_points.copy(),
        geometry=gpd.points_from_xy(df_points["x"], df_points["y"]),
        crs=f"EPSG:{SOURCE_EPSG}",
    )

    # Generate hex grid
    minx, miny, maxx, maxy = pts_gdf.total_bounds
    hex_grid = _generate_hex_grid(minx, miny, maxx, maxy, cell_size)

    # Spatial join
    join = gpd.sjoin(
        pts_gdf, hex_grid[["cell_id", "geometry"]], how="left", predicate="within"
    )

    # Aggregate
    if agg_method == "count":
        agg = join.groupby("cell_id", as_index=False).agg(
            n_points=(agg_column, "count"), value=(agg_column, "count")
        )
    elif agg_method == "sum":
        agg = join.groupby("cell_id", as_index=False).agg(
            n_points=(agg_column, "count"), value=(agg_column, "sum")
        )
    elif agg_method == "mean":
        agg = join.groupby("cell_id", as_index=False).agg(
            n_points=(agg_column, "count"), value=(agg_column, "mean")
        )
    elif agg_method == "median":
        agg = join.groupby("cell_id", as_index=False).agg(
            n_points=(agg_column, "count"), value=(agg_column, "median")
        )
    elif agg_method == "share":
        agg = join.groupby("cell_id", as_index=False).agg(
            n_points=(agg_column, "count"), sum_value=(agg_column, "sum")
        )
        agg["value"] = agg["sum_value"] / agg["n_points"]
        agg = agg.drop(columns=["sum_value"])
    else:
        raise ValueError(f"Unknown agg_method: {agg_method}")

    # Merge with grid geometries
    grid_gdf = hex_grid.merge(agg, on="cell_id", how="inner")

    return agg, grid_gdf


def _generate_hex_grid(
    minx: float, miny: float, maxx: float, maxy: float, cell_size: float
) -> gpd.GeoDataFrame:
    """
    Generate a flat-top hex grid covering the bbox.
    cell_size is the short diagonal (flat-to-flat distance).
    """
    R = cell_size / math.sqrt(3.0)  # circumradius
    dx = 1.5 * R  # horizontal step between centers
    dy = cell_size  # vertical step equals short diagonal

    x_start = minx - cell_size
    y_start = miny - cell_size

    geoms = []
    cell_ids = []
    row = 0
    y = y_start

    while y <= maxy + cell_size:
        x_offset = 0.75 * R if (row % 2 == 1) else 0.0
        x = x_start + x_offset

        while x <= maxx + cell_size:
            poly = _hexagon(x, y, R)
            # Filter by bbox intersection
            if (
                poly.bounds[2] >= minx
                and poly.bounds[0] <= maxx
                and poly.bounds[3] >= miny
                and poly.bounds[1] <= maxy
            ):
                cell_ids.append(len(cell_ids))
                geoms.append(poly)
            x += dx
        row += 1
        y += dy

    return gpd.GeoDataFrame(
        {"cell_id": cell_ids}, geometry=geoms, crs=f"EPSG:{SOURCE_EPSG}"
    )


def plot_choropleth_map(
    gdf: gpd.GeoDataFrame,
    value_column: str,
    title: str,
    output_path: str,
    vmin: float | None = None,
    vmax: float | None = None,
    cmap: str = MAP_CMAP,
    alpha: float = MAP_ALPHA,
    figsize: tuple = FIGURE_SIZE_MAP,
    clip_quantile: float | None = None,
    show_pt_overlay: bool = False,
    stops_df: pd.DataFrame | None = None,
    nodes_df: pd.DataFrame | None = None,
    pt_links_df: pd.DataFrame | None = None,
) -> str:
    """
    Generic function to create choropleth maps with optional PT network overlay.

    Parameters:
        gdf: GeoDataFrame with data to plot (will be projected to visualization CRS)
        value_column: column name to visualize
        title: plot title
        output_path: full path to save the plot
        vmin, vmax: color scale limits (if None, uses data min/max)
        cmap: colormap name
        alpha: transparency
        figsize: figure size tuple
        clip_quantile: if provided, clips vmax to this quantile of the data
        show_pt_overlay: whether to add PT network and stops overlay
        stops_df: PT stops DataFrame (required if show_pt_overlay=True)
        nodes_df: network nodes DataFrame (required if show_pt_overlay=True)
        pt_links_df: PT links DataFrame (required if show_pt_overlay=True)
        cluster_stops: whether to cluster nearby stops (default: True)
        cluster_distance: distance in meters to cluster stops (default: 50m)

    Returns:
        output_path if successful, empty string if failed
    """
    if gdf is None or len(gdf) == 0 or value_column not in gdf.columns:
        return ""

    # Project to visualization CRS
    gdf_viz = project_to_visualization_crs(gdf)

    # Handle color scale limits
    if clip_quantile is not None and vmax is None:
        q_val = gdf[value_column].quantile(clip_quantile)
        if pd.notna(q_val):
            vmax = float(q_val)

    fig, ax = plt.subplots(figsize=figsize)

    # Plot choropleth (without legend, we'll add custom colorbar)
    im = gdf_viz.plot(
        column=value_column,
        cmap=cmap,
        vmin=vmin,
        vmax=vmax,
        linewidth=0.0,
        ax=ax,
        legend=False,
        alpha=alpha,
    )

    # Add horizontal colorbar at the bottom
    cbar = fig.colorbar(
        im.get_children()[0],
        ax=ax,
        orientation="horizontal",
        shrink=0.6,
        pad=0.1,
        aspect=30,
    )

    ax.set_aspect("equal", adjustable="box")
    ax.set_title(title)
    ax.set_xlabel("x")
    ax.set_ylabel("y")

    # Add basemap
    _add_basemap(ax, gdf_viz)

    # Optionally add PT network overlay
    if show_pt_overlay and stops_df is not None:
        pt_handles, pt_labels = add_pt_network_overlay(
            ax,
            stops_df=stops_df,
            nodes_df=nodes_df,
            pt_links_df=pt_links_df,
            show_network=(nodes_df is not None and pt_links_df is not None),
            show_stops=True,
            zorder_network=10,
            zorder_stops=11,
            add_to_legend=True,
        )

        # Add legend if PT overlay elements were added
        if pt_handles:
            ax.legend(
                handles=pt_handles,
                labels=pt_labels,
                loc="upper right",
                fontsize=9,
                framealpha=0.95,
                edgecolor="black",
            )

    plt.tight_layout()
    fig.savefig(output_path, dpi=PLOT_DPI)
    plt.close(fig)

    return output_path


def _add_basemap(
    ax, gdf_3857: gpd.GeoDataFrame, source=None, bounds: tuple | None = None
):
    """Add a contextily basemap under current plot."""
    if bounds is not None:
        xmin, ymin, xmax, ymax = bounds
    else:
        xmin, ymin, xmax, ymax = gdf_3857.total_bounds

    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)

    crs_str = f"EPSG:{BASEMAP_EPSG}"
    if source is None:
        cx.add_basemap(ax, crs=crs_str, attribution_size=6)
    else:
        cx.add_basemap(ax, source=source, crs=crs_str, attribution_size=6)


def _cluster_nearby_stops(
    stops_df: pd.DataFrame, cluster_distance: float = 50.0
) -> pd.DataFrame:
    """
    Cluster nearby PT stops into single representative points.

    This reduces visual clutter from overlapping stop markers by combining
    stops that are within cluster_distance meters of each other.

    Parameters:
        stops_df: DataFrame with PT stops (columns: x, y) in SOURCE_EPSG (meters)
        cluster_distance: maximum distance in meters to cluster stops together

    Returns:
        DataFrame with clustered stops (x, y coordinates are cluster centroids)
    """
    if stops_df is None or stops_df.empty:
        return stops_df

    # Extract coordinates
    coords = stops_df[["x", "y"]].values

    # Single stop case
    if len(coords) == 1:
        return stops_df

    # Compute pairwise distances
    distances = pdist(coords, metric="euclidean")

    # Perform hierarchical clustering
    linkage_matrix = linkage(distances, method="complete")

    # Cut dendrogram at cluster_distance to get cluster labels
    cluster_labels = fcluster(linkage_matrix, cluster_distance, criterion="distance")

    # Compute cluster centroids
    clustered_stops = []
    for cluster_id in np.unique(cluster_labels):
        cluster_mask = cluster_labels == cluster_id
        cluster_coords = coords[cluster_mask]

        # Use centroid of cluster
        centroid_x = cluster_coords[:, 0].mean()
        centroid_y = cluster_coords[:, 1].mean()

        clustered_stops.append({"x": centroid_x, "y": centroid_y})

    return pd.DataFrame(clustered_stops)


def add_pt_network_overlay(
    ax,
    stops_df: pd.DataFrame | None = None,
    nodes_df: pd.DataFrame | None = None,
    pt_links_df: pd.DataFrame | None = None,
    show_network: bool = True,
    show_stops: bool = True,
    cluster_stops: bool = True,
    cluster_distance: float = 100.0,
    network_color: str = PT_NETWORK_COLOR,
    network_linewidth: float = PT_NETWORK_LINEWIDTH,
    network_alpha: float = PT_NETWORK_ALPHA,
    stop_color: str = PT_STOP_COLOR,
    stop_size: float = PT_STOP_SIZE,
    stop_alpha: float = PT_STOP_ALPHA,
    stop_edge_color: str = PT_STOP_EDGE_COLOR,
    stop_edge_width: float = PT_STOP_EDGE_WIDTH,
    zorder_network: int = 10,
    zorder_stops: int = 11,
    add_to_legend: bool = True,
) -> tuple[list, list]:
    """
    Add PT network overlay to an existing matplotlib axis.

    This is a modular function that can be called on any map plot to add
    PT network lines and stops as an overlay.

    Parameters:
        ax: matplotlib axis to add overlay to
        stops_df: DataFrame with PT stops (columns: x, y) in SOURCE_EPSG
        nodes_df: DataFrame with network nodes (columns: node_id, x, y) in SOURCE_EPSG
        pt_links_df: DataFrame with PT links (columns: from_node, to_node) in SOURCE_EPSG
        show_network: whether to show PT network lines
        show_stops: whether to show PT stops
        cluster_stops: whether to cluster nearby stops to reduce visual clutter
        cluster_distance: distance in meters to cluster stops (default: 50m)
        network_color: color for PT network lines
        network_linewidth: line width for PT network
        network_alpha: transparency for PT network
        stop_color: color for PT stops
        stop_size: size of PT stop markers
        stop_alpha: transparency for PT stops
        stop_edge_color: edge color for PT stops
        stop_edge_width: edge width for PT stops
        zorder_network: z-order for network lines (higher = on top)
        zorder_stops: z-order for stops (higher = on top)
        add_to_legend: whether to return legend handles

    Returns:
        (handles, labels): matplotlib legend handles and labels for the PT overlay

    Example usage:
        fig, ax = plt.subplots()
        # ... plot your data ...
        handles, labels = add_pt_network_overlay(ax, stops_df, nodes_df, pt_links_df)
        ax.legend(handles, labels)
    """
    handles = []
    labels = []

    # Layer 1: PT network links
    if (
        show_network
        and pt_links_df is not None
        and nodes_df is not None
        and not pt_links_df.empty
        and not nodes_df.empty
    ):
        # Merge links with node coordinates
        pt_links_with_coords = pt_links_df.merge(
            nodes_df.rename(
                columns={"node_id": "from_node", "x": "from_x", "y": "from_y"}
            ),
            on="from_node",
            how="left",
        ).merge(
            nodes_df.rename(columns={"node_id": "to_node", "x": "to_x", "y": "to_y"}),
            on="to_node",
            how="left",
        )

        # Filter out links with missing coordinates
        pt_links_with_coords = pt_links_with_coords.dropna(
            subset=["from_x", "from_y", "to_x", "to_y"]
        )

        if not pt_links_with_coords.empty:
            # Create LineString geometries for each PT link
            from shapely.geometry import LineString

            pt_link_geoms = [
                LineString([(row["from_x"], row["from_y"]), (row["to_x"], row["to_y"])])
                for _, row in pt_links_with_coords.iterrows()
            ]

            pt_links_gdf = gpd.GeoDataFrame(
                pt_links_with_coords, geometry=pt_link_geoms, crs=f"EPSG:{SOURCE_EPSG}"
            )
            pt_links_viz = project_to_visualization_crs(pt_links_gdf)

            # Plot PT network as lines
            pt_links_viz.plot(
                ax=ax,
                color=network_color,
                linewidth=network_linewidth,
                alpha=network_alpha,
                zorder=zorder_network,
            )

            if add_to_legend:
                # Create custom legend handle for network
                from matplotlib.lines import Line2D

                network_handle = Line2D(
                    [0],
                    [0],
                    color=network_color,
                    linewidth=network_linewidth,
                    alpha=network_alpha,
                    label="PT network",
                )
                handles.append(network_handle)
                labels.append("PT network")

    # Layer 2: PT stops
    if show_stops and stops_df is not None and not stops_df.empty:
        # Cluster nearby stops if requested
        if cluster_stops:
            stops_to_plot = _cluster_nearby_stops(stops_df, cluster_distance)
        else:
            stops_to_plot = stops_df

        stops_gdf = gpd.GeoDataFrame(
            stops_to_plot.copy(),
            geometry=gpd.points_from_xy(stops_to_plot["x"], stops_to_plot["y"]),
            crs=f"EPSG:{SOURCE_EPSG}",
        )
        stops_viz = project_to_visualization_crs(stops_gdf)

        ax.scatter(
            stops_viz.geometry.x,
            stops_viz.geometry.y,
            s=stop_size,
            c=stop_color,
            alpha=stop_alpha,
            marker="o",
            edgecolors=stop_edge_color,
            linewidths=stop_edge_width,
            zorder=zorder_stops,
        )

        if add_to_legend:
            # Create custom legend handle for stops
            from matplotlib.lines import Line2D

            stop_handle = Line2D(
                [0],
                [0],
                marker="o",
                color="w",
                markerfacecolor=stop_color,
                markersize=8,
                markeredgecolor=stop_edge_color,
                markeredgewidth=stop_edge_width,
                alpha=stop_alpha,
                label="PT stops",
                linestyle="None",
            )
            handles.append(stop_handle)
            labels.append("PT stops")

    return handles, labels


def _load_seville_neighborhoods(data_path: str) -> gpd.GeoDataFrame:
    """
    Load seville Mikrobezirke (micro-districts) shapefile for neighborhood-based aggregation.
    Returns a GeoDataFrame with standardized columns and proper CRS.
    """

    shapefile_path = f"{data_path}/{'shapefiles/census_district_shapefiles/SECC_CE_20220101.shp'}"

    if not os.path.exists(shapefile_path):
        raise FileNotFoundError(
            f"seville census section shapefile not found: {shapefile_path}"
        )

    gdf = gpd.read_file(shapefile_path)[
            ["CUSEC", "CMUN", "CPRO", "geometry"]
        ]

    # Standardize columns
    gdf_census_sections = gdf_census_sections.rename(columns = {
        "CUSEC": "census_section_code",
        "CMUN": "municipality_code",
        "CPRO": "province_code",
    })

    # Ensure proper CRS
    neighborhoods = ensure_source_crs(neighborhoods)

    return neighborhoods


# =============================================================================
# DATA LOADING AND PROCESSING FUNCTIONS
# =============================================================================


def configure(context):
    """
    Analysis stage: accessibility proof of concept and mode share comparison.
    """

    # Required configs used for locating inputs/outputs
    output_path = context.config("output_path")
    context.config("output_prefix")
    context.config("analysis_path")
    context.config("data_path")
    sim_output_dir = context.config("simulation_output_dir")

    # Conditional dependency: only trigger matsim.output if simulation outputs are missing
    sim_dir = os.path.join(output_path, sim_output_dir)
    schedule_xml = os.path.join(sim_dir, "output_transitSchedule.xml.gz")
    persons_sim_csv_gz = os.path.join(sim_dir, "output_persons.csv.gz")

    if not (os.path.exists(schedule_xml) and os.path.exists(persons_sim_csv_gz)):
        context.stage("matsim.output")

    # Add HTS data dependency for mode share comparison
    context.stage("data.hts.entd.reweighted")


def _extract_stops_from_schedule(schedule_path: str) -> pd.DataFrame:
    """
    Parse MATSim transit schedule and return a DataFrame with PT stops.
    Columns: stop_id, x, y, linkRefId, name, stopAreaId
    """
    if not os.path.exists(schedule_path):
        raise FileNotFoundError(f"Transit schedule not found: {schedule_path}")

    if schedule_path.endswith(".gz"):
        with gzip.open(schedule_path, "rb") as f:
            tree = ET.parse(f)
    else:
        tree = ET.parse(schedule_path)
    root = tree.getroot()

    # stopFacility elements may have namespaces; detect by suffix
    stops = []
    for elem in root.iter():
        tag = elem.tag
        if isinstance(tag, str) and tag.endswith("stopFacility"):
            sid = elem.attrib.get("id")
            x = elem.attrib.get("x")
            y = elem.attrib.get("y")
            link_ref = elem.attrib.get("linkRefId")
            name = elem.attrib.get("name")
            stop_area = elem.attrib.get("stopAreaId")
            if sid is None or x is None or y is None:
                continue
            try:
                stops.append(
                    {
                        "stop_id": sid,
                        "x": float(x),
                        "y": float(y),
                        "linkRefId": link_ref,
                        "name": name,
                        "stopAreaId": stop_area,
                    }
                )
            except ValueError:
                continue
    return pd.DataFrame(stops)


def _extract_pt_network_from_matsim(
    network_path: str,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Parse MATSim network XML and extract nodes and PT links.
    Returns:
        nodes_df: DataFrame with columns: node_id, x, y
        pt_links_df: DataFrame with columns: link_id, from_node, to_node, modes

    PT links are identified by having modes that include transit-specific modes:
    - subway, tram, rail, bus (actual transit modes)
    - artificial (PT-specific artificial links created by pt2matsim)
    Excludes stopFacilityLink (these are zero-length links at stops)
    """
    if not os.path.exists(network_path):
        raise FileNotFoundError(f"Network file not found: {network_path}")

    if network_path.endswith(".gz"):
        with gzip.open(network_path, "rb") as f:
            tree = ET.parse(f)
    else:
        tree = ET.parse(network_path)
    root = tree.getroot()

    # Extract nodes
    nodes = []
    for elem in root.iter():
        tag = elem.tag
        if isinstance(tag, str) and tag.endswith("node"):
            node_id = elem.attrib.get("id")
            x = elem.attrib.get("x")
            y = elem.attrib.get("y")
            if node_id is None or x is None or y is None:
                continue
            try:
                nodes.append(
                    {
                        "node_id": node_id,
                        "x": float(x),
                        "y": float(y),
                    }
                )
            except ValueError:
                continue

    nodes_df = pd.DataFrame(nodes)

    # PT mode keywords to identify transit links
    PT_MODES = {"subway", "tram", "rail", "bus", "artificial"}
    EXCLUDE_MODES = {"stopFacilityLink"}  # zero-length links at stops

    # Extract PT links
    pt_links = []
    for elem in root.iter():
        tag = elem.tag
        if isinstance(tag, str) and tag.endswith("link"):
            link_id = elem.attrib.get("id")
            from_node = elem.attrib.get("from")
            to_node = elem.attrib.get("to")
            modes = elem.attrib.get("modes", "")

            if link_id is None or from_node is None or to_node is None:
                continue

            # Split modes by comma and check for PT modes
            mode_set = set(m.strip() for m in modes.split(","))

            # Include if it has any PT mode and doesn't have excluded modes
            has_pt_mode = bool(mode_set & PT_MODES)
            has_excluded = bool(mode_set & EXCLUDE_MODES)

            if has_pt_mode and not has_excluded:
                pt_links.append(
                    {
                        "link_id": link_id,
                        "from_node": from_node,
                        "to_node": to_node,
                        "modes": modes,
                    }
                )

    pt_links_df = pd.DataFrame(pt_links)

    return nodes_df, pt_links_df


def read_persons(sim_dir: str) -> pd.DataFrame:
    """
    Read MATSim output persons home coordinates (semicolon-delimited).
    Returns DataFrame with columns: person_id (if available), x, y
    """
    persons_path = os.path.join(sim_dir, "output_persons.csv.gz")
    if os.path.exists(persons_path):
        df = pd.read_csv(persons_path, sep=";")
        out = pd.DataFrame(
            {
                "person_id": df["person"],
                "x": pd.to_numeric(df["first_act_x"], errors="coerce"),
                "y": pd.to_numeric(df["first_act_y"], errors="coerce"),
            }
        )
        # Direct attributes available in output_persons.csv
        if "sex" in df.columns:
            out["sex"] = df["sex"]

        # Age (keep numeric only)
        if "age" in df.columns:
            out["age"] = pd.to_numeric(df["age"], errors="coerce")

        # Employment (boolean)
        if "employed" in df.columns:

            def _to_bool(v):
                if pd.isna(v):
                    return np.nan
                if isinstance(v, bool):
                    return v
                if isinstance(v, (int, float)):
                    try:
                        return bool(int(v))
                    except Exception:
                        return bool(v)
                s = str(v).strip().lower()
                return s in ("true", "t", "1", "yes", "y")

            out["employed"] = df["employed"].apply(_to_bool)

        # Household income (kept numeric; no derived quartiles to avoid clutter)
        if "householdIncome" in df.columns:
            out["householdIncome"] = pd.to_numeric(
                df["householdIncome"], errors="coerce"
            )

        # Additional optional demographics if available; keep names simple and normalized where possible
        optional_cols = [
            "income",
            "income_class",  # legacy names if present
            "employment",
            "has_license",
        ]
        for c in optional_cols:
            if c in df.columns and c not in out.columns:
                out[c] = df[c]
        return out

    raise FileNotFoundError("output_persons.csv.gz not found")


def nearest_stop_geopandas(
    persons_xy: pd.DataFrame, stops_df: pd.DataFrame
) -> pd.DataFrame:
    """
    Compute nearest stop for each person using GeoPandas sjoin_nearest.
    Returns a DataFrame with columns:
      - dist_to_nearest_stop_m
      - nearest_stop_id
    aligned with the input persons_xy rows.
    """
    # Build GeoDataFrames
    p_gdf = gpd.GeoDataFrame(
        persons_xy.copy(),
        geometry=gpd.points_from_xy(persons_xy["x"], persons_xy["y"]),
        crs=None,
    )
    s_gdf = gpd.GeoDataFrame(
        stops_df.copy(),
        geometry=gpd.points_from_xy(stops_df["x"], stops_df["y"]),
        crs=None,
    )
    if len(s_gdf) == 0:
        return pd.DataFrame(
            {
                "dist_to_nearest_stop_m": [math.nan] * len(p_gdf),
                "nearest_stop_id": [None] * len(p_gdf),
            }
        )

    joined = gpd.sjoin_nearest(
        p_gdf,
        s_gdf[["stop_id", "geometry"]],
        how="left",
        distance_col="dist_to_nearest_stop_m",
    )
    out = joined[["dist_to_nearest_stop_m", "stop_id"]].rename(
        columns={"stop_id": "nearest_stop_id"}
    )
    return out.reset_index(drop=True)


def plot_persons_distance_heatmap(
    persons_df: pd.DataFrame,
    stops_df: pd.DataFrame,
    analysis_path: str,
    nodes_df: pd.DataFrame | None = None,
    pt_links_df: pd.DataFrame | None = None,
):
    """
    Create a heatmap-like scatter plot: all persons colored by distance to nearest stop.
    Generates both with and without PT overlay versions.
    """
    # Drop missing
    df = persons_df.dropna(subset=["x", "y", "dist_to_nearest_stop_m"]).copy()
    if df.empty:
        return ""

    # clip at 95th percentile to improve readability
    vmax = df["dist_to_nearest_stop_m"].quantile(0.95)
    colors = (
        df["dist_to_nearest_stop_m"].clip(upper=float(vmax))
        if pd.notna(vmax)
        else df["dist_to_nearest_stop_m"]
    )

    # Build projected GeoDataFrames for plotting + basemap
    persons_gdf = gpd.GeoDataFrame(
        df.copy(), geometry=gpd.points_from_xy(df["x"], df["y"]), crs=None
    )
    persons_3857 = project_to_visualization_crs(persons_gdf)
    stops_3857 = gpd.GeoDataFrame(
        stops_df.copy(),
        geometry=gpd.points_from_xy(stops_df["x"], stops_df["y"]),
        crs=None,
    )
    stops_3857 = project_to_visualization_crs(stops_3857)

    # VERSION 1: With X markers for stops (original)
    fig, ax = plt.subplots(figsize=(10, 10))
    sc = ax.scatter(
        persons_3857.geometry.x,
        persons_3857.geometry.y,
        c=colors.loc[persons_3857.index],
        s=3,
        cmap=MAP_CMAP,
        alpha=MAP_ALPHA,
        linewidths=0,
        zorder=3,
    )
    # Overlay stops for reference
    if not stops_3857.empty:
        ax.scatter(
            stops_3857.geometry.x,
            stops_3857.geometry.y,
            s=6,
            c="k",
            alpha=0.6,
            marker=MarkerStyle("x"),
            label="PT stops",
            zorder=4,
        )

    ax.set_aspect("equal", adjustable="box")
    ax.set_title("Distance to nearest PT stop (m)")
    # Add horizontal colorbar at the bottom
    cbar = plt.colorbar(
        sc, ax=ax, orientation="horizontal", shrink=0.6, pad=0.1, aspect=30
    )
    ax.legend(loc="upper right")
    ax.set_xlabel("x")
    ax.set_ylabel("y")

    # Basemap
    _add_basemap(ax, persons_3857)

    png_path = os.path.join(
        analysis_path, f"{OUTPUT_PREFIX}_persons_distance_heatmap.png"
    )
    plt.tight_layout()
    fig.savefig(png_path, dpi=200)
    plt.close(fig)

    # VERSION 2: With PT overlay (no X markers)
    fig2, ax2 = plt.subplots(figsize=(10, 10))
    sc2 = ax2.scatter(
        persons_3857.geometry.x,
        persons_3857.geometry.y,
        c=colors.loc[persons_3857.index],
        s=3,
        cmap=MAP_CMAP,
        alpha=MAP_ALPHA,
        linewidths=0,
        zorder=3,
    )

    ax2.set_aspect("equal", adjustable="box")
    ax2.set_title("Distance to nearest PT stop (m)")

    # Basemap
    _add_basemap(ax2, persons_3857)

    # Add PT network overlay (replaces X markers)
    pt_handles = []
    pt_labels = []
    if stops_df is not None:
        pt_handles, pt_labels = add_pt_network_overlay(
            ax2,
            stops_df=stops_df,
            nodes_df=nodes_df,
            pt_links_df=pt_links_df,
            show_network=(nodes_df is not None and pt_links_df is not None),
            show_stops=True,
            cluster_stops=True,
            zorder_network=10,
            zorder_stops=11,
            add_to_legend=True,
        )

    # Add horizontal colorbar at the bottom
    cbar2 = plt.colorbar(
        sc2, ax=ax2, orientation="horizontal", shrink=0.6, pad=0.1, aspect=30
    )

    # Add PT legend if overlay was added
    if pt_handles:
        ax2.legend(
            handles=pt_handles,
            labels=pt_labels,
            loc="upper right",
            fontsize=9,
            framealpha=0.95,
            edgecolor="black",
        )

    ax2.set_xlabel("x")
    ax2.set_ylabel("y")

    png_path2 = os.path.join(
        analysis_path, f"{OUTPUT_PREFIX}_persons_distance_heatmap_with_pt_overlay.png"
    )
    plt.tight_layout()
    fig2.savefig(png_path2, dpi=200)
    plt.close(fig2)


def plot_pt_network_with_accessibility(
    persons_df: pd.DataFrame,
    stops_df: pd.DataFrame,
    nodes_df: pd.DataFrame,
    pt_links_df: pd.DataFrame,
    analysis_path: str,
):
    """
    Create a comprehensive PT accessibility map showing:
    - Person accessibility (heatmap background)
    - PT network links (lines) - using modular overlay
    - PT stops (points) - using modular overlay
    """
    print("  Creating PT network accessibility map...")

    # Drop missing person data
    df_persons = persons_df.dropna(subset=["x", "y", "dist_to_nearest_stop_m"]).copy()
    if df_persons.empty:
        print("    WARNING: No person data available for PT network map")
        return ""

    # Clip at 95th percentile for better color scale
    vmax = df_persons["dist_to_nearest_stop_m"].quantile(0.95)
    colors = (
        df_persons["dist_to_nearest_stop_m"].clip(upper=float(vmax))
        if pd.notna(vmax)
        else df_persons["dist_to_nearest_stop_m"]
    )

    # Project persons to visualization CRS
    persons_gdf = gpd.GeoDataFrame(
        df_persons.copy(),
        geometry=gpd.points_from_xy(df_persons["x"], df_persons["y"]),
        crs=f"EPSG:{SOURCE_EPSG}",
    )
    persons_viz = project_to_visualization_crs(persons_gdf)

    # Create figure
    fig, ax = plt.subplots(figsize=FIGURE_SIZE_MAP)

    # Add basemap first (bottom layer)
    _add_basemap(ax, persons_viz)

    # Layer 1: Person accessibility heatmap (background)
    sc = ax.scatter(
        persons_viz.geometry.x,
        persons_viz.geometry.y,
        c=colors.loc[persons_viz.index],
        s=2,
        cmap=MAP_CMAP,
        alpha=0.3,  # More transparent to see network clearly
        linewidths=0,
        zorder=2,
        label="Person accessibility",
    )

    # Layer 2-3: PT network and stops using modular overlay function
    pt_handles, pt_labels = add_pt_network_overlay(
        ax,
        stops_df=stops_df,
        nodes_df=nodes_df,
        pt_links_df=pt_links_df,
        show_network=True,
        show_stops=True,
        zorder_network=10,
        zorder_stops=11,
        add_to_legend=True,
    )

    print(f"    Plotted {len(pt_links_df) if not pt_links_df.empty else 0} PT links")
    print(f"    Plotted {len(stops_df) if not stops_df.empty else 0} PT stops")

    # Formatting
    ax.set_aspect("equal", adjustable="box")
    ax.set_title("PT Network and Accessibility", fontsize=14, fontweight="bold")
    ax.set_xlabel("x")
    ax.set_ylabel("y")

    # Add colorbar for accessibility
    cbar = plt.colorbar(
        sc, ax=ax, orientation="horizontal", shrink=0.6, pad=0.08, aspect=30
    )
    cbar.set_label("Distance to nearest PT stop (m)", fontsize=10)

    # Add legend with PT overlay elements
    if pt_handles:
        ax.legend(
            handles=pt_handles,
            labels=pt_labels,
            loc="upper right",
            fontsize=9,
            framealpha=0.95,
            edgecolor="black",
        )

    # Save figure
    png_path = os.path.join(
        analysis_path, f"{OUTPUT_PREFIX}_pt_network_with_accessibility.png"
    )
    plt.tight_layout()
    fig.savefig(png_path, dpi=PLOT_DPI)
    plt.close(fig)

    print(f"    Saved: {png_path}")
    return png_path


def _plot_hexbin_points(
    df: pd.DataFrame,
    value: np.ndarray,
    analysis_path: str,
    title: str,
    filename: str,
    cell_size: float,
    vmin: float | None = None,
    vmax: float | None = None,
    reduce_fn=np.mean,
    show_pt_overlay: bool = False,
    stops_df: pd.DataFrame | None = None,
    nodes_df: pd.DataFrame | None = None,
    pt_links_df: pd.DataFrame | None = None,
):
    """Generic hexbin plot from point coordinates using Matplotlib hexbin.
    - df must contain x, y in SOURCE_EPSG meters.
    - value is an array aligned with df rows.
    - gridsize is derived from bbox width divided by cell_size_m.
    - Saves to analysis_path/filename.
    """
    if df is None or df.empty:
        return ""
    pts_gdf = gpd.GeoDataFrame(
        df.copy(), geometry=gpd.points_from_xy(df["x"], df["y"]), crs=None
    )
    pts_3857 = project_to_visualization_crs(pts_gdf)
    xs = pts_3857.geometry.x.to_numpy(dtype=float)
    ys = pts_3857.geometry.y.to_numpy(dtype=float)
    xmin, ymin, xmax, ymax = pts_3857.total_bounds
    width = max(1.0, xmax - xmin)
    height = max(1.0, ymax - ymin)
    # Treat cell_size as short diagonal (flat-to-flat). In Web Mercator meters already.
    # Matplotlib's gridsize ~ number of hexes across width; approximate by width / D_short
    gridsize = max(5, int(width / max(1.0, float(cell_size))))
    # Add small padding to avoid cropping edge hexbins
    pad_frac = 0.05
    xmin_p = xmin - width * pad_frac
    xmax_p = xmax + width * pad_frac
    ymin_p = ymin - height * pad_frac
    ymax_p = ymax + height * pad_frac

    fig, ax = plt.subplots(figsize=(10, 10))
    # Add basemap first using padded bounds
    _add_basemap(ax, pts_3857, bounds=(xmin_p, ymin_p, xmax_p, ymax_p))
    hb = ax.hexbin(
        xs,
        ys,
        C=np.asarray(value, dtype=float),
        gridsize=gridsize,
        reduce_C_function=reduce_fn,
        extent=(xmin_p, xmax_p, ymin_p, ymax_p),
        cmap=MAP_CMAP,
        vmin=vmin,
        vmax=vmax,
        linewidths=0.0,
        alpha=MAP_ALPHA,
    )
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("x")
    ax.set_ylabel("y")
    ax.set_title(title)

    # Optionally add PT network overlay
    pt_handles = []
    pt_labels = []
    if show_pt_overlay and stops_df is not None:
        pt_handles, pt_labels = add_pt_network_overlay(
            ax,
            stops_df=stops_df,
            nodes_df=nodes_df,
            pt_links_df=pt_links_df,
            show_network=(nodes_df is not None and pt_links_df is not None),
            show_stops=True,
            cluster_stops=True,
            zorder_network=10,
            zorder_stops=11,
            add_to_legend=True,
        )

    # Add horizontal colorbar at the bottom
    cbar = fig.colorbar(
        hb, ax=ax, orientation="horizontal", shrink=0.6, pad=0.1, aspect=30
    )

    # Add PT legend if overlay was added
    if pt_handles:
        ax.legend(
            handles=pt_handles,
            labels=pt_labels,
            loc="upper right",
            fontsize=9,
            framealpha=0.95,
            edgecolor="black",
        )

    out_path = os.path.join(analysis_path, f"{OUTPUT_PREFIX}_{filename}")
    plt.tight_layout()
    fig.savefig(out_path, dpi=200)
    plt.close(fig)
    return out_path


def plot_grid_share_hexbin(
    persons_df: pd.DataFrame,
    within_flag_col: str,
    analysis_path: str,
    grid_shape: str,
    cell_size: float,
    pt_stop_distance: float,
    stops_df: pd.DataFrame | None = None,
    nodes_df: pd.DataFrame | None = None,
    pt_links_df: pd.DataFrame | None = None,
):
    """
    Hexbin plot of share within distance using mean reduction of boolean within flag.
    Generates both with and without PT overlay versions.
    """
    df = persons_df.dropna(subset=["x", "y", within_flag_col]).copy()
    if df.empty:
        return ""
    values = df[within_flag_col].astype(float).to_numpy()
    title = f"Share of population within {int(pt_stop_distance)}m of a PT stop"

    # Version WITHOUT overlay
    filename = f"share_within_grid{int(cell_size)}m_distance{int(pt_stop_distance)}m_{grid_shape}.png"
    _plot_hexbin_points(
        df=df,
        value=values,
        analysis_path=analysis_path,
        title=title,
        filename=filename,
        cell_size=cell_size,
        vmin=0.0,
        vmax=1.0,
        reduce_fn=np.mean,
    )

    # Version WITH overlay
    if stops_df is not None:
        title_with_pt = (
            f"Share of population within {int(pt_stop_distance)}m of a PT stop"
        )
        filename_with_pt = f"share_within_grid{int(cell_size)}m_distance{int(pt_stop_distance)}m_{grid_shape}_with_pt_overlay.png"
        return _plot_hexbin_points(
            df=df,
            value=values,
            analysis_path=analysis_path,
            title=title_with_pt,
            filename=filename_with_pt,
            cell_size=cell_size,
            vmin=0.0,
            vmax=1.0,
            reduce_fn=np.mean,
            show_pt_overlay=True,
            stops_df=stops_df,
            nodes_df=nodes_df,
            pt_links_df=pt_links_df,
        )

    return ""


def plot_value_hexbin(
    persons_df: pd.DataFrame,
    value_col: str,
    analysis_path: str,
    grid_shape: str,
    title: str,
    filename: str,
    cell_size: float,
    clip_q95: bool = True,
    stops_df: pd.DataFrame | None = None,
    nodes_df: pd.DataFrame | None = None,
    pt_links_df: pd.DataFrame | None = None,
):
    """
    Hexbin plot for a per-person numeric value (e.g., median access walk distance).
    Generates both with and without PT overlay versions.
    """
    df = persons_df.dropna(subset=["x", "y", value_col]).copy()
    if df.empty:
        return ""
    vmax = None
    if clip_q95:
        q95 = df[value_col].quantile(0.95)
        if pd.notna(q95):
            vmax = float(q95)

    # Version WITHOUT overlay
    _plot_hexbin_points(
        df=df,
        value=df[value_col].astype(float).to_numpy(),
        analysis_path=analysis_path,
        title=title,
        filename=filename,
        cell_size=cell_size,
        vmin=0.0,
        vmax=vmax,
        reduce_fn=np.median,
    )

    # Version WITH overlay
    if stops_df is not None:
        # Create filename with PT overlay suffix
        base_name = filename.rsplit(".", 1)[0]  # Remove .png
        ext = filename.rsplit(".", 1)[1] if "." in filename else "png"
        filename_with_pt = f"{base_name}_with_pt_overlay.{ext}"

        return _plot_hexbin_points(
            df=df,
            value=df[value_col].astype(float).to_numpy(),
            analysis_path=analysis_path,
            title=title,
            filename=filename_with_pt,
            cell_size=cell_size,
            vmin=0.0,
            vmax=vmax,
            reduce_fn=np.median,
            show_pt_overlay=True,
            stops_df=stops_df,
            nodes_df=nodes_df,
            pt_links_df=pt_links_df,
        )

    return ""


def _discover_eqasim_legs_file(sim_dir: str) -> str | None:
    """
    Return path to eqasim_legs.csv. Prefer root; fallback to final iteration in ITERS.
    """
    root = os.path.join(sim_dir, "eqasim_legs.csv")
    if os.path.exists(root):
        return root
    iters = os.path.join(sim_dir, "ITERS")
    if not os.path.isdir(iters):
        return None
    try:
        it_dirs = [d for d in os.listdir(iters) if d.startswith("it.")]
        it_nums = sorted([int(d.split(".")[-1]) for d in it_dirs])
        if not it_nums:
            return None
        last_it = it_nums[-1]
        cand = os.path.join(iters, f"it.{last_it}", f"{last_it}.eqasim_legs.csv")
        if os.path.exists(cand):
            return cand
        # Fallback to any eqasim_legs.csv in that folder
        cand2 = os.path.join(iters, f"it.{last_it}", "eqasim_legs.csv")
        return cand2 if os.path.exists(cand2) else None
    except Exception:
        return None


def _read_eqasim_legs(path: str) -> pd.DataFrame:
    """
    Read eqasim_legs.csv (semicolon separated) and normalize column types.
    Required columns include: person_id, person_trip_id, leg_index, mode, travel_time,
    routed_distance, euclidean_distance, vehicle_distance.
    """
    if not path or not os.path.exists(path):
        raise FileNotFoundError(f"eqasim_legs.csv not found at {path}")
    cols = [
        "person_id",
        "person_trip_id",
        "leg_index",
        "origin_x",
        "origin_y",
        "destination_x",
        "destination_y",
        "departure_time",
        "travel_time",
        "vehicle_distance",
        "routed_distance",
        "mode",
        "euclidean_distance",
        "origin_link_id",
        "destination_link_id",
    ]
    df = pd.read_csv(path, sep=";")
    # Ensure expected subset exists
    missing = [
        c
        for c in ["person_id", "person_trip_id", "leg_index", "mode"]
        if c not in df.columns
    ]
    if missing:
        raise RuntimeError(f"eqasim_legs.csv missing required columns: {missing}")
    # Coerce numerics
    for c in [
        "person_trip_id",
        "leg_index",
        "origin_x",
        "origin_y",
        "destination_x",
        "destination_y",
        "departure_time",
        "travel_time",
        "vehicle_distance",
        "routed_distance",
        "euclidean_distance",
    ]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def _extract_access_egress_walk(
    legs: pd.DataFrame,
    walk_modes: tuple = ("walk",),
    pt_modes: tuple = ("pt",),
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    From legs per (person_id, person_trip_id), find walk legs before first PT leg (access)
    and after last PT leg (egress). Return:
     - legs_walk_df: filtered legs with walk_leg_type in {access, egress} and walk_distance_m/walk_duration_s
     - per_trip_df: one row per trip with aggregated access/egress distance and duration
    Trips without any PT leg are ignored (no access/egress defined).
    """
    if legs.empty:
        return pd.DataFrame(), pd.DataFrame()

    legs_sorted = legs.sort_values(["person_id", "person_trip_id", "leg_index"]).copy()

    def pick_distance(row):
        # Prefer routed_distance, fallback to euclidean, then vehicle
        for c in ("routed_distance", "euclidean_distance", "vehicle_distance"):
            v = row.get(c, None)
            if pd.notna(v) and float(v) >= 0:
                return float(v)
        return float("nan")

    rows = []
    for (pid, tid), g in legs_sorted.groupby(
        ["person_id", "person_trip_id"], sort=False
    ):
        modes = g["mode"].astype(str).tolist()
        pt_pos = [i for i, m in enumerate(modes) if m in pt_modes]
        if not pt_pos:
            continue  # no PT on this trip
        first_pt_idx = g.iloc[pt_pos[0]]["leg_index"]
        last_pt_idx = g.iloc[pt_pos[-1]]["leg_index"]
        # Access: walk legs before first_pt_idx
        access_mask = (g["leg_index"] < first_pt_idx) & (g["mode"].isin(walk_modes))
        egress_mask = (g["leg_index"] > last_pt_idx) & (g["mode"].isin(walk_modes))
        for typ, mask in (("access", access_mask), ("egress", egress_mask)):
            if mask.any():
                for _, r in g[mask].iterrows():
                    rows.append(
                        {
                            "person_id": pid,
                            "person_trip_id": tid,
                            "leg_index": r["leg_index"],
                            "walk_leg_type": typ,
                            "mode": r["mode"],
                            "walk_duration_s": r.get("travel_time", float("nan")),
                            "walk_distance_m": pick_distance(r),
                        }
                    )

    legs_walk_df = pd.DataFrame(rows)
    if legs_walk_df.empty:
        return legs_walk_df, pd.DataFrame()

    # Aggregate per trip and type
    agg = legs_walk_df.groupby(
        ["person_id", "person_trip_id", "walk_leg_type"], as_index=False
    ).agg(
        total_walk_distance_m=("walk_distance_m", "sum"),
        total_walk_duration_s=("walk_duration_s", "sum"),
    )
    # Pivot to columns access_* and egress_*
    pvt = agg.pivot_table(
        index=["person_id", "person_trip_id"],
        columns="walk_leg_type",
        values=["total_walk_distance_m", "total_walk_duration_s"],
        fill_value=0.0,
    )
    pvt.columns = [f"{a}_{b}" for a, b in pvt.columns.to_flat_index()]
    per_trip = pvt.reset_index()
    # Ensure consistent columns even if one type absent
    for base in ("total_walk_distance_m", "total_walk_duration_s"):
        for typ in ("access", "egress"):
            col = f"{base}_{typ}"
            if col not in per_trip.columns:
                per_trip[col] = 0.0

    # Rename to clear output names
    per_trip = per_trip.rename(
        columns={
            "total_walk_distance_m_access": "access_walk_distance_m",
            "total_walk_distance_m_egress": "egress_walk_distance_m",
            "total_walk_duration_s_access": "access_walk_duration_s",
            "total_walk_duration_s_egress": "egress_walk_duration_s",
        }
    )
    return legs_walk_df, per_trip


def plot_access_egress_distributions(
    per_trip_df: pd.DataFrame, analysis_path: str
) -> str:
    """
    Plot side-by-side histograms of access vs egress walking distances.
    """
    if per_trip_df is None or per_trip_df.empty:
        return ""

    acc = per_trip_df["access_walk_distance_m"].astype(float)
    egr = per_trip_df["egress_walk_distance_m"].astype(float)

    # Calculate medians for vertical lines
    acc_median = acc.median()
    egr_median = egr.median()

    # Robust x-limit: up to 95th percentile across both
    combined = pd.concat([acc, egr])
    q95 = combined.quantile(0.95)
    max_val = combined.max()
    max_x = float(q95) if pd.notna(q95) and q95 > 0 else float(max(max_val, 1.0))
    bins = min(40, max(10, int(max_x // 20)))  # ~20m bin width up to a cap

    fig, axes = plt.subplots(1, 2, figsize=(12, 5), sharey=True)

    # Access histogram
    ax = axes[0]
    ax.hist(acc, bins=bins, range=(0, max_x), color=ACCESS_COLOR, alpha=0.8)
    # Add median line for access
    if pd.notna(acc_median):
        ax.axvline(
            acc_median,
            color="darkred",
            linestyle="--",
            linewidth=2,
            label=f"Median: {acc_median:.0f}m",
        )
        ax.legend(loc="upper right")
    ax.set_title("Access walk distance")
    ax.set_xlabel("Distance (m)")
    ax.set_ylabel("Trips")

    # Egress histogram
    ax = axes[1]
    ax.hist(egr, bins=bins, range=(0, max_x), color=EGRESS_COLOR, alpha=0.8)
    # Add median line for egress
    if pd.notna(egr_median):
        ax.axvline(
            egr_median,
            color="darkred",
            linestyle="--",
            linewidth=2,
            label=f"Median: {egr_median:.0f}m",
        )
        ax.legend(loc="upper right")
    ax.set_title("Egress walk distance")
    ax.set_xlabel("Distance (m)")

    fig.suptitle("Access vs Egress Walking Distances")
    plt.tight_layout()
    png_path = os.path.join(
        analysis_path, f"{OUTPUT_PREFIX}_access_egress_distribution.png"
    )
    fig.savefig(png_path, dpi=200)
    plt.close(fig)
    return png_path


def plot_walking_dist_by_category(
    per_trip_df: pd.DataFrame,
    persons_df: pd.DataFrame,
    category_col: str,
    analysis_path: str,
) -> str:
    """
    Boxplots of access and egress walking distances grouped by a demographic category.
    """
    if (
        per_trip_df is None
        or per_trip_df.empty
        or category_col not in persons_df.columns
    ):
        return ""

    df = per_trip_df.merge(
        persons_df[["person_id", category_col]], on="person_id", how="left"
    )
    df = df.dropna(subset=[category_col])
    if df.empty:
        return ""

    # Handle category ordering and labeling
    if category_col == "age_class":
        # Use categorical order for age classes
        if hasattr(df[category_col], "cat") and df[category_col].cat.ordered:
            ordered_cats = df[category_col].cat.categories.tolist()
        else:
            # Fallback: use unique values in order they appear
            ordered_cats = df[category_col].unique().tolist()
    else:
        # Order other categories by median access distance for readability
        medians = (
            df.groupby(category_col)["access_walk_distance_m"].median().sort_values()
        )
        ordered_cats = medians.index.tolist()

    # Create display labels
    def get_display_label(cat, col):
        if col == "employed":
            return "Employed" if cat else "Unemployed"
        elif col == "sex":
            return {"m": "Male", "f": "Female"}.get(cat, str(cat))
        else:
            return str(cat)

    display_labels = [get_display_label(cat, category_col) for cat in ordered_cats]

    # Prepare data for boxplots
    access_data = [
        df[df[category_col] == cat]["access_walk_distance_m"].dropna().values
        for cat in ordered_cats
    ]
    egress_data = [
        df[df[category_col] == cat]["egress_walk_distance_m"].dropna().values
        for cat in ordered_cats
    ]

    # Create figure with sufficient width
    fig, ax = plt.subplots(
        figsize=(max(FIGURE_SIZE_BOX[0], len(ordered_cats) * 0.8), FIGURE_SIZE_BOX[1])
    )

    # Position setup
    x = np.arange(len(ordered_cats))
    width = 0.2

    # Create boxplots with colors
    access_bp = ax.boxplot(
        access_data,
        positions=x - width / 2,
        widths=width,
        patch_artist=True,
        showfliers=False,
        boxprops=dict(facecolor=ACCESS_COLOR, alpha=0.7),
    )
    egress_bp = ax.boxplot(
        egress_data,
        positions=x + width / 2,
        widths=width,
        patch_artist=True,
        showfliers=False,
        boxprops=dict(facecolor=EGRESS_COLOR, alpha=0.7),
    )

    # Create descriptive title
    category_titles = {
        "age_class": "Age Group",
        "employed": "Employment Status",
        "sex": "Gender",
    }
    title = f"Access and Egress by {category_titles.get(category_col, category_col.title())}"

    # Labels and formatting
    ax.set_xticks(x)
    ax.set_xticklabels(display_labels, rotation=45, ha="right")
    ax.set_ylabel("Walking distance (m)")
    # Set custom xlabel for age_class
    if category_col == "age_class":
        ax.set_xlabel("Age Groups")
    ax.set_title(title)

    # Legend
    legend_handles = [
        Patch(facecolor=ACCESS_COLOR, alpha=0.7, label="Access"),
        Patch(facecolor=EGRESS_COLOR, alpha=0.7, label="Egress"),
    ]
    ax.legend(handles=legend_handles, loc="upper right", fontsize=13)

    png_path = os.path.join(
        analysis_path, f"{OUTPUT_PREFIX}_distances_by_{category_col}.png"
    )
    plt.tight_layout()
    fig.savefig(png_path, dpi=PLOT_DPI)
    plt.close(fig)
    return png_path


# =============================================================================
# MODE SHARE COMPARISON FUNCTIONS
# =============================================================================


def mode_share_comparison(
    context, df_sim_trips, df_hts_trips, df_hts_persons, suffix=None
):
    """
    Compare mode shares between two trip datasets.

    Parameters:
        context: execution context
        df_sim_trips: Simulation trip dataset
        df_hts_trips: Reference trip dataset
        df_hts_persons: Person-level data for reference dataset (for weights)
        suffix: optional suffix for output filenames
    """
    import analysis_dhaka.seville.ivt_style.myplottools as myplottools

    # Mode mapping: merge car_passenger with car for consistent comparison
    mode_map = {
        "bike": "bike",
        "car": "car",
        "car_passenger": "car",
        "pt": "pt",
        "walk": "walk",
    }

    # Simulation: count trips by mode
    df_sim = df_sim_trips.copy()
    df_sim["mode"] = df_sim["mode"].map(mode_map).fillna("other")
    sim_counts = df_sim[df_sim["mode"].isin(["bike", "car", "pt", "walk"])][
        "mode"
    ].value_counts()
    sim_share = sim_counts / sim_counts.sum() * 100

    # HTS: sum weights by mode
    df_hts = df_hts_trips.copy()
    df_hts["mode"] = df_hts["mode"].map(mode_map).fillna(df_hts["mode"])
    # Merge weights if not present
    if "weight_person" not in df_hts.columns and df_hts_persons is not None:
        df_hts = df_hts.merge(
            df_hts_persons[["person_id", "weight_person"]], on="person_id", how="left"
        )
    hts_counts = (
        df_hts[df_hts["mode"].isin(["bike", "car", "pt", "walk"])]
        .groupby("mode")["weight_person"]
        .sum()
    )
    hts_share = hts_counts / hts_counts.sum() * 100

    # Align modes
    modes = ["bike", "car", "pt", "walk"]
    sim_vals = [sim_share.get(m, 0) for m in modes]
    hts_vals = [hts_share.get(m, 0) for m in modes]

    # Plot
    title_plot = "Mode Share Comparison"
    title_figure = "mode_share"
    if suffix:
        title_plot += " - " + suffix
        title_figure += "_" + suffix
    title_figure += ".png"

    myplottools.plot_comparison_bar(
        context,
        imtitle=title_figure,
        plottitle=title_plot,
        ylabel="Percentage",
        xlabel="Mode",
        lab=modes,
        hts=hts_vals,
        simulation=sim_vals,
        lablist=["HTS", "Simulation"],
        t=12,
        figsize=[8, 6],
        dpi=300,
        w=0.35,
        xticksrot=True,
    )

    print(f"INFO: Mode share comparison plot saved: {title_figure}")


def mode_share_by_distance(
    context, df_sim_trips, df_hts_trips, df_hts_persons, suffix=None
):
    """
    Plot mode share by distance bins for two trip datasets.
    Shows how mode share changes with trip distance.

    Parameters:
        context: execution context
        df_sim_trips: Simulation trip dataset
        df_hts_trips: Reference trip dataset
        df_hts_persons: Person-level data for reference dataset (for weights)
        suffix: optional suffix for output filenames
    """

    # Skip if reference data not available
    if df_hts_trips is None or len(df_hts_trips) == 0:
        print("INFO: Skipping mode share by distance - no reference data available")
        return

    # Mode mapping
    mode_map = {
        "bike": "bike",
        "car": "car",
        "car_passenger": "car",
        "pt": "pt",
        "walk": "walk",
    }

    # Prepare simulation data
    df_sim = df_sim_trips.copy()
    # Use routed_distance if available, otherwise euclidean_distance
    if "routed_distance" in df_sim.columns:
        df_sim["distance_m"] = df_sim["routed_distance"]
    elif "euclidean_distance" in df_sim.columns:
        df_sim["distance_m"] = df_sim["euclidean_distance"]
    else:
        print("WARNING: No distance column in simulation data")
        return

    df_sim["mode"] = df_sim["mode"].map(mode_map).fillna("other")
    df_sim = df_sim[df_sim["mode"].isin(["bike", "car", "pt", "walk"])]

    # Prepare HTS data
    df_hts = df_hts_trips.copy()
    df_hts["distance_m"] = df_hts["routed_distance"]
    df_hts["mode"] = df_hts["mode"].map(mode_map).fillna(df_hts["mode"])

    df_hts = df_hts[df_hts["mode"].isin(["bike", "car", "pt", "walk"])]

    # Merge weights if not present
    if "weight_person" not in df_hts.columns and df_hts_persons is not None:
        df_hts = df_hts.merge(
            df_hts_persons[["person_id", "weight_person"]], on="person_id", how="left"
        )

    # Define distance bins (in meters)
    distance_bins = [0, 1000, 2000, 3000, 4000, 5000, 6000]
    bin_centers = [
        (distance_bins[i] + distance_bins[i + 1]) / 2
        for i in range(len(distance_bins) - 1)
    ]

    modes = ["bike", "car", "pt", "walk"]

    # Calculate mode shares for each distance bin
    hts_shares = {mode: [] for mode in modes}
    sim_shares = {mode: [] for mode in modes}

    for i in range(len(distance_bins) - 1):
        dist_min = distance_bins[i]
        dist_max = distance_bins[i + 1]

        # HTS: weighted counts
        hts_bin = df_hts[
            (df_hts["distance_m"] >= dist_min) & (df_hts["distance_m"] < dist_max)
        ]
        if len(hts_bin) > 0:
            hts_total = hts_bin.groupby("mode")["weight_person"].sum()
            hts_sum = hts_total.sum()
            if hts_sum > 0:
                for mode in modes:
                    hts_shares[mode].append(hts_total.get(mode, 0) / hts_sum)
            else:
                for mode in modes:
                    hts_shares[mode].append(0)
        else:
            for mode in modes:
                hts_shares[mode].append(0)

        # Simulation: unweighted counts
        sim_bin = df_sim[
            (df_sim["distance_m"] >= dist_min) & (df_sim["distance_m"] < dist_max)
        ]
        if len(sim_bin) > 0:
            sim_total = sim_bin["mode"].value_counts()
            sim_sum = sim_total.sum()
            if sim_sum > 0:
                for mode in modes:
                    sim_shares[mode].append(sim_total.get(mode, 0) / sim_sum)
            else:
                for mode in modes:
                    sim_shares[mode].append(0)
        else:
            for mode in modes:
                sim_shares[mode].append(0)

    # Create the plot
    fig, ax = plt.subplots(figsize=(8, 5))

    # Define colors for each mode
    colors = {
        "bike": "#FF8C00",  # Orange
        "car": "#2E8B57",  # Green
        "pt": "#294088",  # Blue
        "walk": "#48A0F8",  # Light Blue
    }

    # Plot HTS (dashed lines) - these will appear first in legend
    hts_lines = []
    for mode in modes:
        (line,) = ax.plot(
            bin_centers,
            hts_shares[mode],
            linestyle="--",
            marker="o",
            color=colors[mode],
            label=f"HTS {mode}",
            linewidth=1.5,
            markersize=5,
        )
        hts_lines.append(line)

    # Plot Simulation (solid lines) - these will appear second in legend
    sim_lines = []
    for mode in modes:
        (line,) = ax.plot(
            bin_centers,
            sim_shares[mode],
            linestyle="-",
            marker=">",
            color=colors[mode],
            label=f"Sim {mode}",
            linewidth=2,
            markersize=6,
        )
        sim_lines.append(line)

    ax.set_xlabel("Distance (m)", fontsize=12)
    ax.set_ylabel("Mode share", fontsize=12)
    ax.set_title("Mode share by distance", fontsize=14)

    # Extend y-axis limit to give 10% more room above the data
    y_min, y_max = ax.get_ylim()
    y_range = y_max - y_min
    ax.set_ylim(y_min, y_max + 0.15 * y_range)

    # Create legend with 2 rows arranged as:
    # Row 1: HTS bike, HTS car, HTS pt, HTS walk
    # Row 2: Sim bike, Sim car, Sim pt, Sim walk
    all_lines = [item for pair in zip(hts_lines, sim_lines) for item in pair]
    all_labels = [line.get_label() for line in all_lines]

    ax.legend(
        all_lines,
        all_labels,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.98),
        ncol=4,
        fontsize=9,
    )
    ax.grid(True, alpha=0.3)

    # Save figure
    analysis_path = context.config("analysis_path")
    title_figure = "mode_share_by_distance"
    if suffix:
        title_figure += "_" + suffix
    title_figure += ".png"

    plt.savefig("%s/%s" % (analysis_path, title_figure), dpi=300)
    plt.close()

    print(f"INFO: Mode share by distance plot saved: {title_figure}")


# =============================================================================
# MAIN EXECUTE FUNCTION
# =============================================================================


def execute(context):
    """
    Main analysis execution with clear phases:
    A) Data Loading
    B) Data Enrichment for all PT stop distances
    C) Aggregation and Visualization for all combinations
    """
    # Get configuration
    output_path = context.config("output_path")
    analysis_path = context.config("analysis_path")
    data_path = context.config("data_path")
    sim_output_dir = context.config("simulation_output_dir")

    os.makedirs(analysis_path, exist_ok=True)

    # =========================================================================
    # A) DATA LOADING
    # =========================================================================

    print("Phase A: Loading data...")

    # Load simulation outputs
    sim_dir = os.path.join(output_path, sim_output_dir)
    schedule_xml = os.path.join(sim_dir, "output_transitSchedule.xml.gz")
    network_xml = os.path.join(sim_dir, "output_network.xml.gz")

    # Extract transit stops
    stops_df = _extract_stops_from_schedule(schedule_xml)

    # Extract PT network
    print("  Extracting PT network from MATSim output...")
    nodes_df, pt_links_df = _extract_pt_network_from_matsim(network_xml)
    print(f"    Found {len(nodes_df)} nodes and {len(pt_links_df)} PT links")

    # Ensure stops are in source CRS
    stops_gdf = gpd.GeoDataFrame(
        stops_df.copy(),
        geometry=gpd.points_from_xy(stops_df["x"], stops_df["y"]),
        crs=f"EPSG:{SOURCE_EPSG}",
    )

    # Load person home coordinates
    persons_df = read_persons(sim_dir)

    # Load trip legs if available
    legs_path = _discover_eqasim_legs_file(sim_dir)
    legs_df = pd.DataFrame()
    if legs_path:
        legs_df = _read_eqasim_legs(legs_path)

    # =========================================================================
    # B) DATA ENRICHMENT FOR ALL PT STOP DISTANCES
    # =========================================================================

    print("Phase B: Enriching data...")

    # Ensure persons are in source CRS and clean coordinates
    persons_df = persons_df.dropna(subset=["x", "y"])
    persons_gdf = gpd.GeoDataFrame(
        persons_df.copy(),
        geometry=gpd.points_from_xy(persons_df["x"], persons_df["y"]),
        crs=f"EPSG:{SOURCE_EPSG}",
    )

    # Compute nearest stop distances (only once)
    nearest_df = nearest_stop_geopandas(persons_df[["x", "y"]], stops_df)
    persons_df = pd.concat([persons_df.reset_index(drop=True), nearest_df], axis=1)

    # Add within distance flags for all PT stop distances
    for pt_stop_distance in PT_STOP_DISTANCE_M:
        within_flag_col = f"within_{int(pt_stop_distance)}m"
        persons_df[within_flag_col] = (
            persons_df["dist_to_nearest_stop_m"] <= pt_stop_distance
        )

    # Extract access/egress walk data if legs are available (only once)
    legs_walk_df = pd.DataFrame()
    per_trip_df = pd.DataFrame()
    if not legs_df.empty:
        legs_walk_df, per_trip_df = _extract_access_egress_walk(
            legs_df, walk_modes=WALK_MODES, pt_modes=PT_MODES
        )

        # Aggregate per-person access metrics
        if not per_trip_df.empty and "access_walk_distance_m" in per_trip_df.columns:
            per_person = (
                per_trip_df.dropna(subset=["access_walk_distance_m"])
                .groupby("person_id", as_index=False)
                .agg(
                    access_walk_distance_median=("access_walk_distance_m", "median"),
                    access_walk_distance_mean=("access_walk_distance_m", "mean"),
                    n_pt_trips=("access_walk_distance_m", "count"),
                )
            )
            persons_df = persons_df.merge(per_person, on="person_id", how="left")

    # =========================================================================
    # C) AGGREGATION AND VISUALIZATION FOR ALL COMBINATIONS
    # =========================================================================

    print(
        "Phase C: Creating aggregations and visualizations for all parameter combinations..."
    )

    # Generate analysis for each combination of parameters
    for cell_size in CELL_SIZE_M:
        for pt_stop_distance in PT_STOP_DISTANCE_M:
            for grid_shape in GRID_SHAPES:
                # Skip neighborhoods for all but the first cell size (since cell size doesn't matter for neighborhoods)
                if grid_shape == "neighborhoods" and cell_size != CELL_SIZE_M[0]:
                    continue

                print(
                    f"  Processing combination: cell_size={int(cell_size)}m, pt_stop_distance={int(pt_stop_distance)}m, grid_shape={grid_shape}"
                )

                within_flag_col = f"within_{int(pt_stop_distance)}m"

                # Share within distance aggregation
                share_col = f"share_within_{int(pt_stop_distance)}m"
                grid_df, grid_gdf = aggregate_to_grid(
                    persons_df,
                    agg_column=within_flag_col,
                    agg_method="share",
                    cell_size=cell_size,
                    grid_shape=grid_shape,
                    data_path=data_path,
                )

                # Plot accessibility maps
                if grid_shape == "hex":
                    plot_grid_share_hexbin(
                        persons_df,
                        within_flag_col,
                        analysis_path,
                        grid_shape,
                        cell_size,
                        pt_stop_distance,
                        stops_df=stops_df,
                        nodes_df=nodes_df,
                        pt_links_df=pt_links_df,
                    )
                elif grid_shape == "neighborhoods":
                    # For neighborhoods, cell size is irrelevant - exclude from filename
                    # Use dynamic vmin based on actual data for better color contrast
                    data_vmin = (
                        float(grid_gdf["value"].min()) if not grid_gdf.empty else 0.0
                    )

                    title = f"Share of population within {int(pt_stop_distance)}m of a PT stop"
                    output_path_map = os.path.join(
                        analysis_path,
                        f"{OUTPUT_PREFIX}_share_within_distance{int(pt_stop_distance)}m_{grid_shape}.png",
                    )
                    plot_choropleth_map(
                        grid_gdf,
                        "value",
                        title,
                        output_path_map,
                        vmin=data_vmin,
                        vmax=1.0,
                        clip_quantile=None,
                    )

                    # Also create a version WITH PT network overlay
                    title_with_pt = f"Share of population within {int(pt_stop_distance)}m of a PT stop"
                    output_path_map_pt = os.path.join(
                        analysis_path,
                        f"{OUTPUT_PREFIX}_share_within_distance{int(pt_stop_distance)}m_{grid_shape}_with_pt_overlay.png",
                    )
                    plot_choropleth_map(
                        grid_gdf,
                        "value",
                        title_with_pt,
                        output_path_map_pt,
                        vmin=data_vmin,
                        vmax=1.0,
                        clip_quantile=None,
                        show_pt_overlay=True,
                        stops_df=stops_df,
                        nodes_df=nodes_df,
                        pt_links_df=pt_links_df,
                    )
                else:
                    # For square grids, include cell size in filename
                    title = f"Share of population within {int(pt_stop_distance)}m of a PT stop"
                    output_path_map = os.path.join(
                        analysis_path,
                        f"{OUTPUT_PREFIX}_share_within_grid{int(cell_size)}m_distance{int(pt_stop_distance)}m_{grid_shape}.png",
                    )
                    plot_choropleth_map(
                        grid_gdf,
                        "value",
                        title,
                        output_path_map,
                        vmin=0.0,
                        vmax=1.0,
                        clip_quantile=None,
                    )

                    # Also create a version WITH PT network overlay
                    title_with_pt = f"Share of population within {int(pt_stop_distance)}m of a PT stop"
                    output_path_map_pt = os.path.join(
                        analysis_path,
                        f"{OUTPUT_PREFIX}_share_within_grid{int(cell_size)}m_distance{int(pt_stop_distance)}m_{grid_shape}_with_pt_overlay.png",
                    )
                    plot_choropleth_map(
                        grid_gdf,
                        "value",
                        title_with_pt,
                        output_path_map_pt,
                        vmin=0.0,
                        vmax=1.0,
                        clip_quantile=None,
                        show_pt_overlay=True,
                        stops_df=stops_df,
                        nodes_df=nodes_df,
                        pt_links_df=pt_links_df,
                    )

                # Access walk distance aggregation (if data available)
                if "access_walk_distance_median" in persons_df.columns:
                    dist_df, dist_gdf = aggregate_to_grid(
                        persons_df[["x", "y", "access_walk_distance_median"]],
                        agg_column="access_walk_distance_median",
                        agg_method="median",
                        cell_size=cell_size,
                        grid_shape=grid_shape,
                        data_path=data_path,
                    )

                    # Plot walking distance maps
                    if grid_shape == "hex":
                        plot_value_hexbin(
                            persons_df,
                            "access_walk_distance_median",
                            analysis_path,
                            grid_shape,
                            "Median access walk distance (m)",
                            f"access_median_grid{int(cell_size)}m_{grid_shape}.png",
                            cell_size,
                            clip_q95=True,
                            stops_df=stops_df,
                            nodes_df=nodes_df,
                            pt_links_df=pt_links_df,
                        )
                    elif grid_shape == "neighborhoods":
                        # For neighborhoods, cell size is irrelevant - exclude from filename
                        # Use dynamic vmin based on actual data for better color contrast
                        data_vmin = (
                            float(dist_gdf["value"].min())
                            if not dist_gdf.empty
                            else 0.0
                        )

                        title = "Median access walk distance (m)"
                        output_path_dist = os.path.join(
                            analysis_path,
                            f"{OUTPUT_PREFIX}_access_median_{grid_shape}.png",
                        )
                        plot_choropleth_map(
                            dist_gdf,
                            "value",
                            title,
                            output_path_dist,
                            vmin=data_vmin,
                            clip_quantile=0.95,
                        )

                        # Also create a version WITH PT network overlay
                        title_with_pt = "Median access walk distance (m)"
                        output_path_dist_pt = os.path.join(
                            analysis_path,
                            f"{OUTPUT_PREFIX}_access_median_{grid_shape}_with_pt_overlay.png",
                        )
                        plot_choropleth_map(
                            dist_gdf,
                            "value",
                            title_with_pt,
                            output_path_dist_pt,
                            vmin=data_vmin,
                            clip_quantile=0.95,
                            show_pt_overlay=True,
                            stops_df=stops_df,
                            nodes_df=nodes_df,
                            pt_links_df=pt_links_df,
                        )
                    else:
                        # For square grids, include cell size in filename
                        title = "Median access walk distance (m)"
                        output_path_dist = os.path.join(
                            analysis_path,
                            f"{OUTPUT_PREFIX}_access_median_grid{int(cell_size)}m_{grid_shape}.png",
                        )
                        plot_choropleth_map(
                            dist_gdf,
                            "value",
                            title,
                            output_path_dist,
                            vmin=0.0,
                            clip_quantile=0.95,
                        )

                        # Also create a version WITH PT network overlay
                        title_with_pt = "Median access walk distance (m)"
                        output_path_dist_pt = os.path.join(
                            analysis_path,
                            f"{OUTPUT_PREFIX}_access_median_grid{int(cell_size)}m_{grid_shape}_with_pt_overlay.png",
                        )
                        plot_choropleth_map(
                            dist_gdf,
                            "value",
                            title_with_pt,
                            output_path_dist_pt,
                            vmin=0.0,
                            clip_quantile=0.95,
                            show_pt_overlay=True,
                            stops_df=stops_df,
                            nodes_df=nodes_df,
                            pt_links_df=pt_links_df,
                        )

    # Shape-independent visualizations (using first values as representative)
    print("  Creating shape-independent visualizations...")

    # Person-level distance heatmap (using first PT_STOP_DISTANCE_M)
    plot_persons_distance_heatmap(
        persons_df, stops_df, analysis_path, nodes_df, pt_links_df
    )

    # NEW: PT network with accessibility overlay
    plot_pt_network_with_accessibility(
        persons_df, stops_df, nodes_df, pt_links_df, analysis_path
    )

    # Access vs egress distributions
    if not per_trip_df.empty:
        plot_access_egress_distributions(per_trip_df, analysis_path)

    # Demographic breakdowns
    demographic_cols = ["sex", "employed"]

    # Add age_class for demographic analysis only
    persons_with_age_class = persons_df.copy()
    persons_with_age_class["age_class"] = compute_age_class(persons_df["age"])
    demographic_cols.append("age_class")

    for col in demographic_cols:
        if col in persons_with_age_class.columns and not per_trip_df.empty:
            plot_walking_dist_by_category(
                per_trip_df, persons_with_age_class, col, analysis_path
            )

    # Save critical outputs only
    print("  Saving critical outputs...")

    # Transit stops (time-consuming to re-parse)
    stops_df.to_csv(
        os.path.join(analysis_path, f"{OUTPUT_PREFIX}_stops.csv"), index=False
    )
    stops_gdf.to_file(
        os.path.join(analysis_path, f"{OUTPUT_PREFIX}_stops.gpkg"), driver="GPKG"
    )

    # PT network data
    if not nodes_df.empty:
        nodes_df.to_csv(
            os.path.join(analysis_path, f"{OUTPUT_PREFIX}_network_nodes.csv"),
            index=False,
        )
    if not pt_links_df.empty:
        pt_links_df.to_csv(
            os.path.join(analysis_path, f"{OUTPUT_PREFIX}_pt_links.csv"), index=False
        )

    # Final enriched persons data
    persons_df.to_csv(
        os.path.join(analysis_path, f"{OUTPUT_PREFIX}_persons_enriched.csv"),
        index=False,
    )

    # =========================================================================
    # D) MODE SHARE COMPARISON
    # =========================================================================

    print("Phase D: Comparing mode shares...")

    try:
        # Load simulation trips
        eqasim_trips_path = os.path.join(sim_dir, "eqasim_trips.csv")
        if os.path.exists(eqasim_trips_path):
            df_sim_trips = pd.read_csv(eqasim_trips_path, sep=";")
            print(f"  Loaded {len(df_sim_trips)} trips from simulation")

            # Load HTS data
            df_hts_households, df_hts_persons, df_hts_trips = context.stage(
                "data.hts.entd.reweighted"
            )
            print(f"  Loaded {len(df_hts_trips)} trips from HTS")

            # Rename weight column for consistency
            if "person_weight" in df_hts_persons.columns:
                df_hts_persons = df_hts_persons.rename(
                    columns={"person_weight": "weight_person"}
                )

            # Generate mode share comparisons
            mode_share_comparison(
                context, df_sim_trips, df_hts_trips, df_hts_persons, suffix=None
            )
            mode_share_by_distance(
                context, df_sim_trips, df_hts_trips, df_hts_persons, suffix=None
            )

            print("  Mode share comparison completed")
        else:
            print(f"  WARNING: Simulation trips file not found: {eqasim_trips_path}")
    except Exception as e:
        print(f"  WARNING: Could not complete mode share comparison: {e}")
        import traceback

        traceback.print_exc()

    print("Analysis complete!")
