"""Authentication and account recovery routes."""
import hashlib
import time
from careblue.routes.blueprints import AccessBlueprint as Blueprint
from database import IntegrityError, get_db_connection
from flask import g, abort, current_app as app, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from careblue.security import clear_failed_logins, is_login_locked_out, log_audit, record_failed_login
from careblue.accounts import password_error, email_valid, verify_mfa

from careblue.routes.blueprints import access

bp = Blueprint("auth", __name__, public=True)

@bp.get("/")
def home():
    if "user_id" in session:
        from careblue.tenancy import home_endpoint
        return redirect(url_for(home_endpoint()))
    return render_template("auth/login.html")

def authenticate(role=None, slug=None):
    operator_login = role == 'platform'
    hospital = None
    if slug:
        conn = get_db_connection()
        hospital = conn.execute("SELECT * FROM hospitals WHERE slug=?", (slug,)).fetchone()
        if not hospital:
            abort(404)
        g.hospital = hospital
    template = "auth/platform_login.html" if role == "platform" else "auth/doctor_login.html" if role == "doctor" else "auth/login.html"
    if request.method == "POST":
        if is_login_locked_out():
            flash("Too many attempts. Try again in five minutes.", "danger")
            return render_template(template), 429
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        role = role or request.form.get("user_type")
        if role not in {"admin", "doctor", "platform"}:
            abort(400, description="Choose a valid account type.")
        if role == 'platform' and not operator_login:
            abort(400, description='Use the platform operator sign-in page.')
        conn = get_db_connection()
        try:
            if role == "platform":
                users = conn.execute("SELECT * FROM platform_admins WHERE LOWER(email)=?", (username.lower(),)).fetchall()
            else:
                if role == "admin":
                    sql = "SELECT s.*,h.name AS hospital_name FROM staff s JOIN hospitals h ON h.id=s.hospital_id WHERE LOWER(s.email)=?"
                    params = [username.lower()]
                else:
                    sql = "SELECT s.*,h.name AS hospital_name FROM doctors s JOIN hospitals h ON h.id=s.hospital_id WHERE s.username=? AND s.password IS NOT NULL"
                    params = [username]
                if hospital:
                    sql += " AND s.hospital_id=?"
                    params.append(hospital['id'])
                users = conn.execute(sql,params).fetchall()
            user = users[0] if len(users) == 1 else None
        finally:
            conn.close()
        if (user and user["active"] and check_password_hash(user["password"], password)
                and verify_mfa(user, request.form.get("mfa_code", "").strip(), role)):
            if role != "platform":
                from careblue.tenancy import hospital_available
                selected = conn.execute("SELECT * FROM hospitals WHERE id=?", (user['hospital_id'],)).fetchone()
                if not hospital_available(selected):
                    flash("This hospital workspace is unavailable. Contact platform support.","danger")
                    return render_template(template), 403
                g.hospital = selected
            else:
                g.pop('hospital', None)
            session.clear()
            session.permanent = True
            session.update(user_id=user['id'],user_type=role,name=user['name'],session_version=user['session_version'],authenticated_at=int(time.time()))
            if role != "platform":
                session.update(hospital_id=user['hospital_id'],hospital_name=user['hospital_name'])
                if role == 'admin':
                    session['staff_role'] = user['role']
            g.actor = user
            clear_failed_logins()
            log_audit("login", "platform_admin" if role == 'platform' else "staff" if role == 'admin' else 'doctor',user['id'])
            from careblue.tenancy import home_endpoint
            return redirect(url_for(home_endpoint()))
        record_failed_login()
        flash("Invalid credentials, authenticator code, or disabled account.", "danger")
        return render_template(template), 401
    return render_template(template)

@bp.route("/login", methods=["GET", "POST"])
def login():
    return authenticate()

@bp.route("/doctor/login", methods=["GET", "POST"])
def doctor_login():
    return authenticate("doctor")

@bp.route("/h/<slug>", methods=["GET", "POST"])
@bp.route("/h/<slug>/login", methods=["GET", "POST"])
def workspace_login(slug):
    return authenticate(slug=slug)

