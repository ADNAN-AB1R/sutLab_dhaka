import os
import re
import shutil
import gzip

"""
Assembles a runnable MATSim scenario for Dhaka without going through the
eqasim-java "prepare" step (which needs Java classes - SevilleConfigurator,
RunAdaptConfig, etc. - that only exist for Seville's mode set, not Dhaka's
rickshaw/paratransit modes; see project discussion).

Demand side (population/facilities/households/vehicles) comes from this
pipeline's own pure-Python MATSim writers. Supply side (road network +
transit schedule/vehicles) is pre-built externally via pt2matsim and just
copied in from raw_data/Dhaka/matsim/ - see dhaka.matsim_supply_path config.

Produces, under output_path, with output_prefix:
  population.xml.gz, facilities.xml.gz, households.xml.gz, vehicles.xml.gz,
  network.xml.gz, transit_schedule.xml.gz, transit_vehicles.xml.gz, config.xml
"""

SUPPLY_FILES = {
    "network.xml.gz": "dhaka_multimodalnetwork.xml.gz",
    "transit_schedule.xml.gz": "dhaka_schedule.xml.gz",
    "transit_vehicles.xml.gz": "vehicles_unmapped.xml",
}

PCE_PATTERN = re.compile(r'pce="([0-9.eE+-]+)"')


def scale_transit_pce(xml_text, capacity_factor):
    """vehicles_unmapped.xml (pt2matsim output) carries real-world
    passengerCarEquivalents (e.g. pce=2.8 for a bus). qsim's
    flowCapacityFactor/storageCapacityFactor are scaled down for the
    sub-sampled population (see CONFIG_TEMPLATE - NOT to the raw
    sampling_rate, see that module's comment for why; capacity_factor here
    must always match whatever value flowCapacityFactor actually uses), but
    the transit fleet itself always runs at its real, full-frequency
    schedule (transit demand isn't sub-sampled - every simulated agent who
    boards pt needs a real departure to catch). Left unscaled, each transit
    vehicle consumes pce car-equivalents of a road capacity that has been
    shrunk - e.g. at capacity_factor=0.1, one pce=2.8 bus occupies as much
    of the scaled network as ~28 real cars would on the unscaled network.
    Confirmed to be causing artificial gridlock (trips stuck mid-tour in
    leg histograms) on the ~14k network links shared by bus and car
    traffic when left unscaled entirely. Scaling pce by capacity_factor
    keeps every vehicle type's relative footprint (bus still "bigger" than
    a car) while making its capacity draw consistent with the
    already-scaled network - the standard MATSim sample-scenario fix for
    this."""
    def replace(match):
        original_pce = float(match.group(1))
        return 'pce="%.6g"' % (original_pce * capacity_factor)
    return PCE_PATTERN.sub(replace, xml_text)

