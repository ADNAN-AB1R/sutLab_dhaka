# Running MATSim on the Dhaka scenario

A minimal Maven project that runs the MATSim scenario assembled by
`dhaka/matsim/assemble_scenario.py` (the `dhaka.matsim.assemble_scenario`
pipeline stage). Deliberately **not** eqasim-java — see that stage's
docstring or `docs/dhaka_pipeline.md` §8/§9 for why (eqasim-java's
`SevilleModeChoiceModule` hardcodes exactly 5 modes and doesn't recognize
rickshaw/paratransit). `assemble_scenario.py` already produces a complete,
plain-MATSim scenario in native MATSim XML, so all that's needed here is
vanilla `matsim-core` and a one-line runner.

## Prerequisites

- **JDK 17–24.** Not JDK 25+ unless you also bump `matsim.version` in
  `pom.xml` — see the version note below.
- **Maven 3.6+**
- The scenario itself already assembled: `output/dhaka_1pct_*` files must
  exist. Produced by running the Python pipeline
  (`python -m synpp config_dhaka.yml`, or targeting
  `dhaka.matsim.assemble_scenario` specifically) from the repo root.

## Build

```bash
cd matsim_run
mvn clean package
```

Produces `target/dhaka-matsim-run-1.0.jar` — a single runnable "fat" jar
(~85MB) with all of MATSim's transitive dependencies bundled in (guava,
guice, geotools, log4j, ...). It's gitignored; rebuild it locally rather
than expecting it in version control.

The first build downloads MATSim and its full dependency tree from
`repo.matsim.org` and Maven Central (~10–15 minutes depending on your
connection). Later builds are fast.

## Version note: why 2025.0, not the newest MATSim

`pom.xml` pins `matsim.version` to **2025.0**, the latest full stable annual
release — deliberately *not* the `2027.0-*` weekly-snapshot line that
`repo.matsim.org` currently lists as "latest."

This was confirmed empirically, not assumed: an attempt to run against
`2026.0` (the previous snapshot line) failed immediately with

```
Error: LinkageError occurred while loading main class org.matsim.run.RunMatsim
java.lang.UnsupportedClassVersionError: org/matsim/run/RunMatsim has been
compiled by a more recent version of the Java Runtime (class file version
69.0), this version of the Java Runtime only recognizes class file versions
up to 68.0
```

Class file version 69 corresponds to JDK 25; the environment this was built
against has JDK 24. `2025.0` and `2024.0` were both confirmed to load and
run correctly under JDK 24. If you have JDK 25 available and want the
newest MATSim, bump `<matsim.version>` in `pom.xml` — otherwise leave it at
`2025.0` for broad compatibility.

Also worth knowing: at this version, public-transit support ships **inside**
`matsim-core` itself (confirmed by inspecting the jar directly — no separate
`org.matsim.contrib:pt` artifact exists), so no additional pt dependency is
declared.

## A known build issue, already fixed

An earlier build produced a jar that crashed at startup with:

```
java.util.ServiceConfigurationError: javax.imageio.spi.ImageWriterSpi:
Provider com.sun.media.imageioimpl.plugins.jpeg.CLibJPEGImageWriterSpi
could not be instantiated
Caused by: java.lang.IllegalArgumentException: vendorName == null!
```

This happened *after* config/network/population/transit-schedule all loaded
successfully — it was purely an unrelated image-codec service registration
failing, not a problem with the scenario. Root cause: `javax.media:jai_core`
and `javax.media:jai_imageio` are pulled in transitively by GeoTools (for
raster/GeoTIFF/coverage support this scenario never touches), and Maven
Shade's merging of their `META-INF/services` provider files into one fat
jar breaks the native-backed JPEG codec's static initialization.

Fixed via `<exclusions>` on the `org.matsim:matsim` dependency in
`pom.xml` (already applied). MATSim falls back to the JDK's own built-in
pure-Java ImageIO providers for the PNG/JPEG stats plots it writes, which is
all that's actually needed. If a future MATSim version upgrade reintroduces
a similar conflict, this is the pattern to reapply.

