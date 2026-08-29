package org.dhaka.mode_choice.utilities.estimators;

/**
 * A single mode's utility function, matching the thesis's specification
 * (U = asc + betaDuration*time + betaFare*fare, all pooled except asc).
 * No Person parameter - the thesis's mode-choice component has no
 * socio-demographic terms at all (confirmed by reading its methodology),
 * so nothing here needs it. Bound one-per-mode in DhakaModeChoiceModule and
 * dispatched by DhakaTripEstimator based on the candidate mode string.
 */
public interface DhakaUtilityEstimator {
    double estimateUtility(double travelTimeMinutes, double distanceMeters);
}
