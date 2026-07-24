import pandas as pd
import numpy as np
import geopandas as gpd
import analysis_dhaka.seville.ivt_style.myutils as myutils
import analysis_dhaka.seville.ivt_style.myplottools as myplottools
import matplotlib.pyplot as plt
#import data.constants as c
import pyproj
#import data.utils
import data.spatial.utils
from tqdm import tqdm
import data.hts.entd.cleaned
import warnings

# Suppress NetworkX FutureWarning about node_link_data/edges kwarg emitted upstream by dependencies.
# We don't call node_link_data here; this avoids noisy logs until upstream sets edges explicitly.
warnings.filterwarnings("ignore", message=r".*edges kwarg.*node_link_data.*", category=FutureWarning)


def configure(context):
    context.config("output_path")
    context.config("data_path")
    context.config("analysis_path")
    context.config("output_prefix")

    context.stage("synthesis.output")
    
    context.stage("seville.data.census.population")
    # Also prepare census license totals for comparison plots

    context.stage("seville.data.census.population")
    context.stage("seville.data.census.employment")
    context.stage("seville.data.census.licenses")


    context.stage("data.hts.entd.reweighted")

    context.config("weekend_scenario", False)
    context.config("specific_weekend_scenario", "all") # options are "all", "saturday", "sunday"
    context.config("specific_day_scenario", "avgworkday") #options can be any of the days of the week or "avgworkday"
    
    
def import_data_synthetic(context, population_selector = None, custom_output_path = None):
    """
    Import synthetic population data.
    
    Args:
        context: Pipeline context
        population_selector: Optional filters for population
        custom_output_path: Optional custom path (for loading alternative algorithm output)
    """
    output_path = custom_output_path if custom_output_path else context.config("output_path")
    output_prefix = context.config("output_prefix")
    
    filepath = "%s/%strips.csv" % (output_path, output_prefix)
    df_trips = pd.read_csv(filepath, encoding = "latin1", sep = ";")

    filepath = "%s/%spersons.csv" %  (output_path, output_prefix)
    df_persons = pd.read_csv(filepath, encoding = "latin1", sep = ";")

    filepath = "%s/%shouseholds.csv" %  (output_path, output_prefix)
    df_hhl = pd.read_csv(filepath, encoding = "latin1", sep = ";")


    # Add activity flag: whether a person has at least one trip
    df_persons["is_active"] = df_persons["person_id"].isin(df_trips["person_id"]).astype(bool)

    # NOTE (kept for reference): legacy merges that dropped non-travelers.
    # df_syn = df_persons.merge(df_hhl, left_on="person_id", right_on="household_id")
    # df_syn = df_persons.merge(df_trips, left_on="person_id", right_on="person_id")

    # Debug: how many persons are covered by trips vs. total persons
    total_persons = len(df_persons)
    persons_in_trips = df_trips["person_id"].nunique()
    # print(f"[DEBUG] Synthetic persons total={total_persons}, in trips={persons_in_trips} ({persons_in_trips/total_persons*100:.2f}%)")
    
    t_id = df_trips["person_id"].values.tolist()
    df_persons_no_trip = df_persons[np.logical_not(df_persons["person_id"].isin(t_id))]
    df_persons_no_trip = df_persons_no_trip.set_index(["person_id"])
    print(df_persons_no_trip.shape, "persons without trip in synpop")
    # print(f"[DEBUG] Synthetic df_syn rows={len(df_syn)}; unique persons in df_syn={df_syn['person_id'].nunique()}")

    if population_selector:
        if "age_selector" in population_selector.keys():
            age_min = population_selector["age_selector"][0]
            age_max = population_selector["age_selector"][1]
            # Apply filters to both persons and trips (keep person-level intact)
            df_persons = df_persons[(df_persons["age"] <= age_max) & (df_persons["age"] >= age_min)]
            df_trips = df_trips[(df_trips["age"] <= age_max) & (df_trips["age"] >= age_min)]
            df_persons_no_trip = df_persons_no_trip[(df_persons_no_trip["age"] <= age_max) & (df_persons_no_trip["age"] >= age_min)]
            print("INFO excluding agents NOT between the age of ", age_min, " and ", age_max)
        if "gender_selector" in population_selector.keys():
            gender = population_selector["gender_selector"]
            if gender == "male":
                g = 0
            else:
                g = 1
            df_persons = df_persons[df_persons["sex"] == g]
            df_trips = df_trips[df_trips["sex"] == g]
            df_persons_no_trip = df_persons_no_trip[df_persons_no_trip["sex"] == g]
            print("INFO only considering ", gender, " agents.")
        if "canton_selector" in population_selector.keys():
            cantons = population_selector["canton_selector"]
            df_persons = df_persons[df_persons["canton_id"].isin(cantons)]
            df_trips = df_trips[df_trips["canton_id"].isin(cantons)]
            df_persons_no_trip = df_persons_no_trip[df_persons_no_trip["canton_id"].isin(cantons)]
            print("INFO only considering agents living in cantons n° ", cantons)

    # Return person-level dataframe with is_active and the trips dataframe.
    # df_syn (merged persons-trips) is deprecated in favor of is_active flag.
    return df_persons, df_trips, df_persons_no_trip   


def import_data_actual(context, population_selector = None):
    try:
        hts_data = context.stage("data.hts.entd.reweighted")
        if hts_data is None or any(x is None for x in hts_data):
            print("WARNING: HTS data is not available - returning None")
            return None, None, None
        
        df_act_households , df_act_persons, df_act_trips = hts_data
    except Exception as e:
        print(f"WARNING: Could not load HTS data: {e}")
        return None, None, None
    
    # First ensure number_of_vehicles is numeric and convert to boolean
    df_act_households["number_of_vehicles"] = pd.to_numeric(df_act_households["number_of_vehicles"], errors="coerce").fillna(0)
    df_act_households["car_availability"] = df_act_households["number_of_vehicles"] > 0
    # Merge to persons dataframe on household_id
    df_act_persons = df_act_persons.merge(
        df_act_households[["household_id", "car_availability"]],
        on="household_id",
        how="left"
    )
    # Fill missing households with False (if person household_id not in households df)
    df_act_persons["car_availability"] = df_act_persons["car_availability"].fillna(False)

    df_act_persons.rename(columns = {"person_weight": "weight_person"}, inplace = True)
    df_px = df_act_persons[["person_id", "weight_person", "employed", "studies",
                                                "age", "sex", "car_availability", "has_license", "has_pt_subscription", "socioprofessional_class"]]
    df_act = df_act_trips.merge(df_px, on=["person_id"], how='left')

    df_act["preceding_purpose"] = df_act["preceding_purpose"].astype(str)
    df_act["following_purpose"] = df_act["following_purpose"].astype(str)
    df_act["od"] = df_act["preceding_purpose"] + "_" + df_act["following_purpose"]

    # Only keep the persons that could have been used in activity chain matching, due to 
    # using specifc days of the week
    df_act = df_act[~df_act["weight_person"].isna()]
    df_act = df_act.set_index(["person_id"])
    df_act.sort_index(inplace=True)

    t_id = df_act_trips["person_id"].values.tolist()
    # Tag active persons (with at least one trip)
    df_act_persons["is_active"] = df_act_persons["person_id"].isin(t_id).astype(bool)
    df_persons_no_trip = df_act_persons[np.logical_not(df_act_persons["person_id"].isin(t_id))]
    df_persons_no_trip = df_persons_no_trip.set_index(["person_id"])
    print(df_persons_no_trip.shape, "persons without trip in hts")
    # Debug: size and coverage
    total_hts_persons = len(df_act_persons)
    persons_in_trips = df_act_trips["person_id"].nunique()
    # print(f"[DEBUG] HTS persons total={total_hts_persons}, in trips={persons_in_trips} ({persons_in_trips/total_hts_persons*100:.2f}%)")

    if population_selector:
        if "age_selector" in population_selector.keys():
            age_min = population_selector["age_selector"][0]
            age_max = population_selector["age_selector"][1]
            # Filter trip-level by person age via merged attributes
            df_act = df_act[(df_act["age"] <= age_max) & (df_act["age"] >= age_min)]
            df_persons_no_trip = df_persons_no_trip[(df_persons_no_trip["age"] <= age_max) & (df_persons_no_trip["age"] >= age_min)]
            print("INFO excluding agents NOT between the age of ", age_min, " and ", age_max)
        if "gender_selector" in population_selector.keys():
            gender = population_selector["gender_selector"]
            if gender == "male":
                g = 0
            else:
                g = 1
            df_act = df_act[df_act["sex"] == g]
            df_persons_no_trip = df_persons_no_trip[df_persons_no_trip["sex"] == g]
            print("INFO only considering ", gender, " agents.")

    # df_act contains only those that have trips
    # Legacy return was (df_act_trip_merged, df_persons_no_trip). We now return the
    # person-level dataframe (with is_active) alongside the trips and the no-trip view.
    return df_act_persons, df_act_trips, df_persons_no_trip


    
