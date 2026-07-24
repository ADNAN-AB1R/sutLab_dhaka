"""
Pipeline flowchart figure for the Dhaka data-preparation paper.
Standalone script (not a synpp stage) - run directly:
    python paper/scripts/flowchart.py
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import matplotlib.pyplot as plt
import matplotlib.patches as patches
import palettable

import documentation.plotting as plotting

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "figures")

# Grayscale / Black and White theme for print
INPUT_COLOR = "#ffffff"
STAGE_COLORS = {
    "clean": "#f0f0f0",      
    "synth": "#e0e0e0",      
    "location": "#d0d0d0",   
    "output": "#c0c0c0",     
}
CROSSCHECK_COLOR = "#666666"
TEXT_DARK = "#000000"
MUTED = "#000000"

XLIM = (0, 10.4)

def box(ax, x, y, w, h, text, facecolor, fontsize = 6.2, fontweight = "normal",
        edgecolor = "#000000", linewidth = 0.7, linestyle = "solid", textcolor = None, zorder = 3):
    rect = patches.FancyBboxPatch(
        (x, y), w, h,
        boxstyle = "round,pad=0.02,rounding_size=0.04",
        linewidth = linewidth, edgecolor = edgecolor, facecolor = facecolor,
        linestyle = linestyle, zorder = zorder,
    )
    ax.add_patch(rect)
    ax.text(
        x + w / 2, y + h / 2, text, ha = "center", va = "center",
        fontsize = fontsize, fontweight = fontweight, color = textcolor or TEXT_DARK,
        zorder = zorder + 1, linespacing = 1.35, wrap = True,
    )
    return (x + w / 2, y, x + w / 2, y + h, x, x + w)  # (cx, ybot, cx, ytop, xleft, xright)

def arrow(ax, xy_start, xy_end, color = "#000000", linewidth = 0.9, style = "-|>",
          connectionstyle = "arc3,rad=0.0", linestyle = "solid"):
    ax.annotate(
        "", xy = xy_end, xytext = xy_start,
        arrowprops = dict(
            arrowstyle = style, color = color, linewidth = linewidth,
            linestyle = linestyle, shrinkA = 0, shrinkB = 0,
            connectionstyle = connectionstyle,
        ),
        zorder = 2,
    )

def elbow(ax, points, color = "#000000", linewidth = 0.9, linestyle = "solid"):
    """Orthogonal (Manhattan) connector through `points`, arrowhead on the
    final segment. Coordinates are chosen in main() so that no two connectors
    cross: long edges run in dedicated vertical lanes at the canvas edges and
    horizontal segments are staggered onto distinct y-bands."""
    if len(points) > 2:
        xs = [p[0] for p in points[:-1]]
        ys = [p[1] for p in points[:-1]]
        ax.plot(xs, ys, color = color, linewidth = linewidth, linestyle = linestyle,
                solid_capstyle = "round", solid_joinstyle = "round", zorder = 2)
    arrow(ax, points[-2], points[-1], color = color, linewidth = linewidth,
          linestyle = linestyle)

def stage_label(ax, x, y, text):
    ax.text(x, y, text, ha = "left", va = "center", fontsize = 6.8,
             fontweight = "bold", color = MUTED)

def main():
    plotting.setup()
    plt.rc("font", size = 6.2)

    fig, ax = plt.subplots(figsize = (7.0, 7.1))
    # Slightly wider than XLIM so the outermost boxes' borders aren't clipped
    # (the input/cleaning columns span XLIM exactly by construction)
    ax.set_xlim(XLIM[0] - 0.06, XLIM[1] + 0.06)
    ax.set_ylim(4.9, 27.3)
    ax.axis("off")

    # ------------------------------------------------------------------
    # Row 1: raw inputs - 5 equal columns spanning XLIM
    input_y, input_h = 25.2, 1.5
    col_w = 1.85
    gap = (XLIM[1] - XLIM[0] - 5 * col_w) / 4
    col_x = [XLIM[0] + i * (col_w + gap) for i in range(5)]

    inputs = [
        "DTCA HTS\n(dtca_full.xlsx)\n52,672 households",
        "BBS Census 2022\n(Community Report PDF\n+ district workbook)",
        "Ward boundaries\n(ward_dhk_75.shp)",
        "OSM buildings\n(nationwide extract)",
        "Self-built GTFS +\nOSM road network",
    ]
    input_geo = [box(ax, x, input_y, col_w, input_h, t, INPUT_COLOR, fontsize = 5.8) for x, t in zip(col_x, inputs)]

    stage_label(ax, XLIM[0], input_y - 0.7, "INPUTS")

    # ------------------------------------------------------------------
    # Row 2: cleaning & harmonization - aligned under matching input columns
    clean_y, clean_h = 21.7, 2.0
    clean_texts = [
        "HTS cleaning:\nmode/purpose mapping,\nward-polygon distance\nsampling",
        "PDF table extraction\n(ward-level age / sex /\nhousehold marginals,\nTables C-01 / C-02)",
        "Ward-scheme reconciliation\n(S01-S75 / N01-N54+N98 /\nSAVAR / KERANIGANJ)",
        "Building classification\n(home / work / education /\nshop / leisure)",
        "Transit schedule mapping\n(pt2matsim, external)",
    ]
    clean_geo = [box(ax, col_x[i], clean_y, col_w, clean_h, t, STAGE_COLORS["clean"], fontsize = 5.7)
                 for i, t in enumerate(clean_texts)]

    stage_label(ax, XLIM[0], clean_y - 0.8, "DATA \nCLEANING\n & \nZONING")

    # input -> cleaning, straight down (no crossing needed - columns already align)
    for i in range(5):
        cx = input_geo[i][0]
        arrow(ax, (cx, input_y), (cx, clean_y + clean_h))

    # Census cross-validation branch (dashed, informational), hung directly
    # under the right half of the PDF-extraction box so the extraction ->
    # IPU edge (which leaves from the left half) stays clear of it
    cc_x, cc_y, cc_w, cc_h = col_x[1] + 0.55, 19.3, col_w, 1.1
    box(ax, cc_x, cc_y, cc_w, cc_h, "Cross-validation\n(printed subtotals,\ndistrict workbook)",
        "white", fontsize = 5.5, edgecolor = CROSSCHECK_COLOR, linestyle = "dashed", textcolor = CROSSCHECK_COLOR)
    elbow(ax, [(clean_geo[1][0] + 0.55, clean_y), (cc_x + cc_w / 2, cc_y + cc_h)],
          color = CROSSCHECK_COLOR, linewidth = 0.8, linestyle = "dashed")

    # ------------------------------------------------------------------
    # Row 3: population synthesis
    synth_y, synth_h = 15.4, 2.0
    synth_w = [2.9, 2.6, 2.5]
    synth_gap = 0.35
    synth_x = [XLIM[0] + 0.3]
    synth_x.append(synth_x[0] + synth_w[0] + synth_gap)
    synth_x.append(synth_x[1] + synth_w[1] + synth_gap)

    synth_texts = [
        "IPU control totals\n(census age x sex + household\nscale; HTS-derived for\nSavar / Keraniganj)",
        "IPU raking + TRS\nintegerization\n(seed = HTS households)",
        "Ward assignment\n(weighted draw from real\nHTS ward distribution)",
    ]
    synth_geo = [box(ax, x, synth_y, w, synth_h, t, STAGE_COLORS["synth"], fontsize = 5.8)
                 for x, w, t in zip(synth_x, synth_w, synth_texts)]

    stage_label(ax, XLIM[0], synth_y - 0.85, "POPULATION \nSYNTHESIS \n(IPU / TRS)")

    # cleaning -> synthesis, orthogonal lanes (no two connectors cross):
    # - HTS / census drop straight into the IPU-totals box footprint
    # - ward scheme elbows right on its own y-band into ward assignment
    # - buildings route around the synthesis row via the right-hand lane
    #   (x = 9.3) down to the location-choice box, since any straight drop
    #   would have to pass through the synthesis chain
    synth_top = synth_y + synth_h
    elbow(ax, [(clean_geo[0][0], clean_y), (clean_geo[0][0], synth_top)])
    elbow(ax, [(clean_geo[1][0] - 0.55, clean_y), (clean_geo[1][0] - 0.55, synth_top)])
    elbow(ax, [(clean_geo[2][0], clean_y), (clean_geo[2][0], 18.5),
               (synth_geo[2][0] - 0.75, 18.5), (synth_geo[2][0] - 0.75, synth_top)])

    # synthesis internal flow (index 5 of the box geometry is its right edge)
    arrow(ax, (synth_geo[0][5], synth_y + synth_h / 2), (synth_geo[1][4], synth_y + synth_h / 2))
    arrow(ax, (synth_geo[1][5], synth_y + synth_h / 2), (synth_geo[2][4], synth_y + synth_h / 2))

    # ------------------------------------------------------------------
    # Row 4: activity & location assignment
    loc_y, loc_h = 11.7, 2.0
    loc_w = [2.3, 2.9, 2.3]
    loc_gap = 0.4
    loc_x = [XLIM[0] + 0.6]
    loc_x.append(loc_x[0] + loc_w[0] + loc_gap)
    loc_x.append(loc_x[1] + loc_w[1] + loc_gap)

    loc_texts = [
        "Statistical matching\n(synthetic <-> HTS\nbehavioral linkage)",
        "Home / work / education /\nsecondary location choice\n(distance-distribution based)",
        "Trip & activity schedule\nassembly",
    ]
    loc_geo = [box(ax, x, loc_y, w, loc_h, t, STAGE_COLORS["location"], fontsize = 5.8)
               for x, w, t in zip(loc_x, loc_w, loc_texts)]

    stage_label(ax, XLIM[0], loc_y - 0.6, "ACTIVITY & LOCATION ASSIGNMENT")

    # synthesis -> location assignment, staggered y-bands so the two elbows
    # and the buildings lane (below) never intersect
    loc_top = loc_y + loc_h
    elbow(ax, [(synth_geo[1][0] - 0.45, synth_y), (synth_geo[1][0] - 0.45, 14.15),
               (loc_geo[0][0], 14.15), (loc_geo[0][0], loc_top)])
    elbow(ax, [(synth_geo[2][0], synth_y), (synth_geo[2][0], 14.4),
               (loc_geo[1][0], 14.4), (loc_geo[1][0], loc_top)])

    # buildings -> location choice via the right-hand lane, entering to the
    # right of the ward-assignment elbow (band y=14.0 sits below it)
    elbow(ax, [(clean_geo[3][0], clean_y), (clean_geo[3][0], 19.6),
               (9.3, 19.6), (9.3, 14.0), (loc_geo[1][0] + 0.6, 14.0),
               (loc_geo[1][0] + 0.6, loc_top)])

    # location internal flow
    arrow(ax, (loc_geo[0][5], loc_y + loc_h / 2), (loc_geo[1][4], loc_y + loc_h / 2))
    arrow(ax, (loc_geo[1][5], loc_y + loc_h / 2), (loc_geo[2][4], loc_y + loc_h / 2))

    # ------------------------------------------------------------------
    # Row 5: outputs
    out_y, out_h = 8.4, 1.8
    out_w = [3.1, 2.9]
    out_gap = 0.6
    out_x = [XLIM[0] + 1.0]
    out_x.append(out_x[0] + out_w[0] + out_gap)

    out_texts = [
        "Synthetic population\n(persons / households / trips\nCSV + GPKG)",
        "MATSim population /\nfacilities / households /\nvehicles XML",
    ]
    out_geo = [box(ax, x, out_y, w, out_h, t, STAGE_COLORS["output"], fontsize = 5.8)
               for x, w, t in zip(out_x, out_w, out_texts)]

    stage_label(ax, XLIM[0], out_y - 0.6, "OUTPUT")

    out_top = out_y + out_h
    elbow(ax, [(loc_geo[1][0], loc_y), (loc_geo[1][0], 10.7),
               (out_geo[0][0], 10.7), (out_geo[0][0], out_top)])
    elbow(ax, [(loc_geo[2][0], loc_y), (loc_geo[2][0], 11.05),
               (out_geo[1][0], 11.05), (out_geo[1][0], out_top)])

    # ------------------------------------------------------------------
    # Final assembly
    final_y, final_h = 5.6, 1.7
    final_x, final_w = 2.2, 6.0
    final_geo = box(ax, final_x, final_y, final_w, final_h,
        "MATSim-ready scenario\n(config.xml + network/transit schedule from external pt2matsim)",
        STAGE_COLORS["output"], fontsize = 6.0, fontweight = "bold")

    # outputs drop straight into the final box; the external transit supply
    # runs down the far-right lane (x = 9.9, clear of every box and of the
    # buildings lane at x = 9.3) and enters the final box from the right
    elbow(ax, [(out_geo[0][0], out_y), (out_geo[0][0], final_y + final_h)])
    elbow(ax, [(out_geo[1][0], out_y), (out_geo[1][0], final_y + final_h)])
    elbow(ax, [(clean_geo[4][0], clean_y), (clean_geo[4][0], 20.5),
               (9.9, 20.5), (9.9, final_y + final_h / 2),
               (final_x + final_w, final_y + final_h / 2)])

    plt.tight_layout()

    os.makedirs(OUT_DIR, exist_ok = True)
    plt.savefig(os.path.join(OUT_DIR, "pipeline_flowchart2.pdf"))
    plt.savefig(os.path.join(OUT_DIR, "pipeline_flowchart2.png"), dpi = 300)
    plt.close()
    print("Wrote pipeline_flowchart.pdf / .png")

if __name__ == "__main__":
    main()