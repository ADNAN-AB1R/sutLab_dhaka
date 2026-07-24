import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


def configure(context):
    context.stage("synthesis.population.enriched")
    context.stage("seville.data.census.population")
    context.stage("seville.ipu.attributed")
    context.config("analysis_path")
    context.stage("seville.data.hts.entd.filtered")
    context.stage("seville.data.census.households")


def plot_population(context):
    df_census = context.stage("seville.data.census.population").copy()
    df_ipu = context.stage("seville.ipu.attributed").copy()
    _, df_hts, _ = context.stage("seville.data.hts.entd.filtered")

    # Attach matching information
    assert df_ipu['person_id'].is_unique

    print("len(df_ipu)", len(df_ipu))


    # ----- Define 5-year bins -----
    def to_5y(age):
        return (age // 5) * 5

    # ----- Census -----
    df_census = (
        df_census.groupby("age_class", as_index=False)["weight"]
        .sum()
    )
    df_census["weight"] = df_census["weight"] / df_census["weight"].sum() * 100

    df_ipu["age_class"] = to_5y(df_ipu["age"])
    df_hts["age_class"] = to_5y(df_hts["age"])

    print("IPU class", df_ipu.groupby("age_class").size())
    print("IPU age", df_ipu.groupby("age").size())

    print("MATCHED", df_hts.groupby("age_class").size())


    # ----- IPU synthetic -----
    df_ipu["weight"] = 1.0
    df_ipu = (
        df_ipu.groupby("age_class", as_index=False)["weight"]
        .sum()
    )
    df_ipu["weight"] = df_ipu["weight"] / df_ipu["weight"].sum() * 100

    # ----- HTS output -----

    df_hts["weight"] = df_hts["person_weight"]
    df_hts = (
        df_hts.groupby("age_class", as_index=False)["weight"]
        .sum()
    )
    df_hts["weight"] = df_hts["weight"] / df_hts["weight"].sum() * 100






    # ----- Align bins -----
    bins = sorted(
        set(df_census["age_class"])
        | set(df_ipu["age_class"])
        | set(df_hts["age_class"])
    )

    def align(df):
        return df.set_index("age_class").reindex(bins, fill_value=0)["weight"]

    census_w = align(df_census)
    ipu_w = align(df_ipu)
    output_w = align(df_hts)

    # ----- Plot (side-by-side bars, no transparency) -----
    x = np.arange(len(bins))
    w = 0.25

    plt.figure(figsize=(11, 6))
    plt.bar(x - w, census_w, width=w, label="Census")
    plt.bar(x,     ipu_w,    width=w, label="IPU synthetic")
    plt.bar(x + w, output_w, width=w, label="HTS output")

    plt.xticks(x, bins, rotation=45)
    plt.xlabel("Age class (5-year bins)")
    plt.ylabel("Population share (%)")
    plt.title("Age distribution comparison")
    plt.legend()
    plt.grid(axis="y", linestyle="--", linewidth=0.5)

    plt.tight_layout()
    plt.savefig(
        f"{context.config('analysis_path')}/census_vs_synthesis_ages.png"
    )
    plt.close()


def plot_household(context):
    df_census = context.stage("seville.data.census.households").copy()
    df_ipu = context.stage("seville.ipu.attributed").copy()
    df_hts, _, _ = context.stage("seville.data.hts.entd.filtered")

    # --------------------------------------------------
    # Census: already aggregated
    # --------------------------------------------------
    census_map = {
        1: "households_1_person",
        2: "households_2_persons",
        3: "households_3_persons",
        4: "households_4_persons",
        5: "households_5plus_persons",
    }

    census_dist = (
        df_census[list(census_map.values())]
        .sum()
        .rename({v: k for k, v in census_map.items()})
    )

    census_dist = census_dist / census_dist.sum() * 100

    # --------------------------------------------------
    # IPU: derive household size from persons
    # --------------------------------------------------
    ipu_sizes = df_ipu.groupby("household_id").size()

    # cap at 5
    ipu_sizes = ipu_sizes.clip(upper=5)

    ipu_dist = ipu_sizes.value_counts().sort_index()
    ipu_dist = ipu_dist / ipu_dist.sum() * 100


    # --------------------------------------------------
    # HTS: household_size column already exists
    # --------------------------------------------------
    # cap at 5 instead of dropping
    df_hts['household_size'] = df_hts['household_size'].clip(upper=5)

    hts_dist = df_hts.groupby("household_size")['household_weight'].sum().sort_index()
    hts_dist = hts_dist / hts_dist.sum() * 100

    # PRINT ALL
    print("="*10)
    print("hts_dist", hts_dist)
    print("="*10)
    print("census_dist", census_dist)
    print("="*10)
    print("ipu_dist", ipu_dist)
    print("="*10)

    # --------------------------------------------------
    # Align bins
    # --------------------------------------------------
    bins = [1, 2, 3, 4, 5]

    census_w = census_dist.reindex(bins, fill_value=0)
    ipu_w = ipu_dist.reindex(bins, fill_value=0)
    hts_w = hts_dist.reindex(bins, fill_value=0)

    labels = ["1", "2", "3", "4", "5+"]

    # --------------------------------------------------
    # Plot
    # --------------------------------------------------
    x = np.arange(len(bins))
    w = 0.25

    plt.figure(figsize=(11, 6))

    plt.bar(x - w, census_w, width=w, label="Census")
    plt.bar(x,     ipu_w,    width=w, label="IPU synthetic")
    plt.bar(x + w, hts_w,    width=w, label="HTS")

    plt.xticks(x, labels)
    plt.xlabel("Household size (persons)")
    plt.ylabel("Household share (%)")
    plt.title("Household size distribution comparison")
    plt.legend()
    plt.grid(axis="y", linestyle="--", linewidth=0.5)

    plt.tight_layout()
    plt.savefig(
        f"{context.config('analysis_path')}/census_vs_synthesis_households.png"
    )
    plt.close()


def execute(context):
    plot_population(context)
    plot_household(context)