def import_data_census(context, population_selector = None):
    df_population = context.stage("seville.data.census.population")

    return df_population
    

def aux_data_frame(df_act_trips, df_syn_trips, df_act_persons, df_syn_persons, population_selector = None):
    # NOTE: Any population selection should be applied upstream on person-level frames
    # and then propagated to trips via person_id. This block is kept as a comment
    # to document the previous approach that attempted to filter here.
    # if population_selector:
    #     if "age_selector" in population_selector.keys():
    #         age_min = population_selector["age_selector"][0]
    #         age_max = population_selector["age_selector"][1]
    #         print("INFO excluding agents NOT between the age of ", age_min, " and ", age_max)
    #     if "gender_selector" in population_selector.keys():
    #         gender = population_selector["gender_selector"]
    #         print("INFO only considering ", gender, " agents.")

    # Work on a local reset copy to avoid mutating caller's df_act
    # Use trip-level frames for chain summaries; merge person weights onto trips
    df_act_reset = df_act_trips.reset_index()  # brings index name 'person_id' as a column
    if 'weight_person' not in df_act_reset.columns and 'weight_person' in df_act_persons.columns:
        df_act_reset = df_act_reset.merge(
            df_act_persons[['person_id','weight_person']], on='person_id', how='left'
        )
    pers_ids = df_act_reset["person_id"].unique()

    df_aux_act = pd.DataFrame({
        "person_id": pers_ids,
        # If weight_person missing (e.g., no merge), default to 1.0 via fillna below
        "weight_person": df_act_reset.groupby("person_id")["weight_person"].mean(),
        "chain": "home-" + df_act_reset.groupby("person_id")["following_purpose"].apply(lambda x: "-".join(x))
    }).fillna({"weight_person": 1.0})

    pers_ids = df_syn_trips["person_id"].unique()

    df_aux_syn = pd.DataFrame({
        "person_id": pers_ids,
        "weights": 1,
    "chain": "home-" + df_syn_trips.groupby("person_id")["following_purpose"].apply(lambda x: "-".join(x))
    })

    return df_aux_act, df_aux_syn


def activity_chains_comparison(context, all_CC, suffix = None):
    # Get percentages, prepare for plotting
    synthetic_sum = all_CC["synthetic Count"].sum()
    if synthetic_sum != 0:
        all_CC["synthetic Count"] = all_CC["synthetic Count"] / synthetic_sum * 100
    else:
        all_CC["synthetic Count"] = 0
    all_CC["actual Count"] = all_CC["actual Count"] / all_CC["actual Count"].sum() *100
    all_CC = all_CC.sort_values(by=['actual Count'], ascending=False)
    all_CC.to_csv("%s/actchains_DF.csv" % context.config("analysis_path"), index = False)

    # First step done: plot activity chain counts
    title_plot = "Synthetic and HTS activity chain comparison"
    title_figure = "activitychains"
    if suffix:
        title_plot += " - " + suffix
        title_figure += "_" + suffix
        
    title_figure += ".png"
    myplottools.plot_comparison_bar(context, imtitle = title_figure, plottitle = title_plot, ylabel = "Percentage", xlabel = "Activity chain", lab = all_CC["Chain"], actual = all_CC["actual Count"], synthetic = all_CC["synthetic Count"], t = 15, figsize = [12,7], dpi = 300, w = 0.35, xticksrot=True)


def activity_counts_comparison(context, all_CC, suffix = None):
    all_CC_dic = all_CC.to_dict('records')
    counts_dic = {}
    for actchain in all_CC_dic:
        chain = actchain["Chain"]
        s = actchain["synthetic Count"]
        a = actchain["actual Count"]
        if np.isnan(s):
            s = 0
        if np.isnan(a):
            a = 0
        if chain == "-" or chain == "h":
            x = 0
        else:
            act = chain.split("-")
            x = len(act) - 2
        x = min(x, 7)
        if x not in counts_dic.keys():
            counts_dic[x] = [s, a]
        else:
            counts_dic[x][0] += s
            counts_dic[x][1] += a
    
    counts = pd.DataFrame(columns = ["number", "synthetic Count", "actual Count"])
    for k in range(min(8, np.max(list(counts_dic.keys())))):
        v = counts_dic[k]
        if k == 7:
            l = "7+"
        else:
            l = str(int(k))
        counts.loc[k] = pd.Series({"number": l, 
                                      "synthetic Count": v[0],
                                      "actual Count": v[1]
                                          })
    
    # Get percentages, prepare for plotting
    counts["synthetic Count"] = counts["synthetic Count"] / counts["synthetic Count"].sum() *100
    counts["actual Count"] = counts["actual Count"] / counts["actual Count"].sum() *100
    #counts = counts.sort_values(by=['actual Count'], ascending=False)

    # First step done: plot activity chain counts
    title_plot = "Synthetic and HTS activity counts comparison"
    title_figure = "activitycounts"
    if suffix:
        title_plot += " - " + suffix
        title_figure += "_" + suffix
        
    title_figure += ".png"
    
    myplottools.plot_comparison_bar(context, imtitle = title_figure, plottitle = title_plot, 
                                    ylabel = "Percentage", xlabel = "Number of activities in the activity chain",
                                    lab = counts["number"], actual = counts["actual Count"], 
                                    synthetic = counts["synthetic Count"], xticksrot=True)
    
    
