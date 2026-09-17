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

    // Effective BDT/km, calibrated against the DTCA survey's reported trip
    // costs by dhaka/mode_choice/calibrate_fares.py. Replaces the previous
    // fuel_price/km_per_liter construction, which could only ever express
    // private fuel burn and so understated observed cost 5.1x (car) and 3.6x
    // (motorcycle) - those buckets are substantially ride-hailing, and Dhaka
    // private cars commonly carry a hired driver's wage and parking too.
    private final double carBdtPerKm;
    private final double motorcycleBdtPerKm;

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
            this.carBdtPerKm = ((Number) car.get("bdt_per_km")).doubleValue();

            Map<String, Object> motorcycle = (Map<String, Object>) fareAssumptions.get("motorcycle");
            this.motorcycleBdtPerKm = ((Number) motorcycle.get("bdt_per_km")).doubleValue();

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

    public double getCarBdtPerKm() {
        return carBdtPerKm;
    }

    public double getMotorcycleBdtPerKm() {
        return motorcycleBdtPerKm;
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
