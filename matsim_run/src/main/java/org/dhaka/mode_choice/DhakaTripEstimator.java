package org.dhaka.mode_choice;

import java.util.Collections;
import java.util.List;
import java.util.Map;

import com.google.inject.Inject;

import org.matsim.api.core.v01.Coord;
import org.matsim.api.core.v01.population.Activity;
import org.matsim.api.core.v01.population.Leg;
import org.matsim.api.core.v01.population.Person;
import org.matsim.api.core.v01.population.PlanElement;
import org.matsim.api.core.v01.population.Route;
import org.matsim.contribs.discrete_mode_choice.components.estimators.AbstractTripRouterEstimator;
import org.matsim.contribs.discrete_mode_choice.model.DiscreteModeChoiceTrip;
import org.matsim.contribs.discrete_mode_choice.model.trip_based.candidates.TripCandidate;
import org.matsim.core.router.TripRouter;
import org.matsim.core.utils.geometry.CoordUtils;
import org.matsim.core.utils.timing.TimeInterpretation;
import org.matsim.facilities.ActivityFacilities;

import org.dhaka.mode_choice.parameters.DhakaModeParameters;
import org.dhaka.mode_choice.utilities.estimators.DhakaUtilityEstimator;

/**
 * The actual MATSim discrete_mode_choice TripEstimator binding (bound under
 * the name "Fitted" - see DhakaModeChoiceModule and config.xml's
 * DiscreteModeChoice module's tripEstimator param in
 * dhaka/matsim/assemble_scenario.py). A thin dispatcher: computes real
 * routed travel time/distance for the candidate via
 * AbstractTripRouterEstimator (same routing mechanism as before), then
 * delegates the actual utility formula to the mode-specific class in
 * utilities/estimators/.
 *
 * This dispatch layer is the vendored equivalent of what org.eqasim.core's
 * own generic utility-estimator-binding machinery provides for real eqasim
 * modules (e.g. how SaoPauloModeChoiceModule's per-mode estimators get
 * combined) - we don't depend on org.eqasim:core (see matsim_run/README.md:
 * no eqasim-java release targets our pinned MATSim 2025.0), so this small
 * piece is hand-written rather than inherited from that framework.
 */
public class DhakaTripEstimator extends AbstractTripRouterEstimator {
    private final Map<String, DhakaUtilityEstimator> estimators;
    private final DhakaModeParameters parameters;

    @Inject
    public DhakaTripEstimator(TripRouter tripRouter, ActivityFacilities facilities,
            TimeInterpretation timeInterpretation, Map<String, DhakaUtilityEstimator> estimators,
            DhakaModeParameters parameters) {
        super(tripRouter, facilities, timeInterpretation, Collections.emptyList());
        this.estimators = estimators;
        this.parameters = parameters;
    }

    @Override
    protected double estimateTrip(Person person, String mode, DiscreteModeChoiceTrip trip,
            List<TripCandidate> previousTrips, List<? extends PlanElement> routedTrip) {
        // Travel TIME is the routed time, summed over every leg including
        // access/egress walks - that is what routing is for, and it is where
        // congestion enters mode choice.
        double travelTimeMinutes = 0.0;
        double routedDistanceMeters = 0.0;

        for (PlanElement element : routedTrip) {
            if (element instanceof Leg) {
                Leg leg = (Leg) element;
                travelTimeMinutes += leg.getTravelTime().orElse(0.0) / 60.0;

                Route route = leg.getRoute();
                if (route != null) {
                    routedDistanceMeters += route.getDistance();
                }
            }
        }

        // Fare DISTANCE is the straight-line origin-destination distance, NOT
        // the routed one. The fare rates in dhaka_mode_parameters.json were
        // calibrated per straight-line km (dhaka/mode_choice/calibrate_fares.py
        // fits the survey's reported cost against O-D distance), and the
        // Python seed stage and precalibrate_asc.py apply them that way.
        // Charging them per routed km instead - route.getDistance() summed
        // over all legs, access/egress walks included - overcharged every
        // paid mode by the network detour ratio (~1.65x for road modes): a
        // 4 km car trip cost 282 BDT here against 171 in calibration, about
        // -3.8 utils. That collapsed car in the first calibration run on
        // 2026-09-18 while the offline model reproduced targets exactly.
        double fareDistanceMeters = straightLineDistance(trip);
        if (Double.isNaN(fareDistanceMeters)) {
            fareDistanceMeters = routedDistanceMeters;
        }

        DhakaUtilityEstimator estimator = estimators.get(mode);
        if (estimator != null) {
            return estimator.estimateUtility(person, travelTimeMinutes, fareDistanceMeters);
        }

        // Graceful fallback for modes outside the 6-mode milestone (e.g.
        // "other" - a small number of seed-assignment trips keep their
        // original HTS-donor mode when dhaka/synthesis/population/
        // mode_choice.py can't resolve a real distance for them; see that
        // file's compute_trip_distances/apply_model). DMC evaluates a
        // trip's EXISTING assigned mode as part of the candidate set, not
        // just the modes actually offered as new alternatives (those are
        // controlled separately by config.xml's modeAvailability:Car
        // availableModes list, which never lists "other"), so this has to
        // degrade rather than throw. Treated like the reference mode
        // (time-only, no ASC/fare) - confirmed rare enough (a handful of
        // trips) that the exact fallback value has negligible effect, and
        // it can never be freshly CHOSEN as a new candidate mode anyway.
        return parameters.getBetaDuration() * travelTimeMinutes;
    }

    /** Straight-line distance between the trip's origin and destination
     * activities, or NaN if either has no coordinate (the caller then falls
     * back to the routed distance). Dhaka population activities always carry
     * x/y, so the fallback exists only for robustness. */
    private static double straightLineDistance(DiscreteModeChoiceTrip trip) {
        Activity origin = trip.getOriginActivity();
        Activity destination = trip.getDestinationActivity();
        Coord from = origin == null ? null : origin.getCoord();
        Coord to = destination == null ? null : destination.getCoord();
        if (from == null || to == null) {
            return Double.NaN;
        }
        return CoordUtils.calcEuclideanDistance(from, to);
    }
}
