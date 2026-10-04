"""Application factory with no startup writes."""
import os
import secrets
from datetime import timedelta
from pathlib import Path
from flask import Flask, jsonify, render_template, g
from werkzeug.exceptions import HTTPException

ROOT = Path(__file__).resolve().parent.parent

def create_app(test_config=None):
    app = Flask(__name__, template_folder=str(ROOT / "templates"), static_folder=str(ROOT / "static"))
    production = bool(os.getenv("VERCEL") or os.getenv("RENDER") or os.getenv("FLASK_ENV") == "production")
    app.config.from_mapping(
        SECRET_KEY=os.getenv("SECRET_KEY"),
        MFA_ENCRYPTION_KEY=os.getenv("MFA_ENCRYPTION_KEY"),
        MFA_PREVIOUS_ENCRYPTION_KEYS=os.getenv("MFA_PREVIOUS_ENCRYPTION_KEYS", ""),
        DATABASE_URL=os.getenv("DATABASE_URL"),
        DATABASE_PATH=os.getenv("DATABASE_PATH", str(ROOT / "instance" / "hospital.db")),
        FACILITY_TIMEZONE=os.getenv("FACILITY_TIMEZONE", "Asia/Kolkata"),
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=production,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=2),
        ABSOLUTE_SESSION_SECONDS=8 * 3600,
        MAX_CONTENT_LENGTH=5 * 1024 * 1024,
        MAX_FORM_MEMORY_SIZE=256 * 1024,
        MAX_FORM_PARTS=600,
        ALLOWED_EXTENSIONS={"png", "jpg", "jpeg"},
        UPLOAD_FOLDER=str(ROOT / "instance" / "uploads"),
        REGISTRATION_ENABLED=os.getenv("REGISTRATION_ENABLED", "1") == "1",
        S3_BUCKET=os.getenv("S3_BUCKET"), S3_ENDPOINT_URL=os.getenv("S3_ENDPOINT_URL"),
        S3_REGION=os.getenv("S3_REGION", "us-east-1"),
        S3_ACCESS_KEY_ID=os.getenv("S3_ACCESS_KEY_ID"), S3_SECRET_ACCESS_KEY=os.getenv("S3_SECRET_ACCESS_KEY"),
        PRODUCTION=production,
    )
    if test_config:
        app.config.update(test_config)
    if not app.config["SECRET_KEY"]:
        if not app.testing:
            raise RuntimeError("Set a persistent SECRET_KEY before starting CareBlue.")
        app.config["SECRET_KEY"] = secrets.token_hex(32)
    if production and not app.testing and not app.config["DATABASE_URL"]:
        raise RuntimeError("Production requires a persistent PostgreSQL DATABASE_URL.")
    if production and not app.testing and not app.config["S3_BUCKET"]:
        raise RuntimeError("Production requires S3_BUCKET for persistent uploads.")
    if os.getenv("TRUSTED_HOSTS"):
        app.config["TRUSTED_HOSTS"] = os.environ["TRUSTED_HOSTS"].split(",")
    from database import close_connections, get_db_connection
    from careblue import security
    from careblue.routes.blueprints import access as security_access
    from careblue.common import utility_processor, header_stats
    from careblue.routes import auth, admin, doctor, lookups, history, availability, registry, appointments, billing, pharmacy, beds, tenant, platform
    from careblue.management import register_commands
    for blueprint in (auth.bp, admin.bp, doctor.bp, lookups.bp, history.bp, availability.bp, tenant.bp, platform.bp):
        app.register_blueprint(blueprint)
    app.before_request(security.enforce_request_security)
    app.before_request(security.begin_request_transaction)
    app.after_request(security.security_headers)
    app.teardown_appcontext(close_connections)
    app.context_processor(utility_processor)
    app.context_processor(header_stats)
    from careblue.tenancy import tenant_context
    app.context_processor(tenant_context)
    app.context_processor(lambda: {"pagers": getattr(g, "pagers", []), "sort_options": getattr(g, "sort_options", {}), "csp_nonce": getattr(g, "csp_nonce", ""),
                                   "registration_enabled": app.config["REGISTRATION_ENABLED"]})
    app.jinja_env.globals["csrf_token"] = security.csrf_token
    app.jinja_env.globals["form_identity"] = security.form_identity
    app.jinja_env.globals["request_token"] = lambda: secrets.token_urlsafe(24)
    from careblue.storage import image_url
    app.jinja_env.globals["image_url"] = image_url
    from careblue.presentation import return_context,back_url,context_link
    app.jinja_env.globals.update(back_url=back_url,context_link=context_link)
    app.context_processor(lambda: {'return_context':return_context()})
    from careblue.services import date_display, time_display, datetime_display, from_minor, age_display
    app.jinja_env.filters.update(date_display=date_display, time_display=time_display, datetime_display=datetime_display, from_minor=from_minor, age_display=age_display)
    register_commands(app)

    @app.get("/health")
    @security_access(public=True)
    def health():
        return jsonify(status="ok")
    @app.get("/ready")
    @security_access(public=True)
    def ready():
        from careblue.migrations import LATEST_VERSION
        conn = None
        try:
            conn = get_db_connection()
            version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
            if version != LATEST_VERSION:
                return jsonify(status="migration_required"), 503
            return jsonify(status="ready")
        except Exception:
            app.logger.exception("Readiness check failed")
            return jsonify(status="unavailable"), 503
        finally:
            if conn:
                conn.close()

    @app.errorhandler(Exception)
    def error(error):
        g.rendering_error = True
        if isinstance(error, HTTPException):
            return render_template("errors/http.html", status=error.code, message=error.description), error.code
        app.logger.exception("Unhandled request error request_id=%s", getattr(g, "request_id", ""))
        return render_template("errors/500.html", request_id=getattr(g, "request_id", "")), 500
    return app