## Run a smoke test first

`config.xml` currently runs `lastIteration=100` by default (see
`matsim_last_iteration` in `config_dhaka.yml`), which takes a long time.
Before committing to that, verify everything loads and simulates correctly
with a 1-iteration test:

```bash
cd ../output
sed \
  -e 's/lastIteration" value="100"/lastIteration" value="1"/' \
  -e 's/outputDirectory" value="simulation_output"/outputDirectory" value="simulation_output_smoketest"/' \
  dhaka_1pct_config.xml > dhaka_1pct_config_smoketest.xml

java -jar ../matsim_run/target/dhaka-matsim-run-1.0.jar dhaka_1pct_config_smoketest.xml
```

Expect this to finish in well under a minute. Success looks like the log
ending with `S H U T D O W N --- shutdown completed` with **no** "unexpected
shutdown" error above it, and `simulation_output_smoketest/modestats.csv`
existing with two rows (iteration 0 and 1, identical mode shares — expected,
since mode isn't dynamically re-chosen).

**Clean up afterward** — even a 1-iteration run writes a full
`simulation_output_smoketest/` directory (events, plans, network, plots;
~350MB for the current ~118K-person population), since MATSim always writes
the first and last iteration in full regardless of `writeEventsInterval`/
`writePlansInterval`:

```bash
rm -rf simulation_output_smoketest dhaka_1pct_config_smoketest.xml
```

## Run for real

```bash
cd output
java -jar ../matsim_run/target/dhaka-matsim-run-1.0.jar dhaka_1pct_config.xml
```

This runs the full `lastIteration` (100 by default). Expect this to take
substantially longer than the 1-iteration smoke test — run it detached or
in a background/tmux session, not an interactive terminal you might close.
Output goes to `simulation_output/` (as set by `outputDirectory` in
`config.xml`).

If you want more or fewer iterations, change `matsim_last_iteration` in
`config_dhaka.yml` and re-run `dhaka.matsim.assemble_scenario` (via the
Python pipeline) to regenerate `config.xml` before rebuilding/re-running
the Java side — don't hand-edit the generated `config.xml` directly, since
it'll be overwritten the next time the pipeline runs that stage.

## Interpreting results

This is **not** calibration — it's the step before calibration. A completed
run's output tells you whether the scenario behaves plausibly; calibration
means comparing that output against real Dhaka benchmarks (traffic counts,
transit ridership, travel-time surveys) and tuning scoring/capacity
parameters to close the gap, which is only meaningful once you have a real
run's output in hand.

What to look at, all under `simulation_output/`:

- **`scorestats.csv`** — average agent score per iteration. Should trend
  upward and flatten out by the later iterations. If it's still climbing
  steeply at iteration 100, the run needs more iterations before its output
  means anything.
- **`modestats.csv`** — mode share per iteration. Should stay essentially
  flat across all iterations: `config.xml`'s replanning module only has
  `ChangeExpBeta` (plan selection) + `ReRoute` (route innovation), matching
  `mode_choice: False` — mode itself is never re-chosen during replanning.
  If mode shares drift, something is wrong with the config, not the model.
- **`legHistogram.txt`** and its plots — departure-time distribution by
  mode; useful for spotting an implausible rush-hour pattern.
- **Per-iteration `ITERS/it.N/N.linkstats.csv.gz`** (or the final
  `output_linkstats.csv.gz`) — per-link volumes and travel times. This is
  where to check for unrealistic gridlock/spillback, which at this
  population's 1% sample rate is a known artifact if
  `storageCapacityFactor` is too low relative to actual link lengths — see
  the comment on that parameter in `dhaka/matsim/assemble_scenario.py`'s
  `CONFIG_TEMPLATE` if you see this.

These CSV/text files are ordinary tabular data — bring them back for
analysis once a real run completes.
