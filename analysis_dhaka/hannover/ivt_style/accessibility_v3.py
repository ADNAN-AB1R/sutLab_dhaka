"""Accessibility analysis v3: trip-origin cost matrix with inclusive/exclusive catchment."""

import os

import gc

import contextily as cx
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from scipy.spatial import cKDTree
from scipy.spatial.distance import cdist


from analysis_dhaka.hannover.ivt_style.accessibility import (
    ACCESS_MODES,
    BASEMAP_EPSG,
    BEELINE_DISTANCE_FACTOR,
    MODE_SPEEDS_MPS,
    OUTPUT_PREFIX,
    PLOT_DPI,
    SOURCE_EPSG,
    _area_key,
    _build_stop_area_facility_count,
    _discover_eqasim_legs_file,
    _extract_pt_network_from_matsim,
    _extract_stops_from_schedule,
    _load_hannover_neighborhoods,
    _load_mikrobezirk_density,
    _read_eqasim_legs,
    _render_neighborhood_panel,
    add_pt_network_overlay,
    ensure_source_crs,
    enrich_stops_with_service,
    extract_empirical_ride_times,
    extract_stop_headways,
    extract_transfer_penalties,
    read_persons,
)

TRAVEL_BUDGET_MIN = 60.0
OUTPUT_PREFIX_V3 = "accessibility_v3"

# Memory threshold for switching to float32
_FLOAT32_CELL_THRESHOLD = 10_000_000

# Trips processed per chunk in the cost-matrix loop.
# Each chunk materialises K × N_stops float32 cells.
# At K=5000, N_stops=5000: ~100 MB per chunk — safe under tight SLURM limits.
_TRIP_CHUNK_SIZE = 5_000


def _config(context, key, default=None):
    """Safely retrieve a config value with optional default."""
    try:
        return context.config(key, default=default)
    except TypeError:
        try:
            return context.config(key)
        except Exception:
            return default


def _safe_float32(arr):
    """Return float32 array if large enough to warrant memory savings."""
    if arr.size > _FLOAT32_CELL_THRESHOLD:
        return arr.astype(np.float32)
    return arr.astype(np.float64)


# ---------------------------------------------------------------------------
# Core analytics
# ---------------------------------------------------------------------------


