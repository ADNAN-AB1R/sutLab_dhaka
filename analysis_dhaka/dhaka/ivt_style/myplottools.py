import numpy as np
import matplotlib.pyplot as plt

#plt.rcParams.update({'font.size': 18})

# ---- Centralized color constants (Okabe-Ito colorblind-safe palette, matching paper/scripts/validation.py) ----
COLOR_SYNTHETIC = "#D55E00"  # Vermillion for synthetic population (input)
COLOR_SIMULATION = "#CC79A7"  # Pink for simulation results (output)
COLOR_ACTUAL_HTS = "#0072B2"  # Blue for HTS reference
COLOR_CENSUS = "#009E73"      # Green for census
COLOR_CARLA = "#2E8B57"       # Sea green for CARLA algorithm (unused for Dhaka - no alt. algorithm comparison)
COLOR_HOERL = "#FF6347"       # Tomato red for Hoerl algorithm (unused for Dhaka)

# Convenience mapping used by some plotting helpers
# Note: 'actual' key kept for backward compatibility in user-facing labels
COLORS = {
    'synthetic': COLOR_SYNTHETIC,
    'simulation': COLOR_SIMULATION,
    'hts': COLOR_ACTUAL_HTS,
    'actual': COLOR_ACTUAL_HTS,  # alias for backward compatibility
    'census': COLOR_CENSUS,
    'carla': COLOR_CARLA,
    'hoerl': COLOR_HOERL,
}

# ---- Consistent purpose ordering ----
# This ensures all plots use the same order for purposes. The DTCA HTS only
# distinguishes home/work/education/other (see dhaka/data/hts/entd/cleaned.py
# PURPOSE_TEXT_MAP) - no shop/leisure split, unlike Seville's ENTD.
PURPOSE_ORDER = ['home', 'work', 'education', 'other']

# ---- Consistent mode ordering ----
# Matches dhaka/data/hts/entd/cleaned.py MODES_MAP. Rickshaw and paratransit
# (shared/reserved 3-wheeler CNG/auto) are Dhaka-specific modes with a large,
# structurally distinct trip share - kept separate rather than folded into car/pt.
MODE_ORDER = ['walk', 'bike', 'rickshaw', 'paratransit', 'car', 'pt', 'other']

def autolabel(rects, ax):
    """Attach a text label above each bar in *rects*, displaying its height."""
    for rect in rects:
        height = rect.get_height()
        ax.annotate('{:.2f}'.format(height),
                    xy=(rect.get_x() + rect.get_width() / 2, height),
                    xytext=(0, 3),  # 3 points vertical offset
                    textcoords="offset points",
                    ha='center', va='bottom')



def add_small_hist(axes, r, c, act, x, y, bins, lab = ["Synthetic", "HTS"]):
    # Synthetic histogram (unweighted)
    axes[r,c].hist(x, bins, alpha=0.6, density=True, color=COLOR_SYNTHETIC)
    # HTS histogram (weighted)
    axes[r,c].hist(y["crowfly_distance"], bins, weights=y["weight_person"], alpha=0.6, density=True, color=COLOR_ACTUAL_HTS)
    axes[r,c].set_ylabel("Percentage")
    axes[r,c].set_xlabel("Crowfly Distance [km]")
    axes[r,c].set_title(act.capitalize())
    return axes


def add_small_cdf(axes, r, c, act, x, y, bins=None, lab = ["Synthetic", "HTS"]):
    x_data = np.array(x, dtype=np.float64)
    x_sorted = np.argsort(x_data)
    x_weights = np.array([1.0 for i in range(len(x))], dtype=np.float64)
    x_cdf = np.cumsum(x_weights[x_sorted])
    if len(x_cdf) > 0:
        x_cdf /= x_cdf[-1]

    y_data = np.array(y["crowfly_distance"], dtype=np.float64)
    y_sorted = np.argsort(y_data)
    y_weights = np.array(y["weight_person"], dtype=np.float64)
    y_cdf = np.cumsum(y_weights[y_sorted])

    if len(y_cdf) >0:
        y_cdf /= y_cdf[-1]

    # HTS as blue, Synthetic as gray
    axes[r,c].plot(y_data[y_sorted], y_cdf, color=COLOR_ACTUAL_HTS)
    axes[r,c].plot(x_data[x_sorted], x_cdf, color=COLOR_SYNTHETIC)

    axes[r,c].set_ylabel("Probability")
    axes[r,c].set_xlabel("Crowfly Distance [km]")
    axes[r,c].set_title(act.capitalize())
    # Legend will be added once to the entire figure, not per subplot
    return axes