def activity_counts_per_purpose(context, all_CC, suffix = None):
    all_CC_dic = all_CC.to_dict('records')
    purposes = ['home', 'work', 'education', 'shop', 'leisure', 'other', "start_out_of_home"]
    counts_dic = {}
    for actchain in all_CC_dic:
        chain = actchain["Chain"]
        s = actchain["synthetic Count"]
        a = actchain["actual Count"]
        if np.isnan(s):
            s = 0
        if np.isnan(a):
            a = 0
        if chain == "-" or chain == "h":
            pass
        else:
            acts = chain.split("-")
            for act in acts:
                if act not in purposes:
                    purposes.append(act)
            for p in purposes:
                cpt_purpose = acts.count(p)
                if cpt_purpose > 0:
                    identifier = p + " - " + str(cpt_purpose) 
                    if cpt_purpose > 1:
                        identifier += " times"
                    else:
                        identifier += " time"
                    if identifier not in counts_dic.keys():
                        counts_dic[identifier] = [s, a]
                    else:
                        counts_dic[identifier][0] += s
                        counts_dic[identifier][1] += a
    
    counts = pd.DataFrame(columns = ["number", "synthetic Count", "actual Count"])

    for k, v in counts_dic.items():
        counts.loc[k] = pd.Series({"number": k, 
                                      "synthetic Count": v[0],
                                      "actual Count": v[1]
                                          })
            

    # Get percentages, prepare for plotting
    synthetic_sum = counts["synthetic Count"].sum()
    if synthetic_sum != 0:
       counts["synthetic Count"] = counts["synthetic Count"] / synthetic_sum * 100
    else:
        counts["synthetic Count"] = 0
    actual_sum = counts["actual Count"].sum()
    if actual_sum != 0:
        counts["actual Count"] = counts["actual Count"] / actual_sum * 100
    else:
        counts["actual Count"] = 0
    counts = counts.sort_values(by=['actual Count'], ascending=False)
    
    idx = counts.index.tolist() 
    counts = counts.reindex(idx)

    # First step done: plot activity chain counts
    title_plot = "Activity counts per purpose comparison"
    title_figure = "activitycountspurpose"
    if suffix:
        title_plot += " - " + suffix
        title_figure += "_" + suffix
        
    title_figure += ".png"
    
    myplottools.plot_comparison_bar(context, imtitle = title_figure, plottitle = title_plot, 
                                    ylabel = "Percentage", xlabel = "Activities with the same purpose in the activity chain",
                                    lab = counts["number"], actual = counts["actual Count"], 
                                    synthetic = counts["synthetic Count"], t = 20, xticksrot=True)
    

def demographics_comparison(context, df_act_persons, df_syn_persons, df_census, suffix=None, use_active_only=False):

    # Age bins (seville): keep 0-5 and 6-14 separate
    bins = [x for x in range(0, 110, 5)] 
    labels = [f"{x}-{x+4}" for x in bins[:-1]]


    # Use person-level frames directly; 'is_active' marks who has trips
    cols_act = [c for c in ["person_id", "age", "weight_person", "is_active", "has_license", "has_pt_subscription", "employed"] if c in df_act_persons.columns]
    cols_syn = [c for c in ["person_id", "age", "is_active", "has_driving_license", "has_pt_subscription", "employed"] if c in df_syn_persons.columns]
    df_act_persons = df_act_persons[cols_act].drop_duplicates(subset=["person_id"]).copy()
    df_syn_persons = df_syn_persons[cols_syn].drop_duplicates(subset=["person_id"]).copy()
    df_cen = df_census.copy()

    # Optional: restrict to active persons for plotting
    if use_active_only:
        if "is_active" in df_act_persons.columns:
            df_act_persons = df_act_persons[df_act_persons["is_active"]]
        if "is_active" in df_syn_persons.columns:
            df_syn_persons = df_syn_persons[df_syn_persons["is_active"]]

    # Cut ages into labeled bins (person-level)
    df_act_persons['age_bin'] = pd.cut(df_act_persons["age"], bins=bins, labels=labels, right=False)
    df_syn_persons['age_bin'] = pd.cut(df_syn_persons["age"], bins=bins, labels=labels, right=False)
    df_cen['age_bin'] = pd.cut(df_cen["age_class"], bins=bins, labels=labels, right=False)


    # Debug: bins and basic distributions before weighting/percentages
    # print(f"[DEBUG] Age bins used: {bins}")
    # print("[DEBUG] HTS age_bin value_counts (raw, person-level, incl. no-trip):\n", df_act_persons['age_bin'].value_counts(dropna=False))
    # print("[DEBUG] SYN age_bin value_counts (raw, person-level, incl. no-trip):\n", df_syn_persons['age_bin'].value_counts(dropna=False))

    # Weighted HTS counts, unweighted synthetic counts; both as percentages
    act_counts = myplottools.compute_counts(df_act_persons['age_bin'], weights=df_act_persons['weight_person'], categories=labels)
    syn_counts = myplottools.compute_counts(df_syn_persons['age_bin'], weights=None, categories=labels)
    census_counts = myplottools.compute_counts(df_cen['age_bin'], weights=df_cen['weight'], categories=labels)

    # Align to labels only (drop possible NaN bucket from unweighted path)
    import pandas as _pd
    act_counts = _pd.Series(act_counts).reindex(labels).fillna(0)
    syn_counts = _pd.Series(syn_counts).reindex(labels).fillna(0)
    census_counts = _pd.Series(census_counts).reindex(labels).fillna(0)

    # Debug: sums should be ~100, show small deviations
    # print(f"[DEBUG] HTS percent sum={act_counts.sum():.6f}; SYN percent sum={syn_counts.sum():.6f}")
    if df_census is None:
        print("[DEBUG] Census is None in demographics_comparison; plotting will be HTS vs SYN only.")

    # Prepare variable to satisfy linters; will be overwritten if census provided

    # Census data processing and alignment
    if df_census is not None:

        # Debug: census distribution sanity
        # print("[DEBUG] Census counts sum=", census_counts.sum())
        # print("[DEBUG] Census age_label distribution (percent):\n", census_counts)

        # Calculate differences (Synthetic - Reference)
        diff_hts = syn_counts - act_counts
        diff_census = syn_counts - census_counts

        # Debug: show biggest gaps
        ordered_labels = list(labels)
        df_debug = pd.DataFrame({
            "label": ordered_labels,
            "pct_hts": act_counts.reindex(ordered_labels).values,
            "pct_syn": syn_counts.reindex(ordered_labels).values,
            "pct_census": census_counts.reindex(ordered_labels).values,
            "gap_syn_minus_census": (syn_counts - census_counts).reindex(ordered_labels).values,
            "gap_hts_minus_census": (act_counts - census_counts).reindex(ordered_labels).values,
        })
        # print("[DEBUG] Age distribution table (percentages and gaps vs census):\n", df_debug)

        # Plot differences
        title_figure_diff = "agedistribution_differences"
        title_plot_diff = "Age Distribution Differences from Synthetic"
        if suffix:
            title_plot_diff += " - " + suffix
            title_figure_diff += "_" + suffix
        title_figure_diff += ".png"

        myplottools.plot_distribution_differences(
            context,
            imtitle=title_figure_diff,
            plottitle=title_plot_diff,
            ylabel="Difference (Percentage Points)",
            xlabel="Age groups",
            lab=labels,
            diff_hts=diff_hts.values,
            diff_census=diff_census.values,
            xticksrot=True
        )

    # Main age plots (two-way or three-way)
    title_figure = "agedistribution"
    title_plot = "Age distribution comparison "
    if suffix:
        title_plot += " - " + suffix
        title_figure += "_" + suffix
    title_figure += ".png"
    if df_census is None:
        myplottools.plot_comparison_bar(
            context,
            imtitle=title_figure,
            plottitle=title_plot,
            ylabel="Percentage",
            xlabel="Age groups",
            lab=labels,
            hts=act_counts.values,
            synthetic=syn_counts.values,
            xticksrot=True
        )
    else:
        myplottools.plot_comparison_bar(
            context,
            imtitle=title_figure,
            plottitle=title_plot,
            ylabel="Percentage",
            xlabel="Age groups",
            lab=labels,
            hts=act_counts.values,
            synthetic=syn_counts.values,
            census=census_counts.values,
            xticksrot=True
        )

        # Graph 1: Synthetic and HTS
        title_figure_syn_hts = "agedistribution_syn_hts"
        title_plot_syn_hts = "Age distribution comparison (Synthetic vs HTS) "
        if suffix:
            title_plot_syn_hts += " - " + suffix
            title_figure_syn_hts += "_" + suffix
        title_figure_syn_hts += ".png"
        myplottools.plot_comparison_bar(
            context,
            imtitle=title_figure_syn_hts,
            plottitle=title_plot_syn_hts,
            ylabel="Percentage",
            xlabel="Age groups",
            lab=labels,
            hts=act_counts.values,
            synthetic=syn_counts.values,
            xticksrot=True
        )

        # Graph 2: Synthetic and Census
        title_figure_syn_census = "agedistribution_syn_census"
        title_plot_syn_census = "Age distribution comparison (Synthetic vs Census) "
        if suffix:
            title_plot_syn_census += " - " + suffix
            title_figure_syn_census += "_" + suffix
        title_figure_syn_census += ".png"
        myplottools.plot_comparison_bar(
            context,
            imtitle=title_figure_syn_census,
            plottitle=title_plot_syn_census,
            ylabel="Percentage",
            xlabel="Age groups",
            lab=labels,
            synthetic=syn_counts.values,
            census=census_counts.values,
            xticksrot=True
        )

    # -------- Employment status comparison (person-level) --------
    def employment_status(df):
        return df["employed"].replace({ False: "unemployed", True: "employed"})

    df_act_persons_local = df_act_persons.copy()
    df_act_persons_local["employment_status"] = employment_status(df_act_persons_local)
    act_counts = myplottools.compute_counts(df_act_persons_local["employment_status"], weights=df_act_persons_local["weight_person"], categories=["unemployed", "employed"])  # fixed order

    syn_persons_local = df_syn_persons.copy()
    syn_employment = employment_status(syn_persons_local)
    syn_counts = myplottools.compute_counts(syn_employment, weights=None, categories=act_counts.index.tolist())
    syn_counts = myplottools.align_series(act_counts, syn_counts)

    title_figure = "employmentstatus"
    title_plot = "Employment status comparison "
    if suffix:
        title_plot += " - " + suffix
        title_figure += "_" + suffix
    title_figure += ".png"
    myplottools.plot_comparison_bar(
        context,
        imtitle=title_figure,
        plottitle=title_plot,
        ylabel="Percentage",
        xlabel="Employment status",
        lab=act_counts.index,
        hts=act_counts.values,
        synthetic=syn_counts.values,
        xticksrot=True
    )

    # -------- Driving license (HTS as reference, add Census third series) --------
    act_license_labels = myplottools.map_bool_to_labels(df_act_persons.get("has_license"), yes_label="Yes", no_label="No")
    act_counts = myplottools.compute_counts(act_license_labels, weights=df_act_persons.get("weight_person"), categories=["No", "Yes"])  # fixed order

    syn_license_labels = myplottools.map_bool_to_labels(df_syn_persons.get("has_driving_license"), yes_label="Yes", no_label="No")
    syn_counts = myplottools.compute_counts(syn_license_labels, weights=None, categories=act_counts.index.tolist())
    syn_counts = myplottools.align_series(act_counts, syn_counts)

    title_figure = "drivinglicense"
    title_plot = "Driving license comparison "
    if suffix:
        title_plot += " - " + suffix
        title_figure += "_" + suffix
    title_figure += ".png"

    # print("ACT: \n", act_counts)
    # print("SYN: \n", syn_counts)

    myplottools.plot_comparison_bar(
        context,
        imtitle=title_figure,
        plottitle=title_plot,
        ylabel="Percentage",
        xlabel="Has driving license",
        lab=act_counts.index,
        hts=act_counts.values,
        synthetic=syn_counts.values,
        xticksrot=True
    )

    # Additional plot including Census (IPF) as third series
    try:
        df_population = context.stage("seville.data.census.population")
        df_employment = context.stage("seville.data.census.employment")
        df_licenses_municipality = context.stage("seville.data.census.licenses")

        total_pop = df_population["weight"].sum()
        total_license = df_licenses_municipality["weight"].sum()
        census_yes = 100.0 * (total_license / total_pop) if total_pop > 0 else 0.0
        census_no = 100.0 - census_yes
        import pandas as _pd
        census_counts = _pd.Series({"No": census_no, "Yes": census_yes})
        census_counts = census_counts.reindex(act_counts.index).fillna(0)

        title_figure_all = "drivinglicense_all"
        title_plot_all = "Driving license comparison (HTS vs Synthetic vs Census) "
        if suffix:
            title_plot_all += " - " + suffix
            title_figure_all += "_" + suffix
        title_figure_all += ".png"

        myplottools.plot_comparison_bar(
            context,
            imtitle=title_figure_all,
            plottitle=title_plot_all,
            ylabel="Percentage",
            xlabel="Has driving license",
            lab=act_counts.index,
            hts=act_counts.values,
            synthetic=syn_counts.values,
            census=census_counts.values,
            xticksrot=True
        )
    except Exception as _e:
        print("Warning: could not add census driving license plot:", _e)

    # -------- Public transport subscription --------
    act_pt_labels = myplottools.map_bool_to_labels(df_act_persons.get("has_pt_subscription"), yes_label="Yes", no_label="No")
    act_counts = myplottools.compute_counts(act_pt_labels, weights=df_act_persons.get("weight_person"), categories=["No", "Yes"])  # fixed order

    syn_pt_labels = myplottools.map_bool_to_labels(df_syn_persons.get("has_pt_subscription"), yes_label="Yes", no_label="No")
    syn_counts = myplottools.compute_counts(syn_pt_labels, weights=None, categories=act_counts.index.tolist())
    syn_counts = myplottools.align_series(act_counts, syn_counts)

    title_figure = "ptsubscription"
    title_plot = "Public transport subscription comparison "
    if suffix:
        title_plot += " - " + suffix
        title_figure += "_" + suffix
    title_figure += ".png"
    myplottools.plot_comparison_bar(
        context,
        imtitle=title_figure,
        plottitle=title_plot,
        ylabel="Percentage",
        xlabel="Has public transport subscription",
        lab=act_counts.index,
        hts=act_counts.values,
        synthetic=syn_counts.values,
        xticksrot=True
    )


