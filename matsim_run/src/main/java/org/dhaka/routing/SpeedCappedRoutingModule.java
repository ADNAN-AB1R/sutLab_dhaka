package org.dhaka.routing;

import java.util.LinkedHashMap;
import java.util.Map;

import com.google.inject.Inject;
import com.google.inject.name.Named;

import org.matsim.api.core.v01.Scenario;
import org.matsim.core.controler.AbstractModule;
import org.matsim.core.router.util.TravelTime;
import org.matsim.core.trafficmonitoring.TravelTimeCalculator;
import org.matsim.vehicles.VehicleType;

/**
 * Binds a {@link SpeedCappedTravelTime} for every network mode whose vehicle
 * type declares a finite maximumVelocity, so the router plans those modes at
 * their real top speed instead of at car speed.
 *
 * The caps are read from the scenario's vehicle types - the same file the
 * QSim reads (dhaka/matsim/assemble_scenario.py's ROAD_VEHICLE_TYPES) - so the
 * speed the router plans with can never drift from the speed the simulation
 * drives at. Modes without a cap (car) keep MATSim's default binding.
 *
 * Installed as an overriding module, the same way MATSim's own bicycle
 * extension replaces the travel time for its mode.
 */
public class SpeedCappedRoutingModule extends AbstractModule {
    private final Map<String, Double> cappedModes = new LinkedHashMap<>();

    public SpeedCappedRoutingModule(Scenario scenario) {
        for (VehicleType type : scenario.getVehicles().getVehicleTypes().values()) {
            String mode = type.getNetworkMode();
            double maximumVelocity = type.getMaximumVelocity();
            if (mode != null && !Double.isInfinite(maximumVelocity) && maximumVelocity > 0) {
                cappedModes.put(mode, maximumVelocity);
            }
        }
    }

    public Map<String, Double> getCappedModes() {
        return cappedModes;
    }

    @Override
    public void install() {
        for (Map.Entry<String, Double> entry : cappedModes.entrySet()) {
            addTravelTimeBinding(entry.getKey()).toProvider(new CappedProvider(entry.getValue()));
        }
    }

    private static class CappedProvider implements com.google.inject.Provider<TravelTime> {
        private final double maximumVelocity;

        // MATSim's defaults (TravelTimeCalculatorConfigGroup: separateModes =
        // true, analyzedModes = {car}) observe link times for car only and
        // bind every other network mode to plain FreeSpeedTravelTime - which
        // ignores the vehicle, hence rickshaws planned at ~38 km/h. The car
        // calculator is bound under the name "car"; its observed times carry
        // the congestion all road modes share, and the cap then enforces each
        // vehicle's own top speed.
        @Inject
        @Named("car")
        private TravelTimeCalculator travelTimeCalculator;

        CappedProvider(double maximumVelocity) {
            this.maximumVelocity = maximumVelocity;
        }

        @Override
        public TravelTime get() {
            return new SpeedCappedTravelTime(travelTimeCalculator.getLinkTravelTimes(), maximumVelocity);
        }
    }
}
