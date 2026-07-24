import pandas as pd
import numpy as np
import analysis_dhaka.dhaka.ivt_style.myplottools as myplottools
import matplotlib.pyplot as plt
import warnings

"""
Dhaka variant of the Seville ivt_style analysis stage (see
analysis_dhaka/seville/ivt_style/analysis.py for the original). Ported to
Dhaka's data contracts:

- No ward-level census age x sex table exists (see dhaka/ipu/prepare.py) -
  the census comparison series is instead built from the same age_sex /
  household_size targets dhaka.ipu.prepare hands to IPU itself (real
  DNCC+DSCC ward-level census, blended with an HTS-derived Savar/Keraniganj
  fallback), so this comparison is exactly consistent with what IPU actually
  targeted rather than a separately-derived figure.
- No Seville-style census employment/licenses stages exist for Dhaka (the
  BBS census has no ward-level breakdown for either) - those comparisons are
  dropped; employment/license/PT-subscription comparisons stay HTS vs
  Synthetic only, as they already are for the generic (non-census) plots.
- No alternative-algorithm (Hoerl) comparison output exists for Dhaka -
  COMPARE_LOCATION_ALGORITHMS is disabled.
- HTS person weights are read from `person_weight` (already the case in
  Seville's own code, so no rename needed) and distances come from
  `euclidean_distance` (meters) on both synthetic and HTS trips - Dhaka's
  HTS trips carry real lat/lon-derived distances directly, so there's no
  separate `routed_distance` column to fall back to.
- `sex` is already the string category "male"/"female" on both HTS and
  synthetic persons (not the 0/1/2 numeric Seville encoding).
"""

warnings.filterwarnings("ignore", message=r".*edges kwarg.*node_link_data.*", category=FutureWarning)


def configure(context):
    context.config("output_path")
    context.config("data_path")
    context.config("analysis_path")
    context.config("output_prefix")

    context.stage("synthesis.output")
    context.stage("dhaka.ipu.prepare")

    context.stage("data.hts.entd.reweighted")


def import_data_synthetic(context, population_selector=None):
    output_path = context.config("output_path")
    output_prefix = context.config("output_prefix")

    filepath = "%s/%strips.csv" % (output_path, output_prefix)
    df_trips = pd.read_csv(filepath, encoding="latin1", sep=";")

    filepath = "%s/%spersons.csv" % (output_path, output_prefix)
    df_persons = pd.read_csv(filepath, encoding="latin1", sep=";")

    df_persons["is_active"] = df_persons["person_id"].isin(df_trips["person_id"]).astype(bool)

    t_id = df_trips["person_id"].values.tolist()
    df_persons_no_trip = df_persons[np.logical_not(df_persons["person_id"].isin(t_id))]
    df_persons_no_trip = df_persons_no_trip.set_index(["person_id"])
    print(df_persons_no_trip.shape, "persons without trip in synpop")

    if population_selector:
        if "age_selector" in population_selector.keys():
            age_min = population_selector["age_selector"][0]
            age_max = population_selector["age_selector"][1]
            df_persons = df_persons[(df_persons["age"] <= age_max) & (df_persons["age"] >= age_min)]
            df_trips = df_trips[(df_trips["age"] <= age_max) & (df_trips["age"] >= age_min)]
            df_persons_no_trip = df_persons_no_trip[(df_persons_no_trip["age"] <= age_max) & (df_persons_no_trip["age"] >= age_min)]
            print("INFO excluding agents NOT between the age of ", age_min, " and ", age_max)
        if "gender_selector" in population_selector.keys():
            gender = population_selector["gender_selector"]
            df_persons = df_persons[df_persons["sex"] == gender]
            df_trips = df_trips[df_trips["sex"] == gender]
            df_persons_no_trip = df_persons_no_trip[df_persons_no_trip["sex"] == gender]
            print("INFO only considering ", gender, " agents.")

    return df_persons, df_trips, df_persons_no_trip


