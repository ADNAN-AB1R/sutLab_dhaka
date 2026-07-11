import shutil
import os.path

import matsim.runtime.eqasim as eqasim

def configure(context):
    context.stage("matsim.simulation.prepare")

    context.stage("matsim.runtime.java")
    context.stage("matsim.runtime.eqasim")
    
    context.config("output_path")
    context.config("simulation_output_dir", "simulation_output")

def execute(context):
    config_path = "%s/%s" % (
        context.path("matsim.simulation.prepare"),
        context.stage("matsim.simulation.prepare")
    )

    # Run routing
    output_dir = os.path.abspath(os.path.join(
        context.config("output_path"), 
        context.config("simulation_output_dir")
    ))
    eqasim.run(context, "org.sutlab.seville.RunSimulation", [
        "--config-path", config_path,
        "--config:controller.lastIteration", str(1),
        "--config:controller.writeEventsInterval", str(1),
        "--config:controller.writePlansInterval", str(1),
        "--config:controller.outputDirectory", output_dir,
    ])

    assert os.path.exists("%s/output_events.xml.gz" % output_dir)