def plot_comparison_bar(context, imtitle, plottitle, ylabel, xlabel, lab, hts=None, synthetic=None, simulation=None, census=None, actual=None, lablist=['HTS', 'Synthetic', 'Simulation', 'Census'], t=15, figsize=[12,7], dpi=300, w=0.25, xticksrot=False):
    """
    Plot comparison bar chart with up to 4 datasets.
    
    Parameters:
        hts: HTS reference data (primary parameter)
        actual: Alias for hts (deprecated, kept for backward compatibility)
        synthetic: Synthetic population data
        simulation: Simulation results data
        census: Census reference data
    """
    import matplotlib.pyplot as plt
    import numpy as np
    
    # Handle backward compatibility: if actual is provided but not hts, use actual as hts
    if hts is None and actual is not None:
        hts = actual

    plt.rcParams['axes.facecolor'] = "#ffffff"
    plt.rcParams['figure.figsize'] = figsize
    plt.rcParams['figure.dpi'] = dpi

    top = t
    if top is not None:
        labels = lab[:top]
    else:
        labels = lab

    datasets = {
        'hts': hts[:top] if hts is not None and top is not None else hts,
        'synthetic': synthetic[:top] if synthetic is not None and top is not None else synthetic,
        'simulation': simulation[:top] if simulation is not None and top is not None else simulation,
        'census': census[:top] if census is not None and top is not None else census
    }
    
    colors = {'hts': COLOR_ACTUAL_HTS, 'synthetic': COLOR_SYNTHETIC, 'simulation': COLOR_SIMULATION, 'census': COLOR_CENSUS}
    
    # Build legend_labels safely, with defaults for missing entries
    default_labels = ['HTS', 'Synthetic', 'Simulation', 'Census']
    legend_labels = {
        'hts': lablist[0] if len(lablist) > 0 else default_labels[0],
        'synthetic': lablist[1] if len(lablist) > 1 else default_labels[1],
        'simulation': lablist[2] if len(lablist) > 2 else default_labels[2],
        'census': lablist[3] if len(lablist) > 3 else default_labels[3]
    }

    # Filter out None datasets
    active_datasets = {k: v for k, v in datasets.items() if v is not None}
    num_bars = len(active_datasets)
    
    x = np.arange(len(labels))  # the label locations
    
    if num_bars > 1:
        width = w / num_bars * 2.5
    else:
        width = 0.5

    fig, ax = plt.subplots()
    fig.set_facecolor("#ffffff")

    # Plot bars
    for i, (key, data) in enumerate(active_datasets.items()):
        offset = width * (i - (num_bars - 1) / 2)
        ax.bar(x + offset, data, width, label=legend_labels[key], color=colors[key], align="center")

    # Add labels and title
    ax.set_ylabel(ylabel)
    ax.set_title(plottitle)
    ax.set_xticks(x)
    ax.set_xlabel(xlabel)

    if xticksrot:
        ax.set_xticklabels(labels, rotation=45, ha="right", rotation_mode='anchor')
    else:
        ax.set_xticklabels(labels)

    ax.legend(loc='upper right')
    fig.tight_layout()
    plt.savefig("%s/" % context.config("analysis_path") + imtitle)
    plt.close()


# ---------- New utilities to standardize analysis pipelines ----------

def align_series(actual, other):
    """
    Align 'other' Series to have the same index and order as 'actual'.
    Any missing categories are filled with 0. Useful to avoid swapped bars when
    plotting multiple series side-by-side.

    Parameters
    - actual: pd.Series with desired index and order
    - other: pd.Series to be reindexed

    Returns
    - pd.Series aligned to actual.index, with NaN replaced by 0
    """
    import pandas as pd  # local import to keep module lightweight
    if actual is None or other is None:
        return other
    # Ensure Series
    if not hasattr(other, "reindex"):
        other = pd.Series(other)
    return other.reindex(actual.index).fillna(0)


def compute_counts(series, weights=None, categories=None, normalize=True, to_percent=True):
    """
    Compute counts for a categorical Series, optionally using weights and a
    fixed category order. Returns a pd.Series with percentages by default.

    Parameters
    - series: pd.Series of labels
    - weights: optional pd.Series of weights (same length)
    - categories: optional list specifying desired category order
    - normalize: whether to normalize to sum=1 (before percentage scaling)
    - to_percent: if True, multiply normalized values by 100
    """
    import pandas as pd
    # Always return a Series; if input is None, return zeros for categories or empty series
    if series is None:
        if categories is not None:
            return pd.Series(0.0, index=pd.Index(categories, name=None))
        return pd.Series(dtype=float)
    if categories is not None:
        cat = pd.Categorical(series, categories=categories, ordered=True)
    else:
        cat = series
    if weights is None:
        counts = pd.Series(cat).value_counts(sort=False, dropna=False)
    else:
        # weighted counts per category
        df = pd.DataFrame({"cat": cat, "w": weights})
        counts = df.groupby("cat")["w"].sum()
    if normalize:
        total = counts.sum()
        counts = (counts / total) if total != 0 else counts * 0.0
    if to_percent:
        counts = counts * 100.0
    return counts


def map_bool_to_labels(series, yes_label="Yes", no_label="No"):
    """Map a boolean Series to labeled strings (No/Yes)."""
    if series is None:
        return None
    return series.replace({False: no_label, True: yes_label})


