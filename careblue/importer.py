"""Copy an existing SQLite database into an empty PostgreSQL database."""
import sqlite3
import tempfile
from pathlib import Path
from flask import current_app
from database import get_db_connection, is_postgres
from careblue.migrations import migrate, columns

TABLES = ("hospitals", "platform_admins", "staff", "doctors", "prescription_templates", "patients", "doctor_slots", "doctor_absences", "appointments", "prescriptions",
          "prescription_versions", "prescription_items", "wards", "beds", "admissions",
          "medicines", "stock_movements", "bills", "bill_items", "payments", "audit_log")

def import_sqlite(source):
    if not is_postgres():
        raise ValueError("DATABASE_URL must identify the target PostgreSQL database.")
    source = Path(source).resolve(strict=True)
    target_url = current_app.config["DATABASE_URL"]
    original_path = current_app.config["DATABASE_PATH"]
    with tempfile.TemporaryDirectory(prefix="careblue-import-") as temp:
        copy = Path(temp) / "source.db"
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as src, sqlite3.connect(copy) as dst:
            src.backup(dst)
        try:
            current_app.config.update(DATABASE_URL=None, DATABASE_PATH=str(copy))
            migrate()
        finally:
            current_app.config.update(DATABASE_URL=target_url, DATABASE_PATH=original_path)
        migrate()
        conn = get_db_connection()
        src = sqlite3.connect(copy)
        src.row_factory = sqlite3.Row
        try:
            conn.execute("SELECT pg_advisory_xact_lock(73192851)")
            if any(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in TABLES):
                raise ValueError("The target must be empty. Existing data will not be overwritten.")
            counts = {}
            for table in TABLES:
                available = columns(conn, table)
                source_columns = [r[1] for r in src.execute(f"PRAGMA table_info({table})")]
                names = [name for name in source_columns if name in available]
                for row in src.execute(f"SELECT * FROM {table} ORDER BY id"):
                    values = [row[name] for name in names]
                    conn.execute(f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join('?' for _ in names)})", values)
                counts[table] = src.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                actual = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                if actual != counts[table]:
                    raise ValueError(f"Row count mismatch in {table}.")
                conn.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), COALESCE(MAX(id), 1), COUNT(*) > 0) FROM {table}")
            conn.commit()
            return counts
        except Exception:
            conn.rollback()
            raise
        finally:
            src.close()
            conn.close()