def import_data_actual(context, population_selector=None):
    try:
        hts_data = context.stage("data.hts.entd.reweighted")
        if hts_data is None or any(x is None for x in hts_data):
            print("WARNING: HTS data is not available - returning None")
            return None, None, None

        df_act_households, df_act_persons, df_act_trips = hts_data
    except Exception as e:
        print(f"WARNING: Could not load HTS data: {e}")
        return None, None, None

    df_act_households["number_of_vehicles"] = pd.to_numeric(df_act_households["number_of_vehicles"], errors="coerce").fillna(0)
    df_act_households["car_availability"] = df_act_households["number_of_vehicles"] > 0
    df_act_persons = df_act_persons.merge(
        df_act_households[["household_id", "car_availability"]],
        on="household_id",
        how="left"
    )
    df_act_persons["car_availability"] = df_act_persons["car_availability"].fillna(False)

    df_act_persons.rename(columns={"person_weight": "weight_person"}, inplace=True)
    cols = [c for c in ["person_id", "weight_person", "employed", "studies",
                         "age", "sex", "car_availability", "has_license", "has_pt_subscription",
                         "socioprofessional_class"] if c in df_act_persons.columns]
    df_px = df_act_persons[cols]
    df_act = df_act_trips.merge(df_px, on=["person_id"], how='left')

    df_act["preceding_purpose"] = df_act["preceding_purpose"].astype(str)
    df_act["following_purpose"] = df_act["following_purpose"].astype(str)
    df_act["od"] = df_act["preceding_purpose"] + "_" + df_act["following_purpose"]

    df_act = df_act[~df_act["weight_person"].isna()]
    df_act = df_act.set_index(["person_id"])
    df_act.sort_index(inplace=True)

    t_id = df_act_trips["person_id"].values.tolist()
    df_act_persons["is_active"] = df_act_persons["person_id"].isin(t_id).astype(bool)
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
            df_act = df_act[df_act["sex"] == gender]
            df_persons_no_trip = df_persons_no_trip[df_persons_no_trip["sex"] == gender]
            print("INFO only considering ", gender, " agents.")

    return df_act_persons, df_act_trips, df_persons_no_trip


def import_data_census(context):
    """
    Builds a long-format (age_class, sex, weight) frame from the exact same
    age_sex targets dhaka.ipu.prepare computed for IPU itself (real
    DNCC+DSCC ward-level census + HTS-derived Savar/Keraniganj fallback -
    see dhaka/ipu/prepare.py). Also returns the household_size targets dict
    for the household-size comparison plot.
    """
    targets_by_area = context.stage("dhaka.ipu.prepare")
    # Single aggregation area (IPU_aggregation_level="departement_id", one zone) for now.
    targets = next(iter(targets_by_area.values()))

    age_sex = targets["age_sex"]
    df_census = pd.DataFrame([
        {"sex": sex, "age_class": age, "weight": count}
        for (sex, age), count in age_sex.items()
    ])

    return df_census, targets["household_size"]


def activity_chains_comparison(context, all_CC, suffix=None):
    synthetic_sum = all_CC["synthetic Count"].sum()
    if synthetic_sum != 0:
        all_CC["synthetic Count"] = all_CC["synthetic Count"] / synthetic_sum * 100
    else:
        all_CC["synthetic Count"] = 0
    all_CC["actual Count"] = all_CC["actual Count"] / all_CC["actual Count"].sum() * 100
    all_CC = all_CC.sort_values(by=['actual Count'], ascending=False)
    all_CC.to_csv("%s/actchains_DF.csv" % context.config("analysis_path"), index=False)

    title_plot = "Synthetic and HTS activity chain comparison"
    title_figure = "activitychains"
    if suffix:
        title_plot += " - " + suffix
        title_figure += "_" + suffix

    title_figure += ".png"
    myplottools.plot_comparison_bar(context, imtitle=title_figure, plottitle=title_plot, ylabel="Percentage", xlabel="Activity chain", lab=all_CC["Chain"], actual=all_CC["actual Count"], synthetic=all_CC["synthetic Count"], t=15, figsize=[12, 7], dpi=300, w=0.35, xticksrot=True)


def activity_counts_comparison(context, all_CC, suffix=None):
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

    counts = pd.DataFrame(columns=["number", "synthetic Count", "actual Count"])
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

    counts["synthetic Count"] = counts["synthetic Count"] / counts["synthetic Count"].sum() * 100
    counts["actual Count"] = counts["actual Count"] / counts["actual Count"].sum() * 100

    title_plot = "Synthetic and HTS activity counts comparison"
    title_figure = "activitycounts"
    if suffix:
        title_plot += " - " + suffix
        title_figure += "_" + suffix

    title_figure += ".png"

    myplottools.plot_comparison_bar(context, imtitle=title_figure, plottitle=title_plot,
                                     ylabel="Percentage", xlabel="Number of activities in the activity chain",
                                     lab=counts["number"], actual=counts["actual Count"],
                                     synthetic=counts["synthetic Count"], xticksrot=True)