def plot_distribution_differences(context, imtitle, plottitle, ylabel, xlabel, lab, diff_hts=None, diff_census=None, diff_actual=None, lablist=['Synthetic vs HTS', 'Synthetic vs Census'], t=15, figsize=[12,7], dpi=300, w=0.35, xticksrot=False):
    """
    Plots the differences between distributions as a bar chart.
    
    Parameters:
        diff_hts: HTS difference data (primary parameter)
        diff_actual: Alias for diff_hts (deprecated, kept for backward compatibility)
        diff_census: Census difference data
    """
    import matplotlib.pyplot as plt
    import numpy as np
    
    # Handle backward compatibility: if diff_actual is provided but not diff_hts, use diff_actual as diff_hts
    if diff_hts is None and diff_actual is not None:
        diff_hts = diff_actual

    plt.rcParams['axes.facecolor'] = "#ffffff"
    plt.rcParams['figure.figsize'] = figsize
    plt.rcParams['figure.dpi'] = dpi

    top = t
    if top is not None:
        labels = lab[:top]
        diff_hts_means = diff_hts[:top] if diff_hts is not None else None
        diff_census_means = diff_census[:top] if diff_census is not None else None
    else:
        labels = lab
        diff_hts_means = diff_hts
        diff_census_means = diff_census

    x = np.arange(len(labels))  # the label locations
    width = w

    fig, ax = plt.subplots()
    fig.set_facecolor("#ffffff")

    # Plot bars (HTS difference in blue, Census difference in gold)
    ax.bar(x - width/2, diff_hts_means, width, label=lablist[0], color=COLOR_ACTUAL_HTS, align="center")
    ax.bar(x + width/2, diff_census_means, width, label=lablist[1], color=COLOR_CENSUS, align="center")

    # Add a horizontal line at y=0 to emphasize the difference
    ax.axhline(0, color='grey', linewidth=0.8)

    # Add labels and title
    ax.set_ylabel(ylabel)
    ax.set_title(plottitle)
    ax.set_xticks(x)
    ax.set_xlabel(xlabel)

    if xticksrot:
        ax.set_xticklabels(labels, rotation=45, ha="right", rotation_mode='anchor')
    else:
        ax.set_xticklabels(labels)

    ax.legend(loc='upper right')
    fig.tight_layout()
    plt.savefig("%s/" % context.config("analysis_path") + imtitle)
    plt.close()


def plot_horizontal_comparison(context, imtitle, plottitle, xlabel, labels, synthetic=None, hts=None, census=None, actual=None, lablist=['Synthetic', 'HTS', 'Census'], figsize=[10, 10], dpi=300, bar_height=0.7):
    """
    Grouped horizontal bar chart for side-by-side comparison of up to three series
    (Synthetic, HTS, Census). Missing series values can be NaN and will be skipped
    per-bar gracefully. Bars are positioned dynamically without gaps for missing data.

    Parameters
    - labels: list of category strings (y-axis)
    - synthetic/hts/census: sequences of values (percentages), same length as labels
    - actual: Alias for hts (deprecated, kept for backward compatibility)
    - lablist: legend labels for [Synthetic, HTS, Census]
    - bar_height: total height allocated to one category; split among active datasets
    """
    import numpy as np
    import matplotlib.pyplot as plt
    
    # Handle backward compatibility: if actual is provided but not hts, use actual as hts
    if hts is None and actual is not None:
        hts = actual

    plt.rcParams['figure.figsize'] = figsize
    plt.rcParams['figure.dpi'] = dpi

    series = []
    legend_labels = []
    colors = []
    if synthetic is not None:
        series.append(np.array(synthetic, dtype=float))
        legend_labels.append(lablist[0])
        colors.append(COLOR_SYNTHETIC)
    if hts is not None:
        series.append(np.array(hts, dtype=float))
        legend_labels.append(lablist[1])
        colors.append(COLOR_ACTUAL_HTS)
    if census is not None:
        series.append(np.array(census, dtype=float))
        legend_labels.append(lablist[2])
        colors.append(COLOR_CENSUS)

    n = len(labels)
    y = np.arange(n)
    k = len(series)
    if k == 0:
        return

    fig, ax = plt.subplots()
    fig.set_facecolor("#ffffff")

    # Track which legend labels have been used to ensure each appears only once
    legend_labels_used = set()
    
    # For each label, determine which series have valid data and position bars dynamically
    for j in range(n):
        valid_indices = []
        valid_values = []
        valid_colors = []
        valid_labels = []
        
        # Check which series have valid (non-NaN) data for this label
        for i in range(k):
            if not np.isnan(series[i][j]):
                valid_indices.append(i)
                valid_values.append(series[i][j])
                valid_colors.append(colors[i])
                valid_labels.append(legend_labels[i])
        
        # If no valid data for this label, skip
        if len(valid_values) == 0:
            continue
            
        # Position bars for valid series only (no gaps)
        k_valid = len(valid_values)
        sub_h = bar_height / k_valid
        offsets = np.linspace(-bar_height/2 + sub_h/2, bar_height/2 - sub_h/2, k_valid)
        
        # Draw bars for this label
        for idx, (val, color, label) in enumerate(zip(valid_values, valid_colors, valid_labels)):
            # Only add to legend if this label hasn't been used yet
            label_for_legend = label if label not in legend_labels_used else None
            if label_for_legend is not None:
                legend_labels_used.add(label)
            ax.barh(y[j] + offsets[idx], val, height=sub_h, color=color, label=label_for_legend)

    ax.set_xlabel(xlabel)
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.set_title(plottitle)
    ax.invert_yaxis()  # top item first
    
    # Get legend handles and labels, maintain original order
    handles, labels_legend = ax.get_legend_handles_labels()
    
    # Ensure legend appears in the original order (Synthetic, HTS, Census)
    ordered_handles = []
    ordered_labels = []
    for original_label in legend_labels:  # This preserves the original order
        for handle, label in zip(handles, labels_legend):
            if label == original_label and label not in ordered_labels:
                ordered_handles.append(handle)
                ordered_labels.append(label)
                break
    
    ax.legend(ordered_handles, ordered_labels, loc='upper right')
    fig.tight_layout()
    plt.savefig("%s/" % context.config("analysis_path") + imtitle)
    plt.close()


