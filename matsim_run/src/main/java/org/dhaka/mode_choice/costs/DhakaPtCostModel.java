package org.dhaka.mode_choice.costs;

import com.google.inject.Inject;

import org.dhaka.mode_choice.parameters.DhakaCostParameters;

/**
 * BRTA-regulated Dhaka city bus rate + minimum fare. The thesis says bus
 * fare uses "government fare basis" but states no number - this is the
 * actual current regulation (~2.4-2.5 Tk/km, ~10 Tk minimum), not
 * thesis-sourced.
 */
public class DhakaPtCostModel {
    private final DhakaCostParameters parameters;

    @Inject
    public DhakaPtCostModel(DhakaCostParameters parameters) {
        this.parameters = parameters;
    }

    public double calculateCost_bdt(double distanceMeters) {
        double distanceKm = distanceMeters / 1000.0;
        return Math.max(parameters.getPtMinimumFareBdt(), parameters.getPtBdtPerKm() * distanceKm);
    }
}