def activity_counts_per_purpose(context, all_CC, suffix=None):
    all_CC_dic = all_CC.to_dict('records')
    purposes = list(myplottools.PURPOSE_ORDER) + ["start_out_of_home"]
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

    counts = pd.DataFrame(columns=["number", "synthetic Count", "actual Count"])

    for k, v in counts_dic.items():
        counts.loc[k] = pd.Series({"number": k,
                                    "synthetic Count": v[0],
                                    "actual Count": v[1]
                                    })

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

    title_plot = "Activity counts per purpose comparison"
    title_figure = "activitycountspurpose"
    if suffix:
        title_plot += " - " + suffix
        title_figure += "_" + suffix

    title_figure += ".png"

    myplottools.plot_comparison_bar(context, imtitle=title_figure, plottitle=title_plot,
                                     ylabel="Percentage", xlabel="Activities with the same purpose in the activity chain",
                                     lab=counts["number"], actual=counts["actual Count"],
                                     synthetic=counts["synthetic Count"], t=20, xticksrot=True)


def demographics_comparison(context, df_act_persons, df_syn_persons, df_census, suffix=None, use_active_only=False):
    bins = [x for x in range(0, 110, 5)]
    labels = [f"{x}-{x+4}" for x in bins[:-1]]

    cols_act = [c for c in ["person_id", "age", "weight_person", "is_active", "has_license", "has_pt_subscription", "employed"] if c in df_act_persons.columns]
    cols_syn = [c for c in ["person_id", "age", "is_active", "has_driving_license", "has_pt_subscription", "employed"] if c in df_syn_persons.columns]
    df_act_persons = df_act_persons[cols_act].drop_duplicates(subset=["person_id"]).copy()
    df_syn_persons = df_syn_persons[cols_syn].drop_duplicates(subset=["person_id"]).copy()
    df_cen = df_census.copy()

    if use_active_only:
        if "is_active" in df_act_persons.columns:
            df_act_persons = df_act_persons[df_act_persons["is_active"]]
        if "is_active" in df_syn_persons.columns:
            df_syn_persons = df_syn_persons[df_syn_persons["is_active"]]

    df_act_persons['age_bin'] = pd.cut(df_act_persons["age"], bins=bins, labels=labels, right=False)
    df_syn_persons['age_bin'] = pd.cut(df_syn_persons["age"], bins=bins, labels=labels, right=False)
    df_cen['age_bin'] = pd.cut(df_cen["age_class"], bins=bins, labels=labels, right=False)

    act_counts = myplottools.compute_counts(df_act_persons['age_bin'], weights=df_act_persons['weight_person'], categories=labels)
    syn_counts = myplottools.compute_counts(df_syn_persons['age_bin'], weights=None, categories=labels)
    census_counts = myplottools.compute_counts(df_cen['age_bin'], weights=df_cen['weight'], categories=labels)

    act_counts = pd.Series(act_counts).reindex(labels).fillna(0)
    syn_counts = pd.Series(syn_counts).reindex(labels).fillna(0)
    census_counts = pd.Series(census_counts).reindex(labels).fillna(0)

    if df_census is None:
        print("[DEBUG] Census is None in demographics_comparison; plotting will be HTS vs SYN only.")

    if df_census is not None:
        diff_hts = syn_counts - act_counts
        diff_census = syn_counts - census_counts

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

    title_figure = "agedistribution"
    title_plot = "Age distribution comparison "
    if suffix:
        title_plot += " - " + suffix
        title_figure += "_" + suffix
    title_figure += ".png"
    if df_census is None:
        myplottools.plot_comparison_bar(
            context, imtitle=title_figure, plottitle=title_plot,
            ylabel="Percentage", xlabel="Age groups", lab=labels,
            hts=act_counts.values, synthetic=syn_counts.values, xticksrot=True
        )
    else:
        myplottools.plot_comparison_bar(
            context, imtitle=title_figure, plottitle=title_plot,
            ylabel="Percentage", xlabel="Age groups", lab=labels,
            hts=act_counts.values, synthetic=syn_counts.values, census=census_counts.values, xticksrot=True
        )

        title_figure_syn_hts = "agedistribution_syn_hts"
        title_plot_syn_hts = "Age distribution comparison (Synthetic vs HTS) "
        if suffix:
            title_plot_syn_hts += " - " + suffix
            title_figure_syn_hts += "_" + suffix
        title_figure_syn_hts += ".png"
        myplottools.plot_comparison_bar(
            context, imtitle=title_figure_syn_hts, plottitle=title_plot_syn_hts,
            ylabel="Percentage", xlabel="Age groups", lab=labels,
            hts=act_counts.values, synthetic=syn_counts.values, xticksrot=True
        )

        title_figure_syn_census = "agedistribution_syn_census"
        title_plot_syn_census = "Age distribution comparison (Synthetic vs Census) "
        if suffix:
            title_plot_syn_census += " - " + suffix
            title_figure_syn_census += "_" + suffix
        title_figure_syn_census += ".png"
        myplottools.plot_comparison_bar(
            context, imtitle=title_figure_syn_census, plottitle=title_plot_syn_census,
            ylabel="Percentage", xlabel="Age groups", lab=labels,
            synthetic=syn_counts.values, census=census_counts.values, xticksrot=True
        )

    def employment_status(df):
        return df["employed"].replace({False: "unemployed", True: "employed"})

    df_act_persons_local = df_act_persons.copy()
    df_act_persons_local["employment_status"] = employment_status(df_act_persons_local)
    act_counts = myplottools.compute_counts(df_act_persons_local["employment_status"], weights=df_act_persons_local["weight_person"], categories=["unemployed", "employed"])

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
        context, imtitle=title_figure, plottitle=title_plot,
        ylabel="Percentage", xlabel="Employment status", lab=act_counts.index,
        hts=act_counts.values, synthetic=syn_counts.values, xticksrot=True
    )

    act_license_labels = myplottools.map_bool_to_labels(df_act_persons.get("has_license"), yes_label="Yes", no_label="No")
    act_counts = myplottools.compute_counts(act_license_labels, weights=df_act_persons.get("weight_person"), categories=["No", "Yes"])

    syn_license_labels = myplottools.map_bool_to_labels(df_syn_persons.get("has_driving_license"), yes_label="Yes", no_label="No")
    syn_counts = myplottools.compute_counts(syn_license_labels, weights=None, categories=act_counts.index.tolist())
    syn_counts = myplottools.align_series(act_counts, syn_counts)

    title_figure = "drivinglicense"
    title_plot = "Driving license comparison "
    if suffix:
        title_plot += " - " + suffix
        title_figure += "_" + suffix
    title_figure += ".png"

    myplottools.plot_comparison_bar(
        context, imtitle=title_figure, plottitle=title_plot,
        ylabel="Percentage", xlabel="Has driving license", lab=act_counts.index,
        hts=act_counts.values, synthetic=syn_counts.values, xticksrot=True
    )

    # No PT subscription data in the DTCA HTS (dhaka/data/hts/entd/cleaned.py
    # sets has_pt_subscription = NaN for everyone) - skip that comparison.