def summary_horizontal(context, df_act_persons, df_syn_persons, df_census, suffix=None, use_active_only=False):
    """
    Build a single horizontal summary plot with the following comparisons:
    - syn vs census: age, sex, employment
    - syn vs hts: driving license, pt subscription

    All metrics are expressed as percentages of persons (HTS/Census weighted; Synthetic unweighted).
    Studies comparison has been removed to avoid showing artificially enriched data.
    """
    import pandas as _pd

    # Optionally restrict to active persons
    if use_active_only:
        if "is_active" in df_act_persons.columns:
            df_act_persons = df_act_persons[df_act_persons["is_active"]]
        if "is_active" in df_syn_persons.columns:
            df_syn_persons = df_syn_persons[df_syn_persons["is_active"]]

    labels_all = []
    syn_vals = []
    hts_vals = []
    cen_vals = []

    # ---------- Age (syn vs census) ----------
    age_bins = [x for x in range(0, 110, 5)]
    age_labels = [f"{x}-{x+4}" for x in age_bins[:-1]]
    if "age" in df_syn_persons.columns:
        syn_age = _pd.cut(df_syn_persons["age"], bins=age_bins, labels=age_labels, right=False)
        syn_age_pct = myplottools.compute_counts(syn_age, categories=age_labels)
    else:
        syn_age_pct = _pd.Series([float("nan")] * len(age_labels), index=age_labels)

    if df_census is not None and {"age_class", "weight"}.issubset(df_census.columns):    
        df_cen = df_census.copy()
        df_cen['age_bin'] = pd.cut(df_cen["age_class"], bins=age_bins, labels=age_labels, right=False)
        census_counts = myplottools.compute_counts(df_cen['age_bin'], weights=df_cen['weight'], categories=age_labels)
        census_counts = _pd.Series(census_counts).reindex(age_labels).fillna(0)



    labels_all.extend([f"Age {l}" for l in age_labels])
    syn_vals.extend(list(syn_age_pct.reindex(age_labels).values))
    hts_vals.extend([float("nan")] * len(age_labels))
    cen_vals.extend(list(census_counts.values))

    # ---------- Sex (syn vs census) ----------
    sex_labels = ["Female", "Male"]
    # Synthetic: sex can be string or numeric (1 male, 2 female) depending on pipeline
    syn_sex_series = df_syn_persons.get("sex")
    if syn_sex_series is not None:
        syn_sex_norm = syn_sex_series.replace({1: "male", 2: "female", 0: "male"}).astype(str).str.lower()
        syn_sex_lab = syn_sex_norm.replace({"female": "Female", "male": "Male"})
        syn_sex_pct = myplottools.compute_counts(syn_sex_lab, categories=sex_labels)
    else:
        syn_sex_pct = _pd.Series([float("nan")] * 2, index=sex_labels)

    cen_sex_pct = _pd.Series([float("nan")] * 2, index=sex_labels)
    if df_census is not None and {"sex", "weight"}.issubset(df_census.columns):
        cen_sex_norm = df_census["sex"].astype(str).str.lower()
        cen_sex_lab = cen_sex_norm.replace({"female": "Female", "male": "Male"})
        tmp = _pd.DataFrame({"label": cen_sex_lab, "w": df_census["weight"]})
        cen_raw = tmp.groupby("label")["w"].sum()
        cen_sex_pct = (cen_raw / cen_raw.sum() * 100.0).reindex(sex_labels).fillna(0)

    labels_all.extend(sex_labels)
    syn_vals.extend(list(syn_sex_pct.reindex(sex_labels).values))
    hts_vals.extend([float("nan")] * len(sex_labels))
    cen_vals.extend(list(cen_sex_pct.reindex(sex_labels).values))

    # ---------- Employment (syn vs census) ----------
    emp_labels = ["Unemployed", "Employed"]
    syn_emp_series = df_syn_persons.get("employed")
    if syn_emp_series is not None:
        syn_emp_lab = syn_emp_series.replace({False: "Unemployed", True: "Employed"})
        syn_emp_pct = myplottools.compute_counts(syn_emp_lab, categories=emp_labels)
    else:
        syn_emp_pct = _pd.Series([float("nan")] * 2, index=emp_labels)

    cen_emp_pct = _pd.Series([float("nan")] * 2, index=emp_labels)
    try:
        # Pull employment and population from IPF preparation
        df_population = context.stage("seville.data.census.population")
        df_employment = context.stage("seville.data.census.employment")
        df_licenses_municipality = context.stage("seville.data.census.licenses")

        total_pop = float(df_population["weight"].sum())
        total_emp = float(df_employment["weight"].sum())
        if total_pop > 0:
            cen_emp_pct = _pd.Series({
                "Unemployed": max(0.0, (1.0 - total_emp / total_pop) * 100.0),
                "Employed": min(100.0, (total_emp / total_pop) * 100.0)
            }).reindex(emp_labels)
    except Exception as _e:
        print("[WARN] Could not derive Census employment distribution:", _e)

    labels_all.extend(emp_labels)
    syn_vals.extend(list(syn_emp_pct.reindex(emp_labels).values))
    hts_vals.extend([float("nan")] * len(emp_labels))
    cen_vals.extend(list(cen_emp_pct.reindex(emp_labels).values))

    # ---------- Driving license (syn vs HTS) ----------
    lic_labels = ["Driving license No", "Driving license Yes"]
    syn_lic_series = df_syn_persons.get("has_driving_license")
    if syn_lic_series is None and "has_license" in df_syn_persons.columns:
        syn_lic_series = df_syn_persons["has_license"]
    syn_lic_lab = _pd.Series(syn_lic_series).replace({False: "Driving license No", True: "Driving license Yes"}) if syn_lic_series is not None else None
    syn_lic_pct = myplottools.compute_counts(syn_lic_lab, categories=lic_labels) if syn_lic_lab is not None else _pd.Series([float("nan")] * 2, index=lic_labels)

    act_lic_series = df_act_persons.get("has_license")
    act_lic_lab = _pd.Series(act_lic_series).replace({False: "Driving license No", True: "Driving license Yes"}) if act_lic_series is not None else None
    _wcol = "weight_person" if "weight_person" in df_act_persons.columns else ("person_weight" if "person_weight" in df_act_persons.columns else None)
    _wser = df_act_persons[_wcol] if _wcol is not None else None
    act_lic_pct = myplottools.compute_counts(act_lic_lab, weights=_wser, categories=lic_labels) if act_lic_lab is not None else _pd.Series([float("nan")] * 2, index=lic_labels)

    labels_all.extend(lic_labels)
    syn_vals.extend(list(syn_lic_pct.reindex(lic_labels).values))
    hts_vals.extend(list(act_lic_pct.reindex(lic_labels).values))
    cen_vals.extend([float("nan")] * len(lic_labels))

    # ---------- PT subscription (syn vs HTS) ----------
    pts_labels = ["PT Subscription No", "PT Subscription Yes"]
    syn_pts_series = df_syn_persons.get("has_pt_subscription")
    syn_pts_lab = _pd.Series(syn_pts_series).replace({False: "PT Subscription No", True: "PT Subscription Yes"}) if syn_pts_series is not None else None
    syn_pts_pct = myplottools.compute_counts(syn_pts_lab, categories=pts_labels) if syn_pts_lab is not None else _pd.Series([float("nan")] * 2, index=pts_labels)

    act_pts_series = df_act_persons.get("has_pt_subscription")
    act_pts_lab = _pd.Series(act_pts_series).replace({False: "PT Subscription No", True: "PT Subscription Yes"}) if act_pts_series is not None else None
    _wcol2 = "weight_person" if "weight_person" in df_act_persons.columns else ("person_weight" if "person_weight" in df_act_persons.columns else None)
    _wser2 = df_act_persons[_wcol2] if _wcol2 is not None else None
    act_pts_pct = myplottools.compute_counts(act_pts_lab, weights=_wser2, categories=pts_labels) if act_pts_lab is not None else _pd.Series([float("nan")] * 2, index=pts_labels)

    labels_all.extend(pts_labels)
    syn_vals.extend(list(syn_pts_pct.reindex(pts_labels).values))
    hts_vals.extend(list(act_pts_pct.reindex(pts_labels).values))
    cen_vals.extend([float("nan")] * len(pts_labels))

    # ---- Plot ----
    imtitle = "summary_horizontal"
    plottitle = "seville sociodemographic summary"
    if suffix:
        imtitle += f"_{suffix}"
        plottitle += f" - {suffix}"
    imtitle += ".png"

    myplottools.plot_horizontal_comparison(
        context,
        imtitle=imtitle,
        plottitle=plottitle,
        xlabel="Percentage of population (%)",
        labels=labels_all,
        synthetic=syn_vals,
        hts=hts_vals,
        census=cen_vals,
        lablist=["Synthetic", "HTS", "Census"],
        figsize=[10, max(8, int(len(labels_all) * 0.35))],
        dpi=300,
        bar_height=0.7
    )


