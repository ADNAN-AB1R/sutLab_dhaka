package org.dhaka.mode_choice.parameters;

import java.io.File;
import java.io.IOException;
import java.util.HashMap;
import java.util.Map;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.google.inject.Singleton;

/**
 * Loads dhaka/mode_choice/dhaka_mode_parameters.json - the estimated mode
 * choice model from Hoque's MSc thesis (paper/Ismamul Hoque Msc Thesis.pdf,
 * Table 4.1). Utility is deliberately simple, matching the thesis's actual
 * specification exactly (not a staged simplification of a richer model -
 * the thesis's mode-choice component has no socio-demographic terms at all,
 * confirmed by reading its methodology):
 *
 *   U[mode] = asc[mode] + betaDuration*travelTimeMinutes + betaFare*fareBdt
 *
 * with `bike` as the reference mode (implicit U contribution from asc is 0).
 * betaDuration/betaFare are pooled across every mode in the thesis, not
 * mode-specific - only the ASC varies by mode. See
 * org.dhaka.mode_choice.utilities.estimators for where this is actually
 * applied per mode, and DhakaCostParameters for the separate fare
 * construction (kept apart from these estimated behavioral coefficients).
 *
 * Path is CWD-relative (../dhaka/mode_choice/...), matching this project's
 * established convention of running from output/ - see matsim_run/README.md.
 */
@Singleton
public class DhakaModeParameters {
    private static final String PATH = "../dhaka/mode_choice/dhaka_mode_parameters.json";

    private final String referenceMode;
    private final double betaDuration;
    private final double betaFare;
    private final Map<String, Double> asc;

    @SuppressWarnings("unchecked")
    public DhakaModeParameters() {
        try {
            ObjectMapper mapper = new ObjectMapper();
            Map<String, Object> raw = mapper.readValue(new File(PATH), Map.class);

            this.referenceMode = (String) raw.get("reference_mode");
            this.betaDuration = ((Number) raw.get("beta_duration_per_minute")).doubleValue();
            this.betaFare = ((Number) raw.get("beta_fare_per_bdt")).doubleValue();

            this.asc = new HashMap<>();
            for (Map.Entry<String, Object> entry : ((Map<String, Object>) raw.get("asc")).entrySet()) {
                this.asc.put(entry.getKey(), ((Number) entry.getValue()).doubleValue());
            }
        } catch (IOException e) {
            throw new RuntimeException("Could not load Dhaka mode parameters from " + PATH, e);
        }
    }

    public String getReferenceMode() {
        return referenceMode;
    }

    public double getBetaDuration() {
        return betaDuration;
    }

    public double getBetaFare() {
        return betaFare;
    }

    public double getAsc(String mode) {
        return asc.getOrDefault(mode, 0.0);
    }
}
