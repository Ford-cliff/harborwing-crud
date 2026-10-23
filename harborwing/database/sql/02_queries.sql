USE harborwing;

-- ===== Row-count verification =====
SELECT 'booking_agent' AS table_name, COUNT(*) AS row_count FROM booking_agent
UNION ALL SELECT 'passenger', COUNT(*) FROM passenger
UNION ALL SELECT 'reservation', COUNT(*) FROM reservation
UNION ALL SELECT 'payment', COUNT(*) FROM payment
UNION ALL SELECT 'airport', COUNT(*) FROM airport
UNION ALL SELECT 'calendar_date', COUNT(*) FROM calendar_date
UNION ALL SELECT 'flight_schedule', COUNT(*) FROM flight_schedule
UNION ALL SELECT 'flight_leg', COUNT(*) FROM flight_leg
UNION ALL SELECT 'flight_cost', COUNT(*) FROM flight_cost
UNION ALL SELECT 'reservation_payment', COUNT(*) FROM reservation_payment
UNION ALL SELECT 'reservation_leg', COUNT(*) FROM reservation_leg;

-- ===== Q1: Reservation with passenger, agent, payments, legs (no inflated totals) =====
-- Payments and legs are aggregated in SEPARATE derived tables, so joining
-- many payments x many legs cannot multiply payment amounts.
SET @rid = 1;
SELECT r.reservation_id, CONCAT(p.first_name,' ',p.last_name) AS passenger,
       COALESCE(a.agent_name,'(direct)') AS agent,
       r.status_code, r.party_size,
       COALESCE(pay.payment_count,0) AS payment_count,
       COALESCE(pay.total_paid,0)    AS total_linked_payments,
       COALESCE(lg.leg_count,0)      AS leg_count,
       lg.legs
FROM reservation r
JOIN passenger p ON p.passenger_id = r.passenger_id
LEFT JOIN booking_agent a ON a.agent_id = r.agent_id
LEFT JOIN (SELECT rp.reservation_id, COUNT(*) payment_count, SUM(py.payment_amount) total_paid
           FROM reservation_payment rp JOIN payment py ON py.payment_id = rp.payment_id
           GROUP BY rp.reservation_id) pay ON pay.reservation_id = r.reservation_id
LEFT JOIN (SELECT rl.reservation_id, COUNT(*) leg_count,
                  GROUP_CONCAT(CONCAT(fl.flight_number,' ',fl.origin_airport_code,'->',
                               fl.destination_airport_code) ORDER BY fl.leg_id SEPARATOR '; ') legs
           FROM reservation_leg rl JOIN flight_leg fl ON fl.leg_id = rl.leg_id
           GROUP BY rl.reservation_id) lg ON lg.reservation_id = r.reservation_id
WHERE r.reservation_id = @rid;

-- ===== Q2: Reservations using a particular leg =====
SET @leg = 1;
SELECT r.reservation_id, CONCAT(p.first_name,' ',p.last_name) AS passenger, r.party_size, r.status_code
FROM reservation_leg rl
JOIN reservation r ON r.reservation_id = rl.reservation_id
JOIN passenger p   ON p.passenger_id = r.passenger_id
WHERE rl.leg_id = @leg;

-- ===== Q3: Payments linked to a reservation =====
SELECT py.payment_id, py.payment_status_code, py.payment_date, py.payment_amount
FROM reservation_payment rp
JOIN payment py ON py.payment_id = rp.payment_id
WHERE rp.reservation_id = @rid
ORDER BY py.payment_date;

-- ===== Q4: Flight cost for flight, aircraft type, and date =====
SET @fn = 'HW100'; SET @ac = (SELECT usual_aircraft_type_code FROM flight_schedule WHERE flight_number='HW100');
SET @on = (SELECT valid_from FROM flight_cost WHERE flight_number='HW100' ORDER BY valid_from LIMIT 1);
SELECT flight_number, aircraft_type_code, valid_from, valid_to, cost_amount
FROM flight_cost
WHERE flight_number = @fn AND aircraft_type_code = @ac
  AND @on BETWEEN valid_from AND valid_to;

-- ===== Integrity audit: overlapping cost periods (policy check) =====
SELECT a.flight_number, a.aircraft_type_code, a.valid_from, b.valid_from AS overlaps_with
FROM flight_cost a JOIN flight_cost b
  ON a.flight_number=b.flight_number AND a.aircraft_type_code=b.aircraft_type_code
 AND a.valid_from < b.valid_from AND b.valid_from <= a.valid_to;
