"""HarborWing Air staff CRUD app (Flask + MySQL).
All SQL values are passed as parameters (%s). Table/column names in SQL strings come only
from the trusted ENT config below, never from user input."""
import os, re
from contextlib import contextmanager
from datetime import datetime, date, timezone
from decimal import Decimal, InvalidOperation
import mysql.connector
from mysql.connector import Error
from dotenv import load_dotenv
from flask import Flask, g, render_template, request, redirect, url_for, flash, abort

load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY") or os.urandom(24).hex()
PAGE = 50
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# ---------- database access ----------------------------------------------
def db():
    if "db" not in g:
        g.db = mysql.connector.connect(
            host=os.getenv("DB_HOST", "127.0.0.1"), port=int(os.getenv("DB_PORT", 3306)),
            user=os.getenv("DB_USER", "root"), password=os.getenv("DB_PASSWORD", ""),
            database=os.getenv("DB_NAME", "harborwing"), charset="utf8mb4")
    return g.db

@app.teardown_appcontext
def close_db(_):
    c = g.pop("db", None)
    if c:
        c.close()

def query(sql, args=(), one=False):
    cur = db().cursor(dictionary=True)
    cur.execute(sql, args)
    rows = cur.fetchall()
    cur.close()
    return (rows[0] if rows else None) if one else rows

@contextmanager
def transaction():
    """All statements inside the with-block commit together, or none do."""
    c = db()
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise

FRIENDLY = {  # MySQL error numbers -> readable messages
    1062: "That record (or link) already exists.",
    1451: "Cannot delete: other records still reference it.",
    1452: "A referenced record does not exist (check passenger, agent, flight, airport or calendar date).",
    3819: "A business rule (CHECK constraint) was violated, e.g. invalid times, amounts or airports.",
    1406: "A value is too long for its field.",
}
def friendly(e):
    return FRIENDLY.get(getattr(e, "errno", None), f"Database error: {getattr(e, 'msg', e)}")

# ---------- field definitions & validation ---------------------------------
STATUS = ["CONF", "PEND", "CANC", "CHKD"]; TICKET = ["ECON", "FLEX", "PROMO"]
CLASS = ["Y", "W", "J"]; PAY_STATUS = ["PAID", "PART", "REFD", "FAIL"]
AIRLINES = ["HW", "HI"]; AIRCRAFT = ["AT72", "DH8D", "E175", "B738"]

def opts_from(sql):
    return lambda: [(r["v"], r["l"]) for r in query(sql)]
PASSENGERS = opts_from("SELECT passenger_id v, CONCAT('#',passenger_id,' ',first_name,' ',last_name) l FROM passenger ORDER BY last_name, first_name")
AGENTS = opts_from("SELECT agent_id v, CONCAT('#',agent_id,' ',agent_name) l FROM booking_agent ORDER BY agent_name")
AIRPORTS = opts_from("SELECT airport_code v, CONCAT(airport_code,' - ',airport_name) l FROM airport ORDER BY airport_code")
FLIGHTS = opts_from("SELECT flight_number v, CONCAT(flight_number,' (',origin_airport_code,'->',destination_airport_code,')') l FROM flight_schedule ORDER BY flight_number")

def F(name, label, type="text", req=True, **kw):
    return dict(name=name, label=label, type=type, req=req, **kw)
now_utc = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")
today = lambda: date.today().isoformat()

class Bad(Exception):
    pass

def options(f):
    o = f["options"]() if callable(f["options"]) else f["options"]
    return [(x, x) if isinstance(x, str) else x for x in o]