def summary_horizontal(context, df_act_persons, df_syn_persons, df_census, suffix=None, use_active_only=False):
    """
    Single horizontal summary plot:
    - syn vs census: age, sex
    - syn vs hts: employment, driving license

    All metrics expressed as percentages of persons (HTS weighted; Synthetic
    unweighted; Census from the IPU targets - see import_data_census).
    """
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
        syn_age = pd.cut(df_syn_persons["age"], bins=age_bins, labels=age_labels, right=False)
        syn_age_pct = myplottools.compute_counts(syn_age, categories=age_labels)
    else:
        syn_age_pct = pd.Series([float("nan")] * len(age_labels), index=age_labels)

    census_counts = pd.Series([float("nan")] * len(age_labels), index=age_labels)
    if df_census is not None and {"age_class", "weight"}.issubset(df_census.columns):
        df_cen = df_census.copy()
        df_cen['age_bin'] = pd.cut(df_cen["age_class"], bins=age_bins, labels=age_labels, right=False)
        census_counts = myplottools.compute_counts(df_cen['age_bin'], weights=df_cen['weight'], categories=age_labels)
        census_counts = pd.Series(census_counts).reindex(age_labels).fillna(0)

    labels_all.extend([f"Age {l}" for l in age_labels])
    syn_vals.extend(list(syn_age_pct.reindex(age_labels).values))
    hts_vals.extend([float("nan")] * len(age_labels))
    cen_vals.extend(list(census_counts.values))

    # ---------- Sex (syn vs census) ----------
    sex_labels = ["Female", "Male"]
    syn_sex_series = df_syn_persons.get("sex")
    if syn_sex_series is not None:
        syn_sex_lab = syn_sex_series.astype(str).str.capitalize()
        syn_sex_pct = myplottools.compute_counts(syn_sex_lab, categories=sex_labels)
    else:
        syn_sex_pct = pd.Series([float("nan")] * 2, index=sex_labels)

    cen_sex_pct = pd.Series([float("nan")] * 2, index=sex_labels)
    if df_census is not None and {"sex", "weight"}.issubset(df_census.columns):
        cen_sex_lab = df_census["sex"].astype(str).str.capitalize()
        tmp = pd.DataFrame({"label": cen_sex_lab, "w": df_census["weight"]})
        cen_raw = tmp.groupby("label")["w"].sum()
        cen_sex_pct = (cen_raw / cen_raw.sum() * 100.0).reindex(sex_labels).fillna(0)

    labels_all.extend(sex_labels)
    syn_vals.extend(list(syn_sex_pct.reindex(sex_labels).values))
    hts_vals.extend([float("nan")] * len(sex_labels))
    cen_vals.extend(list(cen_sex_pct.reindex(sex_labels).values))

    # ---------- Employment (syn vs HTS - no census employment breakdown for Dhaka) ----------
    emp_labels = ["Unemployed", "Employed"]
    syn_emp_series = df_syn_persons.get("employed")
    if syn_emp_series is not None:
        syn_emp_lab = syn_emp_series.replace({False: "Unemployed", True: "Employed"})
        syn_emp_pct = myplottools.compute_counts(syn_emp_lab, categories=emp_labels)
    else:
        syn_emp_pct = pd.Series([float("nan")] * 2, index=emp_labels)

    act_emp_series = df_act_persons.get("employed")
    act_emp_lab = pd.Series(act_emp_series).replace({False: "Unemployed", True: "Employed"}) if act_emp_series is not None else None
    _wcol0 = "weight_person" if "weight_person" in df_act_persons.columns else ("person_weight" if "person_weight" in df_act_persons.columns else None)
    _wser0 = df_act_persons[_wcol0] if _wcol0 is not None else None
    act_emp_pct = myplottools.compute_counts(act_emp_lab, weights=_wser0, categories=emp_labels) if act_emp_lab is not None else pd.Series([float("nan")] * 2, index=emp_labels)

    labels_all.extend(emp_labels)
    syn_vals.extend(list(syn_emp_pct.reindex(emp_labels).values))
    hts_vals.extend(list(act_emp_pct.reindex(emp_labels).values))
    cen_vals.extend([float("nan")] * len(emp_labels))

    # ---------- Driving license (syn vs HTS) ----------
    lic_labels = ["Driving license No", "Driving license Yes"]
    syn_lic_series = df_syn_persons.get("has_driving_license")
    if syn_lic_series is None and "has_license" in df_syn_persons.columns:
        syn_lic_series = df_syn_persons["has_license"]
    syn_lic_lab = pd.Series(syn_lic_series).replace({False: "Driving license No", True: "Driving license Yes"}) if syn_lic_series is not None else None
    syn_lic_pct = myplottools.compute_counts(syn_lic_lab, categories=lic_labels) if syn_lic_lab is not None else pd.Series([float("nan")] * 2, index=lic_labels)

    act_lic_series = df_act_persons.get("has_license")
    act_lic_lab = pd.Series(act_lic_series).replace({False: "Driving license No", True: "Driving license Yes"}) if act_lic_series is not None else None
    _wcol = "weight_person" if "weight_person" in df_act_persons.columns else ("person_weight" if "person_weight" in df_act_persons.columns else None)
    _wser = df_act_persons[_wcol] if _wcol is not None else None
    act_lic_pct = myplottools.compute_counts(act_lic_lab, weights=_wser, categories=lic_labels) if act_lic_lab is not None else pd.Series([float("nan")] * 2, index=lic_labels)

    labels_all.extend(lic_labels)
    syn_vals.extend(list(syn_lic_pct.reindex(lic_labels).values))
    hts_vals.extend(list(act_lic_pct.reindex(lic_labels).values))
    cen_vals.extend([float("nan")] * len(lic_labels))

    imtitle = "summary_horizontal"
    plottitle = "Dhaka sociodemographic summary"
    if suffix:
        imtitle += f"_{suffix}"
        plottitle += f" - {suffix}"
    imtitle += ".png"

    myplottools.plot_horizontal_comparison(
        context, imtitle=imtitle, plottitle=plottitle,
        xlabel="Percentage of population (%)", labels=labels_all,
        synthetic=syn_vals, hts=hts_vals, census=cen_vals,
        lablist=["Synthetic", "HTS", "Census"],
        figsize=[10, max(8, int(len(labels_all) * 0.35))], dpi=300, bar_height=0.7
    )