def compute_distances_synthetic(df_syn, threshold = 25):
    # Use euclidean_distance if available, otherwise try crowfly_distance
    if "euclidean_distance" in df_syn.columns:
        df_syn["crowfly_distance"] = 0.001 * np.array(df_syn["euclidean_distance"])
    elif "crowfly_distance" in df_syn.columns:
        df_syn["crowfly_distance"] = 0.001 * np.array(df_syn["crowfly_distance"])
    else:
        print("WARNING: No distance column found in synthetic data")
        return df_syn

    # Only consider crowfly distances shorter than <threshold> km
    df_syn_dist = df_syn[df_syn["crowfly_distance"] < threshold]
    df_syn_dist = df_syn_dist[df_syn_dist["crowfly_distance"] > 0]
    return df_syn_dist


def compute_distances_actual(df_act, threshold = 25):
    # Use routed_distance from HTS data (already in meters) as approximation for crowfly distance
    df_act["crowfly_distance"] = df_act["routed_distance"] / 1000.0  # Convert meters to km
    
    df_act_dist = df_act[df_act["crowfly_distance"] < threshold]
    df_act_dist = df_act_dist[df_act_dist["crowfly_distance"] > 0]
    return df_act_dist


def compare_dist_from_home(context, df_syn, df_act, target_purpose = "education", suffix = None):
    if not "origin_purpose" in df_act.columns:
        df_act.loc[:, "origin_purpose"] = df_act["preceding_purpose"]

    # select candidates
    filter_home_prim_syn = (df_syn["following_purpose"] == target_purpose) & (df_syn["preceding_purpose"] == "home")
    filter_home_prim_act = (df_act["following_purpose"] == target_purpose) & (df_act["origin_purpose"] == "home")
    filter_prim_home_syn = (df_syn["following_purpose"] == "home") & (df_syn["preceding_purpose"] == target_purpose)
    filter_prim_home_act = (df_act["following_purpose"] == "home") & (df_act["origin_purpose"] == target_purpose)


    df_syn_educ = df_syn[filter_home_prim_syn | filter_prim_home_syn].drop_duplicates(subset = ["person_id"])
    df_act_educ = df_act[filter_prim_home_act | filter_home_prim_act].drop_duplicates(subset = ["person_id"])

    pers_educ_syn = list(set(df_syn_educ["person_id"].values))
    pers_educ_act = list(set(df_act_educ["person_id"].values))

    dic_syn = {"person_id": pers_educ_syn, "dist_home_educ": [0 for i in range(len(pers_educ_syn))]}
    dic_act = {"person_id": pers_educ_act, "weight_person": [0 for i in range(len(pers_educ_act))], "dist_home_educ": [0 for i in range(len(pers_educ_act))]}

    for i in range(len(pers_educ_syn)):
        pid = pers_educ_syn[i]
        df_pers = df_syn_educ[df_syn_educ["person_id"] == pid]
        dist = 0.0
        for _, row in df_pers.iterrows():
            dist = float(row.get("crowfly_distance", 0.0))
        dic_syn["dist_home_educ"][i] = dist
            
    for i in range(len(pers_educ_act)):
        pid = pers_educ_act[i]
        df_pers = df_act_educ[df_act_educ["person_id"] == pid]
        home_x = 0.0
        home_y = 0.0
        educ_x = 0.0
        educ_y = 0.0
        have_home = False
        have_educ = False
        last_weight = 0.0
        for _, row in df_pers.iterrows():
            last_weight = float(row.get("weight_person", 0.0))
            if row.get("origin_purpose") != target_purpose:
                home_x = float(row.get("origin_x", home_x))
                home_y = float(row.get("origin_y", home_y))
                have_home = True
            elif row.get("following_purpose") != target_purpose:
                home_x = float(row.get("destination_x", home_x))
                home_y = float(row.get("destination_y", home_y))
                have_home = True
            if row.get("origin_purpose") == target_purpose:
                educ_x = float(row.get("origin_x", educ_x))
                educ_y = float(row.get("origin_y", educ_y))
                have_educ = True
            elif row.get("following_purpose") == target_purpose:
                educ_x = float(row.get("destination_x", educ_x))
                educ_y = float(row.get("destination_y", educ_y))
                have_educ = True
            if have_home and have_educ:
                break
        if have_home and have_educ:
            dist_val = 0.001 * float(np.sqrt(((home_x - educ_x) ** 2 + (home_y - educ_y) ** 2)))
            weight_val = last_weight
        else:
            dist_val = 0.0
            weight_val = 0.0
        dic_act["dist_home_educ"][i] = dist_val
        dic_act["weight_person"][i] = weight_val

    dist_df_syn = pd.DataFrame.from_dict(dic_syn)
    dist_df_act = pd.DataFrame.from_dict(dic_act)

    syn = dist_df_syn["dist_home_educ"].values
    act = dist_df_act["dist_home_educ"].values
    act_w = dist_df_act["weight_person"].values

    fig, ax = plt.subplots(1,1)
    x_data = np.array(syn, dtype=np.float64)
    x_sorted = np.argsort(x_data)
    x_weights = np.array([1.0 for i in range(len(syn))], dtype=np.float64)
    x_cdf = np.cumsum(x_weights[x_sorted])
    if len(x_cdf) >= 1 and x_cdf[-1] > 0:
        x_cdf /= x_cdf[-1]

    y_data = np.array(act, dtype=np.float64)
    y_sorted = np.argsort(y_data)
    y_weights = np.array(act_w, dtype=np.float64)
    y_cdf = np.cumsum(y_weights[y_sorted])
    if len(y_cdf) >= 1 and y_cdf[-1] > 0:
        y_cdf /= y_cdf[-1]

    ax.plot(y_data[y_sorted], y_cdf, label="Actual", color = "#A3A3A3")
    ax.plot(x_data[x_sorted], x_cdf, label="Synthetic", color="#00205B")  

    imtitle = "dist_home_"+target_purpose
    plottitle = "Distance from home to " + target_purpose
    if suffix:
        imtitle += "_" + suffix
        plottitle  += " - " + suffix 
    imtitle += ".png"

    ax.set_ylabel("Probability")
    ax.set_xlabel("Crowfly Distance [km]")
    ax.legend(loc="best")
    ax.set_title(plottitle)
    plt.tight_layout()
    plt.savefig("%s/" % context.config("analysis_path") + imtitle)
    plt.close()
    return syn, act, act_w


