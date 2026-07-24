import gzip
import math
import os
import statistics
import xml.etree.ElementTree as ET

import contextily as cx
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.markers import MarkerStyle
from matplotlib.patches import Patch
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import cdist, pdist
from shapely.geometry import LineString, Polygon

from analysis_dhaka.marginals import AGE_CLASS_BOUNDS, AGE_CLASS_LABELS

SOURCE_EPSG = 25832
BASEMAP_EPSG = 3857

MAP_CMAP = "magma_r"
MAP_ALPHA = 0.7

GRID_SHAPES = ["hex", "neighborhoods"]
CELL_SIZE_M = [500.0]
PT_STOP_DISTANCE_M = [200.0]

WALK_MODES = ("walk",)
PT_MODES = ("pt",)
ACCESS_MODES = ["walk", "bike"]

FIGURE_SIZE_MAP = (10, 10)
FIGURE_SIZE_BOX = (8, 6)
PLOT_DPI = 200

ACCESS_COLOR = "#3182bd"
EGRESS_COLOR = "#31a354"

PT_NETWORK_COLOR = "#0066CC"
PT_NETWORK_LINEWIDTH = 2.0
PT_NETWORK_ALPHA = 0.8
PT_STOP_COLOR = "#00FF00"
PT_STOP_SIZE = 25
PT_STOP_EDGE_COLOR = "black"
PT_STOP_EDGE_WIDTH = 0.8
PT_STOP_ALPHA = 0.9

OUTPUT_PREFIX = "accessibility"

BEELINE_DISTANCE_FACTOR = 1.3
MODE_SPEEDS_MPS = {"walk": 1.25, "bike": 4.16}
BETA_VALUES = {"walk": 0.25, "bike": 0.20}
MAX_ACCESS_DISTANCE_M = {
    "walk": 2000,  # eqasim_trips mean 782m
    "bike": 5000,  # eqasim trips mean 4035m
}

CATCHMENT_THRESHOLDS = {
    "walk": 10.0,  # (mean) 782 / 1.25 = 625s = 10.4min
    "bike": 15.0,  # (mean) 4035 / 4.16 = 970s = 16.2min
}

# Functional catchment: access + wait + ride + transfer
# Roughly: access threshold + ~5 min wait + ~14 min ride + ~2.5 min transfer
FUNCTIONAL_CATCHMENT_THRESHOLDS = {
    "walk": 30.0,  # 10 min access + ~20 min service
    "bike": 35.0,  # 15 min access + ~20 min service
}

_TYPE_DISPLAY = {"access_only": "Access-Only", "functional": "Functional"}

TRANSFER_PENALTY_MIN = 5.0


def _area_key(v):
    if pd.notna(v) and v != "":
        return f"{float(v):.1f}"
    return None


def compute_age_class(ages: pd.Series) -> pd.Series:
    bounds = list(AGE_CLASS_BOUNDS)
    labels = list(AGE_CLASS_LABELS)
    idx = np.digitize(ages.to_numpy(), bounds, right=True)
    mapped = [
        labels[min(max(int(i), 0), len(labels) - 1)] if np.isfinite(a) else np.nan
        for i, a in zip(idx, ages.to_numpy())
    ]
    return pd.Series(
        pd.Categorical(mapped, categories=labels, ordered=True), index=ages.index
    )