def compute_distances_synthetic(df_syn, threshold=25):
    if "euclidean_distance" in df_syn.columns:
        df_syn["crowfly_distance"] = 0.001 * np.array(df_syn["euclidean_distance"])
    else:
        print("WARNING: No distance column found in synthetic data")
        return df_syn

    df_syn_dist = df_syn[df_syn["crowfly_distance"] < threshold]
    df_syn_dist = df_syn_dist[df_syn_dist["crowfly_distance"] > 0]
    return df_syn_dist


def compute_distances_actual(df_act, threshold=25):
    # Dhaka's HTS trips already carry a real lat/lon-derived euclidean_distance
    # (meters) - see dhaka/data/hts/entd/cleaned.py - unlike Seville's routed_distance.
    df_act["crowfly_distance"] = df_act["euclidean_distance"] / 1000.0

    df_act_dist = df_act[df_act["crowfly_distance"] < threshold]
    df_act_dist = df_act_dist[df_act_dist["crowfly_distance"] > 0]
    return df_act_dist


def generate_plots(context, df_aux_act, df_aux_syn, df_act_trips, df_syn_trips, df_act_persons, df_syn_persons, df_syn_no_trip, df_act_no_trip, suffix, df_census):
    hts_available = df_act_trips is not None and df_act_persons is not None

    syn_CC = df_aux_syn.groupby("chain").size().reset_index(name='count')

    if hts_available and len(df_aux_act) > 0:
        act_CC = df_aux_act.groupby("chain")["weight_person"].sum().reset_index(name='count')
    else:
        act_CC = pd.DataFrame(columns=["chain", "weight_person"])
        act_CC = act_CC.groupby("chain")["weight_person"].sum().reset_index(name='count')

    act_CC.columns = ["Chain", "actual Count"]
    syn_CC.columns = ["Chain", "synthetic Count"]

    syn_CC.loc[len(syn_CC) + 1] = pd.Series({"Chain": "home", "synthetic Count": df_syn_no_trip.shape[0]})

    if hts_available and df_act_no_trip is not None:
        act_no_trip_weight = np.sum(df_act_no_trip["weight_person"].values.tolist())
    else:
        act_no_trip_weight = 0.0
    act_CC.loc[len(act_CC) + 1] = pd.Series({"Chain": "home", "actual Count": act_no_trip_weight})

    all_CC = pd.merge(syn_CC, act_CC, on="Chain", how="outer")
    activity_chains_comparison(context, all_CC, suffix=suffix)
    activity_counts_comparison(context, all_CC, suffix=suffix)
    activity_counts_per_purpose(context, all_CC, suffix=suffix)

    demographics_comparison(context, df_act_persons, df_syn_persons, df_census, suffix)
    summary_horizontal(context, df_act_persons, df_syn_persons, df_census, suffix)

    print("INFO starting crowfly distance analysis...")
    try:
        print("INFO computing crowfly distances for synthetic data")
        df_syn_dist = compute_distances_synthetic(df_syn_trips.copy())

        if hts_available:
            print("INFO computing crowfly distances for HTS data")
            df_act_dist = compute_distances_actual(df_act_trips.reset_index().copy())
            print(f"INFO distances computed - Synthetic: {df_syn_dist.shape}, HTS: {df_act_dist.shape}")
        else:
            print("INFO HTS data not available - synthetic distances only")
            df_act_dist = None

        if hts_available and df_act_dist is not None:
            df_act_dist_with_weight = df_act_dist.copy()
            if "weight_person" not in df_act_dist_with_weight.columns:
                df_act_dist_with_weight = df_act_dist_with_weight.merge(
                    df_act_persons[["person_id", "weight_person"]].drop_duplicates("person_id"),
                    on="person_id", how="left"
                )
        else:
            df_act_dist_with_weight = None

        cdf_title = "distance_purpose_cdf"
        if suffix:
            cdf_title += "_" + suffix
        cdf_title += ".png"

        df_hts_for_plotting = None
        if df_act_dist is not None and df_act_dist_with_weight is not None and len(df_act_dist) > 0:
            df_hts_for_plotting = df_act_dist_with_weight.copy()
            if "purpose" not in df_hts_for_plotting.columns and "following_purpose" in df_hts_for_plotting.columns:
                df_hts_for_plotting["purpose"] = df_hts_for_plotting["following_purpose"]
            print(f"INFO HTS data available for comparison: {len(df_hts_for_plotting)} trips")
        else:
            print("INFO HTS data not available, will show synthetic-only plots")

        try:
            myplottools.plot_comparison_cdf_purpose(context, cdf_title, df_hts_for_plotting, df_syn_dist, dpi=300)
            print(f"SUCCESS: Created {cdf_title}")
        except Exception as e:
            print(f"ERROR creating CDF plot: {e}")
            import traceback
            traceback.print_exc()

        if df_hts_for_plotting is not None:
            dph_title = "distance_purpose_hist"
            dmh_title = "distance_mode_hist"
            dmc_title = "distance_mode_cdf"
            if suffix:
                dph_title += "_" + suffix
                dmh_title += "_" + suffix
                dmc_title += "_" + suffix
            dph_title += ".png"
            dmh_title += ".png"
            dmc_title += ".png"

            myplottools.plot_comparison_hist_purpose(context, dph_title, df_hts_for_plotting, df_syn_dist, bins=np.linspace(0, 25, 120), dpi=300, cols=3, rows=2)
            print(f"SUCCESS: Created {dph_title}")

            if "mode" in df_syn_dist.columns and "mode" in df_hts_for_plotting.columns:
                myplottools.plot_comparison_hist_mode(context, dmh_title, df_hts_for_plotting, df_syn_dist, bins=np.linspace(0, 25, 120), dpi=300, cols=3, rows=2)
                myplottools.plot_comparison_cdf_mode(context, dmc_title, df_hts_for_plotting, df_syn_dist, dpi=300, cols=3, rows=2)
                print(f"SUCCESS: Created {dmh_title}, {dmc_title}")
        else:
            print("INFO: Skipping additional distance plots - no HTS data available")

        print("SUCCESS: Crowfly distance analysis completed")
    except Exception as e:
        print(f"ERROR in crowfly distance analysis: {e}")
        import traceback
        traceback.print_exc()