def load_home_origin_trips(sim_dir):
    """Load and filter output_trips.csv.gz to home-origin trips."""
    path = os.path.join(sim_dir, "output_trips.csv.gz")
    df = pd.read_csv(path, sep=";")
    df = df[df["start_activity_type"] == "home"].copy()

    def _parse_trav_time(t):
        try:
            parts = str(t).split(":")
            if len(parts) == 3:
                h, m, s = parts
                return (int(h) * 3600 + int(m) * 60 + float(s)) / 60.0
            return np.nan
        except Exception:
            return np.nan

    df["trav_time_min"] = df["trav_time"].apply(_parse_trav_time)

    keep_cols = [
        "person", "trip_number", "trip_id",
        "start_x", "start_y", "end_x", "end_y",
        "end_activity_type", "main_mode", "modes",
    ]
    keep_cols = [c for c in keep_cols if c in df.columns]
    df = df[keep_cols].copy()

    for c in ["start_x", "start_y", "end_x", "end_y"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    df = df.dropna(subset=["start_x", "start_y", "end_x", "end_y"])

    total = len(df)
    unique_persons = df["person"].nunique() if "person" in df.columns else "n/a"
    print(
        f"INFO load_home_origin_trips: {total} trips, {unique_persons} unique persons"
    )
    if "end_activity_type" in df.columns:
        purpose_counts = df["end_activity_type"].value_counts()
        print(f"INFO  Purpose breakdown:\n{purpose_counts.to_string()}")

    return df.reset_index(drop=True)


def compute_nearest_egress_stop(trips_df, stops_df):
    """Find the nearest stop for each trip destination using cKDTree (no full matrix)."""
    trip_dest = trips_df[["end_x", "end_y"]].values
    stop_coords = stops_df[["x", "y"]].values

    tree = cKDTree(stop_coords)
    dist_to_nearest, nearest_stop_idx = tree.query(trip_dest, k=1, workers=-1)

    t_egress = (
        dist_to_nearest * BEELINE_DISTANCE_FACTOR / MODE_SPEEDS_MPS["walk"] / 60.0
    ).astype(np.float32)

    print(
        f"INFO compute_nearest_egress_stop: "
        f"mean egress={t_egress.mean():.2f} min, "
        f"median={np.median(t_egress):.2f} min"
    )
    return nearest_stop_idx, t_egress


def process_mode_chunked(
    trips_df,
    stops_df,
    t_egress,
    mode,
    persons_arr,
    person_inverse,
    unique_persons,
    emp_weights_dest=None,
    stop_emp_weight=None,
    chunk_size=_TRIP_CHUNK_SIZE,
    budget=TRAVEL_BUDGET_MIN,
):
    """Process one access mode in trip-chunks to cap peak memory.

    Never builds a full N_trips × N_stops float matrix.  Each chunk is
    K × N_stops (float32) — typically ~100 MB at K=5 000, N_stops=5 000.

    Returns a dict with:
      catchment_inclusive, catchment_exclusive,
      catchment_inclusive_weighted  (or None),
      score_{sum,max,mean,unique_stops},
      score_weighted_{sum,max,mean,unique_stops}  (if emp_weights_dest given),
      min_cost_trip  (float32, length N_trips) — used for Phase 11 CSV.
    """
    n_trips = len(trips_df)
    n_stops = len(stops_df)
    n_persons = len(unique_persons)

    trip_coords = trips_df[["start_x", "start_y"]].values.astype(np.float32)
    stop_coords = stops_df[["x", "y"]].values.astype(np.float32)
    pt_cost = (
        stops_df["wait_min"].values
        + stops_df["ride_min"].values
        + stops_df["transfer_min"].values
    ).astype(np.float32)
    t_egress_f32 = t_egress.astype(np.float32)
    spd = float(MODE_SPEEDS_MPS[mode])

    # Chunk-level accumulators
    catchment_inclusive = np.zeros(n_stops, dtype=np.float64)
    catchment_exclusive = np.zeros(n_stops, dtype=np.float64)
    catchment_incl_weighted = np.zeros(n_stops, dtype=np.float64) if emp_weights_dest is not None else None

    feasible_trip = np.zeros(n_trips, dtype=np.float32)
    min_cost_trip = np.full(n_trips, np.inf, dtype=np.float32)

    # person_reachable: (n_persons × n_stops) bool tracks union of reachable stops.
    # Memory: n_persons=200k, n_stops=5k → 1 GB (bool8).  Fine on 128 GB nodes.
    # If it exceeds 500 M cells, fall back to per-person Python sets (slower).
    _PERSON_REACHABLE_LIMIT = 500_000_000
    if n_persons * n_stops <= _PERSON_REACHABLE_LIMIT:
        person_reachable = np.zeros((n_persons, n_stops), dtype=bool)
        use_dense = True
    else:
        person_reachable_sets = [set() for _ in range(n_persons)]
        use_dense = False

    for chunk_start in range(0, n_trips, chunk_size):
        chunk_end = min(chunk_start + chunk_size, n_trips)
        sl = slice(chunk_start, chunk_end)

        # Access: K × N_stops, float32
        dists = cdist(trip_coords[sl], stop_coords).astype(np.float32)
        access_chunk = dists * (BEELINE_DISTANCE_FACTOR / spd / 60.0)
        del dists

        # Cost: K × N_stops
        cost_chunk = access_chunk + pt_cost[np.newaxis, :] + t_egress_f32[sl, np.newaxis]
        del access_chunk

        within = cost_chunk <= budget  # bool, K × N_stops

        # Inclusive catchment
        catchment_inclusive += within.sum(axis=0)

        # Exclusive catchment (only the best stop per trip)
        best_stop = np.argmin(cost_chunk, axis=1)
        best_cost_arr = cost_chunk[np.arange(chunk_end - chunk_start), best_stop]
        feas_mask = best_cost_arr <= budget
        np.add.at(catchment_exclusive, best_stop[feas_mask], 1.0)

        # Per-trip min cost and feasibility flag
        chunk_min = cost_chunk.min(axis=1)
        min_cost_trip[sl] = chunk_min
        feasible_trip[sl] = (chunk_min <= budget).astype(np.float32)
        del cost_chunk, best_stop, best_cost_arr, chunk_min

        # Weighted inclusive catchment
        if emp_weights_dest is not None:
            catchment_incl_weighted += (
                within * emp_weights_dest[sl, np.newaxis]
            ).sum(axis=0)

        # Accumulate per-person reachable stops
        chunk_pinv = person_inverse[sl]
        if use_dense:
            for p_idx in np.unique(chunk_pinv):
                mask = chunk_pinv == p_idx
                person_reachable[p_idx] |= within[mask].any(axis=0)
        else:
            for local_i, p_idx in enumerate(chunk_pinv):
                idxs = np.where(within[local_i])[0]
                person_reachable_sets[p_idx].update(idxs.tolist())

        del within
        gc.collect()

    print(
        f"INFO process_mode_chunked ({mode}): done, "
        f"mean_min_cost={float(min_cost_trip[np.isfinite(min_cost_trip)].mean()):.2f} min"
    )

    # Unique stops per person
    if use_dense:
        unique_stops_arr = person_reachable.sum(axis=1).astype(np.int32)
        if stop_emp_weight is not None:
            weighted_unique_stops_arr = (
                person_reachable.astype(np.float32) @ stop_emp_weight.astype(np.float32)
            )
        del person_reachable
    else:
        unique_stops_arr = np.array([len(s) for s in person_reachable_sets], dtype=np.int32)
        if stop_emp_weight is not None:
            weighted_unique_stops_arr = np.array(
                [float(stop_emp_weight[list(s)].sum()) if s else 0.0
                 for s in person_reachable_sets],
                dtype=np.float32,
            )
        del person_reachable_sets
    gc.collect()

    # Person-level score aggregations
    trip_df = pd.DataFrame({"person": persons_arr, "feasible": feasible_trip})
    agg = trip_df.groupby("person")["feasible"].agg(["sum", "max", "mean"])

    out = {
        "catchment_inclusive": catchment_inclusive,
        "catchment_exclusive": catchment_exclusive,
        "catchment_inclusive_weighted": catchment_incl_weighted,
        "score_sum": agg["sum"],
        "score_max": agg["max"],
        "score_mean": agg["mean"],
        "score_unique_stops": pd.Series(unique_stops_arr, index=unique_persons),
        "min_cost_trip": min_cost_trip,
    }

    if emp_weights_dest is not None:
        weighted = feasible_trip * emp_weights_dest
        trip_df_w = pd.DataFrame({"person": persons_arr, "weighted": weighted})
        agg_w = trip_df_w.groupby("person")["weighted"].agg(["sum", "max", "mean"])
        out["score_weighted_sum"] = agg_w["sum"]
        out["score_weighted_max"] = agg_w["max"]
        out["score_weighted_mean"] = agg_w["mean"]
        if stop_emp_weight is not None:
            out["score_weighted_unique_stops"] = pd.Series(
                weighted_unique_stops_arr, index=unique_persons
            )

    return out


# ---------------------------------------------------------------------------
# Kept for reference / one-off use outside execute()
# ---------------------------------------------------------------------------

def compute_v3_access_matrix(trips_df, stops_df, mode):
    """Return (N_trips x N_stops) access time matrix for a given mode.
    NOTE: holds the full matrix in RAM — use process_mode_chunked() in execute()."""
    trip_coords = trips_df[["start_x", "start_y"]].values
    stop_coords = stops_df[["x", "y"]].values

    dists = _safe_float32(cdist(trip_coords, stop_coords))
    t_access = dists * BEELINE_DISTANCE_FACTOR / MODE_SPEEDS_MPS[mode] / 60.0
    return t_access


def compute_v3_cost_matrix(access_matrix, stops_df, t_egress_arr):
    """Return (N_trips x N_stops) total trip cost matrix."""
    pt_cost = (
        stops_df["wait_min"].values
        + stops_df["ride_min"].values
        + stops_df["transfer_min"].values
    ).astype(np.float32 if access_matrix.dtype == np.float32 else np.float64)

    t_egress = t_egress_arr.astype(pt_cost.dtype)
    return access_matrix + pt_cost[np.newaxis, :] + t_egress[:, np.newaxis]


def _log_minmax_normalise(raw_values):
    """Apply log1p then min-max normalisation to a sequence of values."""
    raw = np.array(raw_values, dtype=float)
    log_raw = np.log1p(raw)
    min_v, max_v = log_raw.min(), log_raw.max()
    if max_v > min_v:
        normalised = (log_raw - min_v) / (max_v - min_v)
    else:
        normalised = np.zeros_like(log_raw)
    return normalised


def compute_employment_weights_dest(trips_df, data_path, sim_dir, method="mikrobezirk", radius_m=500.0):
    """Return normalised employment weights at trip destinations (one per trip)."""
    n = len(trips_df)
    dest_coords = trips_df[["end_x", "end_y"]].values

    if method == "facility_count":
        try:
            acts_path = os.path.join(sim_dir, "eqasim_activities.csv")
            acts = pd.read_csv(acts_path, sep=";")
            work = acts.loc[acts["purpose"] == "work", ["facility_id", "x", "y"]].copy()
            fac = work.drop_duplicates("facility_id")[["x", "y"]].values

            dists = _safe_float32(cdist(dest_coords, fac))
            raw_values = (dists <= radius_m).sum(axis=1).astype(float)
        except Exception as exc:
            print(f"WARNING compute_employment_weights_dest facility_count: {exc}")
            raw_values = np.zeros(n)

    elif method == "mikrobezirk":
        try:
            neighborhoods = _load_hannover_neighborhoods(data_path)
            _, area_map = _load_mikrobezirk_density(data_path)

            dest_gdf = gpd.GeoDataFrame(
                trips_df[["end_x", "end_y"]].copy().reset_index(drop=True),
                geometry=gpd.points_from_xy(trips_df["end_x"], trips_df["end_y"]),
                crs=f"EPSG:{SOURCE_EPSG}",
            )
            joined = gpd.sjoin(
                dest_gdf,
                neighborhoods[["neighborhood_id", "geometry"]],
                how="left",
                predicate="within",
            )
            # For duplicated indices (multiple matches), keep first
            joined = joined[~joined.index.duplicated(keep="first")]

            # Load work facilities
            acts_path = os.path.join(sim_dir, "eqasim_activities.csv")
            acts = pd.read_csv(acts_path, sep=";")
            work = acts.loc[acts["purpose"] == "work", ["facility_id", "x", "y"]].copy()
            fac = work.drop_duplicates("facility_id")
            fac_gdf = gpd.GeoDataFrame(
                fac,
                geometry=gpd.points_from_xy(fac["x"], fac["y"]),
                crs=f"EPSG:{SOURCE_EPSG}",
            )
            fac_joined = gpd.sjoin(
                fac_gdf,
                neighborhoods[["neighborhood_id", "geometry"]],
                how="left",
                predicate="within",
            )
            fac_count = (
                fac_joined.dropna(subset=["neighborhood_id"])
                .groupby("neighborhood_id")
                .size()
                .reset_index(name="fac_count")
            )

            # Build density per neighbourhood
            neigh_density = {}
            for _, row in fac_count.iterrows():
                nid = str(row["neighborhood_id"]).strip()
                a = area_map.get(nid, 0.0)
                density = float(row["fac_count"]) / (a / 1e6) if a > 0 else 0.0
                neigh_density[nid] = density

            # Map to each trip destination
            raw_values = np.zeros(n, dtype=float)
            for i in range(n):
                if i in joined.index:
                    nid_raw = joined.loc[i, "neighborhood_id"]
                    if pd.notna(nid_raw):
                        nid = str(nid_raw).strip()
                        raw_values[i] = neigh_density.get(nid, 0.0)
        except Exception as exc:
            print(f"WARNING compute_employment_weights_dest mikrobezirk: {exc}")
            raw_values = np.zeros(n)

    else:
        print(f"WARNING compute_employment_weights_dest: unknown method '{method}'")
        raw_values = np.zeros(n)

    normalised = _log_minmax_normalise(raw_values)
    print(
        f"INFO compute_employment_weights_dest ({method}): "
        f"mean={normalised.mean():.3f}, max={normalised.max():.3f}, "
        f"n_nonzero={int((normalised > 0).sum())} / {n}"
    )
    return normalised


def compute_expansion_factor_v3(catchment_walk, catchment_bike):
    """Return DataFrame with stop-level expansion factor categories."""
    walk = np.asarray(catchment_walk, dtype=float)
    bike = np.asarray(catchment_bike, dtype=float)

    n = len(walk)
    ef = np.full(n, np.nan)
    cat = np.full(n, "", dtype=object)

    ratio_mask = walk > 0
    unlocked_mask = (walk == 0) & (bike > 0)
    inactive_mask = (walk == 0) & (bike == 0)

    ef[ratio_mask] = bike[ratio_mask] / walk[ratio_mask]
    cat[ratio_mask] = "ratio"
    cat[unlocked_mask] = "unlocked"
    ef[inactive_mask] = 1.0
    cat[inactive_mask] = "inactive"

    return pd.DataFrame(
        {"walk": walk, "bike": bike, "category": cat, "expansion_factor": ef}
    )


# ---------------------------------------------------------------------------
# Plot helpers
# ---------------------------------------------------------------------------

_MAP_CMAP = "magma_r"


def _plot_score_comparison(
    persons_df,
    analysis_path,
    walk_col,
    bike_col,
    output_name,
    title_suffix,
    cbar_label,
    output_prefix,
):
    """Three-panel walk | bike | difference person-level scatter, log1p normalised."""
    frames = {}
    pooled_log = []
    for mode, col in [("walk", walk_col), ("bike", bike_col)]:
        if col not in persons_df.columns:
            continue
        valid = persons_df[persons_df[col].notna() & np.isfinite(persons_df[col])].copy()
        if valid.empty:
            continue
        frames[mode] = valid
        pooled_log.append(np.log1p(valid[col].to_numpy()))

    if not pooled_log:
        print(f"WARNING _plot_score_comparison: no data for {output_name}")
        return ""

    pooled = np.concatenate(pooled_log)
    L_min, L_max = float(pooled.min()), float(pooled.max())

    def _norm(values):
        lv = np.log1p(values)
        return np.zeros_like(lv) if L_max <= L_min else (lv - L_min) / (L_max - L_min)

    fig, axes = plt.subplots(1, 3, figsize=(21, 6))

    for idx, (mode, col, label) in enumerate([
        ("walk", walk_col, f"Walk {title_suffix}"),
        ("bike", bike_col, f"Micromobility {title_suffix}"),
    ]):
        ax = axes[idx]
        if mode not in frames:
            ax.set_visible(False)
            continue
        valid = frames[mode]
        gdf = gpd.GeoDataFrame(
            valid,
            geometry=gpd.points_from_xy(valid["x"], valid["y"]),
            crs=f"EPSG:{SOURCE_EPSG}",
        ).to_crs(f"EPSG:{BASEMAP_EPSG}")
        sc = ax.scatter(
            gdf.geometry.x, gdf.geometry.y,
            c=_norm(valid[col].to_numpy()),
            cmap=_MAP_CMAP, s=1, alpha=0.6, vmin=0.0, vmax=1.0,
        )
        try:
            cx.add_basemap(ax, source=cx.providers.CartoDB.Positron, alpha=0.5)
        except Exception:
            pass
        ax.set_title(label, fontsize=14, fontweight="bold")
        ax.set_xlabel("")
        ax.set_ylabel("")
        ax.set_aspect("equal")
        plt.colorbar(sc, ax=ax).set_label(cbar_label, fontsize=10)

    diff_ax = axes[2]
    if "walk" in frames and "bike" in frames:
        common = persons_df[
            persons_df[walk_col].notna() & np.isfinite(persons_df[walk_col]) &
            persons_df[bike_col].notna() & np.isfinite(persons_df[bike_col])
        ].copy()
        diff = common[bike_col].to_numpy() - common[walk_col].to_numpy()
        gdf_diff = gpd.GeoDataFrame(
            common,
            geometry=gpd.points_from_xy(common["x"], common["y"]),
            crs=f"EPSG:{SOURCE_EPSG}",
        ).to_crs(f"EPSG:{BASEMAP_EPSG}")
        sc_d = diff_ax.scatter(
            gdf_diff.geometry.x, gdf_diff.geometry.y,
            c=diff, cmap=_MAP_CMAP, s=1, alpha=0.6,
            vmin=0.0, vmax=diff.max() if diff.max() > 0 else 1.0,
        )
        try:
            cx.add_basemap(diff_ax, source=cx.providers.CartoDB.Positron, alpha=0.5)
        except Exception:
            pass
        diff_ax.set_title("Difference (Micromobility - Walk)", fontsize=14, fontweight="bold")
        diff_ax.set_xlabel("")
        diff_ax.set_ylabel("")
        diff_ax.set_aspect("equal")
        plt.colorbar(sc_d, ax=diff_ax).set_label("Raw score difference", fontsize=10)
    else:
        diff_ax.set_visible(False)

    plt.tight_layout()
    out = os.path.join(analysis_path, f"{output_prefix}_{output_name}.png")
    plt.savefig(out, dpi=PLOT_DPI, bbox_inches="tight")
    plt.close()
    print(f"SUCCESS _plot_score_comparison: {out}")
    return out


def _stops_to_viz(stops_df):
    """Convert stops DataFrame to GeoDataFrame in visualisation CRS."""
    gdf = gpd.GeoDataFrame(
        stops_df.copy(),
        geometry=gpd.points_from_xy(stops_df["x"], stops_df["y"]),
        crs=f"EPSG:{SOURCE_EPSG}",
    )
    return gdf.to_crs(f"EPSG:{BASEMAP_EPSG}")


def _add_basemap_safe(ax):
    """Add basemap, silently ignoring failures."""
    try:
        cx.add_basemap(ax, source=cx.providers.CartoDB.Positron, alpha=0.5)
    except Exception:
        pass


def _add_pt_overlay_if_available(ax, stops_df, nodes_df, pt_links_df):
    """Add PT network overlay if data is available."""
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


def plot_stop_catchments_v3(
    stops_df,
    analysis_path,
    weight_label="unweighted",
    catchment_method="inclusive",
    nodes_df=None,
    pt_links_df=None,
):
    """Side-by-side scatter maps of walk vs bike stop catchments (v3)."""
    col_walk = f"catchment_walk_{catchment_method}_{weight_label}"
    col_bike = f"catchment_bike_{catchment_method}_{weight_label}"

    df = stops_df.dropna(subset=["x", "y"]).copy()
    if df.empty or col_walk not in df.columns or col_bike not in df.columns:
        print(
            f"WARNING plot_stop_catchments_v3: missing columns "
            f"{col_walk}/{col_bike}"
        )
        return ""

    stops_viz = _stops_to_viz(df)
    all_vals = pd.concat([df[col_walk], df[col_bike]])
    pos_vals = all_vals[all_vals > 0]
    vmax = float(np.percentile(pos_vals, 95)) if not pos_vals.empty else float(all_vals.max())

    fig, axes = plt.subplots(1, 2, figsize=(20, 10))
    for ax, col, label in zip(
        axes,
        [col_walk, col_bike],
        ["Walk", "Micromobility (bike)"],
    ):
        sc = ax.scatter(
            stops_viz.geometry.x,
            stops_viz.geometry.y,
            c=df[col].values,
            s=50,
            cmap="YlOrRd",
            vmin=0,
            vmax=vmax,
            alpha=0.7,
            edgecolors="black",
            linewidths=0.5,
            zorder=5,
        )
        _add_basemap_safe(ax)
        _add_pt_overlay_if_available(ax, stops_df, nodes_df, pt_links_df)
        cbar = plt.colorbar(sc, ax=ax, shrink=0.6)
        cbar.set_label("Catchment Size (# people)", fontsize=10)
        ax.set_title(
            f"{label} Catchment Areas ({catchment_method}, {weight_label})",
            fontsize=12,
            fontweight="bold",
        )
        ax.set_xlabel("")
        ax.set_ylabel("")
        ax.set_aspect("equal")

    plt.tight_layout()
    out = os.path.join(
        analysis_path,
        f"{OUTPUT_PREFIX_V3}_stop_catchments_{catchment_method}_{weight_label}.png",
    )
    plt.savefig(out, dpi=PLOT_DPI, bbox_inches="tight")
    plt.close()
    print(f"SUCCESS plot_stop_catchments_v3 ({catchment_method}, {weight_label}): {out}")
    return out


def plot_catchment_by_neighbourhood_v3(
    stops_df,
    analysis_path,
    data_path,
    weight_label="unweighted",
    catchment_method="inclusive",
    nodes_df=None,
    pt_links_df=None,
):
    """Aggregate v3 stop catchment values to neighbourhood mean and plot."""
    col_walk = f"catchment_walk_{catchment_method}_{weight_label}"
    col_bike = f"catchment_bike_{catchment_method}_{weight_label}"

    try:
        neighborhoods_gdf = _load_hannover_neighborhoods(data_path)
    except Exception as exc:
        print(f"WARNING plot_catchment_by_neighbourhood_v3: {exc}")
        return ""

    df = stops_df.dropna(subset=["x", "y"]).copy()
    if df.empty:
        print("WARNING plot_catchment_by_neighbourhood_v3: no valid stop data")
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

    neigh_viz = neighborhoods_gdf.to_crs(f"EPSG:{BASEMAP_EPSG}")
    precomputed = {}
    all_vals = []

    for mode, col in [("walk", col_walk), ("bike", col_bike)]:
        if col not in joined.columns:
            continue
        sub = joined.dropna(subset=["neighborhood_id", col]).copy()
        agg = sub.groupby(
            ["neighborhood_id", "neighborhood_name"], as_index=False
        ).agg(value=(col, "mean"))
        merged = neigh_viz.merge(
            agg, on=["neighborhood_id", "neighborhood_name"], how="left"
        )
        merged["value"] = merged["value"].fillna(0.0)
        precomputed[mode] = merged
        all_vals.extend(merged["value"].tolist())

    if not precomputed:
        print(
            f"WARNING plot_catchment_by_neighbourhood_v3: no data for "
            f"{catchment_method}/{weight_label}"
        )
        return ""

    shared_vmax = float(np.percentile(all_vals, 95)) if all_vals else 1.0
    modes = ["walk", "bike"]
    mode_labels = {"walk": "Walk", "bike": "Micromobility"}
    title_suffix = f"({catchment_method}, {weight_label})"

    fig, axes = plt.subplots(1, 2, figsize=(20, 8))
    for ax, mode in zip(axes, modes):
        if mode not in precomputed:
            ax.set_visible(False)
            continue
        _render_neighborhood_panel(
            ax, precomputed[mode], neigh_viz, "value", shared_vmax,
            f"{mode_labels[mode]} - Mean Catchment {title_suffix}",
            "Mean catchment size (# trips / people)",
            stops_df=stops_df, nodes_df=nodes_df, pt_links_df=pt_links_df,
            show_missing_grey=True,
        )
    plt.suptitle(
        f"Stop Catchment by Neighbourhood {title_suffix} (shared scale)",
        fontsize=14, fontweight="bold", y=1.01,
    )
    plt.tight_layout()
    out1 = os.path.join(
        analysis_path,
        f"{OUTPUT_PREFIX_V3}_catchment_by_neighbourhood_{catchment_method}_{weight_label}_shared.png",
    )
    plt.savefig(out1, dpi=PLOT_DPI, bbox_inches="tight")
    plt.close()
    print(f"SUCCESS plot_catchment_by_neighbourhood_v3 (shared): {out1}")

    fig, axes = plt.subplots(1, 2, figsize=(20, 8))
    for ax, mode in zip(axes, modes):
        if mode not in precomputed:
            ax.set_visible(False)
            continue
        mode_vmax = max(float(np.percentile(precomputed[mode]["value"], 95)), 1.0)
        _render_neighborhood_panel(
            ax, precomputed[mode], neigh_viz, "value", mode_vmax,
            f"{mode_labels[mode]} - Mean Catchment {title_suffix}",
            "Mean catchment size (# trips / people)",
            stops_df=stops_df, nodes_df=nodes_df, pt_links_df=pt_links_df,
            show_missing_grey=True,
        )
    plt.suptitle(
        f"Stop Catchment by Neighbourhood {title_suffix} (per-mode scale)",
        fontsize=14, fontweight="bold", y=1.01,
    )
    plt.tight_layout()
    out2 = os.path.join(
        analysis_path,
        f"{OUTPUT_PREFIX_V3}_catchment_by_neighbourhood_{catchment_method}_{weight_label}.png",
    )
    plt.savefig(out2, dpi=PLOT_DPI, bbox_inches="tight")
    plt.close()
    print(f"SUCCESS plot_catchment_by_neighbourhood_v3 (per-mode): {out2}")

    return out2


def plot_person_coverage_v3(
    persons_df,
    analysis_path,
    stops_df=None,
    nodes_df=None,
    pt_links_df=None,
):
    """Person coverage map: walk covered / micromobility only / underserved (v3, trip-based)."""
    # Use max: person is covered if at least one of their trips is feasible
    col_walk = "score_walk_max"
    col_bike = "score_bike_max"

    df = persons_df.dropna(subset=["x", "y"]).copy()
    if df.empty or col_walk not in df.columns or col_bike not in df.columns:
        print("WARNING plot_person_coverage_v3: missing score columns")
        return ""

    walk_covered = df[col_walk] > 0
    bike_covered = df[col_bike] > 0

    conditions = [
        walk_covered,
        (~walk_covered) & bike_covered,
        (~walk_covered) & (~bike_covered),
    ]
    choices = ["walk", "bike_only", "underserved"]
    df["coverage_category"] = np.select(conditions, choices, default="underserved")

    color_map = {"walk": "#9467bd", "bike_only": "#ff7f0e", "underserved": "#d62728"}
    colors = df["coverage_category"].map(color_map)

    persons_gdf = gpd.GeoDataFrame(
        df, geometry=gpd.points_from_xy(df["x"], df["y"]), crs=f"EPSG:{SOURCE_EPSG}"
    ).to_crs(f"EPSG:{BASEMAP_EPSG}")

    fig, ax = plt.subplots(figsize=(12, 12))
    ax.scatter(
        persons_gdf.geometry.x,
        persons_gdf.geometry.y,
        c=colors,
        s=1,
        alpha=0.6,
        rasterized=True,
        zorder=5,
    )
    _add_basemap_safe(ax)

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

    from matplotlib.patches import Patch
    legend_elements = [
        Patch(facecolor=color_map["walk"], label="Walk catchment (60 min budget)"),
        Patch(facecolor=color_map["bike_only"], label="Micromobility only (60 min budget)"),
        Patch(facecolor=color_map["underserved"], label="Underserved (no catchment)"),
    ] + (handles if handles else [])
    ax.legend(handles=legend_elements, loc="upper right", fontsize=9, framealpha=0.9)

    ax.set_title("Person PT Coverage - V3 (60 min budget)", fontsize=14, fontweight="bold")
    ax.set_xlabel("")
    ax.set_ylabel("")
    ax.set_aspect("equal")
    plt.tight_layout()

    out = os.path.join(analysis_path, f"{OUTPUT_PREFIX_V3}_person_coverage.png")
    plt.savefig(out, dpi=PLOT_DPI, bbox_inches="tight")
    plt.close()
    print(f"SUCCESS plot_person_coverage_v3: {out}")
    return out


def plot_person_scores_v3(
    persons_df,
    analysis_path,
    weight_label="unweighted",
    score_agg="max",
    nodes_df=None,
    pt_links_df=None,
):
    """Scatter map of persons coloured by v3 accessibility score (default: max)."""
    col_walk = f"score_walk_{weight_label}_{score_agg}" if weight_label != "unweighted" else f"score_walk_{score_agg}"
    col_bike = f"score_bike_{weight_label}_{score_agg}" if weight_label != "unweighted" else f"score_bike_{score_agg}"

    # Try unweighted column name pattern
    if col_walk not in persons_df.columns:
        col_walk = f"score_walk_{score_agg}"
    if col_bike not in persons_df.columns:
        col_bike = f"score_bike_{score_agg}"

    df = persons_df.dropna(subset=["x", "y"]).copy()
    if df.empty or col_walk not in df.columns or col_bike not in df.columns:
        print(
            f"WARNING plot_person_scores_v3: missing columns "
            f"{col_walk}/{col_bike}"
        )
        return ""

    persons_gdf = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df["x"], df["y"]),
        crs=f"EPSG:{SOURCE_EPSG}",
    ).to_crs(f"EPSG:{BASEMAP_EPSG}")

    all_vals = pd.concat([df[col_walk], df[col_bike]])
    pos_vals = all_vals[all_vals > 0]
    vmax = float(np.percentile(pos_vals, 95)) if not pos_vals.empty else float(all_vals.max())

    fig, axes = plt.subplots(1, 2, figsize=(20, 10))
    for ax, col, label in zip(
        axes,
        [col_walk, col_bike],
        ["Walk", "Micromobility (bike)"],
    ):
        sc = ax.scatter(
            persons_gdf.geometry.x,
            persons_gdf.geometry.y,
            c=df[col].values,
            s=1,
            cmap="YlOrRd",
            vmin=0,
            vmax=vmax,
            alpha=0.6,
            rasterized=True,
            zorder=5,
        )
        _add_basemap_safe(ax)
        _add_pt_overlay_if_available(ax, None, nodes_df, pt_links_df)
        cbar = plt.colorbar(sc, ax=ax, shrink=0.6)
        cbar.set_label(f"Accessibility Score ({score_agg})", fontsize=10)
        ax.set_title(
            f"{label} Person Score ({score_agg}, {weight_label})\n"
            f"[Note: score_agg=max is default]",
            fontsize=11,
            fontweight="bold",
        )
        ax.set_xlabel("")
        ax.set_ylabel("")
        ax.set_aspect("equal")

    plt.tight_layout()
    out = os.path.join(
        analysis_path,
        f"{OUTPUT_PREFIX_V3}_person_scores_{weight_label}_{score_agg}.png",
    )
    plt.savefig(out, dpi=PLOT_DPI, bbox_inches="tight")
    plt.close()
    print(f"SUCCESS plot_person_scores_v3: {out}")
    return out


