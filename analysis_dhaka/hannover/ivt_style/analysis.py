import pandas as pd
import numpy as np
import geopandas as gpd
import analysis_dhaka.hannover.ivt_style.myutils as myutils
import analysis_dhaka.hannover.ivt_style.myplottools as myplottools
import matplotlib.pyplot as plt
#import data.constants as c
import pyproj
#import data.utils
import data.spatial.utils
from tqdm import tqdm
import data.hts.entd.cleaned


def configure(context):
    context.config("output_path")
    context.config("data_path")
    context.config("analysis_path")
    context.config("output_prefix")

    # context.stage("data.hts.trips")
    # context.stage("data.hts.persons")
    # context.stage("synthesis.output")
    context.stage("hannover.data.census.population")
    context.stage("data.hts.entd.reweighted")

    context.config("weekend_scenario", False)
    context.config("specific_weekend_scenario", "all") # options are "all", "saturday", "sunday"
    context.config("specific_day_scenario", "avgworkday") #options can be any of the days of the week or "avgworkday"
    
    
def import_data_synthetic(context, population_selector = None):
    filepath = "%s/%strips.csv" % (context.config("output_path"), context.config("output_prefix"))
    df_trips = pd.read_csv(filepath, encoding = "latin1", sep = ";")

    filepath = "%s/%spersons.csv" %  (context.config("output_path"), context.config("output_prefix"))
    df_persons = pd.read_csv(filepath, encoding = "latin1", sep = ";")

    filepath = "%s/%shouseholds.csv" %  (context.config("output_path"), context.config("output_prefix"))
    df_hhl = pd.read_csv(filepath, encoding = "latin1", sep = ";")


    df_syn = df_persons.merge(df_hhl, left_on="person_id", right_on="household_id")
    df_syn = df_persons.merge(df_trips, left_on="person_id", right_on="person_id")
    
    t_id = df_trips["person_id"].values.tolist()
    df_persons_no_trip = df_persons[np.logical_not(df_persons["person_id"].isin(t_id))]
    df_persons_no_trip = df_persons_no_trip.set_index(["person_id"])
    df_persons_no_trip = df_persons_no_trip[df_persons_no_trip["age"] >= 6]
    df_syn = df_syn[df_syn["age"]>=6]
    print(df_persons_no_trip.shape, "persons without trip in synpop")

    if population_selector:
        if "age_selector" in population_selector.keys():
            age_min = population_selector["age_selector"][0]
            age_max = population_selector["age_selector"][1]
            df_syn = df_syn[(df_syn["age"] <= age_max) & (df_syn["age"] >= age_min)]
            df_persons_no_trip = df_persons_no_trip[(df_persons_no_trip["age"] <= age_max) & (df_persons_no_trip["age"] >= age_min)]
            print("INFO excluding agents NOT between the age of ", age_min, " and ", age_max)
        if "gender_selector" in population_selector.keys():
            gender = population_selector["gender_selector"]
            if gender == "male":
                g = 0
            else:
                g = 1
            df_syn = df_syn[df_syn["sex"] == g]
            df_persons_no_trip = df_persons_no_trip[df_persons_no_trip["sex"] == g]
            print("INFO only considering ", gender, " agents.")
        if "canton_selector" in population_selector.keys():
            cantons = population_selector["canton_selector"]
            df_syn = df_syn[df_syn["canton_id"].isin(cantons)]
            df_persons_no_trip = df_persons_no_trip[df_persons_no_trip["canton_id"].isin(cantons)]
            print("INFO only considering agents living in cantons n° ", cantons)

    # df_syn contains everyone even those without trips (Milos feb '24)
    return df_syn, df_persons_no_trip   


