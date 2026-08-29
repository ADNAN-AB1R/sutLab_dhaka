package org.dhaka.mode_choice.costs;

import com.google.inject.Inject;

import org.dhaka.mode_choice.parameters.DhakaCostParameters;

/**
 * UNVERIFIED PLACEHOLDER. The thesis explicitly states rickshaw fare was
 * "assigned based on local travel experience and prevailing fare practices"
 * with no formula given - the flat Tk/km rate here is a rough, commonly-
 * cited ballpark for negotiated Dhaka rickshaw fares, not a sourced figure.
 * Replace with a real one if it becomes available (see
 * dhaka_mode_parameters.json's "fare_assumptions.rickshaw").
 */
public class DhakaRickshawCostModel {
    private final DhakaCostParameters parameters;

    @Inject
    public DhakaRickshawCostModel(DhakaCostParameters parameters) {
        this.parameters = parameters;
    }

    public double calculateCost_bdt(double distanceMeters) {
        return parameters.getRickshawBdtPerKm() * (distanceMeters / 1000.0);
    }
}