def plot_person_scores_by_neighbourhood_v3(
    persons_df,
    analysis_path,
    data_path,
    weight_label="unweighted",
    score_agg="max",
    nodes_df=None,
    pt_links_df=None,
):
    """Aggregate v3 person scores to neighbourhood mean and plot."""
    col_walk = f"score_walk_{score_agg}"
    col_bike = f"score_bike_{score_agg}"
    if weight_label == "weighted":
        col_walk = f"score_walk_weighted_{score_agg}"
        col_bike = f"score_bike_weighted_{score_agg}"

    try:
        neighborhoods_gdf = _load_hannover_neighborhoods(data_path)
    except Exception as exc:
        print(f"WARNING plot_person_scores_by_neighbourhood_v3: {exc}")
        return ""

    df = persons_df.dropna(subset=["x", "y"]).copy()
    if df.empty:
        print("WARNING plot_person_scores_by_neighbourhood_v3: no valid person data")
        return ""

    persons_gdf = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df["x"], df["y"]),
        crs=f"EPSG:{SOURCE_EPSG}",
    )
    joined = gpd.sjoin(
        persons_gdf,
        neighborhoods_gdf[["neighborhood_id", "neighborhood_name", "geometry"]],
        how="left",
        predicate="within",
    )

    neigh_viz = neighborhoods_gdf.to_crs(f"EPSG:{BASEMAP_EPSG}")
    precomputed = {}
    all_vals = []

    for mode, col in [("walk", col_walk), ("bike", col_bike)]:
        if col not in joined.columns:
            continue
        sub = joined.dropna(subset=["neighborhood_id", col]).copy()
        agg = sub.groupby(
            ["neighborhood_id", "neighborhood_name"], as_index=False
        ).agg(value=(col, "mean"))
        merged = neigh_viz.merge(
            agg, on=["neighborhood_id", "neighborhood_name"], how="left"
        )
        merged["value"] = merged["value"].fillna(0.0)
        precomputed[mode] = merged
        all_vals.extend(merged["value"].tolist())

    if not precomputed:
        print(
            f"WARNING plot_person_scores_by_neighbourhood_v3: no data for "
            f"{weight_label}/{score_agg}"
        )
        return ""

    shared_vmax = float(np.percentile(all_vals, 95)) if all_vals else 1.0
    modes = ["walk", "bike"]
    mode_labels = {"walk": "Walk", "bike": "Micromobility"}
    title_suffix = f"({score_agg}, {weight_label})"

    fig, axes = plt.subplots(1, 2, figsize=(20, 8))
    for ax, mode in zip(axes, modes):
        if mode not in precomputed:
            ax.set_visible(False)
            continue
        _render_neighborhood_panel(
            ax, precomputed[mode], neigh_viz, "value", shared_vmax,
            f"{mode_labels[mode]} - Mean Person Score {title_suffix}",
            "Mean accessibility score",
            stops_df=None, nodes_df=nodes_df, pt_links_df=pt_links_df,
            show_missing_grey=True,
        )
    plt.suptitle(
        f"Person Scores by Neighbourhood {title_suffix} (shared scale)",
        fontsize=14, fontweight="bold", y=1.01,
    )
    plt.tight_layout()
    out1 = os.path.join(
        analysis_path,
        f"{OUTPUT_PREFIX_V3}_person_scores_by_neighbourhood_{weight_label}_{score_agg}_shared.png",
    )
    plt.savefig(out1, dpi=PLOT_DPI, bbox_inches="tight")
    plt.close()
    print(f"SUCCESS plot_person_scores_by_neighbourhood_v3 (shared): {out1}")

    fig, axes = plt.subplots(1, 2, figsize=(20, 8))
    for ax, mode in zip(axes, modes):
        if mode not in precomputed:
            ax.set_visible(False)
            continue
        mode_vmax = max(float(np.percentile(precomputed[mode]["value"], 95)), 1.0)
        _render_neighborhood_panel(
            ax, precomputed[mode], neigh_viz, "value", mode_vmax,
            f"{mode_labels[mode]} - Mean Person Score {title_suffix}",
            "Mean accessibility score",
            stops_df=None, nodes_df=nodes_df, pt_links_df=pt_links_df,
            show_missing_grey=True,
        )
    plt.suptitle(
        f"Person Scores by Neighbourhood {title_suffix} (per-mode scale)",
        fontsize=14, fontweight="bold", y=1.01,
    )
    plt.tight_layout()
    out2 = os.path.join(
        analysis_path,
        f"{OUTPUT_PREFIX_V3}_person_scores_by_neighbourhood_{weight_label}_{score_agg}.png",
    )
    plt.savefig(out2, dpi=PLOT_DPI, bbox_inches="tight")
    plt.close()
    print(f"SUCCESS plot_person_scores_by_neighbourhood_v3 (per-mode): {out2}")

    return out2


