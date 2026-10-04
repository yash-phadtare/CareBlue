"""Versioned, transactional migrations shared by SQLite and PostgreSQL."""
import json
import re
from database import get_db_connection, is_postgres, init_db
from careblue.services import to_minor, parse_medicines

LATEST_VERSION = 6

def columns(conn, table):
    if is_postgres():
        return {r[0] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = ?", (table,)).fetchall()}
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}

def add_column(conn, table, name, definition):
    if name not in columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

def migrate():
    conn = get_db_connection()
    try:
        if is_postgres():
            conn.execute("SELECT pg_advisory_xact_lock(73192851)")
        else:
            # Table rebuilds retain IDs and all dependent records in one transaction.
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("PRAGMA legacy_alter_table = ON")
            conn.execute("BEGIN IMMEDIATE")
        init_db(conn)
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        versions = {r[0] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}
        if 1 not in versions:
            if not is_postgres():
                from migrate_db import migrate_database
                migrate_database(conn)
            foundation(conn)
            conn.execute("INSERT INTO schema_migrations (version) VALUES (1)")
        if 2 not in versions:
            clinical(conn)
            conn.execute("INSERT INTO schema_migrations (version) VALUES (2)")
        if 3 not in versions:
            operations(conn)
            conn.execute("INSERT INTO schema_migrations (version) VALUES (3)")
        if 4 not in versions:
            architecture(conn)
            conn.execute("INSERT INTO schema_migrations (version) VALUES (4)")
        if 5 not in versions:
            multitenancy(conn)
            conn.execute("INSERT INTO schema_migrations (version) VALUES (5)")
        if 6 not in versions:
            efficient_workflows(conn)
            conn.execute("INSERT INTO schema_migrations (version) VALUES (6)")
        if not is_postgres() and conn.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Migration found invalid foreign keys; no changes were committed.")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if not is_postgres():
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA legacy_alter_table = OFF")
        conn.close()

def efficient_workflows(conn):
    # Unknown age is distinct from age zero; queue visits have no reserved time.
    for table, field in (("patients", "age"), ("appointments", "time_slot")):
        if is_postgres():
            conn.execute(f"ALTER TABLE {table} ALTER COLUMN {field} DROP NOT NULL")
        else:
            rebuild_sqlite_table(conn, table, lambda ddl, field=field: re.sub(
                r"\b" + field + r"\s+(INTEGER|TEXT)\s+NOT\s+NULL", field + r" \1", ddl, flags=re.I))
        add_column(conn, table, "idempotency_key", "TEXT")
        conn.execute(f"CREATE UNIQUE INDEX idx_{table}_request ON {table}(hospital_id,idempotency_key) WHERE idempotency_key IS NOT NULL")
    add_column(conn,'patients','clinical_version','INTEGER NOT NULL DEFAULT 1')
    serial = "BIGSERIAL PRIMARY KEY" if is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    conn.execute(f"""CREATE TABLE prescription_templates (
        id {serial}, hospital_id BIGINT NOT NULL REFERENCES hospitals(id),
        doctor_id BIGINT NOT NULL REFERENCES doctors(id), name TEXT NOT NULL,
        diagnosis TEXT NOT NULL, medicines TEXT NOT NULL, instructions TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(hospital_id,doctor_id,name))""")


