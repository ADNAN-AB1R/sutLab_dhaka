package org.dhaka.mode_choice.costs;

import com.google.inject.Inject;

import org.dhaka.mode_choice.parameters.DhakaCostParameters;

/**
 * Fuel-price-based operating cost - the thesis states the fuel price
 * (122 BDT/liter, contextual Dhaka price) but not the assumed consumption
 * rate; km_per_liter is a documented placeholder (see
 * dhaka_mode_parameters.json), not thesis-sourced.
 */
public class DhakaCarCostModel {
    private final DhakaCostParameters parameters;

    @Inject
    public DhakaCarCostModel(DhakaCostParameters parameters) {
        this.parameters = parameters;
    }

    public double calculateCost_bdt(double distanceMeters) {
        double bdtPerKm = parameters.getCarFuelPriceBdtPerLiter() / parameters.getCarKmPerLiter();
        return bdtPerKm * (distanceMeters / 1000.0);
    }
}
