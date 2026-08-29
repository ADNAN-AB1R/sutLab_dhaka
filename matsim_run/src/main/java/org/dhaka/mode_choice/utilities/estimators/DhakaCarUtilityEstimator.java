package org.dhaka.mode_choice.utilities.estimators;

import com.google.inject.Inject;

import org.dhaka.mode_choice.costs.DhakaCarCostModel;
import org.dhaka.mode_choice.parameters.DhakaModeParameters;

public class DhakaCarUtilityEstimator implements DhakaUtilityEstimator {
    private final DhakaModeParameters parameters;
    private final DhakaCarCostModel costModel;

    @Inject
    public DhakaCarUtilityEstimator(DhakaModeParameters parameters, DhakaCarCostModel costModel) {
        this.parameters = parameters;
        this.costModel = costModel;
    }

    @Override
    public double estimateUtility(double travelTimeMinutes, double distanceMeters) {
        double fareBdt = costModel.calculateCost_bdt(distanceMeters);
        return parameters.getAsc("car")
            + parameters.getBetaDuration() * travelTimeMinutes
            + parameters.getBetaFare() * fareBdt;
    }
}
