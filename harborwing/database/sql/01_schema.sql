-- HarborWing Air - schema (MySQL 8.0.16+ for enforced CHECK constraints)
-- Running this file RESETS the database (documented reset process).
-- All timestamps are stored in UTC.

DROP DATABASE IF EXISTS harborwing;
CREATE DATABASE harborwing CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE harborwing;

/* Externally managed reference codes (NO local tables, NO foreign keys):
   reservation.status_code, reservation.ticket_type_code, reservation.travel_class_code,
   payment.payment_status_code, flight_schedule.airline_code,
   flight_schedule.usual_aircraft_type_code, flight_cost.aircraft_type_code. */

/* NOTE: FKs on columns used in CHECK constraints (airport codes, cost dates) omit
   ON UPDATE CASCADE because MySQL forbids that combination; updates are RESTRICTed. */

-- 1. Booking agent
CREATE TABLE booking_agent (
  agent_id      INT UNSIGNED NOT NULL AUTO_INCREMENT,
  agent_name    VARCHAR(120) NOT NULL,
  other_details VARCHAR(255) NULL,
  PRIMARY KEY (agent_id)
) ENGINE=InnoDB;

-- 2. Passenger
CREATE TABLE passenger (
  passenger_id  INT UNSIGNED NOT NULL AUTO_INCREMENT,
  first_name    VARCHAR(60)  NOT NULL,
  second_name   VARCHAR(60)  NULL,
  last_name     VARCHAR(60)  NOT NULL,
  phone_number  VARCHAR(40)  NOT NULL,
  email_address VARCHAR(120) NOT NULL,
  address_line1 VARCHAR(120) NOT NULL,
  address_line2 VARCHAR(120) NULL,
  city          VARCHAR(80)  NOT NULL,
  state_province_county VARCHAR(80) NULL,
  country       VARCHAR(80)  NOT NULL,
  other_details VARCHAR(255) NULL,
  PRIMARY KEY (passenger_id),
  UNIQUE KEY uq_passenger_email (email_address),
  KEY idx_passenger_name (last_name, first_name)
) ENGINE=InnoDB;

-- 3. Reservation
CREATE TABLE reservation (
  reservation_id    INT UNSIGNED NOT NULL AUTO_INCREMENT,
  passenger_id      INT UNSIGNED NOT NULL,           -- main passenger
  agent_id          INT UNSIGNED NULL,               -- NULL = direct booking
  status_code       VARCHAR(10) NOT NULL,
  ticket_type_code  VARCHAR(10) NOT NULL,
  travel_class_code VARCHAR(10) NOT NULL,
  created_at        DATETIME NOT NULL,               -- UTC
  party_size        SMALLINT UNSIGNED NOT NULL,
  PRIMARY KEY (reservation_id),
  KEY idx_res_passenger (passenger_id),
  KEY idx_res_agent (agent_id),
  KEY idx_res_created (created_at),
  CONSTRAINT fk_res_passenger FOREIGN KEY (passenger_id)
    REFERENCES passenger (passenger_id) ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT fk_res_agent FOREIGN KEY (agent_id)
    REFERENCES booking_agent (agent_id) ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT ck_res_party CHECK (party_size > 0)
) ENGINE=InnoDB;

-- 4. Payment
CREATE TABLE payment (
  payment_id          INT UNSIGNED NOT NULL AUTO_INCREMENT,
  payment_status_code VARCHAR(10) NOT NULL,
  payment_date        DATE NOT NULL,
  payment_amount      DECIMAL(10,2) NOT NULL,
  PRIMARY KEY (payment_id),
  KEY idx_payment_date (payment_date),
  CONSTRAINT ck_payment_amount CHECK (payment_amount > 0)
) ENGINE=InnoDB;

-- 5. Airport
CREATE TABLE airport (
  airport_code  CHAR(3) NOT NULL,
  airport_name  VARCHAR(120) NOT NULL,
  location      VARCHAR(120) NOT NULL,
  other_details VARCHAR(255) NULL,
  PRIMARY KEY (airport_code),
  UNIQUE KEY uq_airport_name (airport_name)
) ENGINE=InnoDB;

-- 6. Calendar date
CREATE TABLE calendar_date (
  cal_date        DATE NOT NULL,
  day_of_month    TINYINT UNSIGNED NOT NULL,
  is_business_day BOOLEAN NOT NULL,
  PRIMARY KEY (cal_date),
  CONSTRAINT ck_cal_day CHECK (day_of_month BETWEEN 1 AND 31)
) ENGINE=InnoDB;