def plot_expansion_factor_v3(
    stops_df,
    analysis_path,
    weight_label="unweighted",
    nodes_df=None,
    pt_links_df=None,
):
    """Three expansion factor plots for v3: ratio map, unlocked stops, combined."""
    ef_col = f"expansion_factor_{weight_label}"
    cat_col = f"category_{weight_label}"
    cb_col = f"catchment_bike_inclusive_{weight_label}"

    df = stops_df.dropna(subset=["x", "y"]).copy()
    if df.empty or ef_col not in df.columns or cat_col not in df.columns:
        print(f"WARNING plot_expansion_factor_v3: missing columns for {weight_label}")
        return []

    stops_viz = _stops_to_viz(df)
    output_files = []

    # Plot 1: Ratio map
    ratio_mask = df[cat_col] == "ratio"
    df_ratio = df[ratio_mask]
    viz_ratio = stops_viz[ratio_mask]

    if not df_ratio.empty:
        ef_vals = df_ratio[ef_col].values
        finite_ef = ef_vals[np.isfinite(ef_vals)]
        vmax_r = float(np.percentile(finite_ef, 95)) if len(finite_ef) > 0 else 5.0

        fig, ax = plt.subplots(figsize=(12, 12))
        sc = ax.scatter(
            viz_ratio.geometry.x,
            viz_ratio.geometry.y,
            c=ef_vals,
            s=60,
            cmap="YlOrRd",
            vmin=1.0,
            vmax=vmax_r,
            alpha=0.8,
            edgecolors="black",
            linewidths=0.8,
            zorder=12,
        )
        _add_basemap_safe(ax)
        _add_pt_overlay_if_available(ax, stops_df, nodes_df, pt_links_df)
        cbar = plt.colorbar(sc, ax=ax, shrink=0.6)
        cbar.set_label("Expansion Factor", fontsize=10)
        ax.set_title(
            f"Micromobility Expansion Factor - {weight_label.capitalize()}",
            fontsize=14, fontweight="bold",
        )
        ax.set_xlabel("")
        ax.set_ylabel("")
        ax.set_aspect("equal")
        plt.tight_layout()
        out1 = os.path.join(
            analysis_path,
            f"{OUTPUT_PREFIX_V3}_expansion_factor_{weight_label}_ratio.png",
        )
        plt.savefig(out1, dpi=PLOT_DPI, bbox_inches="tight")
        plt.close()
        print(f"SUCCESS plot_expansion_factor_v3 ratio: {out1}")
        output_files.append(out1)

    # Plot 2: Unlocked stops
    unlocked_mask = df[cat_col] == "unlocked"
    df_unlocked = df[unlocked_mask]
    viz_unlocked = stops_viz[unlocked_mask]

    if not df_unlocked.empty and cb_col in df_unlocked.columns:
        cb_vals = df_unlocked[cb_col].values
        pos_cb = cb_vals[cb_vals > 0]
        vmax_u = float(np.percentile(pos_cb, 95)) if len(pos_cb) > 0 else float(cb_vals.max())

        fig, ax = plt.subplots(figsize=(12, 12))
        sc = ax.scatter(
            viz_unlocked.geometry.x,
            viz_unlocked.geometry.y,
            c=cb_vals,
            s=60,
            cmap="Blues",
            vmin=0,
            vmax=vmax_u,
            alpha=0.8,
            edgecolors="black",
            linewidths=0.8,
            zorder=12,
        )
        _add_basemap_safe(ax)
        _add_pt_overlay_if_available(ax, stops_df, nodes_df, pt_links_df)
        cbar = plt.colorbar(sc, ax=ax, shrink=0.6)
        cbar.set_label("Bike Catchment Size (# people)", fontsize=10)
        ax.set_title(
            f"Unlocked Stops (walk=0, bike>0) - {weight_label.capitalize()}",
            fontsize=14, fontweight="bold",
        )
        ax.set_xlabel("")
        ax.set_ylabel("")
        ax.set_aspect("equal")
        plt.tight_layout()
        out2 = os.path.join(
            analysis_path,
            f"{OUTPUT_PREFIX_V3}_expansion_factor_{weight_label}_unlocked.png",
        )
        plt.savefig(out2, dpi=PLOT_DPI, bbox_inches="tight")
        plt.close()
        print(f"SUCCESS plot_expansion_factor_v3 unlocked: {out2}")
        output_files.append(out2)

    # Plot 3: Combined
    fig, ax = plt.subplots(figsize=(12, 12))

    if not df_ratio.empty:
        ef_vals = df_ratio[ef_col].values
        finite_ef = ef_vals[np.isfinite(ef_vals)]
        vmax_r = float(np.percentile(finite_ef, 95)) if len(finite_ef) > 0 else 5.0
        sc = ax.scatter(
            viz_ratio.geometry.x,
            viz_ratio.geometry.y,
            c=ef_vals,
            s=60,
            cmap="YlOrRd",
            vmin=1.0,
            vmax=vmax_r,
            alpha=0.8,
            edgecolors="black",
            linewidths=0.8,
            zorder=12,
        )
        plt.colorbar(sc, ax=ax, shrink=0.6).set_label("Expansion Factor", fontsize=10)

    if not df_unlocked.empty:
        ax.scatter(
            viz_unlocked.geometry.x,
            viz_unlocked.geometry.y,
            s=60,
            c="dimgrey",
            alpha=0.8,
            edgecolors="black",
            linewidths=0.8,
            zorder=13,
        )

    inactive_mask = df[cat_col] == "inactive"
    df_inactive = df[inactive_mask]
    viz_inactive = stops_viz[inactive_mask]
    if not df_inactive.empty:
        ax.scatter(
            viz_inactive.geometry.x,
            viz_inactive.geometry.y,
            s=20,
            c="lightgrey",
            alpha=0.5,
            edgecolors="none",
            zorder=4,
        )

    _add_basemap_safe(ax)
    _add_pt_overlay_if_available(ax, stops_df, nodes_df, pt_links_df)

    legend_handles = []
    if not df_unlocked.empty:
        legend_handles.append(
            Line2D([0], [0], marker="o", color="w", markerfacecolor="grey",
                   markersize=8, markeredgecolor="black", label="Unlocked (walk=0, bike>0)")
        )
    if not df_inactive.empty:
        legend_handles.append(
            Line2D([0], [0], marker="o", color="w", markerfacecolor="lightgrey",
                   markersize=6, markeredgecolor="none", label="Inactive (walk=0, bike=0)")
        )
    if legend_handles:
        ax.legend(handles=legend_handles, loc="upper right", fontsize=9, framealpha=0.9)

    ax.set_title(
        f"Expansion Factor Combined - {weight_label}",
        fontsize=13, fontweight="bold",
    )
    ax.set_aspect("equal")
    plt.tight_layout()
    out3 = os.path.join(
        analysis_path,
        f"{OUTPUT_PREFIX_V3}_expansion_factor_{weight_label}_combined.png",
    )
    plt.savefig(out3, dpi=PLOT_DPI, bbox_inches="tight")
    plt.close()
    print(f"SUCCESS plot_expansion_factor_v3 combined: {out3}")
    output_files.append(out3)

    return output_files