def import_data_actual(context, population_selector = None):
    df_act_households , df_act_persons, df_act_trips = context.stage("data.hts.entd.reweighted")
    
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

    # df_act_persons = context.stage("data.microcensus.persons")

    # # TODO: for sociodemographics we should actually use all persons
    # # including those filtered in trips (?) Milos feb '24

    # df_act_trips = context.stage("data.microcensus.trips")[0]
    # Merging with person information, correcting trips with erroneous purpose

    df_act_persons.rename(columns = {"person_weight": "weight_person"}, inplace = True)
    df_px = df_act_persons[["person_id", "weight_person", "employed", "studies",
                                                "age", "sex", "car_availability", "has_license", "has_pt_subscription", "socioprofessional_class"]]
    df_act = df_act_trips.merge(df_px, on=["person_id"], how='left')

    # TODO: do we need this and why? Milos Feb '24
    # df_act.loc[(df_act["purpose"]=='work') & (df_act["age"] < 16), "purpose"] = "other"

    #separate trip purposes into O-D
    # df_act["following_purpose"] = df_act["purpose"]
    # df_act["preceding_purpose"] = df_act["following_purpose"].shift(1)
    # df_act.loc[df_act["trip_id"] == 1, "preceding_purpose"] = "home"
    df_act["preceding_purpose"] = df_act["preceding_purpose"].astype(str)
    df_act["following_purpose"] = df_act["following_purpose"].astype(str)
    df_act["od"] = df_act["preceding_purpose"] + "_" + df_act["following_purpose"]

    # Only keep the persons that could have been used in activity chain matching, due to 
    # using specifc days of the week
    df_act = df_act[~df_act["weight_person"].isna()]
    df_act = df_act.set_index(["person_id"])
    df_act.sort_index(inplace=True)

    t_id = df_act_trips["person_id"].values.tolist()
    df_persons_no_trip = df_act_persons[np.logical_not(df_act_persons["person_id"].isin(t_id))]
    df_persons_no_trip = df_persons_no_trip.set_index(["person_id"])
    print(df_persons_no_trip.shape, "persons without trip in hts")

    if population_selector:
        if "age_selector" in population_selector.keys():
            age_min = population_selector["age_selector"][0]
            age_max = population_selector["age_selector"][1]
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
    return df_act, df_persons_no_trip


    
def import_data_census(context, population_selector = None):
    df_population = context.stage("hannover.data.census.population")
    return df_population
    

def aux_data_frame(df_act, df_syn, population_selector = None):
    if population_selector:
        if "age_selector" in population_selector.keys():
            age_min = population_selector["age_selector"][0]
            age_max = population_selector["age_selector"][1]
            df_act = df_act[(df_act["age"] <= age_max) & (df_act["age"] >= age_min)]
            df_syn = df_syn[(df_syn["age"] <= age_max) & (df_syn["age"] >= age_min)]
            print("INFO excluding agents NOT between the age of ", age_min, " and ", age_max)
        if "gender_selector" in population_selector.keys():
            gender = population_selector["gender_selector"]
            df_act = df_act[df_act["sex"] == gender]
            df_syn = df_syn[df_syn["sex"] == gender]
            print("INFO only considering ", gender, " agents.")

    df_act["person_id"] = df_act.index
    pers_ids = df_act["person_id"].unique()
    df_act = df_act.reset_index(drop=True)

    df_aux_act = pd.DataFrame({
        "person_id": pers_ids,
        "weight_person": df_act.groupby("person_id")["weight_person"].mean(),
        "chain": "home-" + df_act.groupby("person_id")["following_purpose"].apply(lambda x: "-".join(x))
    })

    pers_ids = df_syn["person_id"].unique()

    df_aux_syn = pd.DataFrame({
        "person_id": pers_ids,
        "weights": 1,
        "chain": "home-" + df_syn.groupby("person_id")["following_purpose"].apply(lambda x: "-".join(x))
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
                if cpt_purpose > 0 :
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
    

def demographics_comparison(context, df_act, df_syn, df_census, suffix=None):

    def weight_counts(df, column, weight_column='weight_person'):
        """
        Compute weighted counts for a given column in the dataframe.
        """
        counts = df.groupby(column)[weight_column].sum()
        counts = counts / counts.sum() * 100
        return counts
    
    # Age distribution comparison
    bins = [0, 6, 15, 18, 24, 30, 45, 65, 80, 150]
    labels = ["0-5", "6-14", "15-17", "18-23", "24-29", "30-44", "45-64", "65-79", "80+"]
    df_act['age_bin'] = pd.cut(df_act["age"], bins=bins, labels=labels)
    act_counts = df_act.groupby('age_bin')['weight_person'].sum()
    act_counts = act_counts / act_counts.sum() * 100

    syn_age = pd.cut(df_syn["age"], bins=bins, labels=labels)
    syn_counts = syn_age.value_counts(sort=False, normalize=True) * 100
    
    # Census data processing
    if df_census is not None:
        age_class_to_label = dict(zip(bins[:-1], labels))
        df_census["age_label"] = df_census["age_class"].map(age_class_to_label)
        census_counts_raw = df_census.groupby("age_label")["weight"].sum()
        census_counts = (census_counts_raw / census_counts_raw.sum()) * 100
        # Reindex to ensure order matches labels
        census_counts = census_counts.reindex(labels).fillna(0)

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
            actual=act_counts.values,
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
            actual=act_counts.values,
            synthetic=syn_counts.values,
            census=census_counts.values,
            xticksrot=True
        )

    # Employment status comparison
    def employment_status(df):
        return df["employed"].replace({ False: "unemployed", True: "employed"})
    df_act["employment_status"] = employment_status(df_act)
    #act_counts = act_employment.value_counts(normalize=True) * 100
    act_counts = weight_counts(df_act, "employment_status")
    df_act.drop(columns=["employment_status"], inplace=True)

    syn_employment = employment_status(df_syn)
    syn_counts = syn_employment.value_counts(normalize=True) * 100
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
        actual=act_counts.values,
        synthetic=syn_counts.values,
        xticksrot=True
    )
    
    # Driving license
    def has_driving_license(df):
        return df["has_license"].replace({False: "No", True: "Yes"})
    def _has_driving_license(df):
        return df["has_driving_license"].replace({False: "No", True: "Yes"})
    
    df_act["act_license"] = has_driving_license(df_act)
    act_counts = weight_counts(df_act, "act_license")
    #act_counts = act_license.value_counts(normalize=True) * 100
    df_act.drop(columns=["act_license"], inplace=True)

    syn_license = _has_driving_license(df_syn)
    syn_counts = syn_license.value_counts(normalize=True) * 100
    title_figure = "drivinglicense"
    title_plot = "Driving license comparison "
    if suffix:
        title_plot += " - " + suffix
        title_figure += "_" + suffix
    title_figure += ".png"
    myplottools.plot_comparison_bar(
        context,
        imtitle=title_figure,
        plottitle=title_plot,
        ylabel="Percentage",
        xlabel="Has driving license",
        lab=act_counts.index,
        actual=act_counts.values,
        synthetic=syn_counts.values,
        xticksrot=True
    )
    
    # Public transport subscription
    def has_pt_subscription(df):
        return df["has_pt_subscription"].replace({False: "No", True: "Yes"})
    
    df_act["act_pt"] = has_pt_subscription(df_act)
    act_counts = weight_counts(df_act, "act_pt")
    #act_counts = act_pt.value_counts(normalize=True) * 100
    df_act.drop(columns=["act_pt"], inplace=True)

    syn_pt = has_pt_subscription(df_syn)
    syn_counts = syn_pt.value_counts(normalize=True) * 100
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
        actual=act_counts.values,
        synthetic=syn_counts.values,
        xticksrot=True
    )

    
    


