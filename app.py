from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file, abort, g
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
from datetime import datetime, timedelta
import os
import secrets
import time
from io import BytesIO
import logging
from database import get_db_path, init_db, check_database_exists, get_db_connection
from migrate_db import migrate_database

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s in %(module)s: %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
_secret_key = os.environ.get('SECRET_KEY')
_ON_VERCEL_EARLY = 'VERCEL' in os.environ
if not _secret_key:
    if _ON_VERCEL_EARLY:
        # Never crash the serverless function at import time: Vercel dashboard
        # SECRET_KEY may be unset. Warn; sessions reset on cold starts until set.
        logger.warning("SECRET_KEY not set on Vercel — using an ephemeral key. "
                       "Set SECRET_KEY in Vercel env vars or sessions will reset on cold starts.")
    elif os.environ.get('FLASK_ENV') == 'production' or os.environ.get('RENDER'):
        raise RuntimeError("SECRET_KEY environment variable must be set in production")
    else:
        logger.warning("SECRET_KEY not set — using an ephemeral development key. Set SECRET_KEY env var.")
    _secret_key = secrets.token_hex(32)
app.secret_key = _secret_key
# Vercel serverless: only /tmp is writable; uploaded photos are ephemeral.
_ON_VERCEL = 'VERCEL' in os.environ
app.config['UPLOAD_FOLDER'] = os.path.join('/tmp', 'careblue-uploads') if _ON_VERCEL else 'static/images/doctors'
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg'}
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # 5 MB upload cap
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('FLASK_ENV') == 'production' or 'RENDER' in os.environ or 'VERCEL' in os.environ
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=int(os.environ.get('SESSION_HOURS', '2')))

# --- CSRF protection (all state-changing requests must carry the session token) ---
def generate_csrf_token():
    if '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_hex(32)
    return session['_csrf_token']

app.jinja_env.globals['csrf_token'] = generate_csrf_token

@app.before_request
def assign_request_id():
    g.request_id = secrets.token_hex(8)

@app.before_request
def csrf_protect():
    if request.method == 'POST':
        token = session.get('_csrf_token', '')
        submitted = request.form.get('csrf_token', '')
        if not token or not submitted or not secrets.compare_digest(token, submitted):
            abort(400, description='Invalid or missing CSRF token')

# --- Login rate limiting (per process; brute-force mitigation) ---
_login_attempts = {}  # (ip, username) -> [fail_count, first_fail_ts]
MAX_LOGIN_FAILS = 5
LOGIN_WINDOW = 600      # seconds: window in which fails are counted
LOGIN_LOCKOUT = 300     # seconds: lockout duration once limit is hit

def _login_key():
    return (request.remote_addr or 'unknown',
            (request.form.get('username', '') if request.method == 'POST' else '').strip().lower())

def is_login_locked_out():
    fails, first = _login_attempts.get(_login_key(), (0, 0))
    if fails < MAX_LOGIN_FAILS:
        return False
    if time.time() - first < LOGIN_WINDOW + LOGIN_LOCKOUT:
        return True
    _login_attempts.pop(_login_key(), None)
    return False

def record_failed_login():
    key = _login_key()
    fails, first = _login_attempts.get(key, (0, time.time()))
    if time.time() - first > LOGIN_WINDOW:
        fails, first = 0, time.time()
    _login_attempts[key] = (fails + 1, first)

def clear_failed_logins():
    _login_attempts.pop(_login_key(), None)

