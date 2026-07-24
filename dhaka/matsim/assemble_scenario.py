import os
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

    <module name="qsim">
        <param name="startTime" value="00:00:00" />
        <param name="endTime" value="30:00:00" />
        <param name="mainMode" value="car" />
        <param name="numberOfThreads" value="{processes}" />
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

    <module name="replanning">
        <param name="maxAgentPlanMemorySize" value="5" />

        <parameterset type="strategysettings">
            <param name="strategyName" value="ChangeExpBeta" />
            <param name="weight" value="0.8" />
        </parameterset>
        <parameterset type="strategysettings">
            <param name="strategyName" value="ReRoute" />
            <param name="weight" value="0.2" />
        </parameterset>
    </module>

    <module name="controller">
        <param name="outputDirectory" value="{simulation_output_dir}" />
        <param name="firstIteration" value="0" />
        <param name="lastIteration" value="{last_iteration}" />
        <param name="mobsim" value="qsim" />
        <param name="overwriteFiles" value="deleteDirectoryIfExists" />
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

    for target_name, source_name in SUPPLY_FILES.items():
        source_path = "%s/%s" % (supply_base, source_name)
        destination_path = "%s/%s%s" % (output_path, prefix, target_name)

        if not os.path.exists(source_path):
            raise RuntimeError(
                "Expected pre-built MATSim supply file not found: %s" % source_path
            )

        if source_name.endswith(".gz"):
            shutil.copy(source_path, destination_path)
        else:
            # Not pre-compressed (e.g. vehicles_unmapped.xml) - gzip it so the
            # file's actual content matches its .gz extension
            with open(source_path, "rb") as f_in, gzip.open(destination_path, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)

    # Config
    config_content = CONFIG_TEMPLATE.format(
        prefix = prefix,
        random_seed = context.config("random_seed"),
        processes = context.config("processes"),
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