# def mode_purpose_comparison(context, df_syn, df_act, suffix = None):
#     # first in the synthetic data
#     types = df_syn.groupby(["mode","following_purpose"]).count()["person_id"]
#     syn = types / types.sum()

#     # then in the actual data
#     df_act.loc[df_act["mode"]=='car_passanger', "mode"] = 'car_passenger'
#     which = ["car","car_passenger","pt", "taxi","walk"]
#     atypes = df_act.groupby(["mode","destination_purpose"]).sum().loc[which,"weight_person"].reindex(index=which, level=0)
#     act = atypes / atypes.sum()
    
#     lista = [item for item in list(types.index.levels[0]) for i in range(len(types.index.levels[1]))]
#     listb = list(types.index.levels[1]) * len(types.index.levels[0])
#     labels = [a + " " + b for a, b in zip(lista,listb)]

#     # already ready to plot!
#     title_plot = "Synthetic and HTS Mode-Purpose Distribution"
#     title_figure = "modepurpose"
    
#     if suffix:
#         title_plot += " - " + suffix
#         title_figure += "_" + suffix
        
#     title_figure += ".png"
    
#     myplottools.plot_comparison_bar(context, imtitle = title_figure, plottitle = title_plot,
#                                     ylabel = "Percentage", xlabel = "", lab = labels, 
#                                     actual = act.values.tolist(), synthetic = syn.values.tolist(), 
#                                     t = 10, xticksrot = True )