-- 7. Flight schedule (flight_number is the simplifying-assumption unique identifier)
CREATE TABLE flight_schedule (
  flight_number            VARCHAR(10) NOT NULL,
  airline_code             VARCHAR(3)  NOT NULL,
  usual_aircraft_type_code VARCHAR(6)  NOT NULL,
  origin_airport_code      CHAR(3) NOT NULL,
  destination_airport_code CHAR(3) NOT NULL,
  scheduled_departure      DATETIME NOT NULL,        -- UTC
  scheduled_arrival        DATETIME NOT NULL,        -- UTC
  PRIMARY KEY (flight_number),
  KEY idx_fs_origin (origin_airport_code),
  KEY idx_fs_dest (destination_airport_code),
  KEY idx_fs_departure (scheduled_departure),
  CONSTRAINT fk_fs_origin FOREIGN KEY (origin_airport_code)
    REFERENCES airport (airport_code) ON DELETE RESTRICT,
  CONSTRAINT fk_fs_dest FOREIGN KEY (destination_airport_code)
    REFERENCES airport (airport_code) ON DELETE RESTRICT,
  CONSTRAINT ck_fs_airports CHECK (origin_airport_code <> destination_airport_code),
  CONSTRAINT ck_fs_times CHECK (scheduled_arrival > scheduled_departure)
) ENGINE=InnoDB;

-- 8. Flight leg (actual times are NULL until the flight operates)
CREATE TABLE flight_leg (
  leg_id                   INT UNSIGNED NOT NULL AUTO_INCREMENT,
  flight_number            VARCHAR(10) NOT NULL,
  origin_airport_code      CHAR(3) NOT NULL,
  destination_airport_code CHAR(3) NOT NULL,
  actual_departure         DATETIME NULL,            -- UTC
  actual_arrival           DATETIME NULL,            -- UTC
  PRIMARY KEY (leg_id),
  KEY idx_leg_flight (flight_number),
  KEY idx_leg_origin (origin_airport_code),
  KEY idx_leg_dest (destination_airport_code),
  CONSTRAINT fk_leg_flight FOREIGN KEY (flight_number)
    REFERENCES flight_schedule (flight_number) ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT fk_leg_origin FOREIGN KEY (origin_airport_code)
    REFERENCES airport (airport_code) ON DELETE RESTRICT,
  CONSTRAINT fk_leg_dest FOREIGN KEY (destination_airport_code)
    REFERENCES airport (airport_code) ON DELETE RESTRICT,
  CONSTRAINT ck_leg_airports CHECK (origin_airport_code <> destination_airport_code),
  CONSTRAINT ck_leg_times CHECK (actual_arrival IS NULL OR actual_departure IS NULL
                                 OR actual_arrival > actual_departure)
) ENGINE=InnoDB;

-- 9. Flight cost (history kept: new rows, never overwrite)
CREATE TABLE flight_cost (
  flight_number      VARCHAR(10) NOT NULL,
  aircraft_type_code VARCHAR(6)  NOT NULL,
  valid_from         DATE NOT NULL,
  valid_to           DATE NOT NULL,
  cost_amount        DECIMAL(10,2) NOT NULL,
  PRIMARY KEY (flight_number, aircraft_type_code, valid_from),
  KEY idx_cost_lookup (flight_number, aircraft_type_code, valid_from, valid_to),
  KEY idx_cost_valid_to (valid_to),
  CONSTRAINT fk_cost_flight FOREIGN KEY (flight_number)
    REFERENCES flight_schedule (flight_number) ON DELETE RESTRICT ON UPDATE CASCADE,
  CONSTRAINT fk_cost_from FOREIGN KEY (valid_from)
    REFERENCES calendar_date (cal_date) ON DELETE RESTRICT,
  CONSTRAINT fk_cost_to FOREIGN KEY (valid_to)
    REFERENCES calendar_date (cal_date) ON DELETE RESTRICT,
  CONSTRAINT ck_cost_amount CHECK (cost_amount >= 0),
  CONSTRAINT ck_cost_dates CHECK (valid_to >= valid_from)
) ENGINE=InnoDB;

-- 10. Reservation-payment link (many-to-many; no allocation amount by design)
CREATE TABLE reservation_payment (
  reservation_id INT UNSIGNED NOT NULL,
  payment_id     INT UNSIGNED NOT NULL,
  PRIMARY KEY (reservation_id, payment_id),          -- blocks duplicate pairs
  KEY idx_rp_payment (payment_id),
  CONSTRAINT fk_rp_res FOREIGN KEY (reservation_id)
    REFERENCES reservation (reservation_id) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_rp_pay FOREIGN KEY (payment_id)
    REFERENCES payment (payment_id) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB;

-- 11. Reservation-leg link (many-to-many)
CREATE TABLE reservation_leg (
  reservation_id INT UNSIGNED NOT NULL,
  leg_id         INT UNSIGNED NOT NULL,
  PRIMARY KEY (reservation_id, leg_id),
  KEY idx_rl_leg (leg_id),
  CONSTRAINT fk_rl_res FOREIGN KEY (reservation_id)
    REFERENCES reservation (reservation_id) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_rl_leg FOREIGN KEY (leg_id)
    REFERENCES flight_leg (leg_id) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB;
