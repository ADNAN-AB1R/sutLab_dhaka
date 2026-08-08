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
`config_dhaka.yml`, or `dhaka/mode_choice/fitted_coefficients.json` — it
regenerates `output/dhaka_1pct_config.xml` and the demand/supply files.
`synpp` caches unchanged stages, so this is incremental, not a full re-run.

Key `config_dhaka.yml` settings: `sampling_rate`, `random_seed`,
`matsim_last_iteration`, `output_path`/`output_prefix`.

## 3. (Re)fit the mode-choice model

Only needed if `dhaka/mode_choice/fitted_coefficients.json` is missing or
the underlying HTS estimation dataset changes:

```bash
python -m dhaka.mode_choice.estimate
```

Writes `dhaka/mode_choice/fitted_coefficients.json` from
`dhaka/mode_choice/cache_estimation_dataset.parquet`. Prints a predicted-
vs-observed mode share sanity check — should match closely at convergence.

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

After a real (multi-iteration) run completes:

```bash
python -m dhaka.mode_choice.calibrate_asc output/simulation_output/modestats.csv
```

Updates `fitted_coefficients.json`'s ASCs in place — picked up fresh on
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
| `dhaka/mode_choice/estimate.py` | Fits the MNL mode-choice model |
| `dhaka/mode_choice/fitted_coefficients.json` | Fitted model — consumed by both the Python synthesis stage and the Java DMC estimator |
| `dhaka/mode_choice/calibrate_asc.py` | ASC calibration against HTS targets |
| `dhaka/synthesis/population/mode_choice.py` | Applies the fitted model once at synthesis time (initial seed plan) |
| `matsim_run/src/main/java/org/dhaka/` | MATSim runner + `discrete_mode_choice` integration |
| `matsim_run/README.md` | Detailed MATSim run/troubleshooting reference |

## 9. Common mistakes

- Running `javac`/bare `java RunDhaka` instead of `mvn clean package` + `java -jar`.
- Running `java` from anywhere other than `output/`.
- Hand-editing `output/dhaka_1pct_config.xml` — it's regenerated by step 2 and your edits will be silently overwritten. Edit `assemble_scenario.py`'s `CONFIG_TEMPLATE` instead.
- Changing `sampling_rate` without regenerating the scenario — network `flowCapacityFactor`/`storageCapacityFactor` and transit vehicle `passengerCarEquivalents` both scale off it automatically on regeneration; they must move together.
- Committing nothing: several files this project has depended on have gone missing mid-session before recovery. Commit working state regularly.