def parse(ent, form, editing=False):
    vals, errs = {}, []
    for f in ent["fields"]:
        if editing and f.get("pk_lock"):
            continue                                   # key columns can't be edited
        raw, label, t = (form.get(f["name"]) or "").strip(), f["label"], f["type"]
        if raw == "":
            if f["req"]:
                errs.append(f"{label} is required.")
            vals[f["name"]] = None
            continue
        try:
            if f.get("upper"):
                raw = raw.upper()
            if t in ("text", "email"):
                if len(raw) > f.get("max", 255): raise Bad(f"{label} must be at most {f.get('max', 255)} characters.")
                if t == "email" and not EMAIL.match(raw): raise Bad("Enter a valid email address.")
                if f.get("regex") and not re.match(f["regex"], raw): raise Bad(f"{label}: {f['hint']}")
                v = raw
            elif t == "int":
                v = int(raw)
                if not f.get("min", 0) <= v <= f.get("max", 2**31): raise Bad(f"{label} must be between {f.get('min', 0)} and {f.get('max', 2**31)}.")
            elif t == "decimal":
                v = Decimal(raw).quantize(Decimal("0.01"))
                if not Decimal(str(f.get("min", 0))) <= v <= Decimal("99999999.99"): raise Bad(f"{label} must be at least {f.get('min', 0)}.")
            elif t == "date":
                v = date.fromisoformat(raw)
            elif t == "datetime":
                v = datetime.fromisoformat(raw)
            elif t == "select":
                if raw not in {str(o[0]) for o in options(f)}: raise Bad(f"{label}: choose a valid option.")
                v = f["cast"](raw) if "cast" in f else raw
        except Bad as e:
            errs.append(str(e)); continue
        except (ValueError, InvalidOperation):
            errs.append(f"{label}: invalid {t} value."); continue
        vals[f["name"]] = v
    return vals, errs

# ---------- cross-field business rules (run only when single fields are valid) ----------
def chk_schedule(v):
    e = []
    if v["origin_airport_code"] == v["destination_airport_code"]: e.append("Origin and destination airports must differ.")
    if v["scheduled_arrival"] <= v["scheduled_departure"]: e.append("Scheduled arrival must be after departure.")
    return e

def chk_leg(v):
    e = []
    if v["origin_airport_code"] == v["destination_airport_code"]: e.append("Origin and destination airports must differ.")
    if v["actual_departure"] and v["actual_arrival"] and v["actual_arrival"] <= v["actual_departure"]: e.append("Actual arrival must be after actual departure.")
    s = query("SELECT origin_airport_code o, destination_airport_code d FROM flight_schedule WHERE flight_number=%s", (v["flight_number"],), one=True)
    if s and (s["o"], s["d"]) != (v["origin_airport_code"], v["destination_airport_code"]):
        e.append(f"Leg route must match its schedule ({s['o']} -> {s['d']}).")
    return e

def chk_cost(v):
    if v["valid_to"] < v["valid_from"]: return ["Valid-to date must not be before valid-from."]
    # policy for the overlap limitation noted in the Midterm README
    o = query("SELECT valid_from, valid_to FROM flight_cost WHERE flight_number=%s AND aircraft_type_code=%s "
              "AND valid_from<=%s AND valid_to>=%s AND valid_from<>%s",
              (v["flight_number"], v["aircraft_type_code"], v["valid_to"], v["valid_from"], v["valid_from"]), one=True)
    return [f"Overlaps an existing cost period ({o['valid_from']} to {o['valid_to']})."] if o else []

def refs(label, sql): return (label, sql)
LINKS_P = refs("reservation link(s)", "SELECT COUNT(*) n FROM reservation_payment WHERE payment_id=%(k)s")
LINKS_L = refs("reservation link(s)", "SELECT COUNT(*) n FROM reservation_leg WHERE leg_id=%(k)s")

