"""
Data availability matrix (Seville vs. Dhaka) for the Dhaka data-preparation paper.
Standalone script - run directly:
    python paper/scripts/availability_matrix.py
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import matplotlib.pyplot as plt
import matplotlib.patches as patches

import documentation.plotting as plotting

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "figures")

# Status palette (fixed roles, not themed) - text label always accompanies
# color so nothing is color-alone, and it reads fine in grayscale print.
STATUS = {
    "Full":    dict(color = "#bfe6bf", text = "#0b3d0b", label = "Full"),
    "Partial": dict(color = "#fde3ad", text = "#5c3d00", label = "Partial"),
    "None":    dict(color = "#f3c6c4", text = "#5c0000", label = "None"),
}

ROWS = [
    # Dhaka: ward-level age pyramid + sex counts exist, but only inside a
    # 1668-page PDF (extracted in this study), with no joint age x sex table
    # and no ward/union-level rows outside the two city corporations
    ("Fine-grained census\n(age x sex pyramid)",        "Full",    "Partial"),
    ("HTS respondent coverage\n(full household roster)", "Partial", "Full"),
    ("HTS destination\ngeocoding",                        "Full",    "None"),
    ("Official transit GTFS",                             "Full",    "None"),
    ("Admin/ward boundaries\nmatching the HTS",            "Full",    "Partial"),
    ("Driving-license statistics\nper person",             "Partial", "Full"),
    ("Household income data",                              "None",    "Partial"),
    ("Typed building / POI data\n(work, education, shop)", "Full",    "Partial"),
]

COLS = ["Seville\n(data-rich reference)", "Dhaka\n(this study)"]

def main():
    plotting.setup()
    plt.rc("font", size = 7.0)

    n_rows = len(ROWS)
    n_cols = len(COLS)

    row_h = 1.0
    col_w = 2.6
    label_w = 3.6

    fig_w = (label_w + n_cols * col_w) / 2.2
    fig_h = (n_rows * row_h + 1.0) / 2.2
    fig, ax = plt.subplots(figsize = (fig_w, fig_h))

    total_w = label_w + n_cols * col_w
    total_h = n_rows * row_h

    ax.set_xlim(0, total_w)
    ax.set_ylim(0, total_h + 1.0)
    ax.axis("off")
    ax.invert_yaxis()

    # Column headers
    header_y = -0.15
    for j, col_name in enumerate(COLS):
        cx = label_w + j * col_w + col_w / 2
        ax.text(cx, header_y, col_name, ha = "center", va = "bottom",
                 fontsize = 7.2, fontweight = "bold", linespacing = 1.3)

    # Rows
    for i, (row_label, seville_status, dhaka_status) in enumerate(ROWS):
        y = i * row_h
        ax.text(0, y + row_h / 2, row_label, ha = "left", va = "center",
                 fontsize = 6.6, linespacing = 1.25)

        for j, status_key in enumerate([seville_status, dhaka_status]):
            x = label_w + j * col_w
            spec = STATUS[status_key]
            rect = patches.Rectangle(
                (x + 0.08, y + 0.08), col_w - 0.16, row_h - 0.16,
                facecolor = spec["color"], edgecolor = "#898781", linewidth = 0.5,
            )
            ax.add_patch(rect)
            ax.text(x + col_w / 2, y + row_h / 2, spec["label"], ha = "center", va = "center",
                     fontsize = 6.8, fontweight = "bold", color = spec["text"])

        # row separator
        if i > 0:
            ax.plot([0, total_w], [y, y], color = "#e1e0d9", linewidth = 0.5, zorder = 0)

    # Outer frame around the two data columns
    ax.plot(
        [label_w, total_w, total_w, label_w, label_w],
        [0, 0, total_h, total_h, 0],
        color = "#898781", linewidth = 0.8,
    )
    ax.plot([label_w + col_w, label_w + col_w], [0, total_h], color = "#898781", linewidth = 0.5)

    plt.tight_layout()

    os.makedirs(OUT_DIR, exist_ok = True)
    plt.savefig(os.path.join(OUT_DIR, "data_availability_matrix.pdf"))
    plt.savefig(os.path.join(OUT_DIR, "data_availability_matrix.png"), dpi = 300)
    plt.close()
    print("Wrote data_availability_matrix.pdf / .png")

if __name__ == "__main__":
    main()
