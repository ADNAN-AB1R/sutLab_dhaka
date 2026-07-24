import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

"""
Dhaka variant of analysis_dhaka/seville/ivt_style/compare_ipu.py: compares
the raw IPU/TRS output (before location assignment / enrichment) against the
HTS sample and the census-derived control totals it was raked against.

Seville's census inputs (seville.data.census.population/households, both
row-level weighted tables) don't exist for Dhaka. Instead this reuses the
age_sex / household_size targets computed once in dhaka/ipu/prepare.py -
the same numbers IPU itself targeted (real DNCC+DSCC ward-level census +
HTS-derived Savar/Keraniganj fallback - see that module's docstring), so
these plots show a "did IPU converge to what it was asked to hit" check
rather than a separately re-derived comparison.
"""


def configure(context):
    context.stage("dhaka.ipu.attributed")
    context.stage("dhaka.ipu.prepare")
    context.config("analysis_path")
    context.stage("dhaka.data.hts.entd.filtered")


def plot_population(context):
    targets_by_area = context.stage("dhaka.ipu.prepare")
    targets = next(iter(targets_by_area.values()))
    age_sex_targets = targets["age_sex"]

    df_ipu = context.stage("dhaka.ipu.attributed").copy()
    _, df_hts, _ = context.stage("dhaka.data.hts.entd.filtered")

    assert df_ipu['person_id'].is_unique

    print("len(df_ipu)", len(df_ipu))

    def to_5y(age):
        return (age // 5) * 5

    # ----- Census (from IPU's own targets) -----
    census_age = {}
    for (sex, age_class), count in age_sex_targets.items():
        census_age[age_class] = census_age.get(age_class, 0) + count
    df_census = pd.Series(census_age).rename_axis("age_class").reset_index(name="weight")
    df_census["weight"] = df_census["weight"] / df_census["weight"].sum() * 100

    df_ipu["age_class"] = to_5y(df_ipu["age"])
    df_hts["age_class"] = to_5y(df_hts["age"])

    print("IPU class", df_ipu.groupby("age_class").size())
    print("HTS class", df_hts.groupby("age_class").size())

    # ----- IPU synthetic -----
    df_ipu["weight"] = 1.0
    df_ipu_agg = df_ipu.groupby("age_class", as_index=False)["weight"].sum()
    df_ipu_agg["weight"] = df_ipu_agg["weight"] / df_ipu_agg["weight"].sum() * 100

    # ----- HTS -----
    df_hts["weight"] = df_hts["person_weight"]
    df_hts_agg = df_hts.groupby("age_class", as_index=False)["weight"].sum()
    df_hts_agg["weight"] = df_hts_agg["weight"] / df_hts_agg["weight"].sum() * 100

    bins = sorted(
        set(df_census["age_class"]) | set(df_ipu_agg["age_class"]) | set(df_hts_agg["age_class"])
    )

    def align(df):
        return df.set_index("age_class").reindex(bins, fill_value=0)["weight"]

    census_w = align(df_census)
    ipu_w = align(df_ipu_agg)
    hts_w = align(df_hts_agg)

    x = np.arange(len(bins))
    w = 0.25

    plt.figure(figsize=(11, 6))
    plt.bar(x - w, census_w, width=w, label="Census (IPU target)")
    plt.bar(x, ipu_w, width=w, label="IPU synthetic")
    plt.bar(x + w, hts_w, width=w, label="HTS")

    plt.xticks(x, bins, rotation=45)
    plt.xlabel("Age class (5-year bins)")
    plt.ylabel("Population share (%)")
    plt.title("Age distribution comparison (post-IPU/TRS, pre-enrichment)")
    plt.legend()
    plt.grid(axis="y", linestyle="--", linewidth=0.5)

    plt.tight_layout()
    plt.savefig(f"{context.config('analysis_path')}/census_vs_synthesis_ages.png")
    plt.close()


def plot_household(context):
    targets_by_area = context.stage("dhaka.ipu.prepare")
    targets = next(iter(targets_by_area.values()))
    household_size_targets = targets["household_size"]

    df_ipu = context.stage("dhaka.ipu.attributed").copy()
    df_hts, _, _ = context.stage("dhaka.data.hts.entd.filtered")

    census_dist = pd.Series(household_size_targets)
    census_dist = census_dist / census_dist.sum() * 100

    ipu_sizes = df_ipu.groupby("household_id").size().clip(upper=5)
    ipu_dist = ipu_sizes.value_counts().sort_index()
    ipu_dist = ipu_dist / ipu_dist.sum() * 100

    df_hts['household_size'] = df_hts['household_size'].clip(upper=5)
    hts_dist = df_hts.groupby("household_size")['household_weight'].sum().sort_index()
    hts_dist = hts_dist / hts_dist.sum() * 100

    print("=" * 10)
    print("hts_dist", hts_dist)
    print("=" * 10)
    print("census_dist (IPU target)", census_dist)
    print("=" * 10)
    print("ipu_dist", ipu_dist)
    print("=" * 10)

    bins = [1, 2, 3, 4, 5]
    census_w = census_dist.reindex(bins, fill_value=0)
    ipu_w = ipu_dist.reindex(bins, fill_value=0)
    hts_w = hts_dist.reindex(bins, fill_value=0)
    labels = ["1", "2", "3", "4", "5+"]

    x = np.arange(len(bins))
    w = 0.25

    plt.figure(figsize=(11, 6))
    plt.bar(x - w, census_w, width=w, label="Census (IPU target)")
    plt.bar(x, ipu_w, width=w, label="IPU synthetic")
    plt.bar(x + w, hts_w, width=w, label="HTS")

    plt.xticks(x, labels)
    plt.xlabel("Household size (persons)")
    plt.ylabel("Household share (%)")
    plt.title("Household size distribution comparison (post-IPU/TRS, pre-enrichment)")
    plt.legend()
    plt.grid(axis="y", linestyle="--", linewidth=0.5)

    plt.tight_layout()
    plt.savefig(f"{context.config('analysis_path')}/census_vs_synthesis_households.png")
    plt.close()


def execute(context):
    plot_population(context)
    plot_household(context)