def ensure_source_crs(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if gdf.crs is None:
        return gdf.set_crs(epsg=SOURCE_EPSG, allow_override=True)
    if gdf.crs.to_epsg() != SOURCE_EPSG:
        return gdf.to_crs(epsg=SOURCE_EPSG)
    return gdf


def project_to_visualization_crs(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    return ensure_source_crs(gdf).to_crs(epsg=BASEMAP_EPSG)


def aggregate_to_grid(
    df_points: pd.DataFrame,
    agg_column: str,
    agg_method: str,
    cell_size: float,
    grid_shape: str = "square",
    neighborhoods_gdf: gpd.GeoDataFrame | None = None,
    data_path: str | None = None,
) -> tuple[pd.DataFrame, gpd.GeoDataFrame]:
    clean = df_points.copy()
    clean = clean.dropna(subset=["x", "y", agg_column])
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


_AGG_FUNCS = {
    "count": lambda col: {"n_points": (col, "count"), "value": (col, "count")},
    "sum": lambda col: {"n_points": (col, "count"), "value": (col, "sum")},
    "mean": lambda col: {"n_points": (col, "count"), "value": (col, "mean")},
    "median": lambda col: {"n_points": (col, "count"), "value": (col, "median")},
}


def _apply_groupby_agg(grouped, agg_column: str, agg_method: str) -> pd.DataFrame:
    if agg_method in _AGG_FUNCS:
        return grouped.agg(**_AGG_FUNCS[agg_method](agg_column))
    elif agg_method == "share":
        agg = grouped.agg(n_points=(agg_column, "count"), sum_value=(agg_column, "sum"))
        agg["value"] = agg["sum_value"] / agg["n_points"]
        return agg.drop(columns=["sum_value"])
    else:
        raise ValueError(f"Unknown agg_method: {agg_method}")


def _aggregate_to_neighborhoods(
    df_points: pd.DataFrame,
    agg_column: str,
    agg_method: str,
    neighborhoods_gdf: gpd.GeoDataFrame | None,
    data_path: str | None,
) -> tuple[pd.DataFrame, gpd.GeoDataFrame]:
    if neighborhoods_gdf is None:
        neighborhoods_gdf = _load_hannover_neighborhoods(data_path)

    pts_gdf = gpd.GeoDataFrame(
        df_points.copy(),
        geometry=gpd.points_from_xy(df_points["x"], df_points["y"]),
        crs=f"EPSG:{SOURCE_EPSG}",
    )

    join = gpd.sjoin(
        pts_gdf,
        neighborhoods_gdf[["neighborhood_id", "neighborhood_name", "geometry"]],
        how="left",
        predicate="within",
    )

    grouped = join.groupby(["neighborhood_id", "neighborhood_name"], as_index=False)
    agg = _apply_groupby_agg(grouped, agg_column, agg_method)

    grid_gdf = neighborhoods_gdf.merge(
        agg, on=["neighborhood_id", "neighborhood_name"], how="inner"
    )
    return agg, grid_gdf


def _aggregate_to_square_grid(
    df_points: pd.DataFrame, agg_column: str, agg_method: str, cell_size: float
) -> tuple[pd.DataFrame, gpd.GeoDataFrame]:
    minx, miny = df_points["x"].min(), df_points["y"].min()
    gx = ((df_points["x"] - minx) // cell_size).astype(int)
    gy = ((df_points["y"] - miny) // cell_size).astype(int)
    df = df_points.assign(gx=gx, gy=gy)

    grouped = df.groupby(["gx", "gy"], as_index=False)
    grp = _apply_groupby_agg(grouped, agg_column, agg_method)

    polys = [
        Polygon(
            [
                (minx + row["gx"] * cell_size, miny + row["gy"] * cell_size),
                (minx + (row["gx"] + 1) * cell_size, miny + row["gy"] * cell_size),
                (
                    minx + (row["gx"] + 1) * cell_size,
                    miny + (row["gy"] + 1) * cell_size,
                ),
                (minx + row["gx"] * cell_size, miny + (row["gy"] + 1) * cell_size),
            ]
        )
        for _, row in grp.iterrows()
    ]

    grid_gdf = gpd.GeoDataFrame(grp.copy(), geometry=polys, crs=f"EPSG:{SOURCE_EPSG}")
    return grp, grid_gdf


def _hexagon(center_x: float, center_y: float, r: float) -> Polygon:
    pts = [
        (
            center_x + r * math.cos(math.radians(a)),
            center_y + r * math.sin(math.radians(a)),
        )
        for a in range(0, 360, 60)
    ]
    return Polygon(pts)


def _aggregate_to_hex_grid(
    df_points: pd.DataFrame, agg_column: str, agg_method: str, cell_size: float
) -> tuple[pd.DataFrame, gpd.GeoDataFrame]:
    pts_gdf = gpd.GeoDataFrame(
        df_points.copy(),
        geometry=gpd.points_from_xy(df_points["x"], df_points["y"]),
        crs=f"EPSG:{SOURCE_EPSG}",
    )

    minx, miny, maxx, maxy = pts_gdf.total_bounds
    hex_grid = _generate_hex_grid(minx, miny, maxx, maxy, cell_size)

    join = gpd.sjoin(
        pts_gdf, hex_grid[["cell_id", "geometry"]], how="left", predicate="within"
    )

    grouped = join.groupby("cell_id", as_index=False)
    agg = _apply_groupby_agg(grouped, agg_column, agg_method)

    grid_gdf = hex_grid.merge(agg, on="cell_id", how="inner")
    return agg, grid_gdf


def _generate_hex_grid(
    minx: float, miny: float, maxx: float, maxy: float, cell_size: float
) -> gpd.GeoDataFrame:
    R = cell_size / math.sqrt(3.0)
    dx = 1.5 * R
    dy = cell_size

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
    if gdf is None or len(gdf) == 0 or value_column not in gdf.columns:
        return ""

    gdf_viz = project_to_visualization_crs(gdf)

    if clip_quantile is not None and vmax is None:
        q_val = gdf[value_column].quantile(clip_quantile)
        if pd.notna(q_val):
            vmax = float(q_val)

    fig, ax = plt.subplots(figsize=figsize)

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

    fig.colorbar(
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

    _add_basemap(ax, gdf_viz)

    if show_pt_overlay and stops_df is not None:
        pt_handles, pt_labels = add_pt_network_overlay(
            ax,
            stops_df=stops_df,
            nodes_df=nodes_df,
            pt_links_df=pt_links_df,
            show_network=(nodes_df is not None and pt_links_df is not None),
            show_stops=True,
            add_to_legend=True,
        )

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


def _add_basemap(ax, gdf_3857, source=None, bounds=None):
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
    if stops_df is None or stops_df.empty:
        return stops_df

    coords = stops_df[["x", "y"]].values

    if len(coords) == 1:
        return stops_df

    distances = pdist(coords, metric="euclidean")
    linkage_matrix = linkage(distances, method="complete")
    cluster_labels = fcluster(linkage_matrix, cluster_distance, criterion="distance")

    clustered_stops = []
    for cluster_id in np.unique(cluster_labels):
        cluster_mask = cluster_labels == cluster_id
        cluster_coords = coords[cluster_mask]
        clustered_stops.append(
            {"x": cluster_coords[:, 0].mean(), "y": cluster_coords[:, 1].mean()}
        )

    return pd.DataFrame(clustered_stops)


def add_pt_network_overlay(
    ax,
    stops_df: pd.DataFrame | None = None,
    nodes_df: pd.DataFrame | None = None,
    pt_links_df: pd.DataFrame | None = None,
    show_network: bool = True,
    show_stops: bool = True,
    cluster_stops: bool = True,
    cluster_distance: float = 125.0,
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
    handles = []
    labels = []

    if (
        show_network
        and pt_links_df is not None
        and nodes_df is not None
        and not pt_links_df.empty
        and not nodes_df.empty
    ):
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

        pt_links_with_coords = pt_links_with_coords.dropna(
            subset=["from_x", "from_y", "to_x", "to_y"]
        )

        if not pt_links_with_coords.empty:
            pt_link_geoms = [
                LineString([(row["from_x"], row["from_y"]), (row["to_x"], row["to_y"])])
                for _, row in pt_links_with_coords.iterrows()
            ]

            pt_links_gdf = gpd.GeoDataFrame(
                pt_links_with_coords, geometry=pt_link_geoms, crs=f"EPSG:{SOURCE_EPSG}"
            )
            pt_links_viz = project_to_visualization_crs(pt_links_gdf)

            pt_links_viz.plot(
                ax=ax,
                color=network_color,
                linewidth=network_linewidth,
                alpha=network_alpha,
                zorder=zorder_network,
            )

            if add_to_legend:
                handles.append(
                    Line2D(
                        [0],
                        [0],
                        color=network_color,
                        linewidth=network_linewidth,
                        alpha=network_alpha,
                        label="PT network",
                    )
                )
                labels.append("PT network")

    if show_stops and stops_df is not None and not stops_df.empty:
        stops_to_plot = (
            _cluster_nearby_stops(stops_df, cluster_distance)
            if cluster_stops
            else stops_df
        )

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
            handles.append(
                Line2D(
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
            )
            labels.append("PT stops")

    return handles, labels


def _load_hannover_neighborhoods(data_path: str) -> gpd.GeoDataFrame:
    base_path = os.path.join(data_path, "admin_units", "Mikrobezirke")
    shapefile_path = os.path.join(base_path, "SKH5_Mikrobezirke_BA.shp")
    gdf = gpd.read_file(shapefile_path)

    neighborhoods = gdf[["MIKROBZ_BA", "STADTBZNAM", "geometry"]].copy()
    neighborhoods = neighborhoods.rename(
        columns={"MIKROBZ_BA": "neighborhood_id", "STADTBZNAM": "neighborhood_name"}
    )
    return ensure_source_crs(neighborhoods)


def _load_mikrobezirk_density(data_path: str) -> tuple[gpd.GeoDataFrame, dict]:
    neighborhoods = _load_hannover_neighborhoods(data_path)
    shp_path = os.path.join(
        data_path, "admin_units", "Mikrobezirke", "SKH5_Mikrobezirke_BA.shp"
    )
    raw_gdf = gpd.read_file(shp_path)
    area_map = dict(
        zip(
            raw_gdf["MIKROBZ_BA"].astype(str).str.strip(),
            raw_gdf["SHAPE_Area"].astype(float),
        )
    )

    excel_path = os.path.join(data_path, "census", "Age_gender_MBZ.xlsx")
    df_pop = pd.read_excel(
        excel_path, sheet_name="Altersgruppen MBZ Geschlecht", skiprows=7, header=None
    )
    df_pop = df_pop[~df_pop[0].astype(str).str.contains("Gesamt", na=False)]
    df_pop = df_pop[df_pop[0].astype(str).str.match(r"^\d+$")]
    # 1 code column + 9 male age groups + 9 female age groups = index 19 is total
    total_col = 1 + 9 * 2
    pop_map = dict(
        zip(
            df_pop.iloc[:, 0].astype(str).str.strip(),
            pd.to_numeric(df_pop.iloc[:, total_col], errors="coerce").fillna(0),
        )
    )

    density = {}
    for code in set(list(area_map.keys()) + list(pop_map.keys())):
        a = area_map.get(code, 0.0)
        p = pop_map.get(code, 0.0)
        density[code] = p / (a / 1e6) if a > 0 else 0.0

    return neighborhoods, density


def compute_access_times_teleportation(
    homes_df: pd.DataFrame,
    stops_df: pd.DataFrame,
    mode: str,
    max_distance_m: float = 5000,
) -> np.ndarray:
    print(
        f"INFO computing {mode} access times: {len(homes_df)} homes, {len(stops_df)} stops"
    )
    home_coords = homes_df[["x", "y"]].values
    stop_coords = stops_df[["x", "y"]].values
    euclidean_dists = cdist(home_coords, stop_coords, metric="euclidean")

    mode_speed = MODE_SPEEDS_MPS[mode]
    travel_times = (euclidean_dists * BEELINE_DISTANCE_FACTOR / mode_speed) / 60.0
    travel_times[euclidean_dists > max_distance_m] = np.inf

    print(f"  Max distance threshold: {max_distance_m}m")
    return travel_times


def compute_access_only_accessibility(
    travel_time_matrix: np.ndarray, beta: float
) -> np.ndarray:
    scores = np.exp(-beta * travel_time_matrix).sum(axis=1)
    print(
        f"  Accessibility scores: mean={scores.mean():.2f}, "
        f"median={np.median(scores):.2f}, "
        f"range=[{scores.min():.2f}, {scores.max():.2f}]"
    )
    return scores


def _plot_mode_comparison(
    persons_df: pd.DataFrame,
    analysis_path: str,
    modes: list,
    score_col_template: str,
    output_name: str,
    title_suffix: str,
    cbar_label: str,
    diff_subtitle: str = "",
) -> str:
    raw_by_mode: dict[str, pd.DataFrame] = {}
    pooled_log: list[np.ndarray] = []
    for mode in modes:
        col = score_col_template.format(mode=mode)
        if col not in persons_df.columns:
            continue
        valid = persons_df[
            persons_df[col].notna() & np.isfinite(persons_df[col])
        ].copy()
        if valid.empty:
            continue
        raw_by_mode[mode] = valid
        pooled_log.append(np.log1p(valid[col].to_numpy()))

    if not pooled_log:
        return ""

    pooled = np.concatenate(pooled_log)
    L_min, L_max = float(pooled.min()), float(pooled.max())

    def _norm(values):
        lv = np.log1p(values)
        return np.zeros_like(lv) if L_max <= L_min else (lv - L_min) / (L_max - L_min)

    fig, axes = plt.subplots(1, 3, figsize=(21, 6))

    for idx, mode in enumerate(modes):
        ax = axes[idx]
        col = score_col_template.format(mode=mode)
        if mode not in raw_by_mode:
            ax.set_visible(False)
            continue

        valid = raw_by_mode[mode]
        gdf = gpd.GeoDataFrame(
            valid,
            geometry=gpd.points_from_xy(valid["x"], valid["y"]),
            crs=f"EPSG:{SOURCE_EPSG}",
        ).to_crs(f"EPSG:{BASEMAP_EPSG}")

        sc = ax.scatter(
            gdf.geometry.x,
            gdf.geometry.y,
            c=_norm(valid[col].to_numpy()),
            cmap=MAP_CMAP,
            s=1,
            alpha=0.6,
            vmin=0.0,
            vmax=1.0,
        )
        try:
            cx.add_basemap(ax, source=cx.providers.CartoDB.Positron, alpha=0.5)
        except Exception:
            pass

        mode_label = "Micromobility" if mode == "bike" else mode.capitalize()
        ax.set_title(f"{mode_label} {title_suffix}", fontsize=14, fontweight="bold")
        ax.set_xlabel("")
        ax.set_ylabel("")
        ax.set_aspect("equal")
        plt.colorbar(sc, ax=ax).set_label(cbar_label, fontsize=10)

    diff_ax = axes[2]
    if len(modes) >= 2 and modes[0] in raw_by_mode and modes[1] in raw_by_mode:
        walk_col = score_col_template.format(mode=modes[0])
        mm_col = score_col_template.format(mode=modes[1])
        common = persons_df[
            persons_df[walk_col].notna()
            & np.isfinite(persons_df[walk_col])
            & persons_df[mm_col].notna()
            & np.isfinite(persons_df[mm_col])
        ].copy()
        diff = common[mm_col].to_numpy() - common[walk_col].to_numpy()

        gdf_diff = gpd.GeoDataFrame(
            common,
            geometry=gpd.points_from_xy(common["x"], common["y"]),
            crs=f"EPSG:{SOURCE_EPSG}",
        ).to_crs(f"EPSG:{BASEMAP_EPSG}")

        title = "Difference (Micromobility - Walk)"
        if diff_subtitle:
            title += f"\n{diff_subtitle}"

        sc_d = diff_ax.scatter(
            gdf_diff.geometry.x,
            gdf_diff.geometry.y,
            c=diff,
            cmap=MAP_CMAP,
            s=1,
            alpha=0.6,
            vmin=0.0,
            vmax=diff.max(),
        )
        try:
            cx.add_basemap(diff_ax, source=cx.providers.CartoDB.Positron, alpha=0.5)
        except Exception:
            pass
        diff_ax.set_title(title, fontsize=14, fontweight="bold")
        diff_ax.set_xlabel("")
        diff_ax.set_ylabel("")
        diff_ax.set_aspect("equal")
        plt.colorbar(sc_d, ax=diff_ax).set_label("Raw score difference", fontsize=10)
    else:
        diff_ax.set_visible(False)

    plt.tight_layout()
    output_file = os.path.join(analysis_path, f"{OUTPUT_PREFIX}_{output_name}.png")
    plt.savefig(output_file, dpi=PLOT_DPI, bbox_inches="tight")
    plt.close()
    print(f"SUCCESS: Created {output_file}")
    return output_file


def plot_access_only_comparison(
    persons_df: pd.DataFrame,
    analysis_path: str,
    modes: list | None = None,
) -> str:
    return _plot_mode_comparison(
        persons_df,
        analysis_path,
        modes or ACCESS_MODES,
        score_col_template="accessibility_{mode}",
        output_name="access_only_comparison",
        title_suffix="Access",
        cbar_label="Access-only score (log1p norm.)",
    )


def extract_stop_headways(schedule_path: str) -> dict:
    if schedule_path.endswith(".gz"):
        with gzip.open(schedule_path, "rb") as f:
            tree = ET.parse(f)
    else:
        tree = ET.parse(schedule_path)
    root = tree.getroot()

    def _parse_hms(t):
        h, m, s = t.split(":")
        return int(h) * 3600 + int(m) * 60 + int(s)

    stop_headways: dict[str, list[float]] = {}

    for line in root.findall(".//transitLine"):
        for route in line.findall("transitRoute"):
            route_stop_ids = [
                s.attrib["refId"]
                for s in route.findall("routeProfile/stop")
                if "refId" in s.attrib
            ]
            if not route_stop_ids:
                continue

            dep_secs = sorted(
                _parse_hms(d.attrib["departureTime"])
                for d in route.findall("departures/departure")
                if "departureTime" in d.attrib
            )
            if len(dep_secs) < 2:
                continue

            intervals = [
                dep_secs[i + 1] - dep_secs[i] for i in range(len(dep_secs) - 1)
            ]
            intervals = [iv for iv in intervals if 0 < iv <= 7200]
            if not intervals:
                continue
            headway_s = statistics.median(intervals)

            for sid in route_stop_ids:
                stop_headways.setdefault(sid, []).append(headway_s)

    # wait = min headway / 2 (seconds -> minutes)
    result = {
        sid: min(headways) / 2.0 / 60.0 for sid, headways in stop_headways.items()
    }
    print(f"INFO extract_stop_headways: {len(result)} stops")
    return result


def extract_empirical_ride_times(
    legs_df: pd.DataFrame,
    pt_df: pd.DataFrame,
    min_obs: int = 5,
) -> dict:
    if legs_df.empty or pt_df.empty:
        return {}

    pt_legs = legs_df[legs_df["mode"] == "pt"][
        ["person_id", "person_trip_id", "leg_index", "travel_time"]
    ].copy()
    pt_legs["travel_time"] = pd.to_numeric(pt_legs["travel_time"], errors="coerce")

    merged = pt_df[
        ["person_id", "person_trip_id", "leg_index", "access_area_id"]
    ].merge(pt_legs, on=["person_id", "person_trip_id", "leg_index"], how="inner")
    merged = merged.dropna(subset=["travel_time", "access_area_id"])
    merged = merged[merged["travel_time"] > 0]

    merged["area_key"] = merged["access_area_id"].apply(_area_key)
    merged = merged.dropna(subset=["area_key"])

    agg = merged.groupby("area_key").agg(
        ride_s=("travel_time", "mean"),
        n_obs=("travel_time", "count"),
    )

    global_median_min = float(agg["ride_s"].median()) / 60.0

    result: dict[str, float] = {}
    for area_key, row in agg.iterrows():
        ride_min = (
            float(row["ride_s"]) / 60.0
            if row["n_obs"] >= min_obs
            else global_median_min
        )
        result[area_key] = ride_min

    print(
        f"INFO extract_empirical_ride_times: {len(agg)} areas, "
        f"{(agg['n_obs'] < min_obs).sum()} fell back to median ({global_median_min:.1f} min)"
    )
    return result


def extract_transfer_penalties(
    legs_df: pd.DataFrame,
    pt_df: pd.DataFrame,
    min_obs: int = 5,
) -> dict:
    # Counts PT legs per trip; transfers = n_pt_legs - 1, attributed to boarding stop.
    # Returned values are already multiplied by TRANSFER_PENALTY_MIN.
    if legs_df.empty or pt_df.empty:
        return {}

    pt_leg_counts = (
        legs_df[legs_df["mode"] == "pt"]
        .groupby(["person_id", "person_trip_id"])
        .size()
        .reset_index(name="n_pt_legs")
    )

    pt_clean = pt_df[
        ["person_id", "person_trip_id", "leg_index", "access_area_id"]
    ].dropna(subset=["access_area_id"])
    first_boarding = (
        pt_clean.sort_values("leg_index")
        .groupby(["person_id", "person_trip_id"], sort=False)
        .first()
        .reset_index()
    )

    merged = first_boarding.merge(
        pt_leg_counts, on=["person_id", "person_trip_id"], how="inner"
    )
    merged["n_transfers"] = (merged["n_pt_legs"] - 1).clip(lower=0)
    merged["area_key"] = merged["access_area_id"].apply(_area_key)
    merged = merged.dropna(subset=["area_key"])

    agg = merged.groupby("area_key").agg(
        mean_transfers=("n_transfers", "mean"),
        n_obs=("n_transfers", "count"),
    )

    global_mean = float(agg["mean_transfers"].mean())

    result: dict[str, float] = {}
    for area_key, row in agg.iterrows():
        n = float(row["mean_transfers"]) if row["n_obs"] >= min_obs else global_mean
        result[area_key] = n * TRANSFER_PENALTY_MIN

    print(
        f"INFO extract_transfer_penalties: {len(agg)} areas, "
        f"global mean {global_mean:.2f} transfers/trip "
        f"({global_mean * TRANSFER_PENALTY_MIN:.1f} min penalty)"
    )
    return result


def enrich_stops_with_service(
    stops_df: pd.DataFrame,
    wait_by_stop_id: dict,
    ride_by_area_id: dict,
    transfer_by_area_id: dict | None = None,
) -> pd.DataFrame:
    df = stops_df.copy()

    df["wait_min"] = df["stop_id"].map(wait_by_stop_id)
    global_wait = (
        float(np.nanmedian(list(wait_by_stop_id.values()))) if wait_by_stop_id else 5.0
    )
    df["wait_min"] = df["wait_min"].fillna(global_wait)

    area_key_col = df["stopAreaId"].apply(_area_key)
    df["ride_min"] = area_key_col.map(ride_by_area_id)
    global_ride = (
        float(np.nanmedian(list(ride_by_area_id.values()))) if ride_by_area_id else 14.0
    )
    df["ride_min"] = df["ride_min"].fillna(global_ride)

    if transfer_by_area_id:
        df["transfer_min"] = area_key_col.map(transfer_by_area_id)
        global_transfer = float(np.nanmedian(list(transfer_by_area_id.values())))
        df["transfer_min"] = df["transfer_min"].fillna(global_transfer)
    else:
        df["transfer_min"] = 0.0

    print(
        f"INFO enrich_stops_with_service: "
        f"wait={df['wait_min'].mean():.1f}, "
        f"ride={df['ride_min'].mean():.1f}, "
        f"transfer={df['transfer_min'].mean():.1f} min (means)"
    )
    return df


def compute_functional_cost_matrix(
    access_times: np.ndarray,
    stops_df: pd.DataFrame,
) -> np.ndarray:
    wait = stops_df["wait_min"].to_numpy(dtype=float)
    ride = stops_df["ride_min"].to_numpy(dtype=float)
    transfer = stops_df["transfer_min"].to_numpy(dtype=float)

    return (
        access_times
        + wait[np.newaxis, :]
        + ride[np.newaxis, :]
        + transfer[np.newaxis, :]
    )


def compute_functional_accessibility(
    access_times: np.ndarray,
    stops_df: pd.DataFrame,
    beta: float,
    opportunity_weights: np.ndarray | None = None,
) -> np.ndarray:
    total_cost = compute_functional_cost_matrix(access_times, stops_df)
    decay = np.exp(-beta * total_cost)

    if opportunity_weights is not None:
        decay = decay * opportunity_weights[np.newaxis, :]

    scores = decay.sum(axis=1)
    label = (
        "Pop-weighted functional" if opportunity_weights is not None else "Functional"
    )
    print(
        f"  {label} scores: mean={scores.mean():.2f}, "
        f"median={np.median(scores):.2f}, "
        f"range=[{scores.min():.2f}, {scores.max():.2f}]"
    )
    return scores


def plot_functional_comparison(
    persons_df: pd.DataFrame,
    analysis_path: str,
    modes: list | None = None,
) -> str:
    return _plot_mode_comparison(
        persons_df,
        analysis_path,
        modes or ACCESS_MODES,
        score_col_template="accessibility_{mode}_functional",
        output_name="functional_comparison",
        title_suffix="Functional Access",
        cbar_label="Functional score (log1p norm.)",
    )


def assign_stop_population_density(
    stops_df: pd.DataFrame,
    data_path: str,
) -> pd.DataFrame:
    neighborhoods, density_map = _load_mikrobezirk_density(data_path)

    stops_gdf = gpd.GeoDataFrame(
        stops_df.copy(),
        geometry=gpd.points_from_xy(stops_df["x"], stops_df["y"]),
        crs=f"EPSG:{SOURCE_EPSG}",
    )
    joined = gpd.sjoin(
        stops_gdf,
        neighborhoods[["neighborhood_id", "geometry"]],
        how="left",
        predicate="within",
    )
    joined["pop_density"] = (
        joined["neighborhood_id"].astype(str).str.strip().map(density_map).fillna(0.0)
    )
    joined = joined.drop_duplicates(subset="stop_id", keep="first")

    out = stops_df.copy()
    out["pop_density"] = (
        joined.set_index("stop_id")["pop_density"].reindex(out["stop_id"]).values
    )
    out["pop_density"] = out["pop_density"].fillna(0.0)

    n_zero = (out["pop_density"] == 0).sum()
    print(
        f"INFO [pop-density] {len(out)} stops: "
        f"mean={out['pop_density'].mean():.0f}, "
        f"median={out['pop_density'].median():.0f}, "
        f"max={out['pop_density'].max():.0f} p/km2 ({n_zero} with 0)"
    )
    return out


def _build_stop_area_density_map(
    stops_df: pd.DataFrame,
    data_path: str,
) -> dict[str, float]:
    neighborhoods, density_map = _load_mikrobezirk_density(data_path)

    stops_gdf = gpd.GeoDataFrame(
        stops_df[["stop_id", "x", "y", "stopAreaId"]].copy(),
        geometry=gpd.points_from_xy(stops_df["x"], stops_df["y"]),
        crs=f"EPSG:{SOURCE_EPSG}",
    )
    joined = gpd.sjoin(
        stops_gdf,
        neighborhoods[["neighborhood_id", "geometry"]],
        how="left",
        predicate="within",
    )
    joined["_dens"] = (
        joined["neighborhood_id"].astype(str).str.strip().map(density_map).fillna(0.0)
    )
    joined = joined.drop_duplicates(subset="stop_id", keep="first")

    joined["_area_key"] = joined["stopAreaId"].apply(_area_key)
    area_dens = joined.dropna(subset=["_area_key"]).groupby("_area_key")["_dens"].mean()
    return area_dens.to_dict()


def compute_destination_pop_density(
    stops_df: pd.DataFrame,
    pt_df: pd.DataFrame,
    data_path: str,
    min_obs: int = 5,
) -> np.ndarray:
    # Uses empirical egress destinations from eqasim_pt to find where trips go,
    # then looks up destination Mikrobezirk pop density.
    area_density = _build_stop_area_density_map(stops_df, data_path)

    if pt_df.empty:
        print("WARNING [dest-pop-density] eqasim_pt is empty, returning zeros")
        return np.zeros(len(stops_df), dtype=float)

    df = pt_df[["access_area_id", "egress_area_id"]].dropna().copy()
    df["access_key"] = df["access_area_id"].apply(_area_key)
    df["egress_key"] = df["egress_area_id"].apply(_area_key)
    df["dest_dens"] = df["egress_key"].map(area_density).fillna(0.0)

    agg = df.groupby("access_key").agg(
        mean_dest_dens=("dest_dens", "mean"),
        n_obs=("dest_dens", "count"),
    )
    global_median = float(agg["mean_dest_dens"].median()) if len(agg) > 0 else 0.0

    dest_dens_map: dict[str, float] = {}
    for area_key, row in agg.iterrows():
        dest_dens_map[area_key] = (
            float(row["mean_dest_dens"]) if row["n_obs"] >= min_obs else global_median
        )

    result = np.full(len(stops_df), global_median, dtype=float)
    for i, area_id in enumerate(stops_df["stopAreaId"]):
        key = _area_key(area_id)
        if key and key in dest_dens_map:
            result[i] = dest_dens_map[key]

    n_matched = sum(1 for v in result if v != global_median)
    print(
        f"INFO [dest-pop-density] {len(stops_df)} stops: "
        f"mean={result.mean():.0f}, median={np.median(result):.0f}, "
        f"max={result.max():.0f} p/km2 "
        f"({n_matched} matched, {len(stops_df) - n_matched} fallback)"
    )
    return result


def plot_functional_comparison_dest_pop(
    persons_df: pd.DataFrame,
    analysis_path: str,
    modes: list | None = None,
) -> str:
    return _plot_mode_comparison(
        persons_df,
        analysis_path,
        modes or ACCESS_MODES,
        score_col_template="accessibility_{mode}_functional_dest_pop",
        output_name="functional_comparison_dest_pop_weighted",
        title_suffix="Functional Access\n(dest pop-density weighted)",
        cbar_label="Dest pop-weighted score (log1p norm.)",
        diff_subtitle="(dest pop-density weighted)",
    )


def plot_functional_comparison_pop_weighted(
    persons_df: pd.DataFrame,
    analysis_path: str,
    modes: list | None = None,
) -> str:
    return _plot_mode_comparison(
        persons_df,
        analysis_path,
        modes or ACCESS_MODES,
        score_col_template="accessibility_{mode}_functional_pop",
        output_name="functional_comparison_pop_weighted",
        title_suffix="Functional Access\n(pop-density weighted)",
        cbar_label="Pop-weighted score (log1p norm.)",
        diff_subtitle="(pop-density weighted)",
    )


EMP_CATCHMENT_RADIUS_M = 500.0


def _build_stop_area_facility_count(
    stops_df: pd.DataFrame,
    sim_dir: str,
    radius_m: float = EMP_CATCHMENT_RADIUS_M,
) -> dict[str, float]:
    """Map stopAreaId → number of work facilities within *radius_m*.

    Work facilities are unique locations from ``eqasim_activities.csv``
    (purpose='work', deduplicated by facility_id).
    """
    acts_path = os.path.join(sim_dir, "eqasim_activities.csv")
    if not os.path.exists(acts_path):
        raise FileNotFoundError(f"eqasim_activities.csv not found in {sim_dir}")

    acts = pd.read_csv(acts_path, sep=";")
    work = acts.loc[acts["purpose"] == "work", ["facility_id", "x", "y"]].copy()
    fac = work.drop_duplicates("facility_id")[["x", "y"]].values

    # Deduplicate stops to one representative per stopAreaId
    area_stops = (
        stops_df[["stopAreaId", "x", "y"]]
        .drop_duplicates("stopAreaId")
        .reset_index(drop=True)
    )
    stop_coords = area_stops[["x", "y"]].values
    dists = cdist(stop_coords, fac)
    counts = (dists <= radius_m).sum(axis=1).astype(float)

    area_map: dict[str, float] = {}
    for i, area_id in enumerate(area_stops["stopAreaId"]):
        key = _area_key(area_id)
        if key:
            area_map[key] = counts[i]

    print(
        f"INFO [facility-count] {len(fac)} work facilities, "
        f"{len(area_map)} stop areas (r={radius_m:.0f}m): "
        f"mean={np.mean(counts):.1f}, max={np.max(counts):.0f}"
    )
    return area_map


def compute_destination_employment(
    stops_df: pd.DataFrame,
    pt_df: pd.DataFrame,
    sim_dir: str,
    radius_m: float = EMP_CATCHMENT_RADIUS_M,
    min_obs: int = 5,
) -> np.ndarray:
    """Destination-side employment weight per boarding stop.

    For each boarding stop area, look at the empirical egress stop areas
    (from ``eqasim_pt``), count the work facilities within *radius_m* of
    those destination stops, and take the mean.  This answers: 'if I board
    at this stop, how many workplaces can I reach on foot after alighting?'

    Returns an array of length ``len(stops_df)``.
    """
    area_fac_count = _build_stop_area_facility_count(stops_df, sim_dir, radius_m)

    if pt_df.empty:
        print("WARNING [dest-employment] eqasim_pt is empty, returning zeros")
        return np.zeros(len(stops_df), dtype=float)

    df = pt_df[["access_area_id", "egress_area_id"]].dropna().copy()
    df["access_key"] = df["access_area_id"].apply(_area_key)
    df["egress_key"] = df["egress_area_id"].apply(_area_key)
    df["dest_fac"] = df["egress_key"].map(area_fac_count).fillna(0.0)

    agg = df.groupby("access_key").agg(
        mean_dest_fac=("dest_fac", "mean"),
        n_obs=("dest_fac", "count"),
    )
    global_median = float(agg["mean_dest_fac"].median()) if len(agg) > 0 else 0.0

    dest_emp_map: dict[str, float] = {}
    for area_key, row in agg.iterrows():
        dest_emp_map[area_key] = (
            float(row["mean_dest_fac"]) if row["n_obs"] >= min_obs else global_median
        )

    result = np.full(len(stops_df), global_median, dtype=float)
    for i, area_id in enumerate(stops_df["stopAreaId"]):
        key = _area_key(area_id)
        if key and key in dest_emp_map:
            result[i] = dest_emp_map[key]

    n_matched = sum(1 for v in result if v != global_median)
    print(
        f"INFO [dest-employment] {len(stops_df)} stops: "
        f"mean={result.mean():.1f}, median={np.median(result):.1f}, "
        f"max={result.max():.0f} facilities "
        f"({n_matched} matched, {len(stops_df) - n_matched} fallback)"
    )
    return result


def plot_functional_comparison_pop_emp(
    persons_df: pd.DataFrame,
    analysis_path: str,
    modes: list | None = None,
) -> str:
    return _plot_mode_comparison(
        persons_df,
        analysis_path,
        modes or ACCESS_MODES,
        score_col_template="accessibility_{mode}_functional_pop_emp",
        output_name="functional_comparison_pop_weighted_employment",
        title_suffix="Functional Access\n(pop-density \u00d7 employment weighted)",
        cbar_label="Pop \u00d7 emp weighted score (log1p norm.)",
        diff_subtitle="(pop-density \u00d7 employment)",
    )


def plot_functional_comparison_dest_pop_emp(
    persons_df: pd.DataFrame,
    analysis_path: str,
    modes: list | None = None,
) -> str:
    return _plot_mode_comparison(
        persons_df,
        analysis_path,
        modes or ACCESS_MODES,
        score_col_template="accessibility_{mode}_functional_dest_pop_emp",
        output_name="functional_comparison_dest_pop_weighted_employment",
        title_suffix="Functional Access\n(dest pop-density \u00d7 employment weighted)",
        cbar_label="Dest-pop \u00d7 emp weighted score (log1p norm.)",
        diff_subtitle="(dest pop-density \u00d7 employment)",
    )


def compute_catchment_areas(
    access_times_dict: dict,
    stops_df: pd.DataFrame,
    thresholds: dict,
) -> pd.DataFrame:
    print("INFO computing catchment areas")
    stops_enriched = stops_df.copy()

    for mode, threshold_min in thresholds.items():
        if mode not in access_times_dict:
            continue

        travel_times = access_times_dict[mode]
        within_threshold = travel_times <= threshold_min
        catchment_sizes = within_threshold.sum(axis=0)
        stops_enriched[f"catchment_{mode}"] = catchment_sizes
        n_with = len(stops_enriched[stops_enriched[f"catchment_{mode}"] > 0])
        print(f"  {mode.capitalize()}: {n_with} stops with catchments")

    return stops_enriched


def compute_expansion_factors(
    stops_df: pd.DataFrame,
    walk_col: str = "catchment_walk",
    bike_col: str = "catchment_bike",
    epsilon: float = 0.1,
) -> pd.DataFrame:
    print("INFO computing expansion factors")
    stops_enriched = stops_df.copy()

    walk_catchments = stops_enriched[walk_col]
    bike_catchments = stops_enriched[bike_col]

    expansion = (bike_catchments + epsilon) / (walk_catchments + epsilon)
    stops_enriched["expansion_factor"] = expansion
    stops_enriched["additional_people"] = bike_catchments - walk_catchments

    print(
        f"  Expansion: mean={expansion.mean():.2f}, "
        f"median={expansion.median():.2f}, max={expansion.max():.2f}"
    )
    return stops_enriched


def classify_person_coverage(
    persons_df: pd.DataFrame,
    access_times_dict: dict,
    thresholds: dict,
) -> pd.DataFrame:
    print("INFO classifying person-level PT coverage")
    persons_enriched = persons_df.copy()

    walk_times = access_times_dict["walk"].min(axis=1)
    bike_times = access_times_dict["bike"].min(axis=1)

    within_walk = walk_times <= thresholds["walk"]
    within_bike = bike_times <= thresholds["bike"]

    categories = []
    for w, b in zip(within_walk, within_bike):
        if w:
            categories.append("walk")
        elif b:
            categories.append("bike_only")
        else:
            categories.append("underserved")

    persons_enriched["coverage_category"] = pd.Categorical(
        categories, categories=["walk", "bike_only", "underserved"], ordered=True
    )

    counts = persons_enriched["coverage_category"].value_counts()
    total = len(persons_enriched)
    for cat in ["walk", "bike_only", "underserved"]:
        c = counts.get(cat, 0)
        print(f"  {cat}: {c:,} ({100 * c / total:.1f}%)")

    return persons_enriched


def _render_neighborhood_panel(
    ax,
    gdf,
    neigh_viz,
    value_col,
    vmax,
    title,
    cbar_label,
    stops_df=None,
    nodes_df=None,
    pt_links_df=None,
    show_missing_grey=False,
):
    plot_kwargs = dict(
        column=value_col,
        ax=ax,
        cmap="YlOrRd",
        vmin=0,
        vmax=vmax,
        legend=False,
        edgecolor="white",
        linewidth=0.3,
    )
    if show_missing_grey:
        plot_kwargs["missing_kwds"] = {"color": "lightgrey"}

    gdf.plot(**plot_kwargs)
    neigh_viz.boundary.plot(ax=ax, linewidth=0.4, color="grey", alpha=0.5)

    try:
        cx.add_basemap(ax, source=cx.providers.CartoDB.Positron, alpha=0.4)
    except Exception:
        pass

    pt_handles, pt_labels = add_pt_network_overlay(
        ax,
        stops_df=stops_df,
        nodes_df=nodes_df,
        pt_links_df=pt_links_df,
        show_network=True,
        show_stops=True,
        add_to_legend=True,
    )

    if pt_handles:
        ax.legend(
            handles=pt_handles,
            labels=pt_labels,
            loc="upper right",
            fontsize=8,
            framealpha=0.9,
        )

    ax.set_title(title, fontsize=12, fontweight="bold")
    ax.set_xlabel("")
    ax.set_ylabel("")
    ax.set_aspect("equal")

    sm = plt.cm.ScalarMappable(cmap="YlOrRd", norm=plt.Normalize(vmin=0, vmax=vmax))
    sm.set_array([])
    cbar = plt.colorbar(sm, ax=ax, shrink=0.6)
    cbar.set_label(cbar_label, fontsize=9)


def plot_person_coverage_map(
    persons_df: pd.DataFrame,
    stops_df: pd.DataFrame,
    analysis_path: str,
    thresholds: dict,
    nodes_df: pd.DataFrame | None = None,
    pt_links_df: pd.DataFrame | None = None,
    type_label: str = "access_only",
) -> str:
    print("INFO creating person coverage map")

    df = persons_df.dropna(subset=["x", "y", "coverage_category"]).copy()
    if df.empty:
        print("  WARNING: No valid data for coverage map")
        return ""

    color_map = {
        "walk": "#9467bd",
        "bike_only": "#ff7f0e",
        "underserved": "#d62728",
    }
    colors = df["coverage_category"].map(color_map)

    persons_gdf = gpd.GeoDataFrame(
        df, geometry=gpd.points_from_xy(df["x"], df["y"]), crs=f"EPSG:{SOURCE_EPSG}"
    )
    persons_viz = persons_gdf.to_crs(f"EPSG:{BASEMAP_EPSG}")

    fig, ax = plt.subplots(figsize=(12, 12))

    ax.scatter(
        persons_viz.geometry.x,
        persons_viz.geometry.y,
        c=colors,
        s=1,
        alpha=0.6,
        rasterized=True,
    )

    try:
        cx.add_basemap(ax, source=cx.providers.CartoDB.Positron, alpha=0.5)
    except Exception:
        pass

    handles, labels = add_pt_network_overlay(
        ax,
        stops_df=stops_df,
        nodes_df=nodes_df,
        pt_links_df=pt_links_df,
        show_network=True,
        show_stops=True,
        cluster_stops=True,
        add_to_legend=True,
    )

    legend_elements = [
        Patch(
            facecolor=color_map["walk"],
            label=f"Walk catchment (<={thresholds['walk']:.0f} min)",
        ),
        Patch(
            facecolor=color_map["bike_only"],
            label=f"Micromobility only (<={thresholds['bike']:.0f} min)",
        ),
        Patch(facecolor=color_map["underserved"], label="Underserved (no catchment)"),
    ]

    if handles:
        legend_elements.extend(handles)

    ax.legend(handles=legend_elements, loc="upper right", framealpha=0.9)

    _td = _TYPE_DISPLAY.get(type_label, type_label)
    ax.set_title(f"PT Coverage by Access Mode - {_td}", fontsize=14, fontweight="bold")
    ax.set_xlabel("")
    ax.set_ylabel("")
    ax.set_aspect("equal")

    plt.tight_layout()
    output_file = os.path.join(
        analysis_path, f"{OUTPUT_PREFIX}_{type_label}_person_coverage.png"
    )
    plt.savefig(output_file, dpi=PLOT_DPI, bbox_inches="tight")
    plt.close()

    print(f"SUCCESS: Created {output_file}")
    return output_file


def plot_expansion_factors(
    stops_df: pd.DataFrame,
    analysis_path: str,
    nodes_df: pd.DataFrame | None = None,
    pt_links_df: pd.DataFrame | None = None,
    type_label: str = "access_only",
) -> str:
    print("INFO creating expansion factor map")

    df = stops_df.dropna(subset=["x", "y", "expansion_factor"]).copy()
    if df.empty:
        print("  WARNING: No valid stop data for expansion factor map")
        return ""

    stops_gdf = gpd.GeoDataFrame(
        df, geometry=gpd.points_from_xy(df["x"], df["y"]), crs=f"EPSG:{SOURCE_EPSG}"
    )
    stops_viz = stops_gdf.to_crs(f"EPSG:{BASEMAP_EPSG}")

    fig, ax = plt.subplots(figsize=(12, 12))

    expansion = df["expansion_factor"]
    vmin = 1.0
    vmax = np.percentile(expansion, 95)

    scatter = ax.scatter(
        stops_viz.geometry.x,
        stops_viz.geometry.y,
        c=expansion,
        s=60,
        cmap="YlOrRd",
        vmin=vmin,
        vmax=vmax,
        alpha=0.8,
        edgecolors="black",
        linewidths=0.8,
        zorder=12,
    )

    try:
        cx.add_basemap(ax, source=cx.providers.CartoDB.Positron, alpha=0.5)
    except Exception:
        pass

    if nodes_df is not None and pt_links_df is not None:
        add_pt_network_overlay(
            ax,
            stops_df=stops_df,
            nodes_df=nodes_df,
            pt_links_df=pt_links_df,
            show_network=True,
            show_stops=False,
            add_to_legend=False,
        )

    cbar = plt.colorbar(scatter, ax=ax, shrink=0.6)
    cbar.set_label("Expansion Factor", fontsize=10)

    _td = _TYPE_DISPLAY.get(type_label, type_label)
    ax.set_title(
        f"Micromobility Expansion Factor - {_td}",
        fontsize=14,
        fontweight="bold",
    )
    ax.set_xlabel("")
    ax.set_ylabel("")
    ax.set_aspect("equal")

    plt.tight_layout()
    output_file = os.path.join(
        analysis_path, f"{OUTPUT_PREFIX}_{type_label}_expansion_factor.png"
    )
    plt.savefig(output_file, dpi=PLOT_DPI, bbox_inches="tight")
    plt.close()

    print(f"SUCCESS: Created {output_file}")
    return output_file


def plot_stop_catchments(
    stops_df: pd.DataFrame,
    analysis_path: str,
    modes: list | None = None,
    type_label: str = "access_only",
) -> str:
    print("INFO creating stop catchment maps")

    if modes is None:
        modes = ACCESS_MODES

    df = stops_df.dropna(subset=["x", "y"]).copy()
    if df.empty:
        print("  WARNING: No valid stop data for catchment maps")
        return ""

    stops_gdf = gpd.GeoDataFrame(
        df, geometry=gpd.points_from_xy(df["x"], df["y"]), crs=f"EPSG:{SOURCE_EPSG}"
    )
    stops_viz = stops_gdf.to_crs(f"EPSG:{BASEMAP_EPSG}")

    fig, axes = plt.subplots(1, 2, figsize=(20, 10))

    for idx, mode in enumerate(modes):
        ax = axes[idx]
        col_name = f"catchment_{mode}"

        if col_name not in df.columns:
            continue

        valid_data = df[df[col_name].notna()].copy()
        valid_viz = stops_viz.loc[valid_data.index]

        if len(valid_data) == 0:
            continue

        sizes = valid_data[col_name]
        vmin = 0
        vmax = np.percentile(sizes[sizes > 0], 95) if (sizes > 0).any() else sizes.max()

        scatter = ax.scatter(
            valid_viz.geometry.x,
            valid_viz.geometry.y,
            c=sizes,
            s=50,
            cmap="YlOrRd",
            vmin=vmin,
            vmax=vmax,
            alpha=0.7,
            edgecolors="black",
            linewidths=0.5,
        )

        try:
            cx.add_basemap(ax, source=cx.providers.CartoDB.Positron, alpha=0.5)
        except Exception:
            pass

        cbar = plt.colorbar(scatter, ax=ax, shrink=0.6)
        cbar.set_label("Catchment Size (# people)", fontsize=10)

        mode_label = "Micromobility" if mode == "bike" else mode.capitalize()
        print(
            f"  {mode_label}: {len(valid_data)} stops, "
            f"mean={sizes.mean():.0f}, median={sizes.median():.0f}, max={sizes.max():.0f}"
        )

        _td = _TYPE_DISPLAY.get(type_label, type_label)
        ax.set_title(
            f"{mode_label} Catchment Areas - {_td}",
            fontsize=12,
            fontweight="bold",
        )
        ax.set_xlabel("")
        ax.set_ylabel("")
        ax.set_aspect("equal")

    plt.tight_layout()
    output_file = os.path.join(
        analysis_path, f"{OUTPUT_PREFIX}_{type_label}_stop_catchments.png"
    )
    plt.savefig(output_file, dpi=PLOT_DPI, bbox_inches="tight")
    plt.close()

    print(f"SUCCESS: Created {output_file}")
    return output_file


def plot_stop_catchments_by_neighborhood(
    stops_df: pd.DataFrame,
    analysis_path: str,
    data_path: str,
    modes: list | None = None,
    nodes_df: pd.DataFrame | None = None,
    pt_links_df: pd.DataFrame | None = None,
    type_label: str = "access_only",
) -> str:
    print("INFO creating neighborhood-level catchment maps")

    if modes is None:
        modes = ACCESS_MODES

    try:
        neighborhoods_gdf = _load_hannover_neighborhoods(data_path)
    except Exception as exc:
        print(f"  WARNING: could not load neighborhoods: {exc}")
        return ""

    df = stops_df.dropna(subset=["x", "y"]).copy()
    if df.empty:
        print("  WARNING: no valid stop data for neighborhood plots")
        return ""

    stops_gdf = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df["x"], df["y"]),
        crs=f"EPSG:{SOURCE_EPSG}",
    )

    joined = gpd.sjoin(
        stops_gdf,
        neighborhoods_gdf[["neighborhood_id", "neighborhood_name", "geometry"]],
        how="left",
        predicate="within",
    )

    stops_per_neigh = (
        joined.dropna(subset=["neighborhood_id"]).groupby("neighborhood_id").size()
    )
    print(
        f"  Stops per Mikrobezirk: mean={stops_per_neigh.mean():.1f}, "
        f"median={stops_per_neigh.median():.0f}, "
        f"max={stops_per_neigh.max():.0f}, "
        f"1-stop={int((stops_per_neigh == 1).sum())} of {len(stops_per_neigh)}"
    )

    neigh_viz = neighborhoods_gdf.to_crs(f"EPSG:{BASEMAP_EPSG}")

    precomputed: dict[str, gpd.GeoDataFrame] = {}
    all_vals: list[float] = []

    for mode in modes:
        col_name = f"catchment_{mode}"
        if col_name not in joined.columns:
            continue

        sub = joined.dropna(subset=["neighborhood_id", col_name]).copy()
        agg = sub.groupby(["neighborhood_id", "neighborhood_name"], as_index=False).agg(
            value=(col_name, "mean")
        )

        merged = neigh_viz.merge(
            agg, on=["neighborhood_id", "neighborhood_name"], how="left"
        )
        merged["value"] = merged["value"].fillna(0.0)
        precomputed[mode] = merged
        all_vals.extend(merged["value"].tolist())

    shared_vmax = float(np.percentile(all_vals, 95)) if all_vals else 1.0

    _td = _TYPE_DISPLAY.get(type_label, type_label)

    # Figure 1: shared color scale across modes
    n_cols = len(modes)
    fig, axes = plt.subplots(1, n_cols, figsize=(10 * n_cols, 8))
    if n_cols == 1:
        axes = [axes]

    for col_idx, mode in enumerate(modes):
        ax = axes[col_idx]
        mode_label = "Micromobility" if mode == "bike" else mode.capitalize()

        if mode not in precomputed:
            ax.set_visible(False)
            continue

        _render_neighborhood_panel(
            ax,
            precomputed[mode],
            neigh_viz,
            "value",
            shared_vmax,
            f"{mode_label} - Mean Catchment",
            "Mean catchment size (# people)",
            stops_df=stops_df,
            nodes_df=nodes_df,
            pt_links_df=pt_links_df,
            show_missing_grey=True,
        )

    plt.suptitle(
        f"PT Stop Catchment by Neighborhood - {_td}",
        fontsize=16,
        fontweight="bold",
        y=1.01,
    )
    plt.tight_layout()

    output_file = os.path.join(
        analysis_path,
        f"{OUTPUT_PREFIX}_{type_label}_stop_catchments_by_neighborhood_norm.png",
    )
    plt.savefig(output_file, dpi=PLOT_DPI, bbox_inches="tight")
    plt.close()
    print(f"SUCCESS: Created {output_file}")

    # Figure 2: per-mode color scale
    fig2, axes2 = plt.subplots(1, n_cols, figsize=(10 * n_cols, 8))
    if n_cols == 1:
        axes2 = [axes2]

    for col_idx, mode in enumerate(modes):
        ax = axes2[col_idx]
        mode_label = "Micromobility" if mode == "bike" else mode.capitalize()

        if mode not in precomputed:
            ax.set_visible(False)
            continue

        mode_vmax = max(float(np.percentile(precomputed[mode]["value"], 95)), 1.0)

        _render_neighborhood_panel(
            ax,
            precomputed[mode],
            neigh_viz,
            "value",
            mode_vmax,
            f"{mode_label} - Mean Catchment",
            "Mean catchment size (# people)",
            stops_df=stops_df,
            nodes_df=nodes_df,
            pt_links_df=pt_links_df,
            show_missing_grey=True,
        )

    plt.suptitle(
        f"PT Stop Catchment by Neighborhood - {_td}",
        fontsize=16,
        fontweight="bold",
        y=1.01,
    )
    plt.tight_layout()

    output_file2 = os.path.join(
        analysis_path,
        f"{OUTPUT_PREFIX}_{type_label}_stop_catchments_by_neighborhood.png",
    )
    plt.savefig(output_file2, dpi=PLOT_DPI, bbox_inches="tight")
    plt.close()
    print(f"SUCCESS: Created {output_file2}")

    return output_file


def plot_resident_catchment_by_neighborhood(
    persons_df: pd.DataFrame,
    access_times_dict: dict,
    thresholds: dict,
    analysis_path: str,
    data_path: str,
    modes: list | None = None,
    stops_df: pd.DataFrame | None = None,
    nodes_df: pd.DataFrame | None = None,
    pt_links_df: pd.DataFrame | None = None,
    type_label: str = "access_only",
) -> str:
    print("INFO creating resident-level catchment-by-neighborhood maps")

    if modes is None:
        modes = ACCESS_MODES

    try:
        neighborhoods_gdf = _load_hannover_neighborhoods(data_path)
    except Exception as exc:
        print(f"  WARNING: could not load neighborhoods: {exc}")
        return ""

    pdf = persons_df[["x", "y"]].copy()
    pdf.index = range(len(pdf))
    pts_gdf = gpd.GeoDataFrame(
        pdf,
        geometry=gpd.points_from_xy(pdf["x"], pdf["y"]),
        crs=f"EPSG:{SOURCE_EPSG}",
    )
    joined = gpd.sjoin(
        pts_gdf,
        neighborhoods_gdf[["neighborhood_id", "neighborhood_name", "geometry"]],
        how="left",
        predicate="within",
    )

    for mode in modes:
        if mode not in access_times_dict:
            continue
        threshold = thresholds[mode]
        n_reachable = (access_times_dict[mode] <= threshold).sum(axis=1)
        joined[f"n_reachable_{mode}"] = n_reachable

    neigh_viz = neighborhoods_gdf.to_crs(f"EPSG:{BASEMAP_EPSG}")

    n_cols = len(modes)
    fig, axes = plt.subplots(1, n_cols, figsize=(10 * n_cols, 8))
    if n_cols == 1:
        axes = [axes]

    for col_idx, mode in enumerate(modes):
        ax = axes[col_idx]
        mode_label = "Micromobility" if mode == "bike" else mode.capitalize()
        threshold = thresholds.get(mode, "?")
        n_col = f"n_reachable_{mode}"

        if n_col not in joined.columns:
            ax.set_visible(False)
            continue

        count_agg = (
            joined.dropna(subset=["neighborhood_id"])
            .groupby(["neighborhood_id", "neighborhood_name"], as_index=False)
            .agg(mean_n=(n_col, "mean"))
        )
        merged_count = neigh_viz.merge(
            count_agg, on=["neighborhood_id", "neighborhood_name"], how="left"
        )
        merged_count["mean_n"] = merged_count["mean_n"].fillna(0.0)

        vmax_n = max(float(np.percentile(merged_count["mean_n"], 95)), 1.0)

        _render_neighborhood_panel(
            ax,
            merged_count,
            neigh_viz,
            "mean_n",
            vmax_n,
            f"{mode_label} - Mean reachable stops\nper resident (<={threshold} min)",
            "Mean # reachable stops",
            stops_df=stops_df,
            nodes_df=nodes_df,
            pt_links_df=pt_links_df,
        )

    _td = _TYPE_DISPLAY.get(type_label, type_label)
    plt.suptitle(
        f"Resident PT Reachability by Neighborhood - {_td}",
        fontsize=16,
        fontweight="bold",
        y=1.01,
    )
    plt.tight_layout()

    output_file = os.path.join(
        analysis_path,
        f"{OUTPUT_PREFIX}_{type_label}_resident_catchment_by_neighborhood.png",
    )
    plt.savefig(output_file, dpi=PLOT_DPI, bbox_inches="tight")
    plt.close()

    print(f"SUCCESS: Created {output_file}")
    return output_file


def configure(context):
    output_path = context.config("output_path")
    context.config("output_prefix")
    context.config("analysis_path")
    context.config("data_path")
    sim_output_dir = context.config("simulation_output_dir")

    sim_dir = os.path.join(output_path, sim_output_dir)
    schedule_xml = os.path.join(sim_dir, "output_transitSchedule.xml.gz")
    persons_sim_csv_gz = os.path.join(sim_dir, "output_persons.csv.gz")

    # if not (os.path.exists(schedule_xml) and os.path.exists(persons_sim_csv_gz)):
    #     context.stage("matsim.output")

    context.stage("data.hts.entd.reweighted")
    context.stage("analysis.hannover.ivt_style.analysis")


def _extract_stops_from_schedule(schedule_path: str) -> pd.DataFrame:
    if schedule_path.endswith(".gz"):
        with gzip.open(schedule_path, "rb") as f:
            tree = ET.parse(f)
    else:
        tree = ET.parse(schedule_path)
    root = tree.getroot()

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
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if network_path.endswith(".gz"):
        with gzip.open(network_path, "rb") as f:
            tree = ET.parse(f)
    else:
        tree = ET.parse(network_path)
    root = tree.getroot()

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
                nodes.append({"node_id": node_id, "x": float(x), "y": float(y)})
            except ValueError:
                continue

    nodes_df = pd.DataFrame(nodes)

    pt_mode_set = {"subway", "tram", "rail", "bus", "artificial"}
    exclude_mode_set = {"stopFacilityLink"}

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

            mode_set = set(m.strip() for m in modes.split(","))
            if mode_set & pt_mode_set and not mode_set & exclude_mode_set:
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
    persons_path = os.path.join(sim_dir, "output_persons.csv.gz")
    df = pd.read_csv(persons_path, sep=";")

    out = pd.DataFrame(
        {
            "person_id": df["person"],
            "x": pd.to_numeric(df["first_act_x"], errors="coerce"),
            "y": pd.to_numeric(df["first_act_y"], errors="coerce"),
        }
    )

    if "sex" in df.columns:
        out["sex"] = df["sex"]

    if "age" in df.columns:
        out["age"] = pd.to_numeric(df["age"], errors="coerce")

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

    if "householdIncome" in df.columns:
        out["householdIncome"] = pd.to_numeric(df["householdIncome"], errors="coerce")

    for c in ["income", "income_class", "employment", "has_license"]:
        if c in df.columns and c not in out.columns:
            out[c] = df[c]

    return out


def nearest_stop_geopandas(
    persons_xy: pd.DataFrame, stops_df: pd.DataFrame
) -> pd.DataFrame:
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
    df = persons_df.dropna(subset=["x", "y", "dist_to_nearest_stop_m"]).copy()
    if df.empty:
        return ""

    vmax = df["dist_to_nearest_stop_m"].quantile(0.95)
    colors = (
        df["dist_to_nearest_stop_m"].clip(upper=float(vmax))
        if pd.notna(vmax)
        else df["dist_to_nearest_stop_m"]
    )

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

    variants = [
        ("persons_distance_heatmap", False),
        ("persons_distance_heatmap_with_pt_overlay", True),
    ]

    for suffix, with_overlay in variants:
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

        if not with_overlay and not stops_3857.empty:
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
        _add_basemap(ax, persons_3857)

        if with_overlay and stops_df is not None:
            pt_handles, pt_labels = add_pt_network_overlay(
                ax,
                stops_df=stops_df,
                nodes_df=nodes_df,
                pt_links_df=pt_links_df,
                show_network=(nodes_df is not None and pt_links_df is not None),
                show_stops=True,
                cluster_stops=True,
                add_to_legend=True,
            )
        else:
            pt_handles, pt_labels = [], []

        plt.colorbar(
            sc, ax=ax, orientation="horizontal", shrink=0.6, pad=0.1, aspect=30
        )

        if pt_handles:
            ax.legend(
                handles=pt_handles,
                labels=pt_labels,
                loc="upper right",
                fontsize=9,
                framealpha=0.95,
                edgecolor="black",
            )
        elif not with_overlay:
            ax.legend(loc="upper right")

        ax.set_xlabel("x")
        ax.set_ylabel("y")

        png_path = os.path.join(analysis_path, f"{OUTPUT_PREFIX}_{suffix}.png")
        plt.tight_layout()
        fig.savefig(png_path, dpi=200)
        plt.close(fig)

    # Save aggregated hex gpkg of person-to-stop distances (source CRS)
    _, dist_hex_gdf = aggregate_to_grid(
        df[["x", "y", "dist_to_nearest_stop_m"]],
        agg_column="dist_to_nearest_stop_m",
        agg_method="median",
        cell_size=CELL_SIZE_M[0],
        grid_shape="hex",
    )
    if not dist_hex_gdf.empty:
        ensure_source_crs(dist_hex_gdf).to_file(
            os.path.join(
                analysis_path,
                f"{OUTPUT_PREFIX}_persons_distance_heatmap.gpkg",
            ),
            driver="GPKG",
        )


def plot_pt_network_with_accessibility(
    persons_df: pd.DataFrame,
    stops_df: pd.DataFrame,
    nodes_df: pd.DataFrame,
    pt_links_df: pd.DataFrame,
    analysis_path: str,
):
    df_persons = persons_df.dropna(subset=["x", "y", "dist_to_nearest_stop_m"]).copy()
    if df_persons.empty:
        return ""

    vmax = df_persons["dist_to_nearest_stop_m"].quantile(0.95)
    colors = (
        df_persons["dist_to_nearest_stop_m"].clip(upper=float(vmax))
        if pd.notna(vmax)
        else df_persons["dist_to_nearest_stop_m"]
    )

    persons_gdf = gpd.GeoDataFrame(
        df_persons.copy(),
        geometry=gpd.points_from_xy(df_persons["x"], df_persons["y"]),
        crs=f"EPSG:{SOURCE_EPSG}",
    )
    persons_viz = project_to_visualization_crs(persons_gdf)

    fig, ax = plt.subplots(figsize=FIGURE_SIZE_MAP)

    _add_basemap(ax, persons_viz)

    sc = ax.scatter(
        persons_viz.geometry.x,
        persons_viz.geometry.y,
        c=colors.loc[persons_viz.index],
        s=2,
        cmap=MAP_CMAP,
        alpha=0.3,
        linewidths=0,
        zorder=2,
        label="Person accessibility",
    )

    pt_handles, pt_labels = add_pt_network_overlay(
        ax,
        stops_df=stops_df,
        nodes_df=nodes_df,
        pt_links_df=pt_links_df,
        show_network=True,
        show_stops=True,
        add_to_legend=True,
    )

    ax.set_aspect("equal", adjustable="box")
    ax.set_title("PT Network and Accessibility", fontsize=14, fontweight="bold")
    ax.set_xlabel("x")
    ax.set_ylabel("y")

    cbar = plt.colorbar(
        sc, ax=ax, orientation="horizontal", shrink=0.6, pad=0.08, aspect=30
    )
    cbar.set_label("Distance to nearest PT stop (m)", fontsize=10)

    if pt_handles:
        ax.legend(
            handles=pt_handles,
            labels=pt_labels,
            loc="upper right",
            fontsize=9,
            framealpha=0.95,
            edgecolor="black",
        )

    png_path = os.path.join(
        analysis_path, f"{OUTPUT_PREFIX}_pt_network_with_accessibility.png"
    )
    plt.tight_layout()
    fig.savefig(png_path, dpi=PLOT_DPI)
    plt.close(fig)

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
    gridsize = max(5, int(width / max(1.0, float(cell_size))))

    pad = 0.05
    xmin_p = xmin - width * pad
    xmax_p = xmax + width * pad
    ymin_p = ymin - height * pad
    ymax_p = ymax + height * pad

    fig, ax = plt.subplots(figsize=(10, 10))
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
            add_to_legend=True,
        )

    fig.colorbar(hb, ax=ax, orientation="horizontal", shrink=0.6, pad=0.1, aspect=30)

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
    df = persons_df.dropna(subset=["x", "y", within_flag_col]).copy()
    if df.empty:
        return ""

    values = df[within_flag_col].astype(float).to_numpy()
    title = f"Share of population within {int(pt_stop_distance)}m of a PT stop"

    filename = (
        f"share_within_grid{int(cell_size)}m_"
        f"distance{int(pt_stop_distance)}m_{grid_shape}.png"
    )
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

    if stops_df is not None:
        filename_with_pt = (
            f"share_within_grid{int(cell_size)}m_"
            f"distance{int(pt_stop_distance)}m_{grid_shape}_with_pt_overlay.png"
        )
        return _plot_hexbin_points(
            df=df,
            value=values,
            analysis_path=analysis_path,
            title=title,
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
    df = persons_df.dropna(subset=["x", "y", value_col]).copy()
    if df.empty:
        return ""

    vmax = None
    if clip_q95:
        q95 = df[value_col].quantile(0.95)
        if pd.notna(q95):
            vmax = float(q95)

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

    if stops_df is not None:
        base_name = filename.rsplit(".", 1)[0]
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
        cand2 = os.path.join(iters, f"it.{last_it}", "eqasim_legs.csv")
        return cand2 if os.path.exists(cand2) else None
    except Exception:
        return None


def _read_eqasim_legs(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, sep=";")
    missing = [
        c
        for c in ["person_id", "person_trip_id", "leg_index", "mode"]
        if c not in df.columns
    ]
    if missing:
        raise RuntimeError(f"eqasim_legs.csv missing required columns: {missing}")

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
    if legs.empty:
        return pd.DataFrame(), pd.DataFrame()

    legs_sorted = legs.sort_values(["person_id", "person_trip_id", "leg_index"]).copy()

    def pick_distance(row):
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
            continue

        first_pt_idx = g.iloc[pt_pos[0]]["leg_index"]
        last_pt_idx = g.iloc[pt_pos[-1]]["leg_index"]

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

    agg = legs_walk_df.groupby(
        ["person_id", "person_trip_id", "walk_leg_type"], as_index=False
    ).agg(
        total_walk_distance_m=("walk_distance_m", "sum"),
        total_walk_duration_s=("walk_duration_s", "sum"),
    )

    pvt = agg.pivot_table(
        index=["person_id", "person_trip_id"],
        columns="walk_leg_type",
        values=["total_walk_distance_m", "total_walk_duration_s"],
        fill_value=0.0,
    )
    pvt.columns = [f"{a}_{b}" for a, b in pvt.columns.to_flat_index()]
    per_trip = pvt.reset_index()

    for base in ("total_walk_distance_m", "total_walk_duration_s"):
        for typ in ("access", "egress"):
            col = f"{base}_{typ}"
            if col not in per_trip.columns:
                per_trip[col] = 0.0

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
    if per_trip_df is None or per_trip_df.empty:
        return ""

    acc = per_trip_df["access_walk_distance_m"].astype(float)
    egr = per_trip_df["egress_walk_distance_m"].astype(float)

    acc_median = acc.median()
    egr_median = egr.median()

    combined = pd.concat([acc, egr])
    q95 = combined.quantile(0.95)
    max_val = combined.max()
    max_x = float(q95) if pd.notna(q95) and q95 > 0 else float(max(max_val, 1.0))
    bins = min(40, max(10, int(max_x // 20)))

    fig, axes = plt.subplots(1, 2, figsize=(12, 5), sharey=True)

    ax = axes[0]
    ax.hist(acc, bins=bins, range=(0, max_x), color=ACCESS_COLOR, alpha=0.8)
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

    ax = axes[1]
    ax.hist(egr, bins=bins, range=(0, max_x), color=EGRESS_COLOR, alpha=0.8)
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

    if category_col == "age_class":
        if hasattr(df[category_col], "cat") and df[category_col].cat.ordered:
            ordered_cats = df[category_col].cat.categories.tolist()
        else:
            ordered_cats = df[category_col].unique().tolist()
    else:
        medians = (
            df.groupby(category_col)["access_walk_distance_m"].median().sort_values()
        )
        ordered_cats = medians.index.tolist()

    def get_display_label(cat, col):
        if col == "employed":
            return "Employed" if cat else "Unemployed"
        elif col == "sex":
            return {"m": "Male", "f": "Female"}.get(cat, str(cat))
        return str(cat)

    display_labels = [get_display_label(cat, category_col) for cat in ordered_cats]

    access_data = [
        df[df[category_col] == cat]["access_walk_distance_m"].dropna().values
        for cat in ordered_cats
    ]
    egress_data = [
        df[df[category_col] == cat]["egress_walk_distance_m"].dropna().values
        for cat in ordered_cats
    ]

    fig, ax = plt.subplots(
        figsize=(max(FIGURE_SIZE_BOX[0], len(ordered_cats) * 0.8), FIGURE_SIZE_BOX[1])
    )

    x = np.arange(len(ordered_cats))
    width = 0.2

    ax.boxplot(
        access_data,
        positions=x - width / 2,
        widths=width,
        patch_artist=True,
        showfliers=False,
        boxprops=dict(facecolor=ACCESS_COLOR, alpha=0.7),
    )
    ax.boxplot(
        egress_data,
        positions=x + width / 2,
        widths=width,
        patch_artist=True,
        showfliers=False,
        boxprops=dict(facecolor=EGRESS_COLOR, alpha=0.7),
    )

    category_titles = {
        "age_class": "Age Group",
        "employed": "Employment Status",
        "sex": "Gender",
    }
    title = f"Access and Egress by {category_titles.get(category_col, category_col.title())}"

    ax.set_xticks(x)
    ax.set_xticklabels(display_labels, rotation=45, ha="right")
    ax.set_ylabel("Walking distance (m)")
    if category_col == "age_class":
        ax.set_xlabel("Age Groups")
    ax.set_title(title)

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


def mode_share_comparison(
    context, df_sim_trips, df_hts_trips, df_hts_persons, suffix=None
):
    import analysis_dhaka.hannover.ivt_style.myplottools as myplottools

    mode_map = {
        "bike": "bike",
        "car": "car",
        "car_passenger": "car",
        "pt": "pt",
        "walk": "walk",
    }

    df_sim = df_sim_trips.copy()
    df_sim["mode"] = df_sim["mode"].map(mode_map).fillna("other")
    sim_counts = df_sim[df_sim["mode"].isin(["bike", "car", "pt", "walk"])][
        "mode"
    ].value_counts()
    sim_share = sim_counts / sim_counts.sum() * 100

    df_hts = df_hts_trips.copy()
    df_hts["mode"] = df_hts["mode"].map(mode_map).fillna(df_hts["mode"])
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

    modes = ["bike", "car", "pt", "walk"]
    sim_vals = [sim_share.get(m, 0) for m in modes]
    hts_vals = [hts_share.get(m, 0) for m in modes]

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


def mode_share_by_distance(
    context, df_sim_trips, df_hts_trips, df_hts_persons, suffix=None
):
    if df_hts_trips is None or len(df_hts_trips) == 0:
        return

    mode_map = {
        "bike": "bike",
        "car": "car",
        "car_passenger": "car",
        "pt": "pt",
        "walk": "walk",
    }

    df_sim = df_sim_trips.copy()
    df_sim["distance_m"] = df_sim["euclidean_distance"]
    df_sim["mode"] = df_sim["mode"].map(mode_map).fillna("other")
    df_sim = df_sim[df_sim["mode"].isin(["bike", "car", "pt", "walk"])]

    df_hts = df_hts_trips.copy()
    df_hts["distance_m"] = df_hts["routed_distance"]
    df_hts["mode"] = df_hts["mode"].map(mode_map).fillna(df_hts["mode"])
    df_hts = df_hts[df_hts["mode"].isin(["bike", "car", "pt", "walk"])]

    if "weight_person" not in df_hts.columns and df_hts_persons is not None:
        df_hts = df_hts.merge(
            df_hts_persons[["person_id", "weight_person"]], on="person_id", how="left"
        )

    distance_bins = [0, 1000, 2000, 3000, 4000, 5000, 6000]
    bin_centers = [
        (distance_bins[i] + distance_bins[i + 1]) / 2
        for i in range(len(distance_bins) - 1)
    ]
    modes = ["bike", "car", "pt", "walk"]

    hts_shares = {mode: [] for mode in modes}
    sim_shares = {mode: [] for mode in modes}

    for i in range(len(distance_bins) - 1):
        dist_min = distance_bins[i]
        dist_max = distance_bins[i + 1]

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

    fig, ax = plt.subplots(figsize=(8, 5))

    colors = {"bike": "#FF8C00", "car": "#2E8B57", "pt": "#294088", "walk": "#48A0F8"}

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

    y_min, y_max = ax.get_ylim()
    ax.set_ylim(y_min, y_max + 0.15 * (y_max - y_min))

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

    analysis_path = context.config("analysis_path")
    title_figure = "mode_share_by_distance"
    if suffix:
        title_figure += "_" + suffix
    title_figure += ".png"

    plt.savefig("%s/%s" % (analysis_path, title_figure), dpi=300)
    plt.close()


def execute(context):
    output_path = context.config("output_path")
    analysis_path = context.config("analysis_path")
    data_path = context.config("data_path")
    sim_output_dir = context.config("simulation_output_dir")

    os.makedirs(analysis_path, exist_ok=True)

    sim_dir = os.path.join(output_path, sim_output_dir)
    schedule_xml = os.path.join(sim_dir, "output_transitSchedule.xml.gz")
    network_xml = os.path.join(sim_dir, "output_network.xml.gz")

    stops_df = _extract_stops_from_schedule(schedule_xml)
    nodes_df, pt_links_df = _extract_pt_network_from_matsim(network_xml)

    stops_gdf = gpd.GeoDataFrame(
        stops_df.copy(),
        geometry=gpd.points_from_xy(stops_df["x"], stops_df["y"]),
        crs=f"EPSG:{SOURCE_EPSG}",
    )

    persons_df = read_persons(sim_dir)
    persons_df_phase_a = persons_df.dropna(subset=["x", "y"]).copy()
    stops_df_phase_a = stops_df.dropna(subset=["x", "y"]).copy()

    legs_path = _discover_eqasim_legs_file(sim_dir)
    legs_df = pd.DataFrame()
    if legs_path:
        legs_df = _read_eqasim_legs(legs_path)

    persons_df = persons_df.dropna(subset=["x", "y"])
    nearest_df = nearest_stop_geopandas(persons_df[["x", "y"]], stops_df)
    persons_df = pd.concat([persons_df.reset_index(drop=True), nearest_df], axis=1)

    for pt_stop_distance in PT_STOP_DISTANCE_M:
        within_flag_col = f"within_{int(pt_stop_distance)}m"
        persons_df[within_flag_col] = (
            persons_df["dist_to_nearest_stop_m"] <= pt_stop_distance
        )

    legs_walk_df = pd.DataFrame()
    per_trip_df = pd.DataFrame()
    if not legs_df.empty:
        legs_walk_df, per_trip_df = _extract_access_egress_walk(
            legs_df, walk_modes=WALK_MODES, pt_modes=PT_MODES
        )

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

    for cell_size in CELL_SIZE_M:
        for pt_stop_distance in PT_STOP_DISTANCE_M:
            for grid_shape in GRID_SHAPES:
                if grid_shape == "neighborhoods" and cell_size != CELL_SIZE_M[0]:
                    continue

                within_flag_col = f"within_{int(pt_stop_distance)}m"
                grid_df, grid_gdf = aggregate_to_grid(
                    persons_df,
                    agg_column=within_flag_col,
                    agg_method="share",
                    cell_size=cell_size,
                    grid_shape=grid_shape,
                    data_path=data_path,
                )

                if grid_shape == "hex":
                    plot_grid_share_hexbin(
                        persons_df,
                        within_flag_col,
                        analysis_path,
                        grid_shape,
                        cell_size,
                        pt_stop_distance,
                        stops_df,
                        nodes_df,
                        pt_links_df,
                    )
                elif grid_shape == "neighborhoods":
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
                    )

                    output_path_map_pt = os.path.join(
                        analysis_path,
                        f"{OUTPUT_PREFIX}_share_within_distance{int(pt_stop_distance)}m_{grid_shape}_with_pt_overlay.png",
                    )
                    plot_choropleth_map(
                        grid_gdf,
                        "value",
                        title,
                        output_path_map_pt,
                        vmin=data_vmin,
                        vmax=1.0,
                        show_pt_overlay=True,
                        stops_df=stops_df,
                        nodes_df=nodes_df,
                        pt_links_df=pt_links_df,
                    )
                else:
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
                    )

                    output_path_map_pt = os.path.join(
                        analysis_path,
                        f"{OUTPUT_PREFIX}_share_within_grid{int(cell_size)}m_distance{int(pt_stop_distance)}m_{grid_shape}_with_pt_overlay.png",
                    )
                    plot_choropleth_map(
                        grid_gdf,
                        "value",
                        title,
                        output_path_map_pt,
                        vmin=0.0,
                        vmax=1.0,
                        show_pt_overlay=True,
                        stops_df=stops_df,
                        nodes_df=nodes_df,
                        pt_links_df=pt_links_df,
                    )

                if "access_walk_distance_median" in persons_df.columns:
                    dist_df, dist_gdf = aggregate_to_grid(
                        persons_df[["x", "y", "access_walk_distance_median"]],
                        agg_column="access_walk_distance_median",
                        agg_method="median",
                        cell_size=cell_size,
                        grid_shape=grid_shape,
                        data_path=data_path,
                    )

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
                        _, hex_access_gdf = aggregate_to_grid(
                            persons_df[["x", "y", "access_walk_distance_median"]],
                            agg_column="access_walk_distance_median",
                            agg_method="median",
                            cell_size=cell_size,
                            grid_shape="hex",
                        )
                        if not hex_access_gdf.empty:
                            hex_access_gdf = ensure_source_crs(hex_access_gdf)
                            hex_access_gdf.to_file(
                                os.path.join(
                                    analysis_path,
                                    f"{OUTPUT_PREFIX}_access_median_grid{int(cell_size)}m_hex.gpkg",
                                ),
                                driver="GPKG",
                            )
                    elif grid_shape == "neighborhoods":
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

                        output_path_dist_pt = os.path.join(
                            analysis_path,
                            f"{OUTPUT_PREFIX}_access_median_{grid_shape}_with_pt_overlay.png",
                        )
                        plot_choropleth_map(
                            dist_gdf,
                            "value",
                            title,
                            output_path_dist_pt,
                            vmin=data_vmin,
                            clip_quantile=0.95,
                            show_pt_overlay=True,
                            stops_df=stops_df,
                            nodes_df=nodes_df,
                            pt_links_df=pt_links_df,
                        )
                        if not dist_gdf.empty:
                            ensure_source_crs(dist_gdf).to_file(
                                os.path.join(
                                    analysis_path,
                                    f"{OUTPUT_PREFIX}_access_median_neighborhoods.gpkg",
                                ),
                                driver="GPKG",
                            )
                    else:
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

                        output_path_dist_pt = os.path.join(
                            analysis_path,
                            f"{OUTPUT_PREFIX}_access_median_grid{int(cell_size)}m_{grid_shape}_with_pt_overlay.png",
                        )
                        plot_choropleth_map(
                            dist_gdf,
                            "value",
                            title,
                            output_path_dist_pt,
                            vmin=0.0,
                            clip_quantile=0.95,
                            show_pt_overlay=True,
                            stops_df=stops_df,
                            nodes_df=nodes_df,
                            pt_links_df=pt_links_df,
                        )

    plot_persons_distance_heatmap(
        persons_df, stops_df, analysis_path, nodes_df, pt_links_df
    )
    plot_pt_network_with_accessibility(
        persons_df, stops_df, nodes_df, pt_links_df, analysis_path
    )

    if not per_trip_df.empty:
        plot_access_egress_distributions(per_trip_df, analysis_path)

    persons_with_age_class = persons_df.copy()
    persons_with_age_class["age_class"] = compute_age_class(persons_df["age"])

    for col in ["sex", "employed", "age_class"]:
        if col in persons_with_age_class.columns and not per_trip_df.empty:
            plot_walking_dist_by_category(
                per_trip_df, persons_with_age_class, col, analysis_path
            )

    stops_df.to_csv(
        os.path.join(analysis_path, f"{OUTPUT_PREFIX}_stops.csv"), index=False
    )
    stops_gdf.to_file(
        os.path.join(analysis_path, f"{OUTPUT_PREFIX}_stops.gpkg"), driver="GPKG"
    )

    if not nodes_df.empty:
        nodes_df.to_csv(
            os.path.join(analysis_path, f"{OUTPUT_PREFIX}_network_nodes.csv"),
            index=False,
        )
        gpd.GeoDataFrame(
            nodes_df,
            geometry=gpd.points_from_xy(nodes_df["x"], nodes_df["y"]),
            crs=f"EPSG:{SOURCE_EPSG}",
        ).to_file(
            os.path.join(analysis_path, f"{OUTPUT_PREFIX}_network_nodes.gpkg"),
            driver="GPKG",
        )
    if not pt_links_df.empty:
        pt_links_df.to_csv(
            os.path.join(analysis_path, f"{OUTPUT_PREFIX}_pt_links.csv"), index=False
        )
        _links_coords = pt_links_df.merge(
            nodes_df.rename(columns={"node_id": "from_node", "x": "from_x", "y": "from_y"}),
            on="from_node", how="left",
        ).merge(
            nodes_df.rename(columns={"node_id": "to_node", "x": "to_x", "y": "to_y"}),
            on="to_node", how="left",
        ).dropna(subset=["from_x", "from_y", "to_x", "to_y"])
        if not _links_coords.empty:
            gpd.GeoDataFrame(
                _links_coords,
                geometry=[
                    LineString([(r["from_x"], r["from_y"]), (r["to_x"], r["to_y"])])
                    for _, r in _links_coords.iterrows()
                ],
                crs=f"EPSG:{SOURCE_EPSG}",
            ).to_file(
                os.path.join(analysis_path, f"{OUTPUT_PREFIX}_pt_links.gpkg"),
                driver="GPKG",
            )

    persons_df.to_csv(
        os.path.join(analysis_path, f"{OUTPUT_PREFIX}_persons_enriched.csv"),
        index=False,
    )

    try:
        eqasim_trips_path = os.path.join(sim_dir, "eqasim_trips.csv")
        if os.path.exists(eqasim_trips_path):
            df_sim_trips = pd.read_csv(eqasim_trips_path, sep=";")
            df_hts_households, df_hts_persons, df_hts_trips = context.stage(
                "data.hts.entd.reweighted"
            )

            if "person_weight" in df_hts_persons.columns:
                df_hts_persons = df_hts_persons.rename(
                    columns={"person_weight": "weight_person"}
                )

            mode_share_comparison(
                context, df_sim_trips, df_hts_trips, df_hts_persons, suffix=None
            )
            mode_share_by_distance(
                context, df_sim_trips, df_hts_trips, df_hts_persons, suffix=None
            )
    except Exception:
        pass

    try:
        print("INFO starting access-only accessibility")

        access_times = {}
        for mode in ACCESS_MODES:
            homes_xy = persons_df_phase_a[["x", "y"]].copy()
            stops_xy = stops_df_phase_a[["x", "y"]].copy()
            access_times[mode] = compute_access_times_teleportation(
                homes_xy, stops_xy, mode, max_distance_m=MAX_ACCESS_DISTANCE_M[mode]
            )

        for mode in ACCESS_MODES:
            scores = compute_access_only_accessibility(
                access_times[mode], BETA_VALUES[mode]
            )
            persons_df_phase_a[f"accessibility_{mode}"] = scores

        plot_access_only_comparison(
            persons_df_phase_a,
            analysis_path,
            modes=ACCESS_MODES,
        )
        print("SUCCESS: Access-only accessibility completed")

        # Functional accessibility (requires eqasim legs + PT data)
        stops_service: pd.DataFrame | None = None
        try:
            print("INFO starting functional accessibility")

            legs_path = _discover_eqasim_legs_file(sim_dir)
            pt_path = os.path.join(sim_dir, "eqasim_pt.csv")

            if legs_path is None or not os.path.exists(pt_path):
                raise FileNotFoundError("eqasim_legs.csv or eqasim_pt.csv not found")

            legs_df_phase2 = _read_eqasim_legs(legs_path)
            pt_df_phase2 = pd.read_csv(pt_path, sep=";")
            for col in [
                "person_id",
                "person_trip_id",
                "leg_index",
                "access_area_id",
                "egress_area_id",
            ]:
                if col in pt_df_phase2.columns:
                    pt_df_phase2[col] = pd.to_numeric(
                        pt_df_phase2[col], errors="coerce"
                    )

            wait_by_stop = extract_stop_headways(schedule_xml)
            ride_by_area = extract_empirical_ride_times(legs_df_phase2, pt_df_phase2)
            transfer_by_area = extract_transfer_penalties(legs_df_phase2, pt_df_phase2)
            stops_service = enrich_stops_with_service(
                stops_df_phase_a, wait_by_stop, ride_by_area, transfer_by_area
            )

            for mode in ACCESS_MODES:
                func_scores = compute_functional_accessibility(
                    access_times[mode], stops_service, BETA_VALUES[mode]
                )
                persons_df_phase_a[f"accessibility_{mode}_functional"] = func_scores

            plot_functional_comparison(
                persons_df_phase_a,
                analysis_path,
                modes=ACCESS_MODES,
            )
            print("SUCCESS: Functional accessibility completed")

            # Pop-density-weighted functional accessibility
            try:
                print("INFO starting pop-density-weighted functional accessibility")
                stops_with_pop = assign_stop_population_density(
                    stops_service, data_path
                )
                pop_weights = stops_with_pop["pop_density"].to_numpy(dtype=float)

                for mode in ACCESS_MODES:
                    pop_scores = compute_functional_accessibility(
                        access_times[mode],
                        stops_with_pop,
                        BETA_VALUES[mode],
                        opportunity_weights=pop_weights,
                    )
                    persons_df_phase_a[f"accessibility_{mode}_functional_pop"] = (
                        pop_scores
                    )

                plot_functional_comparison_pop_weighted(
                    persons_df_phase_a,
                    analysis_path,
                    modes=ACCESS_MODES,
                )
                print("SUCCESS: Pop-weighted functional accessibility completed")
            except Exception as exc:
                print(f"WARNING: Pop-weighted functional skipped - {exc}")

            # Destination-pop-density-weighted functional accessibility
            try:
                print(
                    "INFO starting dest-pop-density-weighted functional accessibility"
                )
                dest_weights = compute_destination_pop_density(
                    stops_service, pt_df_phase2, data_path
                )

                for mode in ACCESS_MODES:
                    dest_scores = compute_functional_accessibility(
                        access_times[mode],
                        stops_service,
                        BETA_VALUES[mode],
                        opportunity_weights=dest_weights,
                    )
                    persons_df_phase_a[f"accessibility_{mode}_functional_dest_pop"] = (
                        dest_scores
                    )

                plot_functional_comparison_dest_pop(
                    persons_df_phase_a,
                    analysis_path,
                    modes=ACCESS_MODES,
                )
                print("SUCCESS: Dest-pop-weighted functional accessibility completed")
            except Exception as exc:
                print(f"WARNING: Dest-pop-weighted functional skipped - {exc}")

            # Pop-density × dest-employment weighted functional accessibility
            try:
                print(
                    "INFO starting pop-density × dest-employment weighted functional accessibility"
                )
                dest_emp_weights = compute_destination_employment(
                    stops_service, pt_df_phase2, sim_dir
                )

                # Reuse pop_weights from Phase 2b if available, else compute
                try:
                    pop_weights  # noqa: F841
                except NameError:
                    stops_with_pop = assign_stop_population_density(
                        stops_service, data_path
                    )
                    pop_weights = stops_with_pop["pop_density"].to_numpy(dtype=float)

                pop_emp_weights = pop_weights * dest_emp_weights

                for mode in ACCESS_MODES:
                    pe_scores = compute_functional_accessibility(
                        access_times[mode],
                        stops_service,
                        BETA_VALUES[mode],
                        opportunity_weights=pop_emp_weights,
                    )
                    persons_df_phase_a[f"accessibility_{mode}_functional_pop_emp"] = (
                        pe_scores
                    )

                plot_functional_comparison_pop_emp(
                    persons_df_phase_a,
                    analysis_path,
                    modes=ACCESS_MODES,
                )
                print(
                    "SUCCESS: Pop × dest-employment weighted functional accessibility completed"
                )
            except Exception as exc:
                print(
                    f"WARNING: Pop × dest-employment weighted functional skipped - {exc}"
                )

            # Dest-pop-density × dest-employment weighted functional accessibility
            try:
                print(
                    "INFO starting dest-pop × dest-employment weighted functional accessibility"
                )
                # Reuse dest_emp_weights from Phase 2d if available, else compute
                try:
                    dest_emp_weights  # noqa: F841
                except NameError:
                    dest_emp_weights = compute_destination_employment(
                        stops_service, pt_df_phase2, sim_dir
                    )

                # Reuse dest_weights from Phase 2c if available, else compute
                try:
                    dest_weights  # noqa: F841
                except NameError:
                    dest_weights = compute_destination_pop_density(
                        stops_service, pt_df_phase2, data_path
                    )

                dest_pop_emp_weights = dest_weights * dest_emp_weights

                for mode in ACCESS_MODES:
                    dpe_scores = compute_functional_accessibility(
                        access_times[mode],
                        stops_service,
                        BETA_VALUES[mode],
                        opportunity_weights=dest_pop_emp_weights,
                    )
                    persons_df_phase_a[
                        f"accessibility_{mode}_functional_dest_pop_emp"
                    ] = dpe_scores

                plot_functional_comparison_dest_pop_emp(
                    persons_df_phase_a,
                    analysis_path,
                    modes=ACCESS_MODES,
                )
                print(
                    "SUCCESS: Dest-pop × dest-employment weighted functional accessibility completed"
                )
            except Exception as exc:
                print(
                    f"WARNING: Dest-pop × dest-employment weighted functional skipped - {exc}"
                )

        except Exception as exc:
            print(f"WARNING: Functional accessibility skipped - {exc}")

        # Catchment area analysis
        print("INFO starting catchment area analysis")

        stops_with_catchments = compute_catchment_areas(
            access_times, stops_df_phase_a, CATCHMENT_THRESHOLDS
        )

        persons_with_coverage = classify_person_coverage(
            persons_df_phase_a, access_times, CATCHMENT_THRESHOLDS
        )

        plot_person_coverage_map(
            persons_with_coverage,
            stops_with_catchments,
            analysis_path,
            CATCHMENT_THRESHOLDS,
            nodes_df=nodes_df,
            pt_links_df=pt_links_df,
        )

        plot_stop_catchments(stops_with_catchments, analysis_path, modes=ACCESS_MODES)

        plot_stop_catchments_by_neighborhood(
            stops_with_catchments,
            analysis_path,
            data_path,
            modes=ACCESS_MODES,
            nodes_df=nodes_df,
            pt_links_df=pt_links_df,
        )

        plot_resident_catchment_by_neighborhood(
            persons_df_phase_a,
            access_times,
            CATCHMENT_THRESHOLDS,
            analysis_path,
            data_path,
            modes=ACCESS_MODES,
            stops_df=stops_with_catchments,
            nodes_df=nodes_df,
            pt_links_df=pt_links_df,
        )

        print("SUCCESS: Catchment area analysis completed")

        # Expansion factor analysis
        print("INFO starting expansion factor analysis")

        stops_with_expansion = compute_expansion_factors(
            stops_with_catchments,
            walk_col="catchment_walk",
            bike_col="catchment_bike",
        )

        plot_expansion_factors(
            stops_with_expansion,
            analysis_path,
            nodes_df=nodes_df,
            pt_links_df=pt_links_df,
        )

        _exp_cols = [c for c in ["stop_id", "x", "y", "name", "catchment_walk",
                                  "catchment_bike", "expansion_factor"]
                     if c in stops_with_expansion.columns]
        _exp_df = stops_with_expansion[_exp_cols].dropna(subset=["x", "y"])
        if not _exp_df.empty:
            gpd.GeoDataFrame(
                _exp_df,
                geometry=gpd.points_from_xy(_exp_df["x"], _exp_df["y"]),
                crs=f"EPSG:{SOURCE_EPSG}",
            ).to_file(
                os.path.join(
                    analysis_path,
                    f"{OUTPUT_PREFIX}_access_only_expansion_factor.gpkg",
                ),
                driver="GPKG",
            )

        print("SUCCESS: Expansion factor analysis completed")

        # Functional catchment & expansion (requires stops_service from functional phase)
        try:
            if stops_service is None:
                raise RuntimeError(
                    "stops_service unavailable (functional phase failed)"
                )
            print("INFO starting functional catchment analysis")

            functional_cost_dict: dict[str, np.ndarray] = {}
            for mode in ACCESS_MODES:
                functional_cost_dict[mode] = compute_functional_cost_matrix(
                    access_times[mode], stops_service
                )

            func_stops_catchments = compute_catchment_areas(
                functional_cost_dict, stops_service, FUNCTIONAL_CATCHMENT_THRESHOLDS
            )

            plot_stop_catchments(
                func_stops_catchments,
                analysis_path,
                modes=ACCESS_MODES,
                type_label="functional",
            )

            plot_stop_catchments_by_neighborhood(
                func_stops_catchments,
                analysis_path,
                data_path,
                modes=ACCESS_MODES,
                nodes_df=nodes_df,
                pt_links_df=pt_links_df,
                type_label="functional",
            )

            plot_resident_catchment_by_neighborhood(
                persons_df_phase_a,
                functional_cost_dict,
                FUNCTIONAL_CATCHMENT_THRESHOLDS,
                analysis_path,
                data_path,
                modes=ACCESS_MODES,
                stops_df=func_stops_catchments,
                nodes_df=nodes_df,
                pt_links_df=pt_links_df,
                type_label="functional",
            )

            print("SUCCESS: Functional catchment analysis completed")

            print("INFO starting functional expansion factor analysis")

            func_stops_expansion = compute_expansion_factors(
                func_stops_catchments,
                walk_col="catchment_walk",
                bike_col="catchment_bike",
            )

            plot_expansion_factors(
                func_stops_expansion,
                analysis_path,
                nodes_df=nodes_df,
                pt_links_df=pt_links_df,
                type_label="functional",
            )
            _exp_cols = [c for c in ["stop_id", "x", "y", "name", "catchment_walk",
                                  "catchment_bike", "expansion_factor"]
                     if c in func_stops_expansion.columns]
            _exp_df = func_stops_expansion[_exp_cols].dropna(subset=["x", "y"])
            if not _exp_df.empty:
                gpd.GeoDataFrame(
                    _exp_df,
                    geometry=gpd.points_from_xy(_exp_df["x"], _exp_df["y"]),
                    crs=f"EPSG:{SOURCE_EPSG}",
                ).to_file(
                    os.path.join(
                        analysis_path,
                        f"{OUTPUT_PREFIX}_functional_expansion_factor.gpkg",
                    ),
                    driver="GPKG",
                )

            print("SUCCESS: Functional expansion factor analysis completed")
        except Exception as exc:
            print(f"WARNING: Functional catchment/expansion skipped - {exc}")

    except Exception:
        pass
