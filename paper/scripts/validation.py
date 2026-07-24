"""
Validation analysis + charts for the Dhaka data-preparation paper: does the
synthetic population/schedule faithfully reproduce the source HTS/census
patterns, at the right scale? This is internal-validity validation, NOT
MATSim simulation calibration (explicitly out of scope for this paper).

Standalone script - run directly:
    python paper/scripts/validation.py
"""
import sys
import os

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

import numpy as np
import pandas as pd
import geopandas as gpd
import matplotlib.pyplot as plt
import matplotlib.ticker as tck
import palettable

import documentation.plotting as plotting

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "figures")
os.makedirs(OUT_DIR, exist_ok = True)

# Okabe-Ito colorblind-safe palette (Nature/Science-recommended standard for
# scientific publishing) - used in place of the pastel Set2 scheme for a more
# print-professional, journal-appropriate look.
COLOR_HTS_RAW = "#999999"        # neutral gray
COLOR_HTS_WEIGHTED = "#0072B2"   # blue
COLOR_SYNTHETIC = "#D55E00"      # vermillion
COLOR_CENSUS = "#009E73"         # bluish green
COLOR_MALE = "#0072B2"           # blue
COLOR_FEMALE = "#CC79A7"         # reddish purple

# ======================================================================
# Load synthetic population outputs
# ======================================================================

print("Loading synthetic population outputs ...")
syn_persons = pd.read_csv("output/dhaka_1pct_persons.csv", sep = ";")
syn_households = pd.read_csv("output/dhaka_1pct_households.csv", sep = ";")
syn_trips = pd.read_csv("output/dhaka_1pct_trips.csv", sep = ";")
syn_homes = gpd.read_file("output/dhaka_1pct_homes.gpkg")

syn_household_size = syn_persons.groupby("household_id").size().rename("household_size")
syn_households = syn_households.merge(syn_household_size, on = "household_id", how = "left")

# ======================================================================
# Recompute the real (weighted) HTS via the pipeline's own stage functions,
# so the comparison uses exactly the same cleaning/weighting logic as the
# synthesis itself, not a separate ad hoc read of the raw survey.
# ======================================================================

print("Recomputing HTS stages (raw -> cleaned -> filtered -> reweighted) ...")

CONFIG = {
    "data_path": os.path.join(REPO_ROOT, "raw_data", "Dhaka"),
    "dhaka.ward_shp": "spatial/ward_dhk_75.shp",
    "dhaka.ward_number_field": "Ward No.",
    "dhaka.ward_cc_field": "CC",
    "dhaka.ward_upazila_field": "ADM3_EN",
    "dhaka.hts": "hts/dtca_full.xlsx",
    "dhaka.census": "census/bangladesh_bbs_population-and-housing-census-dataset_2022_admin-02.xlsx",
    "dhaka.study_area_upazilas": [
        "Dhaka North City Corporation", "Dhaka South City Corporation",
        "Savar", "Keraniganj",
    ],
    "random_seed": 1234,
    "sampling_rate": 0.01,
}

class FakeContext:
    def __init__(self, config, stages):
        self._config = config
        self._stages = stages
    def config(self, key, default = None):
        return self._config.get(key, default)
    def stage(self, name):
        return self._stages[name]

stages = {}

import dhaka.data.spatial.raw as spatial_raw
stages["dhaka.data.spatial.raw"] = spatial_raw.execute(FakeContext(CONFIG, stages))

import dhaka.data.spatial.iris as spatial_iris
gdf_wards = stages["dhaka.data.spatial.iris"] = spatial_iris.execute(FakeContext(CONFIG, stages))

import dhaka.data.hts.entd.raw as hts_raw
stages["data.hts.entd.raw"] = hts_raw.execute(FakeContext(CONFIG, stages))

import dhaka.data.hts.entd.cleaned as hts_cleaned
hts_households, hts_persons, hts_trips = stages["dhaka.data.hts.entd.cleaned"] = hts_cleaned.execute(FakeContext(CONFIG, stages))
stages["data.hts.entd.cleaned"] = (hts_households, hts_persons, hts_trips)

import dhaka.data.hts.entd.filtered as hts_filtered
stages["dhaka.data.hts.entd.filtered"] = hts_filtered.execute(FakeContext(CONFIG, stages))
stages["data.hts.entd.filtered"] = stages["dhaka.data.hts.entd.filtered"]

