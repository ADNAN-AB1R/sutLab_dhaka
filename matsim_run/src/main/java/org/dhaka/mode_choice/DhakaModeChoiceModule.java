package org.dhaka.mode_choice;

import com.google.inject.multibindings.MapBinder;

import org.dhaka.mode_choice.costs.DhakaCarCostModel;
import org.dhaka.mode_choice.costs.DhakaMotorcycleCostModel;
import org.dhaka.mode_choice.costs.DhakaParatransitCostModel;
import org.dhaka.mode_choice.costs.DhakaPtCostModel;
import org.dhaka.mode_choice.costs.DhakaRickshawCostModel;
import org.dhaka.mode_choice.parameters.DhakaCostParameters;
import org.dhaka.mode_choice.parameters.DhakaModeParameters;
import org.dhaka.mode_choice.utilities.estimators.DhakaBikeUtilityEstimator;
import org.dhaka.mode_choice.utilities.estimators.DhakaCarUtilityEstimator;
import org.dhaka.mode_choice.utilities.estimators.DhakaMotorcycleUtilityEstimator;
import org.dhaka.mode_choice.utilities.estimators.DhakaParatransitUtilityEstimator;
import org.dhaka.mode_choice.utilities.estimators.DhakaPtUtilityEstimator;
import org.dhaka.mode_choice.utilities.estimators.DhakaRickshawUtilityEstimator;
import org.dhaka.mode_choice.utilities.estimators.DhakaUtilityEstimator;
import org.dhaka.mode_choice.utilities.estimators.DhakaWalkUtilityEstimator;
import org.matsim.contribs.discrete_mode_choice.modules.AbstractDiscreteModeChoiceExtension;

/**
 * Mirrors eqasim-java's per-city mode-choice module pattern (e.g.
 * org.eqasim.sao_paulo.mode_choice.SaoPauloModeChoiceModule) - see
 * matsim_run/README.md for why this is vendored into org.dhaka.mode_choice
 * rather than a real org.eqasim:core dependency (no eqasim-java release
 * targets our pinned MATSim 2025.0 - develop needs JDK25/MATSim
 * 2026.0-snapshot, and even the closest release v2.0.0 targets a 2026.0
 * weekly snapshot, not the 2025.0 stable annual release).
 *
 * Binds each mode's utility estimator (utilities/estimators/) into a
 * Map<String, DhakaUtilityEstimator> that DhakaTripEstimator dispatches on
 * by mode, plus the behavioral parameters and cost models they depend on.
 * mode availability (car gated by license) and the vehicle-continuity tour
 * constraint both use MATSim discrete_mode_choice's own built-in "Car"/
 * "VehicleContinuity" components directly via config.xml - no custom
 * DhakaModeAvailability/constraint classes are needed for this 6-mode
 * milestone (unlike sao_paulo, which needs custom ones for car_passenger
 * interaction and a walk-duration cap we don't currently model).
 */
public class DhakaModeChoiceModule extends AbstractDiscreteModeChoiceExtension {
    @Override
    protected void installExtension() {
        // Explicit bindings required: this Controler setup runs with JIT
        // bindings disabled (confirmed empirically - see matsim_run/README.md's
        // "Three Guice wiring issues" section), so even plain concrete
        // classes with a no-arg constructor need to be declared here rather
        // than relying on Guice auto-discovering them.
        bind(DhakaModeParameters.class);
        bind(DhakaCostParameters.class);
        bind(DhakaCarCostModel.class);
        bind(DhakaMotorcycleCostModel.class);
        bind(DhakaPtCostModel.class);
        bind(DhakaRickshawCostModel.class);
        bind(DhakaParatransitCostModel.class);

        MapBinder<String, DhakaUtilityEstimator> estimatorBinder =
            MapBinder.newMapBinder(binder(), String.class, DhakaUtilityEstimator.class);
        estimatorBinder.addBinding("bike").to(DhakaBikeUtilityEstimator.class);
        estimatorBinder.addBinding("car").to(DhakaCarUtilityEstimator.class);
        estimatorBinder.addBinding("motorcycle").to(DhakaMotorcycleUtilityEstimator.class);
        estimatorBinder.addBinding("pt").to(DhakaPtUtilityEstimator.class);
        estimatorBinder.addBinding("rickshaw").to(DhakaRickshawUtilityEstimator.class);
        estimatorBinder.addBinding("paratransit").to(DhakaParatransitUtilityEstimator.class);
        estimatorBinder.addBinding("walk").to(DhakaWalkUtilityEstimator.class);

        bindTripEstimator("Fitted").to(DhakaTripEstimator.class);
    }
}
