import sqlite3
import os
import logging
from CareBlue.database import get_db_path, get_db_connection

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def migrate_database():
    """Migrate the database to add hospital_id to prescriptions table."""
    db_path = get_db_path()
    
    if not os.path.exists(db_path):
        logger.info("Database does not exist. No migration needed.")
        return
    
    logger.info(f"Starting database migration at: {db_path}")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Check if hospital_id column exists in prescriptions table
        cursor.execute("PRAGMA table_info(prescriptions)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'hospital_id' not in columns:
            logger.info("Adding hospital_id column to prescriptions table")
            
            # Create a temporary table with the new schema
            cursor.execute('''
                CREATE TABLE prescriptions_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    appointment_id INTEGER NOT NULL UNIQUE,
                    diagnosis TEXT NOT NULL,
                    medicines TEXT NOT NULL,
                    instructions TEXT,
                    hospital_id INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (appointment_id) REFERENCES appointments(id),
                    FOREIGN KEY (hospital_id) REFERENCES staff(id)
                )
            ''')
            
            # Copy data from the old table to the new one
            cursor.execute('''
                INSERT INTO prescriptions_new (id, appointment_id, diagnosis, medicines, instructions, created_at)
                SELECT id, appointment_id, diagnosis, medicines, instructions, created_at
                FROM prescriptions
            ''')
            
            # Update hospital_id based on the appointment
            cursor.execute('''
                UPDATE prescriptions_new
                SET hospital_id = (
                    SELECT hospital_id
                    FROM appointments
                    WHERE appointments.id = prescriptions_new.appointment_id
                )
            ''')
            
            # Drop the old table
            cursor.execute('DROP TABLE prescriptions')
            
            # Rename the new table to the original name
            cursor.execute('ALTER TABLE prescriptions_new RENAME TO prescriptions')
            
            conn.commit()
            logger.info("Migration completed successfully")
        else:
            logger.info("hospital_id column already exists in prescriptions table")

        # Idempotent performance + integrity indexes (safe to re-run)
        try:
            cursor.execute('''
                CREATE UNIQUE INDEX IF NOT EXISTS idx_appointments_slot
                ON appointments (doctor_id, date, time_slot)
                WHERE status != 'Cancelled'
            ''')
        except sqlite3.IntegrityError:
            # Pre-existing double bookings: leave the index off and rely on
            # the application-level guard; log loudly so data gets cleaned.
            logger.error("Duplicate active slots exist — skipping unique slot index. Clean up duplicates.")
        try:
            cursor.execute('''
                CREATE UNIQUE INDEX IF NOT EXISTS idx_doctor_slots_day
                ON doctor_slots (doctor_id, day_of_week)
            ''')
        except sqlite3.IntegrityError:
            logger.error("Duplicate doctor day-slots exist — skipping unique day index. Clean up duplicates.")
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                actor_id INTEGER,
                actor_type TEXT,
                hospital_id INTEGER,
                action TEXT NOT NULL,
                entity TEXT NOT NULL,
                entity_id INTEGER,
                detail TEXT
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_audit_hospital_time ON audit_log (hospital_id, created_at)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_audit_hospital_time ON audit_log (hospital_id, created_at)')
        for name, ddl in [
            ('wards', '''
             CREATE TABLE IF NOT EXISTS wards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                ward_type TEXT,
                hospital_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (hospital_id) REFERENCES staff(id)
             )'''),
            ('beds', '''
             CREATE TABLE IF NOT EXISTS beds (
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
             )'''),
            ('medicines', '''
             CREATE TABLE IF NOT EXISTS medicines (
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
             )'''),
            ('bills', '''
             CREATE TABLE IF NOT EXISTS bills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                appointment_id INTEGER UNIQUE,
                patient_id INTEGER NOT NULL,
                hospital_id INTEGER NOT NULL,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (appointment_id) REFERENCES appointments(id),
                FOREIGN KEY (patient_id) REFERENCES patients(id),
                FOREIGN KEY (hospital_id) REFERENCES staff(id)
             )'''),
            ('bill_items', '''
             CREATE TABLE IF NOT EXISTS bill_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bill_id INTEGER NOT NULL,
                label TEXT NOT NULL,
                amount REAL NOT NULL,
                FOREIGN KEY (bill_id) REFERENCES bills(id)
             )'''),
            ('payments', '''
             CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bill_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                method TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (bill_id) REFERENCES bills(id)
             )'''),
        ]:
            cursor.execute(ddl)
            logger.info(f"Ensured table {name}")
        for name, ddl in [
            ('idx_appointments_hospital_date',
             'CREATE INDEX IF NOT EXISTS idx_appointments_hospital_date ON appointments (hospital_id, date)'),
            ('idx_appointments_doctor_date',
             'CREATE INDEX IF NOT EXISTS idx_appointments_doctor_date ON appointments (doctor_id, date)'),
            ('idx_patients_hospital',
             'CREATE INDEX IF NOT EXISTS idx_patients_hospital ON patients (hospital_id)'),
            ('idx_doctors_hospital',
             'CREATE INDEX IF NOT EXISTS idx_doctors_hospital ON doctors (hospital_id)'),
            ('idx_prescriptions_appointment',
             'CREATE INDEX IF NOT EXISTS idx_prescriptions_appointment ON prescriptions (appointment_id)'),
            ('idx_beds_ward',
             'CREATE INDEX IF NOT EXISTS idx_beds_ward ON beds (ward_id)'),
            ('idx_beds_patient',
             'CREATE INDEX IF NOT EXISTS idx_beds_patient ON beds (patient_id)'),
            ('idx_medicines_hospital',
             'CREATE INDEX IF NOT EXISTS idx_medicines_hospital ON medicines (hospital_id)'),
            ('idx_bills_patient',
             'CREATE INDEX IF NOT EXISTS idx_bills_patient ON bills (patient_id)'),
            ('idx_bill_items_bill',
             'CREATE INDEX IF NOT EXISTS idx_bill_items_bill ON bill_items (bill_id)'),
            ('idx_payments_bill',
             'CREATE INDEX IF NOT EXISTS idx_payments_bill ON payments (bill_id)'),
        ]:
            cursor.execute(ddl)
            logger.info(f"Ensured index {name}")
        conn.commit()
    
    except Exception as e:
        conn.rollback()
        logger.error(f"Migration error: {e}")
        raise
    finally:
        conn.close()

if __name__ == '__main__':
    migrate_database() 