def plot_comparison_hist_purpose(context, title, actual_df, synthetic_df, bins = np.linspace(0,25,120), dpi = 300, cols = 3, rows = 2):
    # Use consistent purpose ordering across all plots
    available_purposes = set(synthetic_df["following_purpose"].unique())
    if actual_df is not None:
        available_purposes |= set(actual_df["purpose"].unique())
    
    # Filter PURPOSE_ORDER to only include purposes that exist in the data
    modelist = [p for p in PURPOSE_ORDER if p in available_purposes]
    
    # Calculate actual rows needed
    actual_rows = (len(modelist) // cols) + (len(modelist) % cols != 0)
    
    plt.rcParams['figure.dpi'] = dpi
    fig, axes = plt.subplots(nrows=actual_rows, ncols=cols, figsize = (5*cols, 3*actual_rows))
    
    # Ensure axes is 2D array even for single row
    if actual_rows == 1:
        axes = axes.reshape(1, -1)
    
    idx=0
    for r in range(actual_rows):
        for c in range(cols):
            if idx < len(modelist):
                purpose = modelist[idx]
                x = synthetic_df[synthetic_df["following_purpose"]==purpose]["crowfly_distance"]
                y = actual_df[actual_df["purpose"]==purpose][["crowfly_distance", "weight_person"]]
                axes = add_small_hist(axes, r, c, purpose, x, y, bins)
                idx = idx + 1   
            else:
                # Hide unused subplots
                axes[r, c].set_visible(False)
    
    # Add a single legend for the entire figure positioned on the right
    # Create dummy plots for legend
    import matplotlib.lines as mlines
    hts_line = mlines.Line2D([], [], color=COLOR_ACTUAL_HTS, label='HTS')
    synthetic_line = mlines.Line2D([], [], color=COLOR_SYNTHETIC, label='Synthetic')
    fig.legend(handles=[hts_line, synthetic_line], loc='center right', bbox_to_anchor=(0.98, 0.5))
    
    fig.suptitle("Distribution of Distances by Activity", fontsize=14)
    fig.tight_layout()
    fig.subplots_adjust(right=0.85)  # Make room for legend on the right
    plt.savefig("%s/" % context.config("analysis_path") + title, bbox_inches='tight', dpi=dpi)
    plt.close()



def plot_comparison_hist_mode(context, title, actual_df, synthetic_df, bins = np.linspace(0,25,120), dpi = 300, cols = 3, rows = 2):
    # Use consistent mode ordering across all plots
    available_modes = set(synthetic_df["mode"].unique())
    if actual_df is not None:
        available_modes |= set(actual_df["mode"].unique())
    
    # Filter MODE_ORDER to only include modes that exist in the data
    modelist = [m for m in MODE_ORDER if m in available_modes]
    
    # Calculate actual rows needed based on number of modes
    actual_rows = (len(modelist) // cols) + (len(modelist) % cols != 0)
    
    plt.rcParams['figure.dpi'] = dpi
    fig, axes = plt.subplots(nrows=actual_rows, ncols=cols, figsize=(5*cols, 3*actual_rows))
    
    # Ensure axes is 2D array even for single row
    if actual_rows == 1:
        axes = axes.reshape(1, -1)
    
    idx=0
    for r in range(actual_rows):
        for c in range(cols):
            if idx < len(modelist):
                x = synthetic_df[synthetic_df["mode"]==modelist[idx]]["crowfly_distance"]
                y = actual_df[actual_df["mode"]==modelist[idx]][["crowfly_distance", "weight_person"]]        
                axes = add_small_hist(axes, r, c, modelist[idx], x, y, bins)
                idx=idx+1
            else:
                axes[r, c].set_visible(False)

    # Add a single legend for the entire figure positioned on the right
    # Create dummy plots for legend  
    from matplotlib.patches import Rectangle
    hts_patch = Rectangle((0,0),1,1, fc=COLOR_ACTUAL_HTS, alpha=0.5, label='HTS')
    synthetic_patch = Rectangle((0,0),1,1, fc=COLOR_SYNTHETIC, alpha=0.5, label='Synthetic')
    fig.legend(handles=[hts_patch, synthetic_patch], loc='center right', bbox_to_anchor=(0.98, 0.5))
    
    fig.suptitle("Distribution of Distances by Mode", fontsize=14)
    fig.tight_layout()
    fig.subplots_adjust(right=0.85)  # Make room for legend on the right
    plt.savefig("%s/" % context.config("analysis_path") + title, bbox_inches='tight', dpi=dpi)
    plt.close()



def plot_comparison_cdf_purpose(context, title, actual_df, synthetic_df, dpi = 300, cols = 3, rows = 2):
    import pandas as pd
    
    # Use consistent purpose ordering across all plots
    available_purposes = set(synthetic_df["following_purpose"].unique())
    if actual_df is not None:
        purpose_col = 'purpose' if 'purpose' in actual_df.columns else 'following_purpose'
        if purpose_col in actual_df.columns:
            available_purposes |= set(actual_df[purpose_col].unique())
    
    # Filter PURPOSE_ORDER to only include purposes that exist in the data
    modelist = [p for p in PURPOSE_ORDER if p in available_purposes]
    
    # Calculate actual rows needed
    actual_rows = (len(modelist) // cols) + (len(modelist) % cols != 0)
    
    plt.rcParams['figure.dpi'] = dpi
    fig, axes = plt.subplots(nrows=actual_rows, ncols=cols, figsize = (5*cols, 3*actual_rows))
    
    # Ensure axes is 2D array even for single row
    if actual_rows == 1:
        axes = axes.reshape(1, -1)
    
    idx=0
    for r in range(actual_rows):
        for c in range(cols):
            if idx < len(modelist):
                purpose = modelist[idx]
                x = synthetic_df[synthetic_df["following_purpose"]==purpose]["crowfly_distance"]
                
                # Check if actual_df has data for this purpose and required columns
                if actual_df is not None and len(actual_df) > 0:
                    # Try 'purpose' column first, then 'following_purpose' as fallback
                    purpose_col = 'purpose' if 'purpose' in actual_df.columns else 'following_purpose'
                    if purpose_col in actual_df.columns:
                        y = actual_df[actual_df[purpose_col]==purpose][["crowfly_distance", "weight_person"]]
                        if len(y) == 0:  # No HTS data for this purpose
                            y = pd.DataFrame(columns=["crowfly_distance", "weight_person"])
                    else:
                        y = pd.DataFrame(columns=["crowfly_distance", "weight_person"])
                else:
                    y = pd.DataFrame(columns=["crowfly_distance", "weight_person"])
                
                axes = add_small_cdf(axes, r, c, purpose, x, y)
                idx = idx + 1
            else:
                # Hide unused subplots
                axes[r, c].set_visible(False)
    
    # Add a single legend for the entire figure positioned on the right
    # Create dummy plots for legend
    import matplotlib.lines as mlines
    hts_line = mlines.Line2D([], [], color=COLOR_ACTUAL_HTS, label='HTS')
    synthetic_line = mlines.Line2D([], [], color=COLOR_SYNTHETIC, label='Synthetic')
    fig.legend(handles=[hts_line, synthetic_line], loc='center right', bbox_to_anchor=(0.98, 0.5))
    
    fig.suptitle("Distribution of Distances by Activity", fontsize=14)
    fig.tight_layout()
    fig.subplots_adjust(right=0.85)  # Make room for legend on the right
    plt.savefig("%s/" % context.config("analysis_path") + title, bbox_inches='tight', dpi=dpi)
    plt.close()


def plot_comparison_cdf_mode(context, title, actual_df, synthetic_df, bins = np.linspace(0,25,120), dpi = 300, cols = 3, rows = 2):
    # Use consistent mode ordering across all plots
    available_modes = set(synthetic_df["mode"].unique())
    if actual_df is not None:
        available_modes |= set(actual_df["mode"].unique())
    
    # Filter MODE_ORDER to only include modes that exist in the data
    modelist = [m for m in MODE_ORDER if m in available_modes]
    
    # Calculate actual rows needed based on number of modes
    actual_rows = (len(modelist) // cols) + (len(modelist) % cols != 0)
    
    plt.rcParams['figure.dpi'] = dpi
    fig, axes = plt.subplots(nrows=actual_rows, ncols=cols, figsize=(5*cols, 3*actual_rows))
    
    # Ensure axes is 2D array even for single row
    if actual_rows == 1:
        axes = axes.reshape(1, -1)
    
    idx=0
    for r in range(actual_rows):
        for c in range(cols):
            if idx < len(modelist):
                x = synthetic_df[synthetic_df["mode"]==modelist[idx]]["crowfly_distance"]
                y = actual_df[actual_df["mode"]==modelist[idx]][["crowfly_distance", "weight_person"]]        
                axes = add_small_cdf(axes, r, c, modelist[idx], x, y, bins)
                idx=idx+1
            else:
                axes[r, c].set_visible(False)

    # Add a single legend for the entire figure positioned on the right
    # Create dummy plots for legend
    import matplotlib.lines as mlines
    hts_line = mlines.Line2D([], [], color=COLOR_ACTUAL_HTS, label='HTS')
    synthetic_line = mlines.Line2D([], [], color=COLOR_SYNTHETIC, label='Synthetic')
    fig.legend(handles=[hts_line, synthetic_line], loc='center right', bbox_to_anchor=(0.98, 0.5))
    
    fig.suptitle("Distribution of Distances by Mode", fontsize=14)
    fig.tight_layout()
    fig.subplots_adjust(right=0.85)  # Make room for legend on the right
    plt.savefig("%s/" % context.config("analysis_path") + title, bbox_inches='tight', dpi=dpi)
    plt.close()




def plot_mode_share(context, title, df_syn, df2, amdf2, dpi = 300):
    modelist=list(zip(np.sort(df_syn["mode"].unique()),["car","car_passenger","pt", "taxi", "walk"]))
    plt.rcParams['figure.dpi'] = dpi
    y1 = []
    y2 = []
    for mode,mode_cat in modelist:
        y1.append(df2[df2["mode"]==mode]["crowfly_distance"].count() / len(df2))
        # use person weight for weigthing instead
        y2.append(amdf2[amdf2["mode"]==mode_cat]["weight_person"].sum() / amdf2["weight_person"].sum())
    
    labels = [i[0] for i in modelist]

    x = np.arange(len(labels))  # the label locations
    width = 0.35  # the width of the bars

    fig, ax = plt.subplots()
    rects1 = ax.bar(x - width/2, y2, width, label='HTS', color=COLOR_ACTUAL_HTS)
    rects2 = ax.bar(x + width/2, y1, width, label='Synthetic', color=COLOR_SYNTHETIC)

    # Add some text for labels, title and custom x-axis tick labels, etc.
    ax.set_ylabel('Percentage')
    ax.set_title('Mode-share')
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.legend()

    autolabel(rects1, ax)
    autolabel(rects2, ax)

    fig.tight_layout()
    plt.savefig("%s/" % context.config("analysis_path") + title)
    plt.close()


def add_synthetic_cdf(axes, r, c, act, x, label="Synthetic"):
    """Add synthetic-only CDF plot to subplot"""
    x_data = np.array(x, dtype=np.float64)
    x_sorted = np.argsort(x_data)
    x_weights = np.array([1.0 for i in range(len(x))], dtype=np.float64)
    x_cdf = np.cumsum(x_weights[x_sorted])
    if len(x_cdf) > 0:
        x_cdf /= x_cdf[-1]

    # Plot synthetic as dark blue line
    axes[r,c].plot(x_data[x_sorted], x_cdf, label=label, color=COLOR_ACTUAL_HTS, linewidth=2)

    axes[r,c].set_ylabel("Probability")
    axes[r,c].set_xlabel("Crowfly Distance [km]")
    axes[r,c].set_title(act.capitalize())
    axes[r,c].legend(loc="lower right")
    axes[r,c].grid(True, alpha=0.3)
    axes[r,c].set_xlim(0, 25)  # Match your reference image range
    axes[r,c].set_ylim(0, 1)
    return axes


def plot_synthetic_cdf_purpose(context, title, synthetic_df, dpi=300, cols=3, rows=2):
    """Plot synthetic-only CDF by purpose in 2x3 grid layout"""
    import matplotlib.pyplot as plt
    
    purposes = synthetic_df["following_purpose"].unique()
    # Sort purposes for consistent ordering
    purposes = sorted(purposes)
    
    plt.rcParams['figure.dpi'] = dpi
    fig, axes = plt.subplots(nrows=rows, ncols=cols, figsize=(5*cols, 3*rows))
    
    # Ensure axes is 2D array even for single row
    if rows == 1:
        axes = axes.reshape(1, -1)
    
    idx = 0
    for r in range(rows):
        for c in range(cols):
            if idx < len(purposes):
                purpose = purposes[idx]
                x = synthetic_df[synthetic_df["following_purpose"] == purpose]["crowfly_distance"]
                if len(x) > 0:  # Only plot if we have data
                    axes = add_synthetic_cdf(axes, r, c, purpose, x)
                else:
                    # Hide empty subplots
                    axes[r, c].set_visible(False)
                idx += 1
            else:
                # Hide unused subplots
                axes[r, c].set_visible(False)
    
    fig.suptitle("Distribution of Distances by Activity", fontsize=14, y=0.98)
    fig.tight_layout()
    plt.savefig(f"{context.config('analysis_path')}/{title}", bbox_inches='tight', dpi=dpi)
    plt.close()


# ============================================================================
# THREE-WAY COMPARISON FUNCTIONS (HTS vs CARLA vs Hoerl)
# ============================================================================

def add_threeway_hist(axes, r, c, act, x_hts, x_syn1, x_syn2, bins, label_syn1="CARLA", label_syn2="Hoerl"):
    """Add a three-way histogram comparison to a subplot."""
    # HTS histogram (weighted)
    if x_hts is not None and len(x_hts) > 0:
        axes[r,c].hist(x_hts["crowfly_distance"], bins, weights=x_hts["weight_person"], 
                      alpha=0.4, density=True, color=COLOR_ACTUAL_HTS)
    # CARLA histogram (unweighted)
    if len(x_syn1) > 0:
        axes[r,c].hist(x_syn1, bins, alpha=0.4, density=True, color=COLOR_CARLA)
    # Hoerl histogram (unweighted)
    if len(x_syn2) > 0:
        axes[r,c].hist(x_syn2, bins, alpha=0.4, density=True, color=COLOR_HOERL)
    
    axes[r,c].set_ylabel("Percentage")
    axes[r,c].set_xlabel("Crowfly Distance [km]")
    axes[r,c].set_title(act.capitalize())
    return axes


def add_threeway_cdf(axes, r, c, act, x_hts, x_syn1, x_syn2, label_syn1="CARLA", label_syn2="Hoerl"):
    """Add a three-way CDF comparison to a subplot."""
    # HTS CDF (weighted)
    if x_hts is not None and len(x_hts) > 0:
        y_data = np.array(x_hts["crowfly_distance"], dtype=np.float64)
        y_sorted = np.argsort(y_data)
        y_weights = np.array(x_hts["weight_person"], dtype=np.float64)
        y_cdf = np.cumsum(y_weights[y_sorted])
        y_cdf = y_cdf / y_cdf[-1]
        axes[r,c].plot(y_data[y_sorted], y_cdf, color=COLOR_ACTUAL_HTS, linewidth=2)
    
    # CARLA CDF (unweighted)
    if len(x_syn1) > 0:
        x1_data = np.array(x_syn1, dtype=np.float64)
        x1_sorted = np.argsort(x1_data)
        x1_cdf = np.cumsum([1.0] * len(x1_data))
        x1_cdf = x1_cdf / x1_cdf[-1]
        axes[r,c].plot(x1_data[x1_sorted], x1_cdf, color=COLOR_CARLA, linewidth=2)
    
    # Hoerl CDF (unweighted)
    if len(x_syn2) > 0:
        x2_data = np.array(x_syn2, dtype=np.float64)
        x2_sorted = np.argsort(x2_data)
        x2_cdf = np.cumsum([1.0] * len(x2_data))
        x2_cdf = x2_cdf / x2_cdf[-1]
        axes[r,c].plot(x2_data[x2_sorted], x2_cdf, color=COLOR_HOERL, linewidth=2)

    axes[r,c].set_ylabel("Probability")
    axes[r,c].set_xlabel("Crowfly Distance (km)")
    axes[r,c].set_title(act.capitalize())
    axes[r,c].grid(True, alpha=0.3)
    return axes


def plot_threeway_hist_purpose(context, title, actual_df, synthetic_df1, synthetic_df2, 
                               bins=np.linspace(0,25,120), dpi=300, cols=3, rows=2,
                               label_syn1="CARLA", label_syn2="Hoerl"):
    """
    Plot three-way histogram comparison by purpose: HTS vs Algorithm1 vs Algorithm2
    """
    # Use consistent purpose ordering
    available_purposes = set(synthetic_df1["following_purpose"].unique())
    available_purposes |= set(synthetic_df2["following_purpose"].unique())
    if actual_df is not None:
        available_purposes |= set(actual_df["purpose"].unique())
    
    # Filter PURPOSE_ORDER to only include purposes that exist in the data
    modelist = [p for p in PURPOSE_ORDER if p in available_purposes]
    
    # Calculate actual rows needed
    actual_rows = (len(modelist) // cols) + (len(modelist) % cols != 0)
    
    plt.rcParams['figure.dpi'] = dpi
    fig, axes = plt.subplots(nrows=actual_rows, ncols=cols, figsize=(5*cols, 3*actual_rows))
    
    # Ensure axes is 2D array even for single row
    if actual_rows == 1:
        axes = axes.reshape(1, -1)
    
    idx = 0
    for r in range(actual_rows):
        for c in range(cols):
            if idx < len(modelist):
                purpose = modelist[idx]
                x_syn1 = synthetic_df1[synthetic_df1["following_purpose"]==purpose]["crowfly_distance"]
                x_syn2 = synthetic_df2[synthetic_df2["following_purpose"]==purpose]["crowfly_distance"]
                y_hts = None
                if actual_df is not None:
                    y_hts = actual_df[actual_df["purpose"]==purpose][["crowfly_distance", "weight_person"]]
                axes = add_threeway_hist(axes, r, c, purpose, y_hts, x_syn1, x_syn2, bins, 
                                       label_syn1=label_syn1, label_syn2=label_syn2)
                idx += 1
            else:
                axes[r, c].set_visible(False)
    
    # Add a single legend for the entire figure positioned on the right
    import matplotlib.lines as mlines
    hts_line = mlines.Line2D([], [], color=COLOR_ACTUAL_HTS, label='HTS')
    syn1_line = mlines.Line2D([], [], color=COLOR_CARLA, label=label_syn1)
    syn2_line = mlines.Line2D([], [], color=COLOR_HOERL, label=label_syn2)
    fig.legend(handles=[hts_line, syn1_line, syn2_line], loc='center right', bbox_to_anchor=(0.98, 0.5))
    
    fig.suptitle("Distribution of Distances by Activity", fontsize=14)
    fig.tight_layout()
    fig.subplots_adjust(right=0.85)  # Make room for legend on the right
    plt.savefig("%s/%s" % (context.config("analysis_path"), title), bbox_inches='tight', dpi=dpi)
    plt.close()


def plot_threeway_cdf_purpose(context, title, actual_df, synthetic_df1, synthetic_df2,
                              dpi=300, cols=3, rows=2, label_syn1="CARLA", label_syn2="Hoerl"):
    """
    Plot three-way CDF comparison by purpose: HTS vs Algorithm1 vs Algorithm2
    """
    # Use consistent purpose ordering
    available_purposes = set(synthetic_df1["following_purpose"].unique())
    available_purposes |= set(synthetic_df2["following_purpose"].unique())
    if actual_df is not None:
        available_purposes |= set(actual_df["purpose"].unique())
    
    # Filter PURPOSE_ORDER to only include purposes that exist in the data
    modelist = [p for p in PURPOSE_ORDER if p in available_purposes]
    
    # Calculate actual rows needed
    actual_rows = (len(modelist) // cols) + (len(modelist) % cols != 0)
    
    plt.rcParams['figure.dpi'] = dpi
    fig, axes = plt.subplots(nrows=actual_rows, ncols=cols, figsize=(5*cols, 3*actual_rows))
    
    # Ensure axes is 2D array even for single row
    if actual_rows == 1:
        axes = axes.reshape(1, -1)
    
    idx = 0
    for r in range(actual_rows):
        for c in range(cols):
            if idx < len(modelist):
                purpose = modelist[idx]
                x_syn1 = synthetic_df1[synthetic_df1["following_purpose"]==purpose]["crowfly_distance"]
                x_syn2 = synthetic_df2[synthetic_df2["following_purpose"]==purpose]["crowfly_distance"]
                y_hts = None
                if actual_df is not None:
                    y_hts = actual_df[actual_df["purpose"]==purpose][["crowfly_distance", "weight_person"]]
                axes = add_threeway_cdf(axes, r, c, purpose, y_hts, x_syn1, x_syn2,
                                      label_syn1=label_syn1, label_syn2=label_syn2)
                idx += 1
            else:
                axes[r, c].set_visible(False)
    
    # Add a single legend for the entire figure positioned on the right
    import matplotlib.lines as mlines
    hts_line = mlines.Line2D([], [], color=COLOR_ACTUAL_HTS, linewidth=2, label='HTS')
    syn1_line = mlines.Line2D([], [], color=COLOR_CARLA, linewidth=2, label=label_syn1)
    syn2_line = mlines.Line2D([], [], color=COLOR_HOERL, linewidth=2, label=label_syn2)
    fig.legend(handles=[hts_line, syn1_line, syn2_line], loc='center right', bbox_to_anchor=(0.98, 0.5))
    
    fig.suptitle("Distribution of Distances by Activity", fontsize=14)
    fig.tight_layout()
    fig.subplots_adjust(right=0.85)  # Make room for legend on the right
    plt.savefig("%s/%s" % (context.config("analysis_path"), title), bbox_inches='tight', dpi=dpi)
    plt.close()


def plot_threeway_hist_mode(context, title, actual_df, synthetic_df1, synthetic_df2,
                            bins=np.linspace(0,25,120), dpi=300, cols=3, rows=2,
                            label_syn1="CARLA", label_syn2="Hoerl"):
    """
    Plot three-way histogram comparison by mode: HTS vs Algorithm1 vs Algorithm2
    """
    # Use consistent mode ordering
    available_modes = set(synthetic_df1["mode"].unique())
    available_modes |= set(synthetic_df2["mode"].unique())
    if actual_df is not None:
        available_modes |= set(actual_df["mode"].unique())
    
    # Filter MODE_ORDER to only include modes that exist in the data
    modelist = [m for m in MODE_ORDER if m in available_modes]
    
    # Calculate actual rows needed
    actual_rows = (len(modelist) // cols) + (len(modelist) % cols != 0)
    
    plt.rcParams['figure.dpi'] = dpi
    fig, axes = plt.subplots(nrows=actual_rows, ncols=cols, figsize=(5*cols, 3*actual_rows))
    
    # Ensure axes is 2D array even for single row
    if actual_rows == 1:
        axes = axes.reshape(1, -1)
    
    idx = 0
    for r in range(actual_rows):
        for c in range(cols):
            if idx < len(modelist):
                mode = modelist[idx]
                x_syn1 = synthetic_df1[synthetic_df1["mode"]==mode]["crowfly_distance"]
                x_syn2 = synthetic_df2[synthetic_df2["mode"]==mode]["crowfly_distance"]
                y_hts = None
                if actual_df is not None:
                    y_hts = actual_df[actual_df["mode"]==mode][["crowfly_distance", "weight_person"]]
                axes = add_threeway_hist(axes, r, c, mode, y_hts, x_syn1, x_syn2, bins,
                                       label_syn1=label_syn1, label_syn2=label_syn2)
                idx += 1
            else:
                axes[r, c].set_visible(False)
    
    # Add a single legend for the entire figure positioned on the right
    import matplotlib.lines as mlines
    hts_line = mlines.Line2D([], [], color=COLOR_ACTUAL_HTS, label='HTS')
    syn1_line = mlines.Line2D([], [], color=COLOR_CARLA, label=label_syn1)
    syn2_line = mlines.Line2D([], [], color=COLOR_HOERL, label=label_syn2)
    fig.legend(handles=[hts_line, syn1_line, syn2_line], loc='center right', bbox_to_anchor=(0.98, 0.5))
    
    fig.suptitle("Distribution of Distances by Mode", fontsize=14)
    fig.tight_layout()
    fig.subplots_adjust(right=0.85)  # Make room for legend on the right
    plt.savefig("%s/%s" % (context.config("analysis_path"), title), bbox_inches='tight', dpi=dpi)
    plt.close()


def plot_threeway_cdf_mode(context, title, actual_df, synthetic_df1, synthetic_df2,
                           dpi=300, cols=3, rows=2, label_syn1="CARLA", label_syn2="Hoerl"):
    """
    Plot three-way CDF comparison by mode: HTS vs Algorithm1 vs Algorithm2
    """
    # Use consistent mode ordering
    available_modes = set(synthetic_df1["mode"].unique())
    available_modes |= set(synthetic_df2["mode"].unique())
    if actual_df is not None:
        available_modes |= set(actual_df["mode"].unique())
    
    # Filter MODE_ORDER to only include modes that exist in the data
    modelist = [m for m in MODE_ORDER if m in available_modes]
    
    # Calculate actual rows needed
    actual_rows = (len(modelist) // cols) + (len(modelist) % cols != 0)
    
    plt.rcParams['figure.dpi'] = dpi
    fig, axes = plt.subplots(nrows=actual_rows, ncols=cols, figsize=(5*cols, 3*actual_rows))
    
    # Ensure axes is 2D array even for single row
    if actual_rows == 1:
        axes = axes.reshape(1, -1)
    
    idx = 0
    for r in range(actual_rows):
        for c in range(cols):
            if idx < len(modelist):
                mode = modelist[idx]
                x_syn1 = synthetic_df1[synthetic_df1["mode"]==mode]["crowfly_distance"]
                x_syn2 = synthetic_df2[synthetic_df2["mode"]==mode]["crowfly_distance"]
                y_hts = None
                if actual_df is not None:
                    y_hts = actual_df[actual_df["mode"]==mode][["crowfly_distance", "weight_person"]]
                axes = add_threeway_cdf(axes, r, c, mode, y_hts, x_syn1, x_syn2,
                                      label_syn1=label_syn1, label_syn2=label_syn2)
                idx += 1
            else:
                axes[r, c].set_visible(False)
    
    # Add a single legend for the entire figure positioned on the right
    import matplotlib.lines as mlines
    hts_line = mlines.Line2D([], [], color=COLOR_ACTUAL_HTS, linewidth=2, label='HTS')
    syn1_line = mlines.Line2D([], [], color=COLOR_CARLA, linewidth=2, label=label_syn1)
    syn2_line = mlines.Line2D([], [], color=COLOR_HOERL, linewidth=2, label=label_syn2)
    fig.legend(handles=[hts_line, syn1_line, syn2_line], loc='center right', bbox_to_anchor=(0.98, 0.5))
    
    fig.suptitle("Distribution of Distances by Mode", fontsize=14)
    fig.tight_layout()
    fig.subplots_adjust(right=0.85)  # Make room for legend on the right
    plt.savefig("%s/%s" % (context.config("analysis_path"), title), bbox_inches='tight', dpi=dpi)
    plt.close()