def log_audit(action, entity, entity_id=None, detail=None, hospital_id=None):
    """Best-effort audit trail with its own connection, so it never
    interferes with (or rolls back with) the caller's transaction."""
    try:
        conn = get_db_connection()
        try:
            conn.execute('''
                INSERT INTO audit_log (actor_id, actor_type, hospital_id, action, entity, entity_id, detail)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (session.get('user_id'), session.get('user_type'),
                  hospital_id if hospital_id is not None else session.get('hospital_id'),
                  action, entity, entity_id, detail))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        app.logger.error(f"Audit log failed ({getattr(g, 'request_id', 'n/a')}): {e}")

# Initialize database if it doesn't exist
db_path = get_db_path()
try:
    if not os.path.exists(db_path):
        logger.info(f"Database not found at {db_path}. Initializing...")
        init_db()
        logger.info("Database initialized successfully")
    else:
        logger.info(f"Database found at {db_path}")
        # Run migration to update schema if needed
        migrate_database()
        
    # Verify database connection
    conn = get_db_connection()
    conn.execute("SELECT 1")  # Simple query to test connection
    conn.close()
    logger.info("Database connection verified")

    # Daily backup safety net (best-effort; never blocks startup)
    try:
        from database import backup_database_if_stale
        snap = backup_database_if_stale()
        if snap:
            logger.info(f"Startup backup written to {snap}")
    except Exception as e:
        logger.error(f"Startup backup failed: {e}")

    # Demo dataset for the bundled demo account (no-op unless empty)
    try:
        from seed_demo import ensure_demo_data
        ensure_demo_data()
    except Exception as e:
        logger.error(f"Demo seed check failed: {e}")
except Exception as e:
    logger.error(f"Database initialization error: {e}")
    # Create a basic error page if database is not accessible
    @app.route('/')
    def error_page():
        return render_template('errors/database_error.html'), 500
    if 'VERCEL' not in os.environ:
        raise  # Re-raise locally/Render for proper error handling; on Vercel
    # never kill the function at import time (would surface as
    # 500 FUNCTION_INVOCATION_FAILED with no useful page).

# Context processors
@app.context_processor
def utility_processor():
    def now():
        return datetime.now()
    return dict(now=now,
                session_lifetime=int(app.config['PERMANENT_SESSION_LIFETIME'].total_seconds()))

@app.context_processor
def header_stats():
    """Today's scheduled-visit count for the notification bell (role-aware)."""
    try:
        if 'user_id' not in session:
            return {}
        today = datetime.now().strftime('%Y-%m-%d')
        conn = get_db_connection()
        try:
            if session.get('user_type') == 'doctor':
                n = conn.execute(
                    "SELECT COUNT(*) FROM appointments WHERE doctor_id = ? AND date = ? AND status = 'Scheduled' AND hospital_id = ?",
                    (session['user_id'], today, session['hospital_id'])).fetchone()[0]
            else:
                n = conn.execute(
                    "SELECT COUNT(*) FROM appointments WHERE date = ? AND status = 'Scheduled' AND hospital_id = ?",
                    (today, session['hospital_id'])).fetchone()[0]
        finally:
            conn.close()
        return {'today_count': n}
    except Exception:
        return {}

# Helper functions
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

VALID_GENDERS = {'Male', 'Female', 'Other'}
VALID_STATUSES = {'Scheduled', 'Completed', 'Cancelled'}
BED_STATUSES = {'Available', 'Occupied', 'Maintenance'}
PAYMENT_METHODS = {'Cash', 'Card', 'UPI', 'Insurance', 'Other'}

def parse_money(value):
    """Parse a non-negative money amount. Returns rounded float or None."""
    try:
        amount = round(float(value), 2)
    except (TypeError, ValueError):
        return None
    if amount < 0 or amount > 10000000:
        return None
    return amount

def bill_totals(conn, bill_id):
    """Compute total / paid / balance / status for a bill. Caller scopes tenure."""
    total = conn.execute('SELECT COALESCE(SUM(amount), 0) FROM bill_items WHERE bill_id = ?',
                         (bill_id,)).fetchone()[0] or 0
    paid = conn.execute('SELECT COALESCE(SUM(amount), 0) FROM payments WHERE bill_id = ?',
                        (bill_id,)).fetchone()[0] or 0
    total, paid = round(total, 2), round(paid, 2)
    balance = round(total - paid, 2)
    if total <= 0:
        status = 'Unpaid'
    elif balance <= 0:
        status = 'Paid'
    elif paid > 0:
        status = 'Partial'
    else:
        status = 'Unpaid'
    return {'total': total, 'paid': paid, 'balance': balance, 'status': status}

def validate_patient_input(name, age, gender, contact):
    """Server-side mirror of the patient form rules. Returns error string or None."""
    if not name or not contact:
        return 'Name and contact are required'
    if len(name) > 120 or len(contact) > 30:
        return 'Name or contact is too long'
    try:
        age_n = int(age)
    except (TypeError, ValueError):
        return 'Age must be a whole number'
    if not 0 <= age_n <= 130:
        return 'Age must be between 0 and 130'
    if gender not in VALID_GENDERS:
        return 'Please select a valid gender'
    return None

def validate_doctor_input(name, specialization, experience, consultation_fee, contact):
    """Server-side mirror of the doctor form rules. Returns error string or None."""
    if not all([name, specialization, contact]):
        return 'Name, specialization and contact are required'
    if len(name) > 120 or len(specialization) > 120 or len(contact) > 30:
        return 'One of the text fields is too long'
    try:
        exp_n = int(experience)
    except (TypeError, ValueError):
        return 'Experience must be a whole number of years'
    if not 0 <= exp_n <= 60:
        return 'Experience must be between 0 and 60 years'
    try:
        fee_n = float(consultation_fee)
    except (TypeError, ValueError):
        return 'Consultation fee must be a number'
    if fee_n < 0 or fee_n > 1000000:
        return 'Consultation fee is out of range'
    return None

def validate_slot_input(start_time, end_time, break_start, break_end):
    """Validate HH:MM slot configuration. Returns error string or None."""
    fmt = '%H:%M'
    try:
        start = datetime.strptime(start_time, fmt)
        end = datetime.strptime(end_time, fmt)
    except (TypeError, ValueError):
        return 'Start and end times are required (HH:MM)'
    if start >= end:
        return 'End time must be after start time'
    if break_start or break_end:
        try:
            bs = datetime.strptime(break_start, fmt)
            be = datetime.strptime(break_end, fmt)
        except (TypeError, ValueError):
            return 'Break times must both be set or both empty'
        if not (start <= bs < be <= end):
            return 'Break must fall inside working hours'
    return None

def save_doctor_photo(file):
    """Validate, normalize and store an uploaded doctor photo.

    Verifies image integrity with Pillow, resizes to a bounded JPEG with a
    random filename (prevents collisions and path games). Returns the
    static-relative path, or None when no usable file was provided.
    """
    from PIL import Image, UnidentifiedImageError
    if not file or not getattr(file, 'filename', None):
        return None
    if not allowed_file(file.filename):
        return None
    try:
        probe = Image.open(file.stream)
        probe.verify()
        file.stream.seek(0)
        img = Image.open(file.stream).convert('RGB')
    except (UnidentifiedImageError, OSError, ValueError):
        return None
    img.thumbnail((512, 512))
    filename = secrets.token_hex(12) + '.jpg'
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    img.save(os.path.join(app.config['UPLOAD_FOLDER'], filename), 'JPEG', quality=85)
    return 'images/doctors/' + filename

# NOTE: get_db_connection is imported from database.py (retry + timeout logic).
# Do not redefine it here.

def generate_time_slots(start_time, end_time, break_start=None, break_end=None, interval=15):
    slots = []
    current_time = datetime.strptime(start_time, '%H:%M')
    end_time = datetime.strptime(end_time, '%H:%M')
    
    while current_time < end_time:
        slot_end = current_time + timedelta(minutes=interval)
        
        if break_start and break_end:
            break_start_time = datetime.strptime(break_start, '%H:%M')
            break_end_time = datetime.strptime(break_end, '%H:%M')
            if current_time >= break_start_time and slot_end <= break_end_time:
                current_time = break_end_time
                continue
        
        if slot_end <= end_time:
            # Format time in hh:mm am/pm format
            start_str = current_time.strftime('%I:%M %p').lower()
            end_str = slot_end.strftime('%I:%M %p').lower()
            slots.append({
                'start': current_time.strftime('%H:%M'),  # Keep 24h format for storage
                'end': slot_end.strftime('%H:%M'),
                'display_start': start_str,
                'display_end': end_str
            })
        current_time = slot_end
    
    return slots

# Authentication routes
@app.route('/')
def home():
    if 'user_id' in session:
        if session['user_type'] == 'admin':
            return redirect(url_for('admin_dashboard'))
        else:
            return redirect(url_for('doctor_dashboard'))
    return render_template('auth/login.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if is_login_locked_out():
            flash('Too many failed attempts. Try again in a few minutes.', 'danger')
            return render_template('auth/login.html'), 429
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user_type = request.form.get('user_type', '').strip()
        
        if not all([username, password, user_type]):
            flash('All fields are required', 'danger')
            return redirect(url_for('login'))
        
        conn = get_db_connection()
        user = None
        
        try:
            if user_type == 'admin':
                user = conn.execute('SELECT * FROM staff WHERE email = ?', (username,)).fetchone()
                if user and check_password_hash(user['password'], password):
                    session.clear()
                    session.permanent = True
                    session['user_id'] = user['id']
                    session['user_type'] = user_type
                    session['name'] = user['name']
                    session['hospital_id'] = user['id']
                    session['hospital_name'] = user['hospital_name']
                    clear_failed_logins()
                    log_audit('login', 'staff', user['id'])
                    return redirect(url_for('admin_dashboard'))
            else:
                # For doctor login, get hospital info
                user = conn.execute('''
                    SELECT d.*, s.hospital_name 
                    FROM doctors d
                    JOIN staff s ON d.hospital_id = s.id
                    WHERE d.username = ? AND d.password IS NOT NULL
                ''', (username,)).fetchone()
                if user and check_password_hash(user['password'], password):
                    session.clear()
                    session.permanent = True
                    session['user_id'] = user['id']
                    session['user_type'] = user_type
                    session['name'] = user['name']
                    session['hospital_id'] = user['hospital_id']
                    session['hospital_name'] = user['hospital_name']
                    clear_failed_logins()
                    log_audit('login', 'doctor', user['id'])
                    return redirect(url_for('doctor_dashboard'))
            
            record_failed_login()
            flash('Invalid credentials or account not setup', 'danger')
        except Exception as e:
            flash('Login error occurred', 'danger')
            app.logger.error(f"Login error: {str(e)}")
        finally:
            conn.close()
    
    return render_template('auth/login.html')

@app.route('/doctor/login', methods=['GET', 'POST'])
def doctor_login():
    if request.method == 'POST':
        if is_login_locked_out():
            flash('Too many failed attempts. Try again in a few minutes.', 'danger')
            return render_template('auth/doctor_login.html'), 429
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        
        if not all([username, password]):
            flash('All fields are required', 'danger')
            return redirect(url_for('doctor_login'))
        
        conn = get_db_connection()
        try:
            # Get doctor with hospital information
            doctor = conn.execute('''
                SELECT d.*, s.hospital_name 
                FROM doctors d
                JOIN staff s ON d.hospital_id = s.id
                WHERE d.username = ? AND d.password IS NOT NULL
            ''', (username,)).fetchone()
            
            if doctor and check_password_hash(doctor['password'], password):
                session.clear()
                session.permanent = True
                session['user_id'] = doctor['id']
                session['user_type'] = 'doctor'
                session['name'] = doctor['name']
                session['hospital_id'] = doctor['hospital_id']
                session['hospital_name'] = doctor['hospital_name']
                clear_failed_logins()
                return redirect(url_for('doctor_dashboard'))
            
            record_failed_login()
            flash('Invalid credentials or account not setup', 'danger')
        except Exception as e:
            flash('Login error occurred', 'danger')
            app.logger.error(f"Doctor login error: {str(e)}")
        finally:
            conn.close()
    
    return render_template('auth/doctor_login.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        hospital_name = request.form.get('hospital_name', '').strip()

        if not all([name, email, password, hospital_name]):
            flash('All fields are required', 'danger')
            return redirect(url_for('register'))

        if password != confirm_password:
            flash('Passwords do not match', 'danger')
            return redirect(url_for('register'))

        if len(password) < 6:
            flash('Password must be at least 6 characters', 'danger')
            return redirect(url_for('register'))
        
        conn = get_db_connection()
        try:
            cur = conn.execute('INSERT INTO staff (name, email, password, hospital_name) VALUES (?, ?, ?, ?)',
                               (name, email, generate_password_hash(password), hospital_name))
            conn.commit()
            # staff.id doubles as hospital_id, so the new hospital owns this row
            log_audit('register', 'staff', cur.lastrowid, hospital_name, hospital_id=cur.lastrowid)
            flash('Registration successful! Please login.', 'success')
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash('Email already exists!', 'danger')
        finally:
            conn.close()
    
    return render_template('auth/register.html')

@app.route('/session/refresh')
def refresh_session():
    """Sliding-expiry keep-alive for the session-timeout warning (no state change)."""
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401
    session.modified = True
    return jsonify({'ok': True})

@app.route('/admin/backup', methods=['POST'])
def admin_backup():
    if 'user_id' not in session or session['user_type'] != 'admin':
        return redirect(url_for('login'))
    try:
        from database import backup_database
        dest = backup_database()
        if dest:
            log_audit('backup', 'database', None, os.path.basename(dest))
            flash(f"Backup saved ({os.path.basename(dest)})", 'success')
        else:
            flash('Nothing to back up yet', 'warning')
    except Exception as e:
        flash('Backup failed', 'danger')
        app.logger.error(f"Manual backup error: {str(e)}")
    return redirect(url_for('audit_log_view'))

@app.route('/logout')
def logout():
    log_audit('logout', session.get('user_type') or 'unknown', session.get('user_id'))
    session.clear()
    return redirect(url_for('home'))

# Admin routes
@app.route('/admin/dashboard')
def admin_dashboard():
    if 'user_id' not in session or session['user_type'] != 'admin':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    doctors_count = conn.execute('SELECT COUNT(*) FROM doctors WHERE hospital_id = ?', 
                               (session['hospital_id'],)).fetchone()[0]
    patients_count = conn.execute('SELECT COUNT(*) FROM patients WHERE hospital_id = ?', 
                                (session['hospital_id'],)).fetchone()[0]
    appointments_count = conn.execute('SELECT COUNT(*) FROM appointments WHERE hospital_id = ?', 
                                    (session['hospital_id'],)).fetchone()[0]
    
    recent_appointments = conn.execute('''
        SELECT a.id, p.name as patient_name, d.name as doctor_name, a.date, a.time_slot, a.status
        FROM appointments a
        JOIN patients p ON a.patient_id = p.id
        JOIN doctors d ON a.doctor_id = d.id
        WHERE a.hospital_id = ?
        ORDER BY a.date DESC, a.time_slot DESC
        LIMIT 5
    ''', (session['hospital_id'],)).fetchall()

    today = datetime.now().strftime('%Y-%m-%d')
    todays_appointments = conn.execute('''
        SELECT a.id, p.name as patient_name, d.name as doctor_name, a.time_slot, a.status
        FROM appointments a
        JOIN patients p ON a.patient_id = p.id
        JOIN doctors d ON a.doctor_id = d.id
        WHERE a.hospital_id = ? AND a.date = ?
        ORDER BY a.time_slot
        LIMIT 8
    ''', (session['hospital_id'], today)).fetchall()

    week_days = []
    for i in range(6, -1, -1):
        day = datetime.now() - timedelta(days=i)
        week_days.append({'date': day.strftime('%Y-%m-%d'), 'dow': day.strftime('%a')[0], 'label': day.strftime('%d')})
    week_rows = conn.execute('''
        SELECT date, COUNT(*) as c FROM appointments
        WHERE hospital_id = ? AND date >= date('now', '-6 days')
        GROUP BY date
    ''', (session['hospital_id'],)).fetchall()
    week_counts = {r['date']: r['c'] for r in week_rows}

    status_rows = conn.execute('''
        SELECT status, COUNT(*) as c FROM appointments
        WHERE hospital_id = ? GROUP BY status
    ''', (session['hospital_id'],)).fetchall()
    status_counts = {r['status']: r['c'] for r in status_rows}

    activity = conn.execute('''
        SELECT a.action, a.entity, a.detail, a.created_at,
               COALESCE(s.name, d.name) AS actor_name
        FROM audit_log a
        LEFT JOIN staff s ON a.actor_id = s.id AND a.actor_type != 'doctor'
        LEFT JOIN doctors d ON a.actor_id = d.id AND a.actor_type = 'doctor'
        WHERE (a.hospital_id = ? OR a.hospital_id IS NULL)
        ORDER BY a.id DESC LIMIT 7
    ''', (session['hospital_id'],)).fetchall()

    conn.close()

    return render_template('admin/dashboard.html',
                         doctors_count=doctors_count,
                         patients_count=patients_count,
                         appointments_count=appointments_count,
                         recent_appointments=recent_appointments,
                         todays_appointments=todays_appointments,
                         week_days=week_days,
                         week_counts=week_counts,
                         status_counts=status_counts,
                         activity=activity,
                         today=today)

@app.route('/admin/add_patient', methods=['GET', 'POST'])
def add_patient():
    if 'user_id' not in session or session['user_type'] != 'admin':
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        age = request.form.get('age', '').strip()
        gender = request.form.get('gender', '').strip()
        contact = request.form.get('contact', '').strip()
        address = request.form.get('address', '').strip()
        medical_history = request.form.get('medical_history', '').strip()
        
        if not all([name, age, gender, contact]):
            flash('Required fields are missing', 'danger')
            return redirect(url_for('add_patient'))

        error = validate_patient_input(name, age, gender, contact)
        if error:
            flash(error, 'danger')
            return redirect(url_for('add_patient'))

        conn = get_db_connection()
        try:
            cur = conn.execute('''
                INSERT INTO patients (name, age, gender, contact, address, medical_history, created_by, hospital_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (name, int(age), gender, contact, address, medical_history, session['user_id'], session['hospital_id']))
            conn.commit()
            log_audit('create', 'patient', cur.lastrowid, name)
            flash('Patient added successfully!', 'success')
            return redirect(url_for('view_patients'))
        except Exception as e:
            flash('Error adding patient', 'danger')
            app.logger.error(f"Add patient error: {str(e)}")
        finally:
            conn.close()
    
    return render_template('admin/add_patient.html')

@app.route('/admin/add_doctor', methods=['GET', 'POST'])
def add_doctor():
    if 'user_id' not in session or session['user_type'] != 'admin':
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        specialization = request.form.get('specialization', '').strip()
        experience = request.form.get('experience', '').strip()
        consultation_fee = request.form.get('consultation_fee', '').strip()
        contact = request.form.get('contact', '').strip()
        bio = request.form.get('bio', '').strip()
        
        if not all([name, specialization, experience, consultation_fee, contact]):
            flash('Required fields are missing', 'danger')
            return redirect(url_for('add_doctor'))

        error = validate_doctor_input(name, specialization, experience, consultation_fee, contact)
        if error:
            flash(error, 'danger')
            return redirect(url_for('add_doctor'))

        image_path = save_doctor_photo(request.files.get('image'))
        if 'image' in request.files and request.files['image'].filename and image_path is None:
            flash('Uploaded file is not a valid JPG/PNG image', 'danger')
            return redirect(url_for('add_doctor'))

        conn = get_db_connection()
        try:
            cur = conn.execute('''
                INSERT INTO doctors (name, specialization, experience, consultation_fee, contact, bio, image_path, created_by, hospital_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (name, specialization, int(experience), float(consultation_fee), contact, bio, image_path, session['user_id'], session['hospital_id']))
            conn.commit()
            log_audit('create', 'doctor', cur.lastrowid, f"{name} ({specialization})")
            flash('Doctor added successfully!', 'success')
            return redirect(url_for('view_doctors'))
        except Exception as e:
            flash('Error adding doctor', 'danger')
            app.logger.error(f"Add doctor error: {str(e)}")
        finally:
            conn.close()
    
    return render_template('admin/add_doctor.html')

@app.route('/admin/view_patients')
def view_patients():
    if 'user_id' not in session or session['user_type'] != 'admin':
        return redirect(url_for('login'))
    
    search_query = request.args.get('search', '').strip()
    
    conn = get_db_connection()
    patients_total = conn.execute('SELECT COUNT(*) FROM patients WHERE hospital_id = ?',
                                  (session['hospital_id'],)).fetchone()[0]
    try:
        if search_query:
            patients = conn.execute('''
                SELECT * FROM patients 
                WHERE (name LIKE ? OR contact LIKE ?) AND hospital_id = ?
                ORDER BY name
            ''', (f'%{search_query}%', f'%{search_query}%', session['hospital_id'])).fetchall()
        else:
            patients = conn.execute('SELECT * FROM patients WHERE hospital_id = ? ORDER BY name', 
                                   (session['hospital_id'],)).fetchall()
    except Exception as e:
        flash('Error loading patients', 'danger')
        app.logger.error(f"View patients error: {str(e)}")
        patients = []
    finally:
        conn.close()
    
    return render_template('admin/view_patients.html',
                         patients=patients,
                         patients_total=patients_total,
                         search_query=search_query)

@app.route('/admin/view_doctors')
def view_doctors():
    if 'user_id' not in session or session['user_type'] != 'admin':
        return redirect(url_for('login'))
    
    search_query = request.args.get('search', '').strip()
    
    conn = get_db_connection()
    doctors_total = conn.execute('SELECT COUNT(*) FROM doctors WHERE hospital_id = ?',
                                 (session['hospital_id'],)).fetchone()[0]
    try:
        if search_query:
            doctors = conn.execute('''
                SELECT * FROM doctors 
                WHERE (name LIKE ? OR specialization LIKE ?) AND hospital_id = ?
                ORDER BY name
            ''', (f'%{search_query}%', f'%{search_query}%', session['hospital_id'])).fetchall()
        else:
            doctors = conn.execute('SELECT * FROM doctors WHERE hospital_id = ? ORDER BY name', 
                                  (session['hospital_id'],)).fetchall()
    except Exception as e:
        flash('Error loading doctors', 'danger')
        app.logger.error(f"View doctors error: {str(e)}")
        doctors = []
    finally:
        conn.close()
    
    return render_template('admin/view_doctors.html',
                         doctors=doctors,
                         doctors_total=doctors_total,
                         search_query=search_query)

@app.route('/admin/set_doctor_credentials/<int:doctor_id>', methods=['GET', 'POST'])
def set_doctor_credentials(doctor_id):
    if 'user_id' not in session or session['user_type'] != 'admin':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    doctor = conn.execute('SELECT * FROM doctors WHERE id = ? AND hospital_id = ?', 
                         (doctor_id, session['hospital_id'])).fetchone()
    
    if not doctor:
        flash('Doctor not found', 'danger')
        return redirect(url_for('view_doctors'))
    
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        
        if len(username) < 4:
            flash('Username must be at least 4 characters', 'danger')
            return redirect(url_for('set_doctor_credentials', doctor_id=doctor_id))
        
        if len(password) < 8:
            flash('Password must be at least 8 characters', 'danger')
            return redirect(url_for('set_doctor_credentials', doctor_id=doctor_id))
        
        try:
            existing = conn.execute(
                'SELECT id FROM doctors WHERE username = ? AND id != ? AND hospital_id = ?',
                (username, doctor_id, session['hospital_id'])
            ).fetchone()
            
            if existing:
                flash('Username already in use', 'danger')
                return redirect(url_for('set_doctor_credentials', doctor_id=doctor_id))
            
            conn.execute(
                'UPDATE doctors SET username = ?, password = ? WHERE id = ? AND hospital_id = ?',
                (username, generate_password_hash(password), doctor_id, session['hospital_id'])
            )
            conn.commit()
            log_audit('update_credentials', 'doctor', doctor_id, username)
            flash('Doctor credentials updated successfully!', 'success')
            return redirect(url_for('view_doctors'))
        except sqlite3.Error as e:
            flash('Failed to update credentials', 'danger')
            app.logger.error(f"Credential update error: {str(e)}")
        finally:
            conn.close()
    
    return render_template('admin/set_doctor_credentials.html', doctor=doctor)

@app.route('/admin/set_doctor_slots/<int:doctor_id>', methods=['GET', 'POST'])
def set_doctor_slots(doctor_id):
    if 'user_id' not in session or session['user_type'] != 'admin':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    
    # Verify doctor belongs to this hospital
    doctor = conn.execute('SELECT * FROM doctors WHERE id = ? AND hospital_id = ?', 
                         (doctor_id, session['hospital_id'])).fetchone()
    if not doctor:
        flash('Doctor not found', 'danger')
        return redirect(url_for('view_doctors'))
    
    if request.method == 'POST':
        day = request.form.get('day', '').strip()
        start_time = request.form.get('start_time', '').strip()
        end_time = request.form.get('end_time', '').strip()
        break_start = request.form.get('break_start', '').strip() or None
        break_end = request.form.get('break_end', '').strip() or None
        
        if not day:
            flash('Day is required', 'danger')
            return redirect(url_for('set_doctor_slots', doctor_id=doctor_id))

        if request.form.get('day_off'):
            # Day-off submissions carry no times (their inputs are disabled)
            conn.execute('DELETE FROM doctor_slots WHERE doctor_id = ? AND day_of_week = ? AND hospital_id = ?',
                         (doctor_id, day, session['hospital_id']))
            conn.commit()
            log_audit('day_off', 'doctor', doctor_id, day)
            flash(f'{day} marked as day off', 'success')
            return redirect(url_for('set_doctor_slots', doctor_id=doctor_id))

        if not all([start_time, end_time]):
            flash('Opening and closing times are required', 'danger')
            return redirect(url_for('set_doctor_slots', doctor_id=doctor_id))

        if day not in ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'):
            flash('Invalid day of week', 'danger')
            return redirect(url_for('set_doctor_slots', doctor_id=doctor_id))

        error = validate_slot_input(start_time, end_time, break_start, break_end)
        if error:
            flash(error, 'danger')
            return redirect(url_for('set_doctor_slots', doctor_id=doctor_id))
        
        try:
            conn.execute('DELETE FROM doctor_slots WHERE doctor_id = ? AND day_of_week = ? AND hospital_id = ?', 
                        (doctor_id, day, session['hospital_id']))
            conn.execute('''
                INSERT INTO doctor_slots (doctor_id, day_of_week, start_time, end_time, break_start, break_end, hospital_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (doctor_id, day, start_time, end_time, break_start, break_end, session['hospital_id']))
            conn.commit()
            log_audit('update_slots', 'doctor', doctor_id, day)
            flash('Doctor availability slots updated successfully!', 'success')
            return redirect(url_for('view_doctors'))
        except Exception as e:
            flash('Error updating slots', 'danger')
            app.logger.error(f"Set slots error: {str(e)}")
        finally:
            conn.close()
    
    try:
        slots = conn.execute('''
            SELECT * FROM doctor_slots 
            WHERE doctor_id = ? AND hospital_id = ?
            ORDER BY 
                CASE day_of_week
                    WHEN 'Monday' THEN 1
                    WHEN 'Tuesday' THEN 2
                    WHEN 'Wednesday' THEN 3
                    WHEN 'Thursday' THEN 4
                    WHEN 'Friday' THEN 5
                    WHEN 'Saturday' THEN 6
                    WHEN 'Sunday' THEN 7
                END
        ''', (doctor_id, session['hospital_id'])).fetchall()
    except Exception as e:
        flash('Error loading doctor data', 'danger')
        app.logger.error(f"Doctor slots error: {str(e)}")
        slots = []
    finally:
        conn.close()
    
    return render_template('admin/set_doctor_slots.html', 
                         doctor=doctor, 
                         slots=slots)

@app.route('/admin/get_doctor_slots/<int:doctor_id>/<date>')
def get_doctor_slots(doctor_id, date):
    if 'user_id' not in session or session['user_type'] != 'admin':
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        date_obj = datetime.strptime(date, '%Y-%m-%d')
        day = date_obj.strftime('%A')
    except ValueError:
        return jsonify({'error': 'Invalid date format'}), 400
    
    conn = get_db_connection()
    try:
        slot_info = conn.execute('''
            SELECT * FROM doctor_slots 
            WHERE doctor_id = ? AND day_of_week = ? AND hospital_id = ?
        ''', (doctor_id, day, session['hospital_id'])).fetchone()
        
        if not slot_info:
            return jsonify({'error': 'Doctor not available on this day'})
        
        slots = generate_time_slots(
            slot_info['start_time'],
            slot_info['end_time'],
            slot_info['break_start'],
            slot_info['break_end']
        )
        
        booked_slots = conn.execute('''
            SELECT time_slot FROM appointments
            WHERE doctor_id = ? AND date = ? AND status != 'Cancelled' AND hospital_id = ?
        ''', (doctor_id, date, session['hospital_id'])).fetchall()
        
        booked_slots = [slot['time_slot'] for slot in booked_slots]
        
        return jsonify({
            'slots': slots,
            'booked_slots': booked_slots
        })
    except Exception as e:
        app.logger.error(f"Get slots error: {str(e)}")
        return jsonify({'error': 'Server error'}), 500
    finally:
        conn.close()

@app.route('/admin/schedule_appointment', methods=['GET', 'POST'])
def schedule_appointment():
    if 'user_id' not in session or session['user_type'] != 'admin':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    
    if request.method == 'POST':
        patient_id = request.form.get('patient_id', '').strip()
        doctor_id = request.form.get('doctor_id', '').strip()
        date = request.form.get('date', '').strip()
        time_slot = request.form.get('time_slot', '').strip()
        notes = request.form.get('notes', '').strip()
        
        if not all([patient_id, doctor_id, date, time_slot]):
            flash('Required fields are missing', 'danger')
            return redirect(url_for('schedule_appointment'))

        try:
            appt_date = datetime.strptime(date, '%Y-%m-%d').date()
            datetime.strptime(time_slot, '%H:%M')
        except ValueError:
            flash('Invalid date or time slot format', 'danger')
            return redirect(url_for('schedule_appointment'))
        if appt_date < datetime.now().date():
            flash('Appointments cannot be scheduled in the past', 'danger')
            return redirect(url_for('schedule_appointment'))

        try:
            # Tenant check: both records must belong to this hospital
            patient = conn.execute('SELECT id FROM patients WHERE id = ? AND hospital_id = ?',
                                   (patient_id, session['hospital_id'])).fetchone()
            doctor = conn.execute('SELECT id FROM doctors WHERE id = ? AND hospital_id = ?',
                                  (doctor_id, session['hospital_id'])).fetchone()
            if not patient or not doctor:
                flash('Selected patient or doctor was not found', 'danger')
                return redirect(url_for('schedule_appointment'))
            # Double-book guard (also enforced by UNIQUE index, see migrate_db)
            clash = conn.execute('''
                SELECT id FROM appointments
                WHERE doctor_id = ? AND date = ? AND time_slot = ?
                  AND status != 'Cancelled' AND hospital_id = ?
            ''', (doctor_id, date, time_slot, session['hospital_id'])).fetchone()
            if clash:
                flash('That slot was just booked. Please pick another time.', 'warning')
                return redirect(url_for('schedule_appointment'))
            conn.execute('''
                INSERT INTO appointments (patient_id, doctor_id, date, time_slot, notes, hospital_id)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (patient_id, doctor_id, date, time_slot, notes, session['hospital_id']))
            appt_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
            conn.commit()
            log_audit('create', 'appointment', appt_id, f"doctor={doctor_id} patient={patient_id} {date} {time_slot}")
            flash('Appointment scheduled successfully!', 'success')
            return redirect(url_for('view_appointments'))
        except sqlite3.IntegrityError:
            conn.rollback()
            flash('That slot was just booked. Please pick another time.', 'warning')
            return redirect(url_for('schedule_appointment'))
        except Exception as e:
            flash('Error scheduling appointment', 'danger')
            app.logger.error(f"Schedule appointment error: {str(e)}")
        finally:
            conn.close()
    
    try:
        patients = conn.execute('SELECT id, name FROM patients WHERE hospital_id = ? ORDER BY name', 
                              (session['hospital_id'],)).fetchall()
        doctors = conn.execute('SELECT id, name, specialization FROM doctors WHERE hospital_id = ? ORDER BY name', 
                             (session['hospital_id'],)).fetchall()
    except Exception as e:
        flash('Error loading data', 'danger')
        app.logger.error(f"Schedule appointment load error: {str(e)}")
        patients = []
        doctors = []
    finally:
        conn.close()
    
    min_date = datetime.now().strftime('%Y-%m-%d')
    
    return render_template('admin/schedule_appointment.html', 
                         patients=patients, 
                         doctors=doctors,
                         min_date=min_date)

@app.route('/admin/view_appointments')
def view_appointments():
    if 'user_id' not in session or session['user_type'] != 'admin':
        return redirect(url_for('login'))
    
    search_query = request.args.get('search', '').strip()
    status_filter = request.args.get('status', '').strip()
    
    conn = get_db_connection()
    try:
        query = '''
            SELECT a.id, p.name as patient_name, d.name as doctor_name, 
                   a.date, a.time_slot, a.status, a.notes
            FROM appointments a
            JOIN patients p ON a.patient_id = p.id
            JOIN doctors d ON a.doctor_id = d.id
            WHERE a.hospital_id = ?
        '''
        
        params = [session['hospital_id']]
        
        if search_query:
            query += ' AND (p.name LIKE ? OR d.name LIKE ?)'
            params.extend([f'%{search_query}%', f'%{search_query}%'])
        
        if status_filter:
            query += ' AND a.status = ?'
            params.append(status_filter)
        
        query += ' ORDER BY a.date DESC, a.time_slot DESC'
        
        appointments = conn.execute(query, params).fetchall()
        appointments_total = conn.execute('SELECT COUNT(*) FROM appointments WHERE hospital_id = ?',
                                          (session['hospital_id'],)).fetchone()[0]
    except Exception as e:
        flash('Error loading appointments', 'danger')
        app.logger.error(f"View appointments error: {str(e)}")
        appointments = []
    finally:
        conn.close()
    
    return render_template('admin/view_appointments.html',
                         appointments=appointments,
                         appointments_total=appointments_total,
                         search_query=search_query,
                         status_filter=status_filter)

@app.route('/admin/delete_appointment/<int:appointment_id>', methods=['POST'])
def delete_appointment(appointment_id):
    if 'user_id' not in session or session['user_type'] != 'admin':
        return redirect(url_for('login'))

    conn = get_db_connection()
    try:
        cur = conn.execute('DELETE FROM appointments WHERE id = ? AND hospital_id = ?',
                           (appointment_id, session['hospital_id']))
        conn.commit()
        if cur.rowcount:
            log_audit('delete', 'appointment', appointment_id)
            flash('Appointment deleted successfully!', 'success')
        else:
            flash('Appointment not found', 'warning')
    except sqlite3.IntegrityError:
        conn.rollback()
        flash('Cannot delete: related records exist', 'danger')
    except Exception as e:
        flash('Error deleting appointment', 'danger')
        app.logger.error(f"Delete appointment error: {str(e)}")
    finally:
        conn.close()

    return redirect(url_for('view_appointments'))

@app.route('/admin/export_appointments')
def export_appointments():
    if 'user_id' not in session or session['user_type'] != 'admin':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    try:
        appointments = conn.execute('''
            SELECT p.name as patient_name, d.name as doctor_name, 
                   a.date, a.time_slot, a.status, a.notes
            FROM appointments a
            JOIN patients p ON a.patient_id = p.id
            JOIN doctors d ON a.doctor_id = d.id
            WHERE a.hospital_id = ?
            ORDER BY a.date DESC, a.time_slot DESC
        ''', (session['hospital_id'],)).fetchall()

        try:
            import pandas as pd
        except ImportError:
            flash('Excel export is unavailable (export dependencies not installed)', 'danger')
            return redirect(url_for('view_appointments'))
        df = pd.DataFrame(appointments, columns=['Patient Name', 'Doctor Name', 'Date', 'Time Slot', 'Status', 'Notes'])
        
        output = BytesIO()
        writer = pd.ExcelWriter(output, engine='xlsxwriter')
        df.to_excel(writer, sheet_name='Appointments', index=False)
        writer.close()
        output.seek(0)
        
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name='appointments.xlsx'
        )
    except Exception as e:
        flash('Error exporting appointments', 'danger')
        app.logger.error(f"Export appointments error: {str(e)}")
        return redirect(url_for('view_appointments'))
    finally:
        conn.close()

# Doctor routes
@app.route('/doctor/dashboard')
def doctor_dashboard():
    if 'user_id' not in session or session['user_type'] != 'doctor':
        return redirect(url_for('login'))
    
    doctor_id = session['user_id']
    conn = get_db_connection()
    
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        appointments_today = conn.execute('''
            SELECT a.id, p.id as patient_id, p.name as patient_name, a.time_slot, a.status
            FROM appointments a
            JOIN patients p ON a.patient_id = p.id
            WHERE a.doctor_id = ? AND a.date = ? AND a.hospital_id = ?
            ORDER BY a.time_slot
        ''', (doctor_id, today, session['hospital_id'])).fetchall()
        
        upcoming_appointments = conn.execute('''
            SELECT a.id, p.id as patient_id, p.name as patient_name, a.date, a.time_slot, a.status
            FROM appointments a
            JOIN patients p ON a.patient_id = p.id
            WHERE a.doctor_id = ? AND a.date > ? AND a.hospital_id = ?
            ORDER BY a.date, a.time_slot
            LIMIT 5
        ''', (doctor_id, today, session['hospital_id'])).fetchall()

        week_days = []
        for i in range(7):
            day = datetime.now() + timedelta(days=i)
            week_days.append({'date': day.strftime('%Y-%m-%d'), 'dow': day.strftime('%a'), 'label': day.strftime('%d')})
        week_rows = conn.execute('''
            SELECT date, COUNT(*) as c FROM appointments
            WHERE doctor_id = ? AND hospital_id = ? AND date >= ? AND date <= date(?, '+6 days')
            GROUP BY date
        ''', (doctor_id, session['hospital_id'], today, today)).fetchall()
        week_counts = {r['date']: r['c'] for r in week_rows}
    except Exception as e:
        flash('Error loading dashboard data', 'danger')
        app.logger.error(f"Doctor dashboard error: {str(e)}")
        appointments_today = []
        upcoming_appointments = []
        week_days = []
        week_counts = {}
    finally:
        conn.close()

    return render_template('doctor/dashboard.html',
                         appointments_today=appointments_today,
                         upcoming_appointments=upcoming_appointments,
                         week_days=week_days,
                         week_counts=week_counts)

@app.route('/doctor/appointments')
def doctor_appointments():
    if 'user_id' not in session or session['user_type'] != 'doctor':
        return redirect(url_for('login'))
    
    doctor_id = session['user_id']
    search_query = request.args.get('search', '').strip()
    status_filter = request.args.get('status', '').strip()
    
    conn = get_db_connection()
    try:
        query = '''
            SELECT a.id, p.name as patient_name, p.age, p.gender,
                   a.date, a.time_slot, a.status, a.notes
            FROM appointments a
            JOIN patients p ON a.patient_id = p.id
            WHERE a.doctor_id = ? AND a.hospital_id = ?
        '''
        
        params = [doctor_id, session['hospital_id']]
        
        if search_query:
            query += ' AND p.name LIKE ?'
            params.append(f'%{search_query}%')
        
        if status_filter:
            query += ' AND a.status = ?'
            params.append(status_filter)
        
        query += ' ORDER BY a.date DESC, a.time_slot DESC'
        
        appointments = conn.execute(query, params).fetchall()
        appointments_total = conn.execute('SELECT COUNT(*) FROM appointments WHERE doctor_id = ? AND hospital_id = ?',
                                          (doctor_id, session['hospital_id'])).fetchone()[0]
    except Exception as e:
        flash('Error loading appointments', 'danger')
        app.logger.error(f"Doctor appointments error: {str(e)}")
        appointments = []
    finally:
        conn.close()
    
    return render_template('doctor/view_appointments.html',
                         appointments=appointments,
                         appointments_total=appointments_total,
                         search_query=search_query,
                         status_filter=status_filter)

@app.route('/doctor/update_appointment_status/<int:appointment_id>', methods=['POST'])
def update_appointment_status(appointment_id):
    if 'user_id' not in session or session['user_type'] != 'doctor':
        return redirect(url_for('login'))
    
    status = request.form.get('status', '').strip()
    
    if status not in VALID_STATUSES:
        flash('Invalid status value', 'danger')
        return redirect(url_for('doctor_appointments'))
    
    conn = get_db_connection()
    try:
        cur = conn.execute('''
            UPDATE appointments 
            SET status = ? 
            WHERE id = ? AND doctor_id = ? AND hospital_id = ?
        ''', (status, appointment_id, session['user_id'], session['hospital_id']))
        conn.commit()
        if cur.rowcount:
            log_audit('update_status', 'appointment', appointment_id, status)
            flash('Appointment status updated successfully!', 'success')
        else:
            flash('Appointment not found', 'warning')
    except Exception as e:
        flash('Error updating status', 'danger')
        app.logger.error(f"Update status error: {str(e)}")
    finally:
        conn.close()
    
    return redirect(url_for('doctor_appointments'))

@app.route('/doctor/prescriptions/<int:appointment_id>', methods=['GET', 'POST'])
def prescriptions(appointment_id):
    if 'user_id' not in session or session['user_type'] != 'doctor':
        return redirect(url_for('login'))

    conn = get_db_connection()
    try:
        # Verify appointment belongs to this doctor and hospital
        appointment = conn.execute('''
            SELECT a.*, p.name as patient_name, p.age, p.gender, p.medical_history,
                   d.name as doctor_name, d.specialization
            FROM appointments a
            JOIN patients p ON a.patient_id = p.id
            JOIN doctors d ON a.doctor_id = d.id
            WHERE a.id = ? AND a.doctor_id = ? AND a.hospital_id = ?
        ''', (appointment_id, session['user_id'], session['hospital_id'])).fetchone()

        if not appointment:
            flash('Appointment not found or unauthorized access', 'danger')
            return redirect(url_for('doctor_appointments'))

        if request.method == 'POST':
            diagnosis = request.form.get('diagnosis', '').strip()
            medicines = []
            
            medicine_count = int(request.form.get('medicine_count', 1))
            
            for i in range(1, medicine_count + 1):
                name = request.form.get(f'medicine_name_{i}', '').strip()
                dosage = request.form.get(f'medicine_dosage_{i}', '').strip()
                frequency = request.form.get(f'medicine_frequency_{i}', '').strip()
                
                if name and dosage and frequency:
                    morning = '1' if request.form.get(f'medicine_morning_{i}') else '0'
                    afternoon = '1' if request.form.get(f'medicine_afternoon_{i}') else '0'
                    evening = '1' if request.form.get(f'medicine_evening_{i}') else '0'
                    meal = request.form.get(f'medicine_meal_{i}', 'after')
                    
                    medicine_str = f"{name}|{dosage}|{frequency}|{morning}|{afternoon}|{evening}|{meal}"
                    medicines.append(medicine_str)
            
            instructions = request.form.get('instructions', '').strip()
            medicines_str = '\n'.join(medicines) if medicines else ''

            try:
                existing = conn.execute('''
                    SELECT * FROM prescriptions 
                    WHERE appointment_id = ? AND hospital_id = ?
                ''', (appointment_id, session['hospital_id'])).fetchone()
                
                if existing:
                    conn.execute('''
                        UPDATE prescriptions 
                        SET diagnosis=?, medicines=?, instructions=?
                        WHERE appointment_id=? AND hospital_id=?
                    ''', (diagnosis, medicines_str, instructions, appointment_id, session['hospital_id']))
                else:
                    conn.execute('''
                        INSERT INTO prescriptions (appointment_id, diagnosis, medicines, instructions, hospital_id)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (appointment_id, diagnosis, medicines_str, instructions, session['hospital_id']))
                
                conn.commit()
                log_audit('save', 'prescription', appointment_id, diagnosis[:120] if diagnosis else None)
                flash('Prescription saved successfully!', 'success')
                
                if request.form.get('action') == 'print':
                    return redirect(url_for('print_prescription', appointment_id=appointment_id))
                return redirect(url_for('prescriptions', appointment_id=appointment_id))
            except Exception as e:
                conn.rollback()
                flash('Error saving prescription', 'danger')
                app.logger.error(f"Prescription save error: {str(e)}")
                return redirect(url_for('prescriptions', appointment_id=appointment_id))

        # Get prescription if exists
        prescription = conn.execute('''
            SELECT * FROM prescriptions 
            WHERE appointment_id = ? AND hospital_id = ?
        ''', (appointment_id, session['hospital_id'])).fetchone()
        
        # Process prescription for display
        prescription_dict = None
        if prescription:
            prescription_dict = dict(prescription)
            medicines_parsed = []
            if prescription['medicines']:
                for medicine in prescription['medicines'].split('\n'):
                    if medicine.strip():
                        parts = medicine.split('|')
                        if len(parts) == 7:
                            medicines_parsed.append({
                                'name': parts[0],
                                'dosage': parts[1],
                                'frequency': parts[2],
                                'morning': parts[3],
                                'afternoon': parts[4],
                                'evening': parts[5],
                                'meal': parts[6]
                            })
            prescription_dict['medicines_parsed'] = medicines_parsed
        
        return render_template('doctor/prescriptions.html',
                            appointment=appointment,
                            prescription=prescription_dict)
    except Exception as e:
        flash('Error loading prescription data', 'danger')
        app.logger.error(f"Prescription load error: {str(e)}")
        return redirect(url_for('doctor_appointments'))
    finally:
        conn.close()

@app.route('/doctor/print_prescription/<int:appointment_id>')
def print_prescription(appointment_id):
    if 'user_id' not in session or session['user_type'] != 'doctor':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    try:
        prescription_data = conn.execute('''
            SELECT a.id as appointment_id, a.date, a.time_slot, 
                   p.name as patient_name, p.age, p.gender,
                   d.name as doctor_name, d.specialization,
                   pr.diagnosis, pr.medicines, pr.instructions,
                   s.hospital_name
            FROM appointments a
            JOIN patients p ON a.patient_id = p.id
            JOIN doctors d ON a.doctor_id = d.id
            JOIN staff s ON d.hospital_id = s.id
            LEFT JOIN prescriptions pr ON a.id = pr.appointment_id
            WHERE a.id = ? AND a.hospital_id = ?
        ''', (appointment_id, session['hospital_id'])).fetchone()
        
        if not prescription_data:
            flash('Prescription not found!', 'danger')
            return redirect(url_for('doctor_appointments'))
        
        # Get next appointment if exists
        next_appointment = conn.execute('''
            SELECT date, time_slot FROM appointments
            WHERE patient_id = (
                SELECT patient_id FROM appointments WHERE id = ?
            )
            AND date > (
                SELECT date FROM appointments WHERE id = ?
            )
            AND status = 'Scheduled'
            AND hospital_id = ?
            ORDER BY date ASC
            LIMIT 1
        ''', (appointment_id, appointment_id, session['hospital_id'])).fetchone()
        
        # Process medicines for display
        medicines_parsed = []
        if prescription_data['medicines']:
            for medicine in prescription_data['medicines'].split('\n'):
                if medicine.strip():
                    parts = medicine.split('|')
                    if len(parts) == 7:
                        medicines_parsed.append({
                            'name': parts[0],
                            'dosage': parts[1],
                            'frequency': parts[2],
                            'morning': parts[3],
                            'afternoon': parts[4],
                            'evening': parts[5],
                            'meal': parts[6]
                        })
        
        # Create a dictionary with all prescription data
        prescription = dict(prescription_data)
        prescription['medicines_parsed'] = medicines_parsed
        
        return render_template('doctor/print_prescription.html',
                            prescription=prescription,
                            next_appointment=next_appointment)
    except Exception as e:
        flash('Error loading prescription', 'danger')
        app.logger.error(f"Print prescription error: {str(e)}")
        return redirect(url_for('doctor_appointments'))
    finally:
        conn.close()

@app.route('/doctor/patients')
def all_patients_history():
    if 'user_id' not in session or session['user_type'] != 'doctor':
        return redirect(url_for('login'))
    
    search_query = request.args.get('search', '').strip()
    doctor_id = session['user_id']
    
    conn = get_db_connection()
    patients_total = conn.execute('''
        SELECT COUNT(DISTINCT p.id) FROM patients p
        JOIN appointments a ON p.id = a.patient_id
        WHERE a.doctor_id = ? AND p.hospital_id = ?
    ''', (doctor_id, session['hospital_id'])).fetchone()[0]
    try:
        if search_query:
            patients = conn.execute('''
                SELECT DISTINCT p.*, MAX(a.date) as last_visit
                FROM patients p
                JOIN appointments a ON p.id = a.patient_id
                WHERE a.doctor_id = ? 
                AND p.hospital_id = ?
                AND (p.name LIKE ? OR p.contact LIKE ?)
                GROUP BY p.id
                ORDER BY p.name
            ''', (doctor_id, session['hospital_id'], f'%{search_query}%', f'%{search_query}%')).fetchall()
        else:
            patients = conn.execute('''
                SELECT DISTINCT p.*, MAX(a.date) as last_visit
                FROM patients p
                JOIN appointments a ON p.id = a.patient_id
                WHERE a.doctor_id = ? 
                AND p.hospital_id = ?
                GROUP BY p.id
                ORDER BY p.name
            ''', (doctor_id, session['hospital_id'])).fetchall()
    except Exception as e:
        flash('Error loading patients', 'danger')
        app.logger.error(f"All patients history error: {str(e)}")
        patients = []
    finally:
        conn.close()
    
    try:
        return render_template('doctor/all_patients_history.html',
                             patients=patients,
                             patients_total=patients_total,
                             search_query=search_query)
    except Exception as e:
        app.logger.error(f"Template rendering error: {str(e)}")
        flash('Error loading the page', 'danger')
        return redirect(url_for('doctor_dashboard'))

@app.route('/doctor/patient_history/<int:patient_id>')
def patient_history(patient_id):
    if 'user_id' not in session or session['user_type'] != 'doctor':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    try:
        # Get patient details
        patient = conn.execute('''
            SELECT * FROM patients 
            WHERE id = ? AND hospital_id = ?
        ''', (patient_id, session['hospital_id'])).fetchone()
        
        if not patient:
            flash('Patient not found', 'danger')
            return redirect(url_for('doctor_dashboard'))
        
        # Get all prescriptions
        prescriptions = conn.execute('''
            SELECT pr.*, a.date, a.time_slot, d.name as doctor_name
            FROM prescriptions pr
            JOIN appointments a ON pr.appointment_id = a.id
            JOIN doctors d ON a.doctor_id = d.id
            WHERE a.patient_id = ? AND a.hospital_id = ?
            ORDER BY a.date DESC
        ''', (patient_id, session['hospital_id'])).fetchall()
        
        # Process prescriptions for display
        prescriptions_list = []
        for prescription in prescriptions:
            pres_dict = dict(prescription)
            medicines_parsed = []
            if prescription['medicines']:
                for medicine in prescription['medicines'].split('\n'):
                    if medicine.strip():
                        parts = medicine.split('|')
                        if len(parts) == 7:
                            medicines_parsed.append({
                                'name': parts[0],
                                'dosage': parts[1],
                                'frequency': parts[2],
                                'morning': parts[3],
                                'afternoon': parts[4],
                                'evening': parts[5],
                                'meal': parts[6]
                            })
            pres_dict['medicines_parsed'] = medicines_parsed
            prescriptions_list.append(pres_dict)
        
        # Get all appointments
        appointments = conn.execute('''
            SELECT a.*, d.name as doctor_name
            FROM appointments a
            JOIN doctors d ON a.doctor_id = d.id
            WHERE a.patient_id = ? AND a.hospital_id = ?
            ORDER BY a.date DESC
        ''', (patient_id, session['hospital_id'])).fetchall()
        
        return render_template('doctor/patient_history.html',
                            patient=patient,
                            prescriptions=prescriptions_list,
                            appointments=appointments)
    except Exception as e:
        flash('Error loading patient history', 'danger')
        app.logger.error(f"Patient history error: {str(e)}")
        return redirect(url_for('doctor_dashboard'))
    finally:
        conn.close()

@app.route('/admin/delete_doctor/<int:doctor_id>', methods=['POST'])
def delete_doctor(doctor_id):
    if 'user_id' not in session or session['user_type'] != 'admin':
        return redirect(url_for('login'))

    conn = get_db_connection()
    try:
        # First check if doctor has any appointments
        appointments = conn.execute('SELECT COUNT(*) FROM appointments WHERE doctor_id = ? AND hospital_id = ?',
                                    (doctor_id, session['hospital_id'])).fetchone()[0]

        if appointments > 0:
            flash('Cannot delete doctor with existing appointments', 'danger')
            return redirect(url_for('view_doctors'))

        # Delete doctor's image if exists
        doctor = conn.execute('SELECT image_path FROM doctors WHERE id = ? AND hospital_id = ?',
                              (doctor_id, session['hospital_id'])).fetchone()
        if doctor and doctor['image_path']:
            try:
                img_path = os.path.normpath(os.path.join('static', doctor['image_path']))
                if img_path.startswith(os.path.join('static', 'images', 'doctors')) and os.path.isfile(img_path):
                    os.remove(img_path)
            except OSError:
                pass
            try:
                # Vercel/ephemeral uploads live under UPLOAD_FOLDER (/tmp)
                tmp_path = os.path.normpath(os.path.join(app.config['UPLOAD_FOLDER'],
                                                          os.path.basename(doctor['image_path'])))
                if os.path.isfile(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass

        # Delete doctor
        cur = conn.execute('DELETE FROM doctors WHERE id = ? AND hospital_id = ?',
                           (doctor_id, session['hospital_id']))
        conn.commit()
        if cur.rowcount:
            log_audit('delete', 'doctor', doctor_id)
            flash('Doctor deleted successfully', 'success')
        else:
            flash('Doctor not found', 'warning')
    except sqlite3.IntegrityError:
        conn.rollback()
        flash('Cannot delete doctor with existing records', 'danger')
    except Exception as e:
        flash('Error deleting doctor', 'danger')
        app.logger.error(f"Error deleting doctor: {str(e)}")
    finally:
        conn.close()

    return redirect(url_for('view_doctors'))

@app.route('/admin/delete_patient/<int:patient_id>', methods=['POST'])
def delete_patient(patient_id):
    if 'user_id' not in session or session['user_type'] != 'admin':
        return redirect(url_for('login'))

    conn = get_db_connection()
    try:
        # First check if patient has any appointments
        appointments = conn.execute('SELECT COUNT(*) FROM appointments WHERE patient_id = ? AND hospital_id = ?',
                                    (patient_id, session['hospital_id'])).fetchone()[0]

        if appointments > 0:
            flash('Cannot delete patient with existing appointments', 'danger')
            return redirect(url_for('view_patients'))

        # Delete patient
        cur = conn.execute('DELETE FROM patients WHERE id = ? AND hospital_id = ?',
                           (patient_id, session['hospital_id']))
        conn.commit()
        if cur.rowcount:
            log_audit('delete', 'patient', patient_id)
            flash('Patient deleted successfully', 'success')
        else:
            flash('Patient not found', 'warning')
    except sqlite3.IntegrityError:
        conn.rollback()
        flash('Cannot delete patient with existing records', 'danger')
    except Exception as e:
        flash('Error deleting patient', 'danger')
        app.logger.error(f"Error deleting patient: {str(e)}")
    finally:
        conn.close()

    return redirect(url_for('view_patients'))

# ---------------- Billing & invoices ----------------
@app.route('/admin/billing')
def billing():
    if 'user_id' not in session or session['user_type'] != 'admin':
        return redirect(url_for('login'))

    search_query = request.args.get('search', '').strip()
    status_filter = request.args.get('status', '').strip()

    conn = get_db_connection()
    try:
        query = '''
            SELECT b.id, b.appointment_id, b.created_at, p.name AS patient_name
            FROM bills b JOIN patients p ON b.patient_id = p.id
            WHERE b.hospital_id = ?
        '''
        params = [session['hospital_id']]
        if search_query:
            query += ' AND (p.name LIKE ? OR CAST(b.id AS TEXT) LIKE ?)'
            params.extend([f'%{search_query}%', f'%{search_query}%'])
        query += ' ORDER BY b.id DESC'
        bills = conn.execute(query, params).fetchall()

        rows, collected, outstanding = [], 0.0, 0.0
        for b in bills:
            t = bill_totals(conn, b['id'])
            rows.append({**dict(b), **t})
        if status_filter:
            rows = [r for r in rows if r['status'] == status_filter]
        for r in rows:
            collected += r['paid']
            outstanding += r['balance']
        total_bills = conn.execute('SELECT COUNT(*) FROM bills WHERE hospital_id = ?',
                                   (session['hospital_id'],)).fetchone()[0]
    except Exception as e:
        flash('Error loading bills', 'danger')
        app.logger.error(f"Billing view error: {str(e)}")
        rows, total_bills, collected, outstanding = [], 0, 0.0, 0.0
    finally:
        conn.close()

    return render_template('admin/billing.html', bills=rows, total_bills=total_bills,
                           collected=round(collected, 2), outstanding=round(outstanding, 2),
                           search_query=search_query, status_filter=status_filter)

@app.route('/admin/billing/new')
def new_bill():
    if 'user_id' not in session or session['user_type'] != 'admin':
        return redirect(url_for('login'))
    conn = get_db_connection()
    try:
        patients = conn.execute('SELECT id, name FROM patients WHERE hospital_id = ? ORDER BY name',
                                (session['hospital_id'],)).fetchall()
        visits = conn.execute('''
            SELECT a.id, a.date, a.time_slot, p.name AS patient_name, p.id AS patient_id,
                   d.name AS doctor_name, d.consultation_fee
            FROM appointments a
            JOIN patients p ON a.patient_id = p.id
            JOIN doctors d ON a.doctor_id = d.id
            LEFT JOIN bills b ON b.appointment_id = a.id
            WHERE a.hospital_id = ? AND a.status != 'Cancelled' AND b.id IS NULL
            ORDER BY a.date DESC LIMIT 100
        ''', (session['hospital_id'],)).fetchall()
    except Exception as e:
        flash('Error loading billing form', 'danger')
        app.logger.error(f"New bill view error: {str(e)}")
        patients, visits = [], []
    finally:
        conn.close()
    return render_template('admin/bill_form.html', patients=patients, visits=visits,
                           preselect_patient=request.args.get('patient_id', ''),
                           preselect_visit=request.args.get('appointment_id', ''))

@app.route('/admin/billing/create', methods=['POST'])
def create_bill():
    if 'user_id' not in session or session['user_type'] != 'admin':
        return redirect(url_for('login'))
    patient_id = request.form.get('patient_id', '').strip()
    appointment_id = request.form.get('appointment_id', '').strip() or None
    notes = request.form.get('notes', '').strip()
    try:
        item_count = int(request.form.get('item_count', '0'))
    except (TypeError, ValueError):
        item_count = 0

    items = []
    for i in range(1, item_count + 1):
        label = (request.form.get(f'item_label_{i}', '') or '').strip()
        amount = parse_money(request.form.get(f'item_amount_{i}', ''))
        if label and amount is not None and amount > 0:
            items.append((label[:120], amount))
    if not patient_id or not items:
        flash('A patient and at least one billed item are required', 'danger')
        return redirect(url_for('new_bill'))

    conn = get_db_connection()
    try:
        patient = conn.execute('SELECT id FROM patients WHERE id = ? AND hospital_id = ?',
                               (patient_id, session['hospital_id'])).fetchone()
        if not patient:
            flash('Patient not found', 'danger')
            return redirect(url_for('new_bill'))
        consult_item = None
        if appointment_id:
            visit = conn.execute('''
                SELECT a.id, a.patient_id, d.name AS doctor_name, d.consultation_fee
                FROM appointments a JOIN doctors d ON a.doctor_id = d.id
                WHERE a.id = ? AND a.hospital_id = ?
            ''', (appointment_id, session['hospital_id'])).fetchone()
            if not visit or str(visit['patient_id']) != str(patient_id):
                flash('Visit does not belong to this patient', 'danger')
                return redirect(url_for('new_bill'))
            if visit['consultation_fee']:
                consult_item = (f"Consultation — Dr. {visit['doctor_name']}", round(float(visit['consultation_fee']), 2))
        cur = conn.execute('INSERT INTO bills (appointment_id, patient_id, hospital_id, notes) VALUES (?, ?, ?, ?)',
                           (appointment_id, patient_id, session['hospital_id'], notes))
        bill_id = cur.lastrowid
        if consult_item:
            conn.execute('INSERT INTO bill_items (bill_id, label, amount) VALUES (?, ?, ?)',
                         (bill_id, consult_item[0], consult_item[1]))
        for label, amount in items:
            conn.execute('INSERT INTO bill_items (bill_id, label, amount) VALUES (?, ?, ?)',
                         (bill_id, label, amount))
        conn.commit()
        log_audit('create', 'bill', bill_id, f"patient={patient_id} items={len(items) + (1 if consult_item else 0)}")
        flash(f"Bill #{bill_id} created", 'success')
        return redirect(url_for('bill_detail', bill_id=bill_id))
    except sqlite3.IntegrityError:
        conn.rollback()
        flash('That visit is already billed', 'warning')
        return redirect(url_for('new_bill'))
    except Exception as e:
        flash('Error creating bill', 'danger')
        app.logger.error(f"Create bill error: {str(e)}")
        return redirect(url_for('new_bill'))
    finally:
        conn.close()

def _load_bill(conn, bill_id, hospital_id):
    bill = conn.execute('''
        SELECT b.*, p.name AS patient_name, p.age, p.gender, p.contact
        FROM bills b JOIN patients p ON b.patient_id = p.id
        WHERE b.id = ? AND b.hospital_id = ?
    ''', (bill_id, hospital_id)).fetchone()
    if not bill:
        return None
    items = conn.execute('SELECT * FROM bill_items WHERE bill_id = ? ORDER BY id', (bill_id,)).fetchall()
    payments = conn.execute('SELECT * FROM payments WHERE bill_id = ? ORDER BY id', (bill_id,)).fetchall()
    data = dict(bill)
    data['items'] = [dict(i) for i in items]
    data['payments'] = [dict(p) for p in payments]
    data.update(bill_totals(conn, bill_id))
    return data

@app.route('/admin/billing/<int:bill_id>')
def bill_detail(bill_id):
    if 'user_id' not in session or session['user_type'] != 'admin':
        return redirect(url_for('login'))
    conn = get_db_connection()
    try:
        bill = _load_bill(conn, bill_id, session['hospital_id'])
        if not bill:
            flash('Bill not found', 'warning')
            return redirect(url_for('billing'))
    finally:
        conn.close()
    return render_template('admin/bill_detail.html', bill=bill,
                           methods=['Cash', 'Card', 'UPI', 'Insurance', 'Other'])

@app.route('/admin/billing/<int:bill_id>/pay', methods=['POST'])
def record_payment(bill_id):
    if 'user_id' not in session or session['user_type'] != 'admin':
        return redirect(url_for('login'))
    amount = parse_money(request.form.get('amount', ''))
    method = request.form.get('method', '').strip()
    if amount is None or amount <= 0:
        flash('Enter a valid payment amount', 'danger')
        return redirect(url_for('bill_detail', bill_id=bill_id))
    if method not in PAYMENT_METHODS:
        flash('Select a valid payment method', 'danger')
        return redirect(url_for('bill_detail', bill_id=bill_id))
    conn = get_db_connection()
    try:
        bill = conn.execute('SELECT id FROM bills WHERE id = ? AND hospital_id = ?',
                            (bill_id, session['hospital_id'])).fetchone()
        if not bill:
            flash('Bill not found', 'warning')
            return redirect(url_for('billing'))
        balance = bill_totals(conn, bill_id)['balance']
        if amount - balance > 0.009:
            flash(f"Payment exceeds the ₹{balance:.2f} balance", 'danger')
            return redirect(url_for('bill_detail', bill_id=bill_id))
        conn.execute('INSERT INTO payments (bill_id, amount, method) VALUES (?, ?, ?)',
                     (bill_id, amount, method))
        conn.commit()
        log_audit('payment', 'bill', bill_id, f"₹{amount:.2f} via {method}")
        flash(f"Payment of ₹{amount:.2f} recorded", 'success')
    except Exception as e:
        flash('Error recording payment', 'danger')
        app.logger.error(f"Record payment error: {str(e)}")
    finally:
        conn.close()
    return redirect(url_for('bill_detail', bill_id=bill_id))

@app.route('/admin/billing/<int:bill_id>/delete', methods=['POST'])
def delete_bill(bill_id):
    if 'user_id' not in session or session['user_type'] != 'admin':
        return redirect(url_for('login'))
    conn = get_db_connection()
    try:
        bill = conn.execute('SELECT id FROM bills WHERE id = ? AND hospital_id = ?',
                            (bill_id, session['hospital_id'])).fetchone()
        if not bill:
            flash('Bill not found', 'warning')
            return redirect(url_for('billing'))
        n = conn.execute('SELECT COUNT(*) FROM payments WHERE bill_id = ?', (bill_id,)).fetchone()[0]
        if n > 0:
            flash('Cannot delete a bill with recorded payments', 'danger')
            return redirect(url_for('bill_detail', bill_id=bill_id))
        conn.execute('DELETE FROM bill_items WHERE bill_id = ?', (bill_id,))
        conn.execute('DELETE FROM bills WHERE id = ?', (bill_id,))
        conn.commit()
        log_audit('delete', 'bill', bill_id)
        flash('Bill deleted', 'success')
        return redirect(url_for('billing'))
    except Exception as e:
        flash('Error deleting bill', 'danger')
        app.logger.error(f"Delete bill error: {str(e)}")
        return redirect(url_for('bill_detail', bill_id=bill_id))
    finally:
        conn.close()

@app.route('/admin/billing/<int:bill_id>/invoice')
def bill_invoice(bill_id):
    if 'user_id' not in session or session['user_type'] != 'admin':
        return redirect(url_for('login'))
    conn = get_db_connection()
    try:
        bill = _load_bill(conn, bill_id, session['hospital_id'])
        if not bill:
            flash('Bill not found', 'warning')
            return redirect(url_for('billing'))
    finally:
        conn.close()
    return render_template('admin/invoice.html', bill=bill,
                           hospital_name=session.get('hospital_name', ''))

# ---------------- Pharmacy inventory ----------------
@app.route('/admin/pharmacy')
def pharmacy():
    if 'user_id' not in session or session['user_type'] != 'admin':
        return redirect(url_for('login'))

    search_query = request.args.get('search', '').strip()
    low_only = request.args.get('low') == '1'

    conn = get_db_connection()
    try:
        if search_query:
            medicines = conn.execute('''
                SELECT * FROM medicines
                WHERE name LIKE ? AND hospital_id = ? ORDER BY name
            ''', (f'%{search_query}%', session['hospital_id'])).fetchall()
        else:
            medicines = conn.execute('SELECT * FROM medicines WHERE hospital_id = ? ORDER BY name',
                                     (session['hospital_id'],)).fetchall()
        if low_only:
            medicines = [m for m in medicines if (m['stock_qty'] or 0) <= (m['reorder_level'] or 0)]
        total = conn.execute('SELECT COUNT(*) FROM medicines WHERE hospital_id = ?',
                             (session['hospital_id'],)).fetchone()[0]
        low_count = conn.execute('SELECT COUNT(*) FROM medicines WHERE hospital_id = ? AND stock_qty <= reorder_level',
                                 (session['hospital_id'],)).fetchone()[0]
    except Exception as e:
        flash('Error loading inventory', 'danger')
        app.logger.error(f"Pharmacy view error: {str(e)}")
        medicines, total, low_count = [], 0, 0
    finally:
        conn.close()

    return render_template('admin/pharmacy.html', medicines=medicines, total=total,
                           low_count=low_count, search_query=search_query, low_only=low_only)

@app.route('/admin/medicines/add', methods=['POST'])
def add_medicine():
    if 'user_id' not in session or session['user_type'] != 'admin':
        return redirect(url_for('login'))
    name = request.form.get('name', '').strip()
    strength = request.form.get('strength', '').strip()
    unit = request.form.get('unit', '').strip()
    stock = request.form.get('stock_qty', '').strip()
    reorder = request.form.get('reorder_level', '').strip()
    price = request.form.get('unit_price', '').strip()
    if not name or len(name) > 120:
        flash('Medicine name is required (max 120 chars)', 'danger')
        return redirect(url_for('pharmacy'))
    try:
        stock_n = int(stock or 0)
        reorder_n = int(reorder or 10)
    except (TypeError, ValueError):
        flash('Stock and reorder level must be whole numbers', 'danger')
        return redirect(url_for('pharmacy'))
    unit_price = parse_money(price or 0)
    if stock_n < 0 or stock_n > 1000000 or reorder_n < 0 or reorder_n > 1000000 or unit_price is None:
        flash('Stock, reorder level or price is out of range', 'danger')
        return redirect(url_for('pharmacy'))
    conn = get_db_connection()
    try:
        cur = conn.execute('''
            INSERT INTO medicines (name, strength, unit, stock_qty, reorder_level, unit_price, hospital_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (name, strength[:60], unit[:30], stock_n, reorder_n, unit_price, session['hospital_id']))
        conn.commit()
        log_audit('create', 'medicine', cur.lastrowid, f"{name} stock={stock_n}")
        flash('Medicine added to inventory', 'success')
    except Exception as e:
        flash('Error adding medicine', 'danger')
        app.logger.error(f"Add medicine error: {str(e)}")
    finally:
        conn.close()
    return redirect(url_for('pharmacy'))

@app.route('/admin/medicines/<int:medicine_id>/update', methods=['POST'])
def update_medicine(medicine_id):
    if 'user_id' not in session or session['user_type'] != 'admin':
        return redirect(url_for('login'))
    name = request.form.get('name', '').strip()
    strength = request.form.get('strength', '').strip()
    unit = request.form.get('unit', '').strip()
    reorder = request.form.get('reorder_level', '').strip()
    price = request.form.get('unit_price', '').strip()
    if not name or len(name) > 120:
        flash('Medicine name is required (max 120 chars)', 'danger')
        return redirect(url_for('pharmacy'))
    try:
        reorder_n = int(reorder or 0)
    except (TypeError, ValueError):
        flash('Reorder level must be a whole number', 'danger')
        return redirect(url_for('pharmacy'))
    unit_price = parse_money(price or 0)
    if reorder_n < 0 or reorder_n > 1000000 or unit_price is None:
        flash('Reorder level or price is out of range', 'danger')
        return redirect(url_for('pharmacy'))
    conn = get_db_connection()
    try:
        cur = conn.execute('''
            UPDATE medicines SET name = ?, strength = ?, unit = ?, reorder_level = ?, unit_price = ?
            WHERE id = ? AND hospital_id = ?
        ''', (name, strength[:60], unit[:30], reorder_n, unit_price, medicine_id, session['hospital_id']))
        conn.commit()
        if cur.rowcount:
            log_audit('update', 'medicine', medicine_id, name)
            flash('Medicine updated', 'success')
        else:
            flash('Medicine not found', 'warning')
    except Exception as e:
        flash('Error updating medicine', 'danger')
        app.logger.error(f"Update medicine error: {str(e)}")
    finally:
        conn.close()
    return redirect(url_for('pharmacy'))

@app.route('/admin/medicines/<int:medicine_id>/adjust', methods=['POST'])
def adjust_stock(medicine_id):
    if 'user_id' not in session or session['user_type'] != 'admin':
        return redirect(url_for('login'))
    try:
        delta = int(request.form.get('delta', '0').strip())
    except (TypeError, ValueError, AttributeError):
        flash('Adjustment must be a whole number', 'danger')
        return redirect(url_for('pharmacy'))
    if abs(delta) > 100000 or delta == 0:
        flash('Adjustment is out of range', 'danger')
        return redirect(url_for('pharmacy'))
    conn = get_db_connection()
    try:
        med = conn.execute('SELECT * FROM medicines WHERE id = ? AND hospital_id = ?',
                           (medicine_id, session['hospital_id'])).fetchone()
        if not med:
            flash('Medicine not found', 'warning')
            return redirect(url_for('pharmacy'))
        new_qty = (med['stock_qty'] or 0) + delta
        if new_qty < 0:
            flash('Adjustment would take stock below zero', 'danger')
            return redirect(url_for('pharmacy'))
        conn.execute('UPDATE medicines SET stock_qty = ? WHERE id = ? AND hospital_id = ?',
                     (new_qty, medicine_id, session['hospital_id']))
        conn.commit()
        log_audit('adjust_stock', 'medicine', medicine_id, f"{'+' if delta > 0 else ''}{delta} -> {new_qty}")
        flash(f"Stock updated ({med['name']}: {new_qty})", 'success')
    except Exception as e:
        flash('Error adjusting stock', 'danger')
        app.logger.error(f"Adjust stock error: {str(e)}")
    finally:
        conn.close()
    return redirect(url_for('pharmacy'))

@app.route('/admin/medicines/<int:medicine_id>/delete', methods=['POST'])
def delete_medicine(medicine_id):
    if 'user_id' not in session or session['user_type'] != 'admin':
        return redirect(url_for('login'))
    conn = get_db_connection()
    try:
        cur = conn.execute('DELETE FROM medicines WHERE id = ? AND hospital_id = ?',
                           (medicine_id, session['hospital_id']))
        conn.commit()
        if cur.rowcount:
            log_audit('delete', 'medicine', medicine_id)
            flash('Medicine removed', 'success')
        else:
            flash('Medicine not found', 'warning')
    except Exception as e:
        flash('Error removing medicine', 'danger')
        app.logger.error(f"Delete medicine error: {str(e)}")
    finally:
        conn.close()
    return redirect(url_for('pharmacy'))

# ---------------- Bed & ward management ----------------
@app.route('/admin/beds')
def beds():
    if 'user_id' not in session or session['user_type'] != 'admin':
        return redirect(url_for('login'))

    conn = get_db_connection()
    try:
        wards = conn.execute('SELECT * FROM wards WHERE hospital_id = ? ORDER BY name',
                             (session['hospital_id'],)).fetchall()
        beds = conn.execute('''
            SELECT b.*, p.name AS patient_name
            FROM beds b LEFT JOIN patients p ON b.patient_id = p.id
            WHERE b.hospital_id = ? ORDER BY b.bed_number
        ''', (session['hospital_id'],)).fetchall()
        patients = conn.execute('SELECT id, name FROM patients WHERE hospital_id = ? ORDER BY name',
                                (session['hospital_id'],)).fetchall()
    except Exception as e:
        flash('Error loading beds', 'danger')
        app.logger.error(f"Beds view error: {str(e)}")
        wards, beds, patients = [], [], []
    finally:
        conn.close()

    by_ward = {}
    for w in wards:
        by_ward[w['id']] = {'ward': w, 'beds': []}
    for b in beds:
        if b['ward_id'] in by_ward:
            by_ward[b['ward_id']]['beds'].append(b)
    counts = {
        'total': len(beds),
        'free': sum(1 for b in beds if b['status'] == 'Available'),
        'occupied': sum(1 for b in beds if b['status'] == 'Occupied'),
    }
    return render_template('admin/beds.html', ward_groups=list(by_ward.values()),
                           patients=patients, counts=counts)

@app.route('/admin/wards/add', methods=['POST'])
def add_ward():
    if 'user_id' not in session or session['user_type'] != 'admin':
        return redirect(url_for('login'))
    name = request.form.get('name', '').strip()
    ward_type = request.form.get('ward_type', '').strip()
    if not name or len(name) > 80:
        flash('Ward name is required (max 80 chars)', 'danger')
        return redirect(url_for('beds'))
    conn = get_db_connection()
    try:
        cur = conn.execute('INSERT INTO wards (name, ward_type, hospital_id) VALUES (?, ?, ?)',
                           (name, ward_type[:80], session['hospital_id']))
        conn.commit()
        log_audit('create', 'ward', cur.lastrowid, name)
        flash('Ward added', 'success')
    except Exception as e:
        flash('Error adding ward', 'danger')
        app.logger.error(f"Add ward error: {str(e)}")
    finally:
        conn.close()
    return redirect(url_for('beds'))

@app.route('/admin/wards/<int:ward_id>/delete', methods=['POST'])
def delete_ward(ward_id):
    if 'user_id' not in session or session['user_type'] != 'admin':
        return redirect(url_for('login'))
    conn = get_db_connection()
    try:
        n = conn.execute('SELECT COUNT(*) FROM beds WHERE ward_id = ? AND hospital_id = ?',
                         (ward_id, session['hospital_id'])).fetchone()[0]
        if n > 0:
            flash('Cannot delete a ward that still has beds', 'danger')
            return redirect(url_for('beds'))
        cur = conn.execute('DELETE FROM wards WHERE id = ? AND hospital_id = ?',
                           (ward_id, session['hospital_id']))
        conn.commit()
        if cur.rowcount:
            log_audit('delete', 'ward', ward_id)
            flash('Ward deleted', 'success')
        else:
            flash('Ward not found', 'warning')
    except Exception as e:
        flash('Error deleting ward', 'danger')
        app.logger.error(f"Delete ward error: {str(e)}")
    finally:
        conn.close()
    return redirect(url_for('beds'))

@app.route('/admin/beds/add', methods=['POST'])
def add_bed():
    if 'user_id' not in session or session['user_type'] != 'admin':
        return redirect(url_for('login'))
    ward_id = request.form.get('ward_id', '').strip()
    bed_number = request.form.get('bed_number', '').strip()
    if not ward_id or not bed_number or len(bed_number) > 20:
        flash('Ward and bed number are required', 'danger')
        return redirect(url_for('beds'))
    conn = get_db_connection()
    try:
        ward = conn.execute('SELECT id FROM wards WHERE id = ? AND hospital_id = ?',
                            (ward_id, session['hospital_id'])).fetchone()
        if not ward:
            flash('Ward not found', 'danger')
            return redirect(url_for('beds'))
        cur = conn.execute('INSERT INTO beds (ward_id, bed_number, hospital_id) VALUES (?, ?, ?)',
                           (ward_id, bed_number, session['hospital_id']))
        conn.commit()
        log_audit('create', 'bed', cur.lastrowid, f"ward={ward_id} bed={bed_number}")
        flash('Bed added', 'success')
    except sqlite3.IntegrityError:
        conn.rollback()
        flash('That bed number already exists in this ward', 'warning')
    except Exception as e:
        flash('Error adding bed', 'danger')
        app.logger.error(f"Add bed error: {str(e)}")
    finally:
        conn.close()
    return redirect(url_for('beds'))

@app.route('/admin/beds/<int:bed_id>/delete', methods=['POST'])
def delete_bed(bed_id):
    if 'user_id' not in session or session['user_type'] != 'admin':
        return redirect(url_for('login'))
    conn = get_db_connection()
    try:
        bed = conn.execute('SELECT * FROM beds WHERE id = ? AND hospital_id = ?',
                           (bed_id, session['hospital_id'])).fetchone()
        if not bed:
            flash('Bed not found', 'warning')
            return redirect(url_for('beds'))
        if bed['status'] != 'Available' or bed['patient_id']:
            flash('Only free beds can be removed', 'danger')
            return redirect(url_for('beds'))
        conn.execute('DELETE FROM beds WHERE id = ? AND hospital_id = ?', (bed_id, session['hospital_id']))
        conn.commit()
        log_audit('delete', 'bed', bed_id, bed['bed_number'])
        flash('Bed removed', 'success')
    except Exception as e:
        flash('Error removing bed', 'danger')
        app.logger.error(f"Delete bed error: {str(e)}")
    finally:
        conn.close()
    return redirect(url_for('beds'))

@app.route('/admin/beds/<int:bed_id>/assign', methods=['POST'])
def assign_bed(bed_id):
    if 'user_id' not in session or session['user_type'] != 'admin':
        return redirect(url_for('login'))
    patient_id = request.form.get('patient_id', '').strip()
    if not patient_id:
        flash('Select a patient', 'danger')
        return redirect(url_for('beds'))
    conn = get_db_connection()
    try:
        bed = conn.execute('SELECT * FROM beds WHERE id = ? AND hospital_id = ?',
                           (bed_id, session['hospital_id'])).fetchone()
        patient = conn.execute('SELECT id, name FROM patients WHERE id = ? AND hospital_id = ?',
                               (patient_id, session['hospital_id'])).fetchone()
        if not bed or not patient:
            flash('Bed or patient not found', 'danger')
            return redirect(url_for('beds'))
        if bed['status'] != 'Available' or bed['patient_id']:
            flash('That bed is no longer free', 'warning')
            return redirect(url_for('beds'))
        already = conn.execute("SELECT id FROM beds WHERE patient_id = ? AND status = 'Occupied' AND hospital_id = ?",
                               (patient_id, session['hospital_id'])).fetchone()
        if already:
            flash('Patient already occupies another bed', 'warning')
            return redirect(url_for('beds'))
        conn.execute("UPDATE beds SET status = 'Occupied', patient_id = ? WHERE id = ? AND hospital_id = ?",
                     (patient_id, bed_id, session['hospital_id']))
        conn.commit()
        log_audit('assign', 'bed', bed_id, f"{bed['bed_number']} -> {patient['name']}")
        flash(f"Bed {bed['bed_number']} assigned to {patient['name']}", 'success')
    except Exception as e:
        flash('Error assigning bed', 'danger')
        app.logger.error(f"Assign bed error: {str(e)}")
    finally:
        conn.close()
    return redirect(url_for('beds'))

@app.route('/admin/beds/<int:bed_id>/discharge', methods=['POST'])
def discharge_bed(bed_id):
    if 'user_id' not in session or session['user_type'] != 'admin':
        return redirect(url_for('login'))
    conn = get_db_connection()
    try:
        bed = conn.execute('SELECT * FROM beds WHERE id = ? AND hospital_id = ?',
                           (bed_id, session['hospital_id'])).fetchone()
        if not bed or bed['status'] != 'Occupied':
            flash('Bed is not occupied', 'warning')
            return redirect(url_for('beds'))
        conn.execute("UPDATE beds SET status = 'Available', patient_id = NULL WHERE id = ? AND hospital_id = ?",
                     (bed_id, session['hospital_id']))
        conn.commit()
        log_audit('discharge', 'bed', bed_id, bed['bed_number'])
        flash(f"Bed {bed['bed_number']} freed", 'success')
    except Exception as e:
        flash('Error freeing bed', 'danger')
        app.logger.error(f"Discharge bed error: {str(e)}")
    finally:
        conn.close()
    return redirect(url_for('beds'))

@app.route('/admin/beds/<int:bed_id>/maintenance', methods=['POST'])
def maintenance_bed(bed_id):
    if 'user_id' not in session or session['user_type'] != 'admin':
        return redirect(url_for('login'))
    conn = get_db_connection()
    try:
        bed = conn.execute('SELECT * FROM beds WHERE id = ? AND hospital_id = ?',
                           (bed_id, session['hospital_id'])).fetchone()
        if not bed:
            flash('Bed not found', 'warning')
            return redirect(url_for('beds'))
        if bed['status'] == 'Occupied':
            flash('Occupied beds cannot go into maintenance', 'danger')
            return redirect(url_for('beds'))
        new_status = 'Maintenance' if bed['status'] == 'Available' else 'Available'
        conn.execute('UPDATE beds SET status = ? WHERE id = ? AND hospital_id = ?',
                     (new_status, bed_id, session['hospital_id']))
        conn.commit()
        log_audit('maintenance' if new_status == 'Maintenance' else 'reopen', 'bed', bed_id, bed['bed_number'])
        flash(f"Bed {bed['bed_number']} marked {new_status.lower()}", 'success')
    except Exception as e:
        flash('Error updating bed', 'danger')
        app.logger.error(f"Bed maintenance error: {str(e)}")
    finally:
        conn.close()
    return redirect(url_for('beds'))

@app.errorhandler(500)
def internal_error(error):
    request_id = getattr(g, 'request_id', 'n/a')
    logger.error(f"Internal Server Error [{request_id}]: {error}")
    return render_template('errors/500.html', request_id=request_id), 500

@app.route('/admin/audit_log')
def audit_log_view():
    if 'user_id' not in session or session['user_type'] != 'admin':
        return redirect(url_for('login'))

    action_filter = request.args.get('action', '').strip()
    entity_filter = request.args.get('entity', '').strip()

    conn = get_db_connection()
    try:
        query = '''
            SELECT a.*, COALESCE(s.name, d.name) AS actor_name
            FROM audit_log a
            LEFT JOIN staff s ON a.actor_id = s.id AND a.actor_type != 'doctor'
            LEFT JOIN doctors d ON a.actor_id = d.id AND a.actor_type = 'doctor'
            WHERE a.hospital_id = ?
        '''
        params = [session['hospital_id']]
        if action_filter:
            query += ' AND a.action = ?'
            params.append(action_filter)
        if entity_filter:
            query += ' AND a.entity = ?'
            params.append(entity_filter)
        query += ' ORDER BY a.id DESC LIMIT 100'
        entries = conn.execute(query, params).fetchall()
        actions = [r['action'] for r in conn.execute(
            'SELECT DISTINCT action FROM audit_log WHERE hospital_id = ? ORDER BY action',
            (session['hospital_id'],)).fetchall()]
        entities = [r['entity'] for r in conn.execute(
            'SELECT DISTINCT entity FROM audit_log WHERE hospital_id = ? ORDER BY entity',
            (session['hospital_id'],)).fetchall()]
    except Exception as e:
        flash('Error loading audit log', 'danger')
        app.logger.error(f"Audit log view error: {str(e)}")
        entries, actions, entities = [], [], []
    finally:
        conn.close()

    return render_template('admin/audit_log.html',
                           entries=entries, actions=actions, entities=entities,
                           action_filter=action_filter, entity_filter=entity_filter)

@app.errorhandler(404)
def not_found_error(error):
    # `path` is a deployment diagnostic: on Vercel it reveals whether Flask
    # received the original URL or the rewritten function path.
    return render_template('errors/404.html', path=request.path), 404

if __name__ == '__main__':
    # Ensure database exists and is initialized
    if not check_database_exists():
        logger.info("Database not found. Initializing...")
        try:
            init_db()
            logger.info("Database initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise

    # Check if admin user exists
    conn = get_db_connection()
    try:
        admin = conn.execute('SELECT * FROM staff WHERE email = ?', ('admin@hospital.com',)).fetchone()
        if not admin:
            # Create admin user if it doesn't exist
            conn.execute(
                'INSERT INTO staff (name, email, password, hospital_name) VALUES (?, ?, ?, ?)',
                ('Admin', 'admin@hospital.com', generate_password_hash('admin123'), 'City General Hospital')
            )
            conn.commit()
            logger.info("Created default admin user")
    except Exception as e:
        logger.error(f"Error checking/creating admin user: {e}")
        raise
    finally:
        conn.close()

    # Create required directories
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs('instance', exist_ok=True)

    app.run(debug=True)