"""Database boundary: SQLite for development, PostgreSQL for production."""
import os
import re
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

try:
    import psycopg
except ImportError:
    psycopg = None

IntegrityError = (sqlite3.IntegrityError, psycopg.IntegrityError) if psycopg else sqlite3.IntegrityError
ROOT = Path(__file__).resolve().parent

def setting(key, default=None):
    from flask import current_app, has_app_context
    return current_app.config.get(key, default) if has_app_context() else os.environ.get(key, default)

def get_db_path():
    return str(Path(setting("DATABASE_PATH", str(ROOT / "instance" / "hospital.db"))).resolve())

def is_postgres():
    return bool(setting("DATABASE_URL"))

class Row(dict):
    """Named rows with integer indexing for existing count queries."""
    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)

def row_values(names, values):
    row = Row(zip(names, values))
    for key, value in list(row.items()):
        if isinstance(value, datetime):
            row[key] = value.isoformat(sep=" ")
    return row

class PgCursor:
    def __init__(self, connection):
        self.connection = connection
        self.raw = connection.raw.cursor()
        self.lastrowid = None
    def execute(self, sql, params=()):
        sql = postgres_sql(sql)
        self.raw.execute(sql, params)
        return self
    @property
    def rowcount(self):
        return self.raw.rowcount
    def fetchone(self):
        value = self.raw.fetchone()
        return row_values([c.name for c in self.raw.description], value) if value is not None else None
    def fetchall(self):
        return [row_values([c.name for c in self.raw.description], r) for r in self.raw.fetchall()]

class PgConnection:
    def __init__(self, url):
        if psycopg is None:
            raise RuntimeError("Install requirements.txt to enable PostgreSQL")
        self.raw = psycopg.connect(url, connect_timeout=10)
        self.raw.execute("SET TIME ZONE 'UTC'")
    def cursor(self):
        return PgCursor(self)
    def execute(self, sql, params=()):
        return self.cursor().execute(sql, params)
    def commit(self):
        self.raw.commit()
    def rollback(self):
        self.raw.rollback()
    def close(self):
        self.raw.close()

    def insert(self, sql, params=()):
        cursor = self.execute(sql.rstrip().rstrip(";") + " RETURNING id", params)
        row = cursor.fetchone()
        cursor.lastrowid = row["id"] if row else None
        return cursor


def postgres_sql(sql):
    """Translate parameter markers only outside SQL strings, identifiers and comments."""
    tokens = re.split(r"('(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|--[^\n]*|/\*[\s\S]*?\*/|\$\$[\s\S]*?\$\$)", sql)
    return "".join(part.replace("%", "%%") if i % 2 else part.replace("%", "%%").replace("?", "%s") for i, part in enumerate(tokens))


class RequestConnection:
    """One connection per request. Local close releases a lease; teardown owns disposal."""
    def __init__(self, raw):
        self.raw = raw

    def __getattr__(self, name):
        return getattr(self.raw, name)

    def close(self):
        pass

    def dispose(self):
        try:
            self.raw.rollback()
        finally:
            self.raw.close()

    def insert(self, sql, params=()):
        if isinstance(self.raw, PgConnection):
            return self.raw.insert(sql, params)
        return self.raw.execute(sql, params)


class SQLiteConnection(sqlite3.Connection):
    def insert(self, sql, params=()):
        return self.execute(sql, params)

def get_db_connection():
    from flask import g, has_request_context
    if has_request_context() and "db_connection" in g:
        return g.db_connection
    url = setting("DATABASE_URL")
    if url:
        conn = PgConnection(url)
    else:
        path = get_db_path()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=10, factory=SQLiteConnection)
        conn.row_factory = lambda cur, row: row_values([d[0] for d in cur.description], row)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
    if has_request_context():
        conn = RequestConnection(conn)
        g.db_connection = conn
    return conn

def close_connections(error=None):
    from flask import g
    conn = g.pop("db_connection", None)
    if conn:
        conn.dispose()


def begin_write(conn):
    """Explicitly acquire SQLite's write lock before a read/validate/write workflow."""
    if not is_postgres():
        conn.execute("BEGIN IMMEDIATE")

def lock_record(conn, table, record_id):
    if table not in {"bills", "beds", "medicines", "prescriptions", "doctors", "patients", "hospitals"}:
        raise ValueError("Invalid lock target")
    if is_postgres():
        conn.execute(f"SELECT id FROM {table} WHERE id = ? FOR UPDATE", (record_id,)).fetchone()

def init_db(conn=None):
    """Create a base schema. Account creation and migrations are separate commands."""
    own = conn is None
    conn = conn or get_db_connection()
    try:
        exists = (conn.execute("SELECT to_regclass(current_schema() || '.staff')").fetchone()[0]
                  if is_postgres() else conn.execute("SELECT name FROM sqlite_master WHERE name = 'staff'").fetchone())
        if exists:
            return
        schema = (ROOT / "careblue" / "schema.sql").read_text(encoding="utf-8")
        if is_postgres():
            schema = schema.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
        for statement in schema.split(";"):
            if statement.strip():
                conn.execute(statement)
        if own:
            conn.commit()
    finally:
        if own:
            conn.close()

def check_database_exists():
    return is_postgres() or Path(get_db_path()).exists()

def backup_database(keep=7):
    if is_postgres():
        raise RuntimeError("Use the PostgreSQL provider's encrypted backups and point-in-time recovery")
    path = Path(get_db_path())
    if not path.exists():
        return None
    target_dir = path.parent / "backups"
    target_dir.mkdir(exist_ok=True)
    target = target_dir / ("hospital-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f") + ".db")
    with sqlite3.connect(path) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
    return str(target)

def backup_database_if_stale(max_age_hours=24, keep=7):
    return None
