package org.dhaka.mode_choice.parameters;

import java.io.File;
import java.io.IOException;
import java.util.Map;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.google.inject.Singleton;

/**
 * Pure data holder for the scenario's fare/cost-calculation inputs - kept
 * separate from DhakaModeParameters (the estimated behavioral coefficients)
 * per the migration plan's own principle: beta_fare belongs to the estimated
 * choice model, but how a BDT fare is actually computed for a given trip is
 * a scenario-specific calculation, mirroring eqasim's own
 * ModeParameters/CostParameters split (e.g. SaoPauloModeParameters vs
 * SaoPauloCostParameters).
 *
 * Loads dhaka/mode_choice/dhaka_mode_parameters.json's "fare_assumptions"
 * section. See that file for each mode's source/justification - several are
 * documented assumptions filling real gaps the thesis leaves open (e.g.
 * rickshaw's rate has no formula in the thesis at all), not thesis-derived
 * figures themselves.
 */
@Singleton
public class DhakaCostParameters {
    private static final String PATH = "../dhaka/mode_choice/dhaka_mode_parameters.json";

    private final double carFuelPriceBdtPerLiter;
    private final double carKmPerLiter;

    private final double motorcycleFuelPriceBdtPerLiter;
    private final double motorcycleKmPerLiter;

    private final double ptBdtPerKm;
    private final double ptMinimumFareBdt;

    private final double paratransitFlagFallBdt;
    private final double paratransitFlagFallKm;
    private final double paratransitBdtPerKmAfter;

    private final double rickshawBdtPerKm;

    @SuppressWarnings("unchecked")
    public DhakaCostParameters() {
        try {
            ObjectMapper mapper = new ObjectMapper();
            Map<String, Object> raw = mapper.readValue(new File(PATH), Map.class);
            Map<String, Object> fareAssumptions = (Map<String, Object>) raw.get("fare_assumptions");

            Map<String, Object> car = (Map<String, Object>) fareAssumptions.get("car");
            this.carFuelPriceBdtPerLiter = ((Number) car.get("fuel_price_bdt_per_liter")).doubleValue();
            this.carKmPerLiter = ((Number) car.get("km_per_liter")).doubleValue();

            Map<String, Object> motorcycle = (Map<String, Object>) fareAssumptions.get("motorcycle");
            this.motorcycleFuelPriceBdtPerLiter = ((Number) motorcycle.get("fuel_price_bdt_per_liter")).doubleValue();
            this.motorcycleKmPerLiter = ((Number) motorcycle.get("km_per_liter")).doubleValue();

            Map<String, Object> pt = (Map<String, Object>) fareAssumptions.get("pt");
            this.ptBdtPerKm = ((Number) pt.get("bdt_per_km")).doubleValue();
            this.ptMinimumFareBdt = ((Number) pt.get("minimum_fare_bdt")).doubleValue();

            Map<String, Object> paratransit = (Map<String, Object>) fareAssumptions.get("paratransit");
            this.paratransitFlagFallBdt = ((Number) paratransit.get("flag_fall_bdt")).doubleValue();
            this.paratransitFlagFallKm = ((Number) paratransit.get("flag_fall_km")).doubleValue();
            this.paratransitBdtPerKmAfter = ((Number) paratransit.get("bdt_per_km_after")).doubleValue();

            Map<String, Object> rickshaw = (Map<String, Object>) fareAssumptions.get("rickshaw");
            this.rickshawBdtPerKm = ((Number) rickshaw.get("bdt_per_km")).doubleValue();
        } catch (IOException e) {
            throw new RuntimeException("Could not load Dhaka cost parameters from " + PATH, e);
        }
    }

    public double getCarFuelPriceBdtPerLiter() {
        return carFuelPriceBdtPerLiter;
    }

    public double getCarKmPerLiter() {
        return carKmPerLiter;
    }

    public double getMotorcycleFuelPriceBdtPerLiter() {
        return motorcycleFuelPriceBdtPerLiter;
    }

    public double getMotorcycleKmPerLiter() {
        return motorcycleKmPerLiter;
    }

    public double getPtBdtPerKm() {
        return ptBdtPerKm;
    }

    public double getPtMinimumFareBdt() {
        return ptMinimumFareBdt;
    }

    public double getParatransitFlagFallBdt() {
        return paratransitFlagFallBdt;
    }

    public double getParatransitFlagFallKm() {
        return paratransitFlagFallKm;
    }

    public double getParatransitBdtPerKmAfter() {
        return paratransitBdtPerKmAfter;
    }

    public double getRickshawBdtPerKm() {
        return rickshawBdtPerKm;
    }
}
