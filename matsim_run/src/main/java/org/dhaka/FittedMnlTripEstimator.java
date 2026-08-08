package org.dhaka;

import java.util.Collections;
import java.util.List;

import com.google.inject.Inject;

import org.matsim.api.core.v01.population.Leg;
import org.matsim.api.core.v01.population.Person;
import org.matsim.api.core.v01.population.PlanElement;
import org.matsim.contribs.discrete_mode_choice.components.estimators.AbstractTripRouterEstimator;
import org.matsim.contribs.discrete_mode_choice.model.DiscreteModeChoiceTrip;
import org.matsim.contribs.discrete_mode_choice.model.trip_based.candidates.TripCandidate;
import org.matsim.core.population.PersonUtils;
import org.matsim.core.router.TripRouter;
import org.matsim.core.utils.timing.TimeInterpretation;
import org.matsim.facilities.ActivityFacilities;

/**
 * The in-simulation counterpart of dhaka/synthesis/population/
 * mode_choice.py's apply_model(): identical linear-utility MNL
 * (U = beta_time*time + asc[mode] + sum(coefficients[cov][mode]*value)),
 * same fitted_coefficients.json (via FittedCoefficients), same reference
 * mode with implicit U=0. The one real difference - and the whole point
 * of wiring in discrete_mode_choice - is where `time` comes from: not a
 * distance/fallback-speed proxy computed once at synthesis time, but the
 * actual routed/simulated travel time for THIS candidate alternative in
 * THIS iteration (summed from routedTrip's Legs, via AbstractTripRouterEstimator
 * having already routed the candidate through TripRouter before calling
 * this method).
 *
 * Person covariates are read from the same attributes dhaka/matsim/scenario/
 * population.py already writes into population.xml: "householdIncome"
 * (doubles as income_class per dhaka/income.py's documented convention),
 * "age", "sex" (single-letter code), and license via PersonUtils.getLicense()
 * (confirmed by decompiling matsim-core: it reads the "hasLicense" attribute
 * key, which population.py also already writes - same attribute the
 * built-in CarModeAvailability component (config.xml's modeAvailability="Car")
 * uses to gate the car alternative).
 */
public class FittedMnlTripEstimator extends AbstractTripRouterEstimator {
    private final FittedCoefficients model;

    @Inject
    public FittedMnlTripEstimator(TripRouter tripRouter, ActivityFacilities facilities,
            TimeInterpretation timeInterpretation, FittedCoefficients model) {
        super(tripRouter, facilities, timeInterpretation, Collections.emptyList());
        this.model = model;
    }

    @Override
    protected double estimateTrip(Person person, String mode, DiscreteModeChoiceTrip trip,
            List<TripCandidate> previousTrips, List<? extends PlanElement> routedTrip) {
        double travelTimeMinutes = 0.0;

        for (PlanElement element : routedTrip) {
            if (element instanceof Leg) {
                travelTimeMinutes += ((Leg) element).getTravelTime().orElse(0.0) / 60.0;
            }
        }

        double utility = model.getBetaTime() * travelTimeMinutes;

        if (!mode.equals(model.getReferenceMode())) {
            utility += model.getAsc(mode);

            Object incomeAttribute = person.getAttributes().getAttribute("householdIncome");
            Object ageAttribute = person.getAttributes().getAttribute("age");
            Object sexAttribute = person.getAttributes().getAttribute("sex");
            String license = PersonUtils.getLicense(person);

            double incomeClass = incomeAttribute == null ? 0.0 : ((Number) incomeAttribute).doubleValue();
            double age = ageAttribute == null ? 0.0 : ((Number) ageAttribute).doubleValue();
            double female = (sexAttribute != null && "f".equalsIgnoreCase(sexAttribute.toString())) ? 1.0 : 0.0;
            double hasLicense = "yes".equalsIgnoreCase(license) ? 1.0 : 0.0;

            utility += model.getCoefficient("income_class", mode) * incomeClass;
            utility += model.getCoefficient("age", mode) * age;
            utility += model.getCoefficient("female", mode) * female;
            utility += model.getCoefficient("has_license", mode) * hasLicense;
        }

        return utility;
    }
}
