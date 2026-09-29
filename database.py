import sqlite3
import os
import logging
from datetime import datetime
from werkzeug.security import generate_password_hash
import time

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_db_path():
    """Determine the appropriate database path for different environments."""
    # Use a path within the application's directory structure
    app_dir = os.path.dirname(os.path.abspath(__file__))

    # Vercel serverless: only /tmp is writable (ephemeral, per-instance).
    # Data does not persist across cold starts — use a persistent DB
    # (e.g. Vercel Postgres / Neon) for production data.
    if 'VERCEL' in os.environ:
        db_dir = os.path.join('/tmp', 'careblue-data')
        os.makedirs(db_dir, exist_ok=True)
        return os.path.join(db_dir, 'hospital.db')

    # Check if we're running on Render
    if 'RENDER' in os.environ:
        # Use a directory within the application's directory
        db_dir = os.path.join(app_dir, 'data')
        # Ensure the directory exists
        if not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
            logger.info(f"Created database directory: {db_dir}")
        return os.path.join(db_dir, 'hospital.db')
    else:
        # Local development path
        instance_path = os.path.join(app_dir, 'instance')
        if not os.path.exists(instance_path):
            os.makedirs(instance_path, exist_ok=True)
            logger.info(f"Created instance directory: {instance_path}")
        return os.path.join(instance_path, 'hospital.db')

def init_db():
    """Initialize the database with tables and default admin account."""
    db_path = get_db_path()
    
    # Create directory if it doesn't exist
    db_dir = os.path.dirname(db_path)
    if not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
        logger.info(f"Created database directory: {db_dir}")

    logger.info(f"Initializing database at: {db_path}")
    
    # Check if database exists
    db_exists = os.path.exists(db_path)
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        if not db_exists:
            logger.info("Creating new database with tables")
            # Create tables with improved schema
            cursor.executescript('''
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
            ''')
            
            # Create default admin account
            default_password = generate_password_hash('admin123')
            cursor.execute('''
            INSERT INTO staff (name, email, password, hospital_name)
            VALUES (?, ?, ?, ?)
            ''', ('Admin', 'admin@hospital.com', default_password, 'General Hospital'))
            
            conn.commit()
            logger.info("Database initialized successfully with default admin account")
        else:
            logger.info("Database already exists, skipping initialization")
    except Exception as e:
        logger.error(f"Error initializing database: {e}")
        raise
    finally:
        conn.close()

def get_db_connection():
    """Create and return a database connection with row factory."""
    max_retries = 3
    retry_delay = 1  # seconds
    
    for attempt in range(max_retries):
        try:
            db_path = get_db_path()
            # Ensure the database directory exists
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
            
            conn = sqlite3.connect(db_path, timeout=20)  # Increased timeout
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")  # Enforce declared FK constraints
            
            # Test the connection
            conn.execute("SELECT 1")
            return conn
        except sqlite3.Error as e:
            logger.error(f"Database connection attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                continue
            raise
        except Exception as e:
            logger.error(f"Unexpected error during database connection: {e}")
            raise

def check_database_exists():
    """Check if the database file exists."""
    db_path = get_db_path()
    return os.path.exists(db_path)

def backup_database(keep=7):
    """Online backup via SQLite's backup API into backups/ next to the DB.

    Keeps the newest `keep` snapshots, pruning older ones. Returns the
    snapshot path, or None when there is nothing to back up.
    """
    if 'VERCEL' in os.environ:
        # No persistent disk on Vercel serverless; skip file backups.
        logger.info("Skipping file backup on Vercel (ephemeral filesystem)")
        return None
    db_path = get_db_path()
    if not os.path.exists(db_path):
        return None
    backup_dir = os.path.join(os.path.dirname(os.path.abspath(db_path)), 'backups')
    os.makedirs(backup_dir, exist_ok=True)
    dest = os.path.join(backup_dir, f"hospital-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db")
    src = sqlite3.connect(db_path, timeout=20)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    snapshots = sorted(f for f in os.listdir(backup_dir)
                       if f.startswith('hospital-') and f.endswith('.db'))
    for old in snapshots[:-keep]:
        try:
            os.remove(os.path.join(backup_dir, old))
        except OSError:
            logger.warning(f"Could not prune old backup {old}")
    logger.info(f"Database backup written to {dest}")
    return dest

def backup_database_if_stale(max_age_hours=24, keep=7):
    """Create a backup when the newest snapshot is older than max_age_hours."""
    if 'VERCEL' in os.environ:
        return None
    db_path = get_db_path()
    backup_dir = os.path.join(os.path.dirname(os.path.abspath(db_path)), 'backups')
    newest = 0
    if os.path.isdir(backup_dir):
        for f in os.listdir(backup_dir):
            if f.startswith('hospital-') and f.endswith('.db'):
                try:
                    newest = max(newest, os.path.getmtime(os.path.join(backup_dir, f)))
                except OSError:
                    continue
    if time.time() - newest > max_age_hours * 3600:
        return backup_database(keep=keep)
    return None

if __name__ == '__main__':
    if not check_database_exists():
        print("Initializing database...")
        init_db()
    else:
        print("Database already exists at:", get_db_path())