import dhaka.data.hts.entd.reweighted as hts_reweighted
hts_households_w, hts_persons_w, hts_trips_w = hts_reweighted.execute(FakeContext(CONFIG, stages))

import dhaka.data.census.population as census_stage
census_result = census_stage.execute(FakeContext(CONFIG, stages))

# Ward-level census marginals extracted from the BBS Community Report PDF
# (Tables C-01/C-02, DNCC+DSCC only) - the IPU control-total source, see
# dhaka/ipu/prepare.py and dhaka/data/census/extract_community_report.py
census_c01 = pd.read_csv("raw_data/Dhaka/census/extracted/census_c01_wards.csv")
census_c02 = pd.read_csv("raw_data/Dhaka/census/extracted/census_c02_wards.csv")
census_c01["commune_id"] = (
    census_c01["city_corp"].map({"DSCC": "S", "DNCC": "N"})
    + census_c01["ward_num"].astype(str).str.zfill(2)
)

print(f"  HTS (weighted): {len(hts_households_w)} households, {len(hts_persons_w)} persons, {len(hts_trips_w)} trips")
print(f"  Synthetic: {len(syn_households)} households, {len(syn_persons)} persons, {len(syn_trips)} trips")

# ======================================================================
# 1. Census cross-check: ward-level census totals vs. HTS-implied totals,
# like-for-like on the DNCC+DSCC area (the census extraction's coverage)
# ======================================================================

print("\n=== 1. Census cross-check (DNCC+DSCC, like-for-like) ===")

CITY_CORP_ZONES = hts_households_w["commune_id"].astype(str).str.match(r"^[NS]\d")
hts_citycorp_households = hts_households_w[CITY_CORP_ZONES]
hts_citycorp_persons = hts_persons_w[hts_persons_w["household_id"].isin(hts_citycorp_households["household_id"])]

hts_population_estimate = hts_citycorp_persons["person_weight"].sum()
hts_household_estimate = hts_citycorp_households["household_weight"].sum()

census_population = census_c01["pop_total"].sum()
census_households = census_c01["hh_total"].sum()

print(f"BBS ward-level census population (DNCC+DSCC): {census_population:,.0f}")
print(f"HTS-implied population (DNCC+DSCC): {hts_population_estimate:,.0f}")
print(f"BBS ward-level census households (DNCC+DSCC): {census_households:,.0f}")
print(f"HTS-implied households (DNCC+DSCC): {hts_household_estimate:,.0f}")

plotting.setup()

fig, axes = plt.subplots(1, 2, figsize = (6.0, 2.6))

labels = ["BBS census\n(ward-level, DNCC+DSCC)", "HTS-implied\n(DNCC+DSCC)"]

axes[0].bar(labels, [census_population / 1e6, hts_population_estimate / 1e6],
            color = [COLOR_CENSUS, COLOR_HTS_WEIGHTED], width = 0.55, edgecolor = "white", linewidth = 0.5)
axes[0].set_ylabel("Population [$10^6$]")
axes[0].set_title("Population", fontsize = 7.5)
axes[0].grid(axis = "y")
axes[0].set_axisbelow(True)

axes[1].bar(labels, [census_households / 1e6, hts_household_estimate / 1e6],
            color = [COLOR_CENSUS, COLOR_HTS_WEIGHTED], width = 0.55, edgecolor = "white", linewidth = 0.5)
axes[1].set_ylabel("Households [$10^6$]")
axes[1].set_title("Households", fontsize = 7.5)
axes[1].grid(axis = "y")
axes[1].set_axisbelow(True)

for ax in axes:
    ax.tick_params(axis = "x", labelsize = 6.2)

plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, "validation_census_crosscheck.pdf"))
plt.savefig(os.path.join(OUT_DIR, "validation_census_crosscheck.png"), dpi = 300)
plt.close()
print("Wrote validation_census_crosscheck.pdf / .png")

# ======================================================================
# 2. Mode share: raw sample vs. population-weighted HTS estimate
# ======================================================================

print("\n=== 2. Mode share (raw vs. weighted HTS) ===")

mode_order = ["walk", "rickshaw", "pt", "car", "paratransit", "bike", "other"]
mode_labels = {
    "walk": "Walk", "rickshaw": "Rickshaw", "pt": "Public\ntransport",
    "car": "Car", "paratransit": "Paratransit\n(CNG/auto)", "bike": "Bicycle", "other": "Other",
}

