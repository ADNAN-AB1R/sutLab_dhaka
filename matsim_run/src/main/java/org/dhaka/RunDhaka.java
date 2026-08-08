package org.dhaka;

import org.matsim.api.core.v01.Scenario;
import org.matsim.contribs.discrete_mode_choice.modules.DiscreteModeChoiceModule;
import org.matsim.contribs.discrete_mode_choice.modules.config.DiscreteModeChoiceConfigGroup;
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
 * automatically. Mode choice IS dynamically re-chosen during replanning via
 * the discrete_mode_choice contrib (config.xml's DiscreteModeChoice module +
 * a DiscreteModeChoice strategysettings entry in replanning) - the mode
 * dhaka.synthesis.population.mode_choice assigns at synthesis time is only
 * the initial seed plan; FittedMnlTripEstimator re-estimates every trip's
 * mode utility each time that strategy is picked, using that iteration's
 * actual simulated travel time and the same fitted MNL model. (Unrelated:
 * config_dhaka.yml's mode_choice: False flag is a different, pre-existing
 * key that only gates an unused eqasim-java branch - see dhaka/output.py.)
 *
 * Two overrides are needed for this. First, MATSim's default AnalysisMainModeIdentifier
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
 * Second, DiscreteModeChoiceModule (framework wiring) and
 * DhakaModeChoiceExtension (binds FittedMnlTripEstimator - see that class
 * and DhakaModeChoiceExtension for details) together activate the
 * DiscreteModeChoice module/strategy declared in config.xml.
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

        // DiscreteModeChoiceConfigGroup must be registered here, not left to
        // be auto-detected: unlike SwissRailRaptor (special-cased internally
        // by MATSim via config's useTransit=true), discrete_mode_choice is an
        // ordinary contrib - without pre-registering the real config group
        // class, config.xml's <module name="DiscreteModeChoice"> block parses
        // as a generic, untyped ConfigGroup, and DiscreteModeChoiceModule's
        // Guice bindings then fail with a cascading "explicit bindings
        // required" injector-creation error (confirmed empirically: the exact
        // same crash happens even with nothing but the bare module added).
        Config config = ConfigUtils.loadConfig(args[0], new DiscreteModeChoiceConfigGroup());
        Scenario scenario = ScenarioUtils.loadScenario(config);

        Controler controler = new Controler(scenario);
        controler.addOverridingModule(new AbstractModule() {
            @Override
            public void install() {
                bind(AnalysisMainModeIdentifier.class).to(RoutingModeMainModeIdentifier.class);
            }
        });

        // discrete_mode_choice: config.xml's DiscreteModeChoice module +
        // replanning strategysettings entry (see assemble_scenario.py) need
        // both of these installed to actually take effect - the base module
        // wires up the framework/replanning-strategy binding itself, and
        // DhakaModeChoiceExtension binds our FittedMnlTripEstimator under
        // the name ("Fitted") config.xml's tripEstimator param references.
        controler.addOverridingModule(new DiscreteModeChoiceModule());
        controler.addOverridingModule(new DhakaModeChoiceExtension());

        controler.run();
    }
}
