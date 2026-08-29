package org.dhaka.mode_choice.utilities.estimators;

import com.google.inject.Inject;

import org.dhaka.mode_choice.costs.DhakaParatransitCostModel;
import org.dhaka.mode_choice.parameters.DhakaModeParameters;

public class DhakaParatransitUtilityEstimator implements DhakaUtilityEstimator {
    private final DhakaModeParameters parameters;
    private final DhakaParatransitCostModel costModel;

    @Inject
    public DhakaParatransitUtilityEstimator(DhakaModeParameters parameters, DhakaParatransitCostModel costModel) {
        this.parameters = parameters;
        this.costModel = costModel;
    }

    @Override
    public double estimateUtility(double travelTimeMinutes, double distanceMeters) {
        double fareBdt = costModel.calculateCost_bdt(distanceMeters);
        return parameters.getAsc("paratransit")
            + parameters.getBetaDuration() * travelTimeMinutes
            + parameters.getBetaFare() * fareBdt;
    }
}
