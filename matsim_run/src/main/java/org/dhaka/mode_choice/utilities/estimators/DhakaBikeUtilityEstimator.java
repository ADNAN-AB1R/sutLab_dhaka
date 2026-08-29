package org.dhaka.mode_choice.utilities.estimators;

import com.google.inject.Inject;

import org.dhaka.mode_choice.parameters.DhakaModeParameters;

/**
 * Reference mode (thesis: Bicycle) - no ASC term (implicitly 0) and no cost
 * model at all, mirroring the thesis's own zero-fare treatment of bicycle
 * and sao_paulo's precedent of simply omitting a cost model class for modes
 * with no monetary cost term (it has none for walk either).
 */
public class DhakaBikeUtilityEstimator implements DhakaUtilityEstimator {
    private final DhakaModeParameters parameters;

    @Inject
    public DhakaBikeUtilityEstimator(DhakaModeParameters parameters) {
        this.parameters = parameters;
    }

    @Override
    public double estimateUtility(double travelTimeMinutes, double distanceMeters) {
        return parameters.getBetaDuration() * travelTimeMinutes;
    }
}
