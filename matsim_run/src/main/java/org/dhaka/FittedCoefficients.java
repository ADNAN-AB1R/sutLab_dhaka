package org.dhaka;

import java.io.File;
import java.io.IOException;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.google.inject.Singleton;

/**
 * Loads dhaka/mode_choice/fitted_coefficients.json - the same file
 * dhaka/synthesis/population/mode_choice.py applies once at population-
 * synthesis time, now also consumed here by FittedMnlTripEstimator for
 * in-simulation replanning (discrete_mode_choice). @Singleton so the file
 * is parsed once regardless of how many FittedMnlTripEstimator instances
 * Guice creates.
 *
 * Path is CWD-relative, not relative to this class or config.xml: this
 * project's established run convention is to `cd` into output/ before
 * running (see matsim_run/README.md - config.xml's own relative paths are
 * resolved against the invocation CWD the same way), so
 * ../dhaka/mode_choice/ from there reaches the real repo-root file.
 */
@Singleton
public class FittedCoefficients {
    private static final String PATH = "../dhaka/mode_choice/fitted_coefficients.json";

    private final List<String> modes;
    private final String referenceMode;
    private final double betaTime;
    private final Map<String, Double> asc;
    private final Map<String, Map<String, Double>> coefficients;

    @SuppressWarnings("unchecked")
    public FittedCoefficients() {
        try {
            ObjectMapper mapper = new ObjectMapper();
            Map<String, Object> raw = mapper.readValue(new File(PATH), Map.class);

            this.modes = (List<String>) raw.get("modes");
            this.referenceMode = (String) raw.get("reference_mode");
            this.betaTime = ((Number) raw.get("beta_time")).doubleValue();

            this.asc = new HashMap<>();
            for (Map.Entry<String, Object> entry : ((Map<String, Object>) raw.get("asc")).entrySet()) {
                this.asc.put(entry.getKey(), ((Number) entry.getValue()).doubleValue());
            }

            this.coefficients = new HashMap<>();
            for (Map.Entry<String, Object> covEntry : ((Map<String, Object>) raw.get("coefficients")).entrySet()) {
                Map<String, Double> modeMap = new HashMap<>();
                for (Map.Entry<String, Object> modeEntry : ((Map<String, Object>) covEntry.getValue()).entrySet()) {
                    modeMap.put(modeEntry.getKey(), ((Number) modeEntry.getValue()).doubleValue());
                }
                this.coefficients.put(covEntry.getKey(), modeMap);
            }
        } catch (IOException e) {
            throw new RuntimeException("Could not load fitted mode choice coefficients from " + PATH, e);
        }
    }

    public List<String> getModes() {
        return modes;
    }

    public String getReferenceMode() {
        return referenceMode;
    }

    public double getBetaTime() {
        return betaTime;
    }

    public double getAsc(String mode) {
        return asc.getOrDefault(mode, 0.0);
    }

    public double getCoefficient(String covariate, String mode) {
        Map<String, Double> modeMap = coefficients.get(covariate);
        return modeMap == null ? 0.0 : modeMap.getOrDefault(mode, 0.0);
    }
}
