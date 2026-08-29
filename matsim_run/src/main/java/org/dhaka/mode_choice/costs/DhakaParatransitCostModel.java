package org.dhaka.mode_choice.costs;

import com.google.inject.Inject;

import org.dhaka.mode_choice.parameters.DhakaCostParameters;

/**
 * Official BRTA CNG auto-rickshaw meter rate (fixed 2015, still officially
 * in force): flag-fall for the first 2 km, then a per-km rate after. The
 * thesis includes CNG as a mode but specifies NO fare construction for it
 * anywhere in its text (confirmed via full-text search) - this rate is
 * sourced from BRTA regulation, not the thesis. Real-world drivers
 * frequently ignore the meter in practice; treat as an approximation.
 */
public class DhakaParatransitCostModel {
    private final DhakaCostParameters parameters;

    @Inject
    public DhakaParatransitCostModel(DhakaCostParameters parameters) {
        this.parameters = parameters;
    }

    public double calculateCost_bdt(double distanceMeters) {
        double distanceKm = distanceMeters / 1000.0;
        double flagFallKm = parameters.getParatransitFlagFallKm();

        if (distanceKm <= flagFallKm) {
            return parameters.getParatransitFlagFallBdt();
        }

        return parameters.getParatransitFlagFallBdt()
            + parameters.getParatransitBdtPerKmAfter() * (distanceKm - flagFallKm);
    }
}
