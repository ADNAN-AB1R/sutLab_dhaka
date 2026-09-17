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

# Road-based modes that are simulated on the network in the QSim, each with
# the vehicle characteristics that make Dhaka's mixed traffic behave like
# mixed traffic rather than like a stream of identical cars.
#
# Why this exists at all: until this was added, `car` was the ONLY mode in
# networkModes and every other mode was teleported at a fixed speed. That
# left 92.8% of trips never entering the QSim, and it actively broke mode
# choice - a teleported mode is completely immune to congestion, so when the
# network jammed, motorcycle (teleported, 19.1 km/h, always free-flowing)
# beat car (queueing) by construction. Measured in a 15-iteration run: car
# collapsed 7.21% -> 2.64% while motorcycle climbed 9.78% -> 15.54% and was
# still moving. ASC calibration would have "fixed" those shares by
# penalising motorcycle's constant, i.e. encoding a preference to cancel out
# a missing physical constraint - which then breaks for any scenario that
# changes congestion, the exact thing scenario analysis does.
#
# pce: passenger-car equivalents, i.e. how much ROAD SPACE one vehicle takes
# relative to a car. Deliberately NOT the PCU values from published mixed-
# traffic tables (IRC:106 and the Dhaka DHUTS/RSTP work give cycle-rickshaws
# ~1.0-2.0, three-wheelers ~0.8): those factors are estimated empirically and
# bundle the vehicle's SLOWNESS into its space consumption, because a
# capacity analysis has no other way to represent it. Here slowness is
# modelled explicitly and separately by max_speed_kmh below plus
# linkDynamics=PassingQ, so reusing a speed-derived PCU would count the same
# effect twice - a rickshaw would be both physically car-sized AND capped at
# 12 km/h. These values are therefore footprint-only, which is what MATSim's
# own mixed-traffic setups use. DOCUMENTED ASSUMPTIONS: no local PCU study
# exists in raw_data/, so revisit if one arrives.
#
# pce is NOT scaled by capacity_factor here, unlike transit vehicle pce (see
# scale_transit_pce). The difference is that the transit fleet is UNSAMPLED -
# 100% of buses run to serve a 1% sample of passengers - so its footprint has
# to be shrunk to match a shrunken capacity. Road vehicles are sampled: there
# are genuinely only 1% of them, and flowCapacityFactor already accounts for
# exactly that. Scaling their pce as well would double-count the sampling
# correction and leave the network roughly 100x under-congested instead of
# the ~10x the sqrt(sampling_rate) compromise already costs us.
#
# max_speed_kmh: the crucial half. Without a per-vehicle speed cap every
# vehicle inherits the link's freespeed, which would put cycle-rickshaws on
# an 80 km/h expressway. `None` means "no cap, the link governs" (car).
ROAD_VEHICLE_TYPES = {
    "car":         { "pce": 1.0,  "max_speed_kmh": None, "length": 5.0, "seats": 4 },
    "motorcycle":  { "pce": 0.25, "max_speed_kmh": 60.0, "length": 2.2, "seats": 2 },
    "paratransit": { "pce": 0.5,  "max_speed_kmh": 40.0, "length": 2.8, "seats": 3 },
    "rickshaw":    { "pce": 0.5,  "max_speed_kmh": 12.0, "length": 2.2, "seats": 2 },
    "bike":        { "pce": 0.2,  "max_speed_kmh": 15.0, "length": 1.8, "seats": 1 },
}

# Modes a person OWNS, so the vehicle has to come back home with them -
# DiscreteModeChoice's VehicleContinuity constraint. Hired modes
# (rickshaw, paratransit) and pt are deliberately absent: you leave those
# behind at the end of a leg.
VEHICLE_CONTINUITY_MODES = ["car", "motorcycle", "bike"]

# Modes that stay teleported. walk is not a vehicle and has no business on
# a car network (it would need its own walk network to be meaningful);
# car_passenger and other are pipeline fallbacks, both under 0.5% of trips.
TELEPORTED_MODES = ["walk", "car_passenger", "other"]