CONFIG_TEMPLATE = """<?xml version="1.0" ?>
<!DOCTYPE config SYSTEM "http://www.matsim.org/files/dtd/config_v2.dtd">
<config>
    <module name="global">
        <param name="coordinateSystem" value="EPSG:32646" />
        <param name="randomSeed" value="{random_seed}" />
        <param name="numberOfThreads" value="{processes}" />
    </module>

    <module name="network">
        <param name="inputNetworkFile" value="{prefix}network.xml.gz" />
    </module>

    <module name="plans">
        <param name="inputPlansFile" value="{prefix}population.xml.gz" />
    </module>

    <module name="households">
        <param name="inputFile" value="{prefix}households.xml.gz" />
    </module>

    <module name="facilities">
        <param name="inputFacilitiesFile" value="{prefix}facilities.xml.gz" />
    </module>

    <module name="vehicles">
        <param name="vehiclesFile" value="{prefix}vehicles.xml.gz" />
    </module>

    <module name="transit">
        <param name="useTransit" value="true" />
        <param name="transitScheduleFile" value="{prefix}transit_schedule.xml.gz" />
        <param name="vehiclesFile" value="{prefix}transit_vehicles.xml.gz" />
        <param name="transitModes" value="pt" />
    </module>

    <module name="transitRouter">
        <param name="extensionRadius" value="200.0" />
    </module>

    <!-- Lets SwissRailRaptor consider rickshaw (not just walking) as the
         access/egress mode for reaching a transit stop, on top of walk -
         CalcLeastCostModePerStop picks whichever is actually better per
         stop. Confirmed empirically to matter a lot for Dhaka: real
         commuters commonly take a rickshaw to reach a bus stop rather than
         walk, and without this, trips whose nearest usable stop is beyond
         comfortable walking distance (but a completely normal rickshaw hop)
         were being treated as unroutable by the transit router - measured
         raising the real trip-level pt routing success rate from 51% to
         71.5% on a 1% sample. See matsim_run/README.md's "Known
         limitations" section for the full investigation this came out of. -->
    <module name="swissRailRaptor">
        <param name="useIntermodalAccessEgress" value="true" />
        <param name="intermodalAccessEgressModeSelection" value="CalcLeastCostModePerStop" />

        <parameterset type="intermodalAccessEgress">
            <param name="mode" value="walk" />
            <param name="initialSearchRadius" value="1000.0" />
            <param name="maxRadius" value="2000.0" />
            <param name="searchExtensionRadius" value="200.0" />
        </parameterset>
        <parameterset type="intermodalAccessEgress">
            <param name="mode" value="rickshaw" />
            <param name="initialSearchRadius" value="3000.0" />
            <param name="maxRadius" value="5000.0" />
            <param name="searchExtensionRadius" value="500.0" />
        </parameterset>
    </module>

    <module name="qsim">
        <param name="startTime" value="00:00:00" />
        <param name="endTime" value="30:00:00" />
        <param name="mainMode" value="car" />
        <param name="numberOfThreads" value="{processes}" />
        <!-- Standard MATSim practice for a sub-sampled population: scale
             link capacity down to match the sample fraction, otherwise a
             1% population is trying to congest a network sized for 100% of
             real Dhaka traffic and the simulation looks artificially empty.

             NOT scaled by the raw sampling_rate (0.01) - confirmed
             empirically that this causes severe artificial gridlock (leg
             histogram: ~16k cars permanently "en route" all day while
             departures/arrivals never exceeded ~800). Root cause: this
             network's median link capacity is only ~600 veh/h to begin
             with (many 1-2 lane local/residential links), and 0.01x that
             is ~6 veh/h - one vehicle allowed through roughly every 10
             minutes, a hard per-link release-rate ceiling MATSim's queue
             model enforces regardless of how few sampled vehicles actually
             need that link across the whole day. (storageCapacityFactor
             alone does NOT fix this - confirmed by testing at 10x its own
             scale with flowCapacityFactor left at 0.01 and seeing the
             identical gridlock: a vehicle rate-limited to 6/hour doesn't
             need much queue space to cause the jam, so storage was never
             the actual constraint.) sqrt(sampling_rate) applied to BOTH
             factors is the standard mitigation for this at very low sample
             rates - demand is already reduced by the full sampling_rate
             (fewer agents), so a gentler capacity reduction keeps the
             demand/capacity ratio congested without creating pathological
             per-link micro-bottlenecks. -->
        <param name="flowCapacityFactor" value="{storage_capacity_factor}" />
        <param name="storageCapacityFactor" value="{storage_capacity_factor}" />
    </module>

    <!-- car is routed on the network; every other mode is teleported at a
         fixed speed until a mode-specific network/routing setup exists -->
    <module name="routing">
        <param name="networkModes" value="car" />

        <parameterset type="teleportedModeParameters">
            <param name="mode" value="walk" />
            <param name="teleportedModeSpeed" value="1.1" />
            <param name="beelineDistanceFactor" value="1.3" />
        </parameterset>
        <parameterset type="teleportedModeParameters">
            <param name="mode" value="bike" />
            <param name="teleportedModeSpeed" value="3.1" />
            <param name="beelineDistanceFactor" value="1.3" />
        </parameterset>
        <parameterset type="teleportedModeParameters">
            <param name="mode" value="car_passenger" />
            <param name="teleportedModeSpeed" value="6.1" />
            <param name="beelineDistanceFactor" value="1.3" />
        </parameterset>
        <parameterset type="teleportedModeParameters">
            <param name="mode" value="rickshaw" />
            <param name="teleportedModeSpeed" value="2.8" />
            <param name="beelineDistanceFactor" value="1.3" />
        </parameterset>
        <parameterset type="teleportedModeParameters">
            <param name="mode" value="paratransit" />
            <param name="teleportedModeSpeed" value="4.2" />
            <param name="beelineDistanceFactor" value="1.3" />
        </parameterset>
        <parameterset type="teleportedModeParameters">
            <param name="mode" value="other" />
            <param name="teleportedModeSpeed" value="4.2" />
            <param name="beelineDistanceFactor" value="1.3" />
        </parameterset>
    </module>

    <module name="scoring">
        <param name="marginalUtilityOfMoney" value="1.0" />

        <parameterset type="activityParams">
            <param name="activityType" value="home" />
            <param name="typicalDuration" value="12:00:00" />
        </parameterset>
        <parameterset type="activityParams">
            <param name="activityType" value="work" />
            <param name="typicalDuration" value="08:00:00" />
        </parameterset>
        <parameterset type="activityParams">
            <param name="activityType" value="education" />
            <param name="typicalDuration" value="06:00:00" />
        </parameterset>
        <parameterset type="activityParams">
            <param name="activityType" value="shop" />
            <param name="typicalDuration" value="01:00:00" />
        </parameterset>
        <parameterset type="activityParams">
            <param name="activityType" value="leisure" />
            <param name="typicalDuration" value="02:00:00" />
        </parameterset>
        <parameterset type="activityParams">
            <param name="activityType" value="other" />
            <param name="typicalDuration" value="01:00:00" />
        </parameterset>

        <parameterset type="modeParams">
            <param name="mode" value="car" />
            <param name="marginalUtilityOfTraveling_util_hr" value="-6.0" />
            <param name="constant" value="0.0" />
        </parameterset>
        <parameterset type="modeParams">
            <param name="mode" value="pt" />
            <param name="marginalUtilityOfTraveling_util_hr" value="-3.0" />
            <param name="constant" value="0.0" />
        </parameterset>
        <parameterset type="modeParams">
            <param name="mode" value="walk" />
            <param name="marginalUtilityOfTraveling_util_hr" value="-6.0" />
            <param name="constant" value="0.0" />
        </parameterset>
        <parameterset type="modeParams">
            <param name="mode" value="bike" />
            <param name="marginalUtilityOfTraveling_util_hr" value="-6.0" />
            <param name="constant" value="0.0" />
        </parameterset>
        <parameterset type="modeParams">
            <param name="mode" value="car_passenger" />
            <param name="marginalUtilityOfTraveling_util_hr" value="-6.0" />
            <param name="constant" value="0.0" />
        </parameterset>
        <parameterset type="modeParams">
            <param name="mode" value="rickshaw" />
            <param name="marginalUtilityOfTraveling_util_hr" value="-6.0" />
            <param name="constant" value="0.0" />
        </parameterset>
        <parameterset type="modeParams">
            <param name="mode" value="paratransit" />
            <param name="marginalUtilityOfTraveling_util_hr" value="-6.0" />
            <param name="constant" value="0.0" />
        </parameterset>
        <parameterset type="modeParams">
            <param name="mode" value="other" />
            <param name="marginalUtilityOfTraveling_util_hr" value="-6.0" />
            <param name="constant" value="0.0" />
        </parameterset>
    </module>

    <!-- Runs mode choice as an in-simulation replanning strategy: every
         time it's picked, DiscreteModeChoice re-estimates mode for every
         trip in a whole TOUR at once (a chain of trips between two visits
         to "home" - modelType=Tour, tourFinder/homeFinder="ActivityBased"
         with activityTypes="home", matching this module's own scoring
         activityParams above), using that iteration's ACTUAL simulated/
         routed travel time and the same linear-utility MNL fitted in
         dhaka/mode_choice/estimate.py, then samples one candidate tour
         (MultinomialLogit selector, consistent with how the model was
         fit/applied elsewhere - not a deterministic argmax). This replaces
         the old behavior where dhaka/synthesis/population/mode_choice.py's
         one-shot synthesis-time assignment was frozen for the whole run;
         that stage's output is now only the INITIAL seed plan.

         tourEstimator="Cumulative" is a built-in DMC component that sums
         a TripEstimator's per-trip utilities across a tour's trips to
         score whole-tour candidates - confirmed by decompiling the actual
         jar (EstimatorModule.provideCumulativeTourEstimator), it delegates
         to whatever tripEstimator is configured (still "Fitted" -
         matsim_run's FittedMnlTripEstimator, unchanged from the trip-based
         setup: it stays a pure per-trip estimator, Cumulative is what
         makes it usable at tour level, no new Java class needed).

         tourConstraints="VehicleContinuity" (restrictedModes="car" below)
         requires the SAME car a tour departs "home" with to be the one
         that returns - this is what tour-based conversion is actually for,
         closing the trip-based version's known gap (an agent could
         previously drive to work and bus home, stranding the car). Only
         car is restricted, deliberately not the stricter built-in
         "SubtourMode" constraint (which forces one mode for an entire
         tour) - that would suppress the per-trip mode variation the
         fitted model is designed to produce (e.g. rickshaw to a nearby
         shop mid-tour, bus the rest of the way home), and the only
         physical resource actually requiring continuity is the car
         itself. modeAvailability "Car" gates the car alternative by the
         population's existing hasLicense/carAvailability person
         attributes (confirmed via the actual matsim-core jar:
         PersonUtils.getLicense() reads the "hasLicense" attribute key,
         which dhaka/matsim/scenario/population.py already writes) - every
         other mode here has no such constraint in Dhaka (rickshaw/
         paratransit need no license).

         cachedModes matters a lot more here than it would for trip-based:
         tour-based candidate enumeration evaluates many trip x mode
         combinations per agent (a whole tour's worth), each estimated via
         FittedMnlTripEstimator, which does a REAL TripRouter routing call
         per candidate - without caching, the same (trip, mode) pair gets
         re-routed from scratch every time it recurs across different tour
         candidates. Confirmed empirically to matter: without cachedModes,
         a single replanning pass over ~35k plans stalled for 40+ minutes
         near the JVM heap ceiling instead of completing. Wraps
         FittedMnlTripEstimator in DMC's built-in CachedTripEstimator for
         every mode. -->
    <module name="DiscreteModeChoice">
        <param name="modelType" value="Tour" />
        <param name="tripEstimator" value="Fitted" />
        <param name="tourEstimator" value="Cumulative" />
        <param name="selector" value="MultinomialLogit" />
        <param name="modeAvailability" value="Car" />
        <param name="tourConstraints" value="VehicleContinuity" />
        <param name="cachedModes" value="walk,bike,car,pt,rickshaw,paratransit" />

        <parameterset type="modeAvailability:Car">
            <param name="availableModes" value="walk,bike,car,pt,rickshaw,paratransit" />
        </parameterset>
        <parameterset type="tourFinder:ActivityBased">
            <param name="activityTypes" value="home" />
        </parameterset>
        <parameterset type="homeFinder:ActivityBased">
            <param name="activityTypes" value="home" />
        </parameterset>
        <parameterset type="tourConstraint:VehicleContinuity">
            <param name="restrictedModes" value="car" />
        </parameterset>
    </module>

    <module name="replanning">
        <param name="maxAgentPlanMemorySize" value="5" />

        <parameterset type="strategysettings">
            <param name="strategyName" value="ChangeExpBeta" />
            <param name="weight" value="0.5" />
        </parameterset>
        <parameterset type="strategysettings">
            <param name="strategyName" value="ReRoute" />
            <param name="weight" value="0.2" />
        </parameterset>
        <parameterset type="strategysettings">
            <param name="strategyName" value="DiscreteModeChoice" />
            <param name="weight" value="0.3" />
        </parameterset>
    </module>

    <module name="controller">
        <param name="outputDirectory" value="{simulation_output_dir}" />
        <param name="firstIteration" value="0" />
        <param name="lastIteration" value="{last_iteration}" />
        <param name="mobsim" value="qsim" />
        <param name="overwriteFiles" value="deleteDirectoryIfExists" />
        <param name="writeEventsInterval" value="10" />
        <param name="writePlansInterval" value="10" />
    </module>
</config>
"""

