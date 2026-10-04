from careblue.presentation import back_url,context_link
from careblue.routes.blueprints import AccessBlueprint as Blueprint
from flask import abort, flash, redirect, render_template, request, session, url_for
from datetime import datetime
from database import get_db_connection
from careblue.security import log_audit
from careblue.services import page_query
from database import lock_record

bp = Blueprint("availability", __name__, roles=("admin",), permission="doctors")

@bp.route("/admin/doctors/<int:doctor_id>/absences", methods=["GET", "POST"])
def absences(doctor_id):
    conn = get_db_connection()
    try:
        doctor = conn.execute("SELECT * FROM doctors WHERE id=? AND hospital_id=?", (doctor_id, session["hospital_id"])).fetchone()
        if not doctor:
            abort(404)
        if request.method == "POST":
            lock_record(conn, "doctors", doctor_id)
            start = request.form.get("start_date", "")
            end = request.form.get("end_date", "").strip() or start
            reason = request.form.get("reason", "").strip() or 'Unavailable'
            try:
                first = datetime.strptime(start, "%Y-%m-%d").date()
                last = datetime.strptime(end, "%Y-%m-%d").date()
                if first > last or len(reason) > 500:
                    raise ValueError()
            except ValueError:
                flash("Enter a valid date range and an absence reason.", "danger")
                return redirect(context_link("availability.absences", doctor_id=doctor_id))
            clash = conn.execute("SELECT COUNT(*) FROM appointments WHERE doctor_id=? AND hospital_id=? AND date>=? AND date<=? AND status='Scheduled'",
                                 (doctor_id, session["hospital_id"], start, end)).fetchone()[0]
            conn.execute("INSERT INTO doctor_absences (doctor_id, hospital_id, start_date, end_date, reason) VALUES (?,?,?,?,?)",
                         (doctor_id, session["hospital_id"], start, end, reason))
            log_audit("absence", "doctor", doctor_id, start + " to " + end, conn=conn)
            conn.commit()
            flash("Absence saved. These dates are unavailable for booking.", "success")
            if clash:
                flash(f'{clash} existing visits were kept. Review these visits to arrange another doctor or date.','info')
            return redirect(context_link("availability.absences", doctor_id=doctor_id))
        rows = page_query(conn, "SELECT * FROM doctor_absences WHERE doctor_id=? AND hospital_id=? ORDER BY start_date DESC, id DESC",
                          (doctor_id, session["hospital_id"]))
        return render_template("admin/absences.html", doctor=doctor, absences=rows)
    finally:
        conn.close()
