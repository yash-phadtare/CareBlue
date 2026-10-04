"""Session policies, shared login throttling, CSRF and audit records."""
import hashlib
import secrets
import time
import logging
from functools import wraps
from flask import current_app, request, session, g, abort, redirect, url_for, jsonify, has_request_context
from database import get_db_connection

def csrf_token():
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_hex(32)
    return session["_csrf_token"]


def form_identity():
    """Bind an in-memory form to its original account and hospital across sign-in."""
    from itsdangerous import URLSafeTimedSerializer
    if not getattr(g, 'actor', None):
        return ''
    return URLSafeTimedSerializer(current_app.secret_key, salt='form-recovery').dumps(
        [session.get(key) for key in ('user_id', 'user_type', 'hospital_id', 'session_version')])


def matches_form_identity(token):
    from itsdangerous import BadData, URLSafeTimedSerializer
    try:
        identity = URLSafeTimedSerializer(current_app.secret_key, salt='form-recovery').loads(token, max_age=86400)
    except (BadData, TypeError):
        return False
    return bool(getattr(g, 'actor', None)) and identity == [session.get(key) for key in ('user_id', 'user_type', 'hospital_id', 'session_version')]

def login_key():
    username = request.form.get("username", "").strip().lower()
    return hashlib.sha256(((request.remote_addr or "unknown") + ":" + username).encode()).hexdigest()

def is_login_locked_out():
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT locked_until FROM login_attempts WHERE key = ?", (login_key(),)).fetchone()
        return bool(row and row["locked_until"] > int(time.time()))
    finally:
        conn.close()

def record_failed_login():
    now = int(time.time())
    conn = get_db_connection()
    try:
        conn.execute("""INSERT INTO login_attempts (key, failures, first_at, locked_until)
          VALUES (?, 1, ?, 0) ON CONFLICT (key) DO UPDATE SET
          failures = CASE WHEN login_attempts.first_at < ? THEN 1 ELSE login_attempts.failures + 1 END,
          first_at = CASE WHEN login_attempts.first_at < ? THEN ? ELSE login_attempts.first_at END,
          locked_until = CASE WHEN login_attempts.first_at >= ? AND login_attempts.failures >= 4 THEN ? ELSE 0 END""",
          (login_key(), now, now - 600, now - 600, now, now - 600, now + 300))
        conn.execute("DELETE FROM login_attempts WHERE first_at < ? AND locked_until < ?", (now - 86400, now))
        conn.commit()
    finally:
        conn.close()

def clear_failed_logins():
    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM login_attempts WHERE key = ?", (login_key(),))
        conn.commit()
    finally:
        conn.close()

def log_audit(action, entity, entity_id=None, detail=None, hospital_id=None, conn=None, actor_id=None, actor_type=None):
    own = conn is None
    conn = conn or get_db_connection()
    try:
        context = session if has_request_context() else {}
        conn.execute("""INSERT INTO audit_log (actor_id, actor_type, hospital_id, action, entity, entity_id, detail)
                        VALUES (?, ?, ?, ?, ?, ?, ?)""",
                     (actor_id if actor_id is not None else context.get("user_id"), actor_type or context.get("user_type"),
                      hospital_id if hospital_id is not None else context.get("hospital_id"),
                      action, entity, entity_id, detail))
        if own:
            conn.commit()
    except Exception:
        if not own:
            raise
        current_app.logger.exception("Audit event failed", extra={"request_id": getattr(g, "request_id", None)})
    finally:
        if own:
            conn.close()