def configure(context):
    context.stage("matsim.scenario.population")
    context.stage("matsim.scenario.facilities")
    context.stage("matsim.scenario.households")
    context.stage("matsim.scenario.vehicles")

    context.config("data_path")
    context.config("output_path")
    context.config("output_prefix", "dhaka_1pct_")
    context.config("random_seed")
    context.config("processes")
    context.config("sampling_rate")
    context.config("dhaka.matsim_supply_path", "matsim")
    context.config("simulation_output_dir", "simulation_output")
    context.config("matsim_last_iteration", 0)

def execute(context):
    output_path = context.config("output_path")
    prefix = context.config("output_prefix")

    # Demand side: copy from each stage's cache output
    demand_stages = {
        "matsim.scenario.population": "population.xml.gz",
        "matsim.scenario.facilities": "facilities.xml.gz",
        "matsim.scenario.households": "households.xml.gz",
        "matsim.scenario.vehicles": "vehicles.xml.gz",
    }

    for stage_name, target_name in demand_stages.items():
        source_path = "%s/%s" % (context.path(stage_name), context.stage(stage_name))
        shutil.copy(source_path, "%s/%s%s" % (output_path, prefix, target_name))

    # Supply side: pre-built externally via pt2matsim, just copy in
    supply_base = "%s/%s" % (context.config("data_path"), context.config("dhaka.matsim_supply_path"))
    sampling_rate = context.config("sampling_rate")
    capacity_factor = sampling_rate ** 0.5

    for target_name, source_name in SUPPLY_FILES.items():
        source_path = "%s/%s" % (supply_base, source_name)
        destination_path = "%s/%s%s" % (output_path, prefix, target_name)

        if not os.path.exists(source_path):
            raise RuntimeError(
                "Expected pre-built MATSim supply file not found: %s" % source_path
            )

        if target_name == "transit_vehicles.xml.gz":
            # Scale passengerCarEquivalents to match flowCapacityFactor
            # (capacity_factor, NOT the raw sampling_rate - see
            # scale_transit_pce's docstring and the qsim module's comment
            # in CONFIG_TEMPLATE for why) so bus-vs-car relative capacity
            # draw stays consistent with the actual scaled network.
            with open(source_path, "r", encoding = "utf-8") as f_in:
                xml_text = scale_transit_pce(f_in.read(), capacity_factor)
            with gzip.open(destination_path, "wt", encoding = "utf-8") as f_out:
                f_out.write(xml_text)
        elif source_name.endswith(".gz"):
            shutil.copy(source_path, destination_path)
        else:
            # Not pre-compressed - gzip it so the file's actual content
            # matches its .gz extension
            with open(source_path, "rb") as f_in, gzip.open(destination_path, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)

    # Config
    config_content = CONFIG_TEMPLATE.format(
        prefix = prefix,
        random_seed = context.config("random_seed"),
        processes = context.config("processes"),
        sampling_rate = sampling_rate,
        storage_capacity_factor = sampling_rate ** 0.5,
        simulation_output_dir = context.config("simulation_output_dir"),
        last_iteration = context.config("matsim_last_iteration"),
    )

    config_path = "%s/%sconfig.xml" % (output_path, prefix)
    with open(config_path, "w") as f:
        f.write(config_content)

    return "%sconfig.xml" % prefix

def validate(context):
    supply_base = "%s/%s" % (context.config("data_path"), context.config("dhaka.matsim_supply_path"))
    total_size = 0

    for source_name in SUPPLY_FILES.values():
        path = "%s/%s" % (supply_base, source_name)
        if not os.path.exists(path):
            raise RuntimeError("Missing pre-built MATSim supply file: %s" % path)
        total_size += os.path.getsize(path)

    return total_size
