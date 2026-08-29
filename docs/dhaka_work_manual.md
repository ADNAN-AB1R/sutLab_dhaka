Run command (from output/, using the real production config — already has all the fixes: capacity scaling, transit pce scaling, tour-based DiscreteModeChoice with caching + the TourLength filter):


cd d:/MatSim/sutLab/sutLab_dhaka/output
java -Xmx8g -jar ../matsim_run/target/dhaka-matsim-run-1.0.jar dhaka_1pct_config.xml
PowerShell equivalent:


Set-Location D:\MatSim\sutLab\sutLab_dhaka\output
java -Xmx8g -jar D:\MatSim\sutLab\sutLab_dhaka\matsim_run\target\dhaka-matsim-run-1.0.jar dhaka_1pct_config.xml
Notes for when you pick this back up:

-Xmx8g matters now — tour-based DMC needs more heap than the old trip-based setup did.
Current lastIteration in that config — check it before running (grep lastIteration dhaka_1pct_config.xml), since it's whatever matsim_last_iteration was last set to in config_dhaka.yml.
Output lands in output/simulation_output/ (deletes/overwrites any existing one there — overwriteFiles=deleteDirectoryIfExists).
At the last confirmed pace (~6-7 min/iteration), a 15-iteration run is roughly 1.5-2 hours.
Scratch test files (simulation_output_tourtest5/, dhaka_1pct_config_tourtest5.xml) are still sitting in output/ from this session — let me know if you want those cleaned up now or left for reference.

# Dhaka Pipeline — Work Manual

Quick operational reference. For troubleshooting/background, see
`matsim_run/README.md` (MATSim run details) and `docs/dhaka_pipeline.md`
(full pipeline design).

## Pipeline stages

```
raw_data/Dhaka/  →  synpp synthesis (Python)  →  assemble_scenario  →  MATSim run  →  analysis/calibration
```

All commands below run from the repo root (`d:/MatSim/sutLab/sutLab_dhaka`)
unless noted.

## 1. Prerequisites

- Python env with `synpp`, `pandas`, `geopandas`, `numpy`, `scipy` installed.
- JDK 17–24 (not 25+, unless you also bump `matsim.version` in
  `matsim_run/pom.xml`).
- Maven 3.6+.
- `raw_data/Dhaka/` present (HTS, census, buildings, ward shapefile, MATSim
  supply files under `raw_data/Dhaka/matsim/`).

## 2. Run the synthesis pipeline

```bash
python -m synpp config_dhaka.yml
```

Runs every stage in `config_dhaka.yml`'s `run:` list end to end (population
synthesis → `dhaka.matsim.assemble_scenario` → validation figures).

To (re)build just the MATSim scenario without the full run:

```bash
python -m synpp config_dhaka.yml --target dhaka.matsim.assemble_scenario
```

Run this after **any** change to `dhaka/matsim/assemble_scenario.py`,
`config_dhaka.yml`, or `dhaka/mode_choice/dhaka_mode_parameters.json` — it
regenerates `output/dhaka_1pct_config.xml` and the demand/supply files.
`synpp` caches unchanged stages, so this is incremental, not a full re-run.

Key `config_dhaka.yml` settings: `sampling_rate`, `random_seed`,
`matsim_last_iteration`, `output_path`/`output_prefix`.

## 3. The mode-choice model

`dhaka/mode_choice/dhaka_mode_parameters.json` is the current, authoritative
mode-choice model — ASCs and shared `beta_duration`/`beta_fare` estimated in
Hoque's MSc thesis (`paper/Ismamul Hoque Msc Thesis.pdf`, Table 4.1), plus
the scenario's fare-construction assumptions per mode. Nothing needs to be
(re-)fit — there's no estimation step, it's a static file consumed directly
by both the Python seed assignment and the Java DMC estimator. See
`matsim_run/README.md`'s "In-simulation mode choice" section for the full
formula, units verification, and package layout
(`matsim_run/src/main/java/org/dhaka/mode_choice/`).

`dhaka/mode_choice/estimate.py` + `fitted_coefficients.json` are the
project's **earlier** approach (an MNL fit directly from
`cache_estimation_dataset.parquet`, our own 274,946-trip DTCA HTS sample) —
currently unused/superseded, kept as reference in case a locally-fitted
model is wanted again later, not deleted.

## 4. Build the MATSim runner

```bash
cd matsim_run
mvn clean package
```

Produces `target/dhaka-matsim-run-1.0.jar`. Rebuild whenever you change any
`.java` file under `matsim_run/src/`. **Never** compile/run `RunDhaka.java`
directly with `javac`/`java` — it only resolves on Maven's classpath.