def enforce_request_security():
    g.request_id = secrets.token_hex(8)
    g.csp_nonce = secrets.token_urlsafe(24)
    g.started_at = time.monotonic()
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
        if not token or not secrets.compare_digest(session.get("_csrf_token", ""), token):
            if request.headers.get('X-Requested-With') == 'CareBlue' and request.headers.get('X-Form-Identity'):
                return jsonify(error='Your session expired or needs to be refreshed. Your entries are still here.', session_expired=True), 400
            abort(400, description="Your form expired. Reload the page and try again.")
    endpoint = request.endpoint or ""
    if endpoint == "static":
        if request.view_args.get("filename", "").startswith("images/doctors/"):
            abort(404)
        return
    view = current_app.view_functions.get(endpoint)
    # Undeclared application endpoints fail closed. Static files are public.
    public = endpoint == "static" or bool(view and getattr(view, "public", False))
    protected = not public and view is not None
    if not protected and "user_id" not in session:
        return
    if "user_id" not in session:
        if request.is_json or "/get_" in request.path:
            return jsonify(error="Please sign in."), 401
        g.session_expired = True
        return redirect(url_for("auth.login", next=request.path))
    role = session.get("user_type")
    if role not in {"admin", "doctor", "platform"}:
        session.clear()
        if public:
            return
        abort(401)
    table = {"admin":"staff","doctor":"doctors","platform":"platform_admins"}[role]
    conn = get_db_connection()
    try:
        actor = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (session["user_id"],)).fetchone()
    finally:
        conn.close()
    if (not actor or not actor["active"] or actor["session_version"] != session.get("session_version")
            or int(time.time()) - session.get("authenticated_at", 0) > current_app.config["ABSOLUTE_SESSION_SECONDS"]):
        session.clear()
        if public:
            return
        g.session_expired = True
        return redirect(url_for("auth.login"))
    g.actor = actor
    if role != "platform":
        hospital_id = actor["hospital_id"]
        if hospital_id != session.get("hospital_id"):
            session.clear()
            g.pop("actor", None)
            if public:
                return
            abort(401)
        hospital = conn.execute("SELECT * FROM hospitals WHERE id=?", (hospital_id,)).fetchone()
        from careblue.tenancy import hospital_available
        if not hospital or not hospital_available(hospital):
            session.clear()
            g.pop("actor", None)
            if public:
                return
            abort(403, description="Your hospital workspace is unavailable. Contact platform support.")
        g.hospital = hospital
        session["hospital_name"] = hospital["name"]
        if role == "admin":
            session["staff_role"] = actor["role"]
    if protected:
        if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'} and request.headers.get('X-Form-Identity'):
            if not matches_form_identity(request.headers['X-Form-Identity']):
                return jsonify(error='Sign in with the same account and hospital that opened this form.', session_expired=True), 403
        if role not in getattr(view, "allowed_roles", ()):
            abort(403)
        permission = getattr(view, "permission", None)
        if permission and role == "admin":
            from careblue.tenancy import can
            if not can(permission):
                abort(403)


def begin_request_transaction():
    """All mutation requests validate and write on the same connection/transaction."""
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.endpoint:
        from database import begin_write
        begin_write(get_db_connection())

def security_headers(response):
    # Forms fetch mutations without consuming the destination page's flash messages.
    if request.headers.get("X-Requested-With") == "CareBlue" and response.status_code in {302, 303}:
        errors = [message for category, message in session.get("_flashes", []) if category in {"danger", "warning"}]
        if errors:
            session.pop("_flashes", None)
            response = jsonify(error=" ".join(errors))
            response.status_code = 422
        else:
            payload = {"redirect": response.headers["Location"]}
            if getattr(g, "session_expired", False):
                payload["session_expired"] = True
            response = jsonify(payload)
    nonce = getattr(g, "csp_nonce", "")
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'nonce-" + nonce + "'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; font-src 'self'; "
        "connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'")
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Request-ID"] = getattr(g, "request_id", "")
    if current_app.config["SESSION_COOKIE_SECURE"]:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.endpoint != "static":
        response.headers["Cache-Control"] = "no-store"
    current_app.logger.info("request method=%s path=%s status=%s duration_ms=%s request_id=%s",
        request.method, request.path, response.status_code,
        round((time.monotonic() - getattr(g, "started_at", time.monotonic())) * 1000),
        getattr(g, "request_id", ""))
    return response
