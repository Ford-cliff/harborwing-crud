"""Seed the HarborWing Air database with fictional data (repeatable: SEED=42).

Usage:  python seed.py          (after running sql/01_schema.sql, which resets the DB)
Env:    DB_HOST DB_PORT DB_USER DB_PASSWORD DB_NAME
"""
import os, random
from datetime import date, datetime, timedelta
import mysql.connector
from faker import Faker

SEED = 42
random.seed(SEED)
Faker.seed(SEED)
fake = Faker("en_US")

N_AGENTS, N_PASSENGERS, N_AIRPORTS = 60, 120, 60
N_SCHEDULES, N_LEGS, N_RESERVATIONS, N_PAYMENTS = 80, 160, 150, 140
CAL_START, CAL_DAYS = date(2026, 1, 1), 546
NOW_REF = datetime(2026, 10, 12, 0, 0)        # fixed "today" so output is repeatable

STATUS = ["CONF", "PEND", "CANC", "CHKD"]      # demo codes, externally managed
TICKET = ["ECON", "FLEX", "PROMO"]
CLASS = ["Y", "W", "J"]
PAY_STATUS = ["PAID", "PART", "REFD", "FAIL"]
AIRLINES = ["HW", "HW", "HW", "HI"]            # HI = demo partner code
AIRCRAFT = ["AT72", "DH8D", "E175", "B738"]

def pick_pair(pool):
    return random.sample(pool, 2)

conn = mysql.connector.connect(
    host=os.getenv("DB_HOST", "127.0.0.1"), port=int(os.getenv("DB_PORT", 3306)),
    user=os.getenv("DB_USER", "root"), password=os.getenv("DB_PASSWORD", ""),
    database=os.getenv("DB_NAME", "harborwing"))
cur = conn.cursor()

def insert(sql, rows):
    cur.executemany(sql, rows)

# ---- parents first -------------------------------------------------------
insert("INSERT INTO booking_agent (agent_name, other_details) VALUES (%s,%s)",
       [(fake.company()[:120], f"Demo agency; contact {fake.catch_phrase()}"[:255])
        for _ in range(N_AGENTS)])

pax = []
for _ in range(N_PASSENGERS):
    pax.append((fake.first_name(), fake.first_name() if random.random() < .3 else None,
                fake.last_name(), fake.phone_number()[:40], fake.unique.email(),
                fake.street_address(), fake.secondary_address() if random.random() < .25 else None,
                fake.city(), fake.state(), "United States",
                "Frequent flyer (demo)" if random.random() < .2 else None))
