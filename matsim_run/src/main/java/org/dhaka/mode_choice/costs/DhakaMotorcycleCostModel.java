package org.dhaka.mode_choice.costs;

import com.google.inject.Inject;

import org.dhaka.mode_choice.parameters.DhakaCostParameters;

/**
 * Fuel-price-based operating cost, same construction as car but at a
 * motorcycle's much better fuel economy (~35 km/l vs ~12 km/l), so roughly
 * a third of car's cost per km. Separating this out was a large part of the
 * reason motorcycle was split out of the car bucket at all: previously ~64%
 * of "car" trips were actually motorcycles being charged car fuel cost. As
 * with car, the thesis states the fuel price (122 BDT/liter) but not the
 * consumption rate - km_per_liter is a documented placeholder, see
 * dhaka_mode_parameters.json.
 */
public class DhakaMotorcycleCostModel {
    private final DhakaCostParameters parameters;

    @Inject
    public DhakaMotorcycleCostModel(DhakaCostParameters parameters) {
        this.parameters = parameters;
    }

    public double calculateCost_bdt(double distanceMeters) {
        double bdtPerKm = parameters.getMotorcycleFuelPriceBdtPerLiter()
            / parameters.getMotorcycleKmPerLiter();
        return bdtPerKm * (distanceMeters / 1000.0);
    }
}