def plot_proportion_v3(
    persons_df,
    analysis_path,
    col_walk="score_walk_mean",
    col_bike="score_bike_mean",
    output_suffix="",
    nodes_df=None,
    pt_links_df=None,
):
    """Single-panel scatter: proportion of feasible trips per person, walk (purple) and bike (orange) overlaid.

    Answers: What share of each person's home-origin trips are reachable within 60 min?
    Walk-only persons (purple) vs those who need micromobility (orange) to reach stops.
    Use output_suffix to distinguish weighted from unweighted variants.
    """

    df = persons_df.dropna(subset=["x", "y"]).copy()
    if df.empty or col_walk not in df.columns or col_bike not in df.columns:
        print("WARNING plot_proportion_v3: missing score columns")
        return ""

    persons_gdf = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df["x"], df["y"]),
        crs=f"EPSG:{SOURCE_EPSG}",
    ).to_crs(f"EPSG:{BASEMAP_EPSG}")

    fig, ax = plt.subplots(figsize=(12, 12))

    # Plot walk first (purple, lower z-order) then bike (orange) on top
    sc_walk = ax.scatter(
        persons_gdf.geometry.x,
        persons_gdf.geometry.y,
        c=df[col_walk].values,
        cmap="Purples",
        s=1,
        alpha=0.5,
        vmin=0.0,
        vmax=1.0,
        rasterized=True,
        zorder=5,
        label="Walk",
    )
    sc_bike = ax.scatter(
        persons_gdf.geometry.x,
        persons_gdf.geometry.y,
        c=df[col_bike].values,
        cmap="Oranges",
        s=1,
        alpha=0.5,
        vmin=0.0,
        vmax=1.0,
        rasterized=True,
        zorder=6,
        label="Micromobility",
    )

    _add_basemap_safe(ax)

    cb_walk = plt.colorbar(sc_walk, ax=ax, shrink=0.45, pad=0.01, anchor=(0.0, 0.6))
    cb_walk.set_label("Walk proportion (0–1)", fontsize=9)

    cb_bike = plt.colorbar(sc_bike, ax=ax, shrink=0.45, pad=0.01, anchor=(0.0, 0.0))
    cb_bike.set_label("Micromobility proportion (0–1)", fontsize=9)

    legend_elements = [
        Line2D([0], [0], marker="o", color="w", markerfacecolor="#9467bd",
               markersize=7, markeredgecolor="none", label="Walk"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor="#ff7f0e",
               markersize=7, markeredgecolor="none", label="Micromobility"),
    ]
    ax.legend(handles=legend_elements, loc="upper right", fontsize=9, framealpha=0.9)

    ax.set_title(
        "Proportion of Feasible Home-Origin Trips per Person (60 min budget)",
        fontsize=13, fontweight="bold",
    )
    ax.set_xlabel("")
    ax.set_ylabel("")
    ax.set_aspect("equal")
    plt.tight_layout()

    out = os.path.join(analysis_path, f"{OUTPUT_PREFIX_V3}_score_comparison_proportion{output_suffix}.png")
    plt.savefig(out, dpi=PLOT_DPI, bbox_inches="tight")
    plt.close()
    print(f"SUCCESS plot_proportion_v3: {out}")

    # --- Points variant: flat colors, no colormap ---
    # Walk persons shown as solid purple, bike as solid orange.
    # Alpha encodes the proportion value so higher feasibility = more opaque.
    fig, ax = plt.subplots(figsize=(12, 12))

    walk_vals = df[col_walk].values.clip(0.0, 1.0)
    bike_vals = df[col_bike].values.clip(0.0, 1.0)

    # Walk layer: purple, alpha per-point via RGBA array
    walk_rgba = np.column_stack([
        np.full(len(df), 0.580),   # R  (#9467bd)
        np.full(len(df), 0.404),   # G
        np.full(len(df), 0.741),   # B
        walk_vals,                  # A = proportion
    ])
    ax.scatter(
        persons_gdf.geometry.x,
        persons_gdf.geometry.y,
        c=walk_rgba,
        s=1,
        rasterized=True,
        zorder=5,
    )

    # Bike layer: orange, alpha per-point
    bike_rgba = np.column_stack([
        np.full(len(df), 1.000),   # R  (#ff7f0e)
        np.full(len(df), 0.498),   # G
        np.full(len(df), 0.055),   # B
        bike_vals,                  # A = proportion
    ])
    ax.scatter(
        persons_gdf.geometry.x,
        persons_gdf.geometry.y,
        c=bike_rgba,
        s=1,
        rasterized=True,
        zorder=6,
    )

    _add_basemap_safe(ax)

    legend_elements_pts = [
        Line2D([0], [0], marker="o", color="w", markerfacecolor="#9467bd",
               markersize=7, markeredgecolor="none", label="Walk (opacity ∝ proportion)"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor="#ff7f0e",
               markersize=7, markeredgecolor="none", label="Micromobility (opacity ∝ proportion)"),
    ]
    ax.legend(handles=legend_elements_pts, loc="upper right", fontsize=9, framealpha=0.9)

    ax.set_title(
        "Proportion of Feasible Home-Origin Trips per Person (60 min budget)",
        fontsize=13, fontweight="bold",
    )
    ax.set_xlabel("")
    ax.set_ylabel("")
    ax.set_aspect("equal")
    plt.tight_layout()

    out_pts = os.path.join(analysis_path, f"{OUTPUT_PREFIX_V3}_score_comparison_proportion{output_suffix}_points.png")
    plt.savefig(out_pts, dpi=PLOT_DPI, bbox_inches="tight")
    plt.close()
    print(f"SUCCESS plot_proportion_v3 (points): {out_pts}")
    return out


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
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

