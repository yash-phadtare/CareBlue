"""Hospital settings, onboarding and permissions for shared platform code."""
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from flask import g, session, url_for
from database import get_db_connection, lock_record
from careblue.accounts import create_administrator, email_valid, password_error
from careblue.security import log_audit
from werkzeug.security import generate_password_hash

STAFF_ROLES = {
    "admin": {"registry", "doctors", "appointments", "beds", "billing", "pharmacy", "settings", "users", "audit"},
    "reception": {"registry", "appointments", "beds"},
    "billing": {"billing"},
    "pharmacy": {"pharmacy"},
}
PLANS = ("Starter", "Growth", "Enterprise")
STATUSES = ("Trial", "Active", "Suspended")


def can(permission):
    actor = getattr(g, "actor", None)
    return bool(actor and session.get("user_type") == "admin" and permission in STAFF_ROLES.get(actor.get("role"), set()))


def home_endpoint():
    if session.get("user_type") == "platform":
        return "platform.dashboard"
    if session.get("user_type") == "doctor":
        return "doctor.doctor_dashboard"
    actor = getattr(g, "actor", None)
    role = actor.get("role", "admin") if actor else session.get("staff_role", "admin")
    return {"admin": "admin.admin_dashboard", "reception": "admin.view_appointments",
            "billing": "admin.billing", "pharmacy": "admin.pharmacy"}.get(role, "auth.login")


def tenant_context():
    from careblue.presentation import brand_palette
    hospital = getattr(g, "hospital", None)
    palette = brand_palette(hospital['brand_color']) if hospital else None
    login_url = url_for("auth.workspace_login", slug=hospital["slug"]) if hospital else url_for("auth.login")
    return {"hospital": hospital, "can": can, "home_url": url_for(home_endpoint()), "login_url": login_url,
            "brand_palette": palette,
            "staff_roles": STAFF_ROLES, "brand_rgb": tuple(int(palette['brand'][i:i+2],16) for i in (1,3,5)) if palette else (22,119,255)}


def hospital_available(hospital):
    if hospital["status"] == "Suspended":
        return False
    return not (hospital["status"] == "Trial" and hospital["trial_ends_at"] and hospital["trial_ends_at"] < datetime.now(timezone.utc).date().isoformat())


def onboard(conn, name, email, password, hospital_name, slug):
    slug = slug.strip().lower()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{1,48}[a-z0-9])?", slug) or len(slug) < 3:
        raise ValueError("Workspace address must contain 3–50 lowercase letters, numbers or hyphens.")
    if not name.strip() or len(name) > 120 or not email_valid(email):
        raise ValueError("Enter a valid administrator name and email address.")
    error = password_error(password)
    if error:
        raise ValueError(error)
    if conn.execute("SELECT id FROM hospitals WHERE slug=?", (slug,)).fetchone():
        raise ValueError("That workspace address is already taken.")
    actor_id = create_administrator(conn, name.strip(), email.strip().lower(), generate_password_hash(password), hospital_name=hospital_name, hospital_slug=slug)
    hospital_id = conn.execute("SELECT hospital_id FROM staff WHERE id=?", (actor_id,)).fetchone()[0]
    trial_end = (datetime.now(timezone.utc) + timedelta(days=14)).date().isoformat()
    conn.execute("UPDATE hospitals SET status='Trial',trial_ends_at=?,contact_email=? WHERE id=?", (trial_end, email.strip().lower(), hospital_id))
    log_audit("onboard", "hospital", hospital_id, hospital_id=hospital_id, actor_id=actor_id, actor_type="admin", conn=conn)
    return hospital_id


def validated_settings(form):
    values = {}
    for field, maximum in {"name":120,"contact_email":254,"phone":40,"address":1000,"document_footer":500,"timezone":80,"brand_color":7,"document_style":20,"opening_time":5,"closing_time":5}.items():
        value = form.get(field, "").strip()
        if len(value) > maximum:
            raise ValueError(f"{field.replace('_',' ').capitalize()} is too long.")
        values[field] = value
    if not values["name"]:
        raise ValueError("Hospital name is required.")
    if values["contact_email"] and not email_valid(values["contact_email"]):
        raise ValueError("Enter a valid contact email.")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}",values["brand_color"]):
        raise ValueError("Choose a valid brand color.")
    if values["document_style"] not in {"standard","compact"}:
        raise ValueError("Choose a document style.")
    try:
        ZoneInfo(values["timezone"])
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("Choose a valid timezone.")
    from careblue.services import validate_slot_input
    error = validate_slot_input(values["opening_time"], values["closing_time"], None, None)
    if error:
        raise ValueError(error)
    return values


def require_last_admin(conn, hospital_id, actor_id, role, active):
    lock_record(conn, "hospitals", hospital_id)
    current = conn.execute("SELECT * FROM staff WHERE id=? AND hospital_id=?", (actor_id, hospital_id)).fetchone()
    if not current:
        raise ValueError("Staff account not found.")
    if current["active"] and current["role"] == "admin" and (not active or role != "admin"):
        others = conn.execute("SELECT COUNT(*) FROM staff WHERE hospital_id=? AND active=1 AND role='admin' AND id!=?", (hospital_id,actor_id)).fetchone()[0]
        if not others:
            raise ValueError("Keep at least one active hospital administrator.")
    return current