def compute_distances_synthetic(df_syn, threshold = 25):
    df_syn["crowfly_distance"] = 0.001 * np.array(df_syn["crowfly_distance"])

    # Only consider crowfly distances shorter than <threshold> km
    df_syn_dist = df_syn[df_syn["crowfly_distance"] < threshold]
    df_syn_dist = df_syn_dist[df_syn_dist["crowfly_distance"] > 0]
    return df_syn_dist


def compute_distances_actual(df_act, threshold = 25):
    # Compute the distances
    df_act["crowfly_distance"] = 0.001 * np.sqrt(
        (df_act["origin_x"] - df_act["destination_x"])**2 + 
        (df_act["origin_y"] - df_act["destination_y"])**2
    )
    
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
        for _, row in df_pers.iterrows():
             dist = row["crowfly_distance"]
        dic_syn["dist_home_educ"][i] = dist
            
    for i in range(len(pers_educ_act)):
        pid = pers_educ_act[i]
       
        df_pers = df_act_educ[df_act_educ["person_id"] == pid]
        home_x = None
        educ_y = None
        for index, row in df_pers.iterrows():
            if row["origin_purpose"] != target_purpose:
                home_x = row["origin_x"]
                home_y = row["origin_y"]
            elif row["following_purpose"] != target_purpose:
                home_x = row["destination_x"]
                home_y = row["destination_y"]
            if row["origin_purpose"] == target_purpose:
                educ_x = row["origin_x"]
                educ_y = row["origin_y"]
            elif row["following_purpose"] == target_purpose:
                educ_x = row["destination_x"]
                educ_y = row["destination_y"]
            if educ_y is not None and home_y is not None:
                break
        dic_act["dist_home_educ"][i] = 0.001 * np.sqrt(((home_x - educ_x) ** 2 + (home_y - educ_y) ** 2))
        dic_act["weight_person"][i] = row["weight_person"]

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
    if len(x_cdf) >= 1:
        x_cdf /= x_cdf[-1]

    y_data = np.array(act, dtype=np.float64)
    y_sorted = np.argsort(y_data)
    y_weights = np.array(act_w, dtype=np.float64)
    y_cdf = np.cumsum(y_weights[y_sorted])
    if len(y_cdf) >= 1:
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
    plt.savefig("%s/" % context.config("analysis_path") + imtitle)
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