LINK_MODES_PATTERN = re.compile(r'(<link\b[^>]*?\bmodes=")([^"]*)(")')


def add_road_modes_to_network(xml_text, road_modes):
    """Grant every car-carrying link permission to carry the other road
    modes too.

    Required, not cosmetic: MATSim routes a network mode only over links
    whose `modes` attribute lists it. The pt2matsim-built network has
    modes="car" on 93.2% of links and "bus,car" on 6.8%, so simply adding
    motorcycle/rickshaw/paratransit/bike to networkModes would leave every
    trip on those modes unroutable.

    Only links that already carry car are touched, so the pt-only,
    rail/light_rail and artificial/stopFacilityLink links pt2matsim created
    keep their own mode sets - those are transit infrastructure, not road."""
    def replace(match):
        existing = [m for m in match.group(2).split(",") if m]
        if "car" not in existing:
            return match.group(0)
        merged = existing + [m for m in road_modes if m not in existing]
        return match.group(1) + ",".join(merged) + match.group(3)

    return LINK_MODES_PATTERN.sub(replace, xml_text)


def build_vehicle_types_xml(vehicle_types):
    """A vehicles file holding exactly ONE vehicleType per network mode.

    qsim's vehiclesSource=modeVehicleTypesFromVehiclesData looks a vehicle
    type up BY its networkMode, so there must be exactly one type per mode -
    the generic matsim.scenario.vehicles stage emits `default_car` AND
    `default_car_passenger` both declaring networkMode="car", which is
    ambiguous under that source. It also emits ~236k per-agent <vehicle>
    instances, which this source ignores (MATSim creates the vehicles it
    needs from the types), so they are dropped rather than rewritten.

    pce is written through unscaled - see ROAD_VEHICLE_TYPES for why road
    vehicles must NOT get the capacity_factor scaling that transit vehicles
    need."""
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<vehicleDefinitions xmlns="http://www.matsim.org/files/dtd"'
        ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
        ' xsi:schemaLocation="http://www.matsim.org/files/dtd'
        ' http://www.matsim.org/files/dtd/vehicleDefinitions_v2.0.xsd">',
    ]

    for mode, spec in vehicle_types.items():
        lines.append('  <vehicleType id="%s">' % mode)
        lines.append('    <capacity seats="%d" standingRoomInPersons="0" />' % spec["seats"])
        lines.append('    <length meter="%f"/>' % spec["length"])
        lines.append('    <width meter="1.000000"/>')
        if spec["max_speed_kmh"] is not None:
            lines.append('    <maximumVelocity meterPerSecond="%f"/>'
                         % (spec["max_speed_kmh"] / 3.6))
        lines.append('    <passengerCarEquivalents pce="%.6g"/>' % spec["pce"])
        lines.append('    <networkMode networkMode="%s"/>' % mode)
        lines.append('  </vehicleType>')

    # car_passenger is teleported, but the generic pipeline still emits it as
    # a mode; give it its own networkMode so it can never collide with car's
    # type under modeVehicleTypesFromVehiclesData.
    lines.append('  <vehicleType id="car_passenger">')
    lines.append('    <capacity seats="4" standingRoomInPersons="0" />')
    lines.append('    <length meter="5.000000"/>')
    lines.append('    <width meter="1.000000"/>')
    lines.append('    <passengerCarEquivalents pce="1"/>')
    lines.append('    <networkMode networkMode="car_passenger"/>')
    lines.append('  </vehicleType>')

    lines.append('</vehicleDefinitions>')
    return "\n".join(lines) + "\n"


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

    <!-- Lets SwissRailRaptor consider a rickshaw hop (not just walking) as
         the access/egress mode for reaching a transit stop, on top of walk -
         CalcLeastCostModePerStop picks whichever is actually better per
         stop. Confirmed empirically to matter a lot for Dhaka: real
         commuters commonly take a rickshaw to reach a bus stop rather than
         walk, and without this, trips whose nearest usable stop is beyond
         comfortable walking distance (but a completely normal rickshaw hop)
         were being treated as unroutable by the transit router - measured
         raising the real trip-level pt routing success rate from 51% to
         71.5% on a 1% sample. See matsim_run/README.md's "Known
         limitations" section for the full investigation this came out of.

         The feeder mode here is `rickshaw_access`, NOT `rickshaw`: an
         access/egress mode must be able to reach every stop, and 29 stops
         sit on artificial links isolated from the road network, which a
         network-mode rickshaw cannot reach (it aborts the run). See
         rickshaw_access's teleportedModeParameters in the routing module
         above for the full reasoning. -->
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
            <param name="mode" value="rickshaw_access" />
            <param name="initialSearchRadius" value="3000.0" />
            <param name="maxRadius" value="5000.0" />
            <param name="searchExtensionRadius" value="500.0" />
        </parameterset>
    </module>

    <module name="qsim">
        <param name="startTime" value="00:00:00" />
        <param name="endTime" value="30:00:00" />
        <!-- Despite the singular name, MATSim's "mainMode" param is a
             comma-separated LIST (QSimConfigGroup.MAIN_MODE -> setMainModes).
             Must match routing's networkModes or a mode gets routed on the
             network and then never simulated on it. -->
        <param name="mainMode" value="{network_modes}" />

        <!-- PassingQ, not the FIFO default. With cycle-rickshaws capped at
             12 km/h sharing links with cars, a FIFO queue lets one rickshaw
             hold up every vehicle behind it for the whole link - the link
             would deliver rickshaw speed to all traffic. PassingQ keeps the
             queue ordered by each vehicle's own earliest-exit time, so
             faster vehicles overtake, which is what actually happens on a
             Dhaka road.

             SeepageQ is the other candidate and is NOT used: it models one
             nominated mode seeping through a standing jam (the natural
             choice for motorcycles), but it permits only a single seep mode
             and would then deny passing to everything else. Worth revisiting
             as a refinement once counts exist to calibrate against. -->
        <param name="linkDynamics" value="PassingQ" />

        <!-- Take one vehicle type per network mode from vehicles.xml.gz
             (built by build_vehicle_types_xml) instead of giving every mode
             an identical default car. This is what actually carries the pce
             and maximumVelocity differences into the QSim; without it the
             mixed-traffic representation above has no effect whatsoever. -->
        <param name="vehiclesSource" value="modeVehicleTypesFromVehiclesData" />

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

    <!-- Every road-based mode is routed and simulated on the network (see
         ROAD_VEHICLE_TYPES in assemble_scenario.py for the pce/speed of each,
         and for why teleporting them was actively wrong: a teleported mode is
         immune to congestion, so it beats car by construction whenever the
         network jams, and mode choice then has to be miscalibrated to
         compensate).

         Only walk (not a vehicle - it would need its own walk network to mean
         anything) and the two sub-0.5% pipeline fallbacks stay teleported. -->
    <module name="routing">
        <param name="networkModes" value="{network_modes}" />

        <parameterset type="teleportedModeParameters">
            <param name="mode" value="walk" />
            <param name="teleportedModeSpeed" value="1.1" />
            <param name="beelineDistanceFactor" value="1.3" />
        </parameterset>
        <parameterset type="teleportedModeParameters">
            <param name="mode" value="car_passenger" />
            <param name="teleportedModeSpeed" value="6.1" />
            <param name="beelineDistanceFactor" value="1.3" />
        </parameterset>
        <parameterset type="teleportedModeParameters">
            <param name="mode" value="other" />
            <param name="teleportedModeSpeed" value="4.2" />
            <param name="beelineDistanceFactor" value="1.3" />
        </parameterset>

        <!-- rickshaw_access: a TELEPORTED rickshaw used ONLY as a transit
             access/egress feeder (see swissRailRaptor below). It is not a
             mode any agent can choose - it never appears in
             DiscreteModeChoice's availableModes.

             It exists because `rickshaw` became a real network mode. 29 of
             this schedule's 1778 stop facilities sit on artificial pt_*
             links that pt2matsim could not match to a road, and all 29 are
             FULLY ISOLATED from the road network (verified: not one shares
             an endpoint with a car link). A network-mode rickshaw therefore
             cannot reach them at all, and MATSim aborts the whole run with
             TransitAgentTriesToTeleportException ("tries to enter a transit
             stop at link pt_A221_B32 but really is at 108259") the moment
             one agent tries. Granting those links road modes cannot fix it -
             with no road path to their nodes it would just turn a crash into
             unroutable legs.

             Dropping rickshaw access entirely was the alternative and is
             worse: it is what raised real trip-level pt routing success from
             51% to 71.5%, because a Dhaka commuter's nearest usable stop is
             routinely a normal rickshaw hop but beyond walking distance.

             The compromise is deliberately narrow. This feeder leg does not
             experience congestion, which understates pt access time
             slightly - but unlike the motorcycle-teleport problem this does
             NOT bias mode choice, because the choice alternative is still
             `pt` as a whole, not this leg. Same speed as rickshaw's former
             teleport setting, so pt access times are unchanged from the
             configuration the 71.5% was measured on. -->
        <parameterset type="teleportedModeParameters">
            <param name="mode" value="rickshaw_access" />
            <param name="teleportedModeSpeed" value="2.8" />
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
            <param name="mode" value="motorcycle" />
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
        <!-- Transit access/egress feeder legs (see rickshaw_access in the
             routing module). Scored the same as rickshaw - these legs are
             part of a pt trip, not a mode an agent chooses. -->
        <parameterset type="modeParams">
            <param name="mode" value="rickshaw_access" />
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
         routed travel time and the same mode-choice model estimated in
         Hoque's MSc thesis (paper/Ismamul Hoque Msc Thesis.pdf, Table 4.1 -
         U = asc[mode] + beta_duration*time + beta_fare*fare; see
         matsim_run/README.md's "In-simulation mode choice" section and
         dhaka/mode_choice/dhaka_mode_parameters.json), then samples one
         candidate tour (MultinomialLogit selector, consistent with how the
         model was fit/applied elsewhere - not a deterministic argmax).
         This replaces the old behavior where dhaka/synthesis/population/
         mode_choice.py's one-shot synthesis-time assignment was frozen for
         the whole run; that stage's output is now only the INITIAL seed
         plan.

         tourEstimator="Cumulative" is a built-in DMC component that sums
         a TripEstimator's per-trip utilities across a tour's trips to
         score whole-tour candidates - confirmed by decompiling the actual
         jar (EstimatorModule.provideCumulativeTourEstimator), it delegates
         to whatever tripEstimator is configured (still "Fitted" -
         matsim_run's org.dhaka.mode_choice.DhakaTripEstimator, unchanged
         from the trip-based setup: it stays a pure per-trip estimator,
         Cumulative is what makes it usable at tour level, no new Java
         class needed for tour-level scoring itself).

         tourConstraints="VehicleContinuity" (restrictedModes is
         VEHICLE_CONTINUITY_MODES - car, motorcycle and bike, i.e. every
         OWNED vehicle; motorcycle and bike joined car once they became real
         network vehicles instead of teleports) requires the SAME vehicle a
         tour departs "home" with to be the one that returns - this is what
         tour-based conversion is actually for, closing the trip-based
         version's known gap (an agent could previously drive to work and bus
         home, stranding the car). Hired modes (rickshaw, paratransit) are
         deliberately unrestricted, as is the stricter built-in
         "SubtourMode" constraint (which forces one mode for an entire
         tour) - that would suppress the per-trip mode variation the
         model is designed to produce (e.g. rickshaw to a nearby
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
         DhakaTripEstimator, which does a REAL TripRouter routing call
         per candidate - without caching, the same (trip, mode) pair gets
         re-routed from scratch every time it recurs across different tour
         candidates. Confirmed empirically to matter: without cachedModes,
         a single replanning pass over ~35k plans stalled for 40+ minutes
         near the JVM heap ceiling instead of completing. Wraps
         DhakaTripEstimator in DMC's built-in CachedTripEstimator for
         every mode.

         tourFilters="TourLength" (maximumLength=6 below) excludes tours
         with more than 6 trips from DMC replanning entirely - their mode
         stays whatever the seed plan assigned, never dynamically
         re-optimized. Confirmed empirically necessary, not precautionary:
         analysis of the actual population found ~95% of tours have just 2
         trips (a simple out-and-back), but a long tail goes up to 14
         trips in a single tour - and tour-based candidate enumeration is
         combinatorial in trips-per-tour, so those rare long tours (~0.06%
         of all tours, confirmed by direct count) were dominating
         replanning wall-clock time by orders of magnitude versus the
         other 99.94%. 6 was chosen as the cutoff because the population's
         own trip-count distribution has a natural break there (2/3/4 trips
         are common; 7+ is rare and where cost explodes) - excluding that
         thin tail trades a negligible loss of dynamic optimization
         coverage for tour-based DMC actually being tractable to run. -->
    <module name="DiscreteModeChoice">
        <param name="modelType" value="Tour" />
        <param name="tripEstimator" value="Fitted" />
        <param name="tourEstimator" value="Cumulative" />
        <param name="selector" value="MultinomialLogit" />
        <param name="modeAvailability" value="Car" />
        <param name="tourConstraints" value="VehicleContinuity" />
        <param name="tourFilters" value="TourLength" />
        <param name="cachedModes" value="walk,bike,car,motorcycle,pt,rickshaw,paratransit" />

        <parameterset type="modeAvailability:Car">
            <param name="availableModes" value="walk,bike,car,motorcycle,pt,rickshaw,paratransit" />
        </parameterset>
        <parameterset type="tourFilter:TourLength">
            <param name="maximumLength" value="6" />
        </parameterset>
        <parameterset type="tourFinder:ActivityBased">
            <param name="activityTypes" value="home" />
        </parameterset>
        <parameterset type="homeFinder:ActivityBased">
            <param name="activityTypes" value="home" />
        </parameterset>
        <!-- Now that motorcycle and bike are real network vehicles rather
             than teleports, they are subject to the same physical constraint
             car always was: if you rode it out, it has to come home with
             you. Hired modes (rickshaw, paratransit) and pt stay
             unrestricted - you leave those behind at the end of a leg. -->
        <parameterset type="tourConstraint:VehicleContinuity">
            <param name="restrictedModes" value="{vehicle_continuity_modes}" />
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

    sampling_rate = context.config("sampling_rate")
    capacity_factor = sampling_rate ** 0.5

    # Replace the generic stage's vehicles file with one vehicleType per
    # network mode. The generic file cannot be used as-is: two of its types
    # both declare networkMode="car", which is ambiguous under
    # vehiclesSource=modeVehicleTypesFromVehiclesData. See
    # build_vehicle_types_xml.
    vehicles_path = "%s/%svehicles.xml.gz" % (output_path, prefix)
    with gzip.open(vehicles_path, "wt", encoding = "utf-8") as f_out:
        f_out.write(build_vehicle_types_xml(ROAD_VEHICLE_TYPES))

    # Supply side: pre-built externally via pt2matsim, just copy in
    supply_base = "%s/%s" % (context.config("data_path"), context.config("dhaka.matsim_supply_path"))

    for target_name, source_name in SUPPLY_FILES.items():
        source_path = "%s/%s" % (supply_base, source_name)
        destination_path = "%s/%s%s" % (output_path, prefix, target_name)

        if not os.path.exists(source_path):
            raise RuntimeError(
                "Expected pre-built MATSim supply file not found: %s" % source_path
            )

        if target_name == "network.xml.gz":
            # Every road mode needs explicit permission on each car link or
            # it cannot be routed at all - see add_road_modes_to_network.
            with gzip.open(source_path, "rt", encoding = "utf-8") as f_in:
                xml_text = add_road_modes_to_network(f_in.read(), list(ROAD_VEHICLE_TYPES))
            with gzip.open(destination_path, "wt", encoding = "utf-8") as f_out:
                f_out.write(xml_text)
        elif target_name == "transit_vehicles.xml.gz":
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
        network_modes = ",".join(ROAD_VEHICLE_TYPES),
        vehicle_continuity_modes = ",".join(VEHICLE_CONTINUITY_MODES),
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