insert("""INSERT INTO passenger (first_name, second_name, last_name, phone_number, email_address,
          address_line1, address_line2, city, state_province_county, country, other_details)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", pax)

codes = []
airports = []
for i in range(N_AIRPORTS):
    code = fake.unique.lexify("???", letters="ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    city = fake.city()
    codes.append(code)
    airports.append((code, f"{city} Harbor Airport ({code}) [demo]", f"{city}, {fake.country()}",
                     "Fictional demonstration airport"))
insert("INSERT INTO airport VALUES (%s,%s,%s,%s)", airports)

cal = []
for i in range(CAL_DAYS):
    d = CAL_START + timedelta(days=i)
    cal.append((d, d.day, d.weekday() < 5))
insert("INSERT INTO calendar_date VALUES (%s,%s,%s)", cal)

# ---- schedules -----------------------------------------------------------
sched = []
flight_numbers = []
for i in range(N_SCHEDULES):
    fn = f"HW{100 + i}"
    flight_numbers.append(fn)
    o, d = pick_pair(codes)
    dep = datetime(2026, 1, 1) + timedelta(days=random.randint(0, 540),
                                           minutes=random.randrange(0, 1440, 5))
    arr = dep + timedelta(minutes=random.randrange(45, 300, 5))
    sched.append((fn, random.choice(AIRLINES), random.choice(AIRCRAFT), o, d, dep, arr))
insert("INSERT INTO flight_schedule VALUES (%s,%s,%s,%s,%s,%s,%s)", sched)
sched_by_fn = {s[0]: s for s in sched}

# ---- legs (several legs per schedule are possible; first N_SCHEDULES guarantee coverage)
legs = []
fns = flight_numbers + [random.choice(flight_numbers) for _ in range(N_LEGS - N_SCHEDULES)]
for fn in fns:
    s = sched_by_fn[fn]
    if s[5] < NOW_REF:   # operated: actual times differ slightly from schedule
        a_dep = s[5] + timedelta(minutes=random.randint(-5, 90))
        a_arr = s[6] + timedelta(minutes=random.randint(-10, 100))
        if a_arr <= a_dep:
            a_arr = a_dep + timedelta(minutes=45)
    else:
        a_dep = a_arr = None   # not yet operated -> unknown
    legs.append((fn, s[3], s[4], a_dep, a_arr))
insert("""INSERT INTO flight_leg (flight_number, origin_airport_code, destination_airport_code,
          actual_departure, actual_arrival) VALUES (%s,%s,%s,%s,%s)""", legs)

# ---- flight costs: non-overlapping validity periods per (flight, aircraft) ----
last_day = CAL_START + timedelta(days=CAL_DAYS - 1)
costs = []
for fn in flight_numbers:
    usual = sched_by_fn[fn][2]
    plans = [usual] * 2 + ([random.choice([a for a in AIRCRAFT if a != usual])]
                           if random.random() < .5 else [])
    for ac in dict.fromkeys(plans):
        start = CAL_START + timedelta(days=random.randint(0, 150))
        base = random.uniform(2500, 9000)
        for _ in range(random.randint(2, 3)):          # consecutive, no overlap
            end = start + timedelta(days=random.randint(30, 90))
            if end > last_day:
                break
            costs.append((fn, ac, start, end, round(base, 2)))
            base *= random.uniform(0.95, 1.12)
            start = end + timedelta(days=1)
insert("INSERT INTO flight_cost VALUES (%s,%s,%s,%s,%s)", costs)

# ---- reservations --------------------------------------------------------
res = []
for i in range(N_RESERVATIONS):
    direct = random.random() < .4
    created = NOW_REF - timedelta(days=random.randint(1, 300), minutes=random.randint(0, 1439))
    res.append((random.randint(1, N_PASSENGERS), None if direct else random.randint(1, N_AGENTS),
                random.choice(STATUS), random.choice(TICKET), random.choice(CLASS),
                created, random.randint(1, 6)))
insert("""INSERT INTO reservation (passenger_id, agent_id, status_code, ticket_type_code,
          travel_class_code, created_at, party_size) VALUES (%s,%s,%s,%s,%s,%s,%s)""", res)

pays = [(random.choice(PAY_STATUS), (CAL_START + timedelta(days=random.randint(0, 270))),
         round(random.uniform(80, 1800), 2)) for _ in range(N_PAYMENTS)]
insert("INSERT INTO payment (payment_status_code, payment_date, payment_amount) VALUES (%s,%s,%s)", pays)

# ---- linking tables (sets prevent duplicate composite keys) ---------------
rp = set()
for p in range(1, N_PAYMENTS + 1):                 # each payment covers >=1 reservation
    rp.add((random.randint(1, N_RESERVATIONS), p))
for p in random.sample(range(1, N_PAYMENTS + 1), 60):   # shared payments: 2-3 reservations
    for r in random.sample(range(1, N_RESERVATIONS + 1), random.randint(1, 2)):
        rp.add((r, p))
for r in random.sample(range(1, N_RESERVATIONS + 1), 40):  # reservations with several payments
    for p in random.sample(range(1, N_PAYMENTS + 1), random.randint(2, 3)):
        rp.add((r, p))
insert("INSERT INTO reservation_payment VALUES (%s,%s)", sorted(rp))

rl = set()
for r in range(1, N_RESERVATIONS + 1):             # 1-4 legs each (itineraries)
    for l in random.sample(range(1, N_LEGS + 1), random.randint(1, 4)):
        rl.add((r, l))
insert("INSERT INTO reservation_leg VALUES (%s,%s)", sorted(rl))

conn.commit()

# ---- verification --------------------------------------------------------
for t in ["booking_agent", "passenger", "reservation", "payment", "airport", "calendar_date",
          "flight_schedule", "flight_leg", "flight_cost", "reservation_payment", "reservation_leg"]:
    cur.execute(f"SELECT COUNT(*) FROM {t}")
    n = cur.fetchone()[0]
    print(f"{t:22s}{n:6d}  {'OK' if n >= 50 else 'BELOW 50!'}")
conn.close()
