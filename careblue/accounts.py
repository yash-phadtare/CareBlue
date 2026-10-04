import base64
import hashlib
import re
import time
from flask import current_app
from database import get_db_connection

def email_valid(value):
    return bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value)) and len(value) <= 254

def password_error(password):
    if len(password) < 12 or len(password) > 128:
        return "Use a password between 12 and 128 characters."
    if password.lower() in {"password1234", "admin1234567", "123456789012", "doctor123456"}:
        return "Choose a less predictable password."
    return None

def cipher():
    from cryptography.fernet import Fernet, MultiFernet
    key = current_app.config.get("MFA_ENCRYPTION_KEY")
    previous = current_app.config.get("MFA_PREVIOUS_ENCRYPTION_KEYS", "")
    keys = [key] if key else []
    keys.extend(k.strip() for k in previous.split(",") if k.strip())
    if not keys:
        # Read old deployments until the operator explicitly rotates their secrets.
        keys = [base64.urlsafe_b64encode(hashlib.sha256(current_app.secret_key.encode()).digest())]
    return MultiFernet([Fernet(k) for k in keys])

def encrypt_secret(secret):
    if not current_app.config.get("MFA_ENCRYPTION_KEY"):
        raise ValueError("Set a persistent MFA_ENCRYPTION_KEY before enrolling or rotating MFA.")
    return cipher().encrypt(secret.encode()).decode()


def create_administrator(conn, name, email, password_hash, hospital_name=None, hospital_id=None, hospital_slug=None):
    """Hospital ownership belongs to a separate entity; many admins may join it."""
    if hospital_id is None:
        if not hospital_name or not hospital_name.strip() or len(hospital_name) > 120:
            raise ValueError("Enter a hospital name of at most 120 characters.")
        from database import setting
        hospital_id = conn.insert("INSERT INTO hospitals (name,timezone) VALUES (?,?)", (hospital_name.strip(),setting("FACILITY_TIMEZONE","Asia/Kolkata"))).lastrowid
        slug = hospital_slug or f"hospital-{hospital_id}"
        if hospital_slug is None:
            suffix = 1
            while conn.execute("SELECT id FROM hospitals WHERE slug=?", (slug,)).fetchone():
                slug = f"hospital-{hospital_id}-{suffix}"
                suffix += 1
        conn.execute("UPDATE hospitals SET slug=? WHERE id=?", (slug,hospital_id))
    hospital = conn.execute("SELECT name FROM hospitals WHERE id=?", (hospital_id,)).fetchone()
    if not hospital:
        raise ValueError("Hospital not found.")
    return conn.insert("INSERT INTO staff (name,email,password,hospital_id) VALUES (?,?,?,?)",
                       (name, email.lower(), password_hash, hospital_id)).lastrowid

def verify_mfa(account, code, role):
    if not account.get("totp_secret"):
        return True
    import pyotp
    try:
        secret = cipher().decrypt(account["totp_secret"].encode()).decode()
        totp = pyotp.TOTP(secret)
        counter = int(time.time()) // 30
        matching = next((c for c in (counter - 1, counter, counter + 1) if totp.at(c * 30) == code), None)
        if matching is None:
            return False
        table = {"admin":"staff","doctor":"doctors","platform":"platform_admins"}[role]
        conn = get_db_connection()
        try:
            cur = conn.execute(f"UPDATE {table} SET totp_last_counter = ? WHERE id = ? AND totp_last_counter < ?",
                               (matching, account["id"], matching))
            conn.commit()
            return cur.rowcount == 1
        finally:
            conn.close()
    except Exception:
        return False