ENT = {
 "passenger": dict(table="passenger", label="passenger", plural="Passengers", pk=["passenger_id"],
   fields=[F("first_name","First name",max=60), F("second_name","Second name",req=False,max=60), F("last_name","Last name",max=60),
           F("phone_number","Phone",max=40), F("email_address","Email","email",max=120), F("address_line1","Address line 1",max=120),
           F("address_line2","Address line 2",req=False,max=120), F("city","City",max=80),
           F("state_province_county","State/province/county",req=False,max=80), F("country","Country",max=80),
           F("other_details","Other details",req=False)],
   list="SELECT t.* FROM passenger t", search="CONCAT_WS(' ',t.first_name,t.last_name,t.email_address,t.city,t.country)",
   order="t.passenger_id DESC", cols=["passenger_id","first_name","last_name","email_address","city","country"],
   refs=[refs("reservation(s)", "SELECT COUNT(*) n FROM reservation WHERE passenger_id=%(k)s")],
   hint="Delete or reassign those reservations first."),
 "booking_agent": dict(table="booking_agent", label="booking agent", plural="Booking agents", pk=["agent_id"],
   fields=[F("agent_name","Agent name",max=120), F("other_details","Other details",req=False)],
   list="SELECT t.* FROM booking_agent t", search="CONCAT_WS(' ',t.agent_name,t.other_details)", order="t.agent_id DESC",
   cols=["agent_id","agent_name","other_details"], refs=[],
   note="Their reservations were kept and are now direct bookings (agent set to none)."),
 "reservation": dict(table="reservation", label="reservation", plural="Reservations", pk=["reservation_id"],
   fields=[F("passenger_id","Passenger","select",options=PASSENGERS,cast=int), F("agent_id","Booking agent (blank = direct)","select",req=False,options=AGENTS,cast=int),
           F("status_code","Status","select",options=STATUS), F("ticket_type_code","Ticket type","select",options=TICKET),
           F("travel_class_code","Travel class","select",options=CLASS),
           F("created_at","Created (UTC)","datetime",default=now_utc), F("party_size","Party size","int",min=1,max=65535,default="1")],
   list="SELECT t.*, CONCAT(p.first_name,' ',p.last_name) AS passenger, a.agent_name AS agent FROM reservation t "
        "JOIN passenger p ON p.passenger_id=t.passenger_id LEFT JOIN booking_agent a ON a.agent_id=t.agent_id",
   search="CONCAT_WS(' ',t.reservation_id,p.first_name,p.last_name,a.agent_name,t.ticket_type_code)", order="t.reservation_id DESC",
   cols=["reservation_id","passenger","agent","status_code","ticket_type_code","travel_class_code","party_size","created_at"],
   filter=("t.status_code", STATUS, "statuses"), refs=[],
   note="Its payment and leg links were removed; the payments and legs themselves were kept."),
 "payment": dict(table="payment", label="payment", plural="Payments", pk=["payment_id"],
   fields=[F("payment_status_code","Status","select",options=PAY_STATUS), F("payment_date","Date","date",default=today),
           F("payment_amount","Amount","decimal",min=0.01)],
   list="SELECT t.* FROM payment t", search="CONCAT_WS(' ',t.payment_id,t.payment_date,t.payment_amount)", order="t.payment_id DESC",
   cols=["payment_id","payment_status_code","payment_date","payment_amount"], filter=("t.payment_status_code", PAY_STATUS, "statuses"),
   refs=[LINKS_P], hint="Remove the payment from those reservations first (Unlink on the reservation page)."),
 "airport": dict(table="airport", label="airport", plural="Airports", pk=["airport_code"],
   fields=[F("airport_code","Code (3 letters)",max=3,upper=True,regex=r"^[A-Z]{3}$",hint="use exactly 3 letters.",pk_lock=True),
           F("airport_name","Name",max=120), F("location","Location",max=120), F("other_details","Other details",req=False)],
   list="SELECT t.* FROM airport t", search="CONCAT_WS(' ',t.airport_code,t.airport_name,t.location)", order="t.airport_code",
   cols=["airport_code","airport_name","location"],
   refs=[refs("flight schedule(s)", "SELECT COUNT(*) n FROM flight_schedule WHERE origin_airport_code=%(k)s OR destination_airport_code=%(k)s"),
         refs("flight leg(s)", "SELECT COUNT(*) n FROM flight_leg WHERE origin_airport_code=%(k)s OR destination_airport_code=%(k)s")],
   hint="Reassign or delete those schedules/legs first."),
 "flight_schedule": dict(table="flight_schedule", label="flight schedule", plural="Flight schedules", pk=["flight_number"],
   fields=[F("flight_number","Flight number",max=10,upper=True,regex=r"^[A-Z0-9]{2,10}$",hint="2-10 letters/digits.",pk_lock=True),
           F("airline_code","Airline","select",options=AIRLINES), F("usual_aircraft_type_code","Usual aircraft","select",options=AIRCRAFT),
           F("origin_airport_code","Origin","select",options=AIRPORTS), F("destination_airport_code","Destination","select",options=AIRPORTS),
           F("scheduled_departure","Scheduled departure (UTC)","datetime"), F("scheduled_arrival","Scheduled arrival (UTC)","datetime")],
   list="SELECT t.* FROM flight_schedule t", search="CONCAT_WS(' ',t.flight_number,t.origin_airport_code,t.destination_airport_code)",
   order="t.flight_number", cols=["flight_number","airline_code","usual_aircraft_type_code","origin_airport_code","destination_airport_code","scheduled_departure","scheduled_arrival"],
   check=chk_schedule,
   refs=[refs("flight leg(s)", "SELECT COUNT(*) n FROM flight_leg WHERE flight_number=%(k)s"),
         refs("flight cost record(s)", "SELECT COUNT(*) n FROM flight_cost WHERE flight_number=%(k)s")],
   hint="Delete those legs/costs first."),
 "flight_leg": dict(table="flight_leg", label="flight leg", plural="Flight legs", pk=["leg_id"],
   fields=[F("flight_number","Flight","select",options=FLIGHTS), F("origin_airport_code","Origin","select",options=AIRPORTS),
           F("destination_airport_code","Destination","select",options=AIRPORTS),
           F("actual_departure","Actual departure (UTC)","datetime",req=False), F("actual_arrival","Actual arrival (UTC)","datetime",req=False)],
   list="SELECT t.* FROM flight_leg t", search="CONCAT_WS(' ',t.leg_id,t.flight_number,t.origin_airport_code,t.destination_airport_code)",
   order="t.leg_id DESC", cols=["leg_id","flight_number","origin_airport_code","destination_airport_code","actual_departure","actual_arrival"],
   check=chk_leg, refs=[LINKS_L], hint="Remove the leg from those reservations first (Unlink on the reservation page)."),
 "flight_cost": dict(table="flight_cost", label="flight cost", plural="Flight costs", pk=["flight_number","aircraft_type_code","valid_from"],
   fields=[F("flight_number","Flight","select",options=FLIGHTS,pk_lock=True), F("aircraft_type_code","Aircraft","select",options=AIRCRAFT,pk_lock=True),
           F("valid_from","Valid from (must be in calendar)","date",pk_lock=True), F("valid_to","Valid to (must be in calendar)","date"),
           F("cost_amount","Cost amount","decimal",min=0)],
   list="SELECT t.* FROM flight_cost t", search="CONCAT_WS(' ',t.flight_number,t.aircraft_type_code)",
   order="t.flight_number, t.valid_from DESC", cols=["flight_number","aircraft_type_code","valid_from","valid_to","cost_amount"],
   check=chk_cost, refs=[]),
 "calendar_date": dict(table="calendar_date", label="calendar date", plural="Calendar (read-only)", pk=["cal_date"], fields=[], readonly=True,
   list="SELECT t.* FROM calendar_date t", search="CAST(t.cal_date AS CHAR)", order="t.cal_date",
   cols=["cal_date","day_of_month","is_business_day"], refs=[]),
}

