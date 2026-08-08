package org.dhaka;

import org.matsim.contribs.discrete_mode_choice.modules.AbstractDiscreteModeChoiceExtension;

/**
 * Binds FittedMnlTripEstimator under the name "Fitted" - the value
 * config.xml's DiscreteModeChoice module's tripEstimator param references
 * (see dhaka/matsim/assemble_scenario.py's CONFIG_TEMPLATE).
 */
public class DhakaModeChoiceExtension extends AbstractDiscreteModeChoiceExtension {
    @Override
    protected void installExtension() {
        // Explicit binding required: this Controler setup runs with JIT
        // bindings disabled, so even a plain concrete class with a no-arg
        // constructor needs to be declared here rather than relying on
        // Guice auto-discovering it (confirmed empirically - the same
        // "Explicit bindings are required" error this whole class exists to
        // avoid for FittedMnlTripEstimator's own dependencies).
        bind(FittedCoefficients.class);
        bindTripEstimator("Fitted").to(FittedMnlTripEstimator.class);
    }
}