def all_the_plot_distances(context, df_act_dist, df_syn_dist, suffix = None):
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
    dph_title += ".png"
    dpc_title += ".png"
    dmc_title += ".png"
    
    myplottools.plot_comparison_hist_purpose(context, dph_title, df_act_dist, df_syn_dist, bins = np.linspace(0,25,120), dpi = 300, cols = 3, rows = 2)

    myplottools.plot_comparison_cdf_purpose(context, dpc_title, df_act_dist, df_syn_dist, dpi = 300, cols = 3, rows = 2)


def generate_plots(context, df_aux_act, df_aux_syn, df_act, df_syn, df_syn_no_trip, df_act_no_trip, suffix, df_census):
    syn_CC = df_aux_syn.groupby("chain").size().reset_index(name='count')
    act_CC = df_aux_act.groupby("chain")["weight_person"].sum().reset_index(name='count')

    act_CC.columns = ["Chain", "actual Count"]
    syn_CC.columns = ["Chain", "synthetic Count"]

     # 1. ACTIVITY CHAINS
    
    # Creating the new dataframes with activity chain counts
    #syn_CC = myutils.process_synthetic_activity_chain_counts(df_syn)
    syn_CC.loc[len(syn_CC) + 1] = pd.Series({"Chain": "home", "synthetic Count": df_syn_no_trip.shape[0] })
   
    #act_CC = myutils.process_actual_activity_chain_counts(df_act, df_aux)
    act_CC.loc[len(act_CC) + 1] = pd.Series({"Chain": "home", "actual Count": np.sum(df_act_no_trip["weight_person"].values.tolist())})

    # Merging together, comparing
    all_CC = pd.merge(syn_CC, act_CC, on = "Chain", how = "outer")
    activity_chains_comparison(context, all_CC, suffix = suffix)
    
    # Number of activities    
    activity_counts_comparison(context, all_CC, suffix = suffix)
    
    # Number of activities per purposes
    activity_counts_per_purpose(context, all_CC, suffix = suffix)
    
    # Demographics comparison
    demographics_comparison(context, df_act, df_syn, df_census, suffix)
    

    # 2. CROWFLY DISTANCES
    
    # 2.1. Compute the distances
    # df_syn_dist = compute_distances_synthetic(df_syn)
    # df_act_dist = compute_distances_actual(df_act) 
    
    # 2.2 Prepare for plotting
    # df_act_dist["x"] = df_act_dist["weight_person"] * df_act_dist["crowfly_distance"]

    # act = df_act_dist.groupby(["purpose"]).sum()["x"] / df_act_dist.groupby(["purpose"]).sum()["weight_person"]
    # syn = df_syn_dist.groupby(["following_purpose"]).mean()["crowfly_distance"] 

    # act_purposes = list(set(act.reset_index()["purpose"]))
    # syn = syn.reset_index()
    # for p in act_purposes:
    #     if p not in list(set(syn["following_purpose"])):
    #         syn.loc[len(syn)] = [p, 0]

    # syn = syn.groupby(["following_purpose"]).mean()["crowfly_distance"] 

    # # 2.3 Ready to plot!
    # myplottools.plot_comparison_bar(context, imtitle = "distancepurpose.png", plottitle = "Crowfly distance " + suffix, ylabel = "Mean crowfly distance [km]", xlabel = "", lab = syn.index, actual = act, synthetic = syn, t = None, xticksrot = True )
    # all_the_plot_distances(context, df_act_dist, df_syn_dist, suffix)

    # # 2.4 Distance from home to education
    # for primary_purpose in ["work", "education"]:
    #     print("INFO computing distances between home and", primary_purpose)
    #     syn_0, act_0, act_w0 = compare_dist_from_home(context, df_syn, df_act,primary_purpose, suffix = suffix)


    
def execute(context):
    pop_all = None
    suff_all = ""
    pop_selectors = [pop_all]
    suffixes      = [suff_all]

    for population_selector, suffix in list(zip(pop_selectors, suffixes)):
        df_syn, df_syn_no_trip = import_data_synthetic(context, population_selector)
        df_act, df_act_no_trip = import_data_actual(context, population_selector)
        df_census = import_data_census(context, population_selector)
        df_aux_act, df_aux_syn = aux_data_frame(df_act, df_syn)

        generate_plots(context, df_aux_act, df_aux_syn, df_act, df_syn, df_syn_no_trip, df_act_no_trip, suffix, df_census)