raw_share = hts_trips_w["mode"].value_counts(normalize = True).reindex(mode_order).fillna(0)
weighted_share = hts_trips_w.groupby("mode", observed = True)["trip_weight"].sum()
weighted_share = (weighted_share / weighted_share.sum()).reindex(mode_order).fillna(0)

print(pd.DataFrame({"raw_share": raw_share, "weighted_share": weighted_share}))

fig, ax = plt.subplots(figsize = (6.0, 2.6))
x = np.arange(len(mode_order))
width = 0.38

ax.bar(x - width / 2, raw_share.values * 100, width = width, label = "Raw sample (trip count)",
       color = COLOR_HTS_RAW, edgecolor = "white", linewidth = 0.5)
ax.bar(x + width / 2, weighted_share.values * 100, width = width, label = "Population-weighted estimate",
       color = COLOR_HTS_WEIGHTED, edgecolor = "white", linewidth = 0.5)

ax.set_xticks(x)
ax.set_xticklabels([mode_labels[m] for m in mode_order], fontsize = 6.2)
ax.set_ylabel("Share of trips [%]")
ax.grid(axis = "y")
ax.set_axisbelow(True)
ax.legend()

plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, "validation_mode_share.pdf"))
plt.savefig(os.path.join(OUT_DIR, "validation_mode_share.png"), dpi = 300)
plt.close()
print("Wrote validation_mode_share.pdf / .png")

# ======================================================================
# 3. Trip distance distribution by mode (HTS, ward-polygon-sampled)
# ======================================================================

print("\n=== 3. Trip distance distribution by mode ===")

fig, ax = plt.subplots(figsize = (6.0, 2.8))
plot_data = [hts_trips_w[hts_trips_w["mode"] == m]["euclidean_distance"].dropna() / 1000.0 for m in mode_order]

bp = ax.boxplot(plot_data, positions = np.arange(len(mode_order)), widths = 0.55,
                 showfliers = False, patch_artist = True, medianprops = dict(color = "#0b0b0b", linewidth = 1.2))
for patch in bp["boxes"]:
    patch.set_facecolor(COLOR_HTS_WEIGHTED)
    patch.set_edgecolor("#52514e")
    patch.set_linewidth(0.6)
for element in ["whiskers", "caps"]:
    for line in bp[element]:
        line.set_color("#52514e")
        line.set_linewidth(0.7)

ax.set_xticks(np.arange(len(mode_order)))
ax.set_xticklabels([mode_labels[m] for m in mode_order], fontsize = 6.2)
ax.set_ylabel("Trip distance [km]")
ax.grid(axis = "y")
ax.set_axisbelow(True)

plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, "validation_distance_by_mode.pdf"))
plt.savefig(os.path.join(OUT_DIR, "validation_distance_by_mode.png"), dpi = 300)
plt.close()
print("Wrote validation_distance_by_mode.pdf / .png")

# Overall distance distribution: HTS (source) vs synthetic population trips
fig, ax = plt.subplots(figsize = (6.0, 2.6))

hts_dist_km = hts_trips_w["euclidean_distance"].dropna() / 1000.0
syn_dist_km = syn_trips["euclidean_distance"].dropna() / 1000.0

bins = np.linspace(0, 20, 41)
ax.hist(hts_dist_km.clip(upper = 20), bins = bins, weights = np.full(len(hts_dist_km), 1.0 / len(hts_dist_km)) * 100,
        histtype = "step", color = COLOR_HTS_WEIGHTED, linewidth = 1.3, label = "HTS (source)")
ax.hist(syn_dist_km.clip(upper = 20), bins = bins, weights = np.full(len(syn_dist_km), 1.0 / len(syn_dist_km)) * 100,
        histtype = "step", color = COLOR_SYNTHETIC, linewidth = 1.3, label = "Synthetic population")

ax.set_xlabel("Trip distance [km] (clipped at 20km)")
ax.set_ylabel("Share of trips [%]")
ax.grid(axis = "y")
ax.set_axisbelow(True)
ax.legend()

plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, "validation_distance_overall.pdf"))
plt.savefig(os.path.join(OUT_DIR, "validation_distance_overall.png"), dpi = 300)
plt.close()
print("Wrote validation_distance_overall.pdf / .png")