def get_ent(name):
    return ENT.get(name) or abort(404)
where = lambda ent: " AND ".join(f"{c}=%s" for c in ent["pk"])
key_of = lambda ent, row: "~".join(str(row[c]) for c in ent["pk"])

def fetch(ent, key):
    parts = key.split("~")
    return query(f"SELECT * FROM {ent['table']} WHERE {where(ent)}", parts, one=True) if len(parts) == len(ent["pk"]) else None

def fmt(v):
    if isinstance(v, datetime): return v.strftime("%Y-%m-%dT%H:%M")
    if isinstance(v, date): return v.isoformat()
    return "" if v is None else str(v)

def render_form(ent, name, form, key):
    fields = [dict(f, opts=options(f) if f["type"] == "select" else None, locked=bool(key and f.get("pk_lock"))) for f in ent["fields"]]
    return render_template("form.html", ent=ent, name=name, fields=fields, form=form, key=key)

@app.context_processor
def inject_nav():
    return {"ents": ENT}

# ---------- generic CRUD routes ---------------------------------------------
@app.route("/")
def index():
    counts = {n: query(f"SELECT COUNT(*) n FROM {e['table']}", one=True)["n"] for n, e in ENT.items()}
    return render_template("index.html", counts=counts, codes=dict(Status=STATUS, Ticket=TICKET, Class=CLASS, Payment=PAY_STATUS, Airline=AIRLINES, Aircraft=AIRCRAFT))

@app.route("/<name>")
def list_(name):
    ent = get_ent(name)
    q, flt = request.args.get("q", "").strip(), request.args.get("f", "").strip()
    page = max(request.args.get("page", 1, type=int), 1)
    esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    sql, args = f"{ent['list']} WHERE {ent['search']} LIKE %s", [f"%{esc}%"]
    if ent.get("filter") and flt:
        sql += f" AND {ent['filter'][0]} = %s"; args.append(flt)
    rows = query(f"{sql} ORDER BY {ent['order']} LIMIT {PAGE + 1} OFFSET {(page - 1) * PAGE}", args)
    more, rows = len(rows) > PAGE, rows[:PAGE]
    return render_template("list.html", ent=ent, name=name, rows=rows, keys=[key_of(ent, r) for r in rows], q=q, flt=flt, page=page, more=more)

