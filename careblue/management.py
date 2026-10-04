"""Explicit operational commands; web workers never mutate schemas."""
import hashlib
import secrets
import time
import click
from flask import current_app
from werkzeug.security import generate_password_hash
from database import get_db_connection, backup_database, IntegrityError
from careblue.accounts import password_error, email_valid

def register_commands(app):
    @app.cli.command('create-platform-admin')
    @click.option('--email',prompt=True)
    @click.option('--name',prompt=True)
    @click.password_option()
    def create_platform_admin(email,name,password):
        """Bootstrap an operator account independently of all hospital accounts."""
        if not email_valid(email) or not name.strip() or len(name)>120 or password_error(password):
            raise click.ClickException(password_error(password) or 'Enter a valid name and email.')
        conn=get_db_connection()
        try:
            actor=conn.insert('INSERT INTO platform_admins(name,email,password) VALUES(?,?,?)',(name.strip(),email.strip().lower(),generate_password_hash(password))).lastrowid
            from careblue.security import log_audit
            log_audit('create_operator','platform_admin',actor,conn=conn,actor_id=actor,actor_type='platform')
            conn.commit()
        except IntegrityError:
            conn.rollback()
            raise click.ClickException('A platform administrator with this email already exists.')
        finally:
            conn.close()
        click.echo('Platform administrator created. Sign in at /platform/login.')

    @app.cli.command("db-upgrade")
    def db_upgrade():
        """Apply versioned migrations. Back up existing data first."""
        from careblue.migrations import migrate
        migrate()
        click.echo("Database schema is current.")

    @app.cli.command("import-sqlite")
    @click.argument("source", type=click.Path(exists=True, dir_okay=False))
    def import_database(source):
        """Import a SQLite snapshot into an empty configured PostgreSQL database."""
        from careblue.importer import import_sqlite
        try:
            counts = import_sqlite(source)
        except ValueError as error:
            raise click.ClickException(str(error))
        click.echo("Import verified: " + ", ".join(f"{table}={count}" for table, count in counts.items()))

    @app.cli.command("create-admin")
    @click.option("--email", prompt=True)
    @click.option("--name", prompt=True)
    @click.option("--hospital", help="Name of a new hospital.")
    @click.option("--hospital-id", type=int, help="Join an existing hospital.")
    @click.password_option()
    def create_admin(email, name, hospital, hospital_id, password):
        if not email_valid(email) or password_error(password):
            raise click.ClickException(password_error(password) or "Enter a valid email address.")
        conn = get_db_connection()
        try:
            from careblue.accounts import create_administrator
            if hospital_id is not None and hospital:
                raise click.ClickException("Choose either --hospital or --hospital-id.")
            if hospital_id is None and not hospital:
                hospital = click.prompt("New hospital name")
            if not name.strip() or len(name) > 120:
                raise click.ClickException("Enter a name of at most 120 characters.")
            create_administrator(conn, name.strip(), email.strip(), generate_password_hash(password), hospital, hospital_id)
            conn.commit()
        except ValueError as error:
            conn.rollback()
            raise click.ClickException(str(error))
        finally:
            conn.close()
        click.echo("Administrator created.")

    @app.cli.command("account-password")
    @click.option("--role", type=click.Choice(["admin", "doctor", "platform"]), required=True)
    @click.option("--id", "actor_id", type=int, required=True)
    @click.password_option()
    def account_password(role, actor_id, password):
        """Replace password, enable account, and revoke all existing sessions."""
        if password_error(password):
            raise click.ClickException(password_error(password))
        table = {"admin":"staff","doctor":"doctors","platform":"platform_admins"}[role]
        conn = get_db_connection()
        try:
            cur = conn.execute(f"UPDATE {table} SET password = ?, active = 1, session_version = session_version + 1 WHERE id = ?",
                               (generate_password_hash(password), actor_id))
            conn.commit()
            if not cur.rowcount:
                raise click.ClickException("Account not found.")
        finally:
            conn.close()
        click.echo("Password changed; existing sessions revoked.")

    @app.cli.command("disable-account")
    @click.option("--role", type=click.Choice(["admin", "doctor", "platform"]), required=True)
    @click.option("--id", "actor_id", type=int, required=True)
    def disable_account(role, actor_id):
        table = {"admin":"staff","doctor":"doctors","platform":"platform_admins"}[role]
        conn = get_db_connection()
        try:
            conn.execute(f"UPDATE {table} SET active = 0, session_version = session_version + 1 WHERE id = ?", (actor_id,))
            conn.commit()
        finally:
            conn.close()
        click.echo("Account disabled and sessions revoked.")

    @app.cli.command("issue-reset")
    @click.option("--role", type=click.Choice(["admin", "doctor", "platform"]), required=True)
    @click.option("--id", "actor_id", type=int, required=True)
    def issue_reset(role, actor_id):
        """Issue a one-use token after verifying the account owner's identity."""
        token = secrets.token_urlsafe(32)
        table = {"admin":"staff","doctor":"doctors","platform":"platform_admins"}[role]
        conn = get_db_connection()
        try:
            if not conn.execute(f"SELECT id FROM {table} WHERE id = ?", (actor_id,)).fetchone():
                raise click.ClickException("Account not found.")
            conn.execute("DELETE FROM reset_tokens WHERE actor_type = ? AND actor_id = ?", (role, actor_id))
            conn.execute("INSERT INTO reset_tokens (token_hash, actor_type, actor_id, expires_at) VALUES (?, ?, ?, ?)",
                         (hashlib.sha256(token.encode()).hexdigest(), role, actor_id, int(time.time()) + 1800))
            conn.commit()
        finally:
            conn.close()
        click.echo("Deliver this one-use token securely (expires in 30 minutes): " + token)

    @app.cli.command("enroll-mfa")
    @click.option("--role", type=click.Choice(["admin", "doctor", "platform"]), required=True)
    @click.option("--id", "actor_id", type=int, required=True)
    def enroll_mfa(role, actor_id):
        """Enroll a TOTP authenticator after verifying its first code."""
        import pyotp
        from careblue.accounts import encrypt_secret
        secret = pyotp.random_base32()
        table = {"admin":"staff","doctor":"doctors","platform":"platform_admins"}[role]
        totp = pyotp.TOTP(secret)
        click.echo(totp.provisioning_uri(name=f"{role}-{actor_id}", issuer_name="CareBlue"))
        code = click.prompt("Authenticator code", hide_input=True)
        if not totp.verify(code):
            raise click.ClickException("Invalid code; enrollment cancelled.")
        conn = get_db_connection()
        try:
            cur = conn.execute(f"UPDATE {table} SET totp_secret = ?, totp_last_counter = -1, session_version = session_version + 1 WHERE id = ?",
                               (encrypt_secret(secret), actor_id))
            conn.commit()
            if not cur.rowcount:
                raise click.ClickException("Account not found.")
        finally:
            conn.close()
        click.echo("MFA enabled; previous sessions revoked.")

    @app.cli.command("backup")
    def backup():
        click.echo(backup_database())

    @app.cli.command("rotate-mfa-keys")
    @click.option("--legacy-session-key", help="Old SECRET_KEY for pre-migration MFA, supplied via CAREBLUE_LEGACY_SESSION_KEY.", envvar="CAREBLUE_LEGACY_SESSION_KEY")
    def rotate_mfa_keys(legacy_session_key):
        """Re-encrypt all MFA secrets with MFA_ENCRYPTION_KEY in one transaction."""
        from cryptography.fernet import Fernet, MultiFernet
        from careblue.accounts import cipher, encrypt_secret
        import base64
        if not current_app.config.get("MFA_ENCRYPTION_KEY"):
            raise click.ClickException("Set MFA_ENCRYPTION_KEY first.")
        decoder = cipher()
        if legacy_session_key:
            legacy = Fernet(base64.urlsafe_b64encode(hashlib.sha256(legacy_session_key.encode()).digest()))
            decoder = MultiFernet([decoder, legacy])
        conn = get_db_connection()
        try:
            for table in ("staff", "doctors", "platform_admins"):
                for row in conn.execute(f"SELECT id,totp_secret FROM {table} WHERE totp_secret IS NOT NULL").fetchall():
                    secret = decoder.decrypt(row["totp_secret"].encode()).decode()
                    conn.execute(f"UPDATE {table} SET totp_secret=? WHERE id=?", (encrypt_secret(secret), row["id"]))
            conn.commit()
        except Exception as error:
            conn.rollback()
            raise click.ClickException("MFA rotation failed; no secrets were changed.") from error
        finally:
            conn.close()
        click.echo("MFA secrets re-encrypted. Retire previous keys after verification.")

    @app.cli.command("seed-demo")
    @click.option("--confirm-demo", is_flag=True, required=True)
    @click.option("--hospital-id", type=int, required=True)
    def seed_demo(confirm_demo, hospital_id):
        """Seed fictional data only in a local development database."""
        if current_app.config["PRODUCTION"] or current_app.config["DATABASE_URL"]:
            raise click.ClickException("Demo seeding is available only for local SQLite development.")
        from seed_demo import ensure_demo_data
        if not ensure_demo_data(hospital_id, enabled=True):
            raise click.ClickException("The hospital must exist and have no doctors or patients.")
        click.echo("Demo data seeded. Use account-password to choose account credentials.")
