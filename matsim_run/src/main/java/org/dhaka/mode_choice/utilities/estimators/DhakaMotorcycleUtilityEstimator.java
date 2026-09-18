package org.dhaka.mode_choice.utilities.estimators;

import org.matsim.api.core.v01.population.Person;

import com.google.inject.Inject;

import org.dhaka.mode_choice.costs.DhakaMotorcycleCostModel;
import org.dhaka.mode_choice.parameters.DhakaModeParameters;

/**
 * Motorcycle - a COMPOSITE of the thesis's Private Motorcycle (ASC 1.87,
 * 94% of the bucket) and Ride-share Bike (ASC -0.03, 6%), giving a
 * share-weighted thesis ASC of 1.758; the raw trip counts behind that
 * weighting are recorded in dhaka_mode_parameters.json's "composition"
 * section.
 *
 * The ASC this class actually reads from that file is NOT 1.758 - the
 * published constants are re-anchored to Dhaka's observed all-trip mode
 * split by ASC calibration (see matsim_run/README.md's two calibration
 * stages). The composite weighting above is provenance for where the
 * calibration STARTED, not the value in force.
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
    public double estimateUtility(Person person, double travelTimeMinutes, double distanceMeters) {
        double fareBdt = costModel.calculateCost_bdt(distanceMeters);
        return parameters.getAsc("motorcycle")
            + parameters.getBetaDuration() * travelTimeMinutes
            + parameters.getBetaFare(person) * fareBdt;
    }
}
