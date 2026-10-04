-- Version-0 baseline. Apply versioned changes through flask db-upgrade.
CREATE TABLE staff (
id INTEGER PRIMARY KEY AUTOINCREMENT,
name TEXT NOT NULL,
email TEXT UNIQUE NOT NULL,
password TEXT NOT NULL,
hospital_name TEXT NOT NULL,
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE doctors (
id INTEGER PRIMARY KEY AUTOINCREMENT,
name TEXT NOT NULL,
specialization TEXT NOT NULL,
experience INTEGER,
consultation_fee REAL,
contact TEXT,
bio TEXT,
image_path TEXT,
username TEXT UNIQUE,
password TEXT,
created_by INTEGER,
hospital_id INTEGER NOT NULL,
FOREIGN KEY (created_by) REFERENCES staff(id),
FOREIGN KEY (hospital_id) REFERENCES staff(id)
);

CREATE TABLE patients (
id INTEGER PRIMARY KEY AUTOINCREMENT,
name TEXT NOT NULL,
age INTEGER NOT NULL,
gender TEXT,
contact TEXT,
address TEXT,
medical_history TEXT,
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
created_by INTEGER,
hospital_id INTEGER NOT NULL,
FOREIGN KEY (created_by) REFERENCES staff(id),
FOREIGN KEY (hospital_id) REFERENCES staff(id)
);

CREATE TABLE appointments (
id INTEGER PRIMARY KEY AUTOINCREMENT,
patient_id INTEGER NOT NULL,
doctor_id INTEGER NOT NULL,
date TEXT NOT NULL,
time_slot TEXT NOT NULL,
status TEXT DEFAULT 'Scheduled',
notes TEXT,
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
hospital_id INTEGER NOT NULL,
FOREIGN KEY (patient_id) REFERENCES patients(id),
FOREIGN KEY (doctor_id) REFERENCES doctors(id),
FOREIGN KEY (hospital_id) REFERENCES staff(id)
);

CREATE TABLE prescriptions (
id INTEGER PRIMARY KEY AUTOINCREMENT,
appointment_id INTEGER NOT NULL UNIQUE,
diagnosis TEXT NOT NULL,
medicines TEXT NOT NULL,
instructions TEXT,
hospital_id INTEGER NOT NULL,
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
FOREIGN KEY (appointment_id) REFERENCES appointments(id),
FOREIGN KEY (hospital_id) REFERENCES staff(id)
);

CREATE TABLE doctor_slots (
id INTEGER PRIMARY KEY AUTOINCREMENT,
doctor_id INTEGER NOT NULL,
day_of_week TEXT NOT NULL,
start_time TEXT NOT NULL,
end_time TEXT NOT NULL,
break_start TEXT,
break_end TEXT,
hospital_id INTEGER NOT NULL,
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
FOREIGN KEY (doctor_id) REFERENCES doctors(id),
FOREIGN KEY (hospital_id) REFERENCES staff(id)
);

CREATE TABLE audit_log (
id INTEGER PRIMARY KEY AUTOINCREMENT,
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
actor_id INTEGER,
actor_type TEXT,
hospital_id INTEGER,
action TEXT NOT NULL,
entity TEXT NOT NULL,
entity_id INTEGER,
detail TEXT
);
CREATE INDEX idx_audit_hospital_time ON audit_log (hospital_id, created_at);

CREATE TABLE wards (
id INTEGER PRIMARY KEY AUTOINCREMENT,
name TEXT NOT NULL,
ward_type TEXT,
hospital_id INTEGER NOT NULL,
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
FOREIGN KEY (hospital_id) REFERENCES staff(id)
);

CREATE TABLE beds (
id INTEGER PRIMARY KEY AUTOINCREMENT,
ward_id INTEGER NOT NULL,
bed_number TEXT NOT NULL,
status TEXT NOT NULL DEFAULT 'Available',
patient_id INTEGER,
hospital_id INTEGER NOT NULL,
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
FOREIGN KEY (ward_id) REFERENCES wards(id),
FOREIGN KEY (patient_id) REFERENCES patients(id),
FOREIGN KEY (hospital_id) REFERENCES staff(id),
UNIQUE (ward_id, bed_number)
);
CREATE INDEX idx_beds_ward ON beds (ward_id);

CREATE TABLE medicines (
id INTEGER PRIMARY KEY AUTOINCREMENT,
name TEXT NOT NULL,
strength TEXT,
unit TEXT,
stock_qty INTEGER NOT NULL DEFAULT 0,
reorder_level INTEGER NOT NULL DEFAULT 10,
unit_price REAL NOT NULL DEFAULT 0,
hospital_id INTEGER NOT NULL,
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
FOREIGN KEY (hospital_id) REFERENCES staff(id)
);
CREATE INDEX idx_medicines_hospital ON medicines (hospital_id);

CREATE TABLE bills (
id INTEGER PRIMARY KEY AUTOINCREMENT,
appointment_id INTEGER UNIQUE,
patient_id INTEGER NOT NULL,
hospital_id INTEGER NOT NULL,
notes TEXT,
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
FOREIGN KEY (appointment_id) REFERENCES appointments(id),
FOREIGN KEY (patient_id) REFERENCES patients(id),
FOREIGN KEY (hospital_id) REFERENCES staff(id)
);

CREATE TABLE bill_items (
id INTEGER PRIMARY KEY AUTOINCREMENT,
bill_id INTEGER NOT NULL,
label TEXT NOT NULL,
amount REAL NOT NULL,
FOREIGN KEY (bill_id) REFERENCES bills(id)
);
CREATE INDEX idx_bill_items_bill ON bill_items (bill_id);

CREATE TABLE payments (
id INTEGER PRIMARY KEY AUTOINCREMENT,
bill_id INTEGER NOT NULL,
amount REAL NOT NULL,
method TEXT NOT NULL,
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
FOREIGN KEY (bill_id) REFERENCES bills(id)
);
CREATE INDEX idx_payments_bill ON payments (bill_id);