def execute(context):
    pop_all = None
    suff_all = ""
    pop_selectors = [pop_all]
    suffixes = [suff_all]

    for population_selector, suffix in list(zip(pop_selectors, suffixes)):
        df_syn_persons, df_syn_trips, df_syn_no_trip = import_data_synthetic(context, population_selector)
        hts_result = import_data_actual(context, population_selector)

        if hts_result[0] is None:
            print("INFO: Proceeding with synthetic-only analysis (no HTS data)")
            df_act_persons, df_act_trips, df_act_no_trip = None, None, None
            df_aux_act = pd.DataFrame(columns=["person_id", "weight_person", "chain"])
        else:
            df_act_persons, df_act_trips, df_act_no_trip = hts_result
            df_act_reset = df_act_trips.reset_index()
            if 'weight_person' not in df_act_reset.columns and 'weight_person' in df_act_persons.columns:
                df_act_reset = df_act_reset.merge(
                    df_act_persons[['person_id', 'weight_person']], on='person_id', how='left'
                )
            pers_ids = df_act_reset["person_id"].unique()
            df_aux_act = pd.DataFrame({
                "person_id": pers_ids,
                "weight_person": df_act_reset.groupby("person_id")["weight_person"].mean(),
                "chain": "home-" + df_act_reset.groupby("person_id")["following_purpose"].apply(lambda x: "-".join(x))
            }).fillna({"weight_person": 1.0})

        df_census, _household_size_targets = import_data_census(context)

        pers_ids = df_syn_trips["person_id"].unique()
        df_aux_syn = pd.DataFrame({
            "person_id": pers_ids,
            "weights": 1,
            "chain": "home-" + df_syn_trips.groupby("person_id")["following_purpose"].apply(lambda x: "-".join(x))
        })

        generate_plots(context, df_aux_act, df_aux_syn, df_act_trips, df_syn_trips, df_act_persons, df_syn_persons, df_syn_no_trip, df_act_no_trip, suffix, df_census)
