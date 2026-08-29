package org.dhaka.mode_choice.utilities.estimators;

import com.google.inject.Inject;

import org.dhaka.mode_choice.costs.DhakaPtCostModel;
import org.dhaka.mode_choice.parameters.DhakaModeParameters;

public class DhakaPtUtilityEstimator implements DhakaUtilityEstimator {
    private final DhakaModeParameters parameters;
    private final DhakaPtCostModel costModel;

    @Inject
    public DhakaPtUtilityEstimator(DhakaModeParameters parameters, DhakaPtCostModel costModel) {
        this.parameters = parameters;
        this.costModel = costModel;
    }

    @Override
    public double estimateUtility(double travelTimeMinutes, double distanceMeters) {
        double fareBdt = costModel.calculateCost_bdt(distanceMeters);
        return parameters.getAsc("pt")
            + parameters.getBetaDuration() * travelTimeMinutes
            + parameters.getBetaFare() * fareBdt;
    }
}
