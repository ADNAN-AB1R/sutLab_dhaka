package org.dhaka.mode_choice.parameters;

import java.io.File;
import java.io.IOException;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.google.inject.Singleton;

import org.matsim.api.core.v01.population.Person;

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

    // beta_fare multiplier per ordinal income class (index = class 0-8),
    // precomputed from "income_scaling" so estimating a trip costs an array
    // lookup rather than a Math.pow.
    private final double[] fareScaleByIncomeClass;

    // mode -> {person attribute holding the household's vehicle count,
    // constant added when that count is zero}. See "ownership_constants".
    private final Map<String, String> ownershipAttribute;
    private final Map<String, Double> nonOwnerConstant;

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

            Map<String, Object> scaling = (Map<String, Object>) raw.get("income_scaling");
            double elasticity = ((Number) scaling.get("elasticity")).doubleValue();
            double referenceIncome = ((Number) scaling.get("reference_income_bdt_per_month")).doubleValue();
            List<Number> midpoints = (List<Number>) scaling.get("class_midpoints_bdt_per_month");

            this.fareScaleByIncomeClass = new double[midpoints.size()];
            for (int i = 0; i < midpoints.size(); i++) {
                this.fareScaleByIncomeClass[i] =
                    Math.pow(midpoints.get(i).doubleValue() / referenceIncome, -elasticity);
            }

            this.ownershipAttribute = new HashMap<>();
            this.nonOwnerConstant = new HashMap<>();
            Map<String, Object> ownership = (Map<String, Object>) raw.get("ownership_constants");
            for (Map.Entry<String, Object> entry : ownership.entrySet()) {
                if (!(entry.getValue() instanceof Map)) {
                    continue; // the "source" note
                }
                Map<String, Object> spec = (Map<String, Object>) entry.getValue();
                this.ownershipAttribute.put(entry.getKey(), (String) spec.get("household_attribute"));
                this.nonOwnerConstant.put(entry.getKey(),
                    ((Number) spec.get("non_owner_constant")).doubleValue());
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

    /**
     * Cost sensitivity for this person: beta_fare scaled by household income,
     * beta_fare * (income / reference_income) ^ (-elasticity), per
     * dhaka_mode_parameters.json's "income_scaling".
     *
     * Hoque's model pools one beta_fare over everyone, implying a single VTTS
     * of 197.6 BDT/h - 4.2x the implied wage for the Tk 10-20k bracket and
     * 0.49x for the top one. Scaling cost sensitivity with income redistributes
     * that VTTS towards what each traveller can plausibly pay; it leaves the
     * value unchanged for the median-income household, so the transfer is
     * preserved at the centre of the distribution.
     *
     * Reads the ordinal `householdIncome` person attribute written by
     * dhaka/income.py (0-8, -1 = income not stated). Unknown or out-of-range
     * classes get the unscaled beta_fare.
     *
     * Deliberately the ONLY accessor for beta_fare: an unscaled getter would
     * let a new estimator silently skip the income scaling.
     */
    public double getBetaFare(Person person) {
        Object attribute = person.getAttributes().getAttribute("householdIncome");
        if (!(attribute instanceof Number)) {
            return betaFare;
        }
        int incomeClass = (int) Math.round(((Number) attribute).doubleValue());
        if (incomeClass < 0 || incomeClass >= fareScaleByIncomeClass.length) {
            return betaFare;
        }
        return betaFare * fareScaleByIncomeClass[incomeClass];
    }

    /**
     * Vehicle-ownership constant for this person and mode: the mode's
     * non_owner_constant if the person's household owns none of that vehicle
     * type, otherwise 0. See dhaka_mode_parameters.json "ownership_constants".
     *
     * A utility penalty rather than a hard availability gate, deliberately:
     * in the DTCA survey non-owning households still make 14.1% of car,
     * 7.1% of motorcycle and 18.4% of bike trips (staff/company cars,
     * ride-hailing, chauffeured or family vehicles, borrowed bikes). This
     * replaces DiscreteModeChoice's built-in "Car" availability, which gated
     * car on a driving licence held by 4.9% of persons while 60.3% of car
     * trips are made by people without one.
     *
     * A person with no ownership attribute at all gets 0 (treated as an
     * owner), so an older population file degrades to the old behaviour
     * rather than silently penalising everyone.
     */
    public double getOwnershipConstant(Person person, String mode) {
        String attributeName = ownershipAttribute.get(mode);
        if (attributeName == null) {
            return 0.0;
        }
        Object attribute = person.getAttributes().getAttribute(attributeName);
        if (!(attribute instanceof Number)) {
            return 0.0;
        }
        return ((Number) attribute).intValue() == 0 ? nonOwnerConstant.get(mode) : 0.0;
    }

    public double getAsc(String mode) {
        return asc.getOrDefault(mode, 0.0);
    }
}
