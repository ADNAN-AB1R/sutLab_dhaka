"""
Standalone extraction script (NOT a synpp stage): parses ward-level census
marginals out of the BBS 2022 "Population and Housing Census, Community
Report: Dhaka" PDF and writes the two CSVs consumed by dhaka/ipu/prepare.py:

  raw_data/Dhaka/census/extracted/census_c01_wards.csv
      city_corp, ward_num, hh_total, hh_general, hh_institutional, hh_others,
      pop_total, pop_male, pop_female, pop_hijra, sex_ratio
  raw_data/Dhaka/census/extracted/census_c02_wards.csv
      city_corp, ward_num, age_total, age_0-4 ... age_80+

Method: PyMuPDF (fitz) text extraction over the page ranges of Table C-01
(household/population/sex by location) and Table C-02 (population by 17
five-year age groups), matching "Ward No. NN" subtotal rows followed by the
expected count of numeric tokens. City-corporation attribution (DSCC vs DNCC)
is resolved by the position of the "Dhaka South/North City Corporation"
heading in the text stream rather than by fixed page cuts, because the DSCC
-> DNCC transition happens mid-page. A ward appearing multiple times as
"(Part)" (split across thanas) is summed. The sex_ratio column is recomputed
from the summed male/female counts rather than summing printed per-part
ratios.

Cross-validation performed (and expected to hold on re-run):
  - Tables C-01 and C-02 agree on every ward's population total
  - Ward sums reproduce the report's own printed city-corp subtotals exactly:
      DSCC: pop 4,305,063 / hh 1,101,733   DNCC: pop 5,990,723 / hh 1,634,550
  - 75 DSCC wards; 55 DNCC wards (54 numbered + ward 98, the restricted/
    cantonment area - cf. RESTRICTED_AREA_WARD_NUMBER in dhaka/wards.py)

Note: pdfplumber's table extraction was tried first and produced reversed
character runs on this PDF (an artifact of its word-ordering on the bilingual
Bangla/English layout, e.g. 'tcirtsiD' for 'District'); fitz's plain text
extraction is correctly ordered throughout, hence this token-stream approach.

Usage (from the repo root):
    python dhaka/data/census/extract_community_report.py
"""
import csv
import os
import re

import fitz  # PyMuPDF

PDF_PATH = os.path.join("raw_data", "Dhaka", "census", "Community Report Dhaka.pdf")
OUT_DIR = os.path.join("raw_data", "Dhaka", "census", "extracted")

# 0-indexed page ranges covering DSCC+DNCC within each table (the DNCC range
# ends where the rural upazilas begin: "Dhamrai Upazila")
TABLE_RANGES = {
    "C01": range(294, 330),
    "C02": range(381, 416),
}

NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
WARD_RE = re.compile(r"Ward No\. (\d+)(\s*\(([^)]*)\))?")
CC_RE = re.compile(r"Dhaka (North|South) City Corporation")

AGE_GROUPS = ["0-4", "5-9", "10-14", "15-19", "20-24", "25-29", "30-34", "35-39",
              "40-44", "45-49", "50-54", "55-59", "60-64", "65-69", "70-74", "75-79", "80+"]
C01_COLS = ["hh_total", "hh_general", "hh_institutional", "hh_others",
            "pop_total", "pop_male", "pop_female", "pop_hijra", "sex_ratio"]
C02_COLS = ["age_total"] + [f"age_{g}" for g in AGE_GROUPS]

# Printed city-corp subtotals from the report itself, used as a hard check
EXPECTED_SUBTOTALS = {
    "DSCC": {"pop_total": 4305063, "hh_total": 1101733},
    "DNCC": {"pop_total": 5990723, "hh_total": 1634550},
}


def build_flat_text(doc, idx_range):
    return " ".join(" ".join(doc[idx].get_text().split()) for idx in idx_range)


def extract_ward_rows(flat_text, n_values):
    cc_positions = sorted(
        (m.start(), "DSCC" if m.group(1) == "South" else "DNCC")
        for m in CC_RE.finditer(flat_text)
    )

    def cc_at(pos):
        current = None
        for cc_pos, cc_name in cc_positions:
            if cc_pos <= pos:
                current = cc_name
            else:
                break
        return current

    rows, failures = [], []
    for m in WARD_RE.finditer(flat_text):
        ward_num = int(m.group(1))
        cc = cc_at(m.start())
        tokens = flat_text[m.end():m.end() + 400].split()
        vals = []
        for token in tokens:
            if NUM_RE.match(token):
                vals.append(float(token))
                if len(vals) == n_values:
                    break
            else:
                break
        if len(vals) == n_values:
            rows.append((cc, ward_num, vals))
        else:
            failures.append((cc, ward_num, m.group(0)))
    return rows, failures


def aggregate(rows, n_cols):
    agg = {}
    for cc, ward_num, vals in rows:
        key = (cc, ward_num)
        if key not in agg:
            agg[key] = [0.0] * n_cols
        for i, v in enumerate(vals):
            agg[key][i] += v
    return agg


def main():
    doc = fitz.open(PDF_PATH)
    os.makedirs(OUT_DIR, exist_ok=True)

    results = {}
    for table, cols in [("C01", C01_COLS), ("C02", C02_COLS)]:
        flat_text = build_flat_text(doc, TABLE_RANGES[table])
        rows, failures = extract_ward_rows(flat_text, len(cols))
        if failures:
            raise RuntimeError(f"{table}: unparseable ward rows: {failures}")
        results[table] = aggregate(rows, len(cols))
        print(f"{table}: {len(results[table])} unique (city_corp, ward) keys")

    # Recompute sex ratio from summed male/female (not summed per-part ratios)
    for vals in results["C01"].values():
        male, female = vals[C01_COLS.index("pop_male")], vals[C01_COLS.index("pop_female")]
        vals[C01_COLS.index("sex_ratio")] = round(100.0 * male / female, 2) if female else None

    # Cross-checks
    for cc, expected in EXPECTED_SUBTOTALS.items():
        pop = sum(v[C01_COLS.index("pop_total")] for (c, _), v in results["C01"].items() if c == cc)
        hh = sum(v[C01_COLS.index("hh_total")] for (c, _), v in results["C01"].items() if c == cc)
        assert pop == expected["pop_total"], f"{cc} population {pop} != printed subtotal {expected['pop_total']}"
        assert hh == expected["hh_total"], f"{cc} households {hh} != printed subtotal {expected['hh_total']}"
        print(f"{cc}: pop {pop:,.0f} / hh {hh:,.0f} match printed subtotals")

    for key in set(results["C01"]) | set(results["C02"]):
        assert key in results["C01"] and key in results["C02"], f"ward set mismatch at {key}"
        c01_total = results["C01"][key][C01_COLS.index("pop_total")]
        c02_total = results["C02"][key][C02_COLS.index("age_total")]
        assert c01_total == c02_total, f"{key}: C-01 total {c01_total} != C-02 total {c02_total}"
    print("C-01 and C-02 agree on every ward's population total")

    for table, cols in [("C01", C01_COLS), ("C02", C02_COLS)]:
        out_path = os.path.join(OUT_DIR, f"census_{table.lower()}_wards.csv")
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["city_corp", "ward_num"] + cols)
            for (cc, ward_num), vals in sorted(results[table].items()):
                writer.writerow([cc, ward_num] + vals)
        print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