def all_the_plot_distances(context, df_act_dist, df_syn_dist, suffix = None, df_syn_dist_alt = None, alt_label = "Hoerl"):
    """
    Generate distance distribution plots.
    
    Plot generation strategy:
    1. Always generate: HTS vs CARLA (latest simulation_output)
    2. Only if comparison enabled: HTS vs CARLA vs Hoerl (three-way comparison)
    
    Args:
        context: Pipeline context
        df_act_dist: HTS distance data
        df_syn_dist: Synthetic distance data from latest simulation (CARLA)
        suffix: Optional suffix for filenames
        df_syn_dist_alt: Optional alternative synthetic data (Hoerl) for three-way comparison
        alt_label: Label for alternative algorithm (default: "Hoerl")
    """
    dph_title = "distance_purpose_hist"
    dmh_title = "distance_mode_hist"
    dpc_title = "distance_purpose_cdf"
    dmc_title = "distance_mode_cdf"
    
    if suffix:
        dph_title += "_" + suffix
        dmh_title += "_" + suffix
        dpc_title += "_" + suffix
        dmc_title += "_" + suffix
        
    dph_title += ".png"
    dmh_title += ".png"
    dpc_title += ".png"
    dmc_title += ".png"
    
    print("INFO generating distance histograms and CDFs by purpose")
    
    # 1. ALWAYS generate: HTS vs CARLA (latest from simulation_output)
    print("INFO generating HTS vs CARLA comparison plots")
    myplottools.plot_comparison_hist_purpose(
        context, dph_title, df_act_dist, df_syn_dist,
        bins = np.linspace(0,25,120), dpi = 300, cols = 3, rows = 2
    )
    myplottools.plot_comparison_cdf_purpose(
        context, dpc_title, df_act_dist, df_syn_dist,
        dpi = 300, cols = 3, rows = 2
    )
    print(f"SUCCESS: Created HTS vs CARLA plots: {dph_title}, {dpc_title}")
    
    # Also generate mode plots for HTS vs CARLA
    if "mode" in df_syn_dist.columns and "mode" in df_act_dist.columns:
        print("INFO generating distance histograms and CDFs by mode (HTS vs CARLA)")
        myplottools.plot_comparison_hist_mode(
            context, dmh_title, df_act_dist, df_syn_dist,
            bins = np.linspace(0,25,120), dpi = 300, cols = 3, rows = 2
        )
        myplottools.plot_comparison_cdf_mode(
            context, dmc_title, df_act_dist, df_syn_dist,
            dpi = 300, cols = 3, rows = 2
        )
        print(f"SUCCESS: Created HTS vs CARLA mode plots: {dmh_title}, {dmc_title}")
    
    # 2. ONLY if comparison enabled: Generate three-way comparison (HTS vs CARLA vs Hoerl)
    if df_syn_dist_alt is not None:
        print(f"INFO Algorithm comparison mode: generating three-way plots (HTS vs CARLA vs {alt_label})")
        
        # Three-way comparison for purpose distances
        dph_title_3way = dph_title.replace(".png", "_comparison.png")
        dpc_title_3way = dpc_title.replace(".png", "_comparison.png")
        
        myplottools.plot_threeway_hist_purpose(
            context, dph_title_3way, df_act_dist, df_syn_dist, df_syn_dist_alt,
            bins = np.linspace(0,25,120), dpi = 300, cols = 3, rows = 2,
            label_syn1="CARLA", label_syn2=alt_label
        )
        myplottools.plot_threeway_cdf_purpose(
            context, dpc_title_3way, df_act_dist, df_syn_dist, df_syn_dist_alt,
            dpi = 300, cols = 3, rows = 2,
            label_syn1="CARLA", label_syn2=alt_label
        )
        
        print(f"SUCCESS: Created three-way comparison plots: {dph_title_3way}, {dpc_title_3way}")
        
        # Three-way comparison for mode distances if available
        if "mode" in df_syn_dist.columns and "mode" in df_act_dist.columns and "mode" in df_syn_dist_alt.columns:
            print(f"INFO generating three-way distance plots by mode (HTS vs CARLA vs {alt_label})")
            dmh_title_3way = dmh_title.replace(".png", "_comparison.png")
            dmc_title_3way = dmc_title.replace(".png", "_comparison.png")
            
            myplottools.plot_threeway_hist_mode(
                context, dmh_title_3way, df_act_dist, df_syn_dist, df_syn_dist_alt,
                bins = np.linspace(0,25,120), dpi = 300, cols = 3, rows = 2,
                label_syn1="CARLA", label_syn2=alt_label
            )
            myplottools.plot_threeway_cdf_mode(
                context, dmc_title_3way, df_act_dist, df_syn_dist, df_syn_dist_alt,
                dpi = 300, cols = 3, rows = 2,
                label_syn1="CARLA", label_syn2=alt_label
            )
            
            print(f"SUCCESS: Created three-way mode comparison plots: {dmh_title_3way}, {dmc_title_3way}")
    else:
        print("INFO skipping mode distance plots - mode data not available in both datasets")


