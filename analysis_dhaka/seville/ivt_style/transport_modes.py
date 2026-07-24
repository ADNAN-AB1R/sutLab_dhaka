import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

def configure(context):
    context.config("output_path")
    context.config("data_path")
    context.config("analysis_path")
    context.config("output_prefix")
    context.stage("synthesis.output")
    context.stage("data.hts.entd.reweighted")


def import_data_synthetic(context):
    output_path = context.config("output_path")
    output_prefix = context.config("output_prefix")
    
    filepath = "%s/%strips.csv" % (output_path, output_prefix)
    df_trips = pd.read_csv(filepath, encoding = "latin1", sep = ";")

    filepath = "%s/%spersons.csv" % (output_path, output_prefix)
    df_persons = pd.read_csv(filepath, encoding = "latin1", sep = ";")


    return df_persons, df_trips   


def import_data_actual(context):
    _, df_persons, df_act_trips = context.stage("data.hts.entd.reweighted")
    return df_persons, df_act_trips

def export_csvs(context, df_persons, df_trips, suffix):
    print(df_trips.info())

    df_trips = df_trips.copy()

    df_trips['weight'] = df_trips['trip_weight']
    df_trips_original = df_trips[df_trips['person_id'] >= 0].copy()
    df_trips_age = df_trips.copy()
    df_trips_age = df_trips_age.merge(df_persons[['person_id', 'age','trip_weight']], on='person_id', suffixes=['_trip', '_person'])
    assert len(df_trips_age[df_trips_age['trip_weight_trip'] == df_trips_age['trip_weight_person']])
    
    print(len(df_trips_age[df_trips_age['trip_weight_trip'] == df_trips_age['trip_weight_person']]))
    print(df_trips_age[['trip_weight_trip', 'trip_weight_person']].head())



    print(df_trips['weight'].value_counts().sort_index())
    print("TOTAL WEIGHT", df_trips['weight'].sum())
    print("TOTAL LENGTH", len(df_trips))

    print(df_trips.info())

    df_trips = (
        df_trips.groupby("mode", as_index=False)["weight"]
        .sum()
    )
    df_trips_original = (
        df_trips_original.groupby("mode", as_index=False)["weight"]
        .sum()
    )
    df_trips_age['age_group'] = (df_trips_age['age'] // 5) * 5

    df_trips_age_within_age = (
        df_trips_age.groupby("age_group")["mode"]
          .value_counts(normalize=True)
          .unstack(fill_value=0)
    )

    df_trips_age_within_mode = (
        df_trips_age.groupby("mode")["age_group"]
          .value_counts(normalize=True)
          .unstack(fill_value=0)
    )


    df_trips_age = (
        df_trips_age.groupby(['age_group', "mode"], as_index=False)["weight"]
        .sum()
    )

    df_trips["weight_pct"] = df_trips["weight"] / df_trips["weight"].sum() #* 100
    df_trips_original["weight_pct"] = df_trips_original["weight"] / df_trips_original["weight"].sum() #* 100
    df_trips_age["weight_pct"] = df_trips_age["weight"] / df_trips_age["weight"].sum() #* 100

    df_trips_age.loc["Total"] = df_trips_age.sum()


    df_trips.to_csv(f"{context.config('analysis_path')}/mode_shares{suffix}.csv")
    df_trips_original.to_csv(f"{context.config('analysis_path')}/mode_shares_original{suffix}.csv")
    df_trips_age_within_age.to_csv(f"{context.config('analysis_path')}/mode_shares_by_age_within_age{suffix}.csv")
    df_trips_age_within_mode.to_csv(f"{context.config('analysis_path')}/mode_shares_by_age_within_mode{suffix}.csv")
    df_trips_age.to_csv(f"{context.config('analysis_path')}/mode_shares_by_age{suffix}.csv")

    df_trips_age.to_csv(f"{context.config('analysis_path')}/randoM_check{suffix}.csv")

    


def execute(context):
    syn_persons, syn_trips = import_data_synthetic(context)
    hts_persons, hts_trips = import_data_actual(context)
    #hts_persons, hts_trips = import_data_synthetic(context)


    syn_trips['trip_weight'] = 1
    syn_persons['trip_weight'] = 1
    #export_csvs(context, syn_persons, syn_persons, "_synthetic")
    export_csvs(context, hts_persons, hts_trips, "_hts")
    
    if False:
        print("EXITED AFTER EXPORTING CSV FILES")
        exit(0)

        # ----- IPU synthetic -----
    syn_trips["weight"] = 1.0
    syn_trips = (
        syn_trips.groupby("mode", as_index=False)["weight"]
        .sum()
    )
    syn_trips["weight"] = syn_trips["weight"] / syn_trips["weight"].sum() * 100

    # ----- HTS output -----

    hts_trips['weight'] = hts_trips['trip_weight']
    hts_trips = (
        hts_trips.groupby("mode", as_index=False)["weight"]
        .sum()
    )
    hts_trips["weight"] = hts_trips["weight"] / hts_trips["weight"].sum() * 100


    # ----- Align bins -----
    bins = sorted(
        set(syn_trips["mode"])
        | set(hts_trips["mode"])
    )

    def align(df):
        return df.set_index("mode").reindex(bins, fill_value=0)["weight"]

    ipu_w = align(syn_trips)
    output_w = align(hts_trips)

    # ----- Plot (side-by-side bars, no transparency) -----
    x = np.arange(len(bins))
    w = 0.1

    plt.figure(figsize=(11, 6))
    plt.bar(x - w, ipu_w, width=w, label="IPU synthetic")
    plt.bar(x + w, output_w, width=w, label="HTS output")

    plt.xticks(x, bins, rotation=45)
    plt.xlabel("Modes of transportation")
    plt.ylabel("Trip share (%)")
    plt.title("Modes of transportation distribution comparison")
    plt.legend()
    plt.grid(axis="y", linestyle="--", linewidth=0.5)

    plt.tight_layout()
    plt.savefig(
        f"{context.config('analysis_path')}/mode_shares.png"
    )
    plt.close()