@app.route("/<name>/new", methods=["GET", "POST"])
def new(name):
    ent = get_ent(name)
    if ent.get("readonly"): abort(404)
    if request.method == "POST":
        vals, errs = parse(ent, request.form)
        if not errs and ent.get("check"): errs = ent["check"](vals)
        if not errs:
            try:
                with transaction() as c:
                    cur = c.cursor(); cols = list(vals)
                    cur.execute(f"INSERT INTO {ent['table']} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})", list(vals.values()))
                    new_id = cur.lastrowid
                flash(f"{ent['label'].capitalize()} created.", "ok")
                return redirect(url_for("reservation_view", rid=new_id) if name == "reservation" else url_for("list_", name=name))
            except Error as e:
                errs = [friendly(e)]
        for m in errs: flash(m, "err")
        return render_form(ent, name, request.form, None)
    return render_form(ent, name, {f["name"]: (f["default"]() if callable(f.get("default")) else f.get("default", "")) for f in ent["fields"]}, None)

@app.route("/<name>/<key>/edit", methods=["GET", "POST"])
def edit(name, key):
    ent = get_ent(name)
    row = None if ent.get("readonly") else fetch(ent, key)
    if not row:
        flash(f"That {ent['label']} was not found (it may have been deleted).", "err")
        return redirect(url_for("list_", name=name))
    current = {f["name"]: fmt(row[f["name"]]) for f in ent["fields"]}
    if request.method == "POST":
        vals, errs = parse(ent, request.form, editing=True)
        if not errs and ent.get("check"): errs = ent["check"]({**row, **vals})
        if not errs:
            try:
                with transaction() as c:
                    c.cursor().execute(f"UPDATE {ent['table']} SET {', '.join(k + '=%s' for k in vals)} WHERE {where(ent)}", [*vals.values(), *key.split("~")])
                flash(f"{ent['label'].capitalize()} updated.", "ok")
                return redirect(url_for("reservation_view", rid=int(key)) if name == "reservation" else url_for("list_", name=name))
            except Error as e:
                errs = [friendly(e)]
        for m in errs: flash(m, "err")
        return render_form(ent, name, {**current, **request.form}, key)
    return render_form(ent, name, current, key)

@app.route("/<name>/<key>/delete", methods=["POST"])
def delete(name, key):
    ent = get_ent(name)
    if ent.get("readonly") or not fetch(ent, key):
        flash(f"That {ent['label']} was not found.", "err"); return redirect(url_for("list_", name=name))
    for label, sql in ent.get("refs", []):          # safe-delete policy: explain instead of failing
        n = query(sql, {"k": key.split("~")[0]}, one=True)["n"]
        if n:
            flash(f"Cannot delete this {ent['label']}: it is still used by {n} {label}. {ent.get('hint', '')}", "err")
            return redirect(url_for("list_", name=name))
    try:
        with transaction() as c:
            c.cursor().execute(f"DELETE FROM {ent['table']} WHERE {where(ent)}", key.split("~"))
        flash(f"{ent['label'].capitalize()} deleted. {ent.get('note', '')}", "ok")
    except Error as e:
        flash(friendly(e), "err")
    return redirect(url_for("list_", name=name))

# ---------- reservation detail & link management ------------------------------
def reservation_or_none(rid):
    return query("SELECT 1 x FROM reservation WHERE reservation_id=%s", (rid,), one=True)

@app.route("/reservation/<int:rid>")
def reservation_view(rid):
    r = query("""SELECT r.*, CONCAT(p.first_name,' ',p.last_name) passenger_name, p.email_address, p.phone_number, a.agent_name
                 FROM reservation r JOIN passenger p ON p.passenger_id=r.passenger_id
                 LEFT JOIN booking_agent a ON a.agent_id=r.agent_id WHERE r.reservation_id=%s""", (rid,), one=True)
    if not r:
        flash("Reservation not found.", "err"); return redirect(url_for("list_", name="reservation"))
    pays = query("SELECT py.* FROM reservation_payment rp JOIN payment py ON py.payment_id=rp.payment_id WHERE rp.reservation_id=%s ORDER BY py.payment_date", (rid,))
    legs = query("""SELECT fl.*, fs.scheduled_departure, fs.scheduled_arrival FROM reservation_leg rl
                    JOIN flight_leg fl ON fl.leg_id=rl.leg_id JOIN flight_schedule fs ON fs.flight_number=fl.flight_number
                    WHERE rl.reservation_id=%s ORDER BY fl.leg_id""", (rid,))
    free_pays = query("""SELECT payment_id v, CONCAT('#',payment_id,' - ',payment_date,' - ',payment_amount,' (',payment_status_code,')') l FROM payment
                         WHERE payment_id NOT IN (SELECT payment_id FROM reservation_payment WHERE reservation_id=%s) ORDER BY payment_id DESC""", (rid,))
    free_legs = query("""SELECT leg_id v, CONCAT('#',leg_id,' - ',flight_number,' ',origin_airport_code,'->',destination_airport_code) l FROM flight_leg
                         WHERE leg_id NOT IN (SELECT leg_id FROM reservation_leg WHERE reservation_id=%s) ORDER BY leg_id DESC""", (rid,))
    return render_template("reservation.html", r=r, pays=pays, legs=legs, free_pays=free_pays, free_legs=free_legs,
                           total=sum(p["payment_amount"] for p in pays), pay_status=PAY_STATUS, today=today())

