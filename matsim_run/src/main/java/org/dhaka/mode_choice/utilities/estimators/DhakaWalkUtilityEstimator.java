package org.dhaka.mode_choice.utilities.estimators;

import com.google.inject.Inject;

import org.dhaka.mode_choice.parameters.DhakaModeParameters;

/** Zero-fare mode (thesis-stated) - no cost model needed, has its own ASC. */
public class DhakaWalkUtilityEstimator implements DhakaUtilityEstimator {
    private final DhakaModeParameters parameters;

    @Inject
    public DhakaWalkUtilityEstimator(DhakaModeParameters parameters) {
        this.parameters = parameters;
    }

    @Override
    public double estimateUtility(double travelTimeMinutes, double distanceMeters) {
        return parameters.getAsc("walk") + parameters.getBetaDuration() * travelTimeMinutes;
    }
}
