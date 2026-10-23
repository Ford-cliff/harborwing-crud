# HarborWing Air – Reservation Database

MySQL 8.0.16+ (InnoDB, enforced `CHECK` constraints) · Python 3.9+ · all timestamps in **UTC**.

## Setup (create → seed → query)

```bash
pip install -r seed/requirements.txt        # faker, mysql-connector-python
mysql -u root -p < sql/01_schema.sql        # creates AND RESETS database `harborwing`
export DB_USER=root DB_PASSWORD=yourpw      # optional: DB_HOST DB_PORT DB_NAME
python seed/seed.py                         # SEED=42 → identical data every run; prints row counts
mysql -u root -p < sql/02_queries.sql       # row counts + example queries
```

**Reset process:** re-run `01_schema.sql` (drops and recreates the DB), then `seed.py`.

## Tables (11)

| Table | Purpose |
|---|---|
| `booking_agent` | Agents who book on behalf of passengers |
| `passenger` | One row per person; stored once, reused by every reservation |
| `reservation` | A booking: main passenger, optional agent, codes, created time, party size |
| `payment` | A payment event (amount > 0) |
| `airport` | Airport master list; one consistent spelling per code |
| `calendar_date` | Valid dates + day of month + business-day flag |
| `flight_schedule` | Planned flight: route, aircraft, scheduled UTC times |
| `flight_leg` | One operated instance of a schedule, with **actual** UTC times (nullable) |
| `flight_cost` | Cost history per flight + aircraft type + validity period |
| `reservation_payment` | Link: reservations ↔ payments |
| `reservation_leg` | Link: reservations ↔ legs |

## Relationships

- passenger 1—0..* reservation (RESTRICT delete); booking_agent 0..1—0..* reservation (SET NULL).
- flight_schedule 1—0..* flight_leg; airport 1—0..* schedule/leg (twice each: origin, destination).
- flight_schedule 1—0..* flight_cost; calendar_date 1—0..* flight_cost (valid_from and valid_to).
- reservation *—* payment via `reservation_payment`; reservation *—* leg via `reservation_leg`.

**Deletion rules:** history-bearing parents (passenger, airport, schedule, leg's schedule, cost's schedule/calendar) use `RESTRICT`
so records can't vanish silently. Agent deletion sets `reservation.agent_id` to NULL. Link-table rows `CASCADE` when
either side is deleted, since a link has no meaning alone.

## Reference codes (not enforced locally)
Status, ticket type, travel class, payment status, airline, and aircraft type codes are **externally managed**;
no tables or foreign keys exist for them. Demo values used by the seeder: `CONF/PEND/CANC/CHKD`, `ECON/FLEX/PROMO`,
`Y/W/J`, `PAID/PART/REFD/FAIL`, `HW/HI`, `AT72/DH8D/E175/B738` (demonstration data).

## Design explanations

**Why two linking tables?** Both relationships are many-to-many. One payment may cover several reservations and one
reservation may have several payments; likewise legs and reservations (a leg carries many bookings, an itinerary has
many legs). A foreign key in either parent can't express this. The composite primary key on each link blocks duplicate pairs.

**Why scheduled and actual times are separate?** The schedule is the plan; the leg records what happened. Storing both
(actual NULL until operated) means delays can be analysed and actuals never overwrite the plan.

**Why does flight cost use a composite key?** `(flight_number, aircraft_type_code, valid_from)` identifies one cost
version. New prices are new rows with new validity periods, so history is retained instead of overwritten.

**Flight-number assumption.** Flight number alone identifies a schedule here. A production system would also need
the operating date/departure time (and often carrier and suffix), because the same flight number operates daily;
codeshares and rescheduled flights also reuse numbers.

**Advantages:** no repeated passenger/airport data, consistent codes, full cost and time history, referential integrity.
**Trade-offs:** more joins; link tables add complexity; CHECK/FK enforcement costs a little write speed.

## Limitations
1. Shared payment links don't record how much each reservation received; `SUM(payment_amount)` per reservation overstates
   when a payment is shared (and can't be apportioned).
2. Party size is a count; individual travelers aren't identified.
3. Separating data reduces duplication but needs joins (aggregate payments and legs in separate subqueries to avoid inflation).
4. Cost validity periods can overlap; the PK only prevents duplicate start dates. Adopt a policy (e.g. a closing
   procedure that ends the prior period the day before a new one starts, or a trigger). `02_queries.sql` has an overlap audit.
5. Leg origin/destination are stored separately from the schedule's and aren't forced to match it.

## Example queries
See `sql/02_queries.sql`: Q1 reservation overview (payments and legs aggregated separately), Q2 reservations by leg,
Q3 payments by reservation, Q4 cost lookup by flight/aircraft/date, plus row counts and an overlap audit.

## Deliverables checklist
- [ ] Open MySQL Workbench → *Database → Reverse Engineer* against `harborwing`; arrange the EER diagram; save `harborwing.mwb`; export PNG/PDF.
- [ ] Record explainer video (face visible): diagram, relationships, running the SQL, row counts ≥ 50.
