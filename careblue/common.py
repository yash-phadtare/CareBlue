"""Template context providers."""
from flask import session, current_app as app, g
from database import get_db_connection
from careblue.services import facility_now

def utility_processor():
    def now():
        return facility_now()
    return dict(now=now,
                session_lifetime=int(app.config['PERMANENT_SESSION_LIFETIME'].total_seconds()))

def header_stats():
    """Today's scheduled-visit count for the notification bell (role-aware)."""
    if 'user_id' not in session or session.get('user_type') == 'platform' or getattr(g, 'rendering_error', False):
        return {}
    today = facility_now().strftime('%Y-%m-%d')
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
        return {'today_count': n}
    finally:
        conn.close()