def foundation(conn):
    for table in ("staff", "doctors"):
        add_column(conn, table, "active", "INTEGER NOT NULL DEFAULT 1")
        add_column(conn, table, "session_version", "INTEGER NOT NULL DEFAULT 1")
        add_column(conn, table, "totp_secret", "TEXT")
        add_column(conn, table, "totp_last_counter", "BIGINT NOT NULL DEFAULT -1")
    for table, field in (("doctors", "consultation_fee"), ("medicines", "unit_price"), ("bill_items", "amount"), ("payments", "amount")):
        add_column(conn, table, field + "_cents", "BIGINT NOT NULL DEFAULT 0 CHECK (" + field + "_cents >= 0)")
        for row in conn.execute(f"SELECT id, {field} FROM {table}").fetchall():
            conn.execute(f"UPDATE {table} SET {field}_cents = ? WHERE id = ?", (to_minor(row[field] or 0), row["id"]))
    conn.execute("CREATE TABLE IF NOT EXISTS login_attempts (key TEXT PRIMARY KEY, failures INTEGER NOT NULL, first_at BIGINT NOT NULL, locked_until BIGINT NOT NULL DEFAULT 0)")
    conn.execute("CREATE TABLE IF NOT EXISTS reset_tokens (token_hash TEXT PRIMARY KEY, actor_type TEXT NOT NULL, actor_id BIGINT NOT NULL, expires_at BIGINT NOT NULL)")
    for ddl in (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_appointments_slot ON appointments (doctor_id, date, time_slot) WHERE status != 'Cancelled'",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_doctor_slots_day ON doctor_slots (doctor_id, day_of_week)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_one_occupied_bed ON beds (patient_id) WHERE status = 'Occupied'",
        "CREATE INDEX IF NOT EXISTS idx_appointments_hospital_date ON appointments (hospital_id, date)",
        "CREATE INDEX IF NOT EXISTS idx_appointments_doctor_date ON appointments (doctor_id, date)",
        "CREATE INDEX IF NOT EXISTS idx_patients_hospital ON patients (hospital_id)",
        "CREATE INDEX IF NOT EXISTS idx_doctors_hospital ON doctors (hospital_id)",
        "CREATE INDEX IF NOT EXISTS idx_bills_hospital ON bills (hospital_id)",
    ):
        conn.execute(ddl)
    # Existing known demo credentials cannot authenticate after migration.
    from werkzeug.security import check_password_hash
    for table, field, username, password in (("staff", "email", "admin@hospital.com", "admin123"),
            ("doctors", "username", "dr.meera", "doctor123"), ("doctors", "username", "dr.arjun", "doctor123")):
        account = conn.execute(f"SELECT id, password FROM {table} WHERE {field} = ?", (username,)).fetchone()
        if account and account["password"] and check_password_hash(account["password"], password):
            conn.execute(f"UPDATE {table} SET active = 0, session_version = session_version + 1 WHERE id = ?", (account["id"],))
    constraints = {
        "patients": "NEW.age BETWEEN 0 AND 130",
        "appointments": "NEW.status IN ('Scheduled','Completed','Cancelled') AND EXISTS (SELECT 1 FROM patients p WHERE p.id=NEW.patient_id AND p.hospital_id=NEW.hospital_id) AND EXISTS (SELECT 1 FROM doctors d WHERE d.id=NEW.doctor_id AND d.hospital_id=NEW.hospital_id)",
        "doctor_slots": "EXISTS (SELECT 1 FROM doctors d WHERE d.id=NEW.doctor_id AND d.hospital_id=NEW.hospital_id)",
        "prescriptions": "EXISTS (SELECT 1 FROM appointments a WHERE a.id=NEW.appointment_id AND a.hospital_id=NEW.hospital_id)",
        "medicines": "NEW.stock_qty >= 0 AND NEW.reorder_level >= 0",
        "beds": "NEW.status IN ('Available','Occupied','Maintenance') AND ((NEW.status='Occupied' AND NEW.patient_id IS NOT NULL) OR (NEW.status!='Occupied' AND NEW.patient_id IS NULL)) AND EXISTS (SELECT 1 FROM wards w WHERE w.id=NEW.ward_id AND w.hospital_id=NEW.hospital_id) AND (NEW.patient_id IS NULL OR EXISTS (SELECT 1 FROM patients p WHERE p.id=NEW.patient_id AND p.hospital_id=NEW.hospital_id))",
        "bills": "EXISTS (SELECT 1 FROM patients p WHERE p.id=NEW.patient_id AND p.hospital_id=NEW.hospital_id) AND (NEW.appointment_id IS NULL OR EXISTS (SELECT 1 FROM appointments a WHERE a.id=NEW.appointment_id AND a.patient_id=NEW.patient_id AND a.hospital_id=NEW.hospital_id))"
    }
    for table, condition in constraints.items():
        if is_postgres():
            conn.execute(f"""CREATE OR REPLACE FUNCTION validate_{table}() RETURNS trigger AS $$
            BEGIN IF NOT ({condition}) THEN RAISE EXCEPTION 'Invalid {table} relationship or value' USING ERRCODE = '23514'; END IF; RETURN NEW; END; $$ LANGUAGE plpgsql""")
            conn.execute(f"CREATE TRIGGER guard_{table} BEFORE INSERT OR UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION validate_{table}()")
        else:
            for operation in ("INSERT", "UPDATE"):
                conn.execute(f"""CREATE TRIGGER IF NOT EXISTS guard_{table}_{operation.lower()}
                BEFORE {operation} ON {table} WHEN NOT ({condition})
                BEGIN SELECT RAISE(ABORT, 'Invalid {table} relationship or value'); END""")