def link_add(rid, link, col, parent, label):
    if not reservation_or_none(rid):
        flash("Reservation not found.", "err"); return redirect(url_for("list_", name="reservation"))
    val = request.form.get(col, type=int)
    if val is None or not query(f"SELECT 1 x FROM {parent} WHERE {col}=%s", (val,), one=True):
        flash(f"Choose an existing {label}.", "err")
    elif query(f"SELECT 1 x FROM {link} WHERE reservation_id=%s AND {col}=%s", (rid, val), one=True):
        flash(f"That {label} is already linked to this reservation.", "err")        # duplicate-link prevention
    else:
        try:
            with transaction() as c:
                c.cursor().execute(f"INSERT INTO {link} (reservation_id, {col}) VALUES (%s,%s)", (rid, val))
            flash(f"{label.capitalize()} #{val} linked.", "ok")
        except Error as e:
            flash(friendly(e), "err")
    return redirect(url_for("reservation_view", rid=rid))

def link_remove(rid, link, col, val, label):
    try:
        with transaction() as c:
            cur = c.cursor()
            cur.execute(f"DELETE FROM {link} WHERE reservation_id=%s AND {col}=%s", (rid, val))   # only the link row
            n = cur.rowcount
        flash(f"{label.capitalize()} #{val} unlinked (the {label} record itself was kept)." if n else f"That {label} is not linked to this reservation.", "ok" if n else "err")
    except Error as e:
        flash(friendly(e), "err")
    return redirect(url_for("reservation_view", rid=rid))

@app.post("/reservation/<int:rid>/payments/add")
def res_pay_add(rid): return link_add(rid, "reservation_payment", "payment_id", "payment", "payment")
@app.post("/reservation/<int:rid>/legs/add")
def res_leg_add(rid): return link_add(rid, "reservation_leg", "leg_id", "flight_leg", "leg")
@app.post("/reservation/<int:rid>/payments/<int:pid>/remove")
def res_pay_remove(rid, pid): return link_remove(rid, "reservation_payment", "payment_id", pid, "payment")
@app.post("/reservation/<int:rid>/legs/<int:lid>/remove")
def res_leg_remove(rid, lid): return link_remove(rid, "reservation_leg", "leg_id", lid, "leg")

@app.post("/reservation/<int:rid>/payments/create")
def res_pay_create(rid):
    """Create a payment AND link it in ONE transaction: both rows commit, or neither does."""
    if not reservation_or_none(rid):
        flash("Reservation not found.", "err"); return redirect(url_for("list_", name="reservation"))
    vals, errs = parse(ENT["payment"], request.form)
    if not errs:
        try:
            with transaction() as c:
                cur = c.cursor()
                cur.execute("INSERT INTO payment (payment_status_code, payment_date, payment_amount) VALUES (%s,%s,%s)",
                            (vals["payment_status_code"], vals["payment_date"], vals["payment_amount"]))
                cur.execute("INSERT INTO reservation_payment (reservation_id, payment_id) VALUES (%s,%s)", (rid, cur.lastrowid))
            flash("Payment created and linked.", "ok")
        except Error as e:
            errs = [friendly(e)]
    for m in errs: flash(m, "err")
    return redirect(url_for("reservation_view", rid=rid))

# ---------- error pages ---------------------------------------------------------
@app.errorhandler(404)
def nf(_): return render_template("msg.html", msg="Page not found."), 404
@app.errorhandler(Error)
def dberr(e): return render_template("msg.html", msg=f"Database problem: {friendly(e)} Check your .env settings and that MySQL is running."), 500

if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG") == "1")
