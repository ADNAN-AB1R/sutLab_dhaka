package org.dhaka;

import org.matsim.api.core.v01.Scenario;
import org.matsim.core.config.Config;
import org.matsim.core.config.ConfigUtils;
import org.matsim.core.controler.AbstractModule;
import org.matsim.core.controler.Controler;
import org.matsim.core.router.AnalysisMainModeIdentifier;
import org.matsim.core.router.RoutingModeMainModeIdentifier;
import org.matsim.core.scenario.ScenarioUtils;

/**
 * Runs the Dhaka MATSim scenario assembled by dhaka/matsim/assemble_scenario.py
 * (output/dhaka_1pct_config.xml and the population/network/transit_schedule/
 * facilities/households/vehicles files it references).
 *
 * config.xml already declares useTransit="true", and MATSim's own Controler
 * constructor detects that and installs its standard transit module
 * automatically. Mode choice is NOT dynamically re-chosen during replanning
 * (config.xml's replanning module only has ChangeExpBeta + ReRoute, matching
 * mode_choice: False in config_dhaka.yml) - the mode assigned by
 * dhaka.synthesis.population.mode_choice at synthesis time is what gets
 * simulated.
 *
 * One override IS needed: MATSim's default AnalysisMainModeIdentifier
 * throws IllegalStateException ("unknown mode in AnalysisMainModeIdentifier")
 * whenever a trip mixes two modes it doesn't have a built-in ranking for -
 * which happens routinely once SwissRailRaptor's intermodal access/egress is
 * enabled with rickshaw as an access mode (see config.xml's swissRailRaptor
 * module and matsim_run/README.md): a trip can then legitimately contain
 * both "rickshaw" (access/egress) and "pt" (the ride) legs, and the default
 * identifier has no idea which one should count as the trip's "main" mode.
 * RoutingModeMainModeIdentifier sidesteps this entirely by reading the
 * routingMode attribute (what was actually requested/assigned for the whole
 * trip) instead of trying to rank the individual executed leg modes - the
 * semantically correct choice here regardless of the crash, since it's also
 * exactly what dhaka/mode_choice's trip-level analysis relies on.
 *
 * Usage:
 *   java -jar target/dhaka-matsim-run-1.0.jar path/to/dhaka_1pct_config.xml
 */
public class RunDhaka {
    public static void main(String[] args) {
        if (args.length != 1) {
            System.err.println("Usage: java -jar dhaka-matsim-run-1.0.jar <path-to-config.xml>");
            System.exit(1);
        }

        Config config = ConfigUtils.loadConfig(args[0]);
        Scenario scenario = ScenarioUtils.loadScenario(config);

        Controler controler = new Controler(scenario);
        controler.addOverridingModule(new AbstractModule() {
            @Override
            public void install() {
                bind(AnalysisMainModeIdentifier.class).to(RoutingModeMainModeIdentifier.class);
            }
        });
        controler.run();
    }
}