# ======================================================================
# 4. Household size distribution: synthetic vs HTS (weighted) vs BBS census
# ======================================================================

print("\n=== 4. Household size distribution ===")

def bucket_size(s):
    return s.clip(upper = 5).astype(int).astype(str).replace({"5": "5+"})

syn_size_share = bucket_size(syn_households["household_size"]).value_counts(normalize = True)
hts_size_weighted = hts_households_w.assign(bucket = bucket_size(hts_households_w["household_size"])).groupby("bucket")["household_weight"].sum()
hts_size_share = hts_size_weighted / hts_size_weighted.sum()

census_hh_size = pd.Series(census_result["household_size"])
census_hh_size.index = census_hh_size.index.astype(str)
census_share = census_hh_size / census_hh_size.sum()

size_order = ["1", "2", "3", "4", "5+"]
df_size = pd.DataFrame({
    "Synthetic": syn_size_share.reindex(size_order).fillna(0),
    "HTS (weighted)": hts_size_share.reindex(size_order).fillna(0),
    "BBS census (district)": census_share.reindex(size_order).fillna(0),
})
print(df_size)

fig, ax = plt.subplots(figsize = (6.0, 2.8))
x = np.arange(len(size_order))
width = 0.26

ax.bar(x - width, df_size["Synthetic"].values * 100, width = width, label = "Synthetic population",
       color = COLOR_SYNTHETIC, edgecolor = "white", linewidth = 0.5)
ax.bar(x, df_size["HTS (weighted)"].values * 100, width = width, label = "HTS (weighted)",
       color = COLOR_HTS_WEIGHTED, edgecolor = "white", linewidth = 0.5)
ax.bar(x + width, df_size["BBS census (district)"].values * 100, width = width, label = "BBS census (district)",
       color = COLOR_CENSUS, edgecolor = "white", linewidth = 0.5)

ax.set_xticks(x)
ax.set_xticklabels([f"{s} person" + ("s" if s != "1" else "") for s in size_order], fontsize = 6.2)
ax.set_ylabel("Share of households [%]")
ax.grid(axis = "y")
ax.set_axisbelow(True)
ax.legend(fontsize = 6.0)

plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, "validation_household_size.pdf"))
plt.savefig(os.path.join(OUT_DIR, "validation_household_size.png"), dpi = 300)
plt.close()
print("Wrote validation_household_size.pdf / .png")

# ======================================================================
# 5. Age-sex pyramid: synthetic (IPU output) vs HTS control totals (weighted)
# ======================================================================

print("\n=== 5. Age-sex pyramid ===")

AGE_BINS = list(range(0, 85, 5)) + [200]
AGE_LABELS = [f"{a}-{a+4}" for a in range(0, 80, 5)] + ["80+"]

def age_sex_shares(df_persons, age_col, sex_col, weight_col = None):
    ages = pd.cut(df_persons[age_col], bins = AGE_BINS, labels = AGE_LABELS, right = False)
    weights = df_persons[weight_col] if weight_col else 1.0
    grouped = df_persons.assign(age_group = ages, w = weights).groupby(["age_group", sex_col], observed = True)["w"].sum()
    total = grouped.sum()
    return grouped / total

syn_pyramid = age_sex_shares(syn_persons, "age", "sex")
hts_pyramid = age_sex_shares(hts_persons_w, "age", "sex", weight_col = "person_weight")

# Common x-axis range across both panels, otherwise bar lengths aren't
# visually comparable between synthetic and HTS
max_share = max(
    syn_pyramid.xs("male", level = 1).max(), syn_pyramid.xs("female", level = 1).max(),
    hts_pyramid.xs("male", level = 1).max(), hts_pyramid.xs("female", level = 1).max(),
) * 100
axis_limit = np.ceil(max_share / 2) * 2 + 1

fig, axes = plt.subplots(1, 2, figsize = (6.4, 3.2), sharey = True)

