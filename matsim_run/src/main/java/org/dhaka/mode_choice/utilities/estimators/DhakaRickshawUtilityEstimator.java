package org.dhaka.mode_choice.utilities.estimators;

import com.google.inject.Inject;

import org.dhaka.mode_choice.costs.DhakaRickshawCostModel;
import org.dhaka.mode_choice.parameters.DhakaModeParameters;

public class DhakaRickshawUtilityEstimator implements DhakaUtilityEstimator {
    private final DhakaModeParameters parameters;
    private final DhakaRickshawCostModel costModel;

    @Inject
    public DhakaRickshawUtilityEstimator(DhakaModeParameters parameters, DhakaRickshawCostModel costModel) {
        this.parameters = parameters;
        this.costModel = costModel;
    }

    @Override
    public double estimateUtility(double travelTimeMinutes, double distanceMeters) {
        double fareBdt = costModel.calculateCost_bdt(distanceMeters);
        return parameters.getAsc("rickshaw")
            + parameters.getBetaDuration() * travelTimeMinutes
            + parameters.getBetaFare() * fareBdt;
    }
}