def generate_plots(context, df_aux_act, df_aux_syn, df_act_trips, df_syn_trips, df_act_persons, df_syn_persons, df_syn_no_trip, df_act_no_trip, suffix, df_census, df_syn_trips_alt=None):
    """
    Generate all comparison plots.
    
    Args:
        df_syn_trips_alt: Optional alternative synthetic trips (for algorithm comparison)
    """
    # Handle case where HTS data is not available
    hts_available = df_act_trips is not None and df_act_persons is not None
    
    syn_CC = df_aux_syn.groupby("chain").size().reset_index(name='count')
    
    if hts_available and len(df_aux_act) > 0:
        act_CC = df_aux_act.groupby("chain")["weight_person"].sum().reset_index(name='count')
    else:
        # Create empty HTS data for compatibility
        act_CC = pd.DataFrame(columns=["chain", "weight_person"])
        act_CC = act_CC.groupby("chain")["weight_person"].sum().reset_index(name='count')

    act_CC.columns = ["Chain", "actual Count"]
    syn_CC.columns = ["Chain", "synthetic Count"]

     # 1. ACTIVITY CHAINS
    
    # Creating the new dataframes with activity chain counts
    #syn_CC = myutils.process_synthetic_activity_chain_counts(df_syn)
    syn_CC.loc[len(syn_CC) + 1] = pd.Series({"Chain": "home", "synthetic Count": df_syn_no_trip.shape[0] })
   
    #act_CC = myutils.process_actual_activity_chain_counts(df_act, df_aux)
    if hts_available and df_act_no_trip is not None:
        act_no_trip_weight = np.sum(df_act_no_trip["weight_person"].values.tolist())
    else:
        act_no_trip_weight = 0.0
    act_CC.loc[len(act_CC) + 1] = pd.Series({"Chain": "home", "actual Count": act_no_trip_weight})

    # Merging together, comparing
    all_CC = pd.merge(syn_CC, act_CC, on = "Chain", how = "outer")
    activity_chains_comparison(context, all_CC, suffix = suffix)
    
    # Number of activities    
    activity_counts_comparison(context, all_CC, suffix = suffix)
    
    # Number of activities per purposes
    activity_counts_per_purpose(context, all_CC, suffix = suffix)
    
    # Demographics comparison (include no-trip persons)
    demographics_comparison(context, df_act_persons, df_syn_persons, df_census, suffix)
    # New consolidated horizontal summary plot
    summary_horizontal(context, df_act_persons, df_syn_persons, df_census, suffix)


    # 2. CROWFLY DISTANCES
    print("INFO starting crowfly distance analysis...")
    
    try:
        # 2.1. Compute the distances
        print("INFO computing crowfly distances for synthetic data")
        df_syn_dist = compute_distances_synthetic(df_syn_trips.copy())
        
        # Compute HTS distances if data is available
        if hts_available:
            print("INFO computing crowfly distances for HTS data")
            df_act_dist = compute_distances_actual(df_act_trips.reset_index().copy())
            print(f"INFO distances computed - Synthetic: {df_syn_dist.shape}, HTS: {df_act_dist.shape}")
        else:
            print("INFO HTS data not available - synthetic distances only")
            df_act_dist = None
            print(f"INFO distances computed - Synthetic: {df_syn_dist.shape}, HTS: None")
        
        # 2.2 Prepare for plotting: weighted mean distances by purpose
        print("INFO preparing distance data for plotting")
        
        if hts_available and df_act_dist is not None:
            # For HTS: weight by person_weight
            df_act_dist_with_weight = df_act_dist.copy()
            if "weight_person" not in df_act_dist_with_weight.columns:
                # Merge person weights if not already present
                df_act_dist_with_weight = df_act_dist_with_weight.merge(
                    df_act_persons[["person_id", "weight_person"]].drop_duplicates("person_id"),
                    on="person_id", how="left"
                )
            df_act_dist_with_weight["weighted_distance"] = df_act_dist_with_weight["weight_person"] * df_act_dist_with_weight["crowfly_distance"]

            # Calculate weighted mean distances by purpose for HTS
            act_weighted_sum = df_act_dist_with_weight.groupby("following_purpose")["weighted_distance"].sum()
            act_weight_sum = df_act_dist_with_weight.groupby("following_purpose")["weight_person"].sum()
            act_mean_distances = act_weighted_sum / act_weight_sum
        else:
            df_act_dist_with_weight = None
            act_mean_distances = pd.Series(dtype=float)
        
        # Calculate unweighted mean distances by purpose for Synthetic  
        syn_mean_distances = df_syn_dist.groupby("following_purpose")["crowfly_distance"].mean()
        
        # Align purposes between datasets
        all_purposes = list(set(act_mean_distances.index.tolist() + syn_mean_distances.index.tolist()))
        act_aligned = act_mean_distances.reindex(all_purposes).fillna(0)
        syn_aligned = syn_mean_distances.reindex(all_purposes).fillna(0)
        
        # 2.4 Generate detailed distance histograms and CDFs by purpose
        print("INFO creating detailed distance distribution plots")
        
        # Prepare alternative algorithm data if comparison mode is enabled
        df_syn_dist_alt = None
        if df_syn_trips_alt is not None:
            print("INFO preparing alternative algorithm distance data for comparison")
            df_syn_dist_alt = compute_distances_synthetic(df_syn_trips_alt.copy())
        
        # 2.4 Create CDF plots (synthetic vs HTS comparison)
        print("INFO creating CDF plots")
        cdf_title = "distance_purpose_cdf"
        if suffix:
            cdf_title += "_" + suffix
        cdf_title += ".png"
        
        # Prepare HTS data for plotting if available
        df_hts_for_plotting = None
        if df_act_dist is not None and df_act_dist_with_weight is not None and len(df_act_dist) > 0:
            # Ensure column compatibility for plotting functions
            df_hts_for_plotting = df_act_dist_with_weight.copy()
            if "purpose" not in df_hts_for_plotting.columns and "following_purpose" in df_hts_for_plotting.columns:
                df_hts_for_plotting["purpose"] = df_hts_for_plotting["following_purpose"]
            print(f"INFO HTS data available for comparison: {len(df_hts_for_plotting)} trips")
        else:
            print("INFO HTS data not available, will show synthetic-only plots")
        
        # Create the main CDF comparison plot in both 2x3 and 3x2 layouts
        try:
            # Generate 2x3 layout (original)
            myplottools.plot_comparison_cdf_purpose(context, cdf_title, df_hts_for_plotting, df_syn_dist, dpi=300)
            print(f"SUCCESS: Created {cdf_title}")
            
            # Generate 3x2 layout (alternative) 
            # cdf_title_3x2 = cdf_title.replace(".png", "_3x2.png")
            # myplottools.plot_comparison_cdf_purpose(context, cdf_title_3x2, df_hts_for_plotting, df_syn_dist, dpi=300, cols=2, rows=3)
            # print(f"SUCCESS: Created {cdf_title_3x2}")
        except Exception as e:
            print(f"ERROR creating CDF plot: {e}")
            import traceback
            traceback.print_exc()
        
        # Generate additional detailed plots via all_the_plot_distances
        try:
            if df_hts_for_plotting is not None:
                all_the_plot_distances(context, df_hts_for_plotting, df_syn_dist, suffix, 
                                     df_syn_dist_alt=df_syn_dist_alt, alt_label="Hoerl")
                print("SUCCESS: Created additional comparison distance plots")
            else:
                print("INFO: Skipping additional plots - no HTS data available")
        except Exception as e:
            print(f"WARNING: Could not create additional distance plots: {e}")

        print("SUCCESS: Crowfly distance analysis completed")
        
    except Exception as e:
        print(f"ERROR in crowfly distance analysis: {e}")
        import traceback
        traceback.print_exc()



def execute(context):
    COMPARE_LOCATION_ALGORITHMS = True 
    HOERL_OUTPUT_PATH = "output/hoerl"   

    
    pop_all = None
    suff_all = ""
    pop_selectors = [pop_all]
    suffixes      = [suff_all]

    # Check if algorithm comparison mode is enabled
    df_syn_trips_alt = None
    
    if COMPARE_LOCATION_ALGORITHMS:
        print("=" * 80)
        print("ALGORITHM COMPARISON MODE ENABLED")
        print("=" * 80)
        print(f"Loading alternative algorithm output from: {HOERL_OUTPUT_PATH}")
        
        try:
            # Load alternative algorithm synthetic data (Hoerl)
            df_syn_persons_alt, df_syn_trips_alt, df_syn_no_trip_alt = import_data_synthetic(
                context, None, custom_output_path=HOERL_OUTPUT_PATH
            )
            print(f"SUCCESS: Loaded alternative algorithm data")
            print(f"  - Persons: {len(df_syn_persons_alt)}")
            print(f"  - Trips: {len(df_syn_trips_alt)}")
        except Exception as e:
            print(f"ERROR: Could not load alternative algorithm data: {e}")
            print("Proceeding without algorithm comparison")
            df_syn_trips_alt = None
        print("=" * 80)

    for population_selector, suffix in list(zip(pop_selectors, suffixes)):
        df_syn_persons, df_syn_trips, df_syn_no_trip = import_data_synthetic(context, population_selector)
        hts_result = import_data_actual(context, population_selector)
        
        # Handle case where HTS data is not available
        if hts_result[0] is None:
            print("INFO: Proceeding with synthetic-only analysis (no HTS data)")
            df_act_persons, df_act_trips, df_act_no_trip = None, None, None
            # Create empty DataFrames for compatibility
            df_aux_act = pd.DataFrame(columns=["person_id", "weight_person", "chain"])
        else:
            df_act_persons, df_act_trips, df_act_no_trip = hts_result
            df_aux_act, df_aux_syn = aux_data_frame(df_act_trips, df_syn_trips, df_act_persons, df_syn_persons)
        
        df_census = import_data_census(context, population_selector)
        
        # Create synthetic auxiliary data (always available)
        pers_ids = df_syn_trips["person_id"].unique()
        df_aux_syn = pd.DataFrame({
            "person_id": pers_ids,
            "weights": 1,
            "chain": "home-" + df_syn_trips.groupby("person_id")["following_purpose"].apply(lambda x: "-".join(x))
        })

        generate_plots(context, df_aux_act, df_aux_syn, df_act_trips, df_syn_trips, df_act_persons, df_syn_persons, df_syn_no_trip, df_act_no_trip, suffix, df_census, df_syn_trips_alt=df_syn_trips_alt)
