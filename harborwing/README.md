# HarborWing Air – Staff CRUD Application (Project 2)

A Flask + MySQL web app that lets HarborWing employees manage the reservation database **without writing SQL**.

- **Database (Midterm Drill):** `database/` in this repo (schema `database/sql/01_schema.sql`, seed `database/seed/seed.py`, diagram `database/harborwing_diagram.png`, Workbench model `database/harborwing.mwb`). If you keep the Midterm Drill in its own repo, link it here: `<MIDTERM_REPO_URL>` · version/commit used: `<COMMIT_HASH>` · MySQL 8.0.16+.
- **Schema changes:** none. The app uses the Midterm tables, keys and constraints exactly as delivered.
- **Only fix to Midterm files:** `database/seed/requirements.txt` had `-e faker` (a typo that breaks `pip install`); it is now `faker`.

## Features
Create / read / update / delete for **passengers, booking agents, reservations, payments, airports, flight schedules, flight legs, flight costs**; read-only **calendar** and reference-code list (home page).
Reservation page: full view (passenger, agent, payments, legs), **link / unlink** payments and legs, or **create + link** a payment in one transaction. Search on every list, status filters on reservations/payments, pagination.

## Setup (another developer, from a fresh clone)
```bash
# 1. Database from the Midterm Drill (MySQL 8.0.16+ running)
pip install -r database/seed/requirements.txt
mysql -u root -p < database/sql/01_schema.sql      # creates AND RESETS database `harborwing`
export DB_USER=root DB_PASSWORD=yourpw             # Windows: set DB_USER=... / set DB_PASSWORD=...
python database/seed/seed.py                       # synthetic data, SEED=42

# 2. Application
cd application
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                               # then edit DB_PASSWORD and SECRET_KEY
python app.py                                      # open http://127.0.0.1:5000
```
`.env` is git-ignored; only `.env.example` (no secrets) is committed. Settings: `DB_HOST DB_PORT DB_USER DB_PASSWORD DB_NAME SECRET_KEY`.

## Using the app
Pick a table in the top bar → search / filter → **+ New**, **Edit**, **Delete**. Reservations have a **View** page for linking and unlinking.

## How a user action becomes SQL (example: "Create & link payment")
1. Reservation page form posts to `POST /reservation/<id>/payments/create` → `res_pay_create()` in `application/app.py`.
2. `parse(ENT["payment"], request.form)` validates: status in the allowed list, date valid, amount ≥ 0.01. Errors become flash messages and nothing touches the DB.
3. Inside `with transaction():` (one DB transaction) the app runs, as parameterized queries:
   `INSERT INTO payment (payment_status_code, payment_date, payment_amount) VALUES (%s,%s,%s)` then
   `INSERT INTO reservation_payment (reservation_id, payment_id) VALUES (%s,%s)` using `cursor.lastrowid`.
4. Both rows commit together, or on any error `transaction()` rolls back both and `friendly(e)` turns the MySQL error number into a readable message.

The generic screens work the same way: each table is described once in the `ENT` dictionary (fields, validation, search columns, delete checks). `new()`, `edit()`, `delete()`, `list_()` build INSERT / UPDATE / DELETE / SELECT from that config, always passing user values as `%s` parameters. Table and column names come only from `ENT`, never from user input.

## Transactions and foreign-key integrity
| Situation | Behaviour |
|---|---|
| Create + link payment | One transaction (both rows or neither) |
| Unlink payment / leg | Deletes only the row in `reservation_payment` / `reservation_leg`; the payment or leg is kept |
| Duplicate link | Pre-checked with a friendly message; composite primary key is the backstop |
| Delete payment / leg still linked | Blocked with a count and instruction to unlink first (policy) |
| Delete passenger with reservations, airport used by schedules/legs, schedule with legs/costs | Blocked with an explanation (matches the schema's `RESTRICT`) |
| Delete reservation | Allowed; only its link rows go (`CASCADE`); payments and legs stay |
| Delete booking agent | Allowed; their reservations become direct bookings (`SET NULL`) |
| Any other FK / CHECK / duplicate error | Caught and mapped to a clear message (`friendly()`) |

Extra app-level rules: a leg's airports must match its schedule (Midterm limitation #5); flight cost periods may not overlap (Midterm limitation #4); key columns (airport code, flight number, cost key) can't be edited because other tables reference them.

## Demo workflow (for the explainer video)
1. Passengers → **+ New** → save. 2. Reservations → **+ New** (pick that passenger) → lands on its page.
3. **Add leg** twice. 4. **Create & link** a payment. 5. Read the complete reservation.
6. **Edit** the passenger/reservation. 7. **Unlink** the payment, then show it still exists under Payments.
8. Delete that now-unlinked payment (eligible). 9. Errors: try to delete a linked leg or an airport in use, add the same leg twice, save a negative amount or an invalid email.

## Known limitations
- No login/roles (internal demo tool). - Reference codes (status, ticket, class, etc.) are fixed lists in `app.py` because the Midterm schema keeps them external. - Shared payments are not apportioned between reservations (Midterm limitation #1). - Calendar and reference data are maintained through the seed script. - Timestamps are UTC, entered without timezone conversion.