for ax, pyramid, title in [(axes[0], syn_pyramid, "Synthetic population"), (axes[1], hts_pyramid, "HTS (weighted)")]:
    male = pyramid.xs("male", level = 1).reindex(AGE_LABELS).fillna(0) * 100
    female = pyramid.xs("female", level = 1).reindex(AGE_LABELS).fillna(0) * 100

    y = np.arange(len(AGE_LABELS))
    ax.barh(y, -male.values, color = COLOR_MALE, label = "Male", height = 0.8)
    ax.barh(y, female.values, color = COLOR_FEMALE, label = "Female", height = 0.8)

    ax.set_yticks(y)
    ax.set_yticklabels(AGE_LABELS, fontsize = 5.8)
    ax.set_title(title, fontsize = 7.2)
    ax.axvline(0, color = "#898781", linewidth = 0.6)
    ax.set_xlabel("Share of population [%]")
    ax.set_xlim(-axis_limit, axis_limit)
    ax.xaxis.set_major_formatter(tck.FuncFormatter(lambda v, p: f"{abs(v):.0f}"))
    ax.grid(axis = "x")
    ax.set_axisbelow(True)

fig.legend(*axes[0].get_legend_handles_labels(), fontsize = 6.2, loc = "upper center",
           ncol = 2, bbox_to_anchor = (0.5, 1.0), frameon = False)

plt.tight_layout(rect = (0, 0, 1, 0.94))
plt.savefig(os.path.join(OUT_DIR, "validation_age_sex_pyramid.pdf"))
plt.savefig(os.path.join(OUT_DIR, "validation_age_sex_pyramid.png"), dpi = 300)
plt.close()
print("Wrote validation_age_sex_pyramid.pdf / .png")

# ======================================================================
# 5b. Age distribution (5+): ward-level census vs HTS (weighted) vs synthetic.
# Sex-blind because the census has no joint age x sex table at ward level
# (C-01 is sex-only, C-02 age-only). Restricted to the 5+ population since
# the HTS roster has no under-5s and the synthetic targets exclude them
# (see dhaka/ipu/prepare.py) - the census 0-4 bin is dropped here too so all
# three distributions describe the same population.
# ======================================================================

print("\n=== 5b. Age distribution (5+): census vs HTS vs synthetic ===")

CENSUS_AGE_COLUMNS = {
    f"age_{g}": g for g in [
        "5-9", "10-14", "15-19", "20-24", "25-29", "30-34", "35-39", "40-44",
        "45-49", "50-54", "55-59", "60-64", "65-69", "70-74", "75-79", "80+",
    ]
}
AGE5_LABELS = list(CENSUS_AGE_COLUMNS.values())

census_age = census_c02[list(CENSUS_AGE_COLUMNS)].sum().rename(index = CENSUS_AGE_COLUMNS)
census_age_share = census_age / census_age.sum()

def age5_shares(df_persons, weight_col = None):
    df = df_persons[df_persons["age"] >= 5]
    ages = pd.cut(df["age"], bins = list(range(5, 85, 5)) + [200], labels = AGE5_LABELS, right = False)
    weights = df[weight_col] if weight_col else 1.0
    grouped = df.assign(age_group = ages, w = weights).groupby("age_group", observed = True)["w"].sum()
    return grouped / grouped.sum()

hts_age_share = age5_shares(hts_persons_w, weight_col = "person_weight").reindex(AGE5_LABELS).fillna(0)
syn_age_share = age5_shares(syn_persons).reindex(AGE5_LABELS).fillna(0)

print(pd.DataFrame({
    "census": census_age_share, "hts_weighted": hts_age_share, "synthetic": syn_age_share,
}))

fig, ax = plt.subplots(figsize = (6.4, 2.8))
x = np.arange(len(AGE5_LABELS))
width = 0.26

ax.bar(x - width, census_age_share.values * 100, width = width, label = "BBS census (ward-level, DNCC+DSCC)",
       color = COLOR_CENSUS, edgecolor = "white", linewidth = 0.5)
ax.bar(x, hts_age_share.values * 100, width = width, label = "HTS (weighted)",
       color = COLOR_HTS_WEIGHTED, edgecolor = "white", linewidth = 0.5)
ax.bar(x + width, syn_age_share.values * 100, width = width, label = "Synthetic population",
       color = COLOR_SYNTHETIC, edgecolor = "white", linewidth = 0.5)

ax.set_xticks(x)
ax.set_xticklabels(AGE5_LABELS, fontsize = 5.6, rotation = 45, ha = "right")
ax.set_ylabel("Share of population 5+ [%]")
ax.grid(axis = "y")
ax.set_axisbelow(True)
ax.legend(fontsize = 6.0)

plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, "validation_age_distribution.pdf"))
plt.savefig(os.path.join(OUT_DIR, "validation_age_distribution.png"), dpi = 300)
plt.close()
print("Wrote validation_age_distribution.pdf / .png")

# ======================================================================
# 6. Spatial ward distribution: synthetic homes vs HTS-observed ward distribution
# ======================================================================

print("\n=== 6. Spatial ward distribution ===")

syn_homes_ward = gpd.sjoin(syn_homes, gdf_wards[["commune_id", "geometry"]], how = "left", predicate = "within")
syn_ward_counts = syn_homes_ward.groupby("commune_id", observed = True).size()
syn_ward_share = syn_ward_counts / syn_ward_counts.sum()

hts_ward_weighted = hts_households_w.groupby("commune_id")["household_weight"].sum()
hts_ward_share = hts_ward_weighted / hts_ward_weighted.sum()

# Census household share per ward - DNCC+DSCC only (no ward-level census for
# Savar/Keraniganj), so for the 3-way map all shares are renormalized over
# the city-corp wards to stay like-for-like
census_ward_hh = census_c01.set_index("commune_id")["hh_total"]
census_ward_share_cc = census_ward_hh / census_ward_hh.sum()

is_cc_ward = gdf_wards["commune_id"].astype(str).str.match(r"^[NS]\d")
cc_ward_ids = set(gdf_wards.loc[is_cc_ward, "commune_id"].astype(str))

def renormalize_cc(share):
    share_cc = share[share.index.astype(str).isin(cc_ward_ids)]
    return share_cc / share_cc.sum()

syn_ward_share_cc = renormalize_cc(syn_ward_share)
hts_ward_share_cc = renormalize_cc(hts_ward_share)

gdf_compare = gdf_wards.loc[is_cc_ward, ["commune_id", "geometry"]].copy()
gdf_compare["commune_id"] = gdf_compare["commune_id"].astype(str)
gdf_compare["census_share"] = gdf_compare["commune_id"].map(census_ward_share_cc).astype(float).fillna(0) * 100
gdf_compare["hts_share"] = gdf_compare["commune_id"].map(hts_ward_share_cc).astype(float).fillna(0) * 100
gdf_compare["synthetic_share"] = gdf_compare["commune_id"].map(syn_ward_share_cc).astype(float).fillna(0) * 100

vmax = gdf_compare[["census_share", "hts_share", "synthetic_share"]].max().max()

fig, axes = plt.subplots(1, 3, figsize = (6.8, 3.0))
for ax, col, title in [
    (axes[0], "census_share", "BBS census (ward-level)"),
    (axes[1], "hts_share", "HTS-observed"),
    (axes[2], "synthetic_share", "Synthetic population"),
]:
    gdf_compare.plot(column = col, ax = ax, cmap = "YlGnBu", vmin = 0, vmax = vmax,
                      edgecolor = "#898781", linewidth = 0.15, legend = False)
    ax.set_title(title, fontsize = 7.2)
    ax.axis("off")

sm = plt.cm.ScalarMappable(cmap = "YlGnBu", norm = plt.Normalize(vmin = 0, vmax = vmax))
sm._A = []
cbar = fig.colorbar(sm, ax = axes, orientation = "horizontal", fraction = 0.05, pad = 0.02, shrink = 0.6)
cbar.set_label("Share of DNCC+DSCC households by ward [%]", fontsize = 6.5)
cbar.ax.tick_params(labelsize = 5.8)

plt.savefig(os.path.join(OUT_DIR, "validation_spatial_distribution.pdf"), bbox_inches = "tight")
plt.savefig(os.path.join(OUT_DIR, "validation_spatial_distribution.png"), dpi = 300, bbox_inches = "tight")
plt.close()
print("Wrote validation_spatial_distribution.pdf / .png")

# Correlation summary
merged = pd.DataFrame({
    "census": census_ward_share_cc,
    "hts": hts_ward_share_cc,
    "synthetic": syn_ward_share_cc,
}).fillna(0)
print(f"\nWard-level share correlations (DNCC+DSCC wards):")
print(f"  HTS vs synthetic:    r = {merged['hts'].corr(merged['synthetic']):.3f}")
print(f"  census vs HTS:       r = {merged['census'].corr(merged['hts']):.3f}")
print(f"  census vs synthetic: r = {merged['census'].corr(merged['synthetic']):.3f}")

print("\nAll validation figures written to", OUT_DIR)