def clinical(conn):
    serial = "BIGSERIAL PRIMARY KEY" if is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    for ddl in (
        f"""CREATE TABLE admissions (id {serial}, bed_id BIGINT NOT NULL REFERENCES beds(id),
            patient_id BIGINT NOT NULL REFERENCES patients(id), hospital_id BIGINT NOT NULL REFERENCES staff(id),
            admitted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, discharged_at TIMESTAMP)""",
        f"""CREATE TABLE prescription_versions (id {serial}, prescription_id BIGINT NOT NULL REFERENCES prescriptions(id),
            version INTEGER NOT NULL, doctor_id BIGINT NOT NULL REFERENCES doctors(id),
            status TEXT NOT NULL CHECK (status IN ('Draft','Signed','Amended')),
            diagnosis TEXT NOT NULL, instructions TEXT, medicines TEXT NOT NULL, reason TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(prescription_id, version))""",
        f"""CREATE TABLE prescription_items (id {serial}, version_id BIGINT NOT NULL REFERENCES prescription_versions(id),
            name TEXT NOT NULL, strength TEXT, dosage TEXT NOT NULL, route TEXT, frequency TEXT NOT NULL,
            duration TEXT, meal TEXT, morning INTEGER, afternoon INTEGER, evening INTEGER)""",
        f"""CREATE TABLE stock_movements (id {serial}, medicine_id BIGINT NOT NULL REFERENCES medicines(id),
            delta INTEGER NOT NULL, balance INTEGER NOT NULL, reason TEXT NOT NULL, actor_id BIGINT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
        "CREATE INDEX idx_admissions_hospital ON admissions (hospital_id, admitted_at)",
        "CREATE UNIQUE INDEX idx_admissions_active_patient ON admissions (patient_id) WHERE discharged_at IS NULL",
        "CREATE UNIQUE INDEX idx_admissions_active_bed ON admissions (bed_id) WHERE discharged_at IS NULL",
    ):
        conn.execute(ddl)
    add_column(conn, "prescriptions", "version", "INTEGER NOT NULL DEFAULT 0")
    add_column(conn, "prescriptions", "status", "TEXT NOT NULL DEFAULT 'Draft'")
    add_column(conn, "patients", "date_of_birth", "TEXT")
    add_column(conn, "patients", "allergies", "TEXT NOT NULL DEFAULT ''")
    for bed in conn.execute("SELECT * FROM beds WHERE status = 'Occupied'").fetchall():
        conn.execute("INSERT INTO admissions (bed_id, patient_id, hospital_id) VALUES (?, ?, ?)", (bed["id"], bed["patient_id"], bed["hospital_id"]))
    for row in conn.execute("SELECT pr.*, a.doctor_id FROM prescriptions pr JOIN appointments a ON a.id = pr.appointment_id").fetchall():
        items = parse_medicines(row["medicines"])
        payload = json.dumps(items, ensure_ascii=False)
        cur = conn.insert("INSERT INTO prescription_versions (prescription_id, version, doctor_id, status, diagnosis, instructions, medicines, reason) VALUES (?, 1, ?, 'Draft', ?, ?, ?, ?)",
                           (row["id"], row["doctor_id"], row["diagnosis"], row["instructions"], payload, "Imported legacy record; not electronically signed"))
        insert_medicine_items(conn, cur.lastrowid, items)
        conn.execute("UPDATE prescriptions SET medicines = ?, version = 1 WHERE id = ?", (payload, row["id"]))
    # Version records are append-only, including their normalized medicine rows.
    for table in ("prescription_versions", "prescription_items", "audit_log", "stock_movements"):
        if is_postgres():
            conn.execute(f"""CREATE OR REPLACE FUNCTION protect_{table}() RETURNS trigger AS $$
                BEGIN RAISE EXCEPTION 'Historical records are immutable' USING ERRCODE = '23514'; END; $$ LANGUAGE plpgsql""")
            conn.execute(f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION protect_{table}()")
        else:
            for operation in ("UPDATE", "DELETE"):
                conn.execute(f"CREATE TRIGGER immutable_{table}_{operation.lower()} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT, 'Historical records are immutable'); END")

def insert_medicine_items(conn, version_id, items):
    for item in items:
        conn.execute("""INSERT INTO prescription_items (version_id, name, strength, dosage, route, frequency, duration, meal, morning, afternoon, evening)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                     (version_id, item["name"], item.get("strength", ""), item["dosage"], item.get("route", ""),
                      item["frequency"], item.get("duration", ""), item["meal"],
                      int(item["morning"]), int(item["afternoon"]), int(item["evening"])))

