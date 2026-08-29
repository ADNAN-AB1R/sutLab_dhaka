package org.dhaka.mode_choice.utilities.estimators;

import com.google.inject.Inject;

import org.dhaka.mode_choice.costs.DhakaMotorcycleCostModel;
import org.dhaka.mode_choice.parameters.DhakaModeParameters;

/**
 * Motorcycle - a COMPOSITE of the thesis's Private Motorcycle (ASC 1.87,
 * 94% of the bucket) and Ride-share Bike (ASC -0.03, 6%); its ASC in
 * dhaka_mode_parameters.json is the share-weighted 1.758, with the raw
 * trip counts behind that weighting recorded in the file's "composition"
 * section.
 */
public class DhakaMotorcycleUtilityEstimator implements DhakaUtilityEstimator {
    private final DhakaModeParameters parameters;
    private final DhakaMotorcycleCostModel costModel;

    @Inject
    public DhakaMotorcycleUtilityEstimator(DhakaModeParameters parameters,
            DhakaMotorcycleCostModel costModel) {
        this.parameters = parameters;
        this.costModel = costModel;
    }

    @Override
    public double estimateUtility(double travelTimeMinutes, double distanceMeters) {
        double fareBdt = costModel.calculateCost_bdt(distanceMeters);
        return parameters.getAsc("motorcycle")
            + parameters.getBetaDuration() * travelTimeMinutes
            + parameters.getBetaFare() * fareBdt;
    }
}