## 5. Run MATSim

**Must run from `output/`** — MATSim resolves `config.xml`'s relative
paths against the invocation directory, not the config file's location.

```bash
cd output
java -jar ../matsim_run/target/dhaka-matsim-run-1.0.jar dhaka_1pct_config.xml
```

Iteration count comes from `matsim_last_iteration` in `config_dhaka.yml` →
change it there and re-run step 2 to regenerate `config.xml` before
re-running — don't hand-edit the generated XML.

Run a 1-iteration smoke test before a real run (see
`matsim_run/README.md` § "Run a smoke test first" for the exact commands).
A real run takes a long time — run detached/backgrounded.

## 6. Calibrate mode-choice ASCs

**Not optional.** The thesis's published ASCs were estimated on commute-only
trips and do not reproduce Dhaka's all-trip mode split out of the box (walk
~1% modelled vs ~33% observed). See `matsim_run/README.md`'s calibration
section for why.

After a real (multi-iteration) run completes:

```bash
python -m dhaka.mode_choice.calibrate_asc output/simulation_output/modestats.csv
```

Updates `dhaka_mode_parameters.json`'s ASCs in place — picked up fresh on
the next MATSim run, **no rebuild needed**. This closes the gap between
HTS-observed mode shares and what the simulation converges to. It's
iterative: run → calibrate → run → calibrate, until each mode's simulated
share is within tolerance (e.g. ±2 points) of its HTS target. Check
progress in `simulation_output/modestats.csv` (last few iterations should
be roughly flat/converged before calibrating against them).

## 7. Interpreting output (`output/simulation_output/`)

- **`modestats.csv`** — mode share per iteration. Should move over the
  early/mid iterations (mode choice is re-evaluated each iteration via
  `discrete_mode_choice`), then settle. Flat from iteration 0 = something's
  broken (check startup log for Guice `CreationException`).
- **`scorestats.csv`** — average agent score; should trend up and flatten.
- **`legHistogram`** — departure-time pattern by mode.
- **`ITERS/it.N/N.linkstats.csv.gz`** — link volumes/travel times; check
  here for gridlock/spillback.
- **`matsim_run/analyze_mode_fallback.py`** — trip-level pt routing
  success rate (corrects for SwissRailRaptor's fallback-to-walk behavior,
  which naive `modestats.csv` numbers don't account for).

## 8. Key files

| File | Purpose |
|---|---|
| `config_dhaka.yml` | Master pipeline config |
| `dhaka/matsim/assemble_scenario.py` | Builds `config.xml`, copies demand/supply files |
| `paper/Ismamul Hoque Msc Thesis.pdf` | Source of the current mode-choice model's coefficients (Table 4.1) |
| `dhaka/mode_choice/dhaka_mode_parameters.json` | Current mode-choice model — ASCs, `beta_duration`/`beta_fare`, fare-construction assumptions; consumed by both the Python synthesis stage and the Java DMC estimator |
| `dhaka/mode_choice/calibrate_asc.py` | ASC calibration against HTS targets |
| `dhaka/mode_choice/estimate.py` + `fitted_coefficients.json` | Earlier HTS-fitted model — currently unused/superseded, kept as reference |
| `dhaka/synthesis/population/mode_choice.py` | Applies the current model once at synthesis time — feeds `output/dhaka_1pct_trips.csv` only, **not** `population.xml` (MATSim is seeded with HTS-donor modes; see `matsim_run/README.md`) |
| `matsim_run/src/main/java/org/dhaka/mode_choice/` | The mode-choice model in Java (mirrors eqasim-java's per-city package layout) — used every MATSim iteration |
| `matsim_run/src/main/java/org/dhaka/RunDhaka.java` | MATSim runner entry point + `discrete_mode_choice` wiring |
| `matsim_run/README.md` | Detailed MATSim run/troubleshooting reference |

## 9. Common mistakes

- Running `javac`/bare `java RunDhaka` instead of `mvn clean package` + `java -jar`.
- Running `java` from anywhere other than `output/`.
- Hand-editing `output/dhaka_1pct_config.xml` — it's regenerated by step 2 and your edits will be silently overwritten. Edit `assemble_scenario.py`'s `CONFIG_TEMPLATE` instead.
- Changing `sampling_rate` without regenerating the scenario — network `flowCapacityFactor`/`storageCapacityFactor` and transit vehicle `passengerCarEquivalents` both scale off it automatically on regeneration; they must move together.
- Committing nothing: several files this project has depended on have gone missing mid-session before recovery. Commit working state regularly.