@bp.route("/platform/login", methods=["GET", "POST"])
def platform_login():
    return authenticate("platform")

@bp.route("/register", methods=["GET", "POST"])
def register():
    if not app.config["REGISTRATION_ENABLED"]:
        abort(404)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        hospital = request.form.get("hospital_name", "").strip()
        error = password_error(password)
        if not email_valid(email) or not name or not hospital or max(len(name), len(hospital)) > 120:
            error = "Enter a valid name, hospital name, and email address."
        if password != request.form.get("confirm_password"):
            error = "Passwords do not match."
        if error:
            flash(error, "danger")
            return render_template("auth/register.html"), 422
        conn = get_db_connection()
        try:
            from careblue.tenancy import onboard
            slug = request.form.get('slug','').strip().lower()
            hospital_id = onboard(conn,name,email,password,hospital,slug)
            conn.commit()
        except ValueError as error:
            conn.rollback()
            flash(str(error),'danger')
            return render_template('auth/register.html'),422
        except IntegrityError:
            conn.rollback()
            flash("An account with these details already exists.", "danger")
            return render_template("auth/register.html"), 409
        finally:
            conn.close()
        session.clear()
        flash("Hospital workspace created. Sign in to customize it and add your team.", "success")
        return redirect(url_for("auth.workspace_login",slug=slug))
    return render_template("auth/register.html")

@bp.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    if request.method == "POST":
        token = request.form.get("token", "")
        password = request.form.get("password", "")
        error = password_error(password)
        if password != request.form.get("confirm_password"):
            error = "Passwords do not match."
        if error:
            flash(error, "danger")
            return render_template("auth/reset_password.html"), 422
        conn = get_db_connection()
        try:
            digest = hashlib.sha256(token.encode()).hexdigest()
            row = conn.execute("SELECT * FROM reset_tokens WHERE token_hash = ? AND expires_at > ?", (digest, int(time.time()))).fetchone()
            if not row:
                flash("This reset token is invalid or expired.", "danger")
                return render_template("auth/reset_password.html"), 400
            cur = conn.execute("DELETE FROM reset_tokens WHERE token_hash = ?", (digest,))
            if cur.rowcount != 1:
                conn.rollback()
                abort(409, description="This token has already been used.")
            table = {"admin":"staff","doctor":"doctors","platform":"platform_admins"}.get(row["actor_type"])
            if not table:
                abort(400)
            conn.execute(f"UPDATE {table} SET password = ?, session_version = session_version + 1 WHERE id = ?",
                         (generate_password_hash(password), row["actor_id"]))
            conn.commit()
        finally:
            conn.close()
        session.clear()
        flash("Password changed. Sign in with your new password.", "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/reset_password.html")

@bp.post("/session/refresh")
def refresh_session():
    if "user_id" not in session:
        return jsonify(ok=False), 401
    session.modified = True
    return jsonify(ok=True, expires_in=int(app.permanent_session_lifetime.total_seconds()))


@bp.get('/session/recover')
def recover_form_session():
    from careblue.security import csrf_token, matches_form_identity
    if not getattr(g, 'actor', None):
        return jsonify(error='Sign in first, then return here to restore saving.'), 401
    if not matches_form_identity(request.headers.get('X-Form-Identity', '')):
        return jsonify(error='Use the same account and hospital that opened this form. If account access changed, open a new form.'), 403
    return jsonify(csrf_token=csrf_token())

@bp.post("/admin/backup")
@access("admin")
def admin_backup():
    from database import is_postgres, backup_database
    if is_postgres():
        flash("Database backups are managed by the PostgreSQL provider.", "info")
    else:
        backup_database()
        log_audit("backup", "database")
        flash("Database snapshot saved.", "success")
    return redirect(url_for("admin.audit_log_view"))

@bp.post("/logout")
def logout():
    log_audit("logout", session.get("user_type") or "unknown", session.get("user_id"))
    session.clear()
    return redirect(url_for("auth.home"))