def operations(conn):
    serial = "BIGSERIAL PRIMARY KEY" if is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    conn.execute(f"""CREATE TABLE doctor_absences (id {serial}, doctor_id BIGINT NOT NULL REFERENCES doctors(id),
        hospital_id BIGINT NOT NULL REFERENCES staff(id), start_date TEXT NOT NULL, end_date TEXT NOT NULL,
        reason TEXT NOT NULL, CHECK(start_date <= end_date))""")
    conn.execute("CREATE INDEX idx_absences_doctor_date ON doctor_absences (doctor_id, start_date, end_date)")
    add_column(conn, "payments", "idempotency_key", "TEXT")
    add_column(conn, "bills", "idempotency_key", "TEXT")
    conn.execute("CREATE UNIQUE INDEX idx_payment_request ON payments (bill_id, idempotency_key)")
    conn.execute("CREATE UNIQUE INDEX idx_bill_request ON bills (hospital_id, idempotency_key)")


def rebuild_sqlite_table(conn, table, transform):
    """Preserve table data, indexes and triggers while changing FK/column definitions."""
    ddl = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
    sequence = conn.execute('SELECT seq FROM sqlite_sequence WHERE name=?',(table,)).fetchone()
    objects = conn.execute("SELECT sql FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger') AND sql IS NOT NULL", (table,)).fetchall()
    new = table + "_upgrade"
    ddl = re.sub(r'CREATE TABLE\s+(?:"' + table + r'"|' + table + r')', "CREATE TABLE " + new, transform(ddl), count=1, flags=re.I)
    conn.execute(ddl)
    names = columns(conn, table) & columns(conn, new)
    names = sorted(names)
    fields = ",".join(names)
    conn.execute(f"INSERT INTO {new} ({fields}) SELECT {fields} FROM {table}")
    conn.execute(f"DROP TABLE {table}")
    conn.execute(f"ALTER TABLE {new} RENAME TO {table}")
    if sequence:
        # Deleted record numbers must not be reused after rebuilding a table.
        updated=conn.execute('UPDATE sqlite_sequence SET seq=MAX(seq,?) WHERE name=?',(sequence[0],table))
        if not updated.rowcount:
            conn.execute('INSERT INTO sqlite_sequence(name,seq) VALUES(?,?)',(table,sequence[0]))
    for obj in objects:
        conn.execute(obj[0])


