package org.dhaka.routing;

import org.matsim.api.core.v01.network.Link;
import org.matsim.api.core.v01.population.Person;
import org.matsim.core.router.util.TravelTime;
import org.matsim.vehicles.Vehicle;

/**
 * Travel time for a network mode whose vehicles have a top speed below what
 * the road allows: the slower of the underlying (observed, congested) link
 * time and the time to cross the link at the vehicle's maximum velocity.
 *
 * Why this exists: the QSim enforces each vehicle type's maximumVelocity when
 * it DRIVES a vehicle, but MATSim's router does not when it PLANS a route - it
 * plans every network mode on the same observed/free-speed link times, and the
 * routes it builds carry no vehicle (vehicleRefId="null"), so nothing
 * vehicle-specific can reach the default travel time. Measured on the
 * 2026-09-18 run: the router expected rickshaw legs at a median 38 km/h
 * (p90 62) and bike at 42 km/h, against caps of 12 and 15 km/h - one 11.4 km
 * rickshaw leg was planned at 11 min 40 s. Mode choice (DhakaTripEstimator
 * reads routed leg times) therefore saw a rickshaw as fast as a car but far
 * cheaper, while the simulation then drove it at 12 km/h. That produced the
 * rickshaw and bike surge (rickshaw 30% -> 41% in one iteration) and the
 * matching losses for car, pt and motorcycle that survived every proxy and
 * fare correction made on the Python side.
 *
 * Congestion is still respected: on a jammed link the observed time exceeds
 * the free-flow cap time and wins.
 */
public class SpeedCappedTravelTime implements TravelTime {
    private final TravelTime base;
    private final double maximumVelocity_m_s;

    public SpeedCappedTravelTime(TravelTime base, double maximumVelocity_m_s) {
        this.base = base;
        this.maximumVelocity_m_s = maximumVelocity_m_s;
    }

    @Override
    public double getLinkTravelTime(Link link, double time, Person person, Vehicle vehicle) {
        double observed = base.getLinkTravelTime(link, time, person, vehicle);
        double atTopSpeed = link.getLength() / maximumVelocity_m_s;
        return Math.max(observed, atTopSpeed);
    }
}