def execute(context):
    """Main v3 accessibility analysis using home-origin trips and per-trip cost."""
    output_path = context.config("output_path")
    analysis_path = context.config("analysis_path")
    data_path = context.config("data_path")
    sim_output_dir = context.config("simulation_output_dir")

    os.makedirs(analysis_path, exist_ok=True)

    sim_dir = os.path.join(output_path, sim_output_dir)
    
    employment_method = _config(context, "employment_weight_method", default="mikrobezirk")
    emp_radius_m = float(_config(context, "employment_radius_m", default=500.0))

    schedule_xml = os.path.join(sim_dir, "output_transitSchedule.xml.gz")
    network_xml = os.path.join(sim_dir, "output_network.xml.gz")

    # Phase 1: Load base data
    print("INFO v3 Phase 1: loading base data")
    try:
        stops_df = _extract_stops_from_schedule(schedule_xml)
        nodes_df, pt_links_df = _extract_pt_network_from_matsim(network_xml)
        persons_df = read_persons(sim_dir)
        persons_df = persons_df.dropna(subset=["x", "y"]).copy()
        stops_df = stops_df.dropna(subset=["x", "y"]).copy()
        print(f"INFO v3: {len(stops_df)} stops, {len(persons_df)} persons loaded")
    except Exception as exc:
        print(f"WARNING v3 Phase 1 failed: {exc}")
        return

    # Phase 2: Stop service metrics
    print("INFO v3 Phase 2: computing stop service metrics")
    try:
        legs_path = _discover_eqasim_legs_file(sim_dir)
        pt_path = os.path.join(sim_dir, "eqasim_pt.csv")

        if legs_path is None or not os.path.exists(pt_path):
            raise FileNotFoundError("eqasim_legs or eqasim_pt.csv not found")

        legs_df = _read_eqasim_legs(legs_path)
        pt_df = pd.read_csv(pt_path, sep=";")
        for col in ["person_id", "person_trip_id", "leg_index", "access_area_id", "egress_area_id"]:
            if col in pt_df.columns:
                pt_df[col] = pd.to_numeric(pt_df[col], errors="coerce")

        wait_by_stop = extract_stop_headways(schedule_xml)
        ride_by_area = extract_empirical_ride_times(legs_df, pt_df)
        transfer_by_area = extract_transfer_penalties(legs_df, pt_df)
        stops_df = enrich_stops_with_service(stops_df, wait_by_stop, ride_by_area, transfer_by_area)
        print("SUCCESS v3 Phase 2: stop service metrics computed")
    except Exception as exc:
        print(f"WARNING v3 Phase 2 failed: {exc} — using fallback service values")
        for col, fallback in [("wait_min", 5.0), ("ride_min", 14.0), ("transfer_min", 0.0)]:
            if col not in stops_df.columns:
                stops_df[col] = fallback

    # Phase 3: Load home-origin trips
    print("INFO v3 Phase 3: loading home-origin trips")
    try:
        trips_df = load_home_origin_trips(sim_dir)
        print(f"SUCCESS v3 Phase 3: {len(trips_df)} home-origin trips loaded")
    except Exception as exc:
        print(f"WARNING v3 Phase 3 failed: {exc}")
        return

    # Phase 4: Nearest egress stop and egress times
    print("INFO v3 Phase 4: computing nearest egress stop")
    try:
        nearest_stop_idx, t_egress = compute_nearest_egress_stop(trips_df, stops_df)
        print("SUCCESS v3 Phase 4: egress stops computed")
    except Exception as exc:
        print(f"WARNING v3 Phase 4 failed: {exc}")
        return

    # Phase 5-8: Process each access mode in trip-chunks — no full N_trips×N_stops
    # matrix is ever held in RAM.  Peak per chunk ≈ K×N_stops×4 bytes (~100 MB).
    print("INFO v3 Phase 5-8: chunked cost-matrix, catchments and person scores")

    person_col = "person" if "person" in trips_df.columns else trips_df.columns[0]
    persons_arr = trips_df[person_col].values
    unique_persons, person_inverse = np.unique(persons_arr, return_inverse=True)

    # Employment weights at trip destinations (one per trip)
    emp_weights_dest = None
    try:
        emp_weights_dest = compute_employment_weights_dest(
            trips_df, data_path, sim_dir, method=employment_method, radius_m=emp_radius_m
        )
        print("SUCCESS v3: employment weights computed")
    except Exception as exc:
        print(f"WARNING v3 employment weights failed: {exc}")

    # Stop-level employment weight: mean trip emp-weight for trips whose nearest
    # egress stop is this stop.  Used for weighted_unique_stops.
    stop_emp_weight = None
    if emp_weights_dest is not None:
        n_stops = len(stops_df)
        _stop_w_sum = np.zeros(n_stops, dtype=np.float64)
        _stop_w_cnt = np.zeros(n_stops, dtype=np.float64)
        np.add.at(_stop_w_sum, nearest_stop_idx, emp_weights_dest)
        np.add.at(_stop_w_cnt, nearest_stop_idx, 1.0)
        stop_emp_weight = np.where(_stop_w_cnt > 0, _stop_w_sum / np.maximum(_stop_w_cnt, 1.0), 0.0).astype(np.float32)
        del _stop_w_sum, _stop_w_cnt

    # Per-mode results
    person_score_parts = {}  # col_name -> Series indexed by person
    min_cost_per_mode = {}   # mode -> float32 array of length N_trips (for Phase 11 CSV)

    chunk_size = int(_config(context, "trip_chunk_size", default=_TRIP_CHUNK_SIZE))

    for mode in ACCESS_MODES:
        try:
            print(f"INFO v3 processing mode: {mode} (chunk_size={chunk_size})")
            res = process_mode_chunked(
                trips_df=trips_df,
                stops_df=stops_df,
                t_egress=t_egress,
                mode=mode,
                persons_arr=persons_arr,
                person_inverse=person_inverse,
                unique_persons=unique_persons,
                emp_weights_dest=emp_weights_dest,
                stop_emp_weight=stop_emp_weight,
                chunk_size=chunk_size,
            )

            # Attach catchments to stops_df
            stops_df[f"catchment_{mode}_inclusive_unweighted"] = res["catchment_inclusive"]
            stops_df[f"catchment_{mode}_exclusive_unweighted"] = res["catchment_exclusive"]
            if res["catchment_inclusive_weighted"] is not None:
                stops_df[f"catchment_{mode}_inclusive_weighted"] = res["catchment_inclusive_weighted"]

            for method in ["inclusive", "exclusive"]:
                arr = res[f"catchment_{method}"]
                n_nz = int((arr > 0).sum())
                print(f"INFO catchment ({mode}, {method}): "
                      f"mean={arr.mean():.1f}, max={arr.max():.0f}, {n_nz} stops >0")


            # Collect person scores
            for key in ["score_sum", "score_max", "score_mean", "score_unique_stops",
                        "score_weighted_sum", "score_weighted_max", "score_weighted_mean",
                        "score_weighted_unique_stops"]:
                if key in res:
                    person_score_parts[f"score_{mode}_{key[6:]}"] = res[key]

            min_cost_per_mode[mode] = res["min_cost_trip"]
            gc.collect()
            print(f"SUCCESS v3 mode {mode} processed")

        except Exception as exc:
            print(f"WARNING v3 mode {mode} failed: {exc}")

    # Build person scores DataFrame and merge to persons_df
    try:
        persons_scores_df = pd.DataFrame(person_score_parts)
        persons_scores_df.index.name = person_col
        persons_df = persons_df.merge(
            persons_scores_df.reset_index(),
            left_on="person_id",
            right_on=person_col,
            how="left",
        )
        print(f"SUCCESS v3: person scores merged ({len(persons_scores_df)} persons, "
              f"{len(person_score_parts)} score columns)")
    except Exception as exc:
        print(f"WARNING v3 person scores merge failed: {exc}")

    # Free Phase 5-8 intermediates — large N_trips / N_persons arrays no longer needed.
    try: del t_egress
    except NameError: pass
    try: del persons_arr
    except NameError: pass
    try: del person_inverse
    except NameError: pass
    try: del unique_persons
    except NameError: pass
    try: del emp_weights_dest
    except NameError: pass
    try: del stop_emp_weight
    except NameError: pass
    try: del person_score_parts
    except NameError: pass
    try: del persons_scores_df
    except NameError: pass
    try: del res
    except NameError: pass
    gc.collect()

    # Phase 9: Plots
    print("INFO v3 Phase 9: generating plots")

    # Score comparison (walk vs bike vs difference):
    #   unweighted → unique_stops (# distinct reachable stops across all trips)
    #   weighted   → weighted_unique_stops (sum of stop emp-weights over reachable stops)
    #                falls back to weighted_sum if weighted_unique_stops not computed
    _wus_walk = "score_walk_weighted_unique_stops" if "score_walk_weighted_unique_stops" in persons_df.columns else "score_walk_weighted_sum"
    _wus_bike = "score_bike_weighted_unique_stops" if "score_bike_weighted_unique_stops" in persons_df.columns else "score_bike_weighted_sum"
    for weight_label, walk_col, bike_col in [
        ("unweighted", "score_walk_unique_stops", "score_bike_unique_stops"),
        ("weighted", _wus_walk, _wus_bike),
    ]:
        try:
            _plot_score_comparison(
                persons_df,
                analysis_path,
                walk_col=walk_col,
                bike_col=bike_col,
                output_name=f"score_comparison_{weight_label}",
                title_suffix=f"Accessibility ({weight_label})",
                cbar_label=f"Score (log1p norm., {weight_label})",
                output_prefix=OUTPUT_PREFIX_V3,
            )
        except Exception as exc:
            print(f"WARNING score comparison ({weight_label}): {exc}")

    try:
        plot_person_coverage_v3(persons_df, analysis_path, stops_df=stops_df, nodes_df=nodes_df, pt_links_df=pt_links_df)
    except Exception as exc:
        print(f"WARNING plot_person_coverage_v3: {exc}")

    try:
        plot_proportion_v3(persons_df, analysis_path, nodes_df=nodes_df, pt_links_df=pt_links_df)
    except Exception as exc:
        print(f"WARNING plot_proportion_v3: {exc}")

    # Weighted proportion — share of employment-weighted trips feasible per person
    if "score_walk_weighted_mean" in persons_df.columns and "score_bike_weighted_mean" in persons_df.columns:
        try:
            plot_proportion_v3(
                persons_df, analysis_path,
                col_walk="score_walk_weighted_mean",
                col_bike="score_bike_weighted_mean",
                output_suffix="_weighted",
                nodes_df=nodes_df, pt_links_df=pt_links_df,
            )
        except Exception as exc:
            print(f"WARNING plot_proportion_v3 (weighted): {exc}")

    for catchment_method in ["inclusive", "exclusive"]:
        for weight_label in ["unweighted"]:
            try:
                plot_stop_catchments_v3(
                    stops_df, analysis_path,
                    weight_label=weight_label,
                    catchment_method=catchment_method,
                    nodes_df=nodes_df, pt_links_df=pt_links_df,
                )
            except Exception as exc:
                print(f"WARNING plot_stop_catchments_v3 ({catchment_method}, {weight_label}): {exc}")

            try:
                plot_catchment_by_neighbourhood_v3(
                    stops_df, analysis_path, data_path,
                    weight_label=weight_label,
                    catchment_method=catchment_method,
                    nodes_df=nodes_df, pt_links_df=pt_links_df,
                )
            except Exception as exc:
                print(f"WARNING plot_catchment_by_neighbourhood_v3 ({catchment_method}, {weight_label}): {exc}")

    # Weighted plots (inclusive only)
    for weight_label in ["weighted"]:
        try:
            plot_stop_catchments_v3(
                stops_df, analysis_path,
                weight_label=weight_label,
                catchment_method="inclusive",
                nodes_df=nodes_df, pt_links_df=pt_links_df,
            )
        except Exception as exc:
            print(f"WARNING plot_stop_catchments_v3 (inclusive, {weight_label}): {exc}")

        try:
            plot_catchment_by_neighbourhood_v3(
                stops_df, analysis_path, data_path,
                weight_label=weight_label,
                catchment_method="inclusive",
                nodes_df=nodes_df, pt_links_df=pt_links_df,
            )
        except Exception as exc:
            print(f"WARNING plot_catchment_by_neighbourhood_v3 (inclusive, {weight_label}): {exc}")

    if not persons_df.empty and "score_walk_max" in persons_df.columns:
        for weight_label in ["unweighted", "weighted"]:
            score_col = "max"
            try:
                plot_person_scores_v3(
                    persons_df, analysis_path,
                    weight_label=weight_label, score_agg=score_col,
                    nodes_df=nodes_df, pt_links_df=pt_links_df,
                )
            except Exception as exc:
                print(f"WARNING plot_person_scores_v3 ({weight_label}): {exc}")

            try:
                plot_person_scores_by_neighbourhood_v3(
                    persons_df, analysis_path, data_path,
                    weight_label=weight_label, score_agg=score_col,
                    nodes_df=nodes_df, pt_links_df=pt_links_df,
                )
            except Exception as exc:
                print(f"WARNING plot_person_scores_by_neighbourhood_v3 ({weight_label}): {exc}")

    # Phase 10: Expansion factors (inclusive, unweighted and weighted)
    print("INFO v3 Phase 10: computing and plotting expansion factors")
    for weight_label in ["unweighted", "weighted"]:
        cw_col = f"catchment_walk_inclusive_{weight_label}"
        cb_col = f"catchment_bike_inclusive_{weight_label}"
        if cw_col not in stops_df.columns or cb_col not in stops_df.columns:
            continue
        try:
            ef_df = compute_expansion_factor_v3(
                stops_df[cw_col], stops_df[cb_col]
            )
            stops_df[f"expansion_factor_{weight_label}"] = ef_df["expansion_factor"].values
            stops_df[f"category_{weight_label}"] = ef_df["category"].values

            plot_expansion_factor_v3(
                stops_df, analysis_path,
                weight_label=weight_label,
                nodes_df=nodes_df, pt_links_df=pt_links_df,
            )
        except Exception as exc:
            print(f"WARNING v3 expansion factor ({weight_label}): {exc}")

    # Phase 11: Save outputs
    print("INFO v3 Phase 11: saving outputs")

    # Stops GeoPackage
    try:
        catchment_cols = [
            c for c in stops_df.columns
            if c.startswith("catchment_") or c.startswith("expansion_factor") or c.startswith("category_")
        ]
        base_cols = [c for c in ["stop_id", "x", "y", "name"] if c in stops_df.columns]
        out_stops = stops_df[base_cols + catchment_cols].dropna(subset=["x", "y"])
        gpd.GeoDataFrame(
            out_stops,
            geometry=gpd.points_from_xy(out_stops["x"], out_stops["y"]),
            crs=f"EPSG:{SOURCE_EPSG}",
        ).to_file(
            os.path.join(analysis_path, f"{OUTPUT_PREFIX_V3}_stops.gpkg"),
            driver="GPKG",
        )
        print("SUCCESS saved v3 stops GeoPackage")
    except Exception as exc:
        print(f"WARNING v3 Phase 11 stops save failed: {exc}")

    # Dedicated EF GeoPackages — one per weight variant, matching original pattern
    for weight_label in ["unweighted", "weighted"]:
        try:
            ef_cols = [
                c for c in [
                    "stop_id", "x", "y", "name",
                    f"catchment_walk_inclusive_{weight_label}",
                    f"catchment_bike_inclusive_{weight_label}",
                    f"expansion_factor_{weight_label}",
                    f"category_{weight_label}",
                ]
                if c in stops_df.columns
            ]
            ef_df = stops_df[ef_cols].dropna(subset=["x", "y"])
            gpd.GeoDataFrame(
                ef_df,
                geometry=gpd.points_from_xy(ef_df["x"], ef_df["y"]),
                crs=f"EPSG:{SOURCE_EPSG}",
            ).to_file(
                os.path.join(
                    analysis_path,
                    f"{OUTPUT_PREFIX_V3}_expansion_factor_{weight_label}.gpkg",
                ),
                driver="GPKG",
            )
            print(f"SUCCESS saved v3 EF GeoPackage ({weight_label})")
        except Exception as exc:
            print(f"WARNING v3 EF gpkg save ({weight_label}) failed: {exc}")

    # Persons GeoPackage
    try:
        score_cols = [c for c in persons_df.columns if c.startswith("score_")]
        base_person_cols = [c for c in ["person_id", "x", "y"] if c in persons_df.columns]
        out_persons = persons_df[base_person_cols + score_cols].dropna(subset=["x", "y"])
        gpd.GeoDataFrame(
            out_persons,
            geometry=gpd.points_from_xy(out_persons["x"], out_persons["y"]),
            crs=f"EPSG:{SOURCE_EPSG}",
        ).to_file(
            os.path.join(analysis_path, f"{OUTPUT_PREFIX_V3}_persons.gpkg"),
            driver="GPKG",
        )
        print("SUCCESS saved v3 persons GeoPackage")
    except Exception as exc:
        print(f"WARNING v3 Phase 11 persons save failed: {exc}")

    # Trips CSV with per-trip feasibility — reuses min_cost_per_mode from Phase 5-8
    try:
        feasibility_data = {person_col: trips_df[person_col].values}
        for mode, min_cost in min_cost_per_mode.items():
            feasibility_data[f"feasible_{mode}"] = (min_cost <= TRAVEL_BUDGET_MIN).astype(np.int8)
            feasibility_data[f"min_cost_{mode}_min"] = min_cost
        feasibility_df = pd.DataFrame(feasibility_data)
        feasibility_df.to_csv(
            os.path.join(analysis_path, f"{OUTPUT_PREFIX_V3}_trips_feasibility.csv"),
            index=False,
        )
        print("SUCCESS saved v3 trips feasibility CSV")
    except Exception as exc:
        print(f"WARNING v3 Phase 11 trips save failed: {exc}")

    print("SUCCESS accessibility_v3 execute_v3 complete")