def architecture(conn):
    serial = "BIGSERIAL PRIMARY KEY" if is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    conn.execute(f"CREATE TABLE hospitals (id {serial}, name TEXT NOT NULL, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("INSERT INTO hospitals (id,name) SELECT id,hospital_name FROM staff")
    add_column(conn, "staff", "hospital_id", "BIGINT REFERENCES hospitals(id)")
    conn.execute("UPDATE staff SET hospital_id=id")
    if is_postgres():
        conn.execute("SELECT setval(pg_get_serial_sequence('hospitals','id'),COALESCE(MAX(id),1),COUNT(*)>0) FROM hospitals")
        conn.execute("ALTER TABLE staff ALTER COLUMN hospital_id SET NOT NULL")
        refs = conn.execute("""SELECT tc.table_name,tc.constraint_name FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage k USING (constraint_catalog,constraint_schema,constraint_name)
            WHERE tc.table_schema=current_schema() AND tc.constraint_type='FOREIGN KEY' AND k.column_name='hospital_id' AND tc.table_name!='staff'""").fetchall()
        for ref in refs:
            table = ref["table_name"]
            conn.execute(f'ALTER TABLE {table} DROP CONSTRAINT "{ref["constraint_name"]}"')
            conn.execute(f"ALTER TABLE {table} ADD FOREIGN KEY (hospital_id) REFERENCES hospitals(id)")
    else:
        for table in ("staff", "doctors", "patients", "appointments", "prescriptions", "doctor_slots", "wards", "beds", "medicines", "bills", "admissions", "doctor_absences"):
            def transform(ddl):
                ddl = re.sub(r"(FOREIGN KEY\s*\(hospital_id\)\s*REFERENCES\s+)staff", r"\1hospitals", ddl, flags=re.I)
                ddl = re.sub(r"(hospital_id\s+BIGINT\s+NOT NULL\s+REFERENCES\s+)staff", r"\1hospitals", ddl, flags=re.I)
                if table == "staff":
                    ddl = ddl.replace("hospital_id BIGINT REFERENCES", "hospital_id BIGINT NOT NULL REFERENCES")
                return ddl
            rebuild_sqlite_table(conn, table, transform)
    conn.execute("CREATE INDEX idx_staff_hospital ON staff(hospital_id)")
    conn.execute("ALTER TABLE staff DROP COLUMN hospital_name")
    for table in ("beds", "medicines"):
        add_column(conn, table, "archived", "INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1))")
    add_column(conn, "prescription_versions", "identity_snapshot", "TEXT NOT NULL DEFAULT '{}'")
    # Existing immutable versions cannot reconstruct historical identities. Record current
    # identity once and mark its provenance, without claiming it was captured at signing.
    if is_postgres():
        conn.execute("ALTER TABLE prescription_versions DISABLE TRIGGER immutable_prescription_versions")
    else:
        conn.execute("DROP TRIGGER immutable_prescription_versions_update")
    from careblue.workflows import prescription_snapshot
    rows = conn.execute("""SELECT v.id,p.name AS patient_name,p.age,p.gender,p.date_of_birth,p.allergies,
        d.name AS doctor_name,d.specialization,h.name AS hospital_name,a.date,a.time_slot
        FROM prescription_versions v JOIN prescriptions pr ON pr.id=v.prescription_id
        JOIN appointments a ON a.id=pr.appointment_id JOIN patients p ON p.id=a.patient_id
        JOIN doctors d ON d.id=v.doctor_id JOIN hospitals h ON h.id=a.hospital_id""").fetchall()
    for row in rows:
        snapshot = json.loads(prescription_snapshot(row))
        snapshot["identity_provenance"] = "Captured at migration; original identity unavailable"
        conn.execute("UPDATE prescription_versions SET identity_snapshot=? WHERE id=?", (json.dumps(snapshot), row["id"]))
    if is_postgres():
        conn.execute("ALTER TABLE prescription_versions ENABLE TRIGGER immutable_prescription_versions")
    else:
        conn.execute("CREATE TRIGGER immutable_prescription_versions_update BEFORE UPDATE ON prescription_versions BEGIN SELECT RAISE(ABORT,'Historical records are immutable'); END")
    # Integer minor units are the only stored source of monetary values.
    for table, field in (("doctors", "consultation_fee"), ("medicines", "unit_price"), ("bill_items", "amount"), ("payments", "amount")):
        conn.execute(f"ALTER TABLE {table} DROP COLUMN {field}")


def multitenancy(conn):
    serial = "BIGSERIAL PRIMARY KEY" if is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    for name, definition in {
        "slug": "TEXT", "status": "TEXT NOT NULL DEFAULT 'Active' CHECK(status IN ('Trial','Active','Suspended'))",
        "plan": "TEXT NOT NULL DEFAULT 'Starter' CHECK(plan IN ('Starter','Growth','Enterprise'))",
        "timezone": "TEXT NOT NULL DEFAULT 'Asia/Kolkata'", "brand_color": "TEXT NOT NULL DEFAULT '#1677FF'",
        "logo_path": "TEXT", "contact_email": "TEXT NOT NULL DEFAULT ''", "phone": "TEXT NOT NULL DEFAULT ''",
        "address": "TEXT NOT NULL DEFAULT ''", "document_footer": "TEXT NOT NULL DEFAULT ''",
        "document_style": "TEXT NOT NULL DEFAULT 'standard' CHECK(document_style IN ('standard','compact'))",
        "opening_time": "TEXT NOT NULL DEFAULT '09:00'", "closing_time": "TEXT NOT NULL DEFAULT '17:00'",
        "trial_ends_at": "TEXT", "settings_version": "INTEGER NOT NULL DEFAULT 1"
    }.items():
        add_column(conn, "hospitals", name, definition)
    from database import setting
    from zoneinfo import ZoneInfo
    zone = setting("FACILITY_TIMEZONE", "Asia/Kolkata")
    ZoneInfo(zone)
    conn.execute("UPDATE hospitals SET timezone=?", (zone,))
    conn.execute("UPDATE hospitals SET slug='hospital-' || CAST(id AS TEXT)")
    conn.execute("CREATE UNIQUE INDEX idx_hospital_slug ON hospitals(slug)")
    add_column(conn, "staff", "role", "TEXT NOT NULL DEFAULT 'admin' CHECK(role IN ('admin','reception','billing','pharmacy'))")
    conn.execute(f"""CREATE TABLE platform_admins (id {serial}, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, session_version INTEGER NOT NULL DEFAULT 1,
        totp_secret TEXT, totp_last_counter BIGINT NOT NULL DEFAULT -1, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    # Usernames and staff emails may be reused by independent hospital workspaces.
    for table, field in (("staff", "email"), ("doctors", "username")):
        if is_postgres():
            constraints = conn.execute("""SELECT tc.constraint_name FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage k USING(constraint_catalog,constraint_schema,constraint_name)
                WHERE tc.table_schema=current_schema() AND tc.table_name=? AND tc.constraint_type='UNIQUE' AND k.column_name=?""", (table, field)).fetchall()
            for row in constraints:
                conn.execute(f'ALTER TABLE {table} DROP CONSTRAINT "{row[0]}"')
        else:
            rebuild_sqlite_table(conn, table, lambda ddl: re.sub(r"\b" + field + r"\s+TEXT\s+UNIQUE", field + " TEXT", ddl, flags=re.I))
        conn.execute(f"CREATE UNIQUE INDEX idx_{table}_tenant_login ON {table}(hospital_id, {field